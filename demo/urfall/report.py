"""Render the evaluation results to markdown, JSON, JSONL and failure frames."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Sequence as TypingSequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.anomaly_rules import AnomalyConfig
from demo.person_anomaly_demo import event_record
from demo.urfall.dataset import FPS, LABEL_LYING, LABEL_TRANSITION
from demo.urfall.metrics import Confusion
from demo.urfall.replay import FALL, SequenceReplay


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if math.isnan(value):
            return "—"
        return f"{value:.{digits}f}"
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _pct(value: float) -> str:
    return "—" if (value is None or math.isnan(value)) else f"{value * 100:.1f}%"


def _table(headers: TypingSequence[str], rows: Iterable[TypingSequence[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def write_events_jsonl(path: Path, replay: SequenceReplay, *, fps: float = FPS) -> None:
    """Serialize fall events, reusing the demo's record contract verbatim.

    ``event_record`` is not modified because tests/test_person_anomaly_demo.py
    asserts its exact key set; sequence/frame context is added alongside.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for event in replay.events:
            if event.event_type != FALL:
                continue
            record = {
                **event_record(
                    event,
                    anchor_wall=0.0,
                    source=f"urfall:{replay.sequence}",
                    evidence_path=None,
                ),
                "sequence": replay.sequence,
                "frame": int(round(event.timestamp * fps)) + 1,
                "t_seconds": round(event.timestamp, 3),
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_sweep_csv(path: Path, rows: TypingSequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_metrics_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_failure_frames(
    out_dir: Path,
    replays: Iterable[SequenceReplay],
    gt: Mapping[tuple[str, int], int],
    frame_paths: Mapping[str, Mapping[int, Path]],
    config: AnomalyConfig,
    *,
    limit: int = 12,
) -> list[Path]:
    """Save a few worst-case frames with the prediction and label burnt in.

    OpenCV is imported here and nowhere else in the package, and only imwrite
    is used -- this harness never opens a window.
    """
    if limit <= 0:
        return []
    import cv2

    from demo.person_anomaly_demo import draw_zone
    from demo.urfall.detect import imread_unicode, imwrite_unicode

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    candidates: list[tuple[str, int, str]] = []
    for replay in replays:
        for signal in replay.signals:
            label = gt.get((replay.sequence, signal.frame))
            if label is None or label == LABEL_TRANSITION:
                continue
            if label == LABEL_LYING and not signal.alarm:
                candidates.append((replay.sequence, signal.frame, "FN"))
            elif label != LABEL_LYING and signal.alarm:
                candidates.append((replay.sequence, signal.frame, "FP"))

    # Round-robin across (sequence, failure kind) buckets, evenly spaced within
    # each, so the sample shows the variety of failures rather than the first N
    # consecutive frames of whichever sequence happens to fail first.
    by_bucket: dict[tuple[str, str], list[tuple[str, int, str]]] = {}
    for sequence, frame, kind in candidates:
        by_bucket.setdefault((sequence, kind), []).append((sequence, frame, kind))

    spaced: dict[tuple[str, str], list[tuple[str, int, str]]] = {}
    per_bucket = max(1, limit // max(1, len(by_bucket)) + 1)
    for bucket, items in by_bucket.items():
        step = max(1, len(items) // per_bucket)
        spaced[bucket] = items[::step]

    picked: list[tuple[str, int, str]] = []
    for index in range(max((len(v) for v in spaced.values()), default=0)):
        for bucket in sorted(spaced):
            if index < len(spaced[bucket]) and len(picked) < limit:
                picked.append(spaced[bucket][index])
        if len(picked) >= limit:
            break

    for sequence, frame, kind in picked:
        source = frame_paths.get(sequence, {}).get(frame)
        if source is None:
            continue
        image = imread_unicode(source)
        if image is None:
            continue
        draw_zone(cv2, image, config.linger_zone, "linger zone", (255, 180, 0))
        colour = (0, 0, 255) if kind == "FN" else (0, 140, 255)
        cv2.putText(
            image,
            f"{sequence} f{frame} {kind}",
            (10, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            colour,
            2,
            cv2.LINE_AA,
        )
        target = out_dir / f"{sequence}-f{frame:04d}-{kind}.jpg"
        if imwrite_unicode(target, image):
            written.append(target)
    return written


def _confusion_row(name: str, confusion: Confusion, extra: TypingSequence[str] = ()) -> list[str]:
    return [
        name,
        str(confusion.tp),
        str(confusion.fp),
        str(confusion.tn),
        str(confusion.fn),
        _pct(confusion.precision),
        _pct(confusion.recall),
        _pct(confusion.specificity),
        _fmt(confusion.f1),
        _pct(confusion.balanced_accuracy),
        *extra,
    ]


_CONFUSION_HEADERS = ("序列", "TP", "FP", "TN", "FN", "精确率", "召回率", "特异度", "F1", "平衡准确率")


def write_markdown(path: Path, payload: Mapping[str, Any]) -> None:
    env = payload["environment"]
    data = payload["dataset"]
    frame = payload["frame_level"]
    overall = Confusion(**{k: frame["overall"][k] for k in ("tp", "fp", "tn", "fn")})
    ceiling = payload["recall_ceiling"]
    perf = payload["performance"]

    parts: list[str] = []
    label = payload.get("label") or env.get("model", "")
    parts.append(f"# 人员倒地检测 — UR Fall 数据集评测报告（{label}）\n" if label
                 else "# 人员倒地检测 — UR Fall 数据集评测报告\n")
    parts.append(
        "本报告由 `demo/eval_urfall.py` 自动生成，评测对象是 `demo/anomaly_rules.py` 中"
        "基于**人员框宽高比 + 持续时间**的倒地规则。\n"
    )

    parts.append("\n## 1. 运行环境\n")
    parts.append(
        _table(
            ("项目", "值"),
            [
                ("GPU", env.get("gpu_name", "—")),
                ("驱动 / CUDA", f"{env.get('driver', '—')} / {env.get('cuda', '—')}"),
                ("torch", env.get("torch", "—")),
                ("torchvision", env.get("torchvision", "—")),
                ("ultralytics", env.get("ultralytics", "—")),
                ("模型", f"`{env.get('model')}` (sha1 `{env.get('model_sha1', '')[:12]}`)"),
                ("跟踪器", f"`{env.get('tracker')}` (sha1 `{env.get('tracker_sha1', '')[:12]}`)"),
                ("推理配置", f"imgsz={env.get('imgsz')}, conf={env.get('yolo_conf')}, batch={env.get('batch')}, half={env.get('half')}"),
                ("缓存摘要", f"`{env.get('key_digest', '')[:16]}`"),
            ],
        )
    )

    parts.append("\n\n## 2. 数据集\n")
    parts.append(
        f"UR Fall Detection Dataset，cam0 RGB，{data['n_sequences']} 段"
        f"（{data['n_fall']} 段跌倒 / {data['n_adl']} 段日常活动），"
        f"共 {data['n_frames']} 帧，30 fps。\n\n"
    )
    parts.append(
        f"标注口径：正样本 = `1`（躺地），负样本 = `-1`（未躺地），"
        f"**排除** `0`（过渡态，{data['n_transition']} 帧）和无标注帧（{data['n_unlabeled']} 帧）。\n\n"
        f"实际可评测 **{data['n_scored']} 帧**，其中正样本 {data['n_positive']} 帧、"
        f"负样本 {data['n_negative']} 帧，正样本占比 **{_pct(data['prevalence'])}**。\n\n"
        f"> 正负样本高度不均衡，「永不报警」的空模型就能拿到 {_pct(1 - data['prevalence'])} 的准确率，"
        f"因此本报告**不以准确率为主指标**，请看 F1、特异度和平衡准确率。\n"
    )
    parts.append("\n")
    parts.append(
        _table(
            ("序列", "帧数", "躺地", "过渡", "未躺地", "无标注"),
            [
                [name, c["frames"], c["lying"], c["transition"], c["not_lying"], c["unlabeled"]]
                for name, c in data["per_sequence"].items()
            ],
        )
    )

    cfg = payload["config"]
    ev = payload.get("event_summary")
    if ev:
        parts.append("\n\n## 3. 事件级指标（主指标）\n")
        parts.append(
            "巡逻场景下每次跌倒**报一次就够**，不需要在人躺着的每一帧都持续报警。"
            "因此主指标是「这次跌倒有没有被报出来、多快、报完之后稳不稳」，"
            "而不是逐帧召回率。\n\n"
        )
        parts.append(
            _table(
                ("指标", "结果"),
                [
                    ["**事件级召回**", f"**{ev['falls_reported']}/{ev['n_falls']} "
                                   f"次跌倒被报出（{_pct(ev['event_recall'])}）**"],
                    ["漏报", f"{len(ev['missed'])} 次" +
                             (f"（{', '.join(ev['missed'])}）" if ev["missed"] else "")],
                    ["告警延迟", f"中位 {_fmt(ev['median_latency_frames'])} 帧 / "
                               f"最大 {_fmt(ev['max_latency_frames'])} 帧"
                               f"（{(ev['max_latency_frames'] or 0) / 30:.2f} 秒）"],
                    ["**误报**", f"**{ev['adl_sequences_with_false_alarm']}/{ev['n_adl_sequences']} 段，"
                               f"共 {ev['false_alarm_frames']} 帧**"
                               f"（{_fmt(ev['adl_minutes'], 1)} 分钟日常活动）"],
                    ["报警后覆盖率", f"**{_pct(ev['pooled_coverage_after_alarm'])}**"
                                 "（告警拉起后还能盖住多少躺地帧）"],
                ],
            )
        )
        parts.append("\n\n### 3.1 帧级漏检的真实构成\n")
        total_fn = ev["pre_alarm_fn"] + ev["dropout_fn"]
        parts.append(
            f"帧级召回率会把去抖窗口算作失败。把 {total_fn} 个漏检帧按后果拆开：\n\n"
        )
        parts.append(
            _table(
                ("类型", "帧数", "占比", "运营影响"),
                [
                    ["报警前延迟", ev["pre_alarm_fn"], _pct(ev["pre_alarm_fn"] / total_fn) if total_fn else "—",
                     "**无** — 去抖+检测延迟，报一次就够"],
                    ["报警后掉线", ev["dropout_fn"], _pct(ev["dropout_fn"] / total_fn) if total_fn else "—",
                     "**真缺陷** — 消费方会以为人已经起来了"],
                ],
            )
        )
        if ev["dropout_fn"] == 0:
            parts.append(
                "\n\n> **报警后掉线 0 帧**：告警一旦拉起，在所有已报出的序列里都 100% 保持到最后。"
                "所以下一节的帧级召回率衡量的几乎全是去抖窗口，**不是检测失败**。"
                "唯一真正的失败是漏报的那次跌倒。\n"
            )
        parts.append("\n\n### 3.2 分序列\n\n")
        parts.append(
            _table(
                ("序列", "躺地帧", "报出", "延迟(帧/秒)", "已覆盖", "报警前漏", "报警后掉线", "报警后覆盖率"),
                [
                    [
                        r["sequence"],
                        r["lying_frames"],
                        "是" if r["reported"] else "**否**",
                        "—" if r["latency_frames"] is None
                        else f"{r['latency_frames']} / {r['latency_seconds']:.2f}s",
                        r["covered"],
                        r["pre_alarm_fn"],
                        r["dropout_fn"],
                        _pct(r["coverage_after_alarm"]),
                    ]
                    for r in payload.get("event_level", []) if r["kind"] == "fall"
                ],
            )
        )

    parts.append("\n\n## 4. 帧级指标（次要，用于诊断）\n")
    parts.append(
        "逐帧统计。注意上面已经说明：帧级召回率把去抖窗口算作漏检，"
        "**不能当作运营意义上的检出率**，它的用途是定位问题而不是对外汇报。\n\n"
    )
    adl_lying = payload.get("adl_lying_frames", 0)
    if adl_lying:
        any_lying = payload.get("frame_level_any_lying", {}).get("overall")
        parts.append(
            f"**计分口径**：URFD 的标注只描述姿态，不区分意图——它的日常活动段里有 "
            f"**{adl_lying} 帧**是人**主动躺在地板上**，标注与真跌倒完全相同。"
            "本表采用 `fall_only` 口径：只有跌倒段里的躺地帧算正样本，"
            "日常活动段里的主动躺下算负样本（在那里报警 = 误报）。\n\n"
        )
        if any_lying:
            parts.append(
                f"> 若改用 `any_lying` 口径（只问「能不能看出有人躺着」，不问是不是跌倒），"
                f"同一批结果是：精确率 {_pct(any_lying['precision'])}、"
                f"召回率 {_pct(any_lying['recall'])}、F1 {_fmt(any_lying['f1'])}。"
                "两者差距就是「摔倒」和「自己躺下」区分不开的代价。\n\n"
            )
    feature_cn = "**躯干角**（肩中点→髋中点与垂直方向夹角，来自 17 个 COCO 关键点）" \
        if payload.get("feature") == "torso_angle" else "**宽高比**（人员外框 宽/高）"
    parts.append(f"倒地判据：{feature_cn}\n\n")
    parts.append(
        f"参数：`{cfg['threshold_name']}={cfg['threshold_value']}`, "
        f"`fall_seconds={cfg['fall_seconds']}`, `confidence={cfg['confidence']}`, "
        f"`track_confidence={cfg.get('track_confidence')}`, "
        f"`cooldown_seconds={cfg['cooldown_seconds']}`, "
        f"`track_lost_grace_seconds={cfg['track_lost_grace_seconds']}`；"
        f"幽灵快照策略 `{payload['ghost_policy']}`。\n\n"
    )
    parts.append(_table(_CONFUSION_HEADERS, [_confusion_row("**合计**", overall)]))
    parts.append("\n\n### 分序列\n\n")
    parts.append(
        _table(
            _CONFUSION_HEADERS,
            [
                _confusion_row(name, Confusion(**{k: c[k] for k in ("tp", "fp", "tn", "fn")}))
                for name, c in frame["per_sequence"].items()
            ],
        )
    )

    detection = payload.get("detection_rate")
    if detection:
        parts.append("\n\n## 4.1 人员检测率（关键诊断）\n")
        parts.append(
            "倒地规则判断的是**人员框的形状**，所以在任何检不到人的帧上它都无从判断。"
            "下表按真实姿态拆分 YOLO11s 的检出率：\n\n"
        )
        parts.append(
            _table(
                ("范围", "未躺地帧检出率", "躺地帧检出率"),
                [
                    [
                        "**合计**",
                        f"{_pct(detection['not_lying_rate'])} "
                        f"({detection['not_lying_detected']}/{detection['not_lying_frames']})",
                        f"**{_pct(detection['lying_rate'])}** "
                        f"({detection['lying_detected']}/{detection['lying_frames']})",
                    ]
                ]
                + [
                    [
                        name,
                        f"{_pct(info['not_lying_rate'])} ({info['not_lying_detected']}/{info['not_lying_frames']})",
                        f"{_pct(info['lying_rate'])} ({info['lying_detected']}/{info['lying_frames']})",
                    ]
                    for name, info in detection["per_sequence"].items()
                ],
            )
        )
        parts.append(
            "\n\n> 人一旦完全躺在地上，YOLO11s 经常**完全检测不到人**。"
            "COCO 预训练的 person 类以站立姿态为主，俯视角度下的卧姿人体属于分布外样本。"
            "**这部分漏检靠调整 `fall_ratio` / `fall_seconds` 无法挽回。**\n"
        )

    runs = payload.get("longest_runs")
    if runs:
        parts.append("\n\n## 4.2 各序列是否「有可能」报警\n")
        parts.append(
            "统计每段中「连续超过 `fall_ratio` 的检出帧」最长有多少"
            "（检测空洞不会清零引擎的计时器，但低于阈值的检出帧会）：\n\n"
        )
        parts.append(
            _table(
                ("序列", "最长连续超阈值帧", "结束于", "去抖所需帧", "是否可能报警"),
                [
                    [
                        name,
                        info["longest_run_frames"],
                        info["ends_at_frame"] or "—",
                        info["needed_frames"],
                        "是" if info["could_fire"] else "**否**",
                    ]
                    for name, info in runs.items()
                ],
            )
        )

    parts.append("\n\n## 5. 召回率的结构性上限\n")
    parts.append(
        f"规则要求横躺姿态**持续 {cfg['fall_seconds']} 秒**（{ceiling['debounce_frames']} 帧）才报警，"
        "所以在计时满足之前的每一个「躺地」帧都是**不可避免的漏检**。"
        "这个上限只由标注和参数决定，与模型无关：\n\n"
    )
    parts.append(
        _table(
            ("序列", "首个过渡帧", "首个躺地帧", "最早可能报警帧", "可达 TP", "结构性漏检"),
            [
                [
                    name,
                    _fmt(info["first_transition_frame"]),
                    _fmt(info["first_lying_frame"]),
                    _fmt(info["earliest_alarm_frame"]),
                    info["reachable_tp"],
                    info["unreachable_fn"],
                ]
                for name, info in ceiling["per_sequence"].items()
            ],
        )
    )
    parts.append(
        f"\n\n**可达召回上限 = {ceiling['reachable_tp']}/{ceiling['total_lying_frames']} = "
        f"{_pct(ceiling['ceiling_recall'])}**，实测召回率 {_pct(overall.recall)}。\n\n"
    )
    if payload.get("ceiling_by_seconds"):
        parts.append("不同 `fall_seconds` 下的上限：\n\n")
        parts.append(
            _table(
                ("fall_seconds", "去抖帧数", "可达召回上限"),
                [
                    [_fmt(row["fall_seconds"], 2), row["debounce_frames"], _pct(row["ceiling_recall"])]
                    for row in payload["ceiling_by_seconds"]
                ],
            )
        )
        parts.append(
            "\n\n> URFD 每段跌倒的过渡态窗口恰好是 30 帧（1.0 秒）。"
            "因此 `fall_seconds` 超过 1.0 秒的部分，是在人已经躺在地上之后才计满时长，"
            "属于**纯粹的召回损失**。\n"
        )

    if payload.get("debounce_adjusted"):
        adjusted = Confusion(**{k: payload["debounce_adjusted"][k] for k in ("tp", "fp", "tn", "fn")})
        parts.append("\n\n## 6. 去抖补偿后的帧级指标（诊断用）\n")
        parts.append(
            "剔除每段中「最早可能报警帧」之前的所有帧后重新计分。"
            "**这是诊断用的上界，不是真实性能**，用于区分「规则延迟」和「规则漏判」。\n\n"
        )
        parts.append(_table(_CONFUSION_HEADERS, [_confusion_row("**合计**", adjusted)]))

    parts.append("\n\n## 7. 序列级指标\n")
    summary = payload["sequence_summary"]
    parts.append(
        f"跌倒段检出 **{summary['fall_sequences_detected']}/{summary['n_fall_sequences']}**，"
        f"日常活动段误报 **{summary['adl_sequences_with_false_alarm']}/{summary['n_adl_sequences']}** 段"
        f"（共 {summary['adl_false_alarm_events']} 次误报事件）。\n\n"
    )
    parts.append(
        _table(
            ("序列", "应报警", "实际报警", "起始事件", "首个躺地帧", "首次报警帧", "延迟(帧)", "延迟(秒)", "track ID 数", "中断次数", "幽灵帧", "最大宽高比", "最大躯干角"),
            [
                [
                    row["sequence"],
                    _fmt(row["expected_fall"]),
                    _fmt(row["alarmed"]),
                    row["n_start_events"],
                    _fmt(row["first_gt_lying_frame"]),
                    _fmt(row["first_alarm_frame"]),
                    _fmt(row["latency_frames"]),
                    _fmt(row["latency_seconds"], 2),
                    row["n_track_ids"],
                    row["n_condition_changed_ends"],
                    row["n_ghost_frames"],
                    _fmt(row["max_ratio_seen"], 2),
                    _fmt(row.get("max_torso_angle_seen"), 1),
                ]
                for row in payload["sequence_level"]
            ],
        )
    )
    parts.append(
        "\n\n> 「中断次数」= 宽高比掉到阈值下导致告警被取消的次数。每次都会触发 "
        f"`cooldown_seconds={cfg['cooldown_seconds']}` 的冷却，"
        "在只有几秒长的片段里，**一个坏帧就可能让后续整段都报不出来**。\n"
        "> 「延迟」为带符号值；人在过渡期就已横躺时为负数是合理的。\n"
    )

    if payload.get("sweep_top"):
        parts.append("\n\n## 8. 阈值扫描\n")
        parts.append(
            f"在缓存的检测结果上网格搜索 `fall_ratio` × `fall_seconds`，共 {payload['sweep_size']} 组。"
            "其余参数保持默认。按 F1 排序的前 10 组：\n\n"
        )
        pname = payload.get("sweep_parameter", "fall_ratio")
        headers = (pname, "fall_seconds", "TP", "FP", "TN", "FN", "精确率", "召回率", "特异度", "F1", "段级召回", "ADL误报事件")
        rows = [
            [
                _fmt(r["value"], 2),
                _fmt(r["fall_seconds"], 2),
                r["tp"],
                r["fp"],
                r["tn"],
                r["fn"],
                _pct(r["precision"]),
                _pct(r["recall"]),
                _pct(r["specificity"]),
                _fmt(r["f1"]),
                _pct(r["sequence_recall"]),
                r["adl_false_alarm_events"],
            ]
            for r in payload["sweep_top"]
        ]
        current = payload.get("sweep_current")
        if current:
            rows.append(["**" + _fmt(current["value"], 2) + "**", "**" + _fmt(current["fall_seconds"], 2) + "**",
                         current["tp"], current["fp"], current["tn"], current["fn"],
                         _pct(current["precision"]), _pct(current["recall"]), _pct(current["specificity"]),
                         "**" + _fmt(current["f1"]) + "**", _pct(current["sequence_recall"]),
                         current["adl_false_alarm_events"]])
        parts.append(_table(headers, rows))
        parts.append("\n\n（最后一行加粗的是**当前配置的参数**。完整网格见 `sweep.csv`。）\n")
        rec = payload.get("sweep_recommended")
        best = payload.get("sweep_best")
        if rec:
            parts.append(
                f"\n**推荐参数（限定日常活动段零误报）**：`{pname}={rec['value']}`、"
                f"`fall_seconds={rec['fall_seconds']}` —— 精确率 {_pct(rec['precision'])}、"
                f"召回率 {_pct(rec['recall'])}、特异度 {_pct(rec['specificity'])}、"
                f"F1 {_fmt(rec['f1'])}，段级检出 {_pct(rec['sequence_recall'])}。\n"
            )
            if best and best.get("adl_false_alarm_events", 0) > 0:
                parts.append(
                    f"\n> F1 绝对值最高的那组（`{pname}={best['value']}`，F1 {_fmt(best['f1'])}）"
                    f"靠放宽阈值换召回，代价是在日常活动段产生 {best['adl_false_alarm_events']} 次误报。"
                    "巡逻机器人误报的代价更高，因此推荐上面这组。\n"
                )

    if payload.get("loso"):
        loso = payload["loso"]
        pooled = Confusion(**{k: loso["pooled"][k] for k in ("tp", "fp", "tn", "fn")})
        parts.append("\n\n### 8.1 留一法交叉验证\n")
        parts.append(
            "上面的扫描是在同一批序列上选参数再报告，属于**拟合而非估计**。"
            "下表每折用 9 段选参、在留出的 1 段上评测，汇总后的数字才是可对外引用的：\n\n"
        )
        parts.append(
            _table(
                ("留出序列", f"选中 {loso.get('parameter', 'fall_ratio')}", "选中 fall_seconds", "TP", "FP", "TN", "FN", "F1"),
                [
                    [
                        f["held_out"],
                        _fmt(f["picked_value"], 2),
                        _fmt(f["picked_fall_seconds"], 2),
                        f["tp"],
                        f["fp"],
                        f["tn"],
                        f["fn"],
                        _fmt(f["f1"]),
                    ]
                    for f in loso["folds"]
                ],
            )
        )
        parts.append(
            f"\n\n**汇总（样本外）**：精确率 {_pct(pooled.precision)}，召回率 {_pct(pooled.recall)}，"
            f"特异度 {_pct(pooled.specificity)}，F1 {_fmt(pooled.f1)}。\n"
        )

    parts.append("\n\n## 9. 性能\n")
    parts.append(
        _table(
            ("设备", "帧数", "预处理 ms", "推理 ms", "后处理 ms", "解码 ms", "纯模型 FPS", "实际吞吐 FPS", "单线程 FPS", "显存峰值 MB"),
            [
                [
                    row["device"],
                    row["frames"],
                    _fmt(row.get("preprocess_ms"), 2),
                    _fmt(row.get("inference_ms"), 2),
                    _fmt(row.get("postprocess_ms"), 2),
                    _fmt(row.get("decode_ms"), 2),
                    _fmt(row.get("model_fps"), 1),
                    _fmt(row.get("wall_fps"), 1),
                    _fmt(row.get("serial_fps"), 1),
                    _fmt(row.get("peak_vram_mb"), 1),
                ]
                for row in perf
            ],
        )
    )
    if len(perf) >= 2:
        gpu = next((r for r in perf if r.get("device") != "cpu"), None)
        cpu = next((r for r in perf if r.get("device") == "cpu"), None)
        if gpu and cpu and gpu.get("inference_ms"):
            speedup = cpu["inference_ms"] / gpu["inference_ms"]
            parts.append(f"\n\nGPU 相对 CPU 的推理加速比：**{speedup:.1f}×**\n")
    parts.append(
        "\n> 「纯模型 FPS」只含 preprocess+inference+postprocess。"
        "「实际吞吐 FPS」是整段跑完的真实墙钟吞吐（PNG 解码在工作线程里与推理重叠）。"
        "「单线程 FPS」是把解码和推理串行相加的假设值——"
        "两者差距说明多线程预取抵消了多少解码开销。\n"
    )

    parts.append("\n\n## 10. 结论与范围说明\n")
    for line in payload.get("conclusions", []):
        parts.append(f"- {line}\n")
    parts.append(
        f"\n**范围限制**：本次只评测了 URFD 70 段中的 {data['n_sequences']} 段，"
        f"单摄像头、每段单人、{data['n_scored']} 个可评测帧、**仅 {data['n_fall']} 个跌倒事件**。"
        f"序列级结果是 n={data['n_fall']}，翻一段就会让段级召回率变动 "
        f"{100 / max(1, data['n_fall']):.0f} 个百分点。"
        "这些数字用于指导调参和暴露规则缺陷是够的，但不足以作为「已验证的检测率」对外宣称。\n"
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(parts), encoding="utf-8")
