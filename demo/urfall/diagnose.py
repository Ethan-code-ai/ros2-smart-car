"""Per-frame diagnostic dump for one sequence.

Prints detection presence, track id and width/height ratio against the ground
truth label, which is the fastest way to tell whether a disappointing score
comes from the detector, the tracker or the rule.

    python demo/urfall/diagnose.py fall-01
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.person_anomaly_demo import load_config
from demo.urfall import dataset as ds
from demo.urfall.detect import read_cache

LABEL_TEXT = {ds.LABEL_NOT_LYING: "-", ds.LABEL_TRANSITION: "~", ds.LABEL_LYING: "L"}


def main(argv: list[str]) -> int:
    name = argv[0] if argv else "fall-01"
    work = Path(argv[1]) if len(argv) > 1 else ROOT / ".urfall-work"
    config = load_config(str(ROOT / "demo" / "person_anomaly_config.json"))

    cached = read_cache(work / "detections" / f"{name}.json")
    gt = ds.parse_ground_truth(ds.fetch_ground_truth(work))

    print(f"# {name}  fall_ratio={config.fall_ratio}  confidence={config.confidence}")
    print(f"{'frame':>5} {'gt':>2} {'ids':>12} {'conf':>5} {'ratio':>6}  flag")

    run = 0
    best_run = 0
    best_end = 0
    for item in cached.frames:
        label = gt.get((name, item.frame))
        kept = [d for d in item.detections if d.confidence >= config.confidence]
        ids = ",".join(str(d.track_id) for d in kept) or "-"
        ratio = max((d.width_height_ratio for d in kept), default=0.0)
        conf = max((d.confidence for d in kept), default=0.0)
        over = bool(kept) and ratio >= config.fall_ratio
        if over:
            run += 1
            if run > best_run:
                best_run, best_end = run, item.frame
        elif kept:
            run = 0  # a detected frame below threshold resets the timer
        flag = "FALL" if over else ("" if kept else "no-det")
        print(f"{item.frame:>5} {LABEL_TEXT.get(label, '?'):>2} {ids:>12} {conf:>5.2f} {ratio:>6.2f}  {flag}")

    need = int(round(config.fall_seconds * ds.FPS))
    print()
    print(f"最长连续超阈值（按检出帧计）: {best_run} 帧，结束于第 {best_end} 帧；需要 {need} 帧才会报警")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
