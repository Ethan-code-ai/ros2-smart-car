import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo.anomaly_rules import AnomalyConfig, AnomalyEngine, PersonDetection
from demo.person_anomaly_demo import event_record


def person(track_id=1, confidence=0.9, bbox=(100, 100, 180, 300)):
    return PersonDetection(track_id, confidence, bbox)


class AnomalyEngineTests(unittest.TestCase):
    def setUp(self):
        self.config = AnomalyConfig(
            fall_seconds=1.5,
            linger_seconds=8.0,
            block_seconds=4.0,
            track_lost_grace_seconds=1.0,
            cooldown_seconds=0.0,
            stationary_speed_ratio=0.05,
            corridor_overlap_ratio=0.25,
            corridor_source="image_zone",
            linger_zone=(0.05, 0.05, 0.95, 0.95),
            corridor_zone=(0.35, 0.05, 0.65, 0.95),
        )
        self.engine = AnomalyEngine(self.config)

    def update(self, detections, now):
        return self.engine.update(detections, frame_width=640, frame_height=480, now=now)

    def test_fall_requires_continuous_duration_and_starts_once(self):
        self.update([person(bbox=(100, 160, 300, 240))], 0.0)
        events, _ = self.update([person(bbox=(100, 160, 300, 240))], 1.4)
        self.assertEqual(events, [])
        events, _ = self.update([person(bbox=(100, 160, 300, 240))], 1.5)
        self.assertEqual([event.event_type for event in events], ["fall_suspected"])
        events, _ = self.update([person(bbox=(100, 160, 300, 240))], 2.0)
        self.assertEqual(events, [])

    def test_short_transit_does_not_trigger_blockage(self):
        self.update([person(bbox=(240, 100, 400, 300))], 0.0)
        events, _ = self.update([], 0.8)
        self.assertEqual(events, [])
        events, _ = self.update([], 2.0)
        self.assertEqual(events, [])

    def test_blockage_starts_after_duration(self):
        self.update([person(bbox=(240, 100, 400, 300))], 0.0)
        events, _ = self.update([person(bbox=(240, 100, 400, 300))], 3.9)
        self.assertEqual(events, [])
        events, _ = self.update([person(bbox=(240, 100, 400, 300))], 4.0)
        self.assertEqual([event.event_type for event in events], ["corridor_blocked"])

    def test_map_mode_does_not_use_image_zone(self):
        config = AnomalyConfig(
            block_seconds=4.0,
            corridor_source="map",
            corridor_zone=(0.35, 0.05, 0.65, 0.95),
        )
        engine = AnomalyEngine(config)
        engine.update([person(bbox=(240, 100, 400, 300))], frame_width=640, frame_height=480, now=0.0)
        events, _ = engine.update(
            [person(bbox=(240, 100, 400, 300))],
            frame_width=640,
            frame_height=480,
            now=4.0,
        )
        self.assertEqual(events, [])

    def test_map_signal_starts_blockage_after_duration(self):
        config = AnomalyConfig(block_seconds=4.0, corridor_source="map")
        engine = AnomalyEngine(config)
        engine.update(
            [person()],
            frame_width=640,
            frame_height=480,
            now=0.0,
            corridor_occupied=True,
        )
        events, _ = engine.update(
            [person()],
            frame_width=640,
            frame_height=480,
            now=4.0,
            corridor_occupied=True,
        )
        self.assertEqual([event.event_type for event in events], ["corridor_blocked"])

    def test_linger_starts_in_zone_and_ends_after_track_loss(self):
        self.update([person(bbox=(60, 100, 180, 300))], 0.0)
        events, _ = self.update([person(bbox=(60, 100, 180, 300))], 8.0)
        self.assertEqual([event.event_type for event in events], ["long_stay"])
        events, _ = self.update([], 8.5)
        self.assertEqual(events, [])
        events, _ = self.update([], 9.1)
        self.assertEqual([(event.state, event.reason) for event in events], [("ended", "track_lost")])

    def test_priority_selects_fall_over_blockage_and_linger(self):
        self.update([person(bbox=(240, 160, 440, 240))], 0.0)
        events, _ = self.update([person(bbox=(240, 160, 440, 240))], 8.0)
        self.assertEqual([event.event_type for event in events], ["fall_suspected"])

    def test_reset_ends_active_event(self):
        self.update([person(bbox=(240, 100, 400, 300))], 0.0)
        self.update([person(bbox=(240, 100, 400, 300))], 4.0)
        events = self.engine.reset(4.5)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].state, "ended")
        self.assertEqual(events[0].reason, "manual_reset")

    def test_event_record_contains_the_demo_contract(self):
        self.update([person(bbox=(240, 100, 400, 300))], 0.0)
        events, _ = self.update([person(bbox=(240, 100, 400, 300))], 4.0)
        record = event_record(
            events[0],
            anchor_wall=1_700_000_000.0,
            source="demo.mp4",
            evidence_path="demo_output/evidence/example.jpg",
        )
        self.assertEqual(
            set(record),
            {
                "event_id",
                "event_type",
                "state",
                "track_id",
                "timestamp",
                "started_at",
                "duration_seconds",
                "confidence",
                "bbox",
                "reason",
                "source",
                "evidence_path",
            },
        )
        self.assertEqual(record["event_type"], "corridor_blocked")
        self.assertEqual(record["evidence_path"], "demo_output/evidence/example.jpg")


if __name__ == "__main__":
    unittest.main()
