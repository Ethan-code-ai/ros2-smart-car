"""Smoke-test the independent backend with isolated credentials and SQLite."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx


ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="v4-independent-", dir=output, ignore_cleanup_errors=True) as directory:
        data = Path(directory).resolve()
        assert data.is_relative_to(output.resolve())
        env = os.environ.copy()
        for key in ("INSIGHTROVER_CREDENTIAL_PROFILE", "ASSISTANT_API_KEY", "ASSISTANT_API_KEY_FILE",
                    "ASSISTANT_BASE_URL", "ASSISTANT_MODEL", "ROBOT_SERVICE_TOKEN", "APP_VERSION"):
            env.pop(key, None)
        env.update(DATABASE_URL="sqlite:///" + (data / "test.db").as_posix(), REDIS_ENABLED="false",
                   ROBOT_WEB_URL="", ROBOT_GATEWAY_URL="", STORAGE_LOCAL_DIR=str(data / "storage"),
                   JWT_SECRET="standalone-isolated-test-key")
        subprocess.run([sys.executable, "-m", "app.seed"], cwd=ROOT / "backend", env=env,
                       check=True, stdout=subprocess.DEVNULL)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with (data / "server.log").open("wb") as log:
            process = subprocess.Popen([
                sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
            ], cwd=ROOT / "backend", env=env, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=2) as client:
                    for _ in range(100):
                        if process.poll() is not None:
                            raise RuntimeError("Standalone server exited")
                        try:
                            response = client.get("/health/ready")
                            if response.status_code == 200 and response.json()["status"] == "ready":
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(.1)
                    else:
                        raise RuntimeError("Standalone readiness timeout")
                    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
                    login.raise_for_status()
                    client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
                    spec = client.get("/openapi.json").json()
                    assert spec["info"]["version"] == "4.0.0"
                    assert "/api/v1/robots/{robot_id}/acceptances" in spec["paths"]
                    assert client.get("/api/v1/assistant/settings").json() == {"enabled": False, "configured": False}
                    assert client.post("/api/v1/assistant/sessions").status_code == 201
                    assert client.get("/api/v1/assistant/sessions").json()["items"]
                database = sqlite3.connect(data / "test.db")
                try:
                    revisions = [row[0] for row in database.execute("SELECT revision FROM schema_migrations ORDER BY revision")]
                finally:
                    database.close()
                assert {"v4-assistant-permissions-1", "v4-capability-evidence-1"} <= set(revisions)
                result = {"version": "4.0.0", "standalone_start": True, "login": True, "assistant_session": True,
                          "model_disabled_without_config": True, "acceptance_api_present": True,
                          "migrations": revisions, "physical_vehicle_connected": False}
                (output / "v4-independent-backend-check.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(result, ensure_ascii=False))
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
