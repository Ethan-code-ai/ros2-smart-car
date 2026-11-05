from types import SimpleNamespace

import pytest

from app.api.routes.navigation_tasks import _permission
from app.services import robot_context
from app.services.realtime import MemoryRealtimeStore


def test_vehicle_context_marks_lost_data_stale_without_inventing_task(monkeypatch):
    binding = ("r-001", "http://fixture")
    context = robot_context.RobotContextService()
    store = MemoryRealtimeStore()
    reply = {"ok": True, "status": {
        "ros2_status_age_sec": 0.1, "current_task": None,
        "navigation_task": {"run_id": "run", "state": "waiting_load"},
    }}

    async def get_status(robot_id):
        assert robot_id == binding[0]
        return reply

    monkeypatch.setattr(robot_context, "get_realtime_store", lambda: store)
    monkeypatch.setattr(robot_context, "get_robot_gateway", lambda url: SimpleNamespace(get_status=get_status))

    def poll():
        # The mocked gateway completes without I/O.
        with pytest.raises(StopIteration):
            context.poll(binding, True).send(None)

    poll()
    assert context.context("r-001", binding)["available"]
    assert context.context("r-001", binding)["telemetry"]["navigation_task"]["state"] == "waiting_load"
    assert context.context("r-002", binding)["telemetry"] is None
    assert context.context("r-001", ("r-001", "http://replacement"))["telemetry"] is None
    reply["ok"] = False
    poll()
    assert context.context("r-001", binding)["stale"]
    assert store.get_status("r-001") is None


def test_business_task_manage_permission_alone_cannot_dispatch():
    user = SimpleNamespace(permissions={"missions.manage", "missions.read"})
    _permission(user, "delivery", manage=False)
    with pytest.raises(Exception) as rejected:
        _permission(user, "delivery")
    assert rejected.value.status_code == 403


@pytest.mark.parametrize("failure", ["timeout", "slow_response"])
def test_failed_or_slow_poll_clears_legacy_status(monkeypatch, failure):
    binding = ("r-001", "http://fixture")
    service = robot_context.RobotContextService()
    store = MemoryRealtimeStore()
    store.set_status("r-001", {"mode": "navigation"})
    clock = [10.0]

    async def get_status(robot_id):
        if failure == "timeout":
            raise TimeoutError()
        clock[0] += 3
        return {"ok": True, "status": {"ros2_status_age_sec": 0.1}}

    monkeypatch.setattr(robot_context.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(robot_context, "get_realtime_store", lambda: store)
    monkeypatch.setattr(robot_context, "get_robot_gateway", lambda url: SimpleNamespace(get_status=get_status))
    with pytest.raises(StopIteration):
        service.poll(binding, True).send(None)
    assert not service.context("r-001", binding)["available"]
    assert store.get_status("r-001") is None
