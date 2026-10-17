"""Unit tests for the URFD evaluation harness.

No GPU, no dataset, no network: every test builds synthetic detections or
synthetic ground truth.  The point is to prove the harness is correct before
anyone trusts the numbers it prints.
"""

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo import anomaly_rules as anomaly
from demo.anomaly_rules import AnomalyConfig, PersonDetection
from demo.urfall import dataset as ds
from demo.urfall import metrics as mx
from demo.urfall.detect import CacheKey, FrameDetections, read_cache, write_cache
from demo.urfall.replay import (
    GHOST_EXCLUDE,
    GHOST_INCLUDE,
    alarm_frames_from_events,
    replay_frames,
)

FPS = 30.0

STANDING = (100.0, 100.0, 180.0, 300.0)   # ratio 80/200 = 0.4
LYING = (100.0, 160.0, 300.0, 240.0)      # ratio 200/80 = 2.5


def config(**overrides):
    base = dict(
        confidence=0.35,
        track_confidence=0.15,
        fall_ratio=1.1,
        fall_feature_mode="auto",
        fall_angle_degrees=60.0,
        keypoint_confidence=0.3,
        fall_seconds=1.5,
        linger_seconds=8.0,
        block_seconds=4.0,
        track_lost_grace_seconds=1.0,
        cooldown_seconds=5.0,
        stationary_speed_ratio=0.05,
        corridor_source="map",
    )
    base.update(overrides)
    return AnomalyConfig(**base)


def frames_from_boxes(boxes, *, track_id=1, confidence=0.9, start=1):
    """Build a FrameDetections list; a None box means "nothing detected"."""
    out = []
    for offset, bbox in enumerate(boxes):
        number = start + offset
        detections = [] if bbox is None else [PersonDetection(track_id, confidence, bbox)]
        out.append(FrameDetections(frame=number, now=(number - 1) / FPS, detections=detections))
    return out


def replay(boxes, cfg=None, **kwargs):
    return replay_frames(
        frames_from_boxes(boxes),
        cfg or config(),
        sequence="synthetic",
        frame_width=640,
        frame_height=480,
        **kwargs,
    )


