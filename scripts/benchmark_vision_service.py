"""Observe an already running joint workload; never starts vehicle movement.

Produces a certificate only after 600 seconds of fresh vision + video + ready
navigation with no controller overrun. Route/motion must be set up separately.
"""
import argparse
import json
import math
from pathlib import Path
import time
import urllib.request


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8080')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seconds',type=float,default=600)
    args=parser.parse_args()
    if args.seconds<600 or not math.isfinite(args.seconds):parser.error('正式验收至少 600 秒')
    ages=[];seq=None;start=time.monotonic();model=None;initial=None;joint=True;last=None
    while time.monotonic()-start<args.seconds:
        with urllib.request.urlopen(args.url+'/api/status',timeout=3) as response:state=json.load(response)
        vision=state.get('vision',{});navigation=state.get('navigation',{});control=state.get('search_control',{})
        if initial is None:initial=control.get('control_overruns')
        joint &= (navigation.get('ready') is True and navigation.get('state') in {'starting','running'}
                  and control.get('control_overruns')==initial and initial is not None)
        if vision.get('state')!='running':joint=False
        if vision.get('updated_at')!=seq:
            age=time.time()-vision.get('updated_at',0)+vision.get('frame_age_ms',9999)/1000
            ages.append(age);seq=vision.get('updated_at')
            if model is not None and model!=vision.get('model_sha256'):joint=False
            model=vision.get('model_sha256')
        with urllib.request.urlopen(args.url+'/api/video?frame=true&raw=true',timeout=3) as response:
            if response.headers.get('Content-Type')!='image/jpeg':joint=False
            response.read()
        last=vision
        time.sleep(.1)
    elapsed=time.monotonic()-start
    ages.sort()
    record={'build_id':'vision-search-0.3.10','model_sha256':model,'duration_sec':elapsed,
            'effective_hz':len(ages)/elapsed,'age_p95_sec':ages[min(len(ages)-1,int(.95*len(ages)))] if ages else 99,
            'control_overruns':0 if joint else 1,'joint_navigation_video':joint,'imgsz':last.get('imgsz') if last else None}
    args.output.write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(record,indent=2))
    if not joint or record['effective_hz']<3 or record['age_p95_sec']>.5:raise SystemExit(1)


if __name__=='__main__':main()
