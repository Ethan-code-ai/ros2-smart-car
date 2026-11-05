"""Validate engineer-reviewed physical evidence before enabling a capability."""
from datetime import datetime, timezone
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool


CHECKS = {
    "move_relative": {"distance_bound", "speed_bound", "odometry_stop", "stale_odometry_stop", "cancel"},
    "navigate": {"localization", "goal_reached", "cancel", "obstacle_stop"},
    "patrol": {"localization", "route_completed", "dwell_transition", "cancel", "obstacle_stop"},
    "deliver": {"pickup_reached", "loaded_by_human", "destination_reached", "handover_by_human", "interruption"},
    "observe_target": {"stationary_lock", "no_motion_output", "target_confirmation", "expired_frame_rejected"},
    "patrol_search": {"route_completed", "dwell_transition", "target_stop", "plan_waits_human",
                      "search_resume", "evidence_saved", "expired_frame_rejected"},
}
COMMON_CHECKS = {"stop", "emergency_stop", "disconnect", "manual_takeover", "control_publishers"}


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Artifact(EvidenceModel):
    name: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TargetTrials(EvidenceModel):
    target: str = Field(min_length=1, max_length=64)
    appearances: int = Field(ge=10, strict=True)
    successful_stops: int = Field(ge=9, strict=True)


class MobileMeasurements(EvidenceModel):
    joint_duration_sec: float = Field(ge=600)
    effective_hz: float = Field(ge=3)
    frame_age_p95_sec: float = Field(ge=0, le=.5)
    control_overruns_added: int = Field(ge=0, le=0, strict=True)
    navigation_running: StrictBool
    video_running: StrictBool


class PhysicalEvidence(EvidenceModel):
    version: int = Field(ge=1, le=1, strict=True)
    environment: Literal["physical"]
    robot_id: str = Field(min_length=1, max_length=36)
    capability: Literal["navigate", "patrol", "deliver", "observe_target", "patrol_search", "move_relative"]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    map_key: str | None = Field(max_length=512)
    model_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    performed_at: datetime
    checks: dict[str, StrictBool]
    artifacts: list[Artifact] = Field(min_length=1, max_length=20)
    target_trials: list[TargetTrials] = Field(default_factory=list, max_length=30)
    interference_duration_sec: float | None = Field(default=None, ge=600)
    interference_false_stops: int | None = Field(default=None, ge=0, le=0, strict=True)
    mobile: MobileMeasurements | None = None


def validate_evidence(evidence, *, now=None):
    required = CHECKS[evidence.capability] | COMMON_CHECKS
    missing = required - {name for name, passed in evidence.checks.items() if passed is True}
    if missing:
        raise ValueError("缺少通过的实车检查项 " + "、".join(sorted(missing)))
    performed = evidence.performed_at
    now = now or datetime.now(timezone.utc)
    if performed.tzinfo is None or performed.timestamp() > now.timestamp() + 300:
        raise ValueError("验收时间必须包含时区且不能在未来")
    visual = evidence.capability in {"observe_target", "patrol_search"}
    if visual:
        if not evidence.model_sha256 or not evidence.target_trials:
            raise ValueError("搜索验收须记录模型摘要和逐目标试验")
        if evidence.interference_duration_sec is None or evidence.interference_false_stops != 0:
            raise ValueError("搜索验收须包含十分钟干扰测试")
        if any(trial.successful_stops > trial.appearances or
               trial.successful_stops / trial.appearances < .9 for trial in evidence.target_trials):
            raise ValueError("目标停车成功率不足或计数无效")
    if evidence.capability == "patrol_search" and evidence.mobile is None:
        raise ValueError("路线搜索缺少联合性能记录")
    if evidence.mobile is not None and not (evidence.mobile.navigation_running and evidence.mobile.video_running):
        raise ValueError("联合性能记录须同时运行导航与视频")
    return evidence


def canonical_evidence(evidence):
    encoded = json.dumps(evidence.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return encoded, hashlib.sha256(encoded.encode()).hexdigest()


def valid_record(record):
    if getattr(record, "revoked_at", None) is not None:
        return False
    try:
        evidence = validate_evidence(PhysicalEvidence.model_validate_json(record.evidence))
        encoded, digest = canonical_evidence(evidence)
        return (record.evidence == encoded and digest == record.evidence_sha256
                and evidence.robot_id == record.robot_id and evidence.capability == record.capability
                and evidence.fingerprint == record.fingerprint)
    except (ValueError, TypeError, AttributeError):
        return False
