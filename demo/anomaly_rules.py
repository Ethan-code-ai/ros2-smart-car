"""Pure stateful rules for the personnel anomaly demonstration.

The runner supplies person detections from a vision model.  This module keeps
the time based decisions independent from OpenCV, Ultralytics, and ROS2 so the
rules can be tested on a normal Python installation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Iterable, Mapping, Sequence


Point = tuple[float, float]
BBox = tuple[float, float, float, float]
# One COCO keypoint: image x, image y, detector confidence.
Keypoint = tuple[float, float, float]


EVENT_PRIORITY = {
    "fall_suspected": 0,
    "corridor_blocked": 1,
    "long_stay": 2,
}

# Indices into the 17-point COCO keypoint layout used by the YOLO pose models.
LEFT_SHOULDER = 5
RIGHT_SHOULDER = 6
LEFT_HIP = 11
RIGHT_HIP = 12

FALL_FEATURE_MODES = frozenset({"bbox", "angle", "auto", "either", "both"})


@dataclass(frozen=True)
class PersonDetection:
    track_id: int
    confidence: float
    bbox: BBox
    # Empty when a plain detection model produced this box.  The fall rule
    # falls back to the bounding-box shape in that case.
    keypoints: tuple[Keypoint, ...] = ()

    @property
    def center(self) -> Point:
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

    @property
    def bottom_center(self) -> Point:
        x1, _, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, y2)

    @property
    def width_height_ratio(self) -> float:
        x1, y1, x2, y2 = self.bbox
        height = max(1.0, y2 - y1)
        return max(0.0, x2 - x1) / height

    def _mean_point(self, indices: Sequence[int], min_confidence: float) -> Point | None:
        points = [
            (self.keypoints[i][0], self.keypoints[i][1])
            for i in indices
            if i < len(self.keypoints) and self.keypoints[i][2] >= min_confidence
        ]
        if not points:
            return None
        return (
            sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points),
        )

    def torso_angle_degrees(self, min_keypoint_confidence: float = 0.3) -> float | None:
        """Tilt of the shoulder-to-hip vector away from vertical, in degrees.

        0 means standing upright, 90 means the torso is horizontal.  Returns
        None when neither shoulder nor neither hip was observed confidently
        enough, so the caller can fall back to the bounding-box heuristic.

        Unlike the box aspect ratio this survives a truncated box and is far
        less sensitive to camera angle, which matters on a low-mounted robot
        camera where perspective distorts the box shape badly.
        """
        shoulder = self._mean_point((LEFT_SHOULDER, RIGHT_SHOULDER), min_keypoint_confidence)
        hip = self._mean_point((LEFT_HIP, RIGHT_HIP), min_keypoint_confidence)
        if shoulder is None or hip is None:
            return None
        dx = hip[0] - shoulder[0]
        dy = hip[1] - shoulder[1]
        if dx == 0.0 and dy == 0.0:
            return None
        # Image y grows downward; an upright torso is mostly vertical, so the
        # angle is measured off the vertical axis and is sign-independent.
        return math.degrees(math.atan2(abs(dx), abs(dy)))


@dataclass(frozen=True)
class AnomalyConfig:
    confidence: float = 0.35
    # A detection this weak cannot start a track, but may continue one that is
    # already established.  A body on the floor scores much lower than a
    # standing person, so a single hard gate throws away the frames that
    # matter most.
    track_confidence: float = 0.15
    fall_ratio: float = 1.1
    # How the box shape and the keypoint torso angle combine:
    #   "bbox"   box aspect ratio only (works without a pose model)
    #   "angle"  torso angle only; never fires when hips/shoulders are missing
    #   "auto"   torso angle when measurable, else fall back to the box
    #   "either" fallen if either feature says so   (highest recall)
    #   "both"   every measurable feature must agree (highest precision)
    fall_feature_mode: str = "bbox"
    fall_angle_degrees: float = 60.0
    keypoint_confidence: float = 0.3
    fall_seconds: float = 1.5
    linger_seconds: float = 8.0
    block_seconds: float = 4.0
    track_lost_grace_seconds: float = 1.0
    cooldown_seconds: float = 5.0
    stationary_speed_ratio: float = 0.05
    corridor_overlap_ratio: float = 0.25
    # "map" consumes a corridor occupancy signal from the robot/map layer.
    # "image_zone" keeps the old standalone rectangle heuristic for demos
    # that intentionally provide a calibrated camera region.
    corridor_source: str = "map"
    linger_zone: tuple[float, float, float, float] = (0.05, 0.08, 0.95, 0.95)
    corridor_zone: tuple[float, float, float, float] = (0.35, 0.05, 0.65, 0.95)

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "AnomalyConfig":
        defaults = cls()

        def number(name: str) -> float:
            value = values.get(name, getattr(defaults, name))
            result = float(value)
            if result < 0:
                raise ValueError(f"{name} must be non-negative")
            return result

        def ratio(name: str) -> float:
            result = number(name)
            if result > 1:
                raise ValueError(f"{name} must be between 0 and 1")
            return result

        def zone(name: str) -> tuple[float, float, float, float]:
            raw = values.get(name, getattr(defaults, name))
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) != 4:
                raise ValueError(f"{name} must contain four normalized coordinates")
            result = tuple(float(item) for item in raw)
            if any(item < 0 or item > 1 for item in result):
                raise ValueError(f"{name} coordinates must be between 0 and 1")
            x1, y1, x2, y2 = result
            if x2 <= x1 or y2 <= y1:
                raise ValueError(f"{name} must have positive width and height")
            return result

        corridor_source = str(values.get("corridor_source", defaults.corridor_source)).strip().lower()
        if corridor_source not in {"map", "image_zone"}:
            raise ValueError("corridor_source must be 'map' or 'image_zone'")

        confidence = number("confidence")
        track_confidence = ratio("track_confidence")
        if track_confidence > confidence:
            raise ValueError("track_confidence must not exceed confidence")
        fall_angle_degrees = number("fall_angle_degrees")
        if fall_angle_degrees > 90:
            raise ValueError("fall_angle_degrees must be between 0 and 90")

        fall_feature_mode = str(
            values.get("fall_feature_mode", defaults.fall_feature_mode)
        ).strip().lower()
        if fall_feature_mode not in FALL_FEATURE_MODES:
            raise ValueError(f"fall_feature_mode must be one of {sorted(FALL_FEATURE_MODES)}")

        return cls(
            confidence=confidence,
            track_confidence=track_confidence,
            fall_ratio=number("fall_ratio"),
            fall_feature_mode=fall_feature_mode,
            fall_angle_degrees=fall_angle_degrees,
            keypoint_confidence=ratio("keypoint_confidence"),
            fall_seconds=number("fall_seconds"),
            linger_seconds=number("linger_seconds"),
            block_seconds=number("block_seconds"),
            track_lost_grace_seconds=number("track_lost_grace_seconds"),
            cooldown_seconds=number("cooldown_seconds"),
            stationary_speed_ratio=number("stationary_speed_ratio"),
            corridor_overlap_ratio=ratio("corridor_overlap_ratio"),
            corridor_source=corridor_source,
            linger_zone=zone("linger_zone"),
            corridor_zone=zone("corridor_zone"),
        )


@dataclass(frozen=True)
class AnomalyEvent:
    event_id: str
    event_type: str
    state: str
    track_id: int
    timestamp: float
    started_at: float
    duration_seconds: float
    confidence: float
    bbox: BBox
    reason: str = "threshold_reached"


@dataclass
class TrackSnapshot:
    track_id: int
    bbox: BBox
    confidence: float
    active_type: str | None
    candidate_durations: dict[str, float]


@dataclass
class _TrackState:
    detection: PersonDetection
    last_seen: float
    previous_center: Point
    fall_since: float | None = None
    linger_since: float | None = None
    block_since: float | None = None
    active_type: str | None = None
    active_started_at: float | None = None
    active_event_id: str | None = None
    cooldown_until: dict[str, float] = field(default_factory=dict)


def _normalized_zone(zone: tuple[float, float, float, float], width: int, height: int) -> BBox:
    x1, y1, x2, y2 = zone
    return (x1 * width, y1 * height, x2 * width, y2 * height)


def point_in_zone(point: Point, zone: BBox) -> bool:
    x, y = point
    x1, y1, x2, y2 = zone
    return x1 <= x <= x2 and y1 <= y <= y2


def intersection_ratio(box: BBox, zone: BBox) -> float:
    x1, y1, x2, y2 = box
    zx1, zy1, zx2, zy2 = zone
    overlap_width = max(0.0, min(x2, zx2) - max(x1, zx1))
    overlap_height = max(0.0, min(y2, zy2) - max(y1, zy1))
    box_area = max(1.0, (x2 - x1) * (y2 - y1))
    return (overlap_width * overlap_height) / box_area


class AnomalyEngine:
    """Turn person detections into debounced anomaly start/end events."""

    def __init__(self, config: AnomalyConfig | None = None):
        self.config = config or AnomalyConfig()
        self._tracks: dict[int, _TrackState] = {}
        self._event_counter = 0

    @property
    def tracks(self) -> Mapping[int, _TrackState]:
        return self._tracks

    def update(
        self,
        detections: Iterable[PersonDetection],
        *,
        frame_width: int,
        frame_height: int,
        now: float,
        corridor_occupied: bool = False,
    ) -> tuple[list[AnomalyEvent], list[TrackSnapshot]]:
        events: list[AnomalyEvent] = []
        snapshots: list[TrackSnapshot] = []
        seen: set[int] = set()
        linger_zone = _normalized_zone(self.config.linger_zone, frame_width, frame_height)
        corridor_zone = _normalized_zone(self.config.corridor_zone, frame_width, frame_height)

        for detection in detections:
            state = self._tracks.get(detection.track_id)
            # Two-tier gate: an established track survives on weak detections,
            # but a weak detection on its own cannot create one (which would
            # let furniture or a shadow raise an alarm).
            floor = self.config.track_confidence if state is not None else self.config.confidence
            if detection.confidence < floor:
                continue
            seen.add(detection.track_id)
            if state is None:
                state = _TrackState(
                    detection=detection,
                    last_seen=now,
                    previous_center=detection.center,
                )
                self._tracks[detection.track_id] = state
                movement_ratio = 0.0
            else:
                elapsed = max(0.001, now - state.last_seen)
                dx = detection.center[0] - state.previous_center[0]
                dy = detection.center[1] - state.previous_center[1]
                frame_diagonal = max(1.0, (frame_width**2 + frame_height**2) ** 0.5)
                movement_ratio = ((dx * dx + dy * dy) ** 0.5) / frame_diagonal / elapsed
                state.detection = detection
                state.last_seen = now
                state.previous_center = detection.center

            if self.looks_fallen(detection):
                state.fall_since = state.fall_since if state.fall_since is not None else now
            else:
                state.fall_since = None

            in_linger_zone = point_in_zone(detection.bottom_center, linger_zone)
            if in_linger_zone and movement_ratio <= self.config.stationary_speed_ratio:
                state.linger_since = state.linger_since if state.linger_since is not None else now
            else:
                state.linger_since = None

            if self.config.corridor_source == "map":
                # The map layer owns this decision after mapping has produced
                # a baseline.  A camera frame alone cannot tell whether a
                # visually central person is occupying a real corridor.
                corridor_match = bool(corridor_occupied)
            else:
                corridor_match = intersection_ratio(detection.bbox, corridor_zone) >= self.config.corridor_overlap_ratio

            if corridor_match:
                state.block_since = state.block_since if state.block_since is not None else now
            else:
                state.block_since = None

            selected = self._selected_candidate(state, now)
            events.extend(self._apply_selection(state, selected, now))
            snapshots.append(self._snapshot(state, now))

        for track_id, state in list(self._tracks.items()):
            if track_id in seen:
                continue
            if now - state.last_seen <= self.config.track_lost_grace_seconds:
                snapshots.append(self._snapshot(state, now))
                continue
            if state.active_type is not None:
                events.append(self._end_event(state, now, "track_lost"))
            del self._tracks[track_id]

        return events, snapshots

    def reset(self, now: float) -> list[AnomalyEvent]:
        events = [self._end_event(state, now, "manual_reset") for state in self._tracks.values() if state.active_type]
        self._tracks.clear()
        return events

    def fall_feature(self, detection: PersonDetection) -> tuple[str, float]:
        """Which feature decides this detection, and its value.

        Exposed so evaluation and on-screen overlays can show whether a
        decision came from the keypoints or from the box shape.
        """
        mode = self.config.fall_feature_mode
        if mode in ("angle", "auto", "either", "both"):
            angle = detection.torso_angle_degrees(self.config.keypoint_confidence)
            if angle is not None:
                return "torso_angle", angle
            if mode == "angle":
                return "unavailable", 0.0
        return "bbox_ratio", detection.width_height_ratio

    def looks_fallen(self, detection: PersonDetection) -> bool:
        mode = self.config.fall_feature_mode
        angle = (
            None
            if mode == "bbox"
            else detection.torso_angle_degrees(self.config.keypoint_confidence)
        )
        by_angle = None if angle is None else angle >= self.config.fall_angle_degrees
        by_box = detection.width_height_ratio >= self.config.fall_ratio

        if mode == "bbox":
            return by_box
        if mode == "angle":
            return bool(by_angle)  # no keypoints means no decision, so no alarm
        if mode == "auto":
            return by_box if by_angle is None else by_angle
        if mode == "either":
            return by_box or bool(by_angle)
        # "both": every feature we could measure has to agree.
        return by_box if by_angle is None else (by_box and by_angle)

    def _selected_candidate(self, state: _TrackState, now: float) -> str | None:
        candidates: list[tuple[str, float, float]] = []
        if state.fall_since is not None:
            candidates.append(("fall_suspected", state.fall_since, self.config.fall_seconds))
        if state.block_since is not None:
            candidates.append(("corridor_blocked", state.block_since, self.config.block_seconds))
        if state.linger_since is not None:
            candidates.append(("long_stay", state.linger_since, self.config.linger_seconds))
        valid = [item for item in candidates if now - item[1] >= item[2]]
        if not valid:
            return None
        return min(valid, key=lambda item: EVENT_PRIORITY[item[0]])[0]

    def _apply_selection(self, state: _TrackState, selected: str | None, now: float) -> list[AnomalyEvent]:
        events: list[AnomalyEvent] = []
        if selected == state.active_type:
            return events
        if state.active_type is not None:
            events.append(self._end_event(state, now, "condition_changed"))
        if selected is None:
            return events
        if now < state.cooldown_until.get(selected, 0.0):
            return events
        state.active_type = selected
        state.active_started_at = now
        state.active_event_id = self._new_event_id(selected, state.detection.track_id, now)
        events.append(
            AnomalyEvent(
                event_id=state.active_event_id,
                event_type=selected,
                state="started",
                track_id=state.detection.track_id,
                timestamp=now,
                started_at=now,
                duration_seconds=0.0,
                confidence=state.detection.confidence,
                bbox=state.detection.bbox,
            )
        )
        return events

    def _end_event(self, state: _TrackState, now: float, reason: str) -> AnomalyEvent:
        assert state.active_type is not None
        assert state.active_started_at is not None
        event = AnomalyEvent(
            event_id=state.active_event_id or self._new_event_id(state.active_type, state.detection.track_id, now),
            event_type=state.active_type,
            state="ended",
            track_id=state.detection.track_id,
            timestamp=now,
            started_at=state.active_started_at,
            duration_seconds=max(0.0, now - state.active_started_at),
            confidence=state.detection.confidence,
            bbox=state.detection.bbox,
            reason=reason,
        )
        state.cooldown_until[state.active_type] = now + self.config.cooldown_seconds
        state.active_type = None
        state.active_started_at = None
        state.active_event_id = None
        return event

    def _snapshot(self, state: _TrackState, now: float) -> TrackSnapshot:
        durations = {}
        for name, started_at, threshold in (
            ("fall_suspected", state.fall_since, self.config.fall_seconds),
            ("corridor_blocked", state.block_since, self.config.block_seconds),
            ("long_stay", state.linger_since, self.config.linger_seconds),
        ):
            durations[name] = 0.0 if started_at is None else min(now - started_at, threshold)
        return TrackSnapshot(
            track_id=state.detection.track_id,
            bbox=state.detection.bbox,
            confidence=state.detection.confidence,
            active_type=state.active_type,
            candidate_durations=durations,
        )

    def _new_event_id(self, event_type: str, track_id: int, now: float) -> str:
        self._event_counter += 1
        return f"{event_type}-{track_id}-{int(now * 1000)}-{self._event_counter}"