class ReplayTests(unittest.TestCase):
    def test_synthetic_lying_sequence_fires_at_exact_frame(self):
        # Standing for frames 1-45, lying from frame 46 (now = 1.5s) onward.
        # The debounce needs 1.5s of sustained ratio, so the alarm must land at
        # now = 3.0s, i.e. frame 91. This pins both the debounce arithmetic and
        # the now = (frame - 1) / 30 convention in one assertion.
        result = replay([STANDING] * 45 + [LYING] * 105)
        self.assertEqual(result.first_alarm_frame, 91)
        self.assertTrue(result.alarmed)
        self.assertEqual(len(result.start_events), 1)

    def test_synthetic_standing_sequence_never_alarms(self):
        result = replay([STANDING] * 150)
        self.assertIsNone(result.first_alarm_frame)
        self.assertFalse(result.alarmed)
        self.assertFalse(any(s.alarm for s in result.signals))

    def test_snapshot_signal_matches_event_stream(self):
        # The strongest cheap invariant: the per-frame signal taken from
        # TrackSnapshot.active_type must equal the one reconstructed from the
        # started/ended event pairs.
        for boxes in (
            [STANDING] * 45 + [LYING] * 105,
            [LYING] * 150,
            [LYING] * 60 + [STANDING] * 10 + [LYING] * 80,
            [LYING] * 60 + [None] * 40 + [LYING] * 50,
        ):
            with self.subTest(boxes=len(boxes)):
                result = replay(boxes)
                observed = [s.alarm for s in result.signals]
                self.assertEqual(observed, alarm_frames_from_events(result))

    def test_single_dropout_frame_triggers_cooldown_suppression(self):
        # Lying from frame 46; frame 100 is a single standing frame. That one
        # frame cancels the alarm and arms cooldown_seconds=5.0, so re-firing
        # needs 5s of cooldown plus another 1.5s of debounce.
        boxes = [STANDING] * 45 + [LYING] * 54 + [STANDING] + [LYING] * 200
        result = replay(boxes)
        signals = {s.frame: s.alarm for s in result.signals}

        self.assertTrue(signals[99])
        self.assertFalse(signals[100])
        self.assertEqual(result.condition_changed_ends, 1)

        resumed = next((f for f in sorted(signals) if f > 100 and signals[f]), None)
        self.assertIsNotNone(resumed)
        # Cooldown expires at now = 99/30 + 5.0; debounce restarts at frame 101.
        self.assertGreaterEqual(resumed, 100 + 150)

    def test_no_cooldown_config_resumes_after_debounce_only(self):
        # Ideal arithmetic says frame 101 + 45 debounce frames = 146, but the
        # timestamps are floats: 145/30 - 100/30 == 1.4999999999999996, which
        # is just under fall_seconds, so the alarm lands one frame (33 ms) late.
        # Pinned deliberately -- it is a property of float timebases, not a bug
        # worth changing the rule over.
        boxes = [STANDING] * 45 + [LYING] * 54 + [STANDING] + [LYING] * 200
        result = replay(boxes, config(cooldown_seconds=0.0))
        signals = {s.frame: s.alarm for s in result.signals}
        resumed = next(f for f in sorted(signals) if f > 100 and signals[f])
        self.assertEqual(resumed, 147)

    def test_ghost_snapshot_policy(self):
        # Track vanishes at frame 101 while an alarm is active. The engine keeps
        # emitting a stale snapshot for track_lost_grace_seconds (30 frames).
        boxes = [STANDING] * 45 + [LYING] * 55 + [None] * 50
        included = replay(boxes, ghost_policy=GHOST_INCLUDE)
        excluded = replay(boxes, ghost_policy=GHOST_EXCLUDE)

        inc = {s.frame: s.alarm for s in included.signals}
        exc = {s.frame: s.alarm for s in excluded.signals}

        self.assertTrue(inc[100])
        self.assertTrue(inc[101])      # ghost keeps the alarm up
        self.assertFalse(exc[101])     # excluded immediately
        self.assertGreater(sum(1 for s in included.signals if s.ghost), 0)
        self.assertEqual(sum(1 for s in excluded.signals if s.alarm and s.ghost), 0)

    def test_low_confidence_detections_are_filtered_at_replay(self):
        frames = frames_from_boxes([LYING] * 150, confidence=0.2)
        result = replay_frames(
            frames, config(), sequence="s", frame_width=640, frame_height=480
        )
        self.assertFalse(result.alarmed)

    def test_zero_detections_produce_no_alarm(self):
        result = replay([None] * 150)
        self.assertFalse(any(s.alarm for s in result.signals))
        self.assertEqual(result.track_ids, set())


