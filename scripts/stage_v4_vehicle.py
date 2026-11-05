"""Stage and build v4 in a new vehicle directory; never starts the chassis."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

import stage_avoidance_release as remote


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--previous", required=True)
    args = parser.parse_args()
    subprocess.run([sys.executable, str(ROOT / "scripts/package_vision_source.py")], check=True)
    archive = ROOT / "releases/v4.0.0/InsightRover-Vehicle-4.0.0-source.tar.gz"
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    target = "/home/jetson/insightrover-v4-" + digest[:12]
    output = ROOT / "output/v4-vehicle-preflight"
    output.mkdir(parents=True, exist_ok=True)
    client = remote.connect(args.host)
    try:
        prepared = remote.python(client, f'''
from pathlib import Path
import json,shutil,subprocess
target=Path({target!r}); previous=Path({args.previous!r})
assert target.parent.resolve()==Path.home().resolve()
assert previous.is_dir() and previous.resolve().is_relative_to(Path.home().resolve())
assert not target.exists(), "Staging target already exists"
assert shutil.disk_usage(target.parent).free>{archive.stat().st_size * 3 + 256 * 1024**2}
processes=subprocess.run(["pgrep","-f","/(web_app_node|decision_controller)( |$)"],capture_output=True,text=True)
assert not processes.stdout.strip(), "Vehicle control processes are already active"
target.mkdir(mode=0o755)
(target/"logs").mkdir()
print(json.dumps({{"target":str(target),"previous":str(previous),"free_bytes":shutil.disk_usage(target).free}}))
''')
        print(prepared, flush=True)
        with client.open_sftp() as sftp:
            sftp.put(str(archive), target + "/source.tar.gz")
        print("Uploaded source. Checking paths, hashes and vehicle parameters.", flush=True)
        prepared = remote.python(client, f'''
from pathlib import Path
import hashlib,json,shutil,tarfile,yaml
root=Path({target!r}); previous=Path({args.previous!r}); archive=root/"source.tar.gz"
assert hashlib.sha256(archive.read_bytes()).hexdigest()=={digest!r}
with tarfile.open(archive) as package:
    members=package.getmembers()
    assert len(members)<5000
    for member in members:
        assert member.isfile() or member.isdir()
        assert not Path(member.name).is_absolute()
        assert (root/member.name).resolve().is_relative_to(root.resolve())
    package.extractall(root)
manifest=json.loads((root/"source-sha256.json").read_text())
for name, expected in manifest.items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name
config=root/"ros2_ws/src/smart_car_decision/config/decision.yaml"
current=yaml.safe_load(config.read_text())
old=yaml.safe_load((previous/"ros2_ws/src/smart_car_decision/config/decision.yaml").read_text())
for name, node in old.items():
    if isinstance(node,dict) and isinstance(node.get("ros__parameters"),dict):
        current.setdefault(name,{{}}).setdefault("ros__parameters",{{}}).update(node["ros__parameters"])
current["mode_manager"]["ros__parameters"]["initial_mode"]="stop"
current["web_app_node"]["ros__parameters"]["static_dir"]=str(root/"web-console/dist")
config.write_text(yaml.safe_dump(current,allow_unicode=True,sort_keys=False))
backup=root/"previous-state"; backup.mkdir()
state=Path.home()/".ros/insightrover"
for path in state.iterdir() if state.exists() else []:
    if path.is_file() and path.suffix in {{".json",".sqlite3"}}:
        assert path.stat().st_size<32*1024**2
        shutil.copy2(path,backup/path.name)
setups=[line for line in (previous/"start_test.sh").read_text().splitlines()
        if line.startswith("source ") and line.endswith("/setup.bash")]
for line in setups:
    assert Path(line[7:]).is_file(),line
driver=Path("/home/jetson/insightrover-scan-provenance-20261028/install/setup.bash")
assert driver.is_file(),"Verified lidar overlay is missing"
setups.append("source "+str(driver))
state={{"target":str(root),"previous":str(previous),"archive_sha256":{digest!r},
       "source_files":len(manifest),"setups":setups,"started":False}}
(root/"staging.json").write_text(json.dumps(state,indent=2))
print(json.dumps(state))
''', timeout=35)
        state = json.loads(prepared)
        print(json.dumps({key: state[key] for key in ("target", "source_files", "started")}), flush=True)
        command = "\n".join([
            "set -eo pipefail",
            "unset AMENT_PREFIX_PATH COLCON_PREFIX_PATH CMAKE_PREFIX_PATH PYTHONPATH",
            *state["setups"],
            "cd " + shlex.quote(target + "/ros2_ws"),
            "colcon build --symlink-install --packages-select smart_car_decision --event-handlers console_direct+"
            " > " + shlex.quote(target + "/logs/build.log") + " 2>&1",
            "source " + shlex.quote(target + "/ros2_ws/install/local_setup.bash"),
            "python3 -c " + shlex.quote(
                "from pathlib import Path; import smart_car_decision.decision_controller as c; "
                "import smart_car_decision.web_app_node as w; "
                "from ament_index_python.packages import get_package_share_directory; "
                f"root=Path({target!r}); "
                "assert Path(c.__file__).resolve().is_relative_to(root); "
                "assert Path(w.__file__).resolve().is_relative_to(root); "
                "assert Path(get_package_share_directory('smart_car_decision')).resolve().is_relative_to(root); "
                "print('Verified isolated v4 imports')"),
        ])
        print("Building the isolated ROS package.", flush=True)
        build = remote.run(client, "bash --noprofile --norc -c " + shlex.quote(command), timeout=180)
        (output / "v4-build.log").write_text(build, encoding="utf-8")
        verified = json.loads(remote.python(client, f'''
from pathlib import Path
import hashlib,json
root=Path({target!r})
paths=list((root/"ros2_ws/src/smart_car_decision").rglob("*.py"))
paths+=list((root/"ros2_ws/src/smart_car_decision/config").glob("*"))
hashes={{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file() and "__pycache__" not in p.parts}}
value=json.loads((root/"staging.json").read_text())
value.update(built=True,effective_hashes=hashes)
(root/"staging.json").write_text(json.dumps(value,indent=2))
print(json.dumps(value))
'''))
        (output / "staged-v4.json").write_text(json.dumps(verified, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"target": target, "built": True, "started": False, "verified_files": len(verified["effective_hashes"])}))
    finally:
        client.close()


if __name__ == "__main__":
    main()
