"""Passive camera-only observation; does not create a mobile acceptance certificate."""
import json
from pathlib import Path
import statistics
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    start=time.monotonic();previous=None;ages=[];durations=[]
    while time.monotonic()-start<20:
        with urllib.request.urlopen('http://192.168.1.106:8080/api/status',timeout=3) as response:value=json.load(response)
        assert value['mode']=='stop' and all(value['cmd_vel'][k]==0 for k in ('linear_x','linear_y','angular_z'))
        v=value.get('vision',{})
        if v.get('updated_at')!=previous and v.get('state')=='running':
            previous=v['updated_at'];ages.append(v['frame_age_ms']);durations.append(v['inference_ms'])
        time.sleep(.1)
    elapsed=time.monotonic()-start
    result={'duration_sec':elapsed,'frames':len(ages),'effective_hz':len(ages)/elapsed,
            'age_p95_ms':sorted(ages)[min(len(ages)-1,int(.95*len(ages)))] if ages else None,
            'inference_median_ms':statistics.median(durations) if durations else None,
            'model_sha256':v.get('model_sha256'),'mobile_accepted':False,'joint_navigation':False,
            'status':value}
    (ROOT/'output/vision-search-20261028/static-vision-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='status'},ensure_ascii=False))
