"""Read-only runtime hash and final stationary audit, without motion commands."""
import hashlib
import json
from pathlib import Path
import stage_avoidance_release as remote

ROOT=Path(__file__).resolve().parents[1]
TARGET='/home/jetson/ros2-smart-car-avoidance-20261028-66c66971'
files=list((ROOT/'ros2_ws/src/smart_car_decision/smart_car_decision').glob('*.py'))
files += [p for p in (ROOT/'web-console/dist').rglob('*') if p.is_file()]
expected={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
source=f'''
import json,hashlib,urllib.request,subprocess
from pathlib import Path
root=Path({TARGET!r}); expected={expected!r}
actual={{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in expected}}
extra=[root/'start_test.sh',root/'ros2_ws/src/smart_car_decision/config/decision.yaml',
 Path.home()/'.ros/insightrover/avoidance-footprint.json',Path.home()/'.ros/insightrover/vision.json',
 Path('/home/jetson/insightrover-scan-provenance-20261028/install/sllidar_ros2/lib/sllidar_ros2/sllidar_node')]
hashes={{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in extra if p.is_file()}}
s=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/status',timeout=5))
assert s['mode']=='stop' and s['odom']['ok']
assert abs(s['odom'].get('linear_speed_xy',99))<.01 and abs(s['odom']['angular_speed'])<.02
assert all(s['cmd_vel'][k]==0 for k in ('linear_x','linear_y','angular_z'))
unit=subprocess.run(['systemctl','--user','show','smart-car-avoidance-037.service','-p','ActiveState','-p','ExecStart'],capture_output=True,text=True)
print(json.dumps({{'matching':actual==expected,'source_hashes':actual,'runtime_hashes':hashes,
 'service':unit.stdout,'status':{{k:s.get(k) for k in ('mode','odom','cmd_vel','avoidance','vision','search')}}}}))
'''
client=remote.connect('192.168.1.106')
try:
    result=json.loads(remote.python(client,source,timeout=20))
    path=ROOT/'output/vision-search-20261028/final-runtime-audit.json'
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'matching':result['matching'],'service':result['service'],
                      'runtime_hashes':result['runtime_hashes'],
                      'mode':result['status']['mode'],'cmd_vel':result['status']['cmd_vel']}))
finally:client.close()
