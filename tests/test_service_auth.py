import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from smart_car_decision.service_auth import ServiceAuthentication


def app():
    value = FastAPI()
    value.add_middleware(ServiceAuthentication)

    @value.get("/health")
    def health():
        return {"ok": True}

    @value.post("/api/command")
    def command():
        return {"ok": True}

    @value.websocket("/ws")
    async def ws(socket):
        await socket.accept()
        await socket.close()

    return value


def test_machine_token_required_and_user_bearer_is_not_accepted():
    client = TestClient(app())
    assert client.post("/api/command").status_code == 200
    assert client.post("/api/command", headers={"X-InsightRover-Service-Token": "", "Authorization": "Bearer user-jwt"}).status_code == 401
    assert client.get("/health", headers={"X-InsightRover-Service-Token": ""}).status_code == 200


@pytest.mark.parametrize("token", ["", "short"])
def test_missing_machine_configuration_fails_closed(monkeypatch, token):
    monkeypatch.setenv("ROBOT_SERVICE_TOKEN", token)
    client = TestClient(app())
    assert client.post("/api/command").status_code == 503


def test_websocket_cannot_bypass_machine_auth():
    client = TestClient(app())
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/ws", headers={"X-InsightRover-Service-Token": "wrong"}):
            pass
    assert error.value.code == 4401