class ScoringTests(unittest.TestCase):
    def test_scoring_math(self):
        result = replay([STANDING] * 45 + [LYING] * 105)
        gt = {}
        for frame in range(1, 151):
            gt[("synthetic", frame)] = ds.LABEL_LYING if frame >= 91 else ds.LABEL_NOT_LYING
        overall, per_sequence = mx.score_frames([result], gt)

        self.assertEqual(overall.tp, 60)   # frames 91-150 lying and alarming
        self.assertEqual(overall.fn, 0)
        self.assertEqual(overall.fp, 0)
        self.assertEqual(overall.tn, 90)   # frames 1-90
        self.assertAlmostEqual(overall.precision, 1.0)
        self.assertAlmostEqual(overall.recall, 1.0)
        self.assertAlmostEqual(overall.f1, 1.0)
        self.assertAlmostEqual(overall.specificity, 1.0)
        self.assertEqual(per_sequence["synthetic"], overall)

    def test_confusion_handles_empty_and_degenerate(self):
        empty = mx.Confusion()
        self.assertTrue(empty.precision != empty.precision)  # NaN
        self.assertTrue(empty.f1 != empty.f1)
        self.assertEqual(empty.total, 0)
        only_tn = mx.Confusion(tn=10)
        self.assertAlmostEqual(only_tn.specificity, 1.0)
        self.assertTrue(only_tn.recall != only_tn.recall)

    def test_positive_scope_changes_how_adl_lying_counts(self):
        # URFD's ADL sequences contain people lying down deliberately, labelled
        # exactly like a fallen person. Alarming there is correct under
        # "any_lying" and a false positive under "fall_only".
        adl = replay([LYING] * 100)
        adl.sequence = "adl-30"
        gt = {("adl-30", f): ds.LABEL_LYING for f in range(1, 101)}

        any_lying, _ = mx.score_frames([adl], gt, positive_scope=mx.SCOPE_ANY_LYING)
        fall_only, _ = mx.score_frames([adl], gt, positive_scope=mx.SCOPE_FALL_ONLY)

        self.assertGreater(any_lying.tp, 0)
        self.assertEqual(any_lying.fp, 0)
        self.assertEqual(fall_only.tp, 0)
        self.assertEqual(fall_only.fp, any_lying.tp)  # same frames, opposite verdict
        self.assertEqual(any_lying.total, fall_only.total)

    def test_adl_alarm_counts_as_false_alarm_even_when_labelled_lying(self):
        # Regression: ADL sequences that contain deliberate lie-downs carry
        # "lying" labels, and were previously exempted from false-alarm
        # counting, which understated the false-alarm rate.
        adl = replay([LYING] * 100)
        adl.sequence = "adl-30"
        gt = {("adl-30", f): ds.LABEL_LYING for f in range(1, 101)}
        rows = mx.event_level([adl], gt)
        self.assertEqual(rows[0]["kind"], "adl")
        self.assertGreater(rows[0]["false_alarm_frames"], 0)

        summary = mx.event_summary(rows, adl_frames=100)
        self.assertEqual(summary["adl_sequences_with_false_alarm"], 1)
        self.assertEqual(summary["n_adl_hard_negatives"], 1)
        self.assertEqual(summary["hard_negatives_with_false_alarm"], 1)

    def test_positive_scope_leaves_fall_sequences_alone(self):
        fall = replay([LYING] * 100)
        fall.sequence = "fall-07"
        gt = {("fall-07", f): ds.LABEL_LYING for f in range(1, 101)}
        a, _ = mx.score_frames([fall], gt, positive_scope=mx.SCOPE_ANY_LYING)
        b, _ = mx.score_frames([fall], gt, positive_scope=mx.SCOPE_FALL_ONLY)
        self.assertEqual(a, b)

    def test_positive_scope_rejects_unknown_value(self):
        with self.assertRaises(ValueError):
            mx.score_frames([], {}, positive_scope="nonsense")

    def test_transition_frames_excluded(self):
        result = replay([LYING] * 100)
        gt = {("synthetic", f): ds.LABEL_TRANSITION for f in range(1, 51)}
        gt.update({("synthetic", f): ds.LABEL_LYING for f in range(51, 101)})
        overall, _ = mx.score_frames([result], gt)
        self.assertEqual(overall.total, 50)
        self.assertEqual(overall.positives, 50)

    def test_unlabeled_frames_excluded(self):
        # Mirrors adl-01 (labels start at frame 6) and adl-02 (start at 31).
        result = replay([STANDING] * 100)
        gt = {("synthetic", f): ds.LABEL_NOT_LYING for f in range(6, 101)}
        overall, _ = mx.score_frames([result], gt)
        self.assertEqual(overall.total, 95)

    def test_sequence_level_latency_is_signed(self):
        result = replay([LYING] * 150)
        result.sequence = "fall-99"
        gt = {("fall-99", f): ds.LABEL_NOT_LYING for f in range(1, 101)}
        gt.update({("fall-99", f): ds.LABEL_LYING for f in range(101, 151)})
        rows = mx.sequence_level([result], gt)
        self.assertEqual(rows[0]["first_alarm_frame"], 46)
        self.assertEqual(rows[0]["first_gt_lying_frame"], 101)
        self.assertEqual(rows[0]["latency_frames"], -55)  # alarmed before GT says lying

    def test_sequence_summary_counts_false_alarms(self):
        fall = replay([LYING] * 150)
        fall.sequence = "fall-01"
        adl = replay([LYING] * 150)
        adl.sequence = "adl-01"
        gt = {}
        rows = mx.sequence_level([fall, adl], gt)
        summary = mx.sequence_summary(rows)
        self.assertEqual(summary["fall_sequences_detected"], 1)
        self.assertEqual(summary["adl_sequences_with_false_alarm"], 1)
        self.assertGreaterEqual(summary["adl_false_alarm_events"], 1)


