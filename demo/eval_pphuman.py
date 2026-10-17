"""Evaluate the official, trained PP-Human fall model on local URFD sequences.

Uses PaddleDetection's own detector, tracker, HRNet postprocessing, skeleton
normalization, 50-frame buffering and ST-GCN inference. No bbox fall rule is
used. Each sequence starts with a fresh tracker and action history. Raw model
decisions and the official 80-frame display hold are both saved for auditing.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import copy
import csv
import hashlib
import io
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.urfall import dataset as ds
from demo.urfall import metrics as mx
from demo.urfall.replay import FrameSignal, SequenceReplay

MODEL_NAMES = ("mot_ppyoloe_l_36e_pipeline", "dark_hrnet_w32_256x192", "STGCN")
SCHEMA = 1


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".tmp")
    staging.write_text(json.dumps(clean_json(value), ensure_ascii=False, indent=2), encoding="utf-8")
    staging.replace(path)


def clean_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(item) for item in value]
    return value


def prepare_models(archives: Path, models: Path) -> None:
    """Use an ASCII model path because Paddle's native file loader needs it."""
    for name in MODEL_NAMES:
        archive_path = archives / (name + ".zip")
        fingerprint = sha256(archive_path)
        target = models / name
        marker = target / ".archive-sha256"
        if marker.is_file() and marker.read_text().strip() == fingerprint:
            if all((target / file).is_file() for file in ("model.pdmodel", "model.pdiparams", "infer_cfg.yml")):
                continue
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                relative = PurePosixPath(member.filename.replace("\\", "/"))
                if relative.is_absolute() or ".." in relative.parts or any(":" in part for part in relative.parts):
                    raise ValueError(f"Unsafe archive entry: {member.filename}")
                if not relative.parts or relative.parts[0] != name:
                    raise ValueError(f"Unexpected model directory: {member.filename}")
                destination = models.joinpath(*relative.parts)
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, destination.open("wb") as output:
                        shutil.copyfileobj(source, output)
        marker.write_text(fingerprint, encoding="ascii")
        print(f"Model ready: {target}", flush=True)


def add_paddle_paths(source: Path) -> None:
    for path in (source / "deploy", source / "deploy/python", source / "deploy/pipeline",
                 source / "deploy/pipeline/pphuman", source / "deploy/pptracking/python"):
        sys.path.insert(0, str(path))


def build_runtime(args):
    # These published models are legacy .pdmodel graphs, not PIR .json graphs.
    os.environ.setdefault("FLAGS_enable_pir_api", "0")
    import paddle
    import numpy as np
    import yaml

    if args.device == "GPU":
        if not paddle.is_compiled_with_cuda() or paddle.device.cuda.device_count() < 1:
            raise RuntimeError("Paddle CUDA is unavailable; no silent CPU fallback is allowed.")
        paddle.set_device("gpu:0")
    else:
        paddle.set_device("cpu")
    np.random.seed(0)
    add_paddle_paths(args.paddle_source)
    from python.keypoint_infer import KeyPointDetector
    from python.keypoint_postprocess import translate_to_ori_images
    from pptracking.python.mot_sde_infer import SDE_Detector
    from pphuman.action_infer import SkeletonActionRecognizer
    from pphuman.action_utils import KeyPointBuff, ActionVisualHelper
    from pipe_utils import crop_image_with_mot, parse_mot_res, parse_mot_keypoint

    tracker_path = args.paddle_source / "deploy/pipeline/config/tracker_config.yml"
    tracker_cfg = yaml.safe_load(tracker_path.read_text(encoding="utf-8"))
    common = {"device": args.device, "run_mode": "paddle", "cpu_threads": 4, "output_dir": str(args.out_dir)}
    mot = SDE_Detector(str(args.models_dir / MODEL_NAMES[0]), str(tracker_path), **common)
    kpt = KeyPointDetector(str(args.models_dir / MODEL_NAMES[1]), batch_size=8, use_dark=True, **common)
    action = SkeletonActionRecognizer(str(args.models_dir / MODEL_NAMES[2]), window_size=50, **common)
    runtime = dict(mot=mot, kpt=kpt, action=action, KeyPointBuff=KeyPointBuff,
                   ActionVisualHelper=ActionVisualHelper, translate=translate_to_ori_images,
                   crop=crop_image_with_mot, parse_mot=parse_mot_res, parse_skeleton=parse_mot_keypoint,
                   tracker_template=copy.deepcopy(mot.tracker))
    commit = subprocess.check_output(["git", "-C", str(args.paddle_source), "rev-parse", "HEAD"], text=True).strip()
    environment = {"python": platform.python_version(), "paddle": paddle.__version__,
                   "cuda": paddle.version.cuda(), "cudnn": paddle.version.cudnn(), "device": args.device,
                   "source_commit": commit, "tracker": tracker_cfg,
                   "window_frames": 50, "display_frames": 80, "coord_size": [384, 512],
                   "decision": "argmax, class 0=fall, class 1=other", "run_mode": "paddle", "dtype": "FP32",
                   "eos_flush": False}
    environment["cudnn_package"] = metadata.version("nvidia-cudnn-cu12")
    if args.device == "GPU":
        environment["gpu"] = paddle.device.cuda.get_device_name(0)
    for name in MODEL_NAMES:
        environment[name] = {file: sha256(args.models_dir / name / file)
                             for file in ("model.pdmodel", "model.pdiparams", "infer_cfg.yml")}
    return runtime, environment


