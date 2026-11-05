"""Shared physical-vehicle identity checks for business control entry points."""
from fastapi import HTTPException
from ..models import Robot
from .robot_context import vehicle_binding


def require_bound_vehicle(db, robot_id=None):
    bound_id, address = vehicle_binding(db)
    if (robot_id is not None and robot_id != bound_id) or db.get(Robot, bound_id) is None:
        raise HTTPException(409, "请求车辆不是当前绑定的实车")
    return bound_id, address


def authorize_business_command(command, params, permissions):
    if "robots.control" not in permissions:
        raise HTTPException(403, "缺少机器人控制权限")
    if command in {"patrol_task", "mission_task"}:
        raise HTTPException(409, "请通过持久化业务任务接口操作")
    if command == "set_mode" and params.get("mode") == "mapping" and "maps.manage" not in permissions:
        raise HTTPException(403, "缺少地图管理权限")