def keypoints(angle_degrees, *, conf=0.9, length=100.0, missing_hips=False, missing_all=False):
    """17 COCO keypoints with the torso tilted by `angle_degrees` from vertical."""
    import math

    pts = [(0.0, 0.0, 0.0)] * 17
    if missing_all:
        return tuple(pts)
    sx, sy = 300.0, 200.0
    rad = math.radians(angle_degrees)
    hx = sx + length * math.sin(rad)
    hy = sy + length * math.cos(rad)
    pts[anomaly.LEFT_SHOULDER] = (sx - 20, sy, conf)
    pts[anomaly.RIGHT_SHOULDER] = (sx + 20, sy, conf)
    if not missing_hips:
        pts[anomaly.LEFT_HIP] = (hx - 15, hy, conf)
        pts[anomaly.RIGHT_HIP] = (hx + 15, hy, conf)
    return tuple(pts)


class TorsoAngleTests(unittest.TestCase):
    def test_upright_and_horizontal_torso(self):
        upright = PersonDetection(1, 0.9, STANDING, keypoints(0.0))
        flat = PersonDetection(1, 0.9, STANDING, keypoints(90.0))
        self.assertAlmostEqual(upright.torso_angle_degrees(), 0.0, places=4)
        self.assertAlmostEqual(flat.torso_angle_degrees(), 90.0, places=4)

    def test_angle_is_sign_independent(self):
        # Head-left and head-right lying should read the same tilt.
        left = PersonDetection(1, 0.9, STANDING, keypoints(70.0))
        right = PersonDetection(1, 0.9, STANDING, keypoints(-70.0))
        self.assertAlmostEqual(left.torso_angle_degrees(), right.torso_angle_degrees(), places=4)

    def test_returns_none_without_usable_keypoints(self):
        self.assertIsNone(PersonDetection(1, 0.9, STANDING).torso_angle_degrees())
        self.assertIsNone(
            PersonDetection(1, 0.9, STANDING, keypoints(45.0, missing_hips=True)).torso_angle_degrees()
        )
        self.assertIsNone(
            PersonDetection(1, 0.9, STANDING, keypoints(45.0, missing_all=True)).torso_angle_degrees()
        )

    def test_low_confidence_keypoints_are_ignored(self):
        weak = PersonDetection(1, 0.9, STANDING, keypoints(90.0, conf=0.05))
        self.assertIsNone(weak.torso_angle_degrees(0.3))
        self.assertIsNotNone(weak.torso_angle_degrees(0.01))

    def test_angle_survives_a_truncated_box(self):
        # Box says "tall" (ratio 0.4) but the torso is flat. The keypoint
        # feature must win -- this is the truncated-body case from fall-04.
        det = PersonDetection(1, 0.9, STANDING, keypoints(85.0))
        self.assertLess(det.width_height_ratio, 1.1)
        engine = anomaly.AnomalyEngine(config(fall_feature_mode="auto", fall_angle_degrees=60.0))
        kind, value = engine.fall_feature(det)
        self.assertEqual(kind, "torso_angle")
        self.assertTrue(engine.looks_fallen(det))

    def test_falls_back_to_bbox_when_no_keypoints(self):
        engine = anomaly.AnomalyEngine(config(fall_feature_mode="auto"))
        kind, value = engine.fall_feature(PersonDetection(1, 0.9, LYING))
        self.assertEqual(kind, "bbox_ratio")
        self.assertAlmostEqual(value, 2.5)

    def test_pose_angle_can_be_disabled(self):
        det = PersonDetection(1, 0.9, STANDING, keypoints(85.0))
        engine = anomaly.AnomalyEngine(config(fall_feature_mode="bbox"))
        kind, _ = engine.fall_feature(det)
        self.assertEqual(kind, "bbox_ratio")
        self.assertFalse(engine.looks_fallen(det))

    def test_replay_fires_on_torso_angle(self):
        frames = []
        for i in range(150):
            angle = 5.0 if i < 45 else 88.0
            frames.append(
                FrameDetections(
                    frame=i + 1,
                    now=i / FPS,
                    detections=[PersonDetection(1, 0.9, STANDING, keypoints(angle))],
                )
            )
        result = replay_frames(
            frames, config(fall_feature_mode="auto", fall_angle_degrees=60.0),
            sequence="synthetic", frame_width=640, frame_height=480,
        )
        self.assertEqual(result.first_alarm_frame, 91)
        self.assertEqual(result.signals[100].feature, "auto")
        self.assertAlmostEqual(result.signals[100].max_torso_angle, 88.0, places=3)
        # The box is upright (ratio 0.4) so only the keypoints can have fired.
        self.assertLess(result.signals[100].max_ratio, 1.1)