def cache_identity(environment: dict, sequence: ds.Sequence, zip_path: Path) -> str:
    payload = {"schema": SCHEMA, "environment": environment, "dataset_zip_sha256": sha256(zip_path),
               "sequence": sequence.name, "frame_numbers": sequence.numbers}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def detect_sequence(sequence: ds.Sequence, runtime: dict, identity: str) -> dict:
    import cv2
    import numpy as np

    mot = runtime["mot"]
    mot.tracker = copy.deepcopy(runtime["tracker_template"])
    # Base counters are class attributes, not part of the copied tracker.
    from mot.tracker.base_jde_tracker import BaseTrack
    from mot.tracker.ocsort_tracker import KalmanBoxTracker
    BaseTrack.init_count(mot.num_classes)
    KalmanBoxTracker.count = 0
    buff = runtime["KeyPointBuff"](50)
    helper = runtime["ActionVisualHelper"](80)
    frames = []
    started = time.perf_counter()
    shape = None
    for index, (number, path) in enumerate(zip(sequence.numbers, sequence.frames)):
        bgr = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError(f"Unable to read {path}")
        shape = bgr.shape[:2]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        # Upstream prints one warning on every empty frame; the cache records
        # these frames explicitly, while exceptions still propagate normally.
        with redirect_stdout(io.StringIO()):
            tracked = mot.predict_image([rgb.copy()], visual=False, frame_count=index)
        mot_res = runtime["parse_mot"](tracked)
        boxes = mot_res["boxes"]
        actions = []
        keypoints = []
        if len(boxes):
            crops, new_boxes, original_boxes = runtime["crop"](rgb, mot_res)
            if len(crops) != len(boxes):
                raise ValueError(f"{sequence.name}:{number}: person crop/track mismatch")
            prediction = runtime["kpt"].predict_image(crops, visual=False)
            points, point_scores = runtime["translate"](prediction, np.asarray(new_boxes))
            keypoints = points.tolist()
            kpt_res = {"keypoint": [keypoints, point_scores.tolist()], "bbox": original_boxes}
            buff.update(kpt_res, mot_res)
            if buff.get_state():
                collected = buff.get_collected_keypoint()
                skeleton_input = runtime["parse_skeleton"](collected, [384, 512])
                predictions = runtime["action"].predict_skeleton_with_mot(skeleton_input)
                for (track, pred), (_, clip) in zip(predictions, collected):
                    actions.append({"track_id": int(track), "class_id": int(pred["class"][0]),
                                    "score": float(pred["score"][0]), "observations": clip.frames})
                helper.update(predictions)
        # Apply the same display lifetime as the official video demo, once per frame.
        alarm_ids = sorted(int(track) for track in helper.get_visualize_ids())
        frames.append({"frame": number, "now": sequence.time_of(number), "boxes": boxes.tolist(),
                       "keypoints": keypoints, "actions": actions, "alarm_ids": alarm_ids,
                       "alarm": bool(alarm_ids), "raw_alarm": any(a["class_id"] == 0 for a in actions)})
        if (index + 1) % 50 == 0:
            print(f"  {sequence.name}: {index + 1}/{len(sequence)} frames", flush=True)
    seconds = time.perf_counter() - started
    return {"schema": SCHEMA, "identity": identity, "sequence": sequence.name, "frames": frames,
            "height": shape[0], "width": shape[1], "seconds": seconds,
            "remaining_buffer_observations": {str(track): clip.frames for track, clip in buff.keypoint_saver.items()}}


