"""Single-plan field authorization. Never changes formal capability acceptance."""
import hashlib
import json
import time

from fastapi import HTTPException

from ..models import AssistantPlan, AssistantSession, AssistantTrialAuthorization
from .assistant_tools import require
from .capabilities import capability_snapshot
from .ops import record_audit
from .robot_context import robot_context_service, vehicle_binding

ALLOWED = frozenset({"observe_target", "move_relative"})


def trial_step(plan):
    if hashlib.sha256(plan.content.encode()).hexdigest() != plan.digest:
        raise HTTPException(409, "计划内容已变化")
    steps = json.loads(plan.content)["steps"]
    if len(steps) != 1 or steps[0]["action"] not in ALLOWED:
        raise HTTPException(409, "现场测试仅支持单步静止识别或受限前进")
    return steps[0]


def require_trial(db, user, plan_id, capability, fingerprint, execution_id=None):
    require(user, "assistant.settings", "assistant.use", "robots.manage", "robots.control")
    plan, grant = db.get(AssistantPlan, plan_id), db.get(AssistantTrialAuthorization, plan_id)
    if not plan or not grant or grant.user_id != user.id or grant.expires_at <= int(time.time()):
        raise HTTPException(409, "单次现场测试授权不存在或已过期")
    session = db.get(AssistantSession, plan.session_id)
    step = trial_step(plan)
    if (not session or session.user_id != user.id or step["action"] != capability
            or grant.plan_version != plan.version or grant.digest != plan.digest
            or grant.fingerprint != fingerprint or grant.execution_id != execution_id):
        raise HTTPException(409, "现场测试授权与计划、构建或执行不匹配")
    return grant


def authorize_trial(db, user, plan, version, digest):
    require(user, "assistant.settings", "assistant.use", "robots.manage", "robots.control")
    if plan.state != "draft" or plan.expires_at <= int(time.time()) or plan.version != version or plan.digest != digest:
        raise HTTPException(409, "只能授权当前未过期的草案")
    step = trial_step(plan)
    binding = vehicle_binding(db)
    if binding[0] != plan.robot_id:
        raise HTTPException(409, "实车绑定已变化")
    context = robot_context_service.context(plan.robot_id, binding)
    capability = capability_snapshot(db, context)["capabilities"][step["action"]]
    if (not capability["supported"] or not capability["ready"]
            or capability["fingerprint"] != step["capability"]["fingerprint"]):
        raise HTTPException(409, "车辆不支持、尚未就绪或构建已变化")
    grant = db.get(AssistantTrialAuthorization, plan.id)
    if grant is not None:
        require_trial(db, user, plan.id, step["action"], capability["fingerprint"])
        return {"authorized": True, "expires_at": grant.expires_at}
    grant = AssistantTrialAuthorization(
        plan_id=plan.id, user_id=user.id, plan_version=version, digest=digest,
        fingerprint=capability["fingerprint"], expires_at=min(plan.expires_at, int(time.time())+300))
    db.add(grant)
    record_audit(db, user, "assistant.trial.authorize", "assistant_plan", plan.id,
                 after={"version": version, "digest": digest, "capability": step["action"],
                        "fingerprint": grant.fingerprint, "expires_at": grant.expires_at,
                        "formal_acceptance_changed": False})
    db.commit()
    return {"authorized": True, "expires_at": grant.expires_at}
