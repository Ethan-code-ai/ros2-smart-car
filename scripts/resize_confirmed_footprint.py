"""Apply the operator-confirmed 25cm length, retaining the front and width."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
path=ROOT/'output/vision-search-20261028/refined-footprint.json'
raw=path.read_bytes();value=json.loads(raw)
body=value['rectangle']
assert abs(body['front']-.0935)<1e-8 and abs(body['half_width']-.105)<1e-8
assert abs(body['rear']-.2065)<1e-8 or abs(body['rear']-.1565)<1e-8
backup=path.with_name(path.name+'.backup-'+hashlib.sha256(raw).hexdigest()[:12])
if not backup.exists():backup.write_bytes(raw)
body['rear']=.1565
value['operator_measurements_m']['length']=.25
if 'nominal_rectangle' in value:value['nominal_rectangle']['rear']=.1565
anchors=value['self_returns']
value['self_returns']=[p for p in anchors if -.1565<=p[0]<=body['front'] and abs(p[1])<=body['half_width']]
value['operator_length_correction']={
    'date':'2026-10-28','confirmed_total_length_m':.25,
    'rear_reduction_m':.05,'front_unchanged':True,'width_unchanged':True,
    'outside_anchors_removed':len(anchors)-len(value['self_returns'])}
path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'rectangle':body,'correction':value['operator_length_correction']}))
