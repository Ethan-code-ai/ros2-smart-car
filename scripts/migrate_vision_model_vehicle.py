"""Prepare YOLOE traffic/person vocabulary, then switch parked config and body fingerprint."""
from pathlib import Path
import shlex
import stage_avoidance_release as remote

ROOT=Path(__file__).resolve().parents[1]
RAM='/dev/shm/insightrover-vision-20261028'
if __name__=='__main__':
    c=remote.connect('192.168.1.106')
    try:
        source=f'''import os,json,time,urllib.request
os.chdir({str(RAM+'/models')!r})
from ultralytics import YOLOE,YOLO
import numpy as np,cv2
model=YOLOE('yoloe-11s-seg.pt')
model.set_classes(['person','stop sign','traffic light','car','truck','bus','motorcycle','bicycle'])
model.save('yoloe-11s-traffic.pt')
del model
model=YOLO('yoloe-11s-traffic.pt')
image=cv2.imdecode(np.frombuffer(urllib.request.urlopen('http://127.0.0.1:8080/api/video?frame=true&raw=true').read(),np.uint8),cv2.IMREAD_COLOR)
for _ in range(3):
 result=model.track(image,device=0,verbose=False,tracker='bytetrack.yaml',persist=True)[0]
print(json.dumps({{'class':type(model).__name__,'names':model.names,'tracking_api_ok':True,'detections':len(result.boxes)}}))
'''
        print(remote.run(c,f'PYTHONPATH={RAM}/packages TMPDIR={RAM}/tmp YOLO_CONFIG_DIR={RAM}/settings YOLO_AUTOINSTALL=false python3 -c '+shlex.quote(source),timeout=120))
        remote.python(c,"import json,urllib.request\ns=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/status'));assert s['mode']=='stop' and all(s['cmd_vel'][k]==0 for k in ['linear_x','linear_y','angular_z'])")
        remote.run(c,'systemctl --user stop smart-car-avoidance-037.service',timeout=30)
        for dest in ('/home/jetson/.ros/insightrover/avoidance-footprint.json',
                     '/home/jetson/ros2-smart-car-avoidance-20261028-66c66971/ros2_ws/src/smart_car_decision/config/decision.yaml'):
            remote.python(c,f"from pathlib import Path\nimport shutil,hashlib\np=Path({dest!r});b=p.with_name(p.name+'.backup-'+hashlib.sha256(p.read_bytes()).hexdigest()[:12])\nif not b.exists():shutil.copy2(p,b)")
        with c.open_sftp() as s:s.put(str(ROOT/'output/vision-search-20261028/refined-footprint.json'),'/home/jetson/.ros/insightrover/avoidance-footprint.json')
        print(remote.python(c,f'''from pathlib import Path
import yaml
p=Path('/home/jetson/ros2-smart-car-avoidance-20261028-66c66971/ros2_ws/src/smart_car_decision/config/decision.yaml')
v=yaml.safe_load(p.read_text());v['yolo11_camera_node']['ros__parameters'].update(model_path={str(RAM+'/models/yoloe-11s-traffic.pt')!r},inference_rate_hz=5.)
p.write_text(yaml.safe_dump(v,allow_unicode=True,sort_keys=False))
p=Path('/home/jetson/ros2-smart-car-avoidance-20261028-66c66971/start_test.sh')
s=p.read_text();needle='export PYTHONPATH="{RAM}/packages:'
assert needle in s
s=s.replace(needle,'export YOLO_CONFIG_DIR="{RAM}/settings"\\n'+needle);p.write_text(s)
print('YOLOE model selected; interior-only measured fingerprint refined')
'''))
        helper=(ROOT/'desktop/insightrover_desktop/vehicle_start.sh').read_text()
        print(remote.run(c,'bash -c '+shlex.quote(helper),timeout=45))
    finally:c.close()
