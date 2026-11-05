import json
import threading
import time
from collections import deque
from pathlib import Path


class PoseDebugLog:
    def __init__(self, path="/tmp/smart_car_logs/pose_debug.jsonl", max_entries=1200, max_bytes=5*1024*1024):
        self.path = Path(path)
        self.max_entries = int(max_entries)
        self._lock = threading.Lock()
        self._entries = deque(maxlen=self.max_entries)
        self.max_bytes = max(1024, int(max_bytes))
        self.write_error = None

    def append_status(self, status):
        entry = {
            "logged_at": round(time.time(), 4),
            "mode": status.get("mode", ""),
            "last_command": status.get("last_command", ""),
            "speed_scale": status.get("speed_scale", 0.0),
            "map_pose": status.get("map_pose", {}),
            "odom": status.get("odom", {}),
            "cmd_vel": status.get("cmd_vel", {}),
            "tf": status.get("tf", {}),
            "map": status.get("map", {}),
            "updated_at": status.get("updated_at"),
        }
        with self._lock:
            self._entries.append(entry)
            self._write_line(entry)
        return entry

    def clear(self):
        with self._lock:
            self._entries.clear()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def snapshot(self, limit=None):
        with self._lock:
            entries = list(self._entries)
        if limit is not None:
            entries = entries[-int(limit):]
        return {
            "ok": True,
            "count": len(entries),
            "path": str(self.path),
            "entries": entries,
            **({"write_error": self.write_error} if self.write_error else {}),
        }

    def _write_line(self, entry):
        try:
            line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
            size = len(line.encode("utf-8"))
            if size > self.max_bytes:
                self.write_error = "diagnostic_entry_too_large"
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size + size > self.max_bytes:
                self.path.replace(self.path.with_name(self.path.name + ".previous"))
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            self.write_error = None
        except OSError as exc:
            # Diagnostic persistence must not terminate the control/status node.
            self.write_error = "diagnostic_write_failed:" + str(exc.errno)
