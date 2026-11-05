import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import control_leases, operator_control


def test_takeover_requires_confirmed_stationary_evidence(monkeypatch):
    manager = control_leases.ControlLeaseManager()
    monkeypatch.setattr(operator_control, "control_lease_manager", manager)
    user = SimpleNamespace(id="u", permissions={"robots.control"})

    async def stop():
        manager.generation += 1
        return {"controller_confirmed": True, "stationary_confirmed": False}

    with pytest.raises(HTTPException, match="未确认车辆静止"):
        asyncio.run(operator_control.select_operator_mode(None, ("r", "car"), user, "mode", "manual", stop))
    assert manager.current is None


def test_drive_handle_is_bound_to_operator_and_orders_stop(monkeypatch):
    manager = control_leases.ControlLeaseManager()
    monkeypatch.setattr(operator_control, "control_lease_manager", manager)
    binding = ("r", "car")
    user = SimpleNamespace(id="u", permissions={"robots.control"})
    other = SimpleNamespace(id="other", permissions=user.permissions)
    calls = []

    async def lease_request(payload=None):
        calls.append(payload["op"] if payload else "query")
        if payload is None:
            return {"controller_id": "c", "control_epoch": "e", "fresh": True, "blocked": True}
        return {"confirmed": True, "lease": {**payload, "fresh": True, "blocked": False}}

    async def request(path, payload):
        calls.append((path, payload))
        return {"ok": True, **{key: payload[key] for key in ("mode", "command") if key in payload}}

    async def stop():
        calls.append("stop")
        manager.generation += 1
        return {"controller_confirmed": True, "stationary_confirmed": True}

    gateway = SimpleNamespace(control_lease=lease_request, operator_request=request)
    monkeypatch.setattr(control_leases, "get_robot_gateway", lambda _: gateway)
    monkeypatch.setattr(operator_control, "get_robot_gateway", lambda _: gateway)
    monkeypatch.setattr(operator_control, "require_bound_vehicle", lambda *_: binding)

    async def run():
        result = await operator_control.select_operator_mode(None, binding, user, "mode", "manual", stop)
        drive = result["drive_id"]
        assert calls[:3] == ["stop", "query", "acquire"]
        assert calls[3][1]["control_lease"] == manager.current.identity()
        with pytest.raises(HTTPException, match="控制权已失效"):
            await operator_control.send_drive(binding, other, "forward", drive, 1)
        await operator_control.send_drive(binding, user, "stop", drive, 2)
        with pytest.raises(HTTPException, match="更新的驾驶指令"):
            await operator_control.send_drive(binding, user, "forward", drive, 1)
        await operator_control.send_drive(binding, user, "forward", drive, 3)
        assert [(call[1]["command"], call[1]["drive_seq"]) for call in calls[4:]] == [("stop", 2), ("forward", 3)]

    asyncio.run(run())
