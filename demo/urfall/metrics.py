"""Scoring: confusion matrices, latency, the recall ceiling, and the sweep."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from pathlib import Path
import sys
from typing import Iterable, Mapping, Sequence as TypingSequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.anomaly_rules import AnomalyConfig
from demo.urfall.dataset import FPS, LABEL_LYING, LABEL_NOT_LYING, LABEL_TRANSITION  # noqa: F401
from demo.urfall.detect import CachedSequence
from demo.urfall.replay import GHOST_INCLUDE, SequenceReplay, replay_sequence

GroundTruth = Mapping[tuple[str, int], int]


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else math.nan


@dataclass(frozen=True)
class Confusion:
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    def __add__(self, other: "Confusion") -> "Confusion":
        return Confusion(self.tp + other.tp, self.fp + other.fp, self.tn + other.tn, self.fn + other.fn)

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    @property
    def positives(self) -> int:
        return self.tp + self.fn

    @property
    def negatives(self) -> int:
        return self.tn + self.fp

    @property
    def precision(self) -> float:
        return _ratio(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> float:
        return _ratio(self.tp, self.positives)

    @property
    def specificity(self) -> float:
        return _ratio(self.tn, self.negatives)

    @property
    def accuracy(self) -> float:
        return _ratio(self.tp + self.tn, self.total)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        if math.isnan(p) or math.isnan(r) or (p + r) == 0:
            return math.nan
        return 2 * p * r / (p + r)

    @property
    def balanced_accuracy(self) -> float:
        r, s = self.recall, self.specificity
        if math.isnan(r) or math.isnan(s):
            return math.nan
        return (r + s) / 2

    @property
    def mcc(self) -> float:
        denom = math.sqrt(
            float(self.tp + self.fp) * (self.tp + self.fn) * (self.tn + self.fp) * (self.tn + self.fn)
        )
        if denom == 0:
            return math.nan
        return (self.tp * self.tn - self.fp * self.fn) / denom

    def to_json(self) -> dict[str, float | int]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "tn": self.tn,
            "fn": self.fn,
            "positives": self.positives,
            "negatives": self.negatives,
            "precision": self.precision,
            "recall": self.recall,
            "specificity": self.specificity,
            "accuracy": self.accuracy,
            "f1": self.f1,
            "balanced_accuracy": self.balanced_accuracy,
            "mcc": self.mcc,
        }


#: Score every "lying" frame as something the rule should flag, wherever it
#: occurs.  Answers "can the system tell that someone is on the floor?"
SCOPE_ANY_LYING = "any_lying"
#: Score lying only inside fall sequences; a deliberate lie-down in an ADL
#: sequence becomes a negative, so alarming there is a false positive.
#: Answers the harder question "can it tell a fall from lying down on purpose?"
SCOPE_FALL_ONLY = "fall_only"
POSITIVE_SCOPES = (SCOPE_ANY_LYING, SCOPE_FALL_ONLY)


def score_frames(
    replays: Iterable[SequenceReplay],
    gt: GroundTruth,
    *,
    skip_before: Mapping[str, int] | None = None,
    positive_scope: str = SCOPE_ANY_LYING,
) -> tuple[Confusion, dict[str, Confusion]]:
    """Frame-level confusion, excluding transition and unlabeled frames.

    URFD labels describe pose, not intent: its ADL sequences include a person
    deliberately lying down on the floor, which is labelled exactly like a
    fallen person.  ``positive_scope`` decides how those frames count -- see
    SCOPE_ANY_LYING / SCOPE_FALL_ONLY.  The two answer different questions and
    both are worth reporting.

    ``skip_before`` optionally drops frames earlier than a per-sequence frame
    number; it is used only to build the debounce-compensated diagnostic view.
    """
    if positive_scope not in POSITIVE_SCOPES:
        raise ValueError(f"positive_scope must be one of {POSITIVE_SCOPES}")
    per_sequence: dict[str, Confusion] = {}
    overall = Confusion()
    for replay in replays:
        floor = (skip_before or {}).get(replay.sequence)
        is_fall_sequence = replay.sequence.startswith("fall")
        tp = fp = tn = fn = 0
        for signal in replay.signals:
            label = gt.get((replay.sequence, signal.frame))
            if label is None or label == LABEL_TRANSITION:
                continue
            if floor is not None and signal.frame < floor:
                continue
            expected = label == LABEL_LYING
            if expected and positive_scope == SCOPE_FALL_ONLY and not is_fall_sequence:
                # Lying down on purpose during daily activity: must not alarm.
                expected = False
            if expected:
                if signal.alarm:
                    tp += 1
                else:
                    fn += 1
            else:
                if signal.alarm:
                    fp += 1
                else:
                    tn += 1
        confusion = Confusion(tp, fp, tn, fn)
        per_sequence[replay.sequence] = confusion
        overall = overall + confusion
    return overall, per_sequence


def first_label_frame(gt: GroundTruth, sequence: str, label: int) -> int | None:
    frames = [frame for (seq, frame), value in gt.items() if seq == sequence and value == label]
    return min(frames) if frames else None


def sequence_level(replays: Iterable[SequenceReplay], gt: GroundTruth) -> list[dict]:
    """Per-sequence outcome, alarm latency and tracker-health diagnostics."""
    rows: list[dict] = []
    for replay in replays:
        kind = "fall" if replay.sequence.startswith("fall") else "adl"
        first_lying = first_label_frame(gt, replay.sequence, LABEL_LYING)
        first_alarm = replay.first_alarm_frame
        latency_frames = (
            first_alarm - first_lying if (first_alarm is not None and first_lying is not None) else None
        )
        rows.append(
            {
                "sequence": replay.sequence,
                "kind": kind,
                "expected_fall": kind == "fall",
                "alarmed": replay.alarmed,
                "n_start_events": len(replay.start_events),
                "first_gt_lying_frame": first_lying,
                "first_alarm_frame": first_alarm,
                "latency_frames": latency_frames,
                "latency_seconds": None if latency_frames is None else round(latency_frames / FPS, 3),
                "n_track_ids": len(replay.track_ids),
                "n_condition_changed_ends": replay.condition_changed_ends,
                "n_ghost_frames": sum(1 for s in replay.signals if s.ghost),
                "max_ratio_seen": round(max((s.max_ratio for s in replay.signals), default=0.0), 3),
                "max_torso_angle_seen": round(
                    max((s.max_torso_angle for s in replay.signals if s.max_torso_angle is not None),
                        default=float("nan")),
                    1,
                ),
                "n_fallen_frames": sum(1 for s in replay.signals if s.n_fallen > 0),
            }
        )
    return rows


def sequence_summary(rows: TypingSequence[dict]) -> dict:
    """Sequence-level detection rate and the ADL false-alarm rate."""
    falls = [r for r in rows if r["kind"] == "fall"]
    adls = [r for r in rows if r["kind"] == "adl"]
    detected = sum(1 for r in falls if r["alarmed"])
    false_sequences = sum(1 for r in adls if r["alarmed"])
    false_events = sum(r["n_start_events"] for r in adls)
    return {
        "n_fall_sequences": len(falls),
        "n_adl_sequences": len(adls),
        "fall_sequences_detected": detected,
        "sequence_recall": _ratio(detected, len(falls)),
        "adl_sequences_with_false_alarm": false_sequences,
        "adl_false_alarm_events": false_events,
    }


def event_level(replays: Iterable[SequenceReplay], gt: GroundTruth) -> list[dict]:
    """Per-sequence event scoring, and the split that frame recall hides.

    An operator needs one alarm per fall, not an alarm on every frame, so
    frame-level recall charges the debounce window as failure.  The false
    negatives are therefore split into two cases with very different
    consequences:

    ``pre_alarm_fn``  lying frames before the first alarm -- the unavoidable
                      cost of requiring a sustained pose; harmless once the
                      alarm does fire.
    ``dropout_fn``    lying frames after an alarm was raised but with no alarm
                      active -- a genuine defect, since the consumer would
                      conclude the person got back up.
    """
    rows: list[dict] = []
    for replay in replays:
        lying = sorted(f for (s, f), v in gt.items() if s == replay.sequence and v == LABEL_LYING)
        alarms = {s.frame: s.alarm for s in replay.signals}
        detected = {s.frame: s.n_detections > 0 for s in replay.signals}
        first_alarm = next((f for f in sorted(alarms) if alarms[f]), None)
        kind = "fall" if replay.sequence.startswith("fall") else "adl"

        row = {
            "sequence": replay.sequence,
            "kind": kind,
            "lying_frames": len(lying),
            "first_lying_frame": lying[0] if lying else None,
            "first_alarm_frame": first_alarm,
            "covered": 0,
            "pre_alarm_fn": 0,
            "dropout_fn": 0,
            "dropout_no_detection": 0,
            "false_alarm_frames": 0,
            "reported": False,
            "latency_frames": None,
            "latency_seconds": None,
            "coverage_after_alarm": math.nan,
        }
        if kind == "adl" or not lying:
            # No fall ever happens in a daily-activity sequence, so every
            # alarm there is a false alarm -- including in the sequences where
            # the subject lies down on the floor deliberately, which carry the
            # same "lying" label as a fallen person.
            row["false_alarm_frames"] = sum(1 for a in alarms.values() if a)
            row["false_alarm"] = row["false_alarm_frames"] > 0
            rows.append(row)
            continue

        row["reported"] = first_alarm is not None
        if first_alarm is not None:
            row["latency_frames"] = first_alarm - lying[0]
            row["latency_seconds"] = round((first_alarm - lying[0]) / FPS, 3)

        for frame in lying:
            if alarms.get(frame):
                row["covered"] += 1
            elif first_alarm is None or frame < first_alarm:
                row["pre_alarm_fn"] += 1
            else:
                row["dropout_fn"] += 1
                if not detected.get(frame, False):
                    row["dropout_no_detection"] += 1

        after = [f for f in lying if first_alarm is not None and f >= first_alarm]
        if after:
            row["coverage_after_alarm"] = sum(1 for f in after if alarms.get(f)) / len(after)
        rows.append(row)
    return rows


def event_summary(rows: TypingSequence[dict], *, adl_frames: int = 0) -> dict:
    """Headline operational numbers: did each fall get reported, and how noisily."""
    falls = [r for r in rows if r["kind"] == "fall"]
    adls = [r for r in rows if r["kind"] == "adl"]
    reported = sum(1 for r in falls if r["reported"])
    pre = sum(r["pre_alarm_fn"] for r in falls)
    drop = sum(r["dropout_fn"] for r in falls)
    covered_after = [r for r in falls if r["coverage_after_alarm"] == r["coverage_after_alarm"]]
    latencies = [r["latency_frames"] for r in falls if r["latency_frames"] is not None]
    adl_minutes = adl_frames / FPS / 60 if adl_frames else math.nan
    fa_frames = sum(r["false_alarm_frames"] for r in adls)
    # ADL sequences where the subject lies down on purpose are the hard
    # negatives: the end pose is indistinguishable from a fall.
    hard = [r for r in adls if r["lying_frames"] > 0]
    return {
        "n_falls": len(falls),
        "n_adl_hard_negatives": len(hard),
        "hard_negatives_with_false_alarm": sum(1 for r in hard if r["false_alarm_frames"] > 0),
        "n_adl_easy": len(adls) - len(hard),
        "easy_negatives_with_false_alarm": sum(
            1 for r in adls if r["lying_frames"] == 0 and r["false_alarm_frames"] > 0
        ),
        "falls_reported": reported,
        "event_recall": _ratio(reported, len(falls)),
        "missed": [r["sequence"] for r in falls if not r["reported"]],
        "pre_alarm_fn": pre,
        "dropout_fn": drop,
        "dropout_no_detection": sum(r["dropout_no_detection"] for r in falls),
        "pooled_coverage_after_alarm": (
            sum(r["covered"] for r in covered_after)
            / sum(r["covered"] + r["dropout_fn"] for r in covered_after)
            if covered_after and sum(r["covered"] + r["dropout_fn"] for r in covered_after)
            else math.nan
        ),
        "median_latency_frames": sorted(latencies)[len(latencies) // 2] if latencies else None,
        "max_latency_frames": max(latencies) if latencies else None,
        "n_adl_sequences": len(adls),
        "adl_sequences_with_false_alarm": sum(1 for r in adls if r["false_alarm_frames"] > 0),
        "false_alarm_frames": fa_frames,
        "adl_minutes": adl_minutes,
        "false_alarm_frames_per_minute": fa_frames / adl_minutes if adl_minutes else math.nan,
    }


def detection_rate_by_label(replays: Iterable[SequenceReplay], gt: GroundTruth) -> dict:
    """How often the person detector finds anybody, split by ground-truth pose.

    The fall rule measures the shape of a person box, so it is blind on any
    frame where no box exists.  If this rate collapses on lying frames, the
    bottleneck is the detector, not the rule's thresholds -- and no amount of
    threshold tuning can recover those frames.
    """
    buckets = {"lying": [0, 0], "not_lying": [0, 0]}
    per_sequence: dict[str, dict] = {}
    for replay in replays:
        local = {"lying": [0, 0], "not_lying": [0, 0]}
        for signal in replay.signals:
            label = gt.get((replay.sequence, signal.frame))
            if label is None or label == LABEL_TRANSITION:
                continue
            key = "lying" if label == LABEL_LYING else "not_lying"
            local[key][1] += 1
            buckets[key][1] += 1
            if signal.n_detections > 0:
                local[key][0] += 1
                buckets[key][0] += 1
        per_sequence[replay.sequence] = {
            "lying_detected": local["lying"][0],
            "lying_frames": local["lying"][1],
            "lying_rate": _ratio(local["lying"][0], local["lying"][1]),
            "not_lying_detected": local["not_lying"][0],
            "not_lying_frames": local["not_lying"][1],
            "not_lying_rate": _ratio(local["not_lying"][0], local["not_lying"][1]),
        }
    return {
        "lying_detected": buckets["lying"][0],
        "lying_frames": buckets["lying"][1],
        "lying_rate": _ratio(buckets["lying"][0], buckets["lying"][1]),
        "not_lying_detected": buckets["not_lying"][0],
        "not_lying_frames": buckets["not_lying"][1],
        "not_lying_rate": _ratio(buckets["not_lying"][0], buckets["not_lying"][1]),
        "per_sequence": per_sequence,
    }


def longest_qualifying_run(replays: Iterable[SequenceReplay], config: AnomalyConfig) -> dict:
    """Longest stretch of detected frames the engine reads as fallen.

    Uses the engine's own verdict, so it works whether the decision came from
    the torso angle or the box ratio.  Compared against the debounce
    requirement, this says directly whether a sequence could ever have alarmed.
    """
    rows: dict[str, dict] = {}
    needed = int(round(config.fall_seconds * FPS))
    for replay in replays:
        best = run = 0
        best_end = 0
        for signal in replay.signals:
            if signal.n_detections == 0:
                continue  # a gap does not reset the engine's timer
            if signal.n_fallen > 0:
                run += 1
                if run > best:
                    best, best_end = run, signal.frame
            else:
                run = 0
        rows[replay.sequence] = {
            "longest_run_frames": best,
            "ends_at_frame": best_end,
            "needed_frames": needed,
            "could_fire": best >= needed,
        }
    return rows


def recall_ceiling(
    gt: GroundTruth,
    sequences: Iterable[str],
    fall_seconds: float,
    *,
    fps: float = FPS,
    from_transition: bool = True,
) -> dict:
    """Highest frame-level recall the debounce physically permits.

    Computed from ground truth alone, with no model involved.  The rule cannot
    raise an alarm until the horizontal pose has been held for ``fall_seconds``,
    so every lying frame before that point is an unavoidable false negative.
    Reporting measured recall without this number makes it uninterpretable.
    """
    debounce = int(round(fall_seconds * fps))
    per_sequence: dict[str, dict] = {}
    total_lying = 0
    total_reachable = 0
    for sequence in sequences:
        lying = sorted(f for (s, f), v in gt.items() if s == sequence and v == LABEL_LYING)
        if not lying:
            continue
        transition = first_label_frame(gt, sequence, LABEL_TRANSITION)
        anchor = transition if (from_transition and transition is not None) else lying[0]
        earliest_alarm = anchor + debounce
        reachable = sum(1 for frame in lying if frame >= earliest_alarm)
        per_sequence[sequence] = {
            "lying_frames": len(lying),
            "first_transition_frame": transition,
            "first_lying_frame": lying[0],
            "earliest_alarm_frame": earliest_alarm,
            "reachable_tp": reachable,
            "unreachable_fn": len(lying) - reachable,
        }
        total_lying += len(lying)
        total_reachable += reachable
    return {
        "fall_seconds": fall_seconds,
        "debounce_frames": debounce,
        "from_transition": from_transition,
        "total_lying_frames": total_lying,
        "reachable_tp": total_reachable,
        "ceiling_recall": _ratio(total_reachable, total_lying),
        "per_sequence": per_sequence,
    }


def earliest_alarm_frames(gt: GroundTruth, sequences: Iterable[str], fall_seconds: float) -> dict[str, int]:
    ceiling = recall_ceiling(gt, sequences, fall_seconds)
    return {name: info["earliest_alarm_frame"] for name, info in ceiling["per_sequence"].items()}


def frange(start: float, stop: float, step: float) -> list[float]:
    values: list[float] = []
    current = start
    # Guard against binary-float drift accumulating past the intended endpoint.
    while current <= stop + step / 2:
        values.append(round(current, 6))
        current += step
    return values


def sweep(
    caches: TypingSequence[CachedSequence],
    gt: GroundTruth,
    base_config: AnomalyConfig,
    *,
    values: TypingSequence[float],
    seconds: TypingSequence[float],
    parameter: str = "fall_ratio",
    ghost_policy: str = GHOST_INCLUDE,
    positive_scope: str = SCOPE_FALL_ONLY,
) -> list[dict]:
    """Grid-search a fall threshold against fall_seconds over cached detections.

    ``parameter`` is ``fall_ratio`` for the bounding-box heuristic or
    ``fall_angle_degrees`` for the keypoint one.  Everything else
    (cooldown_seconds, track_lost_grace_seconds, confidence) is held fixed.

    Scoring defaults to SCOPE_FALL_ONLY so the search is not rewarded for
    alarming on someone who lay down deliberately during daily activity.
    """
    rows: list[dict] = []
    for value in values:
        for second in seconds:
            config = replace(base_config, **{parameter: value, "fall_seconds": second})
            replays = [replay_sequence(c, config, ghost_policy=ghost_policy) for c in caches]
            overall, _ = score_frames(replays, gt, positive_scope=positive_scope)
            seq_rows = sequence_level(replays, gt)
            summary = sequence_summary(seq_rows)
            rows.append(
                {
                    "parameter": parameter,
                    "value": value,
                    "fall_seconds": second,
                    **{k: v for k, v in overall.to_json().items()},
                    "sequence_recall": summary["sequence_recall"],
                    "adl_false_alarm_events": summary["adl_false_alarm_events"],
                    "adl_sequences_with_false_alarm": summary["adl_sequences_with_false_alarm"],
                }
            )
    return rows


def _best(rows: TypingSequence[dict], key: str = "f1") -> dict | None:
    scored = [r for r in rows if not math.isnan(r.get(key, math.nan))]
    if not scored:
        return None
    return max(scored, key=lambda r: (r[key], -r["fall_seconds"]))


def best_config(rows: TypingSequence[dict], key: str = "f1") -> dict | None:
    return _best(rows, key)


def recommended_config(rows: TypingSequence[dict], key: str = "f1") -> dict | None:
    """Best configuration that raises no false alarm on any ADL sequence.

    The raw F1 optimum can buy recall with false alarms, which for a patrol
    robot is the expensive direction to be wrong in.  Restricting to the
    zero-false-alarm subset gives an operationally defensible pick.
    """
    clean = [r for r in rows if r.get("adl_false_alarm_events", 0) == 0]
    return _best(clean, key)


def leave_one_sequence_out(
    caches: TypingSequence[CachedSequence],
    gt: GroundTruth,
    base_config: AnomalyConfig,
    *,
    values: TypingSequence[float],
    seconds: TypingSequence[float],
    parameter: str = "fall_ratio",
    ghost_policy: str = GHOST_INCLUDE,
    zero_false_alarm: bool = True,
    folds: int = 0,
) -> dict:
    """Cross-validate parameter selection: tune on the rest, score the held-out part.

    A sweep that selects and reports on the same sequences is a fit, not an
    estimate.  Each fold picks under the same zero-false-alarm constraint the
    deployed configuration would use, so the folds mirror real selection.

    ``folds`` caps the number of splits.  Leave-one-sequence-out costs one full
    sweep per sequence, which is fine for ten sequences and far too slow for
    seventy, so pass e.g. 10 to group sequences into that many folds instead.
    Sequences are dealt round-robin so every fold mixes falls and ADLs.
    """
    chooser = recommended_config if zero_false_alarm else _best
    n = len(caches)
    k = n if folds <= 0 or folds >= n else folds
    # Round-robin assignment keeps fall/adl balanced across folds given the
    # caches arrive grouped by name.
    groups: list[list[int]] = [[] for _ in range(k)]
    for index in range(n):
        groups[index % k].append(index)

    fold_rows: list[dict] = []
    held_total = Confusion()
    for group in groups:
        if not group:
            continue
        held = [caches[i] for i in group]
        train = [c for i, c in enumerate(caches) if i not in set(group)]
        if not train:
            continue
        train_rows = sweep(
            train, gt, base_config,
            values=values, seconds=seconds, parameter=parameter, ghost_policy=ghost_policy,
        )
        picked = chooser(train_rows)
        if picked is None:
            continue
        config = replace(
            base_config,
            **{parameter: picked["value"], "fall_seconds": picked["fall_seconds"]},
        )
        replays = [replay_sequence(c, config, ghost_policy=ghost_policy) for c in held]
        confusion, _ = score_frames(replays, gt, positive_scope=SCOPE_FALL_ONLY)
        held_total = held_total + confusion
        fold_rows.append(
            {
                "held_out": ", ".join(c.sequence for c in held),
                "n_held": len(held),
                "parameter": parameter,
                "picked_value": picked["value"],
                "picked_fall_seconds": picked["fall_seconds"],
                "train_f1": picked["f1"],
                **confusion.to_json(),
            }
        )
    return {
        "parameter": parameter,
        "zero_false_alarm": zero_false_alarm,
        "n_folds": len(fold_rows),
        "folds": fold_rows,
        "pooled": held_total.to_json(),
        "mean_picked_value": (
            sum(f["picked_value"] for f in fold_rows) / len(fold_rows) if fold_rows else math.nan
        ),
        "mean_picked_fall_seconds": (
            sum(f["picked_fall_seconds"] for f in fold_rows) / len(fold_rows)
            if fold_rows else math.nan
        ),
    }
