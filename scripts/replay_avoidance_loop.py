"""Local closed-loop regression diagnostics; no vehicle connection or commands."""
import importlib.util
import math
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'ros2_ws/src/smart_car_decision'))
spec=importlib.util.spec_from_file_location('regression_scene',root/'tests/test_avoidance.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
for obstacles in [((.8,0,.1),),((.8,0,.1),(.5,.65,.25),(.1,.8,.2))]:
    c=m.AvoidanceController();pose=[0.,0.,0.];speed=0.;previous=None
    for i in range(901):
        now=i*.05
        result=m.step(c,m.scene(tuple(pose),obstacles=obstacles,sequence=i//3),now=now,pose=tuple(pose),actual_speed=speed)
        speed=math.hypot(result['linear_x'],result['linear_y'])
        pose[0]+=math.cos(pose[2])*result['linear_x']*.05
        pose[1]+=math.sin(pose[2])*result['linear_x']*.05
        pose[2]+=result['angular_z']*.05
        if c.state!=previous or i%100==0:
            print(round(now,2),c.state,[round(v,3) for v in pose],round(c.distance,3),c.reason,result)
        previous=c.state
        if c.state in {'blocked','timeout','sensor_fault'}:break
