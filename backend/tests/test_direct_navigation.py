import asyncio
from types import SimpleNamespace as NS
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.services import direct_navigation


def test_direct_navigation_gates_acceptance_and_releases_unconfirmed_dispatch(monkeypatch):
    binding = ("robot", "http://fixture")
    user = NS(id="user", permissions={"robots.control", "maps.read"})
    ident = str(uuid4())
    state = NS(accepted=False, calls=[])
    identity = {"controller_id": "control", "control_epoch": "epoch", "owner": "owner"}
    lease = NS(identity=lambda: identity)
    manager = NS(current=None, generation=0)

    async def acquire(actual, actor, permission, plan_id):
        assert (actual, actor, permission, plan_id) == (binding, user.id, "maps.read", ident)
        state.calls.append("acquire")
        manager.current = lease
        return lease

    async def release(plan_id, **values):
        assert plan_id == ident and values["lease"] is lease
        manager.current = None
        state.calls.append("release")

    async def request(path, payload):
        assert path == "start" and payload == {"plan_id": ident, "control_lease": identity}
        state.calls.append("start")
        raise httpx.ReadTimeout("lost")

    manager.acquire_for_run, manager.release_run = acquire, release
    monkeypatch.setattr(direct_navigation, "control_lease_manager", manager)
    monkeypatch.setattr(direct_navigation, "get_robot_gateway", lambda address: NS(navigation_request=request))
    monkeypatch.setattr(direct_navigation, "vehicle_binding", lambda db: binding)
    monkeypatch.setattr(direct_navigation, "capability_snapshot", lambda *args: {
        "capabilities": {"navigate": {"executable": state.accepted}}})
    with pytest.raises(HTTPException) as denied:
        asyncio.run(direct_navigation.start_direct_navigation(None, binding, user, {"plan_id": ident}))
    assert denied.value.status_code == 409 and not state.calls
    state.accepted = True
    direct_navigation.register_preview(binding, user.id, ident, 0)
    manager.generation += 1
    with pytest.raises(HTTPException, match="预览"):
        asyncio.run(direct_navigation.start_direct_navigation(None, binding, user, {"plan_id": ident}))
    assert not state.calls
    direct_navigation.register_preview(binding, user.id, ident, 1)
    with pytest.raises(HTTPException):
        asyncio.run(direct_navigation.start_direct_navigation(None, binding, user, {"plan_id": ident}))
    assert state.calls == ["acquire", "start", "release"] and manager.current is None
