#!/usr/bin/env python3
"""Fixed-weight YOLO11 evaluation; immediate fallen alarm is the current default."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import shutil
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo.urfall import dataset as ds
from demo.urfall.metrics import Confusion
from demo.urfall.detect import _chunks, _decode_stream, imread_unicode, imwrite_unicode
from demo.fall_alarm import fallen_detections

SCHEMA = 1


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [clean(v) for v in value]
    return None if isinstance(value, float) and not math.isfinite(value) else value


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def select_failures(rows):
    selected = {}
    for row in rows:
        if row['lying_frames'] and row['first_alarm_frame'] is None:
            selected[row['sequence']] = 'missed_fall' if row['kind'] == 'fall' else 'missed_lying_activity'
        elif not row['lying_frames'] and row['false_alarm_frames']:
            selected[row['sequence']] = 'ordinary_activity_false_alarm'
    return selected


def select_scope(rows, available, scope='paddle-failures'):
    failures = select_failures(rows)
    if scope == 'paddle-failures':
        return failures
    by_name = {row['sequence']: row for row in rows}
    return {name: failures.get(name, 'paddle_reported_lying' if by_name[name]['lying_frames'] else 'paddle_normal_activity_correct')
            for name in sorted(available)}


def compatible_cache(cache, expected):
    return (cache.get('schema') == SCHEMA and cache.get('identity') == clean(expected)
            and [f['frame'] for f in cache.get('frames', [])] == expected['frames'])


def extract(result):
    """Keep all three learned classes; no stock 'person' filter or box-shape fall rule."""
    if result.boxes is None:
        return []
    return [{'class_id': int(row[5]), 'class_name': result.names[int(row[5])],
             'score': float(row[4]), 'bbox': [float(v) for v in row[:4]]}
            for row in result.boxes.data.cpu().tolist()]


def iou(a, b):
    intersection = max(0., min(a[2], b[2]) - max(a[0], b[0])) * max(0., min(a[3], b[3]) - max(a[1], b[1]))
    area = lambda box: max(0., box[2] - box[0]) * max(0., box[3] - box[1])
    return intersection / max(1e-9, area(a) + area(b) - intersection)


class Confirmation:
    """Class-independent IoU association, used ONLY for the optional 2s alarm policy.

    Raw detector metrics include every fallen box, including unmatched/new boxes.
    Missing/negative observations pause evidence; gaps >0.3s reset a candidate.
    Neither duplicated timestamps nor held output can supply positive evidence.
    """
    def __init__(self, seconds=2., gap=.3, release=1.):
        self.seconds, self.gap, self.release = seconds, gap, release
        self.tracks, self.next_id, self.last_time = {}, 1, None

    def update(self, now, detections):
        if self.seconds == 0:
            # One scene alarm: even a new/unmatched box triggers on this frame.
            # No tracking, timestamp accumulation, release hold or EOF padding.
            return [1] if fallen_detections(detections) else []
        if self.last_time is not None and now <= self.last_time:
            return sorted(t for t, s in self.tracks.items() if s['alarm'])
        self.last_time = now
        self.tracks = {t: s for t, s in self.tracks.items() if now - s['seen'] <= self.release + 1e-9}
        matches, used = {}, set()
        pairs = sorted(((iou(s['bbox'], d['bbox']), t, n) for t, s in self.tracks.items()
                        for n, d in enumerate(detections)), reverse=True)
        for overlap, track, n in pairs:
            if overlap < .1:
                break
            if track not in matches and n not in used:
                matches[track], used = n, used | {n}
        for n, d in enumerate(detections):
            if n not in used:
                track = self.next_id
                self.next_id += 1
                self.tracks[track] = dict(bbox=d['bbox'], seen=now, positive=None,
                                          duration=0., previous=False, alarm=False)
                matches[track] = n
        for track, state in self.tracks.items():
            n = matches.get(track)
            d = detections[n] if n is not None else None
            if d is not None and d['class_name'].lower() == 'fallen':
                interval = now - state['seen']
                if state['positive'] is None or now - state['positive'] > self.gap + 1e-9:
                    state['duration'] = 0.
                elif state['previous']:
                    state['duration'] += interval
                state['positive'], state['previous'] = now, True
                state['alarm'] |= state['duration'] + 1e-9 >= self.seconds
            else:
                state['previous'] = False
                if state['positive'] is not None and now - state['positive'] > self.gap + 1e-9:
                    state['duration'] = 0.
                if state['positive'] is not None and now - state['positive'] >= self.release - 1e-9:
                    state['alarm'] = False
            if d is not None:
                state['seen'], state['bbox'] = now, d['bbox']
                d['track_id'] = track
                d['confirmation_seconds'] = state['duration']
        return sorted(t for t, s in self.tracks.items() if s['alarm'])


def replay(cache, seconds=0.):
    policy = Confirmation(seconds)
    result = []
    for frame in cache['frames']:
        # Do not annotate/mutate the cached raw predictions.
        detections = [dict(d) for d in frame['detections']]
        ids = policy.update(frame['now'], detections)
        result.append(dict(frame, detections=detections, raw=bool(fallen_detections(detections)),
                           confirmed=bool(ids), alarm_ids=ids))
    return result


def observation_span(frames, labels, sequence):
    longest, start, previous = 0., None, None
    for frame in frames:
        if labels.get((sequence, frame['frame'])) == 1:
            if previous is None or frame['frame'] != previous + 1:
                start = frame['now']
            longest = max(longest, frame['now'] - start)
            previous = frame['frame']
        else:
            start, previous = None, None
    return longest


def score(sequence, frames, labels, field, seconds=0.):
    lying = [f for f in frames if labels.get((sequence, f['frame'])) == 1]
    positive_hits = [f for f in lying if f[field]]
    signals = [f for f in frames if f[field]]
    first_lying = lying[0]['now'] if lying else None
    confusion = Confusion()
    for frame in frames:
        expected = labels.get((sequence, frame['frame']))
        if expected not in (-1, 1):
            continue
        actual = frame[field]
        confusion += Confusion(tp=int(expected == 1 and actual), fp=int(expected == -1 and actual),
                               tn=int(expected == -1 and not actual), fn=int(expected == 1 and not actual))
    span = observation_span(frames, labels, sequence)
    return dict(sequence=sequence, positive=bool(lying), lying_frames=len(lying),
                reported=bool(positive_hits), any_signal=bool(signals),
                false_alarm=not lying and bool(signals), first_signal_frame=signals[0]['frame'] if signals else None,
                first_positive_frame=positive_hits[0]['frame'] if positive_hits else None,
                latency_seconds=positive_hits[0]['now'] - first_lying if positive_hits else None,
                longest_lying_observation_seconds=span, sufficient_observation=bool(lying) and span + 1e-9 >= seconds,
                premature_only=bool(lying) and bool(signals) and not positive_hits,
                frame_metrics=clean(confusion.to_json()))


def summarize(rows):
    positives = [r for r in rows if r['positive']]
    negatives = [r for r in rows if not r['positive']]
    falls = [r for r in positives if r['sequence'].startswith('fall')]
    activities = [r for r in positives if r['sequence'].startswith('adl')]
    eligible = [r for r in positives if r['sufficient_observation']]
    divide = lambda a, b: a / b if b else None
    confusion = Confusion()
    for r in rows:
        m = r['frame_metrics']
        confusion += Confusion(**{k: m[k] for k in ('tp', 'fp', 'tn', 'fn')})
    latency = [r['latency_seconds'] for r in positives if r['reported']]
    return dict(positive_reported=sum(r['reported'] for r in positives), positive_total=len(positives),
                recall=divide(sum(r['reported'] for r in positives), len(positives)),
                falls_reported=sum(r['reported'] for r in falls), falls_total=len(falls),
                lying_activity_reported=sum(r['reported'] for r in activities), lying_activity_total=len(activities),
                negative_false_alarms=sum(r['false_alarm'] for r in negatives), negative_total=len(negatives),
                sufficient_observation_reported=sum(r['reported'] for r in eligible), sufficient_observation_total=len(eligible),
                insufficient_observation=sum(not r['sufficient_observation'] for r in positives),
                missed=[r['sequence'] for r in positives if not r['reported']],
                false_alarms=[r['sequence'] for r in negatives if r['false_alarm']],
                median_latency_seconds=statistics.median(latency) if latency else None,
                frame_metrics=clean(confusion.to_json()))


def annotate(image, frame, title):
    import cv2
    image = image.copy()
    for d in frame['detections']:
        x1, y1, x2, y2 = map(int, d['bbox'])
        color = (0, 0, 255) if d['class_name'].lower() == 'fallen' else (0, 200, 0)
        cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
        text = f"{d['class_name']} {d['score']:.2f} id={d.get('track_id', '?')} t={d.get('confirmation_seconds', 0):.1f}s"
        cv2.putText(image, text, (max(0, x1), max(75, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, .5, color, 1)
    cv2.rectangle(image, (0, 0), (image.shape[1], 62), (15, 15, 15), -1)
    cv2.putText(image, title, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1)
    cv2.putText(image, f"frame={frame['frame']} time={frame['now']:.2f}s raw={frame['raw']} ALARM={frame['confirmed']}",
                (8, 47), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 80, 255) if frame['confirmed'] else (255, 255, 255), 1)
    return image


def build_runtime(args):
    import torch
    import ultralytics
    from ultralytics import YOLO
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA unavailable; this evaluation requires GPU and never silently falls back to CPU.')
    torch.cuda.set_device(0)
    model = YOLO(str(args.model))
    model.to('cuda:0')
    model.model.float()
    classes = {int(k): str(v).lower() for k, v in model.names.items()}
    if set(classes.values()) != {'fallen', 'sitting', 'standing'} or model.task != 'detect':
        raise ValueError(f'Unexpected model task/classes: {model.task}, {classes}')
    params = dict(imgsz=640, conf=.25, iou=.7, half=False, rect=False, device='cuda:0', verbose=False, max_det=300)
    environment = dict(model=str(args.model.resolve()), model_sha256=digest(args.model), classes={str(k): v for k, v in classes.items()},
                       task=model.task, parameter_count=sum(p.numel() for p in model.model.parameters()),
                       python=platform.python_version(), torch=torch.__version__, ultralytics=ultralytics.__version__,
                       cuda=torch.version.cuda, gpu=torch.cuda.get_device_name(0), batch=args.batch, prediction=params,
                       dtype=str(next(model.model.parameters()).dtype), device=str(next(model.model.parameters()).device))
    if environment['dtype'] != 'torch.float32':
        raise RuntimeError('FP32 verification failed')
    return model, params, environment


def identity(environment, sequence, archive):
    return dict(environment=environment, archive_sha256=digest(archive), sequence=sequence.name,
                frames=list(sequence.numbers), fps=ds.FPS)


def predict_fp32(model, images, params):
    # half=False is the resolved setting recorded in the manifest. This version
    # warns on every call that explicitly supplies even False; the default is
    # False, and the actual CUDA predictor dtype is checked after inference.
    return model.predict(images, **{k: v for k, v in params.items() if k != 'half'})


def detect(args, sequences):
    import torch
    model, params, environment = build_runtime(args)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    run = dict(reused_existing=0, reused_prior_subset=0, inferred_sequences=0, inferred_frames=0)
    save(args.cache_dir / 'manifest.json', dict(schema=SCHEMA, environment=environment, run=run))
    archives = ds.discover_zips(args.dataset_dir)
    first = imread_unicode(sequences[0].frames[0])
    predict_fp32(model, first, params)
    for sequence in sequences:
        path = args.cache_dir / f'{sequence.name}.json'
        expected = identity(environment, sequence, archives[sequence.name])
        if path.exists() and not args.force:
            cached = json.loads(path.read_text(encoding='utf-8'))
            if compatible_cache(cached, expected):
                run['reused_existing'] += 1
                print(f'REUSE {sequence.name}', flush=True)
                continue
        prior = args.reuse_cache_dir / path.name if args.reuse_cache_dir else None
        if prior is not None and prior.is_file() and prior.resolve() != path.resolve() and not args.force:
            cached = json.loads(prior.read_text(encoding='utf-8'))
            if compatible_cache(cached, expected):
                shutil.copyfile(prior, path)
                run['reused_prior_subset'] += 1
                print(f'REUSE PRIOR SUBSET {sequence.name}', flush=True)
                continue
        torch.cuda.synchronize()
        started, frames, width, height = time.perf_counter(), [], 0, 0
        stream = _decode_stream(sequence.frames, sequence.numbers, 4)
        for chunk in _chunks(stream, args.batch):
            images = [image for _, image, _ in chunk]
            height, width = images[0].shape[:2]
            predictions = predict_fp32(model, images, params)
            if len(predictions) != len(chunk):
                raise RuntimeError('Prediction count differs from input frame count')
            for (number, _, _), pred in zip(chunk, predictions):
                frames.append(dict(frame=number, now=sequence.time_of(number), detections=extract(pred)))
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
        if model.predictor.device.type != 'cuda' or next(model.predictor.model.model.parameters()).dtype != torch.float32:
            raise RuntimeError('Actual predictor is not CUDA/FP32')
        save(path, dict(schema=SCHEMA, identity=expected, sequence=sequence.name, frames=frames,
                        width=width, height=height, seconds=elapsed))
        run['inferred_sequences'] += 1
        run['inferred_frames'] += len(frames)
        print(f'DONE {sequence.name}: {len(frames)} frames, {len(frames)/elapsed:.1f} FPS', flush=True)
    save(args.cache_dir / 'manifest.json', dict(schema=SCHEMA, environment=environment, run=run))
    print(f'Inference completion: {run}', flush=True)
    return model, params


def compare_paddle(args, sequences, all_frames, labels, archives):
    from demo.eval_pphuman import cache_identity
    folder = args.work_dir / 'pphuman/predictions'
    environment = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))['environment']
    paddle_rows, union_rows = [], []
    for sequence in sequences:
        cached = json.loads((folder / f'{sequence.name}.json').read_text(encoding='utf-8'))
        if cached['identity'] != cache_identity(environment, sequence, archives[sequence.name]):
            raise ValueError(f'Paddle comparison cache fingerprint mismatch: {sequence.name}')
        if [f['frame'] for f in cached['frames']] != list(sequence.numbers):
            raise ValueError(f'Paddle comparison cache frame mismatch: {sequence.name}')
        frames = [dict(frame, paddle_native=bool(p['alarm']), union_native=frame['raw'] or bool(p['alarm']))
                  for frame, p in zip(all_frames[sequence.name], cached['frames'])]
        paddle_rows.append(score(sequence.name, frames, labels, 'paddle_native', args.confirm_seconds))
        union_rows.append(score(sequence.name, frames, labels, 'union_native', args.confirm_seconds))
    return dict(paddle=summarize(paddle_rows), union=summarize(union_rows), paddle_per_sequence=paddle_rows,
                union_per_sequence=union_rows,
                policy='same labeled-lying overlap scoring; Paddle native 80-frame held alarm OR direct YOLO fallen output; no shared 2s confirmation, not a deployed fusion system')


def evaluate(args, sequences, failures):
    labels = ds.parse_ground_truth(ds.fetch_ground_truth(args.work_dir))
    manifest = json.loads((args.cache_dir / 'manifest.json').read_text(encoding='utf-8'))
    environment = manifest['environment']
    if environment['model_sha256'] != digest(args.model):
        raise ValueError('Model file changed since inference')
    archives = ds.discover_zips(args.dataset_dir)
    caches, raw_rows, confirmed_rows, all_frames = [], [], [], {}
    for sequence in sequences:
        cache = json.loads((args.cache_dir / f'{sequence.name}.json').read_text(encoding='utf-8'))
        if not compatible_cache(cache, identity(environment, sequence, archives[sequence.name])):
            raise ValueError(f'Incomplete/incompatible cache: {sequence.name}')
        caches.append(cache)
        frames = replay(cache, args.confirm_seconds)
        all_frames[sequence.name] = frames
        raw_rows.append(score(sequence.name, frames, labels, 'raw', args.confirm_seconds))
        confirmed_rows.append(score(sequence.name, frames, labels, 'confirmed', args.confirm_seconds))
    metrics = dict(created_utc=datetime.now(timezone.utc).isoformat(), environment=environment,
                   selection=dict(policy='all available local URFD sequences' if args.scope == 'all' else 'PP-Human failed subset: missed lying sequences plus ordinary-activity false alarms',
                                  scope=args.scope,
                                  source=str(args.paddle_metrics) if args.compare_paddle or args.scope != 'all' else None,
                                  source_sha256=digest(args.paddle_metrics) if args.compare_paddle or args.scope != 'all' else None,
                                  failures=failures),
                   dataset=dict(sequences=len(caches), frames=sum(len(c['frames']) for c in caches),
                                complete=set(all_frames) == set(archives)),
                   inference_run=manifest.get('run', {}),
                   evaluation_run=dict(mode='cached_replay', gpu_inference_performed=False,
                                       replayed_sequences=len(caches), replayed_frames=sum(len(c['frames']) for c in caches)),
                   alarm_policy=dict(confirm_seconds=args.confirm_seconds,
                                     mode='immediate' if args.confirm_seconds == 0 else 'temporal_confirmation',
                                     gap_seconds=0. if args.confirm_seconds == 0 else .3,
                                     release_seconds=0. if args.confirm_seconds == 0 else 1.,
                                     association='none; scene alarm on current fallen output' if args.confirm_seconds == 0 else 'greedy class-independent IoU>=0.1'),
                   raw=summarize(raw_rows), confirmed=summarize(confirmed_rows),
                   raw_per_sequence=raw_rows, confirmed_per_sequence=confirmed_rows,
                   performance=dict(seconds=sum(c['seconds'] for c in caches)))
    metrics['performance']['fps'] = metrics['dataset']['frames'] / metrics['performance']['seconds']
    lying_errors = dict(total_lying_frames=0, detected_as_fallen=0, no_detection=0, other_classes_only=0)
    for name, frames in all_frames.items():
        for frame in frames:
            if labels.get((name, frame['frame'])) != 1:
                continue
            lying_errors['total_lying_frames'] += 1
            key = 'detected_as_fallen' if frame['raw'] else ('no_detection' if not frame['detections'] else 'other_classes_only')
            lying_errors[key] += 1
    metrics['lying_frame_diagnostics'] = lying_errors
    if args.scope == 'all' and args.compare_paddle:
        metrics['comparison'] = compare_paddle(args, sequences, all_frames, labels, archives)
        prior_names = set(select_failures(json.loads(args.paddle_metrics.read_text(encoding='utf-8'))['per_sequence']))
        metrics['subgroups'] = {name: dict(raw=summarize([r for r in raw_rows if (r['sequence'] in prior_names) == prior]),
                                          confirmed=summarize([r for r in confirmed_rows if (r['sequence'] in prior_names) == prior]))
                               for name, prior in [('prior_paddle_failures', True), ('remaining', False)]}
        p_by_name = {r['sequence']: r for r in metrics['comparison']['paddle_per_sequence']}
        metrics['comparison']['yolo_missed_despite_paddle_hit'] = [r['sequence'] for r in raw_rows
                                                                 if r['positive'] and not r['reported'] and p_by_name[r['sequence']]['reported']]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    evidence = args.out_dir / 'evidence'
    rows = []
    for sequence, raw, confirmed in zip(sequences, raw_rows, confirmed_rows):
        row = dict(sequence=sequence.name, selection_reason=failures[sequence.name],
                   lying_frames=raw['lying_frames'], raw_reported=raw['reported'],
                   raw_false_alarm=raw['false_alarm'], raw_first_frame=raw['first_signal_frame'],
                   raw_latency_seconds=raw['latency_seconds'], confirmed_reported=confirmed['reported'],
                   confirmed_false_alarm=confirmed['false_alarm'], confirmed_first_frame=confirmed['first_signal_frame'],
                   confirmed_latency_seconds=confirmed['latency_seconds'], sufficient_observation=raw['sufficient_observation'])
        rows.append(row)
        frames = all_frames[sequence.name]
        targets = []
        if raw['positive']:
            number = raw['first_positive_frame'] or next(f['frame'] for f in frames if labels.get((sequence.name, f['frame'])) == 1)
            targets.append((number, 'lying-detected' if raw['reported'] else 'lying-missed'))
        else:
            number = raw['first_signal_frame'] or max(frames, key=lambda f: max((d['score'] for d in f['detections']), default=0.))['frame']
            normal_verdict = 'normal-correct' if args.scope == 'all' else 'false-alarm-corrected'
            targets.append((number, 'false-alarm' if raw['false_alarm'] else normal_verdict))
        if confirmed['first_positive_frame']:
            targets.append((confirmed['first_positive_frame'], 'alarm-immediate' if args.confirm_seconds == 0 else f'alarm-{args.confirm_seconds:g}s'))
        for number, verdict in targets:
            index = sequence.numbers.index(number)
            image = annotate(imread_unicode(sequence.frames[index]), frames[index], f'{sequence.name} {verdict}')
            evidence.mkdir(parents=True, exist_ok=True)
            path = evidence / f'{sequence.name}-{verdict}-f{number:03d}.jpg'
            if not imwrite_unicode(path, image):
                raise RuntimeError(f'Cannot save evidence {path}')
        events = []
        previous = set()
        for frame in frames:
            current = set(frame['alarm_ids'])
            for state, tracks in [('started', current - previous), ('ended', previous - current)]:
                events.extend(dict(event_type='person_lying', state=state,
                                   **({'scope': 'scene', 'scene_id': t} if args.confirm_seconds == 0 else {'track_id': t}), frame=frame['frame'],
                                   source_time=frame['now'], reason='state_changed') for t in sorted(tracks))
            previous = current
        events.extend(dict(event_type='person_lying', state='ended',
                           **({'scope': 'scene', 'scene_id': t} if args.confirm_seconds == 0 else {'track_id': t}), frame=frames[-1]['frame'],
                           source_time=frames[-1]['now'], reason='source_ended') for t in sorted(previous))
        event_path = args.out_dir / 'events' / f'{sequence.name}.jsonl'
        event_path.parent.mkdir(parents=True, exist_ok=True)
        event_path.write_text(''.join(json.dumps(e) + '\n' for e in events), encoding='utf-8')
    with (args.out_dir / 'sequences.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    if args.compare_paddle:
        baseline = json.loads((ROOT / 'demo/results-urfall/metrics.json').read_text(encoding='utf-8'))
        old_rows = {r['sequence']: r for r in baseline['event_level']}
        metrics['original_native_subset'] = dict(
            positive_any_alarm=sum(old_rows[r['sequence']]['first_alarm_frame'] is not None for r in raw_rows if r['positive']),
            negative_any_alarm=sum(old_rows[r['sequence']]['first_alarm_frame'] is not None for r in raw_rows if not r['positive']),
            policy='original bbox rule, 0.5s confirmation; historical any alarm, not strict lying overlap')
    save(args.out_dir / 'metrics.json', metrics)
    write_report(args, metrics, rows)
    print(json.dumps(clean(dict(dataset=metrics['dataset'], raw=metrics['raw'], confirmed=metrics['confirmed'])), ensure_ascii=False, indent=2), flush=True)
    return metrics, rows


def write_report(args, metrics, rows):
    raw, alarm = metrics['raw'], metrics['confirmed']
    immediate = args.confirm_seconds == 0
    alarm_label = '即时告警' if immediate else f'确认{args.confirm_seconds:g}秒告警'
    percentage = lambda value: '—' if value is None else f'{value:.1%}'
    full_scope = metrics['selection'].get('scope') == 'all'
    title = '完整本地数据集评测' if metrics['dataset'].get('complete') else ('选定数据评测' if full_scope else '飞桨失败子集评测')
    warning = ('权重训练集与URFD的重叠未知，结果不能代表小车实机或独立泛化性能。'
               if full_scope else '**样本按飞桨失败结果筛选，因此不能用此处比例估计总体准确率、总体提升或泛化性能。**')
    lines = [f'# YOLO11 跌倒专用权重：{title}', '',
             f"共 {metrics['dataset']['sequences']} 段 / {metrics['dataset']['frames']} 帧；完整性：{metrics['dataset'].get('complete', False)}。", '',
             warning, '',
             '本轮仅重放既有预测缓存，未重新执行数据集GPU推理。下方GPU环境和吞吐来自原缓存生成过程。', '',
             '固定设置：640×640、conf=0.25、IoU=0.7、FP32、CUDA；保留全部三类输出，无训练、无阈值扫描、无宽高比倒地规则。', '',
             '所有带躺卧标注的片段均为正样本，包括主动躺下。事件命中要求信号至少覆盖一个已标注躺卧帧，避免提前误报虚增召回。', '',
             f'当前策略：{alarm_label}；' + ('单帧 fallen 达到0.25即报警，不使用飞桨、不要求跟踪ID、不等待2秒。' if immediate else '保留时序确认，仅供历史对照。'), '',
             f'| 指标 | 直接 fallen 输出 | {alarm_label} |', '|---|---:|---:|',
             f"| 躺卧片段命中 | {raw['positive_reported']}/{raw['positive_total']} | {alarm['positive_reported']}/{alarm['positive_total']} |",
             f"| 真实跌倒片段命中 | {raw['falls_reported']}/{raw['falls_total']} | {alarm['falls_reported']}/{alarm['falls_total']} |",
             f"| 主动躺卧片段命中 | {raw['lying_activity_reported']}/{raw['lying_activity_total']} | {alarm['lying_activity_reported']}/{alarm['lying_activity_total']} |",
             f"| 普通活动误报 | {raw['negative_false_alarms']}/{raw['negative_total']} | {alarm['negative_false_alarms']}/{alarm['negative_total']} |",
             f"| 躺卧帧召回率 | {percentage(raw['frame_metrics']['recall'])} | {percentage(alarm['frame_metrics']['recall'])} |",
             f"| 帧级精确率 | {percentage(raw['frame_metrics']['precision'])} | {percentage(alarm['frame_metrics']['precision'])} |", '',
             (f"即时模式没有最短观察时间，全部{raw['positive_total']}段正样本均纳入实际报出统计。" if immediate else
              f"连续躺卧观察达到{args.confirm_seconds:g}秒：{alarm['sufficient_observation_total']}；不足：{alarm['insufficient_observation']}。"),
             ('直接输出与告警逐帧一致；没有循环、冻结或延长短片，EOF 不补帧。事件按场景开始/结束记录，不将场景编号当作人员跟踪ID。' if immediate else
              '没有循环、冻结或延长短片；EOF 不推进计时。确认层采用全部三类框的独立 IoU 关联，短暂缺测暂停计时。'), '']
    if 'original_native_subset' in metrics:
        lines += [
             '## 原方案与飞桨对照', '',
             ('完整覆盖此前飞桨成功和失败的片段，已解除只看失败子集的选择偏差。' if full_scope else
              f"飞桨在选中的{raw['positive_total']}段躺卧片段均未告警，并在选中的{raw['negative_total']}段普通活动产生误报；这是子集的选取条件。"),
             f"原宽高比方案在所选数据的历史告警：正样本 {metrics['original_native_subset']['positive_any_alarm']}/{raw['positive_total']}，普通活动 {metrics['original_native_subset']['negative_any_alarm']}/{raw['negative_total']}。",
             '原方案使用0.5秒规则，飞桨包含80帧显示保持。它们与新模型直接输出、2秒确认属于不同策略；历史“曾报警”不保证对齐躺卧标注，不能混成严格同口径准确率。',
             ('历史全集业务口径仅供背景：原方案29/46、普通活动误报1/23；飞桨28/46、误报3/23。下方另按相同躺卧标注重算对照。' if full_scope else
              '历史全集业务口径仅供背景：原方案29/46、普通活动误报1/23；飞桨28/46、误报3/23。本轮没有运行或推断YOLO11的全集结果。'), '']
    if 'comparison' in metrics:
        comparison = metrics['comparison']
        p, u = comparison['paddle'], comparison['union']
        lines += ['| 同一标注对齐口径 | 飞桨原告警状态 | YOLO直接输出 | 两者OR覆盖 |', '|---|---:|---:|---:|',
                  f"| 躺卧片段 | {p['positive_reported']}/{p['positive_total']} | {raw['positive_reported']}/{raw['positive_total']} | {u['positive_reported']}/{u['positive_total']} |",
                  f"| 真实跌倒片段 | {p['falls_reported']}/{p['falls_total']} | {raw['falls_reported']}/{raw['falls_total']} | {u['falls_reported']}/{u['falls_total']} |",
                  f"| 普通活动误报 | {p['negative_false_alarms']}/{p['negative_total']} | {raw['negative_false_alarms']}/{raw['negative_total']} | {u['negative_false_alarms']}/{u['negative_total']} |", '',
                  'OR覆盖是已有缓存的离线对照：飞桨原告警含80帧显示保持，YOLO使用直接输出，没有共同的2秒确认，也没有部署双模型系统。',
                  f"飞桨能覆盖而YOLO漏掉的躺卧片段：{', '.join(comparison['yolo_missed_despite_paddle_hit']) or '无'}。", '',
                  '## 本次续跑', '', f"缓存复用及新增统计：{metrics['inference_run']}。", '',
                  '| 分组 | 躺卧片段命中 | 真实跌倒命中 | 普通活动误报 |', '|---|---:|---:|---:|']
        for name, group in metrics['subgroups'].items():
            s = group['raw']
            lines.append(f"| {name} | {s['positive_reported']}/{s['positive_total']} | {s['falls_reported']}/{s['falls_total']} | {s['negative_false_alarms']}/{s['negative_total']} |")
        lines.append('')
    lines += ['## 逐段结果', '', f'| 片段 | 选取原因 | 直接命中/误报 | {alarm_label}命中/误报 | 观察时间符合策略 |', '|---|---|---|---|---|']
    for row in rows:
        positive = row['lying_frames'] > 0
        verdict = lambda hit: ('命中' if hit else '漏报') if positive else ('误报' if hit else '无误报')
        lines.append(f"| {row['sequence']} | {row['selection_reason']} | {verdict(row['raw_reported'] if positive else row['raw_false_alarm'])} | {verdict(row['confirmed_reported'] if positive else row['confirmed_false_alarm'])} | {row['sufficient_observation']} |")
    diagnostics = metrics['lying_frame_diagnostics']
    lines += ['', '## 结果解读', '',
              f"片段中至少命中一次不等于持续可靠识别：{diagnostics['total_lying_frames']} 个已标注躺卧帧中，{diagnostics['detected_as_fallen']} 帧输出 fallen，{diagnostics['no_detection']} 帧没有任何检测框，{diagnostics['other_classes_only']} 帧仅输出其他姿态。",
              (f"本次即时告警覆盖{alarm['positive_reported']}/{alarm['positive_total']}段躺卧，普通活动误报{alarm['negative_false_alarms']}/{alarm['negative_total']}。取消确认时间不会改善模型本身的误分类。" if immediate else
               f"直接命中{raw['positive_reported']}/{raw['positive_total']}段，{alarm_label}命中{alarm['positive_reported']}/{alarm['positive_total']}段。短片、持续检测和身份关联均影响报警。"),
              '', '## 环境与限制', '',
              f"GPU：{metrics['environment']['gpu']}；{metrics['environment']['torch']}；{metrics['environment']['ultralytics']}；实际设备/类型：{metrics['environment']['device']} / {metrics['environment']['dtype']}。",
              f"批量推理、读图与输出转CPU合计 {metrics['performance']['seconds']:.2f} 秒，吞吐 {metrics['performance']['fps']:.1f} FPS，不含加载、暖机、截图与视频编码；不是小车实时帧率。",
              '该社区权重训练集划分和与URFD的重叠情况不明确，不能宣称独立泛化成绩。',
              '证据在 evidence/，每段至少一张；两段本地视频仅为无逐帧人工标注的演示检查，不纳入准确率分母。', '', '## 复现', '',
              f'在仓库根目录使用 .venv-demo/Scripts/python.exe demo/eval_yolo11_fall.py evaluate --scope {args.scope} --confirm-seconds {args.confirm_seconds:g} --out-dir "{args.out_dir}"' + (' --compare-paddle' if args.compare_paddle else '') + '。',
              'evaluate 只重放缓存和生成报告；videos --video-replay-dir 指向已保存预测目录时不运行推理。默认不读取飞桨缓存或权重。']
    video_summary = args.out_dir / 'videos/summary.json'
    if video_summary.is_file():
        lines += ['', '## 本地视频检查', '', f'| 视频 | 帧数 | fallen帧 | {alarm_label}帧 | 首次告警（秒） |', '|---|---:|---:|---:|---:|']
        for row in json.loads(video_summary.read_text(encoding='utf-8')):
            lines.append(f"| {Path(row['source']).name} | {row['frames']} | {row['raw_frames']} | {row['confirmed_frames']} | {row['first_alarm_seconds']} |")
        lines += ['', '两段视频均已重新解码检查输出帧数；它们没有逐帧人工标注，不计入准确率。']
    (args.out_dir / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def video_samples(path, out, frames):
    import cv2
    first = next((f for f in frames if f['raw']), None)
    samples = [(first or frames[len(frames)//2], out.with_suffix('.jpg'))]
    alarm = next((f for f in frames if f['confirmed']), None)
    if alarm:
        samples.append((alarm, out.with_name(out.stem + '-alarm.jpg')))
    cap = cv2.VideoCapture(str(path))
    try:
        for sample, target in samples:
            cap.set(cv2.CAP_PROP_POS_FRAMES, sample['frame'] - 1)
            ok, image = cap.read()
            if not ok or not imwrite_unicode(target, annotate(image, sample, 'Local demo sample | no manual ground truth')):
                raise RuntimeError('Video evidence failed')
    finally:
        cap.release()


def videos(args, model=None, params=None):
    import cv2
    if model is None and args.video_replay_dir is None:
        model, params, _ = build_runtime(args)
    summaries = []
    for name in ('正常', '倒地'):
        path = ROOT.parent / f'{name}.mp4'
        if not path.is_file():
            raise FileNotFoundError(path)
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise RuntimeError(f'Cannot open {path}')
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError(f'Invalid FPS: {path}')
        cached = None
        if args.video_replay_dir is not None:
            cached = json.loads((args.video_replay_dir / f'{name}-predictions.json').read_text(encoding='utf-8'))
            if cached['sha256'] != digest(path) or abs(cached['fps'] - fps) > 1e-6:
                raise ValueError(f'Video cache source mismatch: {path}')
            provenance = cached.get('model_sha256')
            if provenance is None:
                parent_metrics = args.video_replay_dir.parent / 'metrics.json'
                provenance = json.loads(parent_metrics.read_text(encoding='utf-8'))['environment']['model_sha256']
            if provenance != digest(args.model):
                raise ValueError('Video cache model differs from the selected checkpoint')
        out = args.out_dir / 'videos' / f'{name}-yolo11-fall.mp4'
        out.parent.mkdir(parents=True, exist_ok=True)
        width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*'mp4v'), fps, (width, height))
        if not writer.isOpened():
            raise RuntimeError(f'Cannot encode {out}')
        policy, frames = Confirmation(args.confirm_seconds), []
        try:
            while True:
                ok, image = cap.read()
                if not ok:
                    break
                number = len(frames) + 1
                now = (number - 1) / fps
                if cached is None:
                    detections = extract(predict_fp32(model, image, params)[0])
                else:
                    source = cached['frames'][number - 1]
                    if source['frame'] != number or abs(source['now'] - now) > 1e-6:
                        raise ValueError('Video cache frame/time mismatch')
                    detections = [{k: d[k] for k in ('class_id', 'class_name', 'score', 'bbox')} for d in source['detections']]
                ids = policy.update(now, detections)
                frame = dict(frame=number, now=now, detections=detections,
                             raw=bool(fallen_detections(detections)), confirmed=bool(ids), alarm_ids=ids)
                frames.append(frame)
                writer.write(annotate(image, frame, 'YOLO11 Human Fall Detection | demo video'))
        finally:
            cap.release()
            writer.release()
        if not frames:
            raise ValueError(f'No video frames: {path}')
        if cached is not None and len(frames) != len(cached['frames']):
            raise ValueError('Video cache length mismatch')
        # Reopen the encoded result and decode all frames to verify it is playable.
        check, decoded = cv2.VideoCapture(str(out)), 0
        while check.read()[0]:
            decoded += 1
        check.release()
        if decoded != len(frames):
            raise RuntimeError(f'Encoded frame count mismatch: {out}')
        save(args.out_dir / 'videos' / f'{name}-predictions.json', dict(source=str(path), sha256=digest(path), fps=fps, frames=frames,
             confirm_seconds=args.confirm_seconds, prediction_source='cached replay' if cached is not None else 'GPU inference',
             model_sha256=digest(args.model), replay_directory=str(args.video_replay_dir) if cached is not None else None))
        first = next((f for f in frames if f['raw']), None)
        video_samples(path, out, frames)
        summary = dict(source=str(path), output=str(out), frames=len(frames), fps=fps, raw_frames=sum(f['raw'] for f in frames),
                       confirmed_frames=sum(f['confirmed'] for f in frames), first_raw_seconds=first['now'] if first else None,
                       first_alarm_seconds=next((f['now'] for f in frames if f['confirmed']), None), decoded_output_frames=decoded,
                       accuracy=None, note='Unannotated demonstration, excluded from dataset accuracy')
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    save(args.out_dir / 'videos' / 'summary.json', summaries)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('all', 'detect', 'evaluate', 'videos'))
    parser.add_argument('--model', type=Path, default=ROOT / 'ros2_ws/src/smart_car_decision/models/yolo11-fall.pt')
    parser.add_argument('--dataset-dir', type=Path, default=ROOT.parent / 'dataset')
    parser.add_argument('--work-dir', type=Path, default=ROOT / '.urfall-work')
    parser.add_argument('--paddle-metrics', type=Path, default=ROOT / 'demo/results-pphuman/metrics.json')
    parser.add_argument('--scope', choices=('paddle-failures', 'all'), default='all')
    parser.add_argument('--compare-paddle', action='store_true', help='Optional historical cached comparison; never loads Paddle models')
    parser.add_argument('--cache-dir', type=Path, default=None)
    parser.add_argument('--out-dir', type=Path, default=None)
    parser.add_argument('--reuse-cache-dir', type=Path, default=None)
    parser.add_argument('--skip-videos', action='store_true', help='Skip already checked local demo videos')
    parser.add_argument('--batch', type=int, default=8)
    parser.add_argument('--confirm-seconds', type=float, default=0., help='Default immediate; positive values only for historical comparisons')
    parser.add_argument('--video-replay-dir', type=Path, help='Reuse raw local-video predictions without GPU inference')
    parser.add_argument('--sequences', default='', help='Optional comma-separated subset of the selected scope')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args(argv)
    suffix = 'all' if args.scope == 'all' else 'paddle-failures'
    args.cache_dir = args.cache_dir or ROOT / f'.urfall-work/yolo11-fall-{suffix}'
    policy_suffix = 'immediate' if args.confirm_seconds == 0 else f'confirm-{args.confirm_seconds:g}s'
    args.out_dir = args.out_dir or ROOT / f'demo/results-yolo11-fall-{suffix}-{policy_suffix}'
    if args.scope == 'all' and args.reuse_cache_dir is None:
        args.reuse_cache_dir = ROOT / '.urfall-work/yolo11-fall-paddle-failures'
    if args.batch < 1 or not math.isfinite(args.confirm_seconds) or args.confirm_seconds < 0:
        parser.error('batch must be positive and confirmation time non-negative')
    if args.command == 'videos':
        videos(args)
        return
    archives = ds.discover_zips(args.dataset_dir)
    if args.scope == 'paddle-failures' or args.compare_paddle:
        paddle = json.loads(args.paddle_metrics.read_text(encoding='utf-8'))
        failures = select_scope(paddle['per_sequence'], archives, args.scope)
    else:
        failures = {name: 'all_local_sequences' for name in sorted(archives)}
    if args.sequences:
        requested = set(args.sequences.split(','))
        if not requested <= failures.keys():
            raise ValueError('Requested sequences must belong to the selected scope')
        failures = {k: failures[k] for k in sorted(requested)}
    # Enumerate/examine only selected extracted sequences: never call prepare_sequences(all).
    sequences = [ds.load_sequence(ds.extract_sequence(archives[name], args.work_dir), name) for name in sorted(failures)]
    if not sequences:
        raise ValueError('No sequences selected')
    args.out_dir.mkdir(parents=True, exist_ok=True)
    save(args.out_dir / 'selection.json', dict(source=str(args.paddle_metrics) if args.compare_paddle or args.scope != 'all' else None,
                                            sha256=digest(args.paddle_metrics) if args.compare_paddle or args.scope != 'all' else None, scope=args.scope, failures=failures,
                                            sequences=len(sequences), frames=sum(len(s) for s in sequences)))
    print(f"Selected {len(sequences)} sequences, {sum(len(s) for s in sequences)} frames; scope={args.scope}", flush=True)
    model = params = None
    if args.command in ('all', 'detect'):
        model, params = detect(args, sequences)
    evaluation = None
    if args.command in ('all', 'evaluate'):
        evaluation = evaluate(args, sequences, failures)
    if args.command == 'all' and not args.skip_videos:
        videos(args, model, params)
        write_report(args, *evaluation)


if __name__ == '__main__':
    main()
