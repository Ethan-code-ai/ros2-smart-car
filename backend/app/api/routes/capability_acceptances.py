from datetime import datetime, timezone
import math
import json
from uuid import UUID
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..deps import get_db, require_permission
from ...models import CapabilityAcceptance, User
from ...services.capabilities import evaluate_capabilities
from ...services.capability_evidence import CHECKS, COMMON_CHECKS, EvidenceModel, PhysicalEvidence, canonical_evidence, validate_evidence, valid_record
from ...services.control_access import require_bound_vehicle
from ...services.ops import record_audit
from ...services.robot_context import robot_context_service

router = APIRouter(prefix="/robots", tags=["capability acceptance"])


class AcceptanceIn(EvidenceModel):
    request_id: UUID
    evidence: PhysicalEvidence


class RevokeIn(EvidenceModel):
    reason: str = Field(min_length=1, max_length=512)


@router.get("/{robot_id}/acceptance-template")
def acceptance_template(robot_id: str, capability: Literal["navigate", "patrol", "deliver", "observe_target", "patrol_search", "move_relative"],
                        db: Session = Depends(get_db), user: User = Depends(require_permission("robots.manage"))):
    binding = require_bound_vehicle(db, robot_id)
    context = robot_context_service.context(robot_id, binding)
    state = evaluate_capabilities(context, [])["capabilities"][capability]
    telemetry = context.get("telemetry") or {}
    visual = capability in {"observe_target", "patrol_search"}
    evidence = {
        "version": 1, "environment": "physical", "robot_id": robot_id, "capability": capability,
        "fingerprint": state["fingerprint"],
        "source_sha256": (telemetry.get("component_manifest") or {}).get("source_sha256"),
        "map_key": telemetry.get("active_map_key"), "performed_at": None,
        "checks": dict.fromkeys(sorted(CHECKS[capability] | COMMON_CHECKS), False), "artifacts": [],
    }
    if visual:
        evidence.update(model_sha256=(telemetry.get("vision") or {}).get("model_sha256"), target_trials=[],
                        interference_duration_sec=None, interference_false_stops=None)
    if capability == "patrol_search":
        evidence["mobile"] = {"joint_duration_sec": None, "effective_hz": None, "frame_age_p95_sec": None,
                              "control_overruns_added": None, "navigation_running": False, "video_running": False}
    return {"request_id": str(uuid4()), "evidence": evidence}


def view(row):
    return {"id": row.id, "robot_id": row.robot_id, "capability": row.capability,
            "fingerprint": row.fingerprint, "evidence_sha256": row.evidence_sha256,
            "accepted_by": row.accepted_by, "created_at": row.created_at,
            "revoked_by": row.revoked_by, "revoked_at": row.revoked_at, "revocation_reason": row.revocation_reason}


@router.get("/{robot_id}/acceptances")
def list_acceptances(robot_id: str, db: Session = Depends(get_db),
                     user: User = Depends(require_permission("robots.read"))):
    require_bound_vehicle(db, robot_id)
    rows = db.scalars(select(CapabilityAcceptance).where(CapabilityAcceptance.robot_id == robot_id)
                      .order_by(CapabilityAcceptance.created_at.desc()).limit(200)).all()
    return {"items": [view(row) for row in rows]}


@router.get("/{robot_id}/acceptances/{ident}")
def get_acceptance(robot_id: str, ident: str, db: Session = Depends(get_db),
                   user: User = Depends(require_permission("robots.read"))):
    require_bound_vehicle(db, robot_id)
    row = db.get(CapabilityAcceptance, ident)
    if row is None or row.robot_id != robot_id:
        raise HTTPException(404, "验收记录不存在")
    try:
        evidence = json.loads(row.evidence)
    except (ValueError, TypeError):
        evidence = {"legacy_text": row.evidence}
    return {**view(row), "evidence": evidence, "evidence_valid": valid_record(row)}


