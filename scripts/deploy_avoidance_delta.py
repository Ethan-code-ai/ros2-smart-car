"""Parked, checksummed module update in the isolated 0.3.7 release."""
import hashlib,json
from pathlib import Path
import stage_avoidance_release as remote

ROOT=Path(__file__).resolve().parents[1]
TARGET='/home/jetson/ros2-smart-car-avoidance-20261028-66c66971'
REL='ros2_ws/src/smart_car_decision/smart_car_decision/avoidance.py'
client=remote.connect('192.168.1.106')
try:
    print(remote.python(client,'''import json,urllib.request
s=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/status',timeout=2))
assert s['mode']=='stop' and abs(s['odom']['linear_speed'])<.01
assert all(s['cmd_vel'][k]==0 for k in ('linear_x','linear_y','angular_z'))
print('stationary verified')
'''))
    remote.run(client,'systemctl --user stop smart-car-avoidance-037.service',timeout=40)
    print(remote.python(client,f'''from pathlib import Path
import hashlib,shutil
p=Path({(TARGET+'/'+REL)!r}); digest=hashlib.sha256(p.read_bytes()).hexdigest()
backup=p.with_name(p.name+'.backup-'+digest[:12])
if not backup.exists(): shutil.copy2(p,backup)
assert hashlib.sha256(backup.read_bytes()).hexdigest()==digest
print('previous module preserved '+digest)
'''))
    with client.open_sftp() as s:s.put(str(ROOT/REL),TARGET+'/'+REL)
    web_hashes={}
    with client.open_sftp() as s:
        for path in (ROOT/'web-console/dist').rglob('*'):
            if path.is_file():
                rel=path.relative_to(ROOT).as_posix()
                s.put(str(path),TARGET+'/'+rel)
                web_hashes[rel]=hashlib.sha256(path.read_bytes()).hexdigest()
    expected=hashlib.sha256((ROOT/REL).read_bytes()).hexdigest()
    result=remote.python(client,f'''import json,hashlib
from pathlib import Path
root=Path({TARGET!r});p=root/{REL!r}
assert hashlib.sha256(p.read_bytes()).hexdigest()=={expected!r}
r=root/'deployment.json';d=json.loads(r.read_text())
d['effective_source_hashes'][{REL!r}]={expected!r}
web={web_hashes!r}
for name,checksum in web.items():
 assert hashlib.sha256((root/name).read_bytes()).hexdigest()==checksum,name
d['effective_source_hashes'].update(web)
d['startup_sha256']=hashlib.sha256((root/'start_test.sh').read_bytes()).hexdigest()
d['driver_overlay']='/home/jetson/insightrover-full-scan-20261028'
d['revision']='0.3.8-cloud-aligned-directional-prefix-diagnostics'
d['activated']=False;d['status']='delta_verified_parked'
r.write_text(json.dumps(d,indent=2))
print(json.dumps(d))
''')
    (ROOT/'output/active-avoidance-20261028-v037/effective-deployment.json').write_text(result,encoding='utf-8')
    print('new module SHA256 '+expected)
finally:client.close()
