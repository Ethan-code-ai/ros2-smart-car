#!/usr/bin/env python3
"""Run the standalone personnel anomaly demonstration."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.anomaly_rules import AnomalyConfig, AnomalyEngine, PersonDetection, TrackSnapshot


def parse_source(value: str) -> int | str:
    try:
        return int(value)
    except ValueError:
        return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone personnel anomaly demo")
    parser.add_argument("--source", default="0", help="camera index or video path")
    parser.add_argument(
        "--model",
        # Pose weights by default: on UR Fall they detect a person lying on the
        # floor 71.7% of the time versus 32.3% for the plain detector, and that
        # recall is what decides whether a fall can be reported at all.
        default=str(ROOT / "ros2_ws" / "src" / "smart_car_decision" / "models" / "yolo11s-pose.pt"),
        help="YOLO model path (倒地检测请用 pose 权重)",
    )
    parser.add_argument(
        "--tracker",
        default=str(ROOT / "ros2_ws" / "src" / "smart_car_decision" / "config" / "bytetrack.yaml"),
        help="tracker configuration path",
    )
    parser.add_argument(
        "--config",
        default=str(Path(__file__).with_name("person_anomaly_config.json")),
        help="anomaly rule configuration JSON",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "demo_output"),
        help="directory for events and evidence screenshots",
    )
    parser.add_argument("--imgsz", type=int, default=640, help="model input size")
    parser.add_argument("--device", default="", help="Ultralytics device, for example 0 or cpu")
    parser.add_argument("--show-fps", action="store_true", help="show processing FPS in the window")
    return parser.parse_args()


def load_config(path: str) -> AnomalyConfig:
    with open(path, "r", encoding="utf-8") as handle:
        return AnomalyConfig.from_mapping(json.load(handle))


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def iso_from_anchor(anchor_wall: float, value: float) -> str:
    timestamp = anchor_wall + value
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone().isoformat(timespec="milliseconds")


def bbox_from_box(box: Any) -> tuple[float, float, float, float]:
    values = box.xyxy[0].tolist()
    return tuple(float(value) for value in values)  # type: ignore[return-value]


def keypoints_from_result(result: Any, index: int) -> tuple[tuple[float, float, float], ...]:
    """Pull one person's COCO keypoints out of a pose result, if present.

    Returns an empty tuple for plain detection models.  Keypoint rows are
    index-aligned with ``result.boxes``.  Models that omit per-point
    confidence are padded to 1.0 so downstream gating still works.
    """
    container = getattr(result, "keypoints", None)
    if container is None:
        return ()
    data = getattr(container, "data", None)
    if data is None or index >= len(data):
        return ()
    points: list[tuple[float, float, float]] = []
    for row in data[index].tolist():
        if len(row) >= 3:
            points.append((float(row[0]), float(row[1]), float(row[2])))
        elif len(row) == 2:
            points.append((float(row[0]), float(row[1]), 1.0))
    return tuple(points)


def extract_detections(result: Any, confidence: float) -> list[PersonDetection]:
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []
    detections: list[PersonDetection] = []
    names = getattr(result, "names", {})
    for index, box in enumerate(boxes):
        class_id = int(box.cls[0])
        if names.get(class_id, str(class_id)) != "person":
            continue
        track_id = getattr(box, "id", None)
        if track_id is None:
            continue
        score = float(box.conf[0])
        if score < confidence:
            continue
        detections.append(
            PersonDetection(
                track_id=int(track_id[0]),
                confidence=score,
                bbox=bbox_from_box(box),
                keypoints=keypoints_from_result(result, index),
            )
        )
    return detections


def draw_zone(cv2: Any, frame: Any, zone: tuple[float, float, float, float], label: str, color: tuple[int, int, int]) -> None:
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = zone
    cv2.rectangle(
        frame,
        (int(x1 * width), int(y1 * height)),
        (int(x2 * width), int(y2 * height)),
        color,
        1,
    )
    cv2.putText(
        frame,
        label,
        (int(x1 * width) + 5, int(y1 * height) + 18),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        color,
        1,
        cv2.LINE_AA,
    )


def snapshot_label(snapshot: TrackSnapshot, config: AnomalyConfig) -> str:
    if snapshot.active_type == "fall_suspected":
        return "FALL"
    if snapshot.active_type == "corridor_blocked":
        return "BLOCKED"
    if snapshot.active_type == "long_stay":
        return "LINGER"
    candidates = snapshot.candidate_durations
    if candidates["fall_suspected"] > 0:
        return f"fall {candidates['fall_suspected']:.1f}/{config.fall_seconds:.1f}s"
    if candidates["corridor_blocked"] > 0:
        return f"block {candidates['corridor_blocked']:.1f}/{config.block_seconds:.1f}s"
    if candidates["long_stay"] > 0:
        return f"stay {candidates['long_stay']:.1f}/{config.linger_seconds:.1f}s"
    return "normal"


def draw_snapshot(cv2: Any, frame: Any, snapshot: TrackSnapshot, config: AnomalyConfig) -> None:
    x1, y1, x2, y2 = (int(value) for value in snapshot.bbox)
    color = {
        "fall_suspected": (0, 0, 255),
        "corridor_blocked": (0, 80, 255),
        "long_stay": (0, 200, 255),
    }.get(snapshot.active_type, (0, 220, 0))
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    cv2.putText(
        frame,
        f"ID {snapshot.track_id} {snapshot_label(snapshot, config)}",
        (x1, max(20, y1 - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        color,
        2,
        cv2.LINE_AA,
    )


def event_record(
    event: Any,
    *,
    anchor_wall: float,
    source: str,
    evidence_path: str | None,
) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "state": event.state,
        "track_id": event.track_id,
        "timestamp": iso_from_anchor(anchor_wall, event.timestamp),
        "started_at": iso_from_anchor(anchor_wall, event.started_at),
        "duration_seconds": round(event.duration_seconds, 3),
        "confidence": round(event.confidence, 3),
        "bbox": [round(value, 2) for value in event.bbox],
        "reason": event.reason,
        "source": source,
        "evidence_path": evidence_path,
    }


def run() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    settings_dir = Path(tempfile.gettempdir()) / "ros2-smart-car-ultralytics"
    settings_dir.mkdir(parents=True, exist_ok=True)
    os.environ["YOLO_CONFIG_DIR"] = str(settings_dir)
    try:
        import cv2
        from ultralytics import YOLO
    except ImportError as exc:
        print(
            "缺少演示依赖，请先运行: python -m pip install -r "
            "ros2_ws/src/smart_car_decision/requirements-vision.txt",
            file=sys.stderr,
        )
        print(f"详细错误: {exc}", file=sys.stderr)
        return 2

    config = load_config(args.config)
    evidence_dir = output_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    events_path = output_dir / "events.jsonl"
    engine = AnomalyEngine(config)
    source = parse_source(args.source)
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        print(f"无法打开视频源: {args.source}", file=sys.stderr)
        return 3

    model = YOLO(args.model)
    anchor_wall = time.time()
    anchor_clock = time.monotonic()
    source_is_file = isinstance(source, str)
    frame_index = 0
    last_clock = time.monotonic()
    print("Demo 已启动，按 q 退出，按 r 清除当前计时和告警。")
    print(f"事件记录: {events_path}")

    try:
        with events_path.open("a", encoding="utf-8") as events_file:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                frame_index += 1
                if source_is_file:
                    source_time = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                    now = source_time if source_time > 0 else frame_index / max(1.0, capture.get(cv2.CAP_PROP_FPS))
                else:
                    now = time.monotonic() - anchor_clock
                model_kwargs: dict[str, Any] = {
                    "source": frame,
                    "imgsz": args.imgsz,
                    "conf": config.confidence,
                    "classes": [0],
                    "tracker": args.tracker,
                    "persist": True,
                    "verbose": False,
                }
                if args.device:
                    model_kwargs["device"] = args.device
                result = model.track(**model_kwargs)[0]
                detections = extract_detections(result, config.confidence)
                frame_events, snapshots = engine.update(
                    detections,
                    frame_width=frame.shape[1],
                    frame_height=frame.shape[0],
                    now=now,
                )

                draw_zone(cv2, frame, config.linger_zone, "linger zone", (255, 180, 0))
                if config.corridor_source == "image_zone":
                    draw_zone(cv2, frame, config.corridor_zone, "corridor", (255, 0, 180))
                for snapshot in snapshots:
                    draw_snapshot(cv2, frame, snapshot, config)

                for event in frame_events:
                    evidence_path: str | None = None
                    if event.state == "started":
                        evidence_file = evidence_dir / f"{event.event_id}.jpg"
                        cv2.imwrite(str(evidence_file), frame)
                        evidence_path = str(evidence_file)
                    record = event_record(
                        event,
                        anchor_wall=anchor_wall,
                        source=str(args.source),
                        evidence_path=evidence_path,
                    )
                    events_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                    events_file.flush()
                    print(f"[{record['state']}] {record['event_type']} ID={record['track_id']}")

                now_wall = time.monotonic()
                fps = 1.0 / max(0.001, now_wall - last_clock)
                last_clock = now_wall
                cv2.putText(
                    frame,
                    "q: quit | r: reset timers",
                    (10, 24),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA,
                )
                if args.show_fps:
                    cv2.putText(
                        frame,
                        f"FPS {fps:.1f}",
                        (10, 48),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        (255, 255, 255),
                        2,
                        cv2.LINE_AA,
                    )
                cv2.imshow("Personnel Anomaly Demo", frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("r"):
                    reset_events = engine.reset(now)
                    for event in reset_events:
                        record = event_record(
                            event,
                            anchor_wall=anchor_wall,
                            source=str(args.source),
                            evidence_path=None,
                        )
                        events_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                        events_file.flush()
                    print("已清除当前计时和告警。")
    finally:
        capture.release()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
