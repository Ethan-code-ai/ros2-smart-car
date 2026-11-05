from datetime import date, datetime, timedelta, timezone
import json
from uuid import uuid4

from app.api.routes import assistant
from app.models import Incident
from app.services.incident_reports import incident_report, record_coverage


def test_report_uses_shanghai_half_open_day_and_tracks_missing_coverage(seeded_db):
    start = datetime(2026, 11, 4, 16, tzinfo=timezone.utc)
    rows = [
        Incident(id=str(uuid4()), title="前一天", type="smoke_fire", occurred_at=start-timedelta(seconds=1)),
        Incident(id=str(uuid4()), title="需核查的箱子", type="suspected_clutter", status="acknowledged", occurred_at=start),
        Incident(id=str(uuid4()), title="当天最后一条", type="smoke_fire", occurred_at=start+timedelta(hours=23)),
        Incident(id=str(uuid4()), title="下一天", type="smoke_fire", occurred_at=start+timedelta(days=1)),
        Incident(id=str(uuid4()), title="时间未知", type="smoke_fire", created_at=start, occurred_at=None),
    ]
    seeded_db.add_all(rows)
    seeded_db.commit()
    robot = "fixture-robot-1"
    record_coverage(seeded_db, robot, "observed", now=start)
    record_coverage(seeded_db, robot, "interrupted", now=start+timedelta(minutes=1))
    record_coverage(seeded_db, robot, "observed", now=start+timedelta(minutes=1, seconds=20))
    try:
        report = incident_report(seeded_db, robot, date(2026, 11, 5), now=start+timedelta(hours=24))
        assert report["start"] == "2026-11-05T00:00:00+08:00"
        assert report["end"] == "2026-11-06T00:00:00+08:00"
        assert report["total"] == 2 and report["missing_occurrence_time"] == 1
        assert report["by_type"] == {"suspected_clutter": 1, "smoke_fire": 1}
        suspect = next(row for row in report["details"] if row["type"] == "suspected_clutter")
        assert "占道未确认" in suspect["type_label"] and suspect["status_label"] == "已确认事件"
        assert [item["state"] for item in report["coverage"]] == ["observed", "interrupted", "unknown"]
    finally:
        from app.models import SafetyCoverage
        from sqlalchemy import delete
        seeded_db.execute(delete(Incident).where(Incident.id.in_([row.id for row in rows])))
        seeded_db.execute(delete(SafetyCoverage).where(SafetyCoverage.robot_id == robot))
        seeded_db.commit()


def test_report_is_persisted_and_model_counts_cannot_replace_database(client, login, monkeypatch):
    class Model:
        async def complete(self, messages, tools):
            if messages[-1]["role"] == "tool":
                assert "total" in json.loads(messages[-1]["content"])
                return {"content": "今天确认堵塞 999999 条。"}
            return {"tool_calls": [{"id": "summary", "function": {"name": "get_incident_summary", "arguments": "{}"}}]}

    monkeypatch.setattr(assistant, "configured_provider", Model)
    admin = {"Authorization": "Bearer " + login("admin", "admin123")}
    operator = {"Authorization": "Bearer " + login("operator", "operator123")}
    root = "/api/v1/assistant"
    assert client.put(root + "/settings", headers=admin, json={"enabled": True}).status_code == 200
    try:
        session = client.post(root + "/sessions", headers=operator).json()["id"]
        reply = client.post(f"{root}/sessions/{session}/messages", headers=operator,
                            json={"client_id": str(uuid4()), "text": "总结今天的异常"})
        assert reply.status_code == 200, reply.text
        value = reply.json()["reply"]
        path = root + "/reports/" + value["report_id"]
        assert "999999" not in value["text"]
        assert client.get(path, headers=admin).status_code == 404
        report = client.get(path, headers=operator).json()
        assert str(report["total"]) in value["text"]
        export = client.get(path, headers=operator, params={"format": "markdown"})
        assert export.status_code == 200 and export.headers["content-type"].startswith("text/markdown")
        assert "异常事件报告" in export.text and "999999" not in export.text
    finally:
        client.put(root + "/settings", headers=admin, json={"enabled": False})
