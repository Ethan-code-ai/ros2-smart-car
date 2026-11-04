"""One operator-authorized short manual probe, retaining every safety guard."""
import json
import time
import argparse
from pathlib import Path
import stage_avoidance_release as remote

SOURCE = r'''
import json,time,urllib.request
base='http://127.0.0.1:8080'
def get():
 return json.load(urllib.request.urlopen(base+'/api/status',timeout=1))
def post(path,body):
 req=urllib.request.Request(base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'},method='POST')
 return json.load(urllib.request.urlopen(req,timeout=1))
samples=[]
initial=get()
assert initial['mode']=='stop' and not initial['emergency_stop']
assert initial['avoidance']['confirmed'] and initial['avoidance']['available']
assert initial['avoidance'].get('motion_model')=='holonomic'
assert initial['odom']['ok'] and abs(initial['odom']['linear_speed'])<.01
try:
 post('/api/speed',{'scale':.1})
 post('/api/mode',{'mode':'manual'})
 time.sleep(.15)
 started=time.monotonic()
 for i in range(8):
  if time.monotonic()-started>.45: break
  post('/api/command',{'command':'forward'})
  time.sleep(.05)
  s=get()
  samples.append({k:s[k] for k in ('mode','odom','cmd_vel','avoidance','last_command')})
  if abs(s['cmd_vel']['linear_x'])>.05 or abs(s['odom'].get('linear_speed_xy',99))>.05:break
  if s['emergency_stop'] or not s['avoidance']['available']: break
finally:
 try: post('/api/command',{'command':'stop'})
 finally: post('/api/mode',{'mode':'stop'})
time.sleep(.4)
final=get()
assert final['mode']=='stop'
assert all(final['cmd_vel'][k]==0 for k in ('linear_x','linear_y','angular_z'))
assert final['odom']['ok'] and abs(final['odom'].get('linear_speed_xy',99))<.01 and abs(final['odom']['angular_speed'])<.02
print(json.dumps({'samples':samples,'final':{k:final[k] for k in ('mode','odom','cmd_vel','avoidance')},'safety_bypassed':False},ensure_ascii=False))
'''
parser=argparse.ArgumentParser()
parser.add_argument('--command',choices=['forward','left','right'],default='forward')
args=parser.parse_args()
SOURCE=SOURCE.replace("{'command':'forward'}",repr({'command':args.command}))
SOURCE=SOURCE.replace("abs(s['cmd_vel']['linear_x'])>.05", "max(abs(s['cmd_vel']['linear_x']),abs(s['cmd_vel']['linear_y']))>.05")
client=remote.connect('192.168.1.106')
try:
    result=json.loads(remote.python(client,SOURCE,timeout=15))
    result['probe_command']=args.command
    result['missing_data_policy']=result['final']['avoidance'].get('missing_data_policy')
    result['assumed_clear']=result['final']['avoidance'].get('assumed_clear')
    path=Path(__file__).resolve().parents[1]/f'output/active-avoidance-20261028/bounded-probe-{time.time_ns()}.json'
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'record':str(path),'command':args.command,
        'missing_data_policy':result['missing_data_policy'],'assumed_clear':result['assumed_clear'],
        'samples':[{'cmd_vel':s['cmd_vel'],'odom':s['odom']['linear_speed_xy'],
                    'state':s['avoidance']['state'],'coverage':s['avoidance']['coverage']} for s in result['samples']],
        'final':{k:result['final'][k] for k in ('mode','cmd_vel','odom')}},ensure_ascii=False,indent=2))
finally:
    client.close()
