"""Read-only driver provenance for current forward and left-front problem sectors."""
import shlex
import stage_avoidance_release as remote

if __name__=='__main__':
    c=remote.connect('192.168.1.106')
    try:
        entry=remote.run(c,'cat /home/jetson/ros2-smart-car-avoidance-20261028-66c66971/start_test.sh')
        code='''import rclpy,json,time
from rclpy.node import Node
from std_msgs.msg import String
rclpy.init();node=Node('provenance_sector_audit');frames=[]
def receive(msg):
 v=json.loads(msg.data);frames.append({'stamp':v['stamp'],'front':v['kinds'][20:42],'left':v['kinds'][220:242]})
node.create_subscription(String,'/scan/provenance',receive,10)
start=time.monotonic()
while len(frames)<12 and time.monotonic()-start<3:rclpy.spin_once(node,timeout_sec=.1)
print(json.dumps(frames));node.destroy_node();rclpy.shutdown()
'''
        command='\n'.join([s for s in entry.splitlines() if s.startswith('source ')]+['export ROS_DOMAIN_ID=77','python3 -c '+shlex.quote(code)])
        print(remote.run(c,'bash -c '+shlex.quote(command),timeout=15))
    finally:c.close()
