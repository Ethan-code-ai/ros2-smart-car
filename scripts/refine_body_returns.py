"""Add only interior returns stable across the operator's 15cm manual translation."""
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ros2_ws/src/smart_car_decision'))
from smart_car_decision.footprint import Rectangle

def points(scan):
    result=[];tx,ty,yaw=scan['transform']
    for i,r in enumerate(scan['ranges']):
        if type(r) not in (float,int) or not math.isfinite(r):continue
        a=scan['angle_min']+i*scan['angle_increment']+yaw
        result.append([tx+r*math.cos(a),ty+r*math.sin(a)])
    return result

if __name__=='__main__':
    folder=ROOT/'output/active-avoidance-20261028'
    before=folder/'raw-scan-1793180884-540354200.json'
    after=folder/'raw-scan-1793181215-149175916.json'
    profile=json.loads((ROOT/'output/active-avoidance-20261028-v038/measured-footprint.json').read_text())
    nominal=dict(profile['rectangle'])
    rect=Rectangle(**nominal)
    a,b=points(json.loads(before.read_text())),points(json.loads(after.read_text()))
    earlier=folder/'raw-scan-1793180563-40348610.json'
    later=folder/'raw-scan-1793181579-135300902.json'
    pre=points(json.loads(earlier.read_text()))
    post=points(json.loads(later.read_text()))
    # A return carried with the vehicle across the manual translation identifies
    # a small rigid protrusion/measurement envelope, not a stationary room object.
    edge=[p for p in b if -rect.rear<p[0]<rect.front and rect.half_width<abs(p[1])<=rect.half_width+.005
          and any(math.dist(p,q)<.003 for q in pre)
          and any(math.dist(p,q)<.003 for q in post)]
    if edge:
        width=min(nominal['half_width']+.005,math.ceil((max(abs(p[1]) for p in edge)+.002)*1000)/1000)
        profile['rectangle']['half_width']=width
        profile['nominal_rectangle']=nominal
        profile['boundary_refinement']={'reason':'rigid returns stable across manual translation','margin_added_m':width-nominal['half_width']}
        rect=Rectangle(**profile['rectangle'])
    anchors=[p for p in b if rect.clearance(p)<-.002 and any(rect.clearance(q)<-.002 and math.dist(p,q)<.008 for q in a)]
    anchors += [p for p in edge if rect.clearance(p)<=0]
    profile['self_returns']+=anchors
    profile['manual_translation_refinement']={'distance_m':.15,'anchors_added':len(anchors),
        'sources':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (before,after,earlier,later)}}
    path=ROOT/'output/vision-search-20261028/refined-footprint.json'
    path.write_text(json.dumps(profile,indent=2))
    print(json.dumps(profile['manual_translation_refinement'],indent=2))
