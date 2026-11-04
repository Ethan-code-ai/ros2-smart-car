"""Stationary incremental deployment of measured geometry, with hashed backups."""
import hashlib,json
from pathlib import Path
import stage_avoidance_release as remote

ROOT=Path(__file__).resolve().parents[1]
TARGET='/home/jetson/ros2-smart-car-avoidance-20261028-66c66971'
client=remote.connect('192.168.1.106')
try:
    remote.python(client,"import json,urllib.request\ns=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/status',timeout=2))\nassert s['mode']=='stop' and abs(s['odom']['linear_speed'])<.01\nprint('stationary')")
    remote.run(client,'systemctl --user stop smart-car-avoidance-037.service',timeout=40)
    hashes={}
    for name in ('avoidance.py','footprint.py','decision_controller.py'):
        rel='ros2_ws/src/smart_car_decision/smart_car_decision/'+name
        remote.python(client,f'''from pathlib import Path
import hashlib,shutil
p=Path({(TARGET+'/'+rel)!r})
if p.exists():
 h=hashlib.sha256(p.read_bytes()).hexdigest();b=p.with_name(p.name+'.backup-'+h[:12])
 if not b.exists():shutil.copy2(p,b)
print('backup checked')
''')
        with client.open_sftp() as s:s.put(str(ROOT/rel),TARGET+'/'+rel)
        hashes[rel]=hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()
    profile=ROOT/'output/active-avoidance-20261028-v038/measured-footprint.json'
    profile_path='/home/jetson/.ros/insightrover/avoidance-footprint.json'
    remote.python(client,f'''from pathlib import Path
import hashlib,shutil
p=Path({profile_path!r});p.parent.mkdir(parents=True,exist_ok=True)
if p.exists():
 h=hashlib.sha256(p.read_bytes()).hexdigest();b=p.with_name(p.name+'.backup-'+h[:12])
 if not b.exists():shutil.copy2(p,b)
print('profile backup checked')
''')
    with client.open_sftp() as s:s.put(str(profile),profile_path)
    result=remote.python(client,f'''from pathlib import Path
import hashlib,json
root=Path({TARGET!r});h={hashes!r}
for rel,digest in h.items():assert hashlib.sha256((root/rel).read_bytes()).hexdigest()==digest
p=root/'deployment.json';d=json.loads(p.read_text());d['effective_source_hashes'].update(h)
d['revision']='0.3.9-measured-footprint';d['footprint_profile_sha256']=hashlib.sha256(Path({profile_path!r}).read_bytes()).hexdigest()
p.write_text(json.dumps(d,indent=2));print(json.dumps(d))
''')
    (ROOT/'output/active-avoidance-20261028-v038/measured-deployment.json').write_text(result,encoding='utf-8')
    print('measured modules and profile verified')
finally:client.close()