def registration_context(db, robot_id, evidence):
    binding = require_bound_vehicle(db, robot_id)
    context = robot_context_service.context(robot_id, binding)
    capability = evaluate_capabilities(context, [])["capabilities"][evidence.capability]
    telemetry = context.get("telemetry") or {}
    odom = telemetry.get("odom") or {}
    readings = [odom.get(key) for key in ("odom_age_sec", "linear_speed", "angular_speed")]
    stationary = (odom.get("ok") is True and all(type(value) in (int, float) and math.isfinite(value) for value in readings)
                  and 0 <= readings[0] <= .5 and abs(readings[1]) < .01 and abs(readings[2]) < .02)
    if not capability["ready"] or telemetry.get("mode") != "stop" or not stationary:
        raise HTTPException(409, "登记前须确认车端就绪且已静止")
    if (evidence.robot_id != robot_id or evidence.fingerprint != capability["fingerprint"]
            or evidence.source_sha256 != (telemetry.get("component_manifest") or {}).get("source_sha256")
            or evidence.map_key != telemetry.get("active_map_key")
            or (evidence.capability in {"observe_target", "patrol_search"} and
                evidence.model_sha256 != (telemetry.get("vision") or {}).get("model_sha256"))):
        raise HTTPException(409, "验收证据与当前车辆、构建、地图或模型不匹配")


@router.post("/{robot_id}/acceptances")
def register(robot_id: str, payload: AcceptanceIn, db: Session = Depends(get_db),
             user: User = Depends(require_permission("robots.manage"))):
    if not {"robots.read", "robots.control"} <= user.permissions:
        raise HTTPException(403, "缺少车辆读取或控制权限")
    require_bound_vehicle(db, robot_id)
    try:
        evidence = validate_evidence(payload.evidence)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    encoded, digest = canonical_evidence(evidence)
    ident = str(payload.request_id)
    previous = db.get(CapabilityAcceptance, ident)
    if previous is not None:
        if previous.robot_id != robot_id or previous.accepted_by != user.id or previous.evidence_sha256 != digest:
            raise HTTPException(409, "重复登记不能改变证据或审核人")
        return view(previous)
    registration_context(db, robot_id, evidence)
    row = CapabilityAcceptance(id=ident, robot_id=robot_id, capability=evidence.capability,
                               fingerprint=evidence.fingerprint, evidence=encoded, evidence_sha256=digest,
                               accepted_by=user.id)
    db.add(row)
    record_audit(db, user, "robots.acceptance.register", "capability_acceptance", ident,
                 after={"capability": row.capability, "fingerprint": row.fingerprint, "evidence_sha256": digest})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        previous = db.get(CapabilityAcceptance, ident)
        if previous is None or previous.robot_id != robot_id or previous.accepted_by != user.id or previous.evidence_sha256 != digest:
            raise HTTPException(409, "登记发生冲突") from None
        return view(previous)
    return view(row)


@router.post("/{robot_id}/acceptances/{ident}/revoke")
def revoke(robot_id: str, ident: str, payload: RevokeIn, db: Session = Depends(get_db),
           user: User = Depends(require_permission("robots.manage"))):
    require_bound_vehicle(db, robot_id)
    row = db.get(CapabilityAcceptance, ident)
    if row is None or row.robot_id != robot_id:
        raise HTTPException(404, "验收记录不存在")
    if row.revoked_at is None:
        claim = db.execute(update(CapabilityAcceptance).where(
            CapabilityAcceptance.id == ident, CapabilityAcceptance.revoked_at.is_(None)
        ).values(revoked_at=datetime.now(timezone.utc), revoked_by=user.id, revocation_reason=payload.reason))
        if claim.rowcount == 1:
            record_audit(db, user, "robots.acceptance.revoke", "capability_acceptance", ident, after={"reason": payload.reason})
        db.commit()
        db.refresh(row)
    return view(row)
