import math
from types import SimpleNamespace as NS

from smart_car_decision.avoidance import ScanEvidence,AvoidanceController,ZERO
from smart_car_decision.scan_health import ScanHealth
from smart_car_decision.footprint import Rectangle


def scan(values=None,seq=0):
    return NS(ranges=values or [3.]*1080,range_min=.05,range_max=12.,angle_min=-math.pi,
              angle_increment=2*math.pi/1079,header=NS(stamp=NS(sec=1,nanosec=seq*100000000)))


def test_health_counts_unique_frames_and_requires_three_good_intervals():
    health=ScanHealth()
    assert not health.observe(scan(seq=0),0)['ready_for_inference']
    for i in (1,2):assert not health.observe(scan(seq=i),0)['ready_for_inference']
    assert health.observe(scan(seq=3),0)['ready_for_inference']
    assert health.observe(scan(seq=3),0)['consecutive_healthy']==3
    assert not health.observe(scan([math.inf]*1080,seq=4),0)['ready_for_inference']


def test_gap_range_cap_consistency_and_explicit_invalid_barriers():
    values=[3.]*1080;values[539]=math.inf
    e=ScanEvidence(scan(values),gap_width_m=.01)
    assert e.inferred_ray_count==1 and e.visibility_ranges[539]<1.
    assert e.rays[539] is None and e.sampling_gap_inferred['kind']=='sampling_gap_inferred'
    values[538]=.2;values[540]=3.
    assert ScanEvidence(scan(values),gap_width_m=.01).inferred_ray_count==0
    values=[3.]*1080;values[539]=math.inf;values[540]=math.nan
    assert ScanEvidence(scan(values),gap_width_m=.01).inferred_ray_count==0
    values[540]=3.;kinds=['hit']*1080;kinds[539]='invalid'
    assert ScanEvidence(scan(values),gap_width_m=.01,ray_kinds=kinds).inferred_ray_count==0


def test_far_depth_edge_does_not_invalidate_short_near_field_cap():
    values=[3.]*1080;values[538]=1.;values[539]=math.inf
    e=ScanEvidence(scan(values),gap_width_m=.01)
    assert e.visibility_ranges[539]<1. and e.inferred_ray_count==1
    assert not e.observed(1.,0)


def test_periodic_duplicate_reuses_real_hit_without_mutating_raw_ray():
    values=[3.]*1080;values[0]=math.inf
    e=ScanEvidence(scan(values))
    assert e.rays[0] is None and e.visibility_ranges[0]==3.
    assert e.duplicate_direction_observations==1 and e.inferred_ray_count==0


def test_large_gap_never_becomes_observed():
    values=[3.]*1080;values[530:550]=[math.inf]*20
    assert ScanEvidence(scan(values),gap_width_m=.01).inferred_ray_count==0


def test_confirmed_edge_fingerprint_never_filters_returns_outside_expanded_body():
    body=Rectangle(.0935,.2065,.105)
    anchor=(-.0477947936,.1022008015)
    point=(-.0494655526,.0993169142)
    s=scan([math.hypot(*point)])
    s.angle_min=math.atan2(point[1],point[0]);s.angle_increment=.01
    e=ScanEvidence(s,self_returns=[anchor],footprint=body)
    assert e.self_return_count==1 and not e.points and e.rays==[None]
    outside=(-.0477947936,.108)
    s.ranges=[math.hypot(*outside)];s.angle_min=math.atan2(outside[1],outside[0])
    e=ScanEvidence(s,self_returns=[anchor],footprint=body)
    assert e.self_return_count==0 and len(e.points)==1


def test_unknown_waits_then_rechecks_without_reissuing_intent_and_release_stops():
    c=AvoidanceController();c.footprint=Rectangle(.0935,.2065,.1)
    args=dict(mode='manual',scan_age=0.,pose=(0.,0.,0.),odom_age=0.,confirmed=True)
    motion={**ZERO,'linear_x':.05}
    result=c.step(motion,now=0.,evidence=ScanEvidence(scan([math.inf]*1080)),**args)
    assert result==ZERO and c.state=='waiting_observation'
    assert c.step(motion,now=.1,evidence=ScanEvidence(scan()),**args)['linear_x']>0
    assert c.step(ZERO,now=.2,evidence=ScanEvidence(scan()),**args)==ZERO
    assert c.anchor is None


def test_unknown_parallel_side_does_not_block_straight_but_still_blocks_turn():
    body=Rectangle(.0935,.2065,.105)
    e=ScanEvidence(scan())
    index=round((math.radians(50)-e.start)/e.increment)
    e.rays[index]=None
    assert body.translated_hull(.03,0).check(e,(0,0,0),.05,(1,0),reference=body) is None
    assert body.check(e,(0,0,-.03),.05) is not None
    front=round((0-e.start)/e.increment);e.rays[front]=None
    assert body.translated_hull(.03,0).check(e,(0,0,0),.05,(1,0),reference=body)['kind']=='unknown'
