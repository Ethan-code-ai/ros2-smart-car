"""Validate explicit driver provenance; infinity alone never means clear space."""
import math

KINDS = {'hit','no_return','unsampled','invalid','unknown','body_occluded'}


def visibility(scan, packet, profile):
    count=len(scan.ranges)
    unknown={'source':'legacy_scan','unknown_rays':sum(not math.isfinite(v) for v in scan.ranges)}
    if not packet:
        return {},unknown
    try:
        if (packet['stamp'] != [scan.header.stamp.sec,scan.header.stamp.nanosec]
                or packet['frame_id']!=scan.header.frame_id or len(packet['kinds'])!=count
                or any(k not in KINDS for k in packet['kinds'])):
            return {},{**unknown,'source':'mismatched_provenance'}
        summary={'source':'driver_provenance','counts':{k:packet['kinds'].count(k) for k in KINDS}}
        approved=(profile.get('verified') is True and profile.get('driver_sha256')==packet.get('driver_sha256')
                  and bool(profile.get('evidence_sha256')) and packet.get('healthy') is True)
        distance=profile.get('usable_range_m',0)
        if not approved or type(distance) not in (float,int) or not math.isfinite(distance) or not 0<distance<=scan.range_max:
            return {},summary
        return {i:distance for i,k in enumerate(packet['kinds']) if k=='no_return' and scan.ranges[i]==math.inf},summary
    except (KeyError,TypeError,AttributeError):
        return {},{**unknown,'source':'invalid_provenance'}
