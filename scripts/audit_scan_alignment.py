"""Compare live TF safety coordinates with the established full-circle cloud."""
import ast,json,shlex
from pathlib import Path
import stage_avoidance_release as remote

ROOT=Path(__file__).resolve().parents[1]
tree=ast.parse((ROOT/'scripts/capture_avoidance_scan.py').read_text(encoding='utf-8'))
source=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
            and any(isinstance(t,ast.Name) and t.id=='SOURCE' for t in n.targets))
audit=r'''
from rcl_interfaces.srv import GetParameters
from smart_car_decision.scan_visualization import simplify_scan_points
pc=node.create_client(GetParameters,'/system_status_node/get_parameters')
assert pc.wait_for_service(timeout_sec=2),'system_status_node parameter service unavailable'
request=GetParameters.Request();request.names=['front_center_deg']
future=pc.call_async(request)
rclpy.spin_until_future_complete(node,future,timeout_sec=2)
assert future.done() and future.result(),'cloud front parameter unavailable'
v=future.result().values[0]
front_deg=v.double_value if v.type==3 else float(v.integer_value)
cloud=simplify_scan_points(scan,max_points=len(scan.ranges),rotation_rad=math.radians(front_deg))
assert len(cloud)==len(raw_evidence.points)
errors=[math.hypot(x-transform[0]-p['x'],y-transform[1]-p['y']) for (x,y),p in zip(raw_evidence.points,cloud)]
wrapped=math.atan2(math.sin(yaw+math.radians(front_deg)),math.cos(yaw+math.radians(front_deg)))
result['alignment']={'cloud_front_deg':front_deg,'tf_yaw_deg':math.degrees(yaw),
 'rotation_error_deg':math.degrees(wrapped),'translation_m':list(transform[:2]),
 'point_count':len(errors),'max_point_error_after_origin_correction_m':max(errors,default=None),
 'same_orientation':abs(wrapped)<.001,'same_points_within_cloud_rounding':max(errors,default=1)<.00072,
 'mapping_reference':'same /scan and TF tree; no map accuracy claim'}
try:
 mt=buffer.lookup_transform('map',scan.header.frame_id,Time.from_msg(scan.header.stamp)).transform
 bt=buffer.lookup_transform('base_link','map',Time.from_msg(scan.header.stamp)).transform
 def planar(t):
  q=t.rotation;return t.translation.x,t.translation.y,math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))
 mx,my,ma=planar(mt); bx,by,ba=planar(bt)
 result['alignment']['map_chain_error_m']=math.hypot(bx+math.cos(ba)*mx-math.sin(ba)*my-transform[0],by+math.sin(ba)*mx+math.cos(ba)*my-transform[1])
except Exception as exc:result['alignment']['map_chain_unavailable']=type(exc).__name__
'''
source=source.replace('print(json.dumps(result,ensure_ascii=False,allow_nan=False));',audit+'\nprint(json.dumps(result,ensure_ascii=False,allow_nan=False));')
client=remote.connect('192.168.1.106')
try:
    entry=remote.run(client,'cat /home/jetson/ros2-smart-car-avoidance-20261028-66c66971/start_test.sh')
    command='\n'.join(['set -e',*[s for s in entry.splitlines() if s.startswith('source ')],
                       'export ROS_DOMAIN_ID=77','python3 -c '+shlex.quote(source)])
    data=json.loads(remote.run(client,'bash -c '+shlex.quote(command),timeout=30))
    path=ROOT/'output/active-avoidance-20261028-v037/alignment-audit.json'
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(data['alignment'],ensure_ascii=False,indent=2))
finally:client.close()