def as_replay(cache: dict, *, raw: bool = False) -> SequenceReplay:
    replay = SequenceReplay(cache["sequence"])
    for frame in cache["frames"]:
        tracks = {int(box[0]) for box in frame["boxes"]}
        replay.track_ids.update(tracks)
        replay.signals.append(FrameSignal(frame=frame["frame"], now=frame["now"],
                             alarm=frame["raw_alarm"] if raw else frame["alarm"],
                             ghost=bool(frame["alarm_ids"]) and not bool(set(frame["alarm_ids"]) & tracks),
                             n_detections=len(tracks), n_tracks=len(tracks), max_ratio=0, feature="stgcn"))
    return replay


def read_caches(args, sequences):
    manifest = json.loads((args.cache_dir / "manifest.json").read_text(encoding="utf-8"))
    zips = ds.discover_zips(args.dataset_dir)
    caches = []
    for sequence in sequences:
        path = args.cache_dir / f"{sequence.name}.json"
        cache = json.loads(path.read_text(encoding="utf-8"))
        if cache["schema"] != SCHEMA or [f["frame"] for f in cache["frames"]] != list(sequence.numbers):
            raise ValueError(f"Incomplete or incompatible prediction cache: {path}")
        expected = cache_identity(manifest["environment"], sequence, zips[sequence.name])
        if cache["identity"] != expected:
            raise ValueError(f"Model/dataset cache fingerprint mismatch: {path}; run detect again")
        caches.append(cache)
    return caches


def alarm_events(cache):
    events = []
    previous = False
    for frame in cache["frames"]:
        if frame["alarm"] != previous:
            events.append({"sequence": cache["sequence"], "frame": frame["frame"], "now": frame["now"],
                           "state": "started" if frame["alarm"] else "ended", "track_ids": frame["alarm_ids"]})
        previous = frame["alarm"]
    return events


def export_evidence(args, sequences, caches, rows):
    import cv2
    import numpy as np

    by_name = {sequence.name: sequence for sequence in sequences}
    by_cache = {cache["sequence"]: cache for cache in caches}
    destination = args.out_dir / "evidence"
    destination.mkdir(parents=True, exist_ok=True)
    for row in rows:
        sequence = by_name[row["sequence"]]
        cache = by_cache[row["sequence"]]
        frame_number = row["first_alarm_frame"]
        verdict = "fall-reported" if row["kind"] == "fall" else "adl-false-alarm"
        if frame_number is None:
            if row["kind"] == "adl":
                continue
            frame_number = row["first_lying_frame"]
            verdict = "fall-missed"
        if frame_number not in sequence.numbers:
            continue
        index = sequence.numbers.index(frame_number)
        frame = cache["frames"][index]
        image = cv2.imdecode(np.fromfile(sequence.frames[index], dtype=np.uint8), cv2.IMREAD_COLOR)
        for box in frame["boxes"]:
            track = int(box[0])
            colour = (0, 0, 255) if track in frame["alarm_ids"] else (0, 200, 0)
            x1, y1, x2, y2 = [int(value) for value in box[3:7]]
            cv2.rectangle(image, (x1, y1), (x2, y2), colour, 2)
            cv2.putText(image, f"ID {track} {box[2]:.2f}", (x1, max(y1 - 7, 18)), cv2.FONT_HERSHEY_SIMPLEX, .55, colour, 2)
        for person in frame["keypoints"]:
            for x, y, confidence in person:
                if confidence >= .2:
                    cv2.circle(image, (int(x), int(y)), 3, (0, 255, 255), -1)
        cv2.rectangle(image, (0, 0), (image.shape[1], 50), (15, 15, 15), -1)
        cv2.putText(image, f"{sequence.name} frame {frame_number} {verdict}", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 1)
        text = " | ".join(f"STGCN id={a['track_id']} class={a['class_id']} logit={a['score']:.3f}" for a in frame["actions"])
        cv2.putText(image, text or "No new model decision at this frame", (8, 41), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1)
        ok, encoded = cv2.imencode(".jpg", image)
        if not ok:
            raise RuntimeError("Evidence image encoding failed")
        encoded.tofile(destination / f"{sequence.name}-{verdict}-f{frame_number:03d}.jpg")


