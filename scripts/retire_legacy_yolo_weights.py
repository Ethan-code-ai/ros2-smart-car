"""User-authorized exact legacy weights only; verified off-device backup before unlink."""
import hashlib
import json
from pathlib import Path
import stage_avoidance_release as remote

ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    c=remote.connect('192.168.1.106')
    try:
        result=json.loads(remote.python(c,'''from pathlib import Path
import json,hashlib,urllib.request,yaml
status=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/status'))
assert status['mode']=='stop' and status['vision']['state']=='running'
assert all(status['cmd_vel'][k]==0 for k in ('linear_x','linear_y','angular_z'))
config=yaml.safe_load(Path('/home/jetson/ros2-smart-car-avoidance-20261028-66c66971/ros2_ws/src/smart_car_decision/config/decision.yaml').read_text())
assert 'yoloe-11s-traffic.pt' in config['yolo11_camera_node']['ros__parameters']['model_path']
items=[]
for root in Path('/home/jetson').glob('ros2-smart-car*'):
 if not root.is_dir():continue
 for p in root.rglob('yolo11*.pt'):
  if p.name not in {'yolo11s.pt','yolo11n.pt'} or p.is_symlink() or not p.is_file():continue
  assert p.resolve().is_relative_to(root.resolve())
  items.append({'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size})
print(json.dumps(items))
''',timeout=35))
        directory=ROOT/'output/vision-search-20261028/legacy-weight-backup'
        directory.mkdir(exist_ok=True)
        for item in result:
            p=directory/(item['sha256']+'.pt')
            if not p.exists():
                with c.open_sftp() as s:s.get(item['path'],str(p))
            assert hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256']
        (directory/'restore-manifest.json').write_text(json.dumps(result,indent=2))
        print(remote.python(c,f'''from pathlib import Path
import json,hashlib,shutil
items={result!r}
before=shutil.disk_usage('/home/jetson').free
for item in items:
 p=Path(item['path']);assert p.name in {{'yolo11s.pt','yolo11n.pt'}}
 assert p.is_relative_to('/home/jetson') and p.parts[3].startswith('ros2-smart-car')
 assert not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256']
 p.unlink()
print(json.dumps({{'files_removed':len(items),'free_bytes_after':shutil.disk_usage('/home/jetson').free,'freed_bytes':shutil.disk_usage('/home/jetson').free-before}}))
''',timeout=35))
    finally:c.close()
