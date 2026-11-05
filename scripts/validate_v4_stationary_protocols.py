"""Physical commissioning of lease expiry and stationary search, without motion.

Uses machine-authenticated car endpoints for commissioning; does not register
acceptance or enable business capabilities. Never acquires a moving mode.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import sys
import time
from uuid import uuid4

import httpx
import stage_avoidance_release as remote
from preflight_v4_vehicle import CAPTURE

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / "output/v4-vehicle-preflight"
    provision = json.loads((output / "provisioned-v4.json").read_text())
    directory = output / ("stationary-protocols-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    directory.mkdir()
    sys.path.insert(0, str(ROOT / "backend"))
    from app.services.windows_credentials import read_credentials
    token = read_credentials(provision["credential_profile"])["robot_token"]
    api = httpx.Client(base_url="http://" + provision["host"] + ":8080", trust_env=False, timeout=20,
                       headers={"X-InsightRover-Service-Token":token})
    ssh = remote.connect(provision["host"])
    result = {"at":datetime.now(timezone.utc).isoformat(),"motion_requested":False,
              "commissioning_only":True,"acceptance_registered":False,"requests":[]}
    ident = None
    try:
        def post(path,payload):
            response=api.post(path,json=payload)
            result["requests"].append({"at":time.time(),"path":path,"payload":payload,
                                       "status":response.status_code,"response":response.json()})
            response.raise_for_status()
            return response.json()
        stopped=post("/api/stop",{})
        assert stopped.get("stationary_confirmed") is True
        initial=api.get("/api/status").json()
        assert initial["mode"]=="stop" and initial.get("emergency_stop") is False
        prefix=remote.python(ssh,"from pathlib import Path\nprint('\\n'.join(l for l in Path("+
            repr(provision["entry"])+").read_text().splitlines() if not l.startswith('exec ')))")
        capture=CAPTURE.replace("deadline=time.monotonic()+6",
            'print("CAPTURE_READY",flush=True)\ndeadline=time.monotonic()+35')
        _,stdout,stderr=ssh.exec_command("bash --noprofile --norc -c "+
            shlex.quote(prefix+"\npython3 -u -c "+shlex.quote(capture)),timeout=50)
        while True:
            line=stdout.readline()
            assert line,"Capture unavailable"
            if line.strip()=="CAPTURE_READY":break
        lease=api.get("/api/control-lease").json()
        assert lease.get("fresh") is True and lease.get("blocked") is True
        request={key:lease[key] for key in ("controller_id","control_epoch")}
        request.update(owner="field-static-"+uuid4().hex,sequence=0,op="acquire",purpose="manual")
        acquired=post("/api/control-lease",request)
        assert acquired.get("confirmed") is True and acquired["lease"]["blocked"] is False
        # Remain in stop mode; no drive commands, no renewals.
        time.sleep(3.4)
        expired=api.get("/api/control-lease").json()
        result["lease_after_expiry"]=expired
        assert expired.get("fresh") is True and expired.get("blocked") is True
        assert expired["control_epoch"]!=request["control_epoch"]
        late=api.post("/api/control-lease",json={**request,"op":"renew","sequence":1})
        result["late_renewal_status"]=late.status_code
        assert late.status_code==409
        assert api.get("/api/status").json()["mode"]=="stop"
        cancelled_id=uuid4().hex
        cancelled=post("/api/search/action",{"run_id":cancelled_id,"action":"cancel"})
        payload={"run_id":cancelled_id,"travel":"stationary","targets":[{"name":"瓶子","prompt":"bottle"}]}
        replay=post("/api/search/start",payload)
        assert cancelled["state"]==replay["state"]=="cancelled"
        ident=uuid4().hex
        payload={**payload,"run_id":ident}
        started=post("/api/search/start",payload)
        duplicate=post("/api/search/start",payload)
        assert started["run_id"]==duplicate["run_id"]==ident
        snapshots=[]
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            state=api.get("/api/search",params={"run_id":ident}).json()
            snapshots.append({"at":time.time(),**state})
            if state.get("state") in {"found","fault"}:break
            time.sleep(.2)
        result["search_snapshots"]=snapshots
        result["found"]=bool(snapshots and snapshots[-1].get("state")=="found")
        result["vision"]=api.get("/api/vision/status").json()
        post("/api/search/action",{"run_id":ident,"action":"finish"})
        result["final_stop"]=post("/api/stop",{})
        captured=stdout.read().decode("utf-8",errors="replace")
        assert stdout.channel.recv_exit_status()==0,stderr.read().decode()[-800:]
        result["capture"]=json.loads(next(line.removeprefix("PREFLIGHT_JSON=")
            for line in captured.splitlines() if line.startswith("PREFLIGHT_JSON=")))
        samples=result["capture"]["samples"]
        result["checks"]={
            "lease_expires":expired["reason"]=="control_lease_expired",
            "late_renewal_rejected":late.status_code==409,
            "cancel_before_dispatch":replay["state"]=="cancelled",
            "duplicate_same_run":started["run_id"]==duplicate["run_id"],
            "target_found":result["found"],
            "zero_commands":bool(samples["cmd_vel"]) and all(
                abs(v[key])<1e-9 for v in samples["cmd_vel"] for key in ("x","y","z")),
            "stationary_odom":bool(samples["odom"]) and all(
                v["linear"]<.01 and v["angular"]<.02 for v in samples["odom"]),
            "stop_confirmed":result["final_stop"].get("stationary_confirmed") is True,
        }
        result["passed"]=all(result["checks"].values())
    except Exception as exc:
        result["passed"]=False
        result["error"]=(type(exc).__name__+": "+str(exc)).replace(token,"[redacted]")
    finally:
        try:
            if ident:
                api.post("/api/search/action",json={"run_id":ident,"action":"cancel"})
            result["cleanup_stop"]=api.post("/api/stop").json()
            result["final_status"]=api.get("/api/status").json()
        except httpx.HTTPError:
            result["passed"]=False
            result["cleanup_stop"]={"stationary_confirmed":False}
        api.close();ssh.close()
        (directory/"evidence.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({key:result[key] for key in ("passed","checks","error") if key in result}))
        print("Evidence: "+str(directory/"evidence.json"))
    if not result["passed"]:raise SystemExit(1)


if __name__=="__main__":main()
