"""Build an auditable local fingerprint from the operator's stationary calibration."""
import hashlib,json,math,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ros2_ws/src/smart_car_decision'))
from smart_car_decision.footprint import Rectangle

folder=ROOT/'output/active-avoidance-20261028'
names=['raw-scan-1793162534-942807759.json','raw-scan-1793162614-606064365.json','raw-scan-1793162814-579156283.json']
frames=[json.loads((folder/n).read_text(encoding='utf-8')) for n in names]
f=Rectangle(.0935,.2065,.10)
inside=[[p for p in d['near_points'] if f.clearance(p)<=.002 and p[0]<-.02] for d in frames]
anchors=[p for group in inside for p in group if f.clearance(p)<=0
         and all(any(math.dist(p,q)<=.008 for q in points) for points in inside)]
record={'rectangle':{'front':f.front,'rear':f.rear,'half_width':f.half_width},
        'operator_measurements_m':{'length':.30,'width':.20,'laser_to_front':.05},
        'self_returns':anchors,'match_tolerance_m':.006,
        'method':'operator reported physical translation; local stable points inside measured silhouette only; shadows remain unknown',
        'evidence':{n:hashlib.sha256((folder/n).read_bytes()).hexdigest() for n in names}}
out=ROOT/'output/active-avoidance-20261028-v038/measured-footprint.json'
out.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'profile':str(out),'anchors':len(anchors),'rectangle':record['rectangle']}))
