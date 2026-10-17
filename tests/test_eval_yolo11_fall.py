"""Learned-class scoring and source-clock alarm tests; no model/GPU required."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo.eval_yolo11_fall import Confirmation, clean, compatible_cache, extract, replay, score, select_failures, select_scope, summarize


def det(name='fallen', bbox=(10, 10, 110, 60)):
    return dict(class_id={'fallen': 0, 'sitting': 1, 'standing': 2}[name], class_name=name, score=.8, bbox=list(bbox))


class EvaluationTests(unittest.TestCase):
    def test_full_scope_retains_paddle_successes_as_well_as_failures(self):
        rows = [dict(sequence='fall-01', kind='fall', lying_frames=4, first_alarm_frame=None, false_alarm_frames=0),
                dict(sequence='fall-02', kind='fall', lying_frames=4, first_alarm_frame=3, false_alarm_frames=0),
                dict(sequence='adl-01', kind='adl', lying_frames=0, first_alarm_frame=None, false_alarm_frames=0)]
        self.assertEqual(set(select_scope(rows, ['fall-01', 'fall-02', 'adl-01'], 'all')), {'fall-01', 'fall-02', 'adl-01'})
        self.assertEqual(set(select_scope(rows, ['fall-01', 'fall-02', 'adl-01'])), {'fall-01'})

    def test_cache_reuse_rejects_changed_config_or_incomplete_frames(self):
        expected = dict(environment=dict(model_sha256='abc', conf=.25), frames=[1, 2])
        cache = dict(schema=1, identity=expected, frames=[dict(frame=1), dict(frame=2)])
        self.assertTrue(compatible_cache(cache, expected))
        other = dict(expected, environment=dict(model_sha256='abc', conf=.3))
        self.assertFalse(compatible_cache(cache, other))
        incomplete = dict(cache, frames=[dict(frame=1)])
        self.assertFalse(compatible_cache(incomplete, expected))

    def test_failure_selection_uses_lying_business_semantics(self):
        rows = [dict(sequence='fall-01', kind='fall', lying_frames=4, first_alarm_frame=None, false_alarm_frames=0),
                dict(sequence='adl-01', kind='adl', lying_frames=4, first_alarm_frame=None, false_alarm_frames=0),
                dict(sequence='adl-02', kind='adl', lying_frames=4, first_alarm_frame=3, false_alarm_frames=4),
                dict(sequence='adl-03', kind='adl', lying_frames=0, first_alarm_frame=3, false_alarm_frames=4),
                dict(sequence='adl-04', kind='adl', lying_frames=0, first_alarm_frame=None, false_alarm_frames=0)]
        self.assertEqual(select_failures(rows), {'fall-01': 'missed_fall', 'adl-01': 'missed_lying_activity',
                                                'adl-03': 'ordinary_activity_false_alarm'})

    def test_extract_keeps_learned_classes_without_person_filter(self):
        class Data:
            def cpu(self): return self
            def tolist(self): return [[1, 2, 3, 4, .9, 0], [5, 6, 7, 8, .8, 1], [9, 10, 11, 12, .7, 2]]
        result = SimpleNamespace(boxes=SimpleNamespace(data=Data()), names={0: 'fallen', 1: 'sitting', 2: 'standing'})
        self.assertEqual([d['class_name'] for d in extract(result)], ['fallen', 'sitting', 'standing'])

    def test_source_time_requires_actual_two_seconds(self):
        policy = Confirmation()
        for i in range(60):
            self.assertFalse(policy.update(i / 30, [det()]))
        self.assertTrue(policy.update(2., [det()]))

    def test_duplicate_timestamp_cannot_confirm(self):
        policy = Confirmation()
        for _ in range(100):
            self.assertFalse(policy.update(0., [det()]))

    def test_short_missing_observation_does_not_count_as_evidence(self):
        policy = Confirmation(seconds=.3)
        policy.update(0., [det()])
        policy.update(.1, [det()])
        policy.update(.2, [])
        self.assertFalse(policy.update(.3, [det()]))
        self.assertFalse(policy.update(.4, [det()]))
        self.assertTrue(policy.update(.5, [det()]))

    def test_long_gap_resets_candidate(self):
        policy = Confirmation(seconds=.4)
        for now in (0., .1, .2, .3):
            policy.update(now, [det()])
        policy.update(.5, [])
        self.assertFalse(policy.update(.7, [det()]))

    def test_sitting_and_standing_do_not_trigger(self):
        policy = Confirmation()
        for i in range(100):
            self.assertFalse(policy.update(i / 30, [det('sitting' if i % 2 else 'standing')]))

    def test_two_people_cannot_combine_time(self):
        policy = Confirmation(seconds=.3)
        self.assertFalse(policy.update(0., [det()]))
        self.assertFalse(policy.update(.1, [det(bbox=(400, 300, 500, 350))]))
        self.assertFalse(policy.update(.2, [det()]))
        self.assertFalse(policy.update(.3, [det(bbox=(400, 300, 500, 350))]))

    def test_active_alarm_does_not_end_on_one_missing_frame(self):
        policy = Confirmation(seconds=.1)
        policy.update(0., [det()])
        self.assertTrue(policy.update(.1, [det()]))
        self.assertTrue(policy.update(.2, []))
        self.assertFalse(policy.update(1.2, []))

    def test_deliberate_lying_is_positive_and_transition_is_unscored(self):
        frames = [dict(frame=1, now=0., raw=False), dict(frame=2, now=.1, raw=True), dict(frame=3, now=.2, raw=True)]
        labels = {('adl-01', 1): -1, ('adl-01', 2): 0, ('adl-01', 3): 1}
        row = score('adl-01', frames, labels, 'raw')
        self.assertTrue(row['reported'])
        self.assertFalse(row['false_alarm'])
        self.assertEqual(row['frame_metrics']['tp'], 1)
        self.assertEqual(row['frame_metrics']['tn'], 1)
        self.assertEqual(row['frame_metrics']['fp'], 0)

    def test_premature_only_alarm_does_not_recover_a_missed_lying_event(self):
        frames = [dict(frame=1, now=0., raw=True), dict(frame=2, now=.1, raw=False)]
        row = score('fall-01', frames, {('fall-01', 1): -1, ('fall-01', 2): 1}, 'raw')
        self.assertFalse(row['reported'])
        self.assertTrue(row['premature_only'])

    def test_eof_does_not_fill_time_or_modify_raw_cache(self):
        cache = dict(frames=[dict(frame=i + 1, now=i / 30, detections=[det()]) for i in range(30)])
        before = json.dumps(cache)
        frames = replay(cache, seconds=2.)
        self.assertTrue(all(f['raw'] for f in frames))
        self.assertFalse(any(f['confirmed'] for f in frames))
        self.assertEqual(len(frames), 30)
        self.assertEqual(json.dumps(cache), before)
        row = score('fall-01', frames, {('fall-01', i + 1): 1 for i in range(30)}, 'confirmed', seconds=2.)
        self.assertFalse(row['sufficient_observation'])
        self.assertEqual(summarize([row])['insufficient_observation'], 1)

    def test_output_json_never_contains_nan(self):
        self.assertEqual(json.dumps(clean({'x': float('nan')}), allow_nan=False), '{"x": null}')

    def test_immediate_default_catches_one_frame_clip_without_padding(self):
        cache = dict(frames=[dict(frame=1, now=0., detections=[det()])])
        before = json.dumps(cache)
        frames = replay(cache)
        self.assertTrue(frames[0]['confirmed'])
        row = score('fall-01', frames, {('fall-01', 1): 1}, 'confirmed')
        self.assertTrue(row['sufficient_observation'])
        self.assertEqual(row['latency_seconds'], 0.)
        self.assertEqual(json.dumps(cache), before)

    def test_immediate_gate_has_no_tracking_timestamp_or_release_dependency(self):
        policy = Confirmation(seconds=0.)
        self.assertTrue(policy.update(0., [det()]))
        self.assertFalse(policy.update(0., [det('sitting')]))
        self.assertTrue(policy.update(0., [det(bbox=(400, 300, 410, 600))]))
        self.assertFalse(policy.update(.01, []))

    def test_immediate_replay_matches_raw_for_every_frame(self):
        frames = replay(dict(frames=[dict(frame=i, now=i / 30., detections=ds)
                                    for i, ds in enumerate(([det()], [], [det('standing')], [det()]), 1)]))
        self.assertEqual([f['confirmed'] for f in frames], [True, False, False, True])
        self.assertTrue(all(f['raw'] == f['confirmed'] for f in frames))


if __name__ == '__main__':
    unittest.main()
