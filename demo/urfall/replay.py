"""Replay cached detections through AnomalyEngine to get a per-frame signal.

Pure Python: no torch, no ultralytics, no OpenCV.  That is what makes the
threshold sweep cheap enough to run a few hundred configurations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.anomaly_rules import AnomalyConfig, AnomalyEngine, AnomalyEvent
from demo.urfall.detect import CachedSequence, FrameDetections

FALL = "fall_suspected"

GHOST_INCLUDE = "include"
GHOST_EXCLUDE = "exclude"
GHOST_POLICIES = (GHOST_INCLUDE, GHOST_EXCLUDE)


@dataclass(frozen=True)
class FrameSignal:
    frame: int
    now: float
    alarm: bool
    ghost: bool
    n_detections: int
    n_tracks: int
    max_ratio: float
    # Number of detections this frame that the engine considers fallen, by
    # whichever feature it chose (torso angle when keypoints exist, else the
    # box aspect ratio).  Feature-agnostic, unlike max_ratio.
    n_fallen: int = 0
    max_torso_angle: float | None = None
    feature: str = "bbox_ratio"


@dataclass
class SequenceReplay:
    sequence: str
    signals: list[FrameSignal] = field(default_factory=list)
    events: list[AnomalyEvent] = field(default_factory=list)
    track_ids: set[int] = field(default_factory=set)

    @property
    def alarmed(self) -> bool:
        """Did a fall alarm ever start in this sequence?"""
        return any(e.event_type == FALL and e.state == "started" for e in self.events)

    @property
    def start_events(self) -> list[AnomalyEvent]:
        return [e for e in self.events if e.event_type == FALL and e.state == "started"]

    @property
    def condition_changed_ends(self) -> int:
        """Fall alarms cancelled by the ratio dipping below threshold.

        Each one arms a ``cooldown_seconds`` window, so a single bad frame can
        suppress the alarm for the rest of a short sequence.
        """
        return sum(
            1
            for e in self.events
            if e.event_type == FALL and e.state == "ended" and e.reason == "condition_changed"
        )

    @property
    def first_alarm_frame(self) -> int | None:
        for signal in self.signals:
            if signal.alarm:
                return signal.frame
        return None


def replay_frames(
    frames: list[FrameDetections],
    config: AnomalyConfig,
    *,
    sequence: str,
    frame_width: int,
    frame_height: int,
    ghost_policy: str = GHOST_INCLUDE,
) -> SequenceReplay:
    """Drive AnomalyEngine frame by frame and record when a fall alarm is active.

    The per-frame prediction is taken from ``TrackSnapshot.active_type`` rather
    than from the event stream: that is exactly what the GUI draws and what a
    downstream consumer sees as "an alarm is active right now".  The two are
    equivalent by construction (``active_type`` transitions are what emit the
    started/ended events), and ``alarm_frames_from_events`` asserts that.
    """
    if ghost_policy not in GHOST_POLICIES:
        raise ValueError(f"ghost_policy must be one of {GHOST_POLICIES}")

    engine = AnomalyEngine(config)
    replay = SequenceReplay(sequence=sequence)

    for item in frames:
        events, snapshots = engine.update(
            item.detections,
            frame_width=frame_width,
            frame_height=frame_height,
            now=item.now,
        )
        replay.events.extend(events)

        # Ask the engine which tracks it actually accepted this frame rather
        # than re-deriving the two-tier confidence gate out here.
        live = {tid for tid, st in engine.tracks.items() if st.last_seen == item.now}
        replay.track_ids.update(live)
        accepted = [d for d in item.detections if d.track_id in live]

        firing = [s for s in snapshots if s.active_type == FALL]
        on_screen = any(s.track_id in live for s in firing)
        alarm = bool(firing) if ghost_policy == GHOST_INCLUDE else on_screen

        angles = [
            a
            for a in (d.torso_angle_degrees(config.keypoint_confidence) for d in accepted)
            if a is not None
        ]
        feature = config.fall_feature_mode

        replay.signals.append(
            FrameSignal(
                frame=item.frame,
                now=item.now,
                alarm=alarm,
                ghost=bool(firing) and not on_screen,
                n_detections=len(live),
                n_tracks=len(snapshots),
                max_ratio=max((d.width_height_ratio for d in accepted), default=0.0),
                n_fallen=sum(1 for d in accepted if engine.looks_fallen(d)),
                max_torso_angle=max(angles) if angles else None,
                feature=feature,
            )
        )

    # Close out anything still active so the event stream is balanced.
    if frames:
        replay.events.extend(engine.reset(frames[-1].now))
    return replay


def replay_sequence(
    cached: CachedSequence,
    config: AnomalyConfig,
    *,
    ghost_policy: str = GHOST_INCLUDE,
) -> SequenceReplay:
    return replay_frames(
        cached.frames,
        config,
        sequence=cached.sequence,
        frame_width=cached.frame_width,
        frame_height=cached.frame_height,
        ghost_policy=ghost_policy,
    )


def replay_all(
    caches: Iterable[CachedSequence],
    config: AnomalyConfig,
    *,
    ghost_policy: str = GHOST_INCLUDE,
) -> list[SequenceReplay]:
    return [replay_sequence(c, config, ghost_policy=ghost_policy) for c in caches]


def alarm_frames_from_events(replay: SequenceReplay) -> list[bool]:
    """Rebuild the per-frame alarm booleans from the event stream alone.

    Used as a correctness invariant in the tests: if this ever disagrees with
    the snapshot-derived signal, either the harness or the engine has a bug.
    """
    intervals: list[tuple[float, float]] = []
    open_starts: dict[str, float] = {}
    for event in replay.events:
        if event.event_type != FALL:
            continue
        if event.state == "started":
            open_starts[event.event_id] = event.started_at
        elif event.state == "ended":
            start = open_starts.pop(event.event_id, event.started_at)
            # The trailing engine.reset() is a bookkeeping flush that closes
            # events still active on the final frame; it is not a condition
            # change at that timestamp, so the alarm covers that frame.
            end = float("inf") if event.reason == "manual_reset" else event.timestamp
            intervals.append((start, end))
    for start in open_starts.values():
        intervals.append((start, float("inf")))

    rebuilt: list[bool] = []
    for signal in replay.signals:
        rebuilt.append(any(start <= signal.now < end for start, end in intervals))
    return rebuilt
