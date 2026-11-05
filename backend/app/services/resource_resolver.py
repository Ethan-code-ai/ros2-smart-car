"""Resolve persisted references. Caller/model coordinates are never accepted."""
import json

from ..models import MapModel, MapPlace, Route


def resolve_references(db, references, permissions):
    if "maps.read" not in permissions:
        raise PermissionError("缺少地图读取权限")
    if not 1 <= len(references) <= 30:
        raise ValueError("请选择 1–30 个资源")
    map_ids = {ref.map_id for ref in references}
    if len(map_ids) != 1:
        raise ValueError("任务必须使用同一张地图")
    map_id = next(iter(map_ids))
    vehicle_refs = [ref for ref in references if ref.source == "vehicle_place"]
    if vehicle_refs:
        if len(vehicle_refs) != len(references):
            raise ValueError("车端地点和业务地点需分别解析后核对地图版本")
        return {"vehicle_place_ids": [ref.resource_id for ref in references], "drawing_id": map_id}
    asset = db.get(MapModel, map_id)
    if not asset or not asset.active or not asset.image_key or not asset.width or not asset.height:
        raise ValueError("地图不存在、已停用或缺少图像")
    points = []

    def place(ident):
        value = db.get(MapPlace, ident)
        if not value or value.map_id != map_id:
            raise ValueError("地点不存在或不属于当前地图")
        points.append({"resource_id": value.id, "name": value.name,
                       "drawing": {"x": value.x, "y": value.y}, "yaw": value.yaw})

    for ref in references:
        if ref.source == "business_place":
            place(ref.resource_id)
        elif ref.source == "business_route":
            if "routes.read" not in permissions:
                raise PermissionError("缺少路线读取权限")
            route = db.get(Route, ref.resource_id)
            if not route or route.map_id != map_id or route.status != "active":
                raise ValueError("路线不存在、不可用或属于其他地图")
            params = json.loads(route.parameters)
            ids = params.get("place_ids") if isinstance(params, dict) else None
            if not isinstance(ids, list) or not ids or any(not isinstance(ident, str) for ident in ids):
                raise ValueError("此路线尚未绑定地点，请在路线参数中配置 place_ids")
            if len(ids) + len(points) > 30:
                raise ValueError("路线最多包含 30 个地点")
            for ident in ids:
                place(ident)
        else:
            raise ValueError("不支持的资源来源")
    if len(points) > 30:
        raise ValueError("路线最多包含 30 个地点")
    return {"drawing": {"id": asset.id, "kind": "uploaded", "name": asset.name,
                        "width": asset.width, "height": asset.height, "revision": asset.image_key},
            "points": points}
