import math

from smart_car_decision.avoidance import ScanEvidence,ZERO
from smart_car_decision.footprint import Rectangle
from test_sampling_gap_recovery import scan
from test_holonomic_avoidance import controller


def evidence(values,kinds=None,enabled=True):
    return ScanEvidence(scan(values),ray_kinds=kinds or ['hit' if math.isfinite(v) else 'unknown' for v in values],
                        assume_missing_clear=enabled)


def test_operator_policy_allows_large_missing_sector_without_rewriting_raw_scan():
    values=[3.]*1080;values[510:570]=[math.inf]*60
    e=evidence(values)
    assert e.observed(.5,0.)
    assert all(v is None for v in e.rays[510:570])
    assert e.assumed_clear['rays']==60 and not e.assumed_clear['device_proven']
    assert not evidence(values,enabled=False).observed(.5,0.)


def test_policy_never_clears_real_obstacle_nan_negative_infinity_or_invalid():
    values=[3.]*1080;values[539]=.1
    e=evidence(values)
    c=controller()
    assert c.step({**ZERO,'linear_x':.1},mode='navigation',now=0,evidence=e,scan_age=.1,
                  pose=(0,0,0),odom_age=.1)==ZERO
    assert c.coverage['kind']=='obstacle'
    for missing in (math.nan,-math.inf,.0):
        values[539]=missing
        assert evidence(values).visibility_ranges[539] is None
    for kind in ('invalid','body_occluded'):
        values[539]=math.inf;kinds=['hit']*1080;kinds[539]=kind
        assert evidence(values,kinds).visibility_ranges[539] is None


def test_body_interior_is_filtered_without_fingerprint_and_shadow_uses_separate_policy():
    values=[3.]*1080;values[539]=.05
    s=scan(values);angle=s.angle_min+539*s.angle_increment
    point=(.05*math.cos(angle),.05*math.sin(angle))
    e=ScanEvidence(s,self_returns=[point],footprint=Rectangle(.0935,.1565,.105),
                   ray_kinds=['hit']*1080,assume_missing_clear=True)
    assert e.self_return_count==1 and e.rays[539] is None
    assert e.visibility_ranges[539]==s.range_max and e.assumed_clear['body_shadow_rays']==1
    conservative=ScanEvidence(s,self_returns=[point],footprint=Rectangle(.0935,.1565,.105),
                             ray_kinds=['hit']*1080)
    assert conservative.visibility_ranges[539] is None
    uncalibrated=ScanEvidence(s,footprint=Rectangle(.0935,.1565,.105),
                             ray_kinds=['hit']*1080,assume_missing_clear=True)
    assert uncalibrated.self_return_count==1 and len(uncalibrated.points)==1079
    assert uncalibrated.assumed_clear['body_shadow_rays']==1
    assert uncalibrated.assumed_clear['body_filter_policy']=='measured_rectangle_interior'


def test_body_mask_keeps_boundary_and_exterior_buffer_obstacles():
    body=Rectangle(.0935,.1565,.105)
    for distance in (.0935,.0945,.12):
        values=[3.]*1080;values[540]=distance
        s=scan(values);s.angle_increment=math.tau/1080
        e=ScanEvidence(s,footprint=body,ray_kinds=['hit']*1080,assume_missing_clear=True)
        assert e.self_return_count==0 and len(e.points)==1080
        c=controller()
        assert c.step({**ZERO,'linear_x':.042},mode='navigation',now=0,evidence=e,scan_age=.1,
                      pose=(0,0,0),odom_age=.1)==ZERO
        assert c.coverage['kind']=='obstacle'


def test_body_mask_uses_transformed_body_coordinates():
    values=[3.]*1080;values[540]=.06
    s=scan(values);s.angle_increment=math.tau/1080
    body=Rectangle(.0935,.1565,.105)
    inside=ScanEvidence(s,footprint=body,ray_kinds=['hit']*1080,assume_missing_clear=True)
    outside=ScanEvidence(s,transform=(.05,0,0),footprint=body,
                         ray_kinds=['hit']*1080,assume_missing_clear=True)
    assert inside.self_return_count==1 and outside.self_return_count==0
    assert any(math.dist(p,(.11,0))<1e-8 for p in outside.points)


def test_absent_provenance_or_cropped_scan_never_enables_policy():
    values=[3.]*1080;values[539]=math.inf
    e=ScanEvidence(scan(values),assume_missing_clear=True)
    assert e.visibility_ranges[539] is None
    s=scan(values);s.angle_increment=.001
    assert evidence(values).assumed_clear['rays']==1
    assert ScanEvidence(s,ray_kinds=['unknown']*1080,assume_missing_clear=True).assumed_clear['rays']==0
