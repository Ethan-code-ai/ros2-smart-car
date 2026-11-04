"""Switch only the isolated lidar overlay while stopped; retain entry backup."""
from pathlib import Path
import shlex
import stage_avoidance_release as remote

ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    c=remote.connect('192.168.1.106')
    try:
        print(remote.python(c,"import urllib.request,json\ns=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/status'))\nassert s['mode']=='stop' and all(s['cmd_vel'][k]==0 for k in ['linear_x','linear_y','angular_z'])\nprint('stationary verified')"))
        remote.run(c,'systemctl --user stop smart-car-avoidance-037.service',timeout=30)
        print(remote.python(c,'''from pathlib import Path
import hashlib,shutil
p=Path('/home/jetson/ros2-smart-car-avoidance-20261028-66c66971/start_test.sh')
s=p.read_text();old='/home/jetson/insightrover-full-scan-20261028/install/setup.bash';new='/home/jetson/insightrover-scan-provenance-20261028/install/setup.bash'
assert s.count(old)==1 and Path(new).exists()
b=p.with_name(p.name+'.backup-'+hashlib.sha256(p.read_bytes()).hexdigest()[:12])
if not b.exists():shutil.copy2(p,b)
p.write_text(s.replace(old,new))
print('private overlay switched, prior entry saved')
'''))
        helper=(ROOT/'desktop/insightrover_desktop/vehicle_start.sh').read_text()
        print(remote.run(c,'bash -c '+shlex.quote(helper),timeout=45))
    finally:c.close()
