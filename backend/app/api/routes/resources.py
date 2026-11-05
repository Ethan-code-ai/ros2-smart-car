from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..deps import get_db, require_permission
from ...gateway import get_robot_gateway
from ...models import User, MapPlace, MapModel, Route
from ...services.resource_resolver import resolve_references
from ...services.robot_context import robot_context_service, vehicle_binding

router = APIRouter(prefix="/resources", tags=["resources"])


class ResourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["business_place", "business_route", "vehicle_place"]
    resource_id: str = Field(min_length=1, max_length=128)
    map_id: str = Field(min_length=1, max_length=128)


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    robot_id: str = Field(min_length=1, max_length=128)
    references: list[ResourceReference] = Field(min_length=1, max_length=30)
    expected_version: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


@router.get("/places")
async def find_places(robot_id: str, keyword: str = Query(default="", max_length=128),
                      db: Session = Depends(get_db), user: User = Depends(require_permission("maps.read"))):
    if "robots.read" not in user.permissions:
        raise HTTPException(403, "缺少机器人读取权限")
    rows = db.scalars(select(MapPlace).join(MapModel, MapPlace.map_id == MapModel.id).where(
        MapModel.active.is_(True), MapPlace.name.contains(keyword, autoescape=True)
    ).order_by(MapPlace.name, MapPlace.id).limit(50)).all()
    items = [{"source": "business_place", "resource_id": row.id, "map_id": row.map_id, "name": row.name} for row in rows]
    binding = vehicle_binding(db)
    context = robot_context_service.context(robot_id, binding)
    vehicle_available = False
    if context["available"]:
        try:
            config = await get_robot_gateway(binding[1]).navigation_request("config")
            calibration = config.get("calibration") or {}
            drawing = calibration.get("drawing") or {}
            vehicle_available = bool(drawing.get("id") and not calibration.get("invalidated")
                                     and calibration.get("map_key") == context["telemetry"].get("active_map_key"))
            if vehicle_available:
                items.extend({"source": "vehicle_place", "resource_id": row["id"],
                              "map_id": drawing["id"], "name": row["name"]}
                             for row in config.get("places", [])[:100] if keyword in row.get("name", ""))
        except (httpx.HTTPError, RuntimeError, ValueError, TypeError, KeyError):
            vehicle_available = False
    return {"items": items, "vehicle_places_available": vehicle_available}


@router.get("/routes")
def find_routes(keyword: str = Query(default="", max_length=128), db: Session = Depends(get_db),
                user: User = Depends(require_permission("routes.read"))):
    if "maps.read" not in user.permissions:
        raise HTTPException(403, "缺少地图读取权限")
    rows = db.scalars(select(Route).join(MapModel, Route.map_id == MapModel.id).where(
        MapModel.active.is_(True), Route.status == "active", Route.name.contains(keyword, autoescape=True)
    ).order_by(Route.name, Route.id).limit(50)).all()
    return {"items": [{"source": "business_route", "resource_id": row.id,
                       "map_id": row.map_id, "name": row.name} for row in rows]}


@router.post("/resolve")
async def resolve(payload: ResolveIn, db: Session = Depends(get_db),
                  user: User = Depends(require_permission("robots.read"))):
    binding = vehicle_binding(db)
    context = robot_context_service.context(payload.robot_id, binding)
    if not context["available"]:
        raise HTTPException(409, "实车状态不可用，请等待重新连接")
    try:
        wire = resolve_references(db, payload.references, user.permissions)
        wire["map_key"] = context["telemetry"].get("active_map_key")
        result = await get_robot_gateway(binding[1]).navigation_request("resolve-resources", wire)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(409, "车端未能核验地点；请检查地图及配准状态") from exc
    if result.get("map_key") != wire["map_key"] or not result.get("resource_version"):
        raise HTTPException(502, "车端资源核验响应无效")
    if payload.expected_version and payload.expected_version != result["resource_version"]:
        raise HTTPException(409, "地点、地图或配准已变化，请重新确认计划")
    return {**result, "references": [ref.model_dump() for ref in payload.references],
            "waypoints": result.get("waypoints", wire.get("points", []))}
