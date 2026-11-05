"""Start the provisioned v4 service in stop mode and record stationary evidence.

No leases or motion commands are issued. Reads the dedicated commissioning vault.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import sys
import time

import httpx
import stage_avoidance_release as remote

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = r'''
import json,math,time
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from geometry_msgs.msg import Twist
from sensor_msgs.msg import LaserScan
from nav_msgs.msg import Odometry
from tf2_ros import Buffer,TransformListener
rclpy.init();node=Node("v4_stationary_preflight")
buffer=Buffer();listener=TransformListener(buffer,node)
samples={"cmd_vel":[],"odom":[],"scan":[]}
def age(stamp):
 return (node.get_clock().now()-Time.from_msg(stamp)).nanoseconds/1e9
def velocity(msg):
 samples["cmd_vel"].append({"t":time.time(),"x":msg.linear.x,"y":msg.linear.y,"z":msg.angular.z})
def odom(msg):
 p=msg.pose.pose.position;t=msg.twist.twist
 samples["odom"].append({"t":time.time(),"age":age(msg.header.stamp),"x":p.x,"y":p.y,
 "linear":math.hypot(t.linear.x,t.linear.y),"angular":abs(t.angular.z)})
def scan(msg):
 finite=[v for v in msg.ranges if math.isfinite(v) and msg.range_min<=v<=msg.range_max]
 samples["scan"].append({"t":time.time(),"age":age(msg.header.stamp),"frame":msg.header.frame_id,
 "rays":len(msg.ranges),"finite":len(finite)})
node.create_subscription(Twist,"/cmd_vel",velocity,10)
node.create_subscription(Odometry,"/odom",odom,qos_profile_sensor_data)
node.create_subscription(LaserScan,"/scan",scan,qos_profile_sensor_data)
deadline=time.monotonic()+6
while time.monotonic()<deadline:rclpy.spin_once(node,timeout_sec=.05)
publishers=[{"name":i.node_name,"namespace":i.node_namespace} for i in node.get_publishers_info_by_topic("/cmd_vel")]
try:
 scan_frame=samples["scan"][-1]["frame"]
 tf=buffer.lookup_transform("base_link",scan_frame,Time()).transform
 transform={"ok":True,"x":tf.translation.x,"y":tf.translation.y}
except Exception as exc:transform={"ok":False,"error":type(exc).__name__}
result={"samples":samples,"publishers":publishers,"scan_tf":transform}
node.destroy_node();rclpy.shutdown()
print("PREFLIGHT_JSON="+json.dumps(result))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true", help="Start the stopped v4 service if inactive")
    args = parser.parse_args()
    output = ROOT / "output/v4-vehicle-preflight"
    config = json.loads((output / "provisioned-v4.json").read_text())
    sys.path.insert(0, str(ROOT / "backend"))
    from app.services.windows_credentials import read_credentials
    token = read_credentials(config["credential_profile"])["robot_token"]
    ssh = remote.connect(config["host"])
    started = False
    result = {"at": datetime.now(timezone.utc).isoformat(), "host": config["host"], "motion_requested": False}
    api = httpx.Client(base_url="http://" + config["host"] + ":8080", timeout=5, trust_env=False,
                       headers={"X-InsightRover-Service-Token": token})
    try:
        state = remote.run(ssh, "systemctl --user is-active smart-car-v4.service || true").strip()
        if state != "active":
            if not args.start:
                raise RuntimeError("Service inactive; pass --start for stationary startup")
            remote.python(ssh, '''
import subprocess
assert not subprocess.run(["pgrep","-f","/(web_app_node|decision_controller)( |$)"],text=True,capture_output=True).stdout.strip(),"A control stack is already active"
assert not subprocess.run(["pgrep","-f","^python3 /home/jetson/Rosmaster/rosmaster/rosmaster_main.py$"],text=True,capture_output=True).stdout.strip(),"Vendor controller owns the chassis; do not start another driver"
subprocess.run(["systemctl","--user","start","smart-car-v4.service"],check=True)
print("Started v4 in configured stop mode")
''')
            started = True
        result["service"] = remote.run(ssh, "systemctl --user show smart-car-v4.service -p ActiveState -p MainPID -p ExecStart")
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                response = api.get("/api/status", timeout=1)
                response.raise_for_status()
                result["initial_status"] = response.json()
                break
            except httpx.HTTPError:
                time.sleep(1)
        if "initial_status" not in result:
            raise RuntimeError("v4 status unavailable after stationary startup")
        assert result["initial_status"]["mode"] == "stop", "Startup did not remain stopped"
        response = api.post("/api/stop");response.raise_for_status()
        result["stop_before"] = response.json()
        with httpx.Client(trust_env=False, timeout=3) as anonymous:
            result["anonymous_status"] = anonymous.get(str(api.base_url)+"/api/status").status_code
        for path in ("/api/control-lease", "/api/vision/status", "/api/navigation/status"):
            response = api.get(path);response.raise_for_status()
            result[path] = response.json()
        entry = config["entry"]
        # Execute the exact launcher's underlays and environment, omitting its launch command.
        prefix = remote.python(ssh, "from pathlib import Path\nprint('\\n'.join(line for line in Path("+
                               repr(entry)+").read_text().splitlines() if not line.startswith('exec ')))")
        source = prefix + "\npython3 -c " + shlex.quote(CAPTURE)
        capture = remote.run(ssh, "bash --noprofile --norc -c " + shlex.quote(source), timeout=30)
        result["capture"] = json.loads(next(line.removeprefix("PREFLIGHT_JSON=")
                                           for line in capture.splitlines() if line.startswith("PREFLIGHT_JSON=")))
        response = api.post("/api/stop");response.raise_for_status()
        result["stop_after"] = response.json()
        result["final_status"] = api.get("/api/status").json()
        samples = result["capture"]["samples"]
        checks = {
            "authenticated": result["anonymous_status"] == 401,
            "stopped": result["final_status"]["mode"] == "stop",
            "stop_confirmed": result["stop_after"].get("stationary_confirmed") is True,
            "zero_cmd_vel": bool(samples["cmd_vel"]) and all(
                abs(v[axis]) < 1e-9 for v in samples["cmd_vel"] for axis in ("x","y","z")),
            "stationary_odom": bool(samples["odom"]) and all(
                v["linear"] < .01 and v["angular"] < .02 for v in samples["odom"]),
            "fresh_scan": bool(samples["scan"]) and all(0 <= v["age"] < .6 for v in samples["scan"]),
            "fresh_odom": bool(samples["odom"]) and all(0 <= v["age"] < .5 for v in samples["odom"]),
            "scan_tf": result["capture"]["scan_tf"]["ok"],
            "single_controller": result["capture"]["publishers"] == [{"name":"decision_controller","namespace":"/"}],
        }
        result["checks"] = checks
        result["passed"] = all(checks.values())
        print(json.dumps({"passed":result["passed"],"checks":checks}, ensure_ascii=False))
    except Exception as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc).replace(token, "[redacted]")
        result["passed"] = False
        try:
            api.post("/api/stop")
        except httpx.HTTPError:
            if started:
                remote.run(ssh, "systemctl --user stop smart-car-v4.service", timeout=40)
        result["journal"] = remote.run(ssh, "journalctl _SYSTEMD_USER_UNIT=smart-car-v4.service -n 100 --no-pager", timeout=15).replace(token, "[redacted]")
        print(json.dumps({"passed":False,"error":result["error"]}, ensure_ascii=False))
    finally:
        api.close();ssh.close()
        (output / "stationary-preflight.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
