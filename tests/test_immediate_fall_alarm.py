"""Business alarm checks independent of either neural network or a GPU."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo.fall_alarm import ImmediateFallAlarm
from demo.yolo11_fall_demo import parse_args


def detection(name='fallen', score=.8):
    return dict(class_name=name, score=score, bbox=[10, 10, 30, 100])


class AlarmTests(unittest.TestCase):
    def test_first_fallen_box_triggers_without_general_detection_or_track_id(self):
        signal = ImmediateFallAlarm().update([detection()])
        self.assertTrue(signal['active'])
        self.assertTrue(signal['started'])

    def test_ongoing_scene_deduplicates_events_without_delaying_new_alarm(self):
        alarm = ImmediateFallAlarm()
        self.assertTrue(alarm.update([detection()])['started'])
        self.assertFalse(alarm.update([detection()])['started'])
        self.assertTrue(alarm.update([detection()])['active'])
        self.assertTrue(alarm.update([])['ended'])
        self.assertTrue(alarm.update([detection()])['started'])

    def test_confidence_boundary_and_other_postures(self):
        alarm = ImmediateFallAlarm()
        for d in (detection('sitting'), detection('standing'), detection(score=.2499), detection(score=float('nan'))):
            self.assertFalse(alarm.update([d])['active'])
        self.assertTrue(alarm.update([detection(score=.25)])['active'])

    def test_old_entry_and_weights_are_independent_cli_defaults(self):
        with patch.object(Path, 'is_file', return_value=True):
            args = parse_args(['--source', '0', '--headless', '--mute'])
        self.assertEqual(args.model.name, 'yolo11-fall.pt')
        self.assertEqual(args.general_model.name, 'yolo11s.pt')
        self.assertNotEqual(args.model, args.general_model)


if __name__ == '__main__':
    unittest.main()
