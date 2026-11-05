import copy
import pytest
from smart_car_decision.navigation_config import DEFAULT_DRAWING, map_key
from smart_car_decision.resource_resolution import resolve_resources


def inputs():
    grid = {"header": {"frame_id": "map"}, "asset_digest": "asset-a",
            "info": {"width": 100, "height": 100, "resolution": .1,
                     "origin": {"x": 0., "y": 0., "yaw": 0.}}, "data": [0]*10000}
    drawing = {**DEFAULT_DRAWING, "kind": "uploaded", "id": "map", "revision": "image-a"}
    calibration = {"map_key": map_key(grid), "matrix": [.01, 0, 0, 0, .01, 0],
                   "drawing": dict(drawing)}
    payload = {"map_key": map_key(grid), "drawing": dict(drawing),
               "points": [{"resource_id": "a", "drawing": {"x": 200, "y": 200}, "yaw": 0}]}
    return payload, {"calibration": calibration}, grid


def test_pixels_resolve_to_meters_without_mutating_state():
    payload, config, grid = inputs()
    before = copy.deepcopy((payload, config, grid))
    result = resolve_resources(payload, config, grid, .25)
    assert result["goals"] == [{"x": 2., "y": 2., "yaw": 0., "resource_id": "a"}]
    assert not result["path_validated"]
    assert before == (payload, config, grid)


@pytest.mark.parametrize("changed", ["map", "drawing", "obstacle", "invalidated"])
def test_changed_or_unsafe_resources_are_rejected(changed):
    payload, config, grid = inputs()
    if changed == "map":
        grid["asset_digest"] = "asset-b"
    elif changed == "drawing":
        payload["drawing"]["revision"] = "different-image"
    elif changed == "obstacle":
        grid["data"][20*100+20] = 100
    else:
        config["calibration"]["invalidated"] = True
    with pytest.raises(ValueError):
        resolve_resources(payload, config, grid, .25)


def test_changed_calibration_changes_confirmation_version():
    payload, config, grid = inputs()
    previous = resolve_resources(payload, config, grid, .25)["resource_version"]
    config["calibration"]["matrix"][2] += .1
    assert resolve_resources(payload, config, grid, .25)["resource_version"] != previous


def test_vehicle_place_uses_saved_coordinates_and_detects_edits():
    _, config, grid = inputs()
    config["places"] = [{"id": "desk", "name": "实验台", "drawing": {"x": 200, "y": 200}, "yaw": 0}]
    payload = {"vehicle_place_ids": ["desk"], "drawing_id": "map", "map_key": map_key(grid),
               "points": [{"drawing": {"x": 9999, "y": 9999}}]}
    result = resolve_resources(payload, config, grid, .25)
    assert result["goals"][0]["x"] == 2
    config["places"][0]["drawing"]["x"] = 300
    assert resolve_resources(payload, config, grid, .25)["resource_version"] != result["resource_version"]
    config["places"] = []
    with pytest.raises(ValueError, match="已删除"):
        resolve_resources(payload, config, grid, .25)


def test_vehicle_place_requires_matching_drawing():
    _, config, grid = inputs()
    with pytest.raises(ValueError, match="图像已变化"):
        resolve_resources({"vehicle_place_ids": ["desk"], "drawing_id": "other", "map_key": map_key(grid)}, config, grid, .25)
