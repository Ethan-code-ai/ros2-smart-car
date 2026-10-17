#!/usr/bin/env python3
"""Two independent YOLO models, one camera, immediate learned-class fall alarms."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo.eval_yolo11_fall import build_runtime, digest, extract, predict_fp32, save
from demo.fall_alarm import ImmediateFallAlarm
from demo.frame_review import FrameReview
from demo.urfall.detect import imwrite_unicode


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='0', help='Camera index, local video or stream URL')
    parser.add_argument('--model', type=Path, default=ROOT / 'ros2_ws/src/smart_car_decision/models/yolo11-fall.pt')
    parser.add_argument('--general-model', type=Path, default=ROOT / 'ros2_ws/src/smart_car_decision/models/yolo11s.pt')
    parser.add_argument('--output-dir', type=Path, help='New output directory; defaults to timestamped demo_output/yolo11-fall')
    parser.add_argument('--headless', action='store_true', help='Process without opening an OpenCV window')
    parser.add_argument('--mute', action='store_true', help='Keep visual/events alarms, disable system sound')
    parser.add_argument('--save-video', action='store_true', help='Save annotated.mp4 with both models\' boxes')
    parser.add_argument('--start-paused', action='store_true', help='Show the first detected frame and wait for Space or N')
    args = parser.parse_args(argv)
    args.batch = 1
    if args.headless and args.start_paused:
        parser.error('--start-paused requires a visible window')
    args.output_dir = args.output_dir or ROOT / 'demo_output/yolo11-fall' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error('Output directory is not empty; choose a new directory to preserve previous results')
    for path in (args.model, args.general_model):
        if not path.is_file():
            parser.error(f'Model file does not exist: {path}')
    return args


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def sound_alarm():
    # SND_ASYNC returns immediately; it never blocks camera capture/inference.
    import winsound
    winsound.PlaySound('SystemHand', winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


def draw(image, fall, general, active, number, now):
    import cv2
    image = image.copy()
    for detections, prefix in ((general, 'general'), (fall, 'fall')):
        for d in detections:
            x1, y1, x2, y2 = map(int, d['bbox'])
            color = (0, 0, 255) if d['class_name'].lower() == 'fallen' and prefix == 'fall' else (
                (255, 180, 0) if prefix == 'general' else (0, 200, 0))
            cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
            text = f"{prefix}: {d['class_name']} {d['score']:.2f}"
            cv2.putText(image, text, (max(0, x1), max(85, y1 - 7)), cv2.FONT_HERSHEY_SIMPLEX, .48, color, 1)
    cv2.rectangle(image, (0, 0), (image.shape[1], 68), (0, 0, 175) if active else (25, 25, 25), -1)
    cv2.putText(image, 'FALL ALARM - IMMEDIATE' if active else 'NO FALL ALARM', (10, 26),
                cv2.FONT_HERSHEY_SIMPLEX, .65, (255, 255, 255), 2)
    cv2.putText(image, f'frame={number} source_time={now:.3f}s | conf=0.25 | no confirmation delay',
                (10, 52), cv2.FONT_HERSHEY_SIMPLEX, .45, (255, 255, 255), 1)
    return image


def run(args):
    import cv2
    import torch
    from ultralytics import YOLO

    fall_model, fall_params, environment = build_runtime(args)
    general_model = YOLO(str(args.general_model)).to('cuda:0')
    general_model.model.float()
    general_params = dict(imgsz=640, conf=.35, iou=.7, rect=False, device='cuda:0', verbose=False)
    environment['general_model'] = dict(path=str(args.general_model.resolve()), sha256=digest(args.general_model),
                                        classes=general_model.names, prediction=general_params)

    source = int(args.source) if args.source.isdecimal() else args.source
    local_file = isinstance(source, str) and Path(source).is_file()
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        cap.release()
        raise RuntimeError(f'Cannot open video/camera: {args.source}')
    fps = cap.get(cv2.CAP_PROP_FPS)
    if local_file and (not math.isfinite(fps) or fps <= 0):
        cap.release()
        raise ValueError('Local video has invalid FPS; source timestamps cannot be determined')
    if not local_file:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    output_fps = fps if math.isfinite(fps) and fps > 0 else 30.
    viewer = None if args.headless else FrameReview(args.start_paused, output_fps)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    save(args.output_dir / 'environment.json', environment)
    policy, writer, number, active_event = ImmediateFallAlarm(.25), None, 0, None
    first_alarm, alarm_frames, alarm_events, max_alarm_dispatch_ms = None, 0, 0, 0.
    started = time.perf_counter()
    events_path, frames_path = args.output_dir / 'events.jsonl', args.output_dir / 'frames.jsonl'
    end_reason = 'source_ended'
    now = 0.

    with events_path.open('w', encoding='utf-8') as events, frames_path.open('w', encoding='utf-8') as frame_log:
        def emit(event):
            events.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n')
            events.flush()
            print(json.dumps(event, ensure_ascii=False, allow_nan=False), flush=True)

        try:
            while True:
                ok, image = cap.read()
                if not ok:
                    if viewer is not None:
                        viewer.wait(ended=True)
                    break
                acquired = time.perf_counter()
                number += 1
                now = (number - 1) / fps if local_file else acquired - started
                fall = extract(predict_fp32(fall_model, image, fall_params)[0])
                signal = policy.update(fall)
                # Dispatch before general inference: its detections cannot gate or delay the decision.
                if signal['started']:
                    alarm_events += 1
                    active_event = f'lying-{number:06d}'
                    evidence = args.output_dir / 'evidence' / f'{active_event}.jpg'
                    if not args.mute:
                        sound_alarm()
                    elapsed_ms = (time.perf_counter() - acquired) * 1000
                    max_alarm_dispatch_ms = max(max_alarm_dispatch_ms, elapsed_ms)
                    first_alarm = now if first_alarm is None else first_alarm
                    alarm_image = draw(image, fall, [], True, number, now)
                    evidence.parent.mkdir(parents=True, exist_ok=True)
                    if not imwrite_unicode(evidence, alarm_image):
                        raise RuntimeError(f'Cannot save evidence: {evidence}')
                    emit(dict(event_type='person_lying', scope='scene', event_id=active_event, state='started',
                              source=args.source, frame=number, source_time=now, wall_time=utc_now(),
                              confirm_seconds=0., detections=signal['fallen'], evidence=str(evidence.resolve()),
                              dispatch_after_frame_read_ms=elapsed_ms, sound_requested=not args.mute))
                if signal['ended']:
                    emit(dict(event_type='person_lying', scope='scene', event_id=active_event, state='ended',
                              frame=number, source_time=now, wall_time=utc_now(), reason='no_fallen_in_current_frame'))
                    active_event = None
                alarm_frames += int(signal['active'])
                general = extract(general_model.predict(image, **general_params)[0])
                frame_log.write(json.dumps(dict(frame=number, source_time=now, alarm=signal['active'],
                                               fall_detections=fall, general_detections=general), allow_nan=False) + '\n')
                frame_log.flush()
                annotated = draw(image, fall, general, signal['active'], number, now)
                if args.save_video:
                    if writer is None:
                        h, w = image.shape[:2]
                        writer = cv2.VideoWriter(str(args.output_dir / 'annotated.mp4'), cv2.VideoWriter_fourcc(*'mp4v'), output_fps, (w, h))
                        if not writer.isOpened():
                            raise RuntimeError('Cannot create annotated video')
                    writer.write(annotated)
                if not args.headless:
                    if not viewer.show(annotated):
                        end_reason = 'user_quit'
                        break
        except BaseException:
            end_reason = 'processing_error'
            raise
        finally:
            if active_event is not None:
                emit(dict(event_type='person_lying', scope='scene', event_id=active_event, state='ended',
                          frame=number, source_time=now, wall_time=utc_now(), reason=end_reason))
            cap.release()
            if writer is not None:
                writer.release()
            if not args.headless:
                cv2.destroyAllWindows()
    if not number:
        raise RuntimeError('Source contained no readable frames')
    for model in (fall_model, general_model):
        if model.predictor.device.type != 'cuda' or next(model.predictor.model.model.parameters()).dtype != torch.float32:
            raise RuntimeError('Actual inference must use CUDA/FP32 for both models')
    decoded = None
    if args.save_video:
        check, decoded = cv2.VideoCapture(str(args.output_dir / 'annotated.mp4')), 0
        while check.read()[0]:
            decoded += 1
        check.release()
        if decoded != number:
            raise RuntimeError('Annotated output frame count differs from input')
    summary = dict(source=args.source, frames=number, fps=fps if local_file else None,
                   alarm_frames=alarm_frames, alarm_events=alarm_events, first_alarm_seconds=first_alarm,
                   confirm_seconds=0., paddle_loaded=False, original_model_retained=True,
                   actual_device='cuda:0', actual_dtype='torch.float32',
                   max_start_dispatch_after_frame_read_ms=max_alarm_dispatch_ms if alarm_events else None,
                   decoded_output_frames=decoded, sound_tested=False,
                   sound_enabled=not args.mute,
                   sound_note='Audible output is not automatically verified; depends on system volume and selected output.',
                   output_dir=str(args.output_dir.resolve()))
    save(args.output_dir / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


if __name__ == '__main__':
    run(parse_args())