class TwoTierConfidenceTests(unittest.TestCase):
    def test_weak_detection_cannot_start_a_track(self):
        cfg = config(confidence=0.35, track_confidence=0.15)
        engine = anomaly.AnomalyEngine(cfg)
        _, snaps = engine.update(
            [PersonDetection(1, 0.20, LYING)], frame_width=640, frame_height=480, now=0.0
        )
        self.assertEqual(snaps, [])
        self.assertEqual(len(engine.tracks), 0)

    def test_weak_detection_continues_an_established_track(self):
        cfg = config(confidence=0.35, track_confidence=0.15)
        engine = anomaly.AnomalyEngine(cfg)
        engine.update([PersonDetection(1, 0.90, LYING)], frame_width=640, frame_height=480, now=0.0)
        _, snaps = engine.update(
            [PersonDetection(1, 0.20, LYING)], frame_width=640, frame_height=480, now=1 / FPS
        )
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0].track_id, 1)

    def test_weak_frames_keep_the_fall_timer_running(self):
        # The whole point: a body on the floor scores low, and the old single
        # gate dropped exactly those frames.
        cfg = config(confidence=0.35, track_confidence=0.15, cooldown_seconds=0.0)
        frames = [
            FrameDetections(
                frame=i + 1,
                now=i / FPS,
                detections=[PersonDetection(1, 0.9 if i < 5 else 0.20, LYING)],
            )
            for i in range(120)
        ]
        strict = replay_frames(frames, config(confidence=0.35, track_confidence=0.35),
                               sequence="s", frame_width=640, frame_height=480)
        lenient = replay_frames(frames, cfg, sequence="s", frame_width=640, frame_height=480)
        self.assertIsNone(strict.first_alarm_frame)
        self.assertEqual(lenient.first_alarm_frame, 46)

    def test_config_rejects_track_confidence_above_confidence(self):
        with self.assertRaises(ValueError):
            anomaly.AnomalyConfig.from_mapping({"confidence": 0.3, "track_confidence": 0.5})

    def test_config_rejects_impossible_angle(self):
        with self.assertRaises(ValueError):
            anomaly.AnomalyConfig.from_mapping({"fall_angle_degrees": 120})


