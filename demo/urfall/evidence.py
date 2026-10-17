"""Render annotated before/after frames as visual evidence.

Draws the person box, the pose skeleton, the measured features and the rule's
verdict onto real dataset frames, optionally pairing two detection caches
side by side so the effect of a change is visible rather than just tabulated.

    python demo/urfall/evidence.py --before detections-boxmodel --after detections
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.anomaly_rules import AnomalyEngine, LEFT_HIP, LEFT_SHOULDER, RIGHT_HIP, RIGHT_SHOULDER
from demo.person_anomaly_demo import load_config
from demo.urfall import dataset as ds
from demo.urfall.detect import imread_unicode, imwrite_unicode, read_cache

# COCO skeleton edges, for drawing only.
SKELETON = [
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16), (0, 1), (0, 2), (1, 3), (2, 4),
]

GREEN = (80, 220, 80)
RED = (60, 60, 235)
AMBER = (40, 170, 245)
WHITE = (255, 255, 255)
GREY = (160, 160, 160)


def _label(cv2, image, text, origin, colour, scale=0.5, thickness=1):
    (w, h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    x, y = origin
    cv2.rectangle(image, (x - 2, y - h - 4), (x + w + 2, y + 3), (0, 0, 0), -1)
    cv2.putText(image, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, colour, thickness, cv2.LINE_AA)


def annotate(cv2, image, frame_detections, engine, *, title, gt_label, alarm):
    """Draw boxes, skeletons and the rule's reasoning onto a copy of a frame."""
    canvas = image.copy()
    config = engine.config
    kept = [d for d in frame_detections.detections if d.confidence >= config.track_confidence]

    for det in kept:
        fallen = engine.looks_fallen(det)
        colour = RED if fallen else GREEN
        x1, y1, x2, y2 = (int(v) for v in det.bbox)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), colour, 2)

        for a, b in SKELETON:
            if a < len(det.keypoints) and b < len(det.keypoints):
                pa, pb = det.keypoints[a], det.keypoints[b]
                if pa[2] >= config.keypoint_confidence and pb[2] >= config.keypoint_confidence:
                    cv2.line(canvas, (int(pa[0]), int(pa[1])), (int(pb[0]), int(pb[1])), AMBER, 2)
        for px, py, pc in det.keypoints:
            if pc >= config.keypoint_confidence:
                cv2.circle(canvas, (int(px), int(py)), 3, AMBER, -1)

        # The torso vector the angle feature is built on.
        shoulder = det._mean_point((LEFT_SHOULDER, RIGHT_SHOULDER), config.keypoint_confidence)
        hip = det._mean_point((LEFT_HIP, RIGHT_HIP), config.keypoint_confidence)
        if shoulder and hip:
            cv2.line(canvas, (int(shoulder[0]), int(shoulder[1])),
                     (int(hip[0]), int(hip[1])), (255, 120, 255), 3)

        angle = det.torso_angle_degrees(config.keypoint_confidence)
        bits = [f"id{det.track_id}", f"c{det.confidence:.2f}", f"r{det.width_height_ratio:.2f}"]
        bits.append(f"a{angle:.0f}d" if angle is not None else "a--")
        _label(cv2, canvas, " ".join(bits), (x1, max(14, y1 - 6)), colour)

    if not kept:
        _label(cv2, canvas, "NO DETECTION", (10, 70), RED, scale=0.7, thickness=2)

    _label(cv2, canvas, title, (10, 20), WHITE, scale=0.55)
    gt_text = {1: "GT: LYING", 0: "GT: transition", -1: "GT: upright"}.get(gt_label, "GT: -")
    _label(cv2, canvas, gt_text, (10, 40), WHITE if gt_label != 1 else AMBER, scale=0.5)
    _label(cv2, canvas, "ALARM: FALL" if alarm else "alarm: none",
           (10, 58), RED if alarm else GREY, scale=0.5)
    return canvas


def stack(cv2, left, right, gap=8):
    import numpy as np

    h = max(left.shape[0], right.shape[0])
    canvas = np.zeros((h, left.shape[1] + gap + right.shape[1], 3), dtype=left.dtype)
    canvas[: left.shape[0], : left.shape[1]] = left
    canvas[: right.shape[0], left.shape[1] + gap:] = right
    return canvas


