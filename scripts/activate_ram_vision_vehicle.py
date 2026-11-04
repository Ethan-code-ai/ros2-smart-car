"""Upload verified local official assets into approved RAM storage, enable parked preview."""
import hashlib
from pathlib import Path
import shlex
import stage_avoidance_release as remote

ROOT=Path(__file__).resolve().parents[1]
RAM='/dev/shm/insightrover-vision-20261028'
if __name__=='__main__':
    c=remote.connect('192.168.1.106')
    try:
        print(remote.python(c,f'''from pathlib import Path
import os,signal
for p in Path('/proc').iterdir():
 if not p.name.isdigit():continue
 try:
  args=(p/'cmdline').read_bytes().split(b'\\0')
  if b'prepare_yoloe11.py' in args and {str(RAM+'/models').encode()!r} in args:
   os.kill(int(p.name),signal.SIGTERM);print('stopped slow model preparation',p.name)
 except (OSError,ProcessLookupError):pass
'''))
        for name in ('yoloe-11s-seg.pt','mobileclip_blt.ts'):
            source=ROOT/'output/vision-search-20261028'/name
            assert source.is_file()
            expected=hashlib.sha256(source.read_bytes()).hexdigest()
            dest=RAM+'/models/'+name
            with c.open_sftp() as s:s.put(str(source),dest+'.upload')
            print(remote.python(c,f"from pathlib import Path\nimport hashlib\np=Path({(dest+'.upload')!r});assert hashlib.sha256(p.read_bytes()).hexdigest()=={expected!r};p.replace({dest!r});print({name!r}+' checksum verified')"))
        print(remote.run(c,f'cd {RAM}; PYTHONPATH={RAM}/packages TMPDIR={RAM}/tmp YOLO_AUTOINSTALL=false python3 prepare_yoloe11.py --directory {RAM}/models',timeout=180))
        remote.python(c,"import json,urllib.request\ns=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/status'));assert s['mode']=='stop' and all(s['cmd_vel'][k]==0 for k in ['linear_x','linear_y','angular_z'])")
        remote.run(c,'systemctl --user stop smart-car-avoidance-037.service',timeout=30)
        print(remote.python(c,f'''from pathlib import Path
import json,shutil,hashlib
p=Path('/home/jetson/.ros/insightrover/vision.json')
if p.exists():
 b=p.with_name(p.name+'.backup-'+hashlib.sha256(p.read_bytes()).hexdigest()[:12])
 if not b.exists():shutil.copy2(p,b)
v=json.loads(p.read_text()) if p.exists() else {{}}
v['model_path']={str(RAM+'/models/yoloe-11s-seg.pt')!r};p.write_text(json.dumps(v))
entry=Path('/home/jetson/ros2-smart-car-avoidance-20261028-66c66971/start_test.sh')
s=entry.read_text();b=entry.with_name(entry.name+'.backup-'+hashlib.sha256(entry.read_bytes()).hexdigest()[:12])
if not b.exists():shutil.copy2(entry,b)
lines=s.splitlines();indices=[i for i,line in enumerate(lines) if line.startswith('exec ')]
assert len(indices)==1, 'Review entry before prefixing RAM dependencies'
lines.insert(indices[0],'export PYTHONPATH="{RAM}/packages:${{PYTHONPATH:-}}"')
entry.write_text('\\n'.join(lines)+'\\n');print('RAM preview configured; no performance certificate created')
'''))
        helper=(ROOT/'desktop/insightrover_desktop/vehicle_start.sh').read_text()
        print(remote.run(c,'bash -c '+shlex.quote(helper),timeout=45))
    finally:c.close()
