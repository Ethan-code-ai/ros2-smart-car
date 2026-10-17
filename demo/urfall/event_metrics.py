"""Event-level scoring: did each real fall get reported, and did it stay reported?

Frame-level recall penalises every frame a person lies on the floor without an
active alarm, but an operator only needs one alarm per fall. What actually
matters is: was each fall reported, how fast, did the alarm hold while the
person was still down, and how often did it cry wolf.

The scoring itself lives in ``metrics.event_level`` / ``metrics.event_summary``
so this CLI and the main report can never drift apart.

    python demo/urfall/event_metrics.py
"""

from __future__ import annotations

import argparse
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

FPS = ds.FPS


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".urfall-work")
    parser.add_argument("--config", type=Path, default=ROOT / "demo" / "person_anomaly_config.json")
    parser.add_argument("--show-all", action="store_true", help="列出每一段，而不只是摘要")
    args = parser.parse_args(argv)

    caches = load_all_caches(args.work_dir / "detections")
    gt = ds.parse_ground_truth(ds.fetch_ground_truth(args.work_dir))
    config = load_config(str(args.config))
    replays = replay_all(caches, config)
    rows = mx.event_level(replays, gt)
    adl_frames = sum(len(c.frames) for c in caches if c.sequence.startswith("adl"))
    s = mx.event_summary(rows, adl_frames=adl_frames)

    falls = [r for r in rows if r["kind"] == "fall"]
    adls = [r for r in rows if r["kind"] == "adl"]

    print("=== 跌倒段（每次跌倒只需报一次）===")
    print(f"{'序列':<9} {'报出':<5} {'延迟(帧/秒)':<13} {'躺地帧':>6} {'已覆盖':>6} "
          f"{'报警前':>6} {'报警后掉线':>10}")
    for r in falls:
        if not args.show_all and r["reported"]:
            continue
        lat = "—" if r["latency_frames"] is None else f"{r['latency_frames']}/{r['latency_seconds']:.2f}s"
        print(f"{r['sequence']:<9} {'是' if r['reported'] else '否':<5} {lat:<13} "
              f"{r['lying_frames']:>6} {r['covered']:>6} {r['pre_alarm_fn']:>6} {r['dropout_fn']:>10}")
    if not args.show_all:
        print("（只列出漏报的段，加 --show-all 看全部）")

    print("\n=== 日常活动段（任何告警都是误报）===")
    for r in adls:
        if r["false_alarm_frames"] == 0 and not args.show_all:
            continue
        tag = "难负样本(主动躺下)" if r["lying_frames"] > 0 else "普通日常"
        print(f"{r['sequence']:<9} 误报 {r['false_alarm_frames']:>4} 帧   {tag}")

    print(f"\n事件级召回      {s['falls_reported']}/{s['n_falls']} 次跌倒被报出"
          f"（{s['event_recall']:.0%}）")
    print(f"漏报            {len(s['missed'])} 次")
    print(f"告警延迟        中位 {s['median_latency_frames']} 帧 / 最大 {s['max_latency_frames']} 帧")
    print(f"误报            {s['adl_sequences_with_false_alarm']}/{s['n_adl_sequences']} 段，"
          f"共 {s['false_alarm_frames']} 帧"
          f"（{s['adl_minutes']:.1f} 分钟，{s['false_alarm_frames_per_minute']:.1f} 帧/分钟）")
    print(f"  难负样本      {s['hard_negatives_with_false_alarm']}/{s['n_adl_hard_negatives']} 段"
          f"（人主动躺在地板上，终止姿态与跌倒相同）")
    print(f"  普通日常      {s['easy_negatives_with_false_alarm']}/{s['n_adl_easy']} 段")
    print(f"报警后覆盖率    {s['pooled_coverage_after_alarm']:.1%}（告警拉起后还能盖住多少躺地帧）")
    print(f"\n帧级漏检拆解    共 {s['pre_alarm_fn'] + s['dropout_fn']} 帧（仅跌倒段）")
    total = max(1, s["pre_alarm_fn"] + s["dropout_fn"])
    print(f"  报警前延迟    {s['pre_alarm_fn']} 帧（{s['pre_alarm_fn'] / total:.0%}）"
          "— 去抖+检测延迟，报一次就够，无运营影响")
    print(f"  报警后掉线    {s['dropout_fn']} 帧（{s['dropout_fn'] / total:.0%}）"
          f"— 真缺陷，其中 {s['dropout_no_detection']} 帧是完全没检到人")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
