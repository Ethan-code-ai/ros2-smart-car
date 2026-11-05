"""Two real natural-language requests through the production host and real car.

No simulated provider, telemetry, capability grant or automatic executable-plan
confirmation. Uses an isolated host database and restores its vault profile.
"""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def model_environment():
    allowed={"ASSISTANT_BASE_URL","ASSISTANT_MODEL","ASSISTANT_API_KEY","ASSISTANT_API_KEY_FILE"}
    values={}
    for line in (ROOT/".env").read_text(encoding="utf-8-sig").splitlines():
        key,separator,value=line.strip().removeprefix("export ").partition("=")
        if separator and key.strip() in allowed:
            value=value.strip()
            if value.startswith(("'",'"')):
                assert len(value)>1 and value[-1]==value[0],"Unclosed model configuration quote"
                value=value[1:-1]
            values[key.strip()]=value
    return values


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interactive-trials",action="store_true",
                        help="Prepare both real plans, then wait for explicit stdin confirmation")
    parser.add_argument("--resume",type=Path,help="Resume prepared plans without another model request")
    parser.add_argument("--refresh-prepared",action="store_true",help="Revalidate saved intent into new drafts; never replay an execution")
    args=parser.parse_args()
    preflight=ROOT/"output/v4-vehicle-preflight"
    provision=json.loads((preflight/"provisioned-v4.json").read_text())
    directory=args.resume.resolve() if args.resume else ROOT/"output"/("v4-live-agent-interface-"+datetime.now().strftime("%Y%m%d-%H%M%S"))
    assert directory.resolve().is_relative_to((ROOT/"output").resolve())
    if not args.resume:directory.mkdir()
    sys.path.insert(0,str(ROOT/"backend"))
    from app.services.windows_credentials import read_credentials,write_credentials
    profile=provision["credential_profile"]
    original=read_credentials(profile)
    values=model_environment()
    key=values.get("ASSISTANT_API_KEY","")
    if path:=values.get("ASSISTANT_API_KEY_FILE"):
        key=Path(path).read_text(encoding="utf-8").strip()
    assert key and values.get("ASSISTANT_BASE_URL") and values.get("ASSISTANT_MODEL")
    credentials={**original,"assistant_url":values["ASSISTANT_BASE_URL"],
                 "assistant_model":values["ASSISTANT_MODEL"],"assistant_key":key}
    env=os.environ.copy()
    for name in ("ASSISTANT_API_KEY","ASSISTANT_API_KEY_FILE","ASSISTANT_BASE_URL","ASSISTANT_MODEL","ROBOT_SERVICE_TOKEN"):
        env.pop(name,None)
    env.update(INSIGHTROVER_CREDENTIAL_PROFILE=profile,
               DATABASE_URL="sqlite:///"+(directory.resolve()/"host.db").as_posix(),
               ROBOT_WEB_URL="http://"+provision["host"]+":8080",ROBOT_GATEWAY_URL="",
               STORAGE_LOCAL_DIR=str(directory.resolve()/"storage"),REDIS_ENABLED="false",
               JWT_SECRET=secrets.token_urlsafe(48))
    result={"at":datetime.now(timezone.utc).isoformat(),"real_model":values["ASSISTANT_MODEL"],
            "real_vehicle":provision["host"],"simulated_components":[],
            "automatic_executable_confirmations":0,"cases":[]}
    if args.resume:
        assert args.interactive_trials
        result=json.loads((directory/"prepared.json").read_text(encoding="utf-8"))
        result["resumed_without_model_calls"]=True
    process=None
    log=None
    ssh=None
    capture_stdout=None
    executions=[]
    try:
        write_credentials(profile,credentials)
        if not args.resume:
            subprocess.run([sys.executable,"-m","app.seed"],cwd=ROOT/"backend",env=env,
                           check=True,stdout=subprocess.DEVNULL)
            subprocess.run([sys.executable,"-c",
                "from app.db.session import SessionLocal;from app.models import Robot;"
                "db=SessionLocal();db.add(Robot(id='r-001',name='Agent现场验证车',serial='yahboom-agent-v4',mode='stop'));db.commit();db.close()"],
                cwd=ROOT/"backend",env=env,check=True)
        import sqlite3
        with sqlite3.connect(directory/"host.db") as db:
            result["initial_execution_rows"]=db.execute("SELECT count(*) FROM assistant_executions").fetchone()[0]
        with socket.socket() as listener:
            listener.bind(("127.0.0.1",0));port=listener.getsockname()[1]
        log=(directory/("server.log" if not args.resume else "server-resume-"+str(time.time_ns())+".log")).open("wb")
        process=subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app","--host","127.0.0.1","--port",str(port)],
            cwd=ROOT/"backend",env=env,stdout=log,stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}",trust_env=False,timeout=40) as api:
            for _ in range(60):
                assert process.poll() is None,"Host exited"
                try:
                    if api.get("/health/ready",timeout=1).status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.2)
            else:raise RuntimeError("Host readiness timeout")
            login=api.post("/api/v1/auth/login",json={"username":"admin","password":"admin123"})
            login.raise_for_status()
            api.headers["Authorization"]="Bearer "+login.json()["access_token"]
            for _ in range(20):
                context=api.get("/api/v1/robots/r-001/context").json()
                if context.get("available") is True:break
                time.sleep(.3)
            assert context.get("available") is True,"Real vehicle telemetry unavailable"
            result["context"]=context
            result["capabilities"]=api.get("/api/v1/robots/r-001/capabilities").json()
            if args.interactive_trials:
                deadline=time.monotonic()+65
                while time.monotonic()<deadline:
                    ready=api.get("/api/v1/robots/r-001/capabilities").json()
                    if all(ready["capabilities"][name]["ready"] for name in ("move_relative","observe_target")):
                        result["capabilities"]=ready
                        break
                    time.sleep(.5)
                else:raise RuntimeError("New task capabilities are not ready")
            enabled=api.put("/api/v1/assistant/settings",json={"enabled":True})
            enabled.raise_for_status()
            prompts=(
                ("forward","请让小车向前前进20厘米，随后停车。先生成需要我确认的任务，不要假装已经执行。"),
                ("recognition","保持小车静止，识别画面中有没有瓶子，发现后保持停车并等待我处理。先生成待确认任务。"),
            )
            for name,prompt in (() if args.resume else prompts):
                session=api.post("/api/v1/assistant/sessions");session.raise_for_status()
                ident=session.json()["id"]
                started=time.monotonic()
                response=api.post(f"/api/v1/assistant/sessions/{ident}/messages",
                    json={"client_id":str(uuid4()),"text":prompt})
                case={"name":name,"prompt":prompt,"http_status":response.status_code,
                      "seconds":round(time.monotonic()-started,2),"response":response.json()}
                if response.is_success:
                    reply=response.json().get("reply") or {}
                    plan=reply.get("plan")
                    if plan and all(step["capability"]["executable"] is False for step in plan["steps"]):
                        # Exercise the rejection path only; never grant missing acceptance.
                        confirmation=api.post("/api/v1/assistant/plans/"+plan["id"]+"/confirm",
                            json={"version":plan["version"],"digest":plan["digest"]})
                        case["blocked_confirmation"]={"status":confirmation.status_code,"response":confirmation.json()}
                        assert confirmation.status_code==409
                result["cases"].append(case)
                print(json.dumps({"case":name,"http_status":case["http_status"],
                    "reply":(case["response"].get("reply") or {}).get("text"),
                    "has_plan":bool((case["response"].get("reply") or {}).get("plan")),
                    "confirmation_status":case.get("blocked_confirmation",{}).get("status")},ensure_ascii=False),flush=True)
            if args.interactive_trials:
                if args.refresh_prepared:
                    assert args.resume
                    for case in result["cases"]:
                        old=(case["response"].get("reply") or {})["plan"]
                        current=api.get("/api/v1/assistant/plans/"+old["id"]).json()
                        previous=current.get("execution")
                        if previous and previous["state"] not in {"completed","cancelled","failed"}:
                            cancelled=api.post("/api/v1/assistant/executions/"+previous["id"]+"/actions",
                                json={"action":"cancel","version":previous["version"]})
                            cancelled.raise_for_status()
                            for _ in range(30):
                                ended=api.get("/api/v1/assistant/executions/"+previous["id"]).json()
                                if ended["state"]=="cancelled":break
                                time.sleep(.2)
                            assert ended["state"]=="cancelled"
                        refreshed=api.post("/api/v1/assistant/plans/"+old["id"]+"/refresh",
                            json={key:old[key] for key in ("version","digest")})
                        refreshed.raise_for_status()
                        case["previous_plan_id"]=old["id"]
                        case["response"]["reply"]["plan"]=refreshed.json()
                plans=[(case["response"].get("reply") or {}).get("plan") for case in result["cases"]]
                assert all(plan and len(plan["steps"])==1 for plan in plans),"Both requests must produce real single-step plans"
                assert plans[0]["steps"][0]["action"]=="move_relative"
                assert abs(plans[0]["steps"][0]["distance_m"]-.20)<1e-9
                assert plans[1]["steps"][0]["action"]=="observe_target" and plans[1]["steps"][0]["target"]=="瓶子"
                result["reviewed_plans"]=[{"id":p["id"],"version":p["version"],"digest":p["digest"],
                                          "steps":p["steps"]} for p in plans]
                (directory/"prepared.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
                print("READY_FOR_TRIAL_CONFIRMATION",flush=True)
                if input().strip()!="confirm":
                    raise RuntimeError("Field test not confirmed; no task was dispatched")
                import shlex
                import stage_avoidance_release as remote
                from preflight_v4_vehicle import CAPTURE
                ssh=remote.connect(provision["host"])
                prefix=remote.python(ssh,"from pathlib import Path\nprint('\\n'.join(l for l in Path("+
                    repr(provision["entry"])+").read_text().splitlines() if not l.startswith('exec ')))")
                source=CAPTURE.replace("deadline=time.monotonic()+6",
                    'print("CAPTURE_READY",flush=True)\ndeadline=time.monotonic()+20')
                _,capture_stdout,capture_stderr=ssh.exec_command("bash --noprofile --norc -c "+
                    shlex.quote(prefix+"\npython3 -u -c "+shlex.quote(source)),timeout=35)
                while True:
                    line=capture_stdout.readline()
                    assert line,"Capture unavailable"
                    if line.strip()=="CAPTURE_READY":break
                for case,plan in zip(result["cases"],plans):
                    # Let the shared context collector observe the preceding stop.
                    deadline=time.monotonic()+5
                    while time.monotonic()<deadline:
                        current=api.get("/api/v1/robots/r-001/capabilities").json()
                        if current["capabilities"][plan["steps"][0]["action"]]["ready"]:break
                        time.sleep(.2)
                    payload={key:plan[key] for key in ("version","digest")}
                    authorized=api.post("/api/v1/assistant/plans/"+plan["id"]+"/trial-authorize",json=payload)
                    authorized.raise_for_status()
                    confirmed=api.post("/api/v1/assistant/plans/"+plan["id"]+"/confirm",json=payload)
                    confirmed.raise_for_status()
                    execution=confirmed.json();executions.append(execution["id"])
                    duplicate=api.post("/api/v1/assistant/plans/"+plan["id"]+"/confirm",json=payload)
                    duplicate.raise_for_status()
                    assert duplicate.json()["id"]==execution["id"]
                    snapshots=[];deadline=time.monotonic()+30
                    while time.monotonic()<deadline:
                        response=api.get("/api/v1/assistant/executions/"+execution["id"])
                        response.raise_for_status();execution=response.json()
                        snapshots.append({"at":time.time(),**execution})
                        if execution["state"] in {"completed","waiting_human","paused","failed","cancelled","unconfirmed"}:break
                        time.sleep(.25)
                    case["execution_snapshots"]=snapshots
                    expected="completed" if case["name"]=="forward" else "waiting_human"
                    assert execution["state"]==expected, json.dumps(execution,ensure_ascii=False)
                    if case["name"]=="recognition":assert execution["vehicle_state"]=="found"
                    print(json.dumps({"case":case["name"],"execution":execution},ensure_ascii=False),flush=True)
                    if execution["state"]=="waiting_human":
                        cancelled=api.post("/api/v1/assistant/executions/"+execution["id"]+"/actions",
                            json={"version":execution["version"],"action":"cancel"})
                        cancelled.raise_for_status()
                        for _ in range(30):
                            current=api.get("/api/v1/assistant/executions/"+execution["id"]).json()
                            if current["state"]=="cancelled":break
                            time.sleep(.2)
                        case["cleanup_execution"]=current
                captured=capture_stdout.read().decode("utf-8",errors="replace")
                assert capture_stdout.channel.recv_exit_status()==0,capture_stderr.read().decode()[-500:]
                result["capture"]=json.loads(next(line.removeprefix("PREFLIGHT_JSON=")
                    for line in captured.splitlines() if line.startswith("PREFLIGHT_JSON=")))
                result["explicitly_confirmed_trials"]=len(executions)
                print("LIVE_AGENT_TRIALS_FINISHED",flush=True)
            result["final_context"]=api.get("/api/v1/robots/r-001/context").json()
        import sqlite3
        with sqlite3.connect(directory/"host.db") as db:
            result["execution_rows"]=db.execute("SELECT count(*) FROM assistant_executions").fetchone()[0]
        assert result["execution_rows"]==result["initial_execution_rows"]+(2 if args.interactive_trials else 0)
    except Exception as exc:
        result["error"]=(type(exc).__name__+": "+str(exc)).replace(key,"[redacted]")
        print(json.dumps({"error":result["error"]}),flush=True)
    finally:
        if capture_stdout is not None and "capture" not in result:
            try:
                remaining=capture_stdout.read().decode("utf-8",errors="replace")
                capture_stdout.channel.recv_exit_status()
                result["capture"]=json.loads(next(line.removeprefix("PREFLIGHT_JSON=")
                    for line in remaining.splitlines() if line.startswith("PREFLIGHT_JSON=")))
            except Exception as exc:
                result["capture_error"]=type(exc).__name__
        if process is not None:
            process.terminate();process.wait(timeout=10)
        if log is not None:log.close()
        if ssh is not None:ssh.close()
        if args.interactive_trials:
            # Independent stop also covers a failed assertion or lost execution response.
            try:
                with httpx.Client(trust_env=False,timeout=4) as vehicle:
                    response=vehicle.post(env["ROBOT_WEB_URL"]+"/api/stop",
                        headers={"X-InsightRover-Service-Token":original["robot_token"]})
                    result["independent_final_stop"]=response.json()
            except httpx.HTTPError:
                result["independent_final_stop"]={"stationary_confirmed":False}
        write_credentials(profile,original)
        (directory/"evidence.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print("Evidence: "+str(directory/"evidence.json"),flush=True)
    if "error" in result:raise SystemExit(1)


if __name__=="__main__":main()