def evaluate(args, sequences):
    caches = read_caches(args, sequences)
    gt = ds.parse_ground_truth(ds.fetch_ground_truth(args.work_dir))
    replays = [as_replay(cache) for cache in caches]
    rows = mx.event_level(replays, gt)
    raw_rows = mx.event_level([as_replay(cache, raw=True) for cache in caches], gt)
    adl_frames = sum(len(cache["frames"]) for cache in caches if cache["sequence"].startswith("adl"))
    summary = mx.event_summary(rows, adl_frames=adl_frames)
    raw_summary = mx.event_summary(raw_rows, adl_frames=adl_frames)
    event_confusion = mx.Confusion(tp=summary["falls_reported"],
        fp=summary["adl_sequences_with_false_alarm"],
        tn=summary["n_adl_sequences"] - summary["adl_sequences_with_false_alarm"],
        fn=summary["n_falls"] - summary["falls_reported"])
    confusion, _ = mx.score_frames(replays, gt, positive_scope=mx.SCOPE_FALL_ONLY)
    dataset_summary = ds.ground_truth_summary(gt, sequences)
    # A standing-only false alarm must not masquerade as detection of a later fall.
    fall_after_onset = []
    premature = []
    for cache in caches:
        if not cache["sequence"].startswith("fall"):
            continue
        name = cache["sequence"]
        onset = mx.first_label_frame(gt, name, ds.LABEL_TRANSITION)
        if onset is None:
            onset = mx.first_label_frame(gt, name, ds.LABEL_LYING)
        decisions = [f["frame"] for f in cache["frames"] if f["raw_alarm"]]
        if onset is not None and any(f >= onset for f in decisions):
            fall_after_onset.append(name)
        if onset is not None and any(f < onset for f in decisions):
            premature.append(name)
    seconds = sum(cache["seconds"] for cache in caches)
    total_frames = sum(len(cache["frames"]) for cache in caches)
    manifest = json.loads((args.cache_dir / "manifest.json").read_text(encoding="utf-8"))
    baseline = None
    baseline_path = ROOT / "demo/results-urfall/metrics.json"
    if baseline_path.is_file():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        if set(baseline["dataset"]["per_sequence"]) != {sequence.name for sequence in sequences}:
            baseline = None
    output = {"created_utc": datetime.now(timezone.utc).isoformat(), "environment": manifest["environment"],
              "dataset": {"n_sequences": len(caches), "n_frames": total_frames, "per_sequence": dataset_summary},
              "event_summary": summary, "raw_event_summary": raw_summary, "per_sequence": rows,
              "event_confusion": event_confusion.to_json(),
              "raw_per_sequence": raw_rows, "frame_metrics_fall_only": confusion.to_json(),
              "fall_decisions_after_onset": fall_after_onset, "premature_fall_decisions": premature,
              "performance": {"seconds": seconds, "fps": total_frames / seconds},
              "limitations": ["Official ST-GCN training includes URFD; this is not an independent generalization test.",
                              "Non-overlapping 50-observation windows; incomplete final windows are not flushed.",
                              "Frame alarms include the official 80-frame visualization hold; raw decisions are also reported."]}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    save_json(args.out_dir / "metrics.json", output)
    with (args.out_dir / "sequences.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        columns = ["sequence", "kind", "reported", "false_alarm_frames", "first_alarm_frame",
                   "first_lying_frame", "latency_seconds", "lying_frames", "pre_alarm_fn", "dropout_fn"]
        writer = csv.DictWriter(handle, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    for cache in caches:
        path = args.out_dir / "events" / (cache["sequence"] + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in alarm_events(cache)), encoding="utf-8")
    export_evidence(args, sequences, caches, rows)
    baseline_summary = (baseline or {}).get("event_summary", {})
    lines = ["# PP-Human 官方跌倒模型：本地 URFD 评测", "",
             f"数据：{len(caches)} 段、{total_frames} 帧，30 FPS。", "",
             "模型：官方 PP-YOLOE + 跟踪 + HRNet + ST-GCN，50 帧骨骼窗口，argmax 判类别，未调整阈值。", "",
             "**训练集重叠：官方说明 ST-GCN 训练包含 URFD；这里是本地回归结果，不是独立泛化性能。**", "",
             "## 事件级结果", "", "| 指标 | PP-Human | 原宽高比方案（已有报告） |", "|---|---:|---:|"]
    for title, key in (("跌倒报出", "falls_reported"), ("ADL 误报段", "adl_sequences_with_false_alarm"),
                       ("主动躺下误报段", "hard_negatives_with_false_alarm"), ("普通 ADL 误报段", "easy_negatives_with_false_alarm"),
                       ("ADL 告警帧", "false_alarm_frames"), ("告警延迟中位数（帧，负数表示早于躺地）", "median_latency_frames")):
        lines.append(f"| {title} | {summary[key]} | {baseline_summary.get(key, '—')} |")
    lines.extend(["", f"事件召回率：{summary['event_recall']:.1%}（{summary['falls_reported']}/{summary['n_falls']}）。",
                  f"ADL 误报：{summary['adl_sequences_with_false_alarm']}/{summary['n_adl_sequences']} 段。",
                  f"漏报：{', '.join(summary['missed']) or '无'}。", "",
                  f"仅统计跌倒过渡开始后新产生的模型跌倒判断：{len(fall_after_onset)}/{summary['n_falls']} 段。",
                  f"过渡前已产生跌倒判断的段：{', '.join(premature) or '无'}。", "",
                  "## 性能与评测口径", "", f"GPU/CPU：{manifest['environment']['device']}；Paddle {manifest['environment']['paddle']}；FP32。",
                  f"CUDA：{manifest['environment']['cuda']}；cuDNN 编译版本：{manifest['environment']['cudnn']}；运行库包：{manifest['environment']['cudnn_package']}。",
                  f"逐帧读取、三模型推理、后处理总耗时 {seconds:.1f} 秒，吞吐 {total_frames / seconds:.1f} FPS（不含模型加载）。", "",
                  "事件级任何一次跌倒判断即算报出；ADL 主动躺下也计入误报。另存 raw_event_summary，防止 80 帧显示保持掩盖模型输出。",
                  "缓存中的 action.score 是官方输出的原始 logit，不是 0～1 概率；分类采用 argmax，没有将其作为置信度阈值。",
                  "帧级只在跌倒段的躺地帧计正样本，排除过渡和缺失标注，属于辅助诊断。",
                  "跟踪、骨骼缓存和显示历史每段重置；不将视频末尾不足 50 帧的缓冲区额外送入模型，以保持官方视频流程。", "",
                  "## 每段结果", "", "| 序列 | 报出/误报 | 首次告警帧 | 延迟（秒） | ADL 告警帧 |", "|---|---|---:|---:|---:|"])
    for row in rows:
        verdict = ("报出" if row["reported"] else "漏报") if row["kind"] == "fall" else ("误报" if row["false_alarm_frames"] else "正常")
        lines.append(f"| {row['sequence']} | {verdict} | {row['first_alarm_frame']} | {row['latency_seconds']} | {row['false_alarm_frames']} |")
    (args.out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(clean_json(summary), ensure_ascii=False, indent=2), flush=True)
    print(f"Report: {args.out_dir / 'report.md'}", flush=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "detect", "evaluate", "all"))
    parser.add_argument("--dataset-dir", type=Path, default=Path(r"D:\智能小车\dataset"))
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".urfall-work")
    parser.add_argument("--archives-dir", type=Path, default=Path(r"D:\智能小车\pphuman_models"))
    parser.add_argument("--models-dir", type=Path, default=Path(r"D:\pphuman-runtime\models"))
    parser.add_argument("--paddle-source", type=Path, default=Path(r"D:\智能小车\third_party\PaddleDetection"))
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".urfall-work/pphuman/predictions")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "demo/results-pphuman")
    parser.add_argument("--device", choices=("GPU", "CPU"), default="GPU")
    parser.add_argument("--sequences", default="", help="Comma-separated sequence names; empty means all")
    parser.add_argument("--force", action="store_true", help="Re-run prediction even for matching caches")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    sequences = ds.prepare_sequences(args.dataset_dir, args.work_dir)
    if args.sequences:
        requested = set(args.sequences.split(","))
        sequences = [seq for seq in sequences if seq.name in requested]
        if {seq.name for seq in sequences} != requested:
            raise ValueError("Some requested sequences are missing")
    ds.fetch_ground_truth(args.work_dir)
    if args.command in ("prepare", "detect", "all"):
        prepare_models(args.archives_dir, args.models_dir)
    if args.command in ("detect", "all"):
        runtime, environment = build_runtime(args)
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        save_json(args.cache_dir / "manifest.json", {"schema": SCHEMA, "environment": environment})
        zips = ds.discover_zips(args.dataset_dir)
        for index, sequence in enumerate(sequences, 1):
            identity = cache_identity(environment, sequence, zips[sequence.name])
            cache_path = args.cache_dir / (sequence.name + ".json")
            if cache_path.is_file() and not args.force:
                previous = json.loads(cache_path.read_text(encoding="utf-8"))
                if previous.get("identity") == identity and [f["frame"] for f in previous["frames"]] == list(sequence.numbers):
                    print(f"[{index}/{len(sequences)}] {sequence.name}: valid cache", flush=True)
                    continue
            print(f"[{index}/{len(sequences)}] {sequence.name}: running", flush=True)
            cache = detect_sequence(sequence, runtime, identity)
            save_json(cache_path, cache)
            print(f"  complete: {cache['seconds']:.2f}s, {len(sequence) / cache['seconds']:.1f} FPS", flush=True)
    if args.command in ("evaluate", "all"):
        evaluate(args, sequences)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
