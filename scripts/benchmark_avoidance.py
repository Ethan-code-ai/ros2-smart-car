"""Vehicle CPU benchmark only: computes commands, never publishes them."""
import json
from pathlib import Path
import stage_avoidance_release as remote

SOURCE = '''import importlib.util,time,math,json
from types import SimpleNamespace as N
p='/home/jetson/ros2-smart-car-avoidance-20261028-66c66971/ros2_ws/src/smart_car_decision/smart_car_decision/avoidance.py'
s=importlib.util.spec_from_file_location('avoidance',p);a=importlib.util.module_from_spec(s);s.loader.exec_module(a)
import sys
from pathlib import Path
sys.path.insert(0,str(Path(p).parent))
from footprint import Rectangle
profile=json.loads((Path.home()/'.ros/insightrover/avoidance-footprint.json').read_text())
scan=N(angle_min=-math.pi,angle_increment=math.tau/1080,range_min=.05,range_max=12,ranges=[5.]*1080,header=N(stamp=N(sec=1,nanosec=0)))
e=a.ScanEvidence(scan);times=[]
for i in range(30):
 c=a.AvoidanceController();c.footprint=Rectangle(**profile['rectangle']); t=time.perf_counter()
 out=c.step({**a.ZERO,'linear_x':.25},mode='manual',now=0,evidence=e,scan_age=.1,pose=(0,0,0),odom_age=.1)
 times.append((time.perf_counter()-t)*1000)
print(json.dumps({'kind':'synthetic_clear_scan_on_vehicle_no_motion','count':len(times),'min_ms':min(times),'max_ms':max(times),'mean_ms':sum(times)/len(times),'output':out}))
'''
client=remote.connect('192.168.1.106')
try:
    result=remote.python(client,SOURCE,timeout=30)
    path=Path(__file__).resolve().parents[1]/'output/active-avoidance-20261028-v037/timing-final.json'
    path.write_text(result,encoding='utf-8');print(result)
finally:client.close()