class DetectionRateTests(unittest.TestCase):
    def test_detection_rate_splits_by_pose(self):
        # Detected while upright, invisible once on the ground -- the exact
        # failure mode seen on the real dataset.
        boxes = [STANDING] * 50 + [None] * 50
        result = replay(boxes)
        gt = {("synthetic", f): ds.LABEL_NOT_LYING for f in range(1, 51)}
        gt.update({("synthetic", f): ds.LABEL_LYING for f in range(51, 101)})

        rate = mx.detection_rate_by_label([result], gt)
        self.assertAlmostEqual(rate["not_lying_rate"], 1.0)
        self.assertAlmostEqual(rate["lying_rate"], 0.0)
        self.assertEqual(rate["lying_frames"], 50)
        self.assertEqual(rate["per_sequence"]["synthetic"]["lying_detected"], 0)

    def test_detection_rate_ignores_transition_and_unlabeled(self):
        result = replay([STANDING] * 10)
        gt = {("synthetic", f): ds.LABEL_TRANSITION for f in range(1, 6)}
        rate = mx.detection_rate_by_label([result], gt)
        self.assertEqual(rate["lying_frames"], 0)
        self.assertEqual(rate["not_lying_frames"], 0)


class LongestRunTests(unittest.TestCase):
    def test_gaps_do_not_reset_but_low_ratio_frames_do(self):
        # 10 lying, a 5-frame detection gap, 10 more lying: the engine's timer
        # survives the gap, so the run is 20.
        result = replay([LYING] * 10 + [None] * 5 + [LYING] * 10)
        runs = mx.longest_qualifying_run([result], config())
        self.assertEqual(runs["synthetic"]["longest_run_frames"], 20)

        # A detected frame below the ratio threshold does reset it.
        result = replay([LYING] * 10 + [STANDING] + [LYING] * 6)
        runs = mx.longest_qualifying_run([result], config())
        self.assertEqual(runs["synthetic"]["longest_run_frames"], 10)

    def test_could_fire_compares_against_debounce(self):
        short = replay([LYING] * 30)
        runs = mx.longest_qualifying_run([short], config(fall_seconds=1.5))
        self.assertEqual(runs["synthetic"]["needed_frames"], 45)
        self.assertFalse(runs["synthetic"]["could_fire"])

        runs = mx.longest_qualifying_run([short], config(fall_seconds=0.5))
        self.assertTrue(runs["synthetic"]["could_fire"])


class UnicodePathTests(unittest.TestCase):
    def test_imread_imwrite_roundtrip_on_non_ascii_path(self):
        # OpenCV's own imread returns None for these paths on Windows; the
        # harness must not depend on ultralytics having patched cv2.
        try:
            import cv2  # noqa: F401
            import numpy as np
        except ImportError:
            self.skipTest("OpenCV not installed")
        from demo.urfall.detect import imread_unicode, imwrite_unicode

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "智能小车-测试" / "frame.png"
            target.parent.mkdir(parents=True)
            image = np.zeros((8, 12, 3), dtype=np.uint8)
            image[2, 3] = (10, 20, 30)

            self.assertTrue(imwrite_unicode(target, image))
            restored = imread_unicode(target)
            self.assertIsNotNone(restored)
            self.assertEqual(restored.shape, (8, 12, 3))
            self.assertEqual(tuple(int(v) for v in restored[2, 3]), (10, 20, 30))

    def test_imread_unicode_returns_none_for_missing_file(self):
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("OpenCV not installed")
        from demo.urfall.detect import imread_unicode

        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(imread_unicode(Path(tmp) / "nope.png"))


