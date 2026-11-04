"""Capture stamped raw scan and hypothetical decisions, without publishing motion."""
import json
import shlex
from pathlib import Path
import stage_avoidance_release as remote

ROOT = Path(__file__).resolve().parents[1]
SOURCE = r'''
import json,math,time,statistics
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from tf2_ros import Buffer,TransformListener
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String
from smart_car_decision.scan_health import ScanHealth
from smart_car_decision.scan_provenance import visibility
from smart_car_decision.avoidance import ScanEvidence,AvoidanceController,ZERO
rclpy.init(); node=Node('avoidance_readonly_capture'); buffer=Buffer(); listener=TransformListener(buffer,node)
scans=[]
health=ScanHealth()
packets={}
def on_scan(msg):
 scans.append(msg)
 age=(node.get_clock().now()-Time.from_msg(msg.header.stamp)).nanoseconds/1e9
 health.observe(msg,age)
def on_packet(msg):
 value=json.loads(msg.data);packets[tuple(value['stamp'])]=value
node.create_subscription(String,'/scan/provenance',on_packet,10)
node.create_subscription(LaserScan,'/scan',on_scan,qos_profile_sensor_data)
deadline=time.monotonic()+3
while time.monotonic()<deadline: rclpy.spin_once(node,timeout_sec=.1)
assert scans,'no scan'
scan=scans[-1]
tf=buffer.lookup_transform('base_link',scan.header.frame_id,Time.from_msg(scan.header.stamp)).transform
q=tf.rotation; yaw=math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))
transform=(tf.translation.x,tf.translation.y,yaw)
e=ScanEvidence(scan,transform)
raw_evidence=e
from pathlib import Path
from smart_car_decision.footprint import Rectangle
from smart_car_decision.avoidance_settings import AvoidanceSettings
profile_path=Path.home()/'.ros/insightrover/avoidance-footprint.json'
profile=json.loads(profile_path.read_text()) if profile_path.exists() else None
footprint=Rectangle(**profile['rectangle']) if profile else None
packet=packets.get((scan.header.stamp.sec,scan.header.stamp.nanosec))
_,summary=visibility(scan,packet,{})
matched=summary.get('source')=='driver_provenance'
if profile:e=ScanEvidence(scan,transform,profile.get('self_returns',()),footprint,
                         .01 if matched and health.status['ready_for_inference'] else 0.,packet['kinds'] if matched else None,
                         assume_missing_clear=bool(matched and packet.get('healthy') is True and health.status['ready_for_inference']))
decisions={}; elapsed=[]
for name,motion in [('forward',{**ZERO,'linear_x':.1}),('left',{**ZERO,'linear_y':.1}),('right',{**ZERO,'linear_y':-.1}),('reverse',{**ZERO,'linear_x':-.1}),('turn',{**ZERO,'angular_z':.1})]:
 c=AvoidanceController(motion_model='holonomic');c.footprint=footprint
 c.scan_health=health.status;c.scan_provenance=summary;c.sampling_gap_inferred=e.sampling_gap_inferred
 c.assumed_clear=e.assumed_clear
 c.missing_data_policy={'mode':'assume_clear','active':e.assumed_clear['rays']>0,'device_proven':False}
 c.settings=AvoidanceSettings(Path.home()/'.ros/insightrover/obstacle-avoidance.json').value
 start=time.perf_counter()
 result=c.step(motion,mode='manual',now=0,evidence=e,scan_age=.1,pose=(0,0,0),odom_age=.1)
 elapsed.append((time.perf_counter()-start)*1000)
 decisions[name]={'output':result,'status':c.status()}
counts={name:0 for name in ['finite_valid','nan','positive_infinity','negative_infinity','out_of_range']}
for v in scan.ranges:
 key=('nan' if math.isnan(v) else 'positive_infinity' if v==math.inf else 'negative_infinity' if v==-math.inf else 'finite_valid' if scan.range_min<=v<=scan.range_max else 'out_of_range')
 counts[key]+=1
result={'stamp':{'sec':scan.header.stamp.sec,'nanosec':scan.header.stamp.nanosec},'frame_id':scan.header.frame_id,
 'angle_min':scan.angle_min,'angle_increment':scan.angle_increment,'range_min':scan.range_min,'range_max':scan.range_max,
 'ranges':[v if math.isfinite(v) else str(v) for v in scan.ranges], 'transform':transform,'counts':counts,
 'near_points':[p for p in e.points if math.hypot(*p)<.4],'decisions':decisions,'evaluation_ms':elapsed,'motion_published':False,
 'self_return_count':e.self_return_count,'measured_rectangle':profile['rectangle'] if profile else None,
 'ray_provenance':packet,'scan_health':health.status}
print(json.dumps(result,ensure_ascii=False,allow_nan=False)); node.destroy_node(); rclpy.shutdown()
'''
client=remote.connect('192.168.1.106')
try:
    entry=remote.run(client,'cat /home/jetson/ros2-smart-car-avoidance-20261028-66c66971/start_test.sh')
    command='\n'.join(['set -e',*[s for s in entry.splitlines() if s.startswith('source ')],
                       'export ROS_DOMAIN_ID=77','python3 -c '+shlex.quote(SOURCE)])
    result=json.loads(remote.run(client,'bash -c '+shlex.quote(command),timeout=30))
    stamp=result['stamp']
    path=ROOT/f"output/active-avoidance-20261028/raw-scan-{stamp['sec']}-{stamp['nanosec']}.json"
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('ranges','near_points','ray_provenance')},ensure_ascii=False,indent=2))
finally:
    client.close()
