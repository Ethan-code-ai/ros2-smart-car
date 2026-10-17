"""YOLO + ByteTrack inference pass and the on-disk detection cache.

This is the only module that imports torch / ultralytics / OpenCV.  It runs the
model once per sequence and writes every detection to JSON so that the rule
replay and the threshold sweep never need a GPU.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Iterator, Sequence as TypingSequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.anomaly_rules import PersonDetection
from demo.urfall.dataset import FPS, Sequence

CACHE_SCHEMA = 2  # 2 added per-detection keypoints for the pose models


@dataclass(frozen=True)
class CacheKey:
    """Everything that can change a cached detection.

    ``evaluate`` refuses to score sequences whose keys disagree, so a cache can
    never be silently mixed across models, devices or batch sizes.
    """

    model_sha1: str
    tracker_sha1: str
    imgsz: int
    yolo_conf: float
    device: str
    half: bool
    batch: int
    classes: tuple[int, ...]
    ultralytics: str
    torch: str

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["classes"] = list(self.classes)
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "CacheKey":
        payload = dict(payload)
        payload["classes"] = tuple(payload["classes"])
        return cls(**payload)

    def digest(self) -> str:
        canonical = json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FrameDetections:
    frame: int
    now: float
    detections: list[PersonDetection]
    speed_ms: dict[str, float] = field(default_factory=dict)
    decode_ms: float = 0.0


@dataclass(frozen=True)
class CachedSequence:
    sequence: str
    key: CacheKey
    key_digest: str
    frame_width: int
    frame_height: int
    fps: float
    frames: list[FrameDetections]


def file_sha1(path: Path | str) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def configure_ultralytics_home() -> Path:
    """Point Ultralytics at a temp settings dir, as person_anomaly_demo does."""
    settings_dir = Path(tempfile.gettempdir()) / "ros2-smart-car-ultralytics"
    settings_dir.mkdir(parents=True, exist_ok=True)
    os.environ["YOLO_CONFIG_DIR"] = str(settings_dir)
    return settings_dir


def reset_tracker(model: Any) -> None:
    """Clear ByteTrack state between sequences.

    Mirrors ``yolo11_camera_node._reset_tracker`` and additionally resets the
    global track-ID counter so each sequence starts at ID 1 and caches stay
    reproducible.  Track identity is opaque to AnomalyEngine, so renumbering
    cannot change any rule decision.
    """
    predictor = getattr(model, "predictor", None)
    for tracker in getattr(predictor, "trackers", ()) or ():
        tracker.reset()
    try:
        from ultralytics.trackers.basetrack import BaseTrack

        BaseTrack.reset_id()
    except ImportError:  # pragma: no cover - depends on ultralytics internals
        pass


def imread_unicode(path: Path | str, flags: int | None = None) -> Any:
    """Read an image through numpy so non-ASCII paths work on Windows.

    OpenCV's own imread goes through a narrow-charset C API and returns None
    for a path like ``D:\\智能小车\\...``.  Ultralytics monkey-patches cv2.imread
    to work around this, but the scoring path must not depend on ultralytics
    having been imported, so the workaround lives here.
    """
    import cv2
    import numpy as np

    if flags is None:
        flags = cv2.IMREAD_COLOR
    try:
        buffer = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None  # match cv2.imread: a missing/unreadable file yields None
    if buffer.size == 0:
        return None
    return cv2.imdecode(buffer, flags)


def imwrite_unicode(path: Path | str, image: Any) -> bool:
    """Write an image through numpy so non-ASCII paths work on Windows."""
    import cv2

    path = Path(path)
    ok, buffer = cv2.imencode(path.suffix or ".jpg", image)
    if not ok:
        return False
    buffer.tofile(str(path))
    return True


def _decode_stream(
    paths: TypingSequence[Path],
    numbers: TypingSequence[int],
    workers: int,
) -> Iterator[tuple[int, Any, float]]:
    """Decode PNGs on a thread pool, yielding strictly in frame order.

    PNG decode is roughly 40% of the single-threaded per-frame budget on this
    dataset, and cv2.imread releases the GIL, so a bounded look-ahead keeps the
    GPU fed without reordering frames (tracking requires temporal order).
    """
    def read(index: int) -> tuple[int, Any, float]:
        started = time.perf_counter()
        image = imread_unicode(paths[index])
        if image is None:
            raise RuntimeError(f"无法解码图像: {paths[index]}")
        return numbers[index], image, (time.perf_counter() - started) * 1000.0

    if workers <= 1:
        for index in range(len(paths)):
            yield read(index)
        return

    lookahead = max(2, workers * 2)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending: list[Any] = []
        next_index = 0
        for _ in range(min(lookahead, len(paths))):
            pending.append(pool.submit(read, next_index))
            next_index += 1
        while pending:
            future = pending.pop(0)
            if next_index < len(paths):
                pending.append(pool.submit(read, next_index))
                next_index += 1
            yield future.result()


def _chunks(stream: Iterator[Any], size: int) -> Iterator[list[Any]]:
    batch: list[Any] = []
    for item in stream:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def run_sequence(
    model: Any,
    sequence: Sequence,
    *,
    imgsz: int,
    yolo_conf: float,
    tracker_cfg: str,
    device: str,
    batch: int = 1,
    half: bool = False,
    decode_workers: int = 4,
    classes: tuple[int, ...] = (0,),
) -> tuple[list[FrameDetections], int, int]:
    """Track one sequence end to end, returning per-frame detections.

    Detections are kept unfiltered (``extract_detections`` is called with a
    threshold of 0.0); AnomalyEngine applies ``config.confidence`` at replay,
    which makes that threshold sweepable without re-running the model.
    """
    from demo.person_anomaly_demo import extract_detections

    reset_tracker(model)
    collected: list[FrameDetections] = []
    width = height = 0

    stream = _decode_stream(sequence.frames, sequence.numbers, decode_workers)
    for chunk in _chunks(stream, max(1, batch)):
        images = [image for _, image, _ in chunk]
        if height == 0:
            height, width = images[0].shape[:2]
        track_kwargs: dict[str, Any] = {
            "source": images if len(images) > 1 else images[0],
            "imgsz": imgsz,
            "conf": yolo_conf,
            "classes": list(classes),
            "tracker": tracker_cfg,
            "persist": True,
            "verbose": False,
            "device": device,
        }
        if half:
            # Only pass it when requested; recent ultralytics warns once per
            # call that 'half' is deprecated in favour of 'quantize'.
            track_kwargs["half"] = True
        results = model.track(**track_kwargs)
        if len(results) != len(chunk):
            raise RuntimeError(
                f"{sequence.name}: 模型返回 {len(results)} 个结果，但输入了 {len(chunk)} 帧"
            )
        for (number, _, decode_ms), result in zip(chunk, results):
            collected.append(
                FrameDetections(
                    frame=number,
                    now=sequence.time_of(number),
                    detections=extract_detections(result, 0.0),
                    speed_ms={k: float(v) for k, v in (getattr(result, "speed", {}) or {}).items()},
                    decode_ms=decode_ms,
                )
            )
    return collected, width, height


def warmup(model: Any, sequence: Sequence, *, frames: int, **kwargs: Any) -> None:
    """Burn a few frames so cuDNN autotuning does not inflate the first timings."""
    if frames <= 0:
        return
    subset = Sequence(
        name=f"{sequence.name}-warmup",
        kind=sequence.kind,
        frames=sequence.frames[:frames],
        numbers=sequence.numbers[:frames],
    )
    run_sequence(model, subset, **kwargs)
    reset_tracker(model)


def write_cache(
    path: Path,
    key: CacheKey,
    sequence_name: str,
    frames: list[FrameDetections],
    *,
    frame_width: int,
    frame_height: int,
    fps: float = FPS,
) -> None:
    payload = {
        "schema": CACHE_SCHEMA,
        "sequence": sequence_name,
        "key": key.to_json(),
        "key_digest": key.digest(),
        "frame_width": frame_width,
        "frame_height": frame_height,
        "fps": fps,
        "frames": [
            {
                "frame": item.frame,
                "now": round(item.now, 6),
                "dets": [
                    {
                        "id": detection.track_id,
                        "conf": round(detection.confidence, 4),
                        "bbox": [round(value, 2) for value in detection.bbox],
                        **(
                            {
                                "kp": [
                                    [round(x, 1), round(y, 1), round(c, 3)]
                                    for x, y, c in detection.keypoints
                                ]
                            }
                            if detection.keypoints
                            else {}
                        ),
                    }
                    for detection in item.detections
                ],
                "speed": {k: round(v, 3) for k, v in item.speed_ms.items()},
                "decode_ms": round(item.decode_ms, 3),
            }
            for item in frames
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def read_cache(path: Path, expect: CacheKey | None = None) -> CachedSequence:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != CACHE_SCHEMA:
        raise ValueError(f"{path}: 缓存 schema 不是 {CACHE_SCHEMA}")
    key = CacheKey.from_json(payload["key"])
    digest = key.digest()
    if digest != payload.get("key_digest"):
        raise ValueError(f"{path}: 缓存摘要与内容不一致")
    if expect is not None and digest != expect.digest():
        raise ValueError(
            f"{path}: 缓存由不同的推理配置生成（{digest[:8]} != {expect.digest()[:8]}），"
            "请加 --force 重新推理"
        )
    frames = [
        FrameDetections(
            frame=int(item["frame"]),
            now=float(item["now"]),
            detections=[
                PersonDetection(
                    int(det["id"]),
                    float(det["conf"]),
                    (float(det["bbox"][0]), float(det["bbox"][1]), float(det["bbox"][2]), float(det["bbox"][3])),
                    tuple((float(p[0]), float(p[1]), float(p[2])) for p in det.get("kp", ())),
                )
                for det in item["dets"]
            ],
            speed_ms={k: float(v) for k, v in item.get("speed", {}).items()},
            decode_ms=float(item.get("decode_ms", 0.0)),
        )
        for item in payload["frames"]
    ]
    return CachedSequence(
        sequence=payload["sequence"],
        key=key,
        key_digest=digest,
        frame_width=int(payload["frame_width"]),
        frame_height=int(payload["frame_height"]),
        fps=float(payload.get("fps", FPS)),
        frames=frames,
    )


def load_all_caches(cache_dir: Path) -> list[CachedSequence]:
    """Read every cached sequence, failing loudly on a key mismatch."""
    manifest_path = cache_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"没有找到 {manifest_path}，请先运行 `eval_urfall.py detect`"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = CacheKey.from_json(manifest["key"])
    cached: list[CachedSequence] = []
    for name in manifest["sequences"]:
        cached.append(read_cache(cache_dir / f"{name}.json", expected))
    return cached


def write_manifest(cache_dir: Path, key: CacheKey, names: list[str], extra: dict[str, Any]) -> None:
    payload = {
        "schema": CACHE_SCHEMA,
        "key": key.to_json(),
        "key_digest": key.digest(),
        "sequences": names,
        **extra,
    }
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