class RecallCeilingTests(unittest.TestCase):
    def _gt(self):
        # 30 transition frames (71-100) then 48 lying frames (101-148).
        gt = {("fall-01", f): ds.LABEL_NOT_LYING for f in range(1, 71)}
        gt.update({("fall-01", f): ds.LABEL_TRANSITION for f in range(71, 101)})
        gt.update({("fall-01", f): ds.LABEL_LYING for f in range(101, 149)})
        return gt

    def test_recall_ceiling_matches_hand_calculation(self):
        ceiling = mx.recall_ceiling(self._gt(), ["fall-01"], 1.5)
        info = ceiling["per_sequence"]["fall-01"]
        self.assertEqual(ceiling["debounce_frames"], 45)
        self.assertEqual(info["first_transition_frame"], 71)
        self.assertEqual(info["earliest_alarm_frame"], 116)  # 71 + 45
        self.assertEqual(info["reachable_tp"], 33)           # frames 116-148
        self.assertEqual(info["unreachable_fn"], 15)
        self.assertAlmostEqual(ceiling["ceiling_recall"], 33 / 48)

    def test_ceiling_is_full_when_debounce_fits_the_transition_window(self):
        # The transition window is exactly 1.0s, so fall_seconds <= 1.0 loses
        # nothing: the alarm can land on the very first lying frame.
        ceiling = mx.recall_ceiling(self._gt(), ["fall-01"], 1.0)
        self.assertAlmostEqual(ceiling["ceiling_recall"], 1.0)

    def test_ceiling_without_transition_lead_is_far_lower(self):
        ceiling = mx.recall_ceiling(self._gt(), ["fall-01"], 1.5, from_transition=False)
        self.assertEqual(ceiling["per_sequence"]["fall-01"]["earliest_alarm_frame"], 146)
        self.assertEqual(ceiling["per_sequence"]["fall-01"]["reachable_tp"], 3)

    def test_earliest_alarm_frames_feeds_skip_before(self):
        floors = mx.earliest_alarm_frames(self._gt(), ["fall-01"], 1.5)
        self.assertEqual(floors, {"fall-01": 116})


class SweepTests(unittest.TestCase):
    def test_frange_endpoints(self):
        values = mx.frange(0.7, 1.8, 0.05)
        self.assertEqual(values[0], 0.7)
        self.assertEqual(values[-1], 1.8)
        self.assertEqual(len(values), 23)

    def test_best_config_prefers_higher_f1(self):
        rows = [
            {"fall_ratio": 1.0, "fall_seconds": 1.5, "f1": 0.5},
            {"fall_ratio": 1.2, "fall_seconds": 0.5, "f1": 0.9},
            {"fall_ratio": 1.3, "fall_seconds": 2.0, "f1": float("nan")},
        ]
        best = mx.best_config(rows)
        self.assertEqual(best["fall_ratio"], 1.2)


