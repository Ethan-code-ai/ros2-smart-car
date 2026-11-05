import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import MissionTask, Robot
from app.services.navigation_execution import execution
from app.services import task_reconciler


@pytest.mark.parametrize("reply_id,reply_state,expected", [
    ("run-1", "waiting_handover", "waiting_handover"),
    ("wrong-run", "completed", "running"),
    ("run-1", "invented", "running"),
])
def test_background_reads_match_identity_and_vehicle_state(monkeypatch, reply_id, reply_state, expected):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    with sessions() as db:
        db.add(Robot(id="r-001", name="fixture", serial="fixture"))
        db.add(MissionTask(id="mission-1", name="fixture", robot_id="r-001",
                           status="transporting", steps=json.dumps([{"execution": {
                               "run_id": "run-1", "task_id": "mission-1", "state": "running", "version": 1,
                           }}])))
        db.commit()
    calls = []

    async def request(path, payload=None, *, query=None):
        calls.append((path, payload, query))
        return {"run_id": reply_id, "task_id": "mission-1", "state": reply_state, "version": 2}

    monkeypatch.setattr(task_reconciler, "get_robot_gateway",
                        lambda url: SimpleNamespace(navigation_request=request))
    # The fake transport has no suspension points and cannot contact a vehicle.
    with pytest.raises(StopIteration):
        task_reconciler.reconcile_once(sessions).send(None)
    assert calls == [("task", None, {"run_id": "run-1"})]
    with sessions() as db:
        assert execution(db.get(MissionTask, "mission-1"))["state"] == expected
    engine.dispose()
