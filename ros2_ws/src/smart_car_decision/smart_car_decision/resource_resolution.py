"""Read-only resource resolution; never plans or starts motion."""
import hashlib
import json
import math

from .navigation_config import drawing_identity, finite_number, map_key, transform_point, validate_map_goal


def resolve_resources(payload, config, grid, radius):
    calibration = config.get("calibration")
    if not grid or not calibration or calibration.get("invalidated"):
        raise ValueError("当前地图尚未完成配准")
    key = map_key(grid)
    if calibration.get("map_key") != key or payload.get("map_key") != key:
        raise ValueError("地图版本已变化")
    if "vehicle_place_ids" in payload:
        ids = payload["vehicle_place_ids"]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 30 or any(not isinstance(i, str) for i in ids):
            raise ValueError("请选择 1–30 个车端地点")
        drawing = calibration["drawing"]
        if payload.get("drawing_id") != drawing["id"]:
            raise ValueError("车端地点所属图像已变化")
        places = {item["id"]: item for item in config.get("places", [])}
        if any(ident not in places for ident in ids):
            raise ValueError("车端地点不存在或已删除")
        # Ignore any caller coordinates: only persisted points may be resolved.
        payload = {"map_key": key, "drawing": drawing, "points": [
            {"resource_id": ident, "name": places[ident].get("name", ""),
             "drawing": places[ident]["drawing"], "yaw": places[ident].get("yaw", 0)} for ident in ids]}
    if drawing_identity(payload.get("drawing")) != drawing_identity(calibration.get("drawing")):
        raise ValueError("地点所属图像与当前配准版本不一致")
    points = payload.get("points")
    if not isinstance(points, list) or not 1 <= len(points) <= 30:
        raise ValueError("请选择 1–30 个地点")
    matrix = calibration["matrix"]
    drawing = calibration["drawing"]
    goals = []
    for item in points:
        pixel = item["drawing"]
        x, y = finite_number(pixel["x"]), finite_number(pixel["y"])
        if not 0 <= x <= drawing["width"] or not 0 <= y <= drawing["height"]:
            raise ValueError("地点超出图像边界")
        goal = transform_point({"x": x, "y": y}, matrix)
        validate_map_goal(goal, grid, radius)
        yaw = finite_number(item.get("yaw", 0))
        c, s = math.cos(yaw), math.sin(yaw)
        goal["yaw"] = math.atan2(matrix[3]*c + matrix[4]*s, matrix[0]*c + matrix[1]*s)
        goals.append({**goal, "resource_id": item["resource_id"]})
    identity = {"map_key": key, "calibration": calibration, "keepout": grid.get("keepout"),
                "points": points, "radius": radius}
    version = hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return {"map_key": key, "resource_version": version, "goals": goals, "waypoints": points,
            "calibration_matrix": matrix, "calibration_drawing": drawing,
            "control_session_id": grid.get("session_id"), "path_validated": False}
