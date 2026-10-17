"""Check that learned-model predictions keep the established URFD semantics."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo.eval_pphuman import as_replay, alarm_events, clean_json, prepare_models
from demo.urfall import metrics as mx


def prediction(frame, alarm=False, raw=False, boxes=None):
    return {"frame": frame, "now": (frame - 1) / 30, "alarm": alarm, "raw_alarm": raw,
            "alarm_ids": [1] if alarm else [], "boxes": boxes or []}


class PPModelScoringTests(unittest.TestCase):
    def test_deliberate_lying_is_false_alarm(self):
        cache = {"sequence": "adl-10", "frames": [prediction(1, True, True)]}
        row = mx.event_level([as_replay(cache)], {("adl-10", 1): 1})[0]
        self.assertFalse(row["reported"])
        self.assertEqual(row["false_alarm_frames"], 1)

    def test_raw_decisions_do_not_inherit_display_hold(self):
        cache = {"sequence": "fall-01", "frames": [prediction(1, True, True), prediction(2, True)]}
        display = as_replay(cache)
        raw = as_replay(cache, raw=True)
        self.assertEqual([s.alarm for s in display.signals], [True, True])
        self.assertEqual([s.alarm for s in raw.signals], [True, False])

    def test_alarm_latency_uses_dataset_time(self):
        cache = {"sequence": "fall-01", "frames": [prediction(30), prediction(45, True, True)]}
        row = mx.event_level([as_replay(cache)], {("fall-01", 30): 1, ("fall-01", 45): 1})[0]
        self.assertTrue(row["reported"])
        self.assertEqual(row["latency_seconds"], .5)

    def test_hold_without_person_is_recorded_as_ghost(self):
        cache = {"sequence": "fall-01", "frames": [prediction(1, True)]}
        self.assertTrue(as_replay(cache).signals[0].ghost)

    def test_event_transitions_do_not_duplicate_start(self):
        cache = {"sequence": "fall-01", "frames": [prediction(1), prediction(2, True),
                prediction(3, True), prediction(4)]}
        self.assertEqual([(e["frame"], e["state"]) for e in alarm_events(cache)], [(2, "started"), (4, "ended")])

    def test_report_json_has_no_nonstandard_nan(self):
        self.assertEqual(json.dumps(clean_json({"value": float("nan")})), '{"value": null}')

    def test_model_archive_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with zipfile.ZipFile(base / "mot_ppyoloe_l_36e_pipeline.zip", "w") as archive:
                archive.writestr("../outside.txt", "bad")
            with self.assertRaises(ValueError):
                prepare_models(base, base / "models")
            self.assertFalse((base / "outside.txt").exists())


if __name__ == "__main__":
    unittest.main()
