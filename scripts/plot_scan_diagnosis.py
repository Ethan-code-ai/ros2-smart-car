"""Render recorded raw scan, measured body and unknown sector for inspection."""
import json,math
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle,Polygon

ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'tests/fixtures/measured-scan-20261028.json'
d=json.loads(p.read_text(encoding='utf-8'));cfg=d['calibration'];f=cfg['rectangle']
tx,ty,yaw=d['transform'];points=[]
for i,value in enumerate(d['ranges']):
    value=float(value)
    if math.isfinite(value):
        a=d['angle_min']+i*d['angle_increment']+yaw
        points.append((tx+value*math.cos(a),ty+value*math.sin(a)))
plt.rcParams['font.sans-serif']=['Microsoft YaHei','SimHei','DejaVu Sans']
fig,ax=plt.subplots(figsize=(7,7),dpi=160)
ax.scatter([-y for x,y in points],[x for x,y in points],s=9,c='#3282b8',zorder=4,label='原始有效回波')
ax.add_patch(Rectangle((-f['half_width'],-f['rear']),2*f['half_width'],f['front']+f['rear'],fc='#a6c8e0',ec='#174c70',label='实测车体轮廓'))
ax.add_patch(Rectangle((-f['half_width']-.15,-f['rear']-.15),2*f['half_width']+.30,f['front']+f['rear']+.30,fill=False,ec='#c36b3c',ls='--',label='15 cm 保护区的矩形上界'))
sector=[(-ty,tx)]
for i in range(227,238):
    a=d['angle_min']+i*d['angle_increment']+yaw
    sector.append((-ty-.6*math.sin(a),tx+.6*math.cos(a)))
sector.append((-ty,tx))
ax.add_patch(Polygon(sector,fc='#f5ba46',alpha=.45,label='持续缺测方向（未知，不是障碍点）'))
ax.scatter([-ty],[tx],marker='^',s=70,c='#183446',label='雷达位置')
ax.set(xlim=(-.65,.65),ylim=(-.65,.75),xlabel='横向坐标：左侧在图左（m）',ylabel='前向坐标：车头朝上（m）',title='同帧雷达、实测轮廓与缺测区域')
ax.set_aspect('equal');ax.grid(alpha=.2);ax.legend(loc='lower right',fontsize=8)
fig.tight_layout()
out=ROOT/'output/active-avoidance-20261028-v038/scan-diagnosis.png'
fig.savefig(out);print(out)