class DatasetTests(unittest.TestCase):
    def test_frame_sort_is_numeric(self):
        with tempfile.TemporaryDirectory() as tmp:
            seq_dir = Path(tmp)
            for number in (1, 9, 10, 100):
                (seq_dir / f"fall-01-cam0-rgb-{number:03d}.png").write_bytes(b"x")
            sequence = ds.load_sequence(seq_dir, "fall-01")
            self.assertEqual(sequence.numbers, (1, 9, 10, 100))

    def test_frame_number_parsing(self):
        self.assertEqual(ds.frame_number("fall-01-cam0-rgb-007.png"), 7)
        self.assertEqual(ds.frame_number(Path("a/b/adl-05-cam0-rgb-180.png")), 180)
        with self.assertRaises(ValueError):
            ds.frame_number("not-a-frame.txt")

    def test_sequence_time_conversion(self):
        sequence = ds.Sequence("fall-01", "fall", (Path("a.png"),), (1,))
        self.assertAlmostEqual(sequence.time_of(1), 0.0)
        self.assertAlmostEqual(sequence.time_of(31), 1.0)

    def test_ground_truth_parser(self):
        text = (
            "fall-01,1,-1,3.1667,2.9098,0.55367,126.0258,1.0324,1899.5366,1055.9988,0.04731\n"
            "fall-01,2,0,3.3067,2.9699,0.47876,125.5657,1.1251,2070.1193,1065.9506,0.048175\n"
            "adl-01,6,1,4.8158,5.5638,0.62698,138.9976,0.93626,1722.725,908.9348,0.08555\n"
            "\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gt.csv"
            path.write_text(text, encoding="utf-8")
            gt = ds.parse_ground_truth([path])
        self.assertEqual(gt[("fall-01", 1)], ds.LABEL_NOT_LYING)
        self.assertEqual(gt[("fall-01", 2)], ds.LABEL_TRANSITION)
        self.assertEqual(gt[("adl-01", 6)], ds.LABEL_LYING)

    def test_ground_truth_parser_rejects_bad_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gt.csv"
            path.write_text("fall-01,1,7,0.1\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                ds.parse_ground_truth([path])

    def test_extraction_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = tmp_path / "fall-01-cam0-rgb.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                for number in (1, 2, 3):
                    archive.writestr(f"fall-01-cam0-rgb/fall-01-cam0-rgb-{number:03d}.png", b"x")
            work = tmp_path / "work"

            first = ds.extract_sequence(zip_path, work)
            marker = first / "extra-marker.txt"
            marker.write_text("kept", encoding="utf-8")

            second = ds.extract_sequence(zip_path, work)
            self.assertEqual(first, second)
            self.assertTrue(marker.exists(), "second call should have been a no-op")

            ds.extract_sequence(zip_path, work, force=True)
            self.assertFalse(marker.exists(), "force should re-extract from scratch")

    def test_extraction_rejects_zip_slip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = tmp_path / "fall-02-cam0-rgb.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("../escaped.png", b"x")
            with self.assertRaises(ValueError):
                ds.extract_sequence(zip_path, tmp_path / "work")

    def test_discover_zips(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for name in ("fall-01-cam0-rgb.zip", "adl-05-cam0-rgb.zip", "unrelated.zip"):
                (tmp_path / name).write_bytes(b"")
            found = ds.discover_zips(tmp_path)
            self.assertEqual(set(found), {"fall-01", "adl-05"})


class CacheTests(unittest.TestCase):
    def _key(self, **overrides):
        base = dict(
            model_sha1="a" * 40,
            tracker_sha1="b" * 40,
            imgsz=640,
            yolo_conf=0.1,
            device="cuda:0",
            half=False,
            batch=1,
            classes=(0,),
            ultralytics="8.4.160",
            torch="2.9.1+cu126",
        )
        base.update(overrides)
        return CacheKey(**base)

    def test_cache_roundtrip(self):
        key = self._key()
        frames = frames_from_boxes([LYING, None, STANDING])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fall-01.json"
            write_cache(path, key, "fall-01", frames, frame_width=640, frame_height=480)
            restored = read_cache(path, key)

        self.assertEqual(restored.sequence, "fall-01")
        self.assertEqual(restored.frame_width, 640)
        self.assertEqual(len(restored.frames), 3)
        self.assertEqual(restored.frames[0].detections[0].bbox, LYING)
        self.assertEqual(restored.frames[0].detections[0].track_id, 1)
        self.assertEqual(restored.frames[1].detections, [])

    def test_cache_rejects_key_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fall-01.json"
            write_cache(path, self._key(), "fall-01", frames_from_boxes([LYING]),
                        frame_width=640, frame_height=480)
            read_cache(path, self._key())  # matching key is fine
            with self.assertRaises(ValueError):
                read_cache(path, self._key(imgsz=1280))
            with self.assertRaises(ValueError):
                read_cache(path, self._key(device="cpu"))

    def test_cache_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fall-01.json"
            write_cache(path, self._key(), "fall-01", frames_from_boxes([LYING]),
                        frame_width=640, frame_height=480)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["key"]["imgsz"] = 1280
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                read_cache(path)

    def test_key_digest_is_order_independent(self):
        self.assertEqual(self._key().digest(), self._key().digest())
        self.assertNotEqual(self._key().digest(), self._key(batch=4).digest())


if __name__ == "__main__":
    unittest.main(verbosity=2)
