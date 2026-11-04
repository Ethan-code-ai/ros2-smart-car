"""Three independent healthy scans gate bounded sampling-gap inference."""
import math


class ScanHealth:
    def __init__(self):
        self.previous = None
        self.streak = 0
        self.status = {'ready_for_inference':False,'consecutive_healthy':0}

    def observe(self, scan, age):
        stamp=(scan.header.stamp.sec,scan.header.stamp.nanosec)
        if self.previous is not None and stamp==self.previous:
            return dict(self.status)
        at=stamp[0]+stamp[1]/1e9
        dt=at-(self.previous[0]+self.previous[1]/1e9) if self.previous is not None else None
        self.previous=stamp
        ratio=sum(math.isfinite(v) and scan.range_min<=v<=scan.range_max for v in scan.ranges)/max(1,len(scan.ranges))
        healthy=(0<=age<=.6 and dt is not None and 0<dt<=.200001 and ratio>=.6)
        self.streak=self.streak+1 if healthy else 0
        self.status={'ready_for_inference':self.streak>=3,'consecutive_healthy':self.streak,
                     'valid_ratio':ratio,'scan_rate_hz':1/dt if dt is not None and dt>0 else None}
        return dict(self.status)
