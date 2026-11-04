"""Persistent avoidance configuration and acknowledged web/controller handshake."""
import json
import math
import os
import threading
import time
from pathlib import Path
from uuid import uuid4

DEFAULTS = {"enabled": True, "trigger_distance_m": .6}


def validate_settings(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        raise ValueError("避障设置必须包含 enabled 和 trigger_distance_m")
    distance = value["trigger_distance_m"]
    if type(value["enabled"]) is not bool or isinstance(distance, bool) or not isinstance(distance, (int, float)) or not math.isfinite(distance) or not .4 <= distance <= 1.5:
        raise ValueError("避障开关须为布尔值，触发距离须为 0.40–1.50 米")
    return {"enabled": value["enabled"], "trigger_distance_m": round(distance, 3)}


class AvoidanceSettings:
    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.value, self.error = dict(DEFAULTS), None
        try:
            if self.path.exists():
                self.value = validate_settings(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError) as exc:
            self.error = "避障设置损坏，请停车后重新保存：" + str(exc)

    def save(self, value):
        value = validate_settings(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + "." + uuid4().hex + ".tmp")
        try:
            temp.write_text(json.dumps(value), encoding="utf-8")
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)
        self.value, self.error = value, None


class AvoidanceService:
    """Only the controller persists settings. HTTP success means pending, not applied."""
    def __init__(self, node, path, publish, clock=time.monotonic):
        self.node, self.publish, self.clock = node, publish, clock
        self.lock = threading.RLock()
        self.settings = AvoidanceSettings(path)
        self.request_id = uuid4().hex
        self.requested = None if self.settings.error else dict(self.settings.value)
        self.actual = None
        self.received_at = None

    def transmit(self):
        with self.lock:
            if self.requested is not None:
                self.publish({"request_id": self.request_id, "settings": self.requested,
                              "robot_radius_m": getattr(self.node, 'navigation_radius', .25)})

    def observe(self, message):
        with self.lock:
            if not isinstance(message, dict) or not isinstance(message.get("confirmed"), bool):
                return
            self.actual = message
            self.received_at = self.clock()

    def snapshot(self):
        with self.lock:
            actual = dict(self.actual or {})
            fresh = self.received_at is not None and self.clock() - self.received_at <= .6
            confirmed = (fresh and actual.get("confirmed") is True and actual.get("request_id") == self.request_id
                         and actual.get("settings") == self.requested
                         and actual.get('robot_radius_m') == getattr(self.node, 'navigation_radius', .25))
            result = {**actual, "supported": True, "confirmed": bool(confirmed), "available": bool(confirmed and actual.get("available")),
                      "requested": self.requested, "settings": actual.get("settings", self.requested or self.settings.value)}
            if not confirmed:
                fault = fresh and actual.get('state') == 'sensor_fault'
                result.update(state='sensor_fault' if fault or self.settings.error else 'waiting',
                              reason=self.settings.error or (actual.get('reason') if fault else '等待控制器确认避障设置'))
            return result

    def update(self, changes):
        with self.lock:
            state = self.node.store.snapshot()
            odom = state.get("odom", {})
            age = self.node.ros_status_age_sec()
            values = (odom.get("linear_speed_xy"), odom.get("angular_speed"))
            if (age is None or age > .5 or state.get("mode") != "stop" or not odom.get("ok") or
                    any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in values) or
                    abs(values[0]) > .01 or abs(values[1]) > .03):
                raise RuntimeError("请停止小车并等待有效里程计确认静止后再修改避障设置")
            if not isinstance(changes, dict) or not changes or set(changes) - set(DEFAULTS):
                raise ValueError("无效的避障设置")
            self.requested = validate_settings({**(self.requested or self.settings.value), **changes})
            self.request_id = uuid4().hex
            self.settings.error = None
            self.transmit()
            return self.snapshot()