def alarm_map(cache, config):
    """Per-frame alarm booleans for one cache, replayed with `config`."""
    from demo.urfall.replay import replay_sequence

    replay = replay_sequence(cache, config)
    return {s.frame: s.alarm for s in replay.signals}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".urfall-work")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "demo" / "results-urfall" / "evidence")
    parser.add_argument("--config", type=Path, default=ROOT / "demo" / "person_anomaly_config.json")
    parser.add_argument("--before", default="detections-boxmodel", help="对比用的旧缓存目录名")
    parser.add_argument("--after", default="detections", help="当前缓存目录名")
    parser.add_argument("--before-label", default="BEFORE yolo11s + ratio1.1/1.5s")
    parser.add_argument("--after-label", default="AFTER yolo11s-pose + ratio0.9/0.5s")
    parser.add_argument(
        "--frames",
        default="fall-01:129,fall-02:083,fall-03:190,fall-04:072,fall-05:123,adl-02:090",
        help="逗号分隔的 序列:帧号（与 --before 缓存左右对比）",
    )
    parser.add_argument(
        "--pairs",
        default="",
        help="用当前缓存把两段不同序列的帧并排，格式 seqA:frame|seqB:frame，多组用逗号分隔",
    )
    parser.add_argument("--pair-prefix", default="pair", help="--pairs 输出文件名前缀")
    args = parser.parse_args(argv)

    import cv2

    from dataclasses import replace as dc_replace

    gt = ds.parse_ground_truth(ds.fetch_ground_truth(args.work_dir))
    after_cfg = load_config(str(args.config))
    # The "before" column is the original shipped rule, for a fair contrast.
    before_cfg = dc_replace(
        after_cfg, fall_ratio=1.1, fall_seconds=1.5, track_confidence=after_cfg.confidence,
        fall_feature_mode="bbox",
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    before_dir = args.work_dir / args.before
    after_dir = args.work_dir / args.after
    have_before = before_dir.is_dir()

    caches: dict[str, dict] = {"before": {}, "after": {}}
    alarms: dict[str, dict] = {"before": {}, "after": {}}
    written = []

    def panel_for(name: str, number: int, title_prefix: str):
        """Annotate one frame using the current ("after") cache."""
        seq_dir = args.work_dir / "frames" / f"{name}-cam0-rgb"
        image = imread_unicode(seq_dir / f"{name}-cam0-rgb-{number:03d}.png")
        if image is None:
            return None
        if name not in caches["after"]:
            cache = read_cache(after_dir / f"{name}.json")
            caches["after"][name] = cache
            alarms["after"][name] = alarm_map(cache, after_cfg)
        cache = caches["after"][name]
        fd = next((f for f in cache.frames if f.frame == number), None)
        if fd is None:
            return None
        return annotate(
            cv2, image, fd, AnomalyEngine(after_cfg),
            title=f"{title_prefix} {name} f{number}",
            gt_label=gt.get((name, number)),
            alarm=alarms["after"][name].get(number, False),
        )

    if args.pairs:
        for group in args.pairs.split(","):
            left_spec, _, right_spec = group.partition("|")
            specs = []
            for spec in (left_spec, right_spec):
                nm, _, num = spec.strip().partition(":")
                specs.append((nm.strip(), int(num)))
            tags = ["真跌倒" if s[0].startswith("fall") else "日常·主动躺下" for s in specs]
            panels = [panel_for(nm, num, tag) for (nm, num), tag in zip(specs, tags)]
            if any(p is None for p in panels):
                print(f"跳过 {group}")
                continue
            combined = stack(cv2, panels[0], panels[1])
            target = args.out_dir / (
                f"{args.pair_prefix}-{specs[0][0]}f{specs[0][1]:03d}"
                f"-vs-{specs[1][0]}f{specs[1][1]:03d}.jpg"
            )
            if imwrite_unicode(target, combined):
                written.append(target)
                print(f"  {target.name}")
        print(f"\n共写出 {len(written)} 张到 {args.out_dir}")
        return 0

    for item in args.frames.split(","):
        name, _, raw = item.partition(":")
        name, number = name.strip(), int(raw)
        seq_dir = args.work_dir / "frames" / f"{name}-cam0-rgb"
        image = imread_unicode(seq_dir / f"{name}-cam0-rgb-{number:03d}.png")
        if image is None:
            print(f"跳过 {name}:{number}（找不到帧）")
            continue
        label = gt.get((name, number))

        panels = []
        for side, directory, cfg, title in (
            ("before", before_dir, before_cfg, args.before_label),
            ("after", after_dir, after_cfg, args.after_label),
        ):
            if side == "before" and not have_before:
                continue
            if name not in caches[side]:
                cache = read_cache(directory / f"{name}.json")
                caches[side][name] = cache
                alarms[side][name] = alarm_map(cache, cfg)
            cache = caches[side][name]
            fd = next((f for f in cache.frames if f.frame == number), None)
            if fd is None:
                continue
            panels.append(
                annotate(
                    cv2, image, fd, AnomalyEngine(cfg),
                    title=f"{title}  |  {name} f{number}",
                    gt_label=label,
                    alarm=alarms[side][name].get(number, False),
                )
            )

        if not panels:
            continue
        combined = panels[0] if len(panels) == 1 else stack(cv2, panels[0], panels[1])
        target = args.out_dir / f"{name}-f{number:03d}.jpg"
        if imwrite_unicode(target, combined):
            written.append(target)
            print(f"  {target.name}")

    print(f"\n共写出 {len(written)} 张对比图到 {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
