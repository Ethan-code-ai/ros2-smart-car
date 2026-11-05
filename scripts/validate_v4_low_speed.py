"""Explicitly authorized field probe through the real shared host control API.

Requires a passed stationary preflight and --execute. No LLM or acceptance bypass.
"""
import argparse
from collections import deque
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import secrets
import shlex
import sys
import threading
import time

import httpx
import stage_avoidance_release as remote
from preflight_v4_vehicle import CAPTURE

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--duration", type=float, default=1.8, help="Bounded forward window, 1–4.5 seconds")
    parser.add_argument("--speed", type=float, default=.07, help="Requested speed limit, .03–.10 m/s")
    parser.add_argument("--distance", type=float, default=.20, help="Stop at odometry distance, .05–.20 m")
    args = parser.parse_args()
    if not args.execute:
        parser.error("On-site authorization and --execute required")
    if not math.isfinite(args.duration) or not 1 <= args.duration <= 4.5:
        parser.error("Duration must be within 1–4.5 seconds")
    if not math.isfinite(args.speed) or not .03 <= args.speed <= .10:
        parser.error("Speed must be within .03–.10 m/s")
    if not math.isfinite(args.distance) or not .05 <= args.distance <= .20:
        parser.error("Distance must be within .05–.20 m")
    output = ROOT / "output/v4-vehicle-preflight"
    assert json.loads((output / "stationary-preflight.json").read_text(encoding="utf-8"))["passed"]
    provision = json.loads((output / "provisioned-v4.json").read_text())
    directory = output / ("low-speed-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    directory.mkdir()
    for key in ("ASSISTANT_API_KEY", "ASSISTANT_API_KEY_FILE", "ASSISTANT_BASE_URL", "ASSISTANT_MODEL",
                "ROBOT_SERVICE_TOKEN"):
        os.environ.pop(key, None)
    os.environ.update(
        DATABASE_URL="sqlite:///" + (directory.resolve() / "host.db").as_posix(),
        STORAGE_LOCAL_DIR=str(directory.resolve() / "storage"), REDIS_ENABLED="false",
        ROBOT_WEB_URL="http://" + provision["host"] + ":8080",
        ROBOT_GATEWAY_URL="", JWT_SECRET=secrets.token_urlsafe(48),
        INSIGHTROVER_CREDENTIAL_PROFILE=provision["credential_profile"])
    sys.path.insert(0, str(ROOT / "backend"))
    from app.seed import seed
    from app.db.session import SessionLocal
    from app.models import Robot
    from app.main import app
    from app.services.windows_credentials import read_credentials
    from starlette.testclient import TestClient
    seed()
    with SessionLocal() as db:
        db.add(Robot(id="r-001", name="现场验收车", serial="yahboom-field-v4", mode="stop"))
        db.commit()
    token = read_credentials(provision["credential_profile"])["robot_token"]
    vehicle = httpx.Client(base_url=os.environ["ROBOT_WEB_URL"], trust_env=False, timeout=3,
                           headers={"X-InsightRover-Service-Token": token})
    ssh = remote.connect(provision["host"])
    result = {"at":datetime.now(timezone.utc).isoformat(),"motion_requested":False,
              "model_api_calls":0,"host_control_api":True,"requests":[]}
    old_scale = None
    capture_stdout = None
    try:
        settings = json.loads(remote.python(ssh, "import json,yaml\nfrom pathlib import Path\nv=yaml.safe_load(Path("+
            repr(str(Path(provision["entry"]).parent).replace("\\","/") + "/ros2_ws/src/smart_car_decision/config/decision.yaml")+
            ").read_text())\nprint(json.dumps(v['decision_controller']['ros__parameters']))"))
        cruise = settings["cruise_speed"]
        assert isinstance(cruise, (int,float)) and .01 <= cruise <= 1
        scale = min(1., args.speed / cruise)
        status = vehicle.get("/api/status");status.raise_for_status()
        status = status.json()
        assert status["mode"] == "stop" and status.get("emergency_stop") is False
        assert status["lidar"]["ok"] and status["odom"]["ok"] and status["tf"]["ok"]
        lease = vehicle.get("/api/control-lease").json()
        assert lease.get("fresh") is True and lease.get("blocked") is True
        old_scale = status.get("speed_scale", 1.0)
        result.update(initial_status=status,requested_speed_mps=cruise*scale,
                      duration_limit_sec=args.duration,distance_limit_m=args.distance)
        with TestClient(app) as host:
            login = host.post("/api/v1/auth/login",json={"username":"admin","password":"admin123"})
            login.raise_for_status()
            host.headers["Authorization"]="Bearer "+login.json()["access_token"]
            def post(path, payload):
                response=host.post(path,json=payload)
                result["requests"].append({"at":time.time(),"path":path,"status":response.status_code,
                                            "response":response.json()})
                response.raise_for_status()
                return response.json()
            try:
                post("/api/control/speed",{"scale":scale})
                drive=post("/api/control/mode",{"mode":"manual"})
                assert drive.get("ok") is True and drive.get("drive_id")
                prefix=remote.python(ssh,"from pathlib import Path\nprint('\\n'.join(l for l in Path("+
                    repr(provision["entry"])+").read_text().splitlines() if not l.startswith('exec ')))")
                source=CAPTURE.replace('deadline=time.monotonic()+6',
                    'print("CAPTURE_READY",flush=True)\ndeadline=time.monotonic()+10')
                source=source.replace(' "linear":math.hypot(t.linear.x,t.linear.y),"angular":abs(t.angular.z)})',
                    ' "linear":math.hypot(t.linear.x,t.linear.y),"angular":abs(t.angular.z)})\n'
                    ' print("ODOM_JSON="+json.dumps(samples["odom"][-1]),flush=True)')
                assert 'print("ODOM_JSON="' in source
                command=prefix+"\npython3 -u -c "+shlex.quote(source)
                _,capture_stdout,capture_stderr=ssh.exec_command("bash --noprofile --norc -c "+shlex.quote(command),timeout=25)
                while True:
                    line=capture_stdout.readline()
                    if not line:
                        raise RuntimeError("Capture failed before motion")
                    if line.strip()=="CAPTURE_READY":
                        break
                latest=deque(maxlen=1)
                capture_lines=[]
                def read_capture():
                    for line in capture_stdout:
                        if line.startswith("ODOM_JSON="):
                            latest.append((time.monotonic(),json.loads(line.removeprefix("ODOM_JSON="))))
                        elif line.startswith("PREFLIGHT_JSON="):
                            capture_lines.append(line)
                reader=threading.Thread(target=read_capture,daemon=True)
                reader.start()
                time.sleep(.6)
                assert latest and time.monotonic()-latest[-1][0]<.5,"No fresh odometry capture"
                origin=latest[-1][1]
                result["motion_requested"]=True
                result["motion_start_at"]=time.time()
                deadline=time.monotonic()+args.duration
                sequence=0
                while time.monotonic()<deadline:
                    assert latest and time.monotonic()-latest[-1][0]<.5,"Live odometry became stale"
                    pose=latest[-1][1]
                    assert 0<=pose["age"]<.5,"Vehicle odometry stamp is stale"
                    if math.hypot(pose["x"]-origin["x"],pose["y"]-origin["y"])>=args.distance:
                        result["distance_stop_triggered"]=True
                        break
                    sequence+=1
                    post("/api/control/command",{"command":"forward","drive_id":drive["drive_id"],"drive_seq":sequence})
                    time.sleep(.1)
                sequence+=1
                post("/api/control/command",{"command":"stop","drive_id":drive["drive_id"],"drive_seq":sequence})
                result["stop_at"]=time.time()
            finally:
                result["host_stop"]=post("/api/v1/robots/r-001/stop",{})
        reader.join(timeout=20)
        assert not reader.is_alive(),"Capture did not finish"
        capture="".join(capture_lines)
        assert capture_stdout.channel.recv_exit_status()==0,capture_stderr.read().decode()[-800:]
        result["capture"]=json.loads(next(l.removeprefix("PREFLIGHT_JSON=") for l in capture.splitlines()
                                          if l.startswith("PREFLIGHT_JSON=")))
        samples=result["capture"]["samples"]
        poses=samples["odom"]
        assert poses and samples["cmd_vel"]
        start=poses[0]
        result["displacement_m"]=math.hypot(poses[-1]["x"]-start["x"],poses[-1]["y"]-start["y"])
        result["peak_displacement_m"]=max(math.hypot(v["x"]-start["x"],v["y"]-start["y"]) for v in poses)
        result["peak_command_mps"]=max(math.hypot(v["x"],v["y"]) for v in samples["cmd_vel"])
        result["checks"]={
            "actual_displacement":.01 <= result["displacement_m"] <= .35,
            "bounded_command":.001 < result["peak_command_mps"] <= args.speed+.005,
            "final_zero":all(abs(v[k])<1e-9 for v in samples["cmd_vel"][-10:] for k in ("x","y","z")),
            "final_stationary":all(v["linear"]<.01 and v["angular"]<.02 for v in poses[-10:]),
            "stop_confirmed":result["host_stop"].get("stationary_confirmed") is True,
            "single_controller":result["capture"]["publishers"]==[{"name":"decision_controller","namespace":"/"}],
        }
        result["passed"]=all(result["checks"].values())
    except Exception as exc:
        result["passed"]=False
        result["error"]=(type(exc).__name__+": "+str(exc)).replace(token,"[redacted]")
    finally:
        try:
            response=vehicle.post("/api/stop");response.raise_for_status()
            result["final_stop"]=response.json()
            if old_scale is not None and result["final_stop"].get("stationary_confirmed") is True:
                result["speed_restored"]=vehicle.post("/api/speed",json={"scale":old_scale}).status_code==200
            result["final_status"]=vehicle.get("/api/status").json()
        except httpx.HTTPError:
            result["final_stop"]={"stationary_confirmed":False}
            result["passed"]=False
        ssh.close();vehicle.close()
        (directory/"evidence.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({k:result[k] for k in ("passed","displacement_m","peak_command_mps","checks","error")
                          if k in result},ensure_ascii=False))
        print("Evidence: "+str(directory/"evidence.json"))
    if not result["passed"]:
        raise SystemExit(1)


if __name__=="__main__":
    main()
