"""Controller-owned lease. Expiry/revoke changes epoch and cannot be renewed."""
import math
from uuid import uuid4


class ControlLease:
    TTL = 3.0

    def __init__(self):
        self.controller_id = uuid4().hex
        self.epoch = uuid4().hex
        self.owner = None
        self.purpose = None
        self.drive_sequence = -1
        self.sequence = -1
        self.deadline = 0.0
        self.reason = "control_lease_required"
        self.last_request_id = None
        self.forward_limit_mps = None

    def revoke(self, reason="control_revoked"):
        self.epoch = uuid4().hex
        self.owner = None
        self.purpose = None
        self.drive_sequence = -1
        self.sequence = -1
        self.deadline = 0.0
        self.reason = reason
        self.forward_limit_mps = None

    def blocked(self, now):
        if self.owner is not None and (not math.isfinite(now) or now >= self.deadline):
            self.revoke("control_lease_expired")
        return self.owner is None

    def update(self, value, now, stationary=False):
        self.blocked(now)
        if not math.isfinite(now) or not isinstance(value, dict):
            return False
        if value.get("op") == "stop" and value.get("controller_id") == self.controller_id:
            self.revoke("operator_stop")
            self.last_request_id = value.get("request_id")
            return True
        if value.get("controller_id") != self.controller_id or value.get("control_epoch") != self.epoch:
            return False
        owner, seq, op = value.get("owner"), value.get("sequence"), value.get("op")
        if not isinstance(owner, str) or not 1 <= len(owner) <= 128 or type(seq) is not int or seq < 0:
            return False
        if op == "acquire":
            purpose = value.get("purpose", "navigation")
            if self.owner is not None or not stationary or purpose not in ("navigation", "manual", "mapping"):
                return False
            limit = value.get("forward_limit_mps")
            if limit is not None and (purpose != "manual" or type(limit) not in (int,float)
                                      or not math.isfinite(limit) or not 0 < limit <= .10):
                return False
            self.owner = owner
            self.purpose = purpose
            self.forward_limit_mps = limit
        elif op == "renew":
            if self.owner != owner or seq <= self.sequence or value.get("forward_limit_mps") != self.forward_limit_mps:
                return False
        elif op == "release":
            if self.owner != owner or seq <= self.sequence:
                return False
            self.revoke("control_released")
            self.last_request_id = value.get("request_id")
            return True
        else:
            return False
        self.sequence, self.deadline, self.reason = seq, now + self.TTL, "control_lease_active"
        self.last_request_id = value.get("request_id")
        return True

    def accepts_drive(self, value, now, mode):
        if (self.blocked(now) or not isinstance(value, dict) or mode != self.purpose
                or mode not in ("manual", "mapping") or value.get("controller_id") != self.controller_id
                or value.get("control_epoch") != self.epoch or value.get("owner") != self.owner):
            return False
        sequence = value.get("drive_seq")
        if type(sequence) is not int or not 0 <= sequence <= 2**53 - 1 or sequence <= self.drive_sequence:
            return False
        self.drive_sequence = sequence
        return True

    def snapshot(self, now):
        blocked = self.blocked(now)
        return {"controller_id": self.controller_id, "control_epoch": self.epoch,
                "owner": self.owner, "purpose": self.purpose, "sequence": self.sequence, "blocked": blocked,
                "last_request_id": self.last_request_id,
                "forward_limit_mps": self.forward_limit_mps, "bounded_forward_supported": True,
                "remaining_sec": max(0., self.deadline-now) if not blocked else 0., "reason": self.reason}
