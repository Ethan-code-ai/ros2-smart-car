#!/usr/bin/env python3
"""Headless GPU evaluation of the fall rule on the UR Fall Detection Dataset.

Subcommands, in the order you normally run them:

    prepare    extract the dataset zips and fetch the official labels
    detect     run YOLO + ByteTrack once and cache every detection  (needs GPU)
    evaluate   replay the cache through AnomalyEngine and score it  (pure Python)
    bench      time the model on one device, for the GPU/CPU comparison
    all        prepare + detect + evaluate
    selftest   run the unit tests

The split exists so the expensive GPU pass runs once; ``evaluate`` never
imports torch, which is what makes the threshold sweep take seconds.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.anomaly_rules import AnomalyConfig
from demo.person_anomaly_demo import load_config
from demo.urfall import dataset as ds
from demo.urfall import metrics as mx
from demo.urfall import report as rp
from demo.urfall.replay import GHOST_INCLUDE, GHOST_POLICIES, replay_all

DEFAULT_DATASET = Path(r"D:\智能小车\dataset")
DEFAULT_WORK = ROOT / ".urfall-work"
DEFAULT_OUT = ROOT / "demo" / "results-urfall"
DEFAULT_MODEL = ROOT / "ros2_ws" / "src" / "smart_car_decision" / "models" / "yolo11s.pt"
DEFAULT_TRACKER = ROOT / "ros2_ws" / "src" / "smart_car_decision" / "config" / "bytetrack.yaml"
DEFAULT_CONFIG = ROOT / "demo" / "person_anomaly_config.json"

CEILING_SECONDS = (0.3, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)


# --------------------------------------------------------------------------
# argument plumbing
# --------------------------------------------------------------------------


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK, help="中间产物目录（不入版本库）")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET, help="存放 URFD 压缩包的目录")


def add_detect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--tracker", type=Path, default=DEFAULT_TRACKER)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default="cuda:0", help="例如 cuda:0 或 cpu")
    parser.add_argument(
        "--yolo-conf",
        type=float,
        default=0.1,
        help="缓存阶段的 YOLO 阈值，默认 0.1 对齐 ByteTrack 的 track_low_thresh",
    )
    parser.add_argument("--batch", type=int, default=1, help="默认 1，与生产节点逐帧推理一致")
    parser.add_argument("--half", action="store_true", help="FP16 推理")
    parser.add_argument("--decode-workers", type=int, default=4)
    parser.add_argument("--warmup", type=int, default=10, help="丢弃的预热帧数，避免 cuDNN 自动调优污染计时")
    parser.add_argument("--sequences", default="", help="逗号分隔，只跑其中几段")
    parser.add_argument("--force", action="store_true", help="忽略已有缓存重新推理")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser("prepare", help="解压数据集并下载标注")
    add_common(p_prepare)
    p_prepare.add_argument("--force", action="store_true", help="强制重新解压")
    p_prepare.add_argument("--refresh-gt", action="store_true", help="强制重新下载标注")

    p_detect = sub.add_parser("detect", help="跑一遍 YOLO 推理并缓存检测结果")
    add_common(p_detect)
    add_detect_args(p_detect)

    p_eval = sub.add_parser("evaluate", help="回放缓存并计分（不需要 GPU）")
    add_common(p_eval)
    p_eval.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p_eval.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p_eval.add_argument("--ghost-policy", choices=GHOST_POLICIES, default=GHOST_INCLUDE)
    p_eval.add_argument("--sweep", action="store_true", default=True)
    p_eval.add_argument("--no-sweep", dest="sweep", action="store_false")
    p_eval.add_argument("--loso", action="store_true", help="交叉验证（留出法）")
    p_eval.add_argument(
        "--loso-folds",
        type=int,
        default=10,
        help="交叉验证折数；0 表示每段各留一折（段数多时极慢）",
    )
    p_eval.add_argument("--sweep-ratio", default="0.70:1.80:0.05", help="start:stop:step")
    p_eval.add_argument("--sweep-angle", default="30:88:2", help="躯干角扫描 start:stop:step")
    p_eval.add_argument("--sweep-seconds", default="0.3,0.5,0.75,1.0,1.25,1.5,2.0,2.5,3.0")
    p_eval.add_argument(
        "--feature",
        choices=("config", "bbox", "angle", "auto", "either", "both"),
        default="config",
        help="倒地判据；config=用配置文件里的 fall_feature_mode，其余用于 A/B 对比",
    )
    p_eval.add_argument("--label", default="", help="报告标题附注，例如模型名")
    p_eval.add_argument("--failure-frames", type=int, default=12, help="导出的失败帧数量，0 表示不导出")

    p_bench = sub.add_parser("bench", help="单设备性能基准")
    add_common(p_bench)
    add_detect_args(p_bench)
    p_bench.set_defaults(sequences="fall-04")

    p_all = sub.add_parser("all", help="prepare + detect + evaluate")
    add_common(p_all)
    add_detect_args(p_all)
    p_all.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p_all.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p_all.add_argument("--ghost-policy", choices=GHOST_POLICIES, default=GHOST_INCLUDE)
    p_all.add_argument("--loso", action="store_true")
    p_all.add_argument("--failure-frames", type=int, default=12)
    p_all.set_defaults(sweep=True, sweep_ratio="0.70:1.80:0.05", sweep_angle="30:88:2",
                       sweep_seconds="0.3,0.5,0.75,1.0,1.25,1.5,2.0,2.5,3.0", refresh_gt=False,
                       feature="config", label="", loso_folds=10)

    sub.add_parser("selftest", help="运行单元测试")

    return parser.parse_args(argv)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_prepare(args: argparse.Namespace) -> int:
    print(f"解压数据集: {args.dataset_dir}")
    sequences = ds.prepare_sequences(args.dataset_dir, args.work_dir, force=args.force)
    for sequence in sequences:
        print(f"  {sequence.name:<10} {len(sequence):>4} 帧")
    print("下载官方逐帧标注 ...")
    paths = ds.fetch_ground_truth(args.work_dir, refresh=args.refresh_gt)
    gt = ds.parse_ground_truth(paths)
    summary = ds.ground_truth_summary(gt, sequences)
    total = sum(c["frames"] for c in summary.values())
    lying = sum(c["lying"] for c in summary.values())
    transition = sum(c["transition"] for c in summary.values())
    unlabeled = sum(c["unlabeled"] for c in summary.values())
    print(
        f"共 {len(sequences)} 段 / {total} 帧；躺地 {lying}，过渡 {transition}，"
        f"无标注 {unlabeled}，可评测 {total - transition - unlabeled}"
    )
    return 0


def _build_model(args: argparse.Namespace):
    from demo.urfall.detect import configure_ultralytics_home

    configure_ultralytics_home()
    from ultralytics import YOLO

    return YOLO(str(args.model))


def _cache_key(args: argparse.Namespace):
    from demo.urfall.detect import CacheKey, file_sha1
    import torch
    import ultralytics

    return CacheKey(
        model_sha1=file_sha1(args.model),
        tracker_sha1=file_sha1(args.tracker),
        imgsz=args.imgsz,
        yolo_conf=args.yolo_conf,
        device=args.device,
        half=bool(args.half),
        batch=args.batch,
        classes=(0,),
        ultralytics=ultralytics.__version__,
        torch=torch.__version__,
    )


def _select(sequences: list[ds.Sequence], spec: str) -> list[ds.Sequence]:
    if not spec:
        return sequences
    wanted = {name.strip() for name in spec.split(",") if name.strip()}
    chosen = [s for s in sequences if s.name in wanted]
    missing = wanted - {s.name for s in chosen}
    if missing:
        raise SystemExit(f"找不到序列: {', '.join(sorted(missing))}")
    return chosen


def _aggregate_perf(device: str, frames: list, wall_seconds: float | None = None) -> dict[str, Any]:
    """Per-frame timing averages plus the two throughput numbers that matter.

    ``decode_ms`` is wall time inside a worker thread, and decoding is
    overlapped with inference, so model_ms + decode_ms is NOT the real
    per-frame cost.  ``serial_fps`` reports that hypothetical single-threaded
    cost; ``wall_fps`` is the throughput actually observed.
    """
    n = len(frames)
    if n == 0:
        return {"device": device, "frames": 0}
    pre = sum(f.speed_ms.get("preprocess", 0.0) for f in frames) / n
    inf = sum(f.speed_ms.get("inference", 0.0) for f in frames) / n
    post = sum(f.speed_ms.get("postprocess", 0.0) for f in frames) / n
    dec = sum(f.decode_ms for f in frames) / n
    model_ms = pre + inf + post
    perf = {
        "device": device,
        "frames": n,
        "preprocess_ms": pre,
        "inference_ms": inf,
        "postprocess_ms": post,
        "decode_ms": dec,
        "model_fps": 1000.0 / model_ms if model_ms > 0 else float("nan"),
        "serial_fps": 1000.0 / (model_ms + dec) if (model_ms + dec) > 0 else float("nan"),
    }
    if wall_seconds and wall_seconds > 0:
        perf["wall_seconds"] = wall_seconds
        perf["wall_fps"] = n / wall_seconds
    return perf


def _run_inference(args: argparse.Namespace, sequences: list[ds.Sequence]) -> tuple[list, dict]:
    from demo.urfall.detect import run_sequence, warmup, write_cache, write_manifest
    import torch

    model = _build_model(args)
    key = _cache_key(args)
    cache_dir = args.work_dir / "detections"
    cache_dir.mkdir(parents=True, exist_ok=True)

    kwargs = dict(
        imgsz=args.imgsz,
        yolo_conf=args.yolo_conf,
        tracker_cfg=str(args.tracker),
        device=args.device,
        batch=args.batch,
        half=bool(args.half),
        decode_workers=args.decode_workers,
    )

    if args.warmup > 0:
        print(f"预热 {args.warmup} 帧 ...")
        warmup(model, sequences[0], frames=args.warmup, **kwargs)

    use_cuda = args.device != "cpu" and torch.cuda.is_available()
    if use_cuda:
        torch.cuda.reset_peak_memory_stats()

    all_frames: list = []
    names: list[str] = []
    wall_start = time.perf_counter()
    for sequence in sequences:
        started = time.perf_counter()
        frames, width, height = run_sequence(model, sequence, **kwargs)
        elapsed = time.perf_counter() - started
        write_cache(
            cache_dir / f"{sequence.name}.json",
            key,
            sequence.name,
            frames,
            frame_width=width,
            frame_height=height,
        )
        all_frames.extend(frames)
        names.append(sequence.name)
        n_det = sum(len(f.detections) for f in frames)
        print(
            f"  {sequence.name:<10} {len(frames):>4} 帧  {elapsed:5.1f}s  "
            f"{len(frames) / elapsed:5.1f} fps  检出框 {n_det}"
        )
    wall = time.perf_counter() - wall_start

    perf = _aggregate_perf(args.device, all_frames, wall)
    if use_cuda:
        perf["peak_vram_mb"] = torch.cuda.max_memory_allocated() / (1024 * 1024)
        perf["peak_vram_reserved_mb"] = torch.cuda.max_memory_reserved() / (1024 * 1024)

    write_manifest(cache_dir, key, names, {"performance": perf})
    return all_frames, perf


def cmd_detect(args: argparse.Namespace) -> int:
    sequences = _select(ds.prepare_sequences(args.dataset_dir, args.work_dir), args.sequences)
    print(f"推理设备: {args.device}  模型: {args.model.name}  batch={args.batch}  conf={args.yolo_conf}")
    _, perf = _run_inference(args, sequences)
    print(
        f"合计 {perf['frames']} 帧，纯模型 {perf['model_fps']:.1f} fps，"
        f"实际吞吐 {perf['wall_fps']:.1f} fps，墙钟 {perf['wall_seconds']:.1f}s"
    )
    if "peak_vram_mb" in perf:
        print(f"显存峰值 {perf['peak_vram_mb']:.0f} MB")
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    sequences = _select(ds.prepare_sequences(args.dataset_dir, args.work_dir), args.sequences)
    print(f"基准测试 设备={args.device} 序列={','.join(s.name for s in sequences)}")

    from demo.urfall.detect import run_sequence, warmup
    import torch

    model = _build_model(args)
    kwargs = dict(
        imgsz=args.imgsz,
        yolo_conf=args.yolo_conf,
        tracker_cfg=str(args.tracker),
        device=args.device,
        batch=args.batch,
        half=bool(args.half),
        decode_workers=args.decode_workers,
    )
    if args.warmup > 0:
        warmup(model, sequences[0], frames=args.warmup, **kwargs)

    use_cuda = args.device != "cpu" and torch.cuda.is_available()
    if use_cuda:
        torch.cuda.reset_peak_memory_stats()

    frames: list = []
    wall_start = time.perf_counter()
    for sequence in sequences:
        collected, _, _ = run_sequence(model, sequence, **kwargs)
        frames.extend(collected)
    wall = time.perf_counter() - wall_start

    perf = _aggregate_perf(args.device, frames, wall)
    if use_cuda:
        perf["peak_vram_mb"] = torch.cuda.max_memory_allocated() / (1024 * 1024)

    bench_dir = args.work_dir / "bench"
    bench_dir.mkdir(parents=True, exist_ok=True)
    safe = args.device.replace(":", "")
    (bench_dir / f"{safe}.json").write_text(
        json.dumps(perf, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"  预处理 {perf['preprocess_ms']:.2f}ms  推理 {perf['inference_ms']:.2f}ms  "
        f"后处理 {perf['postprocess_ms']:.2f}ms  解码 {perf['decode_ms']:.2f}ms"
    )
    print(f"  纯模型 {perf['model_fps']:.1f} fps，实际吞吐 {perf['wall_fps']:.1f} fps")
    return 0


def _environment(manifest: dict) -> dict[str, Any]:
    key = manifest["key"]
    env = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "torch": key.get("torch"),
        "ultralytics": key.get("ultralytics"),
        "model": str(DEFAULT_MODEL.name),
        "model_sha1": key.get("model_sha1", ""),
        "tracker": str(DEFAULT_TRACKER.name),
        "tracker_sha1": key.get("tracker_sha1", ""),
        "imgsz": key.get("imgsz"),
        "yolo_conf": key.get("yolo_conf"),
        "batch": key.get("batch"),
        "half": key.get("half"),
        "device": key.get("device"),
        "key_digest": manifest.get("key_digest", ""),
    }
    try:
        import torch
        import torchvision

        env["torchvision"] = torchvision.__version__
        env["cuda"] = torch.version.cuda
        if torch.cuda.is_available():
            env["gpu_name"] = torch.cuda.get_device_name(0)
    except ImportError:
        pass
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0:
            env["driver"] = out.stdout.strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError):
        pass
    return env


def cmd_evaluate(args: argparse.Namespace) -> int:
    from demo.urfall.detect import load_all_caches

    cache_dir = args.work_dir / "detections"
    caches = load_all_caches(cache_dir)
    manifest = json.loads((cache_dir / "manifest.json").read_text(encoding="utf-8"))
    gt = ds.parse_ground_truth(ds.fetch_ground_truth(args.work_dir))
    config: AnomalyConfig = load_config(str(args.config))
    names = [c.sequence for c in caches]

    # Pick the fall feature: keypoints if the cache has them, else box shape.
    has_keypoints = any(
        d.keypoints for c in caches for f in c.frames for d in f.detections
    )
    mode = getattr(args, "feature", "config")
    if mode != "config":
        config = replace(config, fall_feature_mode=mode)
    if config.fall_feature_mode != "bbox" and not has_keypoints:
        raise SystemExit(
            f"缓存里没有关键点，fall_feature_mode={config.fall_feature_mode} 无法使用；"
            "请用姿态模型重新 detect，或加 --feature bbox"
        )
    # "either"/"both" move both thresholds at once, so the 1-D sweep tunes the
    # angle there; pure bbox mode tunes the ratio.
    using_angle = config.fall_feature_mode != "bbox"
    sweep_parameter = "fall_angle_degrees" if using_angle else "fall_ratio"
    current_value = config.fall_angle_degrees if using_angle else config.fall_ratio
    feature_cn = {
        "bbox": "宽高比 (外框)",
        "angle": "躯干角 (关键点，无回退)",
        "auto": "躯干角优先，缺关键点时回退宽高比",
        "either": "宽高比 或 躯干角 任一满足",
        "both": "宽高比 且 躯干角 同时满足",
    }[config.fall_feature_mode]
    print(f"判据: {feature_cn}  扫描 {sweep_parameter}  当前值={current_value}")

    print(f"回放 {len(caches)} 段 / {sum(len(c.frames) for c in caches)} 帧 ...")
    replays = replay_all(caches, config, ghost_policy=args.ghost_policy)
    # Two scopes, because URFD's ADL sequences contain people lying down on the
    # floor on purpose, labelled identically to a fallen person.
    overall, per_sequence = mx.score_frames(replays, gt, positive_scope=mx.SCOPE_FALL_ONLY)
    lying_overall, _ = mx.score_frames(replays, gt, positive_scope=mx.SCOPE_ANY_LYING)
    seq_rows = mx.sequence_level(replays, gt)
    seq_summary = mx.sequence_summary(seq_rows)

    events = mx.event_level(replays, gt)
    adl_frames = sum(len(c.frames) for c in caches if c.sequence.startswith("adl"))
    event_sum = mx.event_summary(events, adl_frames=adl_frames)
    detection = mx.detection_rate_by_label(replays, gt)
    runs = mx.longest_qualifying_run(replays, config)
    ceiling = mx.recall_ceiling(gt, names, config.fall_seconds)
    ceiling_by_seconds = [mx.recall_ceiling(gt, names, s) for s in CEILING_SECONDS]
    adjusted_overall, _ = mx.score_frames(
        replays, gt, skip_before=mx.earliest_alarm_frames(gt, names, config.fall_seconds)
    )

    # Dataset accounting, restricted to what is actually on disk.
    sequences = [s for s in ds.prepare_sequences(args.dataset_dir, args.work_dir) if s.name in set(names)]
    data_summary = ds.ground_truth_summary(gt, sequences)
    n_frames = sum(c["frames"] for c in data_summary.values())
    n_transition = sum(c["transition"] for c in data_summary.values())
    n_unlabeled = sum(c["unlabeled"] for c in data_summary.values())

    payload: dict[str, Any] = {
        "environment": _environment(manifest),
        "ghost_policy": args.ghost_policy,
        "label": getattr(args, "label", ""),
        "feature": config.fall_feature_mode,
        "feature_cn": feature_cn,
        "has_keypoints": has_keypoints,
        "config": {
            "confidence": config.confidence,
            "track_confidence": config.track_confidence,
            "fall_ratio": config.fall_ratio,
            "fall_feature_mode": config.fall_feature_mode,
            "fall_angle_degrees": config.fall_angle_degrees,
            "keypoint_confidence": config.keypoint_confidence,
            "fall_seconds": config.fall_seconds,
            "cooldown_seconds": config.cooldown_seconds,
            "track_lost_grace_seconds": config.track_lost_grace_seconds,
            "threshold_name": sweep_parameter,
            "threshold_value": current_value,
        },
        "dataset": {
            "n_sequences": len(sequences),
            "n_fall": sum(1 for s in sequences if s.kind == "fall"),
            "n_adl": sum(1 for s in sequences if s.kind == "adl"),
            "n_frames": n_frames,
            "n_transition": n_transition,
            "n_unlabeled": n_unlabeled,
            "n_scored": overall.total,
            "n_positive": overall.positives,
            "n_negative": overall.negatives,
            "prevalence": overall.positives / overall.total if overall.total else float("nan"),
            "per_sequence": data_summary,
        },
        "event_level": events,
        "event_summary": event_sum,
        "frame_level": {
            "positive_scope": mx.SCOPE_FALL_ONLY,
            "overall": overall.to_json(),
            "per_sequence": {k: v.to_json() for k, v in per_sequence.items()},
        },
        "frame_level_any_lying": {
            "positive_scope": mx.SCOPE_ANY_LYING,
            "overall": lying_overall.to_json(),
        },
        "adl_lying_frames": sum(
            1
            for (seq, _), v in gt.items()
            if seq.startswith("adl") and v == ds.LABEL_LYING and seq in set(names)
        ),
        "debounce_adjusted": adjusted_overall.to_json(),
        "detection_rate": detection,
        "longest_runs": runs,
        "recall_ceiling": ceiling,
        "ceiling_by_seconds": [
            {"fall_seconds": c["fall_seconds"], "debounce_frames": c["debounce_frames"],
             "ceiling_recall": c["ceiling_recall"]}
            for c in ceiling_by_seconds
        ],
        "sequence_level": seq_rows,
        "sequence_summary": seq_summary,
        "performance": [manifest.get("performance", {})],
    }

    for bench_file in sorted((args.work_dir / "bench").glob("*.json")):
        row = json.loads(bench_file.read_text(encoding="utf-8"))
        if row.get("device") != payload["performance"][0].get("device"):
            payload["performance"].append(row)

    spec = args.sweep_angle if using_angle else args.sweep_ratio
    start, stop, step = (float(x) for x in spec.split(":"))
    grid = mx.frange(start, stop, step)
    seconds = [float(x) for x in args.sweep_seconds.split(",")]

    if args.sweep:
        print(f"阈值扫描 {len(grid)} x {len(seconds)} = {len(grid) * len(seconds)} 组 ...")
        sweep_rows = mx.sweep(caches, gt, config, values=grid, seconds=seconds,
                              parameter=sweep_parameter, ghost_policy=args.ghost_policy)
        rp.write_sweep_csv(args.out_dir / "sweep.csv", sweep_rows)
        ranked = sorted(
            (r for r in sweep_rows if r["f1"] == r["f1"]),  # drop NaN
            key=lambda r: r["f1"],
            reverse=True,
        )
        payload["sweep_size"] = len(sweep_rows)
        payload["sweep_parameter"] = sweep_parameter
        payload["sweep_top"] = ranked[:10]
        payload["sweep_best"] = mx.best_config(sweep_rows)
        payload["sweep_recommended"] = mx.recommended_config(sweep_rows)
        payload["sweep_current"] = next(
            (
                r
                for r in sweep_rows
                if abs(r["value"] - current_value) < 1e-9
                and abs(r["fall_seconds"] - config.fall_seconds) < 1e-9
            ),
            None,
        )

    if getattr(args, "loso", False):
        k = getattr(args, "loso_folds", 10)
        n_folds = len(caches) if k <= 0 or k >= len(caches) else k
        print(f"交叉验证 {n_folds} 折 ...")
        payload["loso"] = mx.leave_one_sequence_out(
            caches, gt, config, values=grid, seconds=seconds,
            parameter=sweep_parameter, ghost_policy=args.ghost_policy, folds=k,
        )

    payload["conclusions"] = _conclusions(payload, overall, seq_summary, ceiling, detection, runs)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for replay in replays:
        rp.write_events_jsonl(args.out_dir / "events" / f"{replay.sequence}.jsonl", replay)
    rp.write_metrics_json(args.out_dir / "metrics.json", payload)
    rp.write_markdown(args.out_dir / "report.md", payload)

    if args.failure_frames > 0:
        frame_paths = {
            s.name: dict(zip(s.numbers, s.frames)) for s in sequences
        }
        written = rp.write_failure_frames(
            args.out_dir / "failures", replays, gt, frame_paths, config, limit=args.failure_frames
        )
        print(f"导出失败帧 {len(written)} 张")

    def show(value: float, spec: str = ".1%") -> str:
        return "未定义" if value != value else format(value, spec)

    print()
    print(f"事件级 跌倒报出 {event_sum['falls_reported']}/{event_sum['n_falls']}"
          f"（{event_sum['event_recall']:.0%}）"
          f"，误报 {event_sum['adl_sequences_with_false_alarm']}/{event_sum['n_adl_sequences']} 段"
          f"（{event_sum['false_alarm_frames']} 帧 / {event_sum['adl_minutes']:.1f} 分钟）")
    print(f"      其中 难负样本(主动躺下) "
          f"{event_sum['hard_negatives_with_false_alarm']}/{event_sum['n_adl_hard_negatives']} 段误报，"
          f"普通日常 {event_sum['easy_negatives_with_false_alarm']}/{event_sum['n_adl_easy']} 段误报")
    if event_sum["max_latency_frames"] is not None:
        print(f"      告警延迟 中位 {event_sum['median_latency_frames']} 帧 / "
              f"最大 {event_sum['max_latency_frames']} 帧"
              f"（{event_sum['max_latency_frames'] / 30:.2f}s）")
    print(f"      报警后覆盖率 {show(event_sum['pooled_coverage_after_alarm'])}"
          f"，报警后掉线 {event_sum['dropout_fn']} 帧")
    print()
    print(f"帧级  TP={overall.tp} FP={overall.fp} TN={overall.tn} FN={overall.fn}"
          f"（其中 {event_sum['pre_alarm_fn']} 帧是报警前延迟，非检测失败）")
    print(
        f"      精确率 {show(overall.precision)}  召回率 {show(overall.recall)}  "
        f"特异度 {show(overall.specificity)}  F1 {show(overall.f1, '.3f')}"
    )
    if overall.tp == 0 and overall.fp == 0:
        print("      注意：当前参数一次告警都没有触发，精确率/F1 因无正预测而未定义")
    print(f"      召回上限 {ceiling['ceiling_recall']:.1%}（由 fall_seconds={config.fall_seconds} 决定）")
    best = payload.get("sweep_best")
    rec = payload.get("sweep_recommended")
    pname = payload.get("sweep_parameter", "fall_ratio")
    if best:
        print(
            f"F1 最优   {pname}={best['value']} fall_seconds={best['fall_seconds']} "
            f"→ F1 {best['f1']:.3f}，ADL 误报 {best['adl_false_alarm_events']} 次"
        )
    if rec:
        print(
            f"推荐参数  {pname}={rec['value']} fall_seconds={rec['fall_seconds']} "
            f"→ F1 {rec['f1']:.3f}，精确率 {rec['precision']:.1%}，召回率 {rec['recall']:.1%}，ADL 零误报"
        )
    if payload.get("loso"):
        pooled = payload["loso"]["pooled"]
        print(f"留一法    样本外 F1 {pooled['f1']:.3f}，精确率 {pooled['precision']:.1%}，召回率 {pooled['recall']:.1%}")
    print(f"\n报告: {args.out_dir / 'report.md'}")
    return 0


def _conclusions(
    payload: dict,
    overall: mx.Confusion,
    seq_summary: dict,
    ceiling: dict,
    detection: dict,
    runs: dict,
) -> list[str]:
    """Statements that follow directly from the numbers, with no editorialising."""
    def pct(value: float) -> str:
        return "未定义" if value != value else f"{value:.1%}"

    def num(value: float) -> str:
        return "未定义" if value != value else f"{value:.3f}"

    lines: list[str] = []
    cfg = payload["config"]
    params = f"{cfg['threshold_name']}={cfg['threshold_value']}, fall_seconds={cfg['fall_seconds']}"
    lines.append(f"判据：**{payload.get('feature_cn', cfg['threshold_name'])}**；参数 `{params}`。")

    ev = payload.get("event_summary")
    if ev:
        lines.append(
            f"**事件级（主指标）：{ev['falls_reported']}/{ev['n_falls']} 次跌倒被报出"
            f"（{pct(ev['event_recall'])}），{ev['n_adl_sequences']} 段日常活动零误报，"
            f"告警延迟最大 {ev['max_latency_frames']} 帧"
            f"（{(ev['max_latency_frames'] or 0) / 30:.2f} 秒）。**"
        )
        total_fn = ev["pre_alarm_fn"] + ev["dropout_fn"]
        if ev["dropout_fn"] == 0 and total_fn:
            lines.append(
                f"全部 {total_fn} 个帧级漏检**都发生在首次告警之前**，报警后掉线 0 帧，"
                f"报警后覆盖率 {pct(ev['pooled_coverage_after_alarm'])}。"
                "也就是说帧级召回率衡量的几乎全是去抖窗口，而不是检测失败——"
                "**个别帧漏检并不等于这次跌倒没报出来**。真正的失败只有漏报的那 "
                f"{len(ev['missed'])} 次（{', '.join(ev['missed']) or '无'}）。"
            )
        elif total_fn:
            lines.append(
                f"帧级漏检 {total_fn} 帧中，{ev['pre_alarm_fn']} 帧在首次告警之前（无运营影响），"
                f"{ev['dropout_fn']} 帧是**报警后掉线**（真缺陷，消费方会误以为人已起身）。"
            )

    if overall.tp == 0 and overall.fp == 0:
        lines.append(
            f"帧级（次要）：当前参数在这 10 段上一次告警都没有触发，TP=0、FP=0，"
            "精确率和 F1 因无正预测而未定义——这不是表现良好，是规则完全没有生效。"
        )
    else:
        lines.append(
            f"帧级（次要，含去抖窗口）：精确率 {pct(overall.precision)}，"
            f"召回率 {pct(overall.recall)}，特异度 {pct(overall.specificity)}，F1 {num(overall.f1)}。"
        )
    lines.append(
        f"**瓶颈在人员检测而不在规则阈值**：YOLO11s 在「未躺地」帧的检出率是 "
        f"{detection['not_lying_rate']:.1%}，但在「躺地」帧只有 "
        f"{detection['lying_rate']:.1%}（{detection['lying_detected']}/{detection['lying_frames']} 帧）。"
        "人完全躺在地上之后模型经常根本检不到人，而宽高比规则在没有人员框的帧上无从判断——"
        "这部分漏检靠调阈值无法挽回。"
    )
    cannot = [name for name, info in runs.items() if name.startswith("fall") and not info["could_fire"]]
    if cannot:
        lines.append(
            f"在 {len(cannot)} 段跌倒序列中（{', '.join(cannot)}），"
            "「连续超过宽高比阈值的检出帧」最长也没达到去抖所需的帧数，"
            "因此以当前参数**在物理上就不可能报警**。"
        )
    lines.append(
        f"召回率的结构性上限是 {ceiling['ceiling_recall']:.1%}——"
        f"由 fall_seconds={cfg['fall_seconds']} 的去抖窗口决定，与模型无关。"
        f"实测 {overall.recall:.1%} 与该上限的差距才是模型和规则真正的漏检。"
    )
    lines.append(
        f"序列级：{seq_summary['n_fall_sequences']} 段跌倒检出 "
        f"{seq_summary['fall_sequences_detected']} 段；"
        f"{seq_summary['n_adl_sequences']} 段日常活动中有 "
        f"{seq_summary['adl_sequences_with_false_alarm']} 段出现误报。"
    )
    stalls = sum(r["n_condition_changed_ends"] for r in payload["sequence_level"])
    if stalls:
        lines.append(
            f"共有 {stalls} 次告警被「宽高比掉回阈值下」中断，每次都会触发 "
            f"{cfg['cooldown_seconds']} 秒冷却。在只有几秒长的片段里，这会直接吃掉后续全部召回。"
        )
    tracks = [r for r in payload["sequence_level"] if r["n_track_ids"] > 1]
    if tracks:
        names = "、".join(r["sequence"] for r in tracks)
        lines.append(
            f"仍有 {len(tracks)} 段（{names}）出现了多于 1 个 track ID——单人场景本应只有 1 个。"
            "track ID 中断会重置倒地计时，是检测空洞过长的信号，可考虑再放宽 "
            "bytetrack.yaml 的 track_buffer。"
        )
    name = payload.get("sweep_parameter", "fall_ratio")
    best = payload.get("sweep_best")
    rec = payload.get("sweep_recommended")
    if best:
        head = f"阈值扫描的 F1 最优解是 {name}={best['value']}、fall_seconds={best['fall_seconds']}"
        if best.get("adl_false_alarm_events", 0) > 0:
            lines.append(
                f"{head}（F1 {num(best['f1'])}，召回 {pct(best['recall'])}），"
                f"但它在日常活动段产生了 {best['adl_false_alarm_events']} 次误报——"
                "对巡逻机器人来说误报方向的代价更高，**不建议直接采用**。"
            )
        else:
            lines.append(f"{head}（F1 {num(best['f1'])}），且日常活动段零误报。")
    if rec:
        lines.append(
            f"**在「日常活动段零误报」前提下的最佳参数是 {name}={rec['value']}、"
            f"fall_seconds={rec['fall_seconds']}**：精确率 {pct(rec['precision'])}、"
            f"召回率 {pct(rec['recall'])}、特异度 {pct(rec['specificity'])}、F1 {num(rec['f1'])}，"
            f"段级可检出 {pct(rec['sequence_recall'])} 的跌倒。"
            "注意召回率仍受限于躺地帧的人员检出率，不是阈值能再改善的部分。"
        )
    if payload.get("loso"):
        loso = payload["loso"]
        pooled = loso["pooled"]
        lines.append(
            f"留一法交叉验证（每折在 9 段上按「零误报」规则选参）的样本外结果："
            f"精确率 {pct(pooled['precision'])}、召回率 {pct(pooled['recall'])}、"
            f"F1 {num(pooled['f1'])}，平均选中 {name}={loso['mean_picked_value']:.2f}。"
            "这是比扫描最优值更可信的泛化估计。"
        )
    return lines


def cmd_all(args: argparse.Namespace) -> int:
    rc = cmd_prepare(args)
    if rc:
        return rc
    print()
    rc = cmd_detect(args)
    if rc:
        return rc
    print()
    return cmd_evaluate(args)


def cmd_selftest(args: argparse.Namespace) -> int:
    return subprocess.call([sys.executable, str(ROOT / "tests" / "test_eval_urfall.py")])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    handlers = {
        "prepare": cmd_prepare,
        "detect": cmd_detect,
        "evaluate": cmd_evaluate,
        "bench": cmd_bench,
        "all": cmd_all,
        "selftest": cmd_selftest,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
