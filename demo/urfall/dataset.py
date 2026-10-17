"""Dataset preparation for the UR Fall Detection Dataset (URFD).

Handles the three things the evaluation needs before any model runs: the RGB
frames (shipped as one zip per sequence), the official per-frame ground truth
(fetched from the dataset homepage), and the mapping between them.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
from typing import Iterable, Mapping
import urllib.request
import zipfile


FALLS_URL = "http://fenix.ur.edu.pl/~mkepski/ds/data/urfall-cam0-falls.csv"
ADLS_URL = "http://fenix.ur.edu.pl/~mkepski/ds/data/urfall-cam0-adls.csv"
GROUND_TRUTH_URLS = (FALLS_URL, ADLS_URL)

FPS = 30.0

# Ground truth labels in column 2 of the CSVs.
LABEL_NOT_LYING = -1
LABEL_TRANSITION = 0
LABEL_LYING = 1

_SENTINEL_NAME = ".extracted.json"
_SENTINEL_SCHEMA = 1
_FRAME_RE = re.compile(r"-(\d+)\.png$", re.IGNORECASE)
_ZIP_RE = re.compile(r"^((fall|adl)-\d+)-cam0-rgb$", re.IGNORECASE)


@dataclass(frozen=True)
class Sequence:
    """One URFD sequence: ordered frames plus their 1-indexed frame numbers."""

    name: str
    kind: str
    frames: tuple[Path, ...]
    numbers: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.frames) != len(self.numbers):
            raise ValueError(f"{self.name}: frames and numbers must be parallel")

    def __len__(self) -> int:
        return len(self.frames)

    def time_of(self, frame_number: int) -> float:
        """Seconds from the start of the sequence for a 1-indexed frame."""
        return (frame_number - 1) / FPS


def sequence_kind(name: str) -> str:
    return "fall" if name.startswith("fall") else "adl"


def frame_number(path: Path | str) -> int:
    """Parse the trailing frame index out of ``<seq>-cam0-rgb-001.png``."""
    match = _FRAME_RE.search(str(path))
    if match is None:
        raise ValueError(f"cannot parse a frame number from {path}")
    return int(match.group(1))


def discover_zips(dataset_dir: Path) -> dict[str, Path]:
    """Map sequence name -> zip path for every URFD cam0 RGB zip on disk."""
    found: dict[str, Path] = {}
    for path in sorted(dataset_dir.glob("*.zip")):
        match = _ZIP_RE.match(path.stem)
        if match is None:
            continue
        found[match.group(1).lower()] = path
    return found


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Reject absolute paths and traversal before writing anything to disk."""
    members: list[zipfile.ZipInfo] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in Path(name).parts or Path(name).is_absolute():
            raise ValueError(f"unsafe zip member: {info.filename}")
        if not name.lower().endswith(".png"):
            continue
        members.append(info)
    return members


def _sentinel_payload(zip_path: Path, member_count: int) -> dict[str, object]:
    stat = zip_path.stat()
    return {
        "schema": _SENTINEL_SCHEMA,
        "zip_size": stat.st_size,
        "zip_mtime_ns": stat.st_mtime_ns,
        "member_count": member_count,
    }


def extract_sequence(zip_path: Path, work_dir: Path, *, force: bool = False) -> Path:
    """Extract one zip into ``work_dir/frames/<stem>``; a no-op when current.

    Extraction goes to a temporary sibling directory that is swapped in only on
    success, so an interrupted run can never leave a half-extracted directory
    that the sentinel would later bless.
    """
    target = work_dir / "frames" / zip_path.stem
    sentinel = target / _SENTINEL_NAME

    with zipfile.ZipFile(zip_path) as archive:
        members = _safe_members(archive)
        expected = _sentinel_payload(zip_path, len(members))

        if not force and sentinel.is_file():
            try:
                if json.loads(sentinel.read_text(encoding="utf-8")) == expected:
                    return target
            except (json.JSONDecodeError, OSError):
                pass

        staging = target.with_name(target.name + ".tmp")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        for info in members:
            name = Path(info.filename.replace("\\", "/")).name
            with archive.open(info) as source, (staging / name).open("wb") as handle:
                shutil.copyfileobj(source, handle)

    (staging / _SENTINEL_NAME).write_text(json.dumps(expected), encoding="utf-8")
    if target.exists():
        shutil.rmtree(target)
    os.replace(staging, target)
    return target


