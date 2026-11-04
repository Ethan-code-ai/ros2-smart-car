"""Install only the documented manual-translation calibration, while parked."""
from pathlib import Path
import hashlib
import shlex
import stage_avoidance_release as remote

ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    c=remote.connect('192.168.1.106')
    try:
        remote.python(c,"import json,urllib.request\ns=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/status'));assert s['mode']=='stop' and all(s['cmd_vel'][k]==0 for k in ['linear_x','linear_y','angular_z']);assert s['odom']['ok'] and abs(s['odom'].get('linear_speed_xy',99))<.01 and abs(s['odom']['angular_speed'])<.02")
        remote.run(c,'systemctl --user stop smart-car-avoidance-037.service',timeout=30)
        path='/home/jetson/.ros/insightrover/avoidance-footprint.json'
        remote.python(c,f"from pathlib import Path\nimport hashlib,shutil\np=Path({path!r});b=p.with_name(p.name+'.backup-'+hashlib.sha256(p.read_bytes()).hexdigest()[:12])\nif not b.exists():shutil.copy2(p,b)")
        local=ROOT/'output/vision-search-20261028/refined-footprint.json'
        with c.open_sftp() as s:s.put(str(local),path+'.upload')
        digest=hashlib.sha256(local.read_bytes()).hexdigest()
        print(remote.python(c,f"from pathlib import Path\nimport hashlib,json\np=Path({(path+'.upload')!r});assert hashlib.sha256(p.read_bytes()).hexdigest()=={digest!r};v=json.loads(p.read_text());assert v['rectangle']['half_width']<=.105;p.replace({path!r});print(v['rectangle'])"))
        helper=(ROOT/'desktop/insightrover_desktop/vehicle_start.sh').read_text()
        print(remote.run(c,'bash -c '+shlex.quote(helper),timeout=45))
    finally:c.close()
