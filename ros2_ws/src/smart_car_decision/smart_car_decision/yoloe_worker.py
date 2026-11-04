"""Spawned GPU worker: latest frames only, local weights, no runtime networking."""
import hashlib
import os
import queue
import socket
import time
from pathlib import Path


def replace_latest(channel, value):
    try:
        channel.put_nowait(value)
        return True
    except queue.Full:
        try:
            channel.get_nowait()
        except queue.Empty:
            pass
        try:
            channel.put_nowait(value)
            return True
        except queue.Full:
            return False


def run(model_path, requests, responses):
    try:
        path = Path(model_path).resolve()
        if path.name != 'yoloe-11s-seg.pt' or not path.is_file():
            raise ValueError('请离线准备 yoloe-11s-seg.pt')
        if not (path.parent/'mobileclip_blt.ts').is_file():
            raise ValueError('请先准备 YOLOE-11 文本编码器 mobileclip_blt.ts')
        os.chdir(path.parent)
        os.environ['YOLO_AUTOINSTALL']='false'
        os.environ['YOLO_OFFLINE']='true'
        def offline(*args, **kwargs):
            raise RuntimeError('运行时禁止下载，请先执行模型准备')
        socket.socket.connect = offline
        import cv2
        import numpy as np
        from ultralytics import YOLOE
        from .safety_events import DetectionTracks
        model = YOLOE(str(path))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        version, tracks = None, DetectionTracks(expiry_sec=2.)
        prompt_cache={}
        while True:
            item = requests.get()
            if item is None:
                return
            if item['version'] != version:
                # Cache the text features in-process; model identity is fixed.
                if item['version'] not in prompt_cache:
                    prompt_cache[item['version']]=model.get_text_pe(item['prompts'])
                    if len(prompt_cache)>8:prompt_cache.pop(next(iter(prompt_cache)))
                model.set_classes(item['prompts'],prompt_cache[item['version']])
                version, tracks = item['version'], DetectionTracks(expiry_sec=2.)
            if time.monotonic()-item['captured_mono'] > .5:
                continue
            started = time.monotonic()
            frame = cv2.imdecode(np.frombuffer(item['jpeg'], np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                raise ValueError('摄像头画面无法解码')
            result = model.predict(frame, imgsz=item['imgsz'], conf=.35, device=0, verbose=False)[0]
            detections = []
            for box in result.boxes:
                label = result.names[int(box.cls[0])]
                coords = [float(v) for v in box.xyxy[0].tolist()]
                detections.append({'type': label, 'label': label, 'confidence': float(box.conf[0]), 'box': coords})
                cv2.rectangle(frame, tuple(map(int,coords[:2])),tuple(map(int,coords[2:])),(0,180,255),2)
                cv2.putText(frame,label,(int(coords[0]),max(20,int(coords[1]))),cv2.FONT_HERSHEY_SIMPLEX,.5,(0,180,255),1)
            detections = tracks.update(detections, time.monotonic())
            ok, annotated = cv2.imencode('.jpg',frame,[cv2.IMWRITE_JPEG_QUALITY,80])
            replace_latest(responses, {**{k:item[k] for k in ('version','run_id','sequence','captured_mono','captured_at')},
                'detections': detections, 'model_sha256':digest, 'inference_ms':(time.monotonic()-started)*1000,
                'image_size':[frame.shape[1],frame.shape[0]], 'annotated':annotated.tobytes() if ok else item['jpeg'],
                'original':item['jpeg']})
    except Exception as exc:
        replace_latest(responses, {'error':str(exc)})