def load_sequence(seq_dir: Path, name: str) -> Sequence:
    """Build a Sequence from an extracted directory, ordered numerically."""
    pairs = sorted(
        ((frame_number(path), path) for path in seq_dir.glob("*.png")),
        key=lambda item: item[0],
    )
    if not pairs:
        raise ValueError(f"{name}: no PNG frames under {seq_dir}")
    return Sequence(
        name=name,
        kind=sequence_kind(name),
        frames=tuple(path for _, path in pairs),
        numbers=tuple(number for number, _ in pairs),
    )


def prepare_sequences(dataset_dir: Path, work_dir: Path, *, force: bool = False) -> list[Sequence]:
    """Extract every zip found in ``dataset_dir`` and load the frame lists."""
    zips = discover_zips(dataset_dir)
    if not zips:
        raise FileNotFoundError(f"没有在 {dataset_dir} 找到 URFD 的 cam0 RGB 压缩包")
    sequences: list[Sequence] = []
    for name in sorted(zips):
        seq_dir = extract_sequence(zips[name], work_dir, force=force)
        sequences.append(load_sequence(seq_dir, name))
    return sequences


def fetch_ground_truth(work_dir: Path, *, refresh: bool = False, timeout: float = 120.0) -> list[Path]:
    """Download (and cache) the official per-frame label CSVs."""
    out_dir = work_dir / "groundtruth"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for url in GROUND_TRUTH_URLS:
        target = out_dir / url.rsplit("/", 1)[-1]
        if refresh or not target.is_file() or target.stat().st_size == 0:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                payload = response.read()
            if not payload.strip():
                raise ValueError(f"ground truth download was empty: {url}")
            target.write_bytes(payload)
        paths.append(target)
    return paths


def parse_ground_truth(paths: Iterable[Path | str]) -> dict[tuple[str, int], int]:
    """Parse the label CSVs into ``{(sequence, frame): label}``.

    The files are headerless; only columns 0 (sequence), 1 (frame number) and
    2 (label) are used.  The remaining columns are depth-derived features that
    this evaluation does not consume.
    """
    labels: dict[tuple[str, int], int] = {}
    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            fields = line.split(",")
            if len(fields) < 3:
                raise ValueError(f"{path}:{lineno}: expected at least 3 columns")
            label = int(fields[2])
            if label not in (LABEL_NOT_LYING, LABEL_TRANSITION, LABEL_LYING):
                raise ValueError(f"{path}:{lineno}: unexpected label {label}")
            labels[(fields[0].strip(), int(fields[1]))] = label
    if not labels:
        raise ValueError("ground truth parsed to zero rows")
    return labels


def ground_truth_summary(
    labels: Mapping[tuple[str, int], int],
    sequences: Iterable[Sequence],
) -> dict[str, dict[str, int]]:
    """Per-sequence label counts, restricted to sequences present on disk."""
    summary: dict[str, dict[str, int]] = {}
    for sequence in sequences:
        counts = {"frames": len(sequence), "lying": 0, "transition": 0, "not_lying": 0, "unlabeled": 0}
        for number in sequence.numbers:
            label = labels.get((sequence.name, number))
            if label is None:
                counts["unlabeled"] += 1
            elif label == LABEL_LYING:
                counts["lying"] += 1
            elif label == LABEL_TRANSITION:
                counts["transition"] += 1
            else:
                counts["not_lying"] += 1
        summary[sequence.name] = counts
    return summary
