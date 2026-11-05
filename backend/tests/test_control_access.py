from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.services import control_access


def test_only_bound_existing_vehicle_can_be_controlled(monkeypatch):
    monkeypatch.setattr(control_access, "vehicle_binding", lambda db: ("physical", "http://vehicle"))
    db = SimpleNamespace(get=lambda model, ident: object() if ident == "physical" else None)
    assert control_access.require_bound_vehicle(db, "physical") == ("physical", "http://vehicle")
    with pytest.raises(HTTPException) as error:
        control_access.require_bound_vehicle(db, "other")
    assert error.value.status_code == 409
    with pytest.raises(HTTPException):
        control_access.require_bound_vehicle(SimpleNamespace(get=lambda *args: None))


@pytest.mark.parametrize("command", ["patrol_task", "mission_task"])
def test_legacy_task_command_cannot_bypass_execution_service(command):
    with pytest.raises(HTTPException) as error:
        control_access.authorize_business_command(command, {}, {"robots.control", "patrol.manage", "missions.manage"})
    assert error.value.status_code == 409


def test_mapping_requires_business_permission_but_stop_does_not():
    with pytest.raises(HTTPException):
        control_access.authorize_business_command("set_mode", {"mode": "mapping"}, {"robots.control"})
    control_access.authorize_business_command("stop", {}, {"robots.control"})


def test_unbound_business_write_is_rejected_before_gateway(client, login, monkeypatch):
    from app.api.routes import robots
    def unexpected(*args):
        pytest.fail("unbound request reached vehicle gateway")
    monkeypatch.setattr(robots, "get_robot_gateway", unexpected)
    headers = {"Authorization": "Bearer " + login("operator", "operator123")}
    for path, payload in [("control", {"command": "forward"}), ("emergency-stop", {"enabled": True})]:
        response = client.post(f"/api/v1/robots/fixture-robot-1/{path}", json=payload, headers=headers)
        assert response.status_code == 409, response.text


def test_bound_stop_attempted_without_telemetry(client, login, monkeypatch):
    from app.api.routes import robots

    monkeypatch.setattr(control_access, "vehicle_binding", lambda db: ("fixture-robot-1", "http://fixture"))
    calls = []

    async def stop():
        calls.append("stop")
        raise TimeoutError()

    monkeypatch.setattr(robots, "get_robot_gateway", lambda address: SimpleNamespace(stop=stop))
    assert client.post("/api/v1/robots/stop").status_code == 401
    assert calls == []
    headers = {"Authorization": "Bearer " + login("operator", "operator123")}
    response = client.post("/api/v1/robots/stop", headers=headers)
    assert response.status_code == 200
    assert calls == ["stop"]
    assert response.json()["controller_confirmed"] is False
    assert response.json()["stationary_confirmed"] is False
