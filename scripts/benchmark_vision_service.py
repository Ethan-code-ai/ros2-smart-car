"""Observe an already running joint workload; never starts vehicle movement.

Produces a certificate only after 600 seconds of fresh vision + video + ready
navigation with no controller overrun. Route/motion must be set up separately.
"""
import argparse
import json
import math
import os
from pathlib import Path
import time
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8080')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seconds',type=float,default=600)
    args=parser.parse_args()
    if args.seconds<600 or not math.isfinite(args.seconds):parser.error('正式验收至少 600 秒')
    token = os.environ.get('ROBOT_SERVICE_TOKEN', '')
    if len(token) < 32:
        parser.error('请配置车端机器凭据 ROBOT_SERVICE_TOKEN')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def open_vehicle(path):
        request = urllib.request.Request(args.url.rstrip('/') + path,
                                         headers={'X-InsightRover-Service-Token': token})
        return opener.open(request, timeout=3)

    ages=[];seq=None;start=time.monotonic();model=None;initial=None;joint=True;last=None
    identity = None
    while time.monotonic()-start<args.seconds:
        with open_vehicle('/api/status') as response:state=json.load(response)
        vision=state.get('vision',{});navigation=state.get('navigation',{});control=state.get('search_control',{})
        sample_identity = (vision.get('build_id'), state.get('component_manifest', {}).get('source_sha256'),
                           vision.get('imgsz'), vision.get('model_sha256'))
        if identity is None:
            identity = sample_identity
        joint &= all(sample_identity) and sample_identity == identity
        if initial is None:initial=control.get('control_overruns')
        joint &= (navigation.get('ready') is True and navigation.get('state') in {'starting','running'}
                  and control.get('control_overruns')==initial and initial is not None)
        if vision.get('state')!='running':joint=False
        if vision.get('updated_at')!=seq:
            age=time.time()-vision.get('updated_at',0)+vision.get('frame_age_ms',9999)/1000
            ages.append(age);seq=vision.get('updated_at')
            if model is not None and model!=vision.get('model_sha256'):joint=False
            model=vision.get('model_sha256')
        with open_vehicle('/api/video?frame=true&raw=true') as response:
            if response.headers.get('Content-Type')!='image/jpeg':joint=False
            response.read()
        last=vision
        time.sleep(.1)
    elapsed=time.monotonic()-start
    ages.sort()
    record={'build_id':identity[0], 'source_sha256':identity[1], 'model_sha256':model,'duration_sec':elapsed,
            'effective_hz':len(ages)/elapsed,'age_p95_sec':ages[min(len(ages)-1,int(.95*len(ages)))] if ages else 99,
            'control_overruns':0 if joint else 1,'joint_navigation_video':joint,'imgsz':last.get('imgsz') if last else None}
    args.output.write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(record,indent=2))
    if not joint or record['effective_hz']<3 or record['age_p95_sec']>.5:raise SystemExit(1)


if __name__=='__main__':main()
