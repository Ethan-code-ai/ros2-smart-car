"""Immediate scene alarm from the learned fallen class, independent of tracking."""
from __future__ import annotations

import math


def fallen_detections(detections, confidence=.25):
    return [d for d in detections
            if d['class_name'].lower() == 'fallen'
            and math.isfinite(d['score']) and d['score'] >= confidence]


class ImmediateFallAlarm:
    """No confirmation time or hold. Edges only deduplicate an ongoing scene alarm."""
    def __init__(self, confidence=.25):
        self.confidence = confidence
        self.active = False

    def update(self, detections):
        fallen = fallen_detections(detections, self.confidence)
        active = bool(fallen)
        result = dict(active=active, started=active and not self.active,
                      ended=self.active and not active, fallen=fallen)
        self.active = active
        return result
