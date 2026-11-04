"""Parked, hashed incremental update; keeps rollback files, never drives."""
import hashlib
import json
from pathlib import Path
import shlex
import stage_avoidance_release as remote

ROOT=Path(__file__).resolve().parents[1]
TARGET='/home/jetson/ros2-smart-car-avoidance-20261028-66c66971'


if __name__=='__main__':
    client=remote.connect('192.168.1.106')
    try:
        print(remote.python(client,"import urllib.request,json\ns=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/status'))\nassert s['mode']=='stop' and s['odom']['ok'] and abs(s['odom'].get('linear_speed_xy',99))<.01 and abs(s['odom']['angular_speed'])<.02\nassert all(s['cmd_vel'][k]==0 for k in ['linear_x','linear_y','angular_z'])\nprint('stationary confirmed')"))
        remote.run(client,'systemctl --user stop smart-car-avoidance-037.service',timeout=30)
        files=list((ROOT/'ros2_ws/src/smart_car_decision/smart_car_decision').glob('*.py'))
        files += [p for p in (ROOT/'web-console/dist').rglob('*') if p.is_file()]
        manifest={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        for path in files:
            rel=str(path.relative_to(ROOT)).replace('\\','/')
            dest=TARGET+'/'+rel
            remote.python(client,f"from pathlib import Path\nimport shutil,hashlib\np=Path({dest!r});p.parent.mkdir(parents=True,exist_ok=True)\nif p.is_file():\n h=hashlib.sha256(p.read_bytes()).hexdigest();b=p.with_name(p.name+'.backup-'+h[:12])\n if not b.exists():shutil.copy2(p,b)")
            with client.open_sftp() as s:s.put(str(path),dest+'.upload')
            remote.python(client,f"from pathlib import Path\nimport hashlib\np=Path({(dest+'.upload')!r})\nassert hashlib.sha256(p.read_bytes()).hexdigest()=={manifest[rel]!r}\np.replace({dest!r})")
        remote.python(client,f"import json\nfrom pathlib import Path\np=Path({(TARGET+'/deployment.json')!r});v=json.loads(p.read_text());v.setdefault('effective_source_hashes',{{}}).update({manifest!r});v.update(revision='0.3.11',driving_accepted=False);p.write_text(json.dumps(v,indent=2))")
        remote.python(client,f'''from pathlib import Path
import hashlib,shutil
p=Path({(TARGET+'/start_test.sh')!r});text=p.read_text()
ram=Path('/dev/shm/insightrover-vision-20261028')
if str(ram) in text:
 (ram/'settings/Ultralytics').mkdir(parents=True,exist_ok=True)
 if 'TEMPORARY_VISION_NOT_PREPARED' not in text:
  b=p.with_name(p.name+'.backup-'+hashlib.sha256(p.read_bytes()).hexdigest()[:12])
  if not b.exists():shutil.copy2(p,b)
  lines=text.splitlines();index=next(i for i,line in enumerate(lines) if line.startswith('exec '))
  guard='test -f '+str(ram/'models/yoloe-11s-traffic.pt')+' && test -f '+str(ram/'packages/ultralytics/__init__.py')+' || {{ echo TEMPORARY_VISION_NOT_PREPARED; exit 21; }}'
  lines.insert(index,guard);p.write_text('\\n'.join(lines)+'\\n')
''')
        helper=(ROOT/'desktop/insightrover_desktop/vehicle_start.sh').read_text()
        print(remote.run(client,'bash -c '+shlex.quote(helper),timeout=45))
        (ROOT/'output/vision-search-20261028/incremental-deployment.json').write_text(json.dumps(manifest,indent=2))
        print('incremental files verified; startup requested, no motion sent')
    finally:client.close()
