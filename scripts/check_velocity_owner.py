"""Direct DDS discovery plus HTTP status; never publishes."""
import json
import shlex
import stage_avoidance_release as remote

SOURCE='''
import rclpy,time,json,urllib.request
rclpy.init();n=rclpy.create_node('velocity_owner_readonly_audit')
until=time.monotonic()+5
while time.monotonic()<until:rclpy.spin_once(n,timeout_sec=.1)
owners=[{'name':p.node_name,'namespace':p.node_namespace} for p in n.get_publishers_info_by_topic('/cmd_vel')]
s=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/status',timeout=3))
print(json.dumps({'publishers':owners,'status':{k:s.get(k) for k in ('mode','cmd_vel','odom','avoidance')}}))
n.destroy_node();rclpy.shutdown()
'''
client=remote.connect('192.168.1.106')
try:
    entry=remote.run(client,'cat /home/jetson/ros2-smart-car-avoidance-20261028-66c66971/start_test.sh')
    command='\n'.join([line for line in entry.splitlines() if line.startswith('source ')]+[
        'export ROS_DOMAIN_ID=77','python3 -c '+shlex.quote(SOURCE)])
    result=json.loads(remote.run(client,'bash -c '+shlex.quote(command),timeout=20))
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'output/vision-search-20261028/velocity-owner.json'
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
finally:client.close()
