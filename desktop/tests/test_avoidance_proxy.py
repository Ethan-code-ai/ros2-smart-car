import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from insightrover_desktop.server import CAR_PATH


def test_avoidance_is_in_desktop_vehicle_proxy_allowlist():
    assert CAR_PATH.fullmatch('avoidance')
    assert not CAR_PATH.fullmatch('avoidance-extra')
    assert not CAR_PATH.fullmatch('../avoidance')


def test_vision_search_proxy_routes_and_boundaries():
    for route in ('search','search/start','search/action','vision/status','vision/settings','vision/events'):
        assert CAR_PATH.fullmatch(route)
    for route in ('search-extra','../search','vision/../../secret'):
        assert not CAR_PATH.fullmatch(route)
