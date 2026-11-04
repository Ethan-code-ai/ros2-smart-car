"""Start the parked release and record health. Never sends a motion command."""
import json
from pathlib import Path
import stage_avoidance_release as remote

ROOT = Path(__file__).resolve().parents[1]
client = remote.connect('192.168.1.106')
try:
    helper = (ROOT/'desktop/insightrover_desktop/vehicle_start.sh').read_text(encoding='utf-8')
    import shlex
    print(remote.run(client, 'bash -c '+shlex.quote(helper), timeout=45))
    source = '''import json,time,urllib.request
result={}
for attempt in range(30):
 try:
  result=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/status',timeout=2))
  if (result.get('avoidance',{}).get('build_id') and result.get('avoidance',{}).get('confirmed')
      and result.get('cmd_vel',{}).get('ok')): break
 except Exception as e: result={'error':str(e)}
 time.sleep(1)
print(json.dumps(result,ensure_ascii=False))
'''
    result = json.loads(remote.python(client, source, timeout=60))
    assert result['mode'] == 'stop'
    assert all(result['cmd_vel'][k] == 0 for k in ('linear_x','linear_y','angular_z'))
    assert result['avoidance']['build_id'] == 'avoidance-0.3.9-measured-footprint'
    remote.python(client, '''import json
from pathlib import Path
p=Path('/home/jetson/ros2-smart-car-avoidance-20261028-66c66971/deployment.json')
d=json.loads(p.read_text()); d.update(activated=True,status='active_stationary_not_driving_accepted')
p.write_text(json.dumps(d,indent=2))
print('activation recorded')
''')
    (ROOT/'output/active-avoidance-20261028/vehicle-live-status.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('mode','cmd_vel','avoidance')},ensure_ascii=False))
finally:
    client.close()
