"""A/B compare fall-feature modes on one cached detection set.

Every mode replays the same cached detections, so differences come only from
the rule, never from the detector.  Each mode is tuned independently under the
same "no false alarm on any ADL sequence" constraint, which is the only fair
way to compare them.

    python demo/urfall/compare.py [--work-dir ...] [--out results.md]
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.person_anomaly_demo import load_config
from demo.urfall import dataset as ds
from demo.urfall import metrics as mx
from demo.urfall.detect import load_all_caches
from demo.urfall.replay import replay_all

MODES = ("bbox", "angle", "auto", "either", "both")
MODE_CN = {
    "bbox": "宽高比 (现状)",
    "angle": "躯干角 (无回退)",
    "auto": "躯干角优先, 缺则回退",
    "either": "任一满足",
    "both": "同时满足",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".urfall-work")
    parser.add_argument("--config", type=Path, default=ROOT / "demo" / "person_anomaly_config.json")
    parser.add_argument("--out", type=Path, default=None, help="同时写一份 markdown")
    parser.add_argument("--seconds", default="0.3,0.5,0.75,1.0,1.25,1.5")
    parser.add_argument("--loso", action="store_true")
    args = parser.parse_args(argv)

    caches = load_all_caches(args.work_dir / "detections")
    gt = ds.parse_ground_truth(ds.fetch_ground_truth(args.work_dir))
    base = load_config(str(args.config))
    seconds = [float(x) for x in args.seconds.split(",")]
    has_kp = any(d.keypoints for c in caches for f in c.frames for d in f.detections)

    angles = mx.frange(30.0, 88.0, 2.0)
    ratios = mx.frange(0.70, 1.80, 0.05)

    rows: list[dict] = []
    for mode in MODES:
        if mode != "bbox" and not has_kp:
            continue
        cfg = replace(base, fall_feature_mode=mode)
        parameter = "fall_ratio" if mode == "bbox" else "fall_angle_degrees"
        grid = ratios if mode == "bbox" else angles
        sweep_rows = mx.sweep(caches, gt, cfg, values=grid, seconds=seconds, parameter=parameter)
        rec = mx.recommended_config(sweep_rows)
        if rec is None:
            print(f"{mode}: 没有任何零误报配置")
            continue
        tuned = replace(cfg, **{parameter: rec["value"], "fall_seconds": rec["fall_seconds"]})
        replays = replay_all(caches, tuned)
        overall, _ = mx.score_frames(replays, gt)
        seq = mx.sequence_summary(mx.sequence_level(replays, gt))
        entry = {
            "mode": mode,
            "mode_cn": MODE_CN[mode],
            "parameter": parameter,
            "value": rec["value"],
            "fall_seconds": rec["fall_seconds"],
            **overall.to_json(),
            "sequence_recall": seq["sequence_recall"],
            "fall_sequences_detected": seq["fall_sequences_detected"],
            "n_fall_sequences": seq["n_fall_sequences"],
            "adl_false_alarm_events": seq["adl_false_alarm_events"],
        }
        if args.loso:
            loso = mx.leave_one_sequence_out(
                caches, gt, cfg, values=grid, seconds=seconds, parameter=parameter
            )
            entry["loso_f1"] = loso["pooled"]["f1"]
            entry["loso_precision"] = loso["pooled"]["precision"]
            entry["loso_recall"] = loso["pooled"]["recall"]
        rows.append(entry)

    header = f"{'判据':<22} {'参数':<28} {'TP':>4} {'FP':>4} {'精确率':>8} {'召回率':>8} {'F1':>6} {'段级':>6}"
    print(header)
    print("-" * len(header))
    for r in rows:
        param = f"{r['parameter']}={r['value']}, s={r['fall_seconds']}"
        print(
            f"{r['mode_cn']:<22} {param:<28} {r['tp']:>4} {r['fp']:>4} "
            f"{r['precision']:>7.1%} {r['recall']:>7.1%} {r['f1']:>6.3f} "
            f"{r['fall_sequences_detected']}/{r['n_fall_sequences']:>4}"
        )
        if args.loso:
            print(f"{'':<22} {'留一法样本外':<28} {'':>4} {'':>4} "
                  f"{r['loso_precision']:>7.1%} {r['loso_recall']:>7.1%} {r['loso_f1']:>6.3f}")

    if args.out:
        lines = ["| 判据 | 调优后参数 | TP | FP | 精确率 | 召回率 | F1 | 段级检出 |",
                 "|---|---|---:|---:|---:|---:|---:|---:|"]
        for r in rows:
            lines.append(
                f"| {r['mode_cn']} | `{r['parameter']}={r['value']}`, `fall_seconds={r['fall_seconds']}` "
                f"| {r['tp']} | {r['fp']} | {r['precision']:.1%} | {r['recall']:.1%} "
                f"| {r['f1']:.3f} | {r['fall_sequences_detected']}/{r['n_fall_sequences']} |"
            )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        args.out.with_suffix(".json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
