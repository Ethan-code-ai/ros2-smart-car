"""Local browser acceptance using the production app and a simulated car/model."""
import asyncio
import os
import secrets
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse


ROOT = Path(__file__).resolve().parents[1]


def build_app(directory, port):
    os.environ.update(
        DATABASE_URL="sqlite:///" + (Path(directory) / "browser.db").as_posix(),
        JWT_SECRET=secrets.token_hex(32),
        REDIS_ENABLED="false",
        MINIO_ENDPOINT="",
        STORAGE_LOCAL_DIR=str(Path(directory) / "storage"),
        ROBOT_WEB_URL=f"http://127.0.0.1:{port}/fixture-car",
        ROBOT_GATEWAY_URL="",
        ROBOT_SERVICE_TOKEN="",
        INSIGHTROVER_CREDENTIAL_PROFILE="",
    )
    sys.path.insert(0, str(ROOT / "backend"))
    from app.api.routes import assistant
    from app.db.session import SessionLocal
    from app.main import app
    from app.models import AssistantConfiguration, AssistantExecution, Robot, User
    from app.seed import seed
    from app.services import assistant_execution, robot_context

    seed()
    with SessionLocal() as db:
        db.add(Robot(id="r-001", name="智巡一号 · 模拟车", serial="BROWSER-FIXTURE"))
        db.add(AssistantConfiguration(id=1, enabled=True))
        db.commit()

    @asynccontextmanager
    async def fixture_lifespan(_):
        yield

    app.router.lifespan_context = fixture_lifespan
    counters = {"model_waiting": False, "stop_during_wait": False, "stops": 0, "drive_commands": 0,
                "plan_failures": 0, "plan_reads": 0}
    stopped = asyncio.Event()
    mode = "manual"

    def status():
        return {"mode": mode, "network": "connected", "updated_at": time.time(), "emergency_stop": False,
                "lidar": {"ok": True}, "odom": {"ok": True}, "tf": {"ok": True},
                "map": {"ok": True, "width": 64, "height": 40}, "map_pose": {"ok": True, "x": 1.4, "y": 1.2, "yaw": 0},
                "navigation": {"state": "idle", "message": "模拟车在线 · 实验室地图"}}

    def context(robot_id, _binding):
        return {"robot_id": robot_id, "available": True, "telemetry": status()}

    robot_context.robot_context_service.context = context
    assistant.configured_provider = object

    async def model(_provider, _db, _user, _history, text):
        if text == "模拟模型等待":
            stopped.clear()
            counters["model_waiting"] = True
            try:
                await asyncio.wait_for(stopped.wait(), 28)
            finally:
                counters["model_waiting"] = False
            return "模拟模型已恢复。停车状态以控制器回执为准。", None, None
        if "服务器机柜" in text:
            plan = {"robot_id": "r-001", "steps": [{
                "action": "patrol_search", "target": "服务器机柜",
                "resources": [{"names": ["实验室巡检路线"]}], "capability": {"executable": True},
            }]}
            return "沿实验室路线巡检一圈，找到服务器机柜后停车，等待人工处理。", plan, None
        return "模拟车在线，当前位置在实验室地图。", None, None

    assistant.ask = model

    async def allow_simulated_confirmation(*_):
        await asyncio.sleep(.35)

    assistant_execution.validate_steps = allow_simulated_confirmation

    @app.middleware("http")
    async def interrupt_first_plan_read(request, call_next):
        if request.method == "GET" and request.url.path.startswith("/api/v1/assistant/plans/"):
            counters["plan_reads"] += 1
            if counters["plan_failures"] == 0:
                counters["plan_failures"] += 1
                return JSONResponse({"detail": "模拟首次核对计划失败"}, status_code=503)
        return await call_next(request)

    @app.get("/fixture/evidence")
    def evidence():
        with SessionLocal() as db:
            return {**counters, "executions": db.query(AssistantExecution).count()}

    @app.post("/fixture/manual")
    def manual():
        nonlocal mode
        mode = "manual"
        return {"mode": mode}

    @app.post("/fixture/notify")
    async def notify():
        # Use the production message handler so the confirmation is stored and streamed.
        from app.models import AssistantSession
        from uuid import uuid4

        with SessionLocal() as db:
            user = db.query(User).filter_by(username="admin").one()
            session = AssistantSession(id=str(uuid4()), user_id=user.id)
            db.add(session)
            db.commit()
            result = await assistant.send_message(session.id, assistant.MessageIn(
                client_id=uuid4(), text="沿实验室路线巡检，找到服务器机柜后停车"), db, user)
            return {"session_id": session.id, "plan_id": result["reply"]["plan"]["id"]}

    car = FastAPI()

    @car.get("/api/status")
    def vehicle_status():
        return status()

    @car.get("/api/map")
    def vehicle_map():
        cells = [100 if x in (0, 63) or y in (0, 39) or (x in (20, 42) and y not in range(15, 23)) else 0
                 for y in range(40) for x in range(64)]
        return {"header": {"frame_id": "map", "stamp": {"sec": 1, "nanosec": 0}}, "updated_at": time.time(),
                "info": {"width": 64, "height": 40, "resolution": .05, "origin": {"x": 0, "y": 0, "yaw": 0}},
                "data": cells}

    @car.websocket("/ws/status")
    async def vehicle_socket(socket: WebSocket):
        await socket.accept()
        try:
            while True:
                await socket.send_json(status())
                await asyncio.sleep(.25)
        except (WebSocketDisconnect, RuntimeError):
            pass

    @car.post("/api/stop")
    async def vehicle_stop():
        nonlocal mode
        mode = "stop"
        counters["stops"] += 1
        counters["stop_during_wait"] |= counters["model_waiting"]
        stopped.set()
        return {"request_sent": True, "controller_confirmed": True, "stationary_confirmed": True, "errors": []}

    @app.middleware("http")
    async def count_manual_attempts(request, call_next):
        if request.method == "POST" and request.url.path == "/api/control/command":
            counters["drive_commands"] += 1
        return await call_next(request)

    app.mount("/fixture-car", car)
    return app


if __name__ == "__main__":
    port = int(sys.argv[1])
    (ROOT / "output").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="v4-browser-db-", dir=ROOT / "output", ignore_cleanup_errors=True) as tmp:
        fixture = build_app(tmp, port)
        try:
            uvicorn.run(fixture, host="127.0.0.1", port=port, log_level="warning")
        finally:
            from app.db.session import engine
            engine.dispose()
