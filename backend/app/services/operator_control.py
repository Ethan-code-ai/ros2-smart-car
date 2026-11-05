"""Scoped manual and mapping control shared by both host APIs."""
import asyncio
from uuid import uuid4

import httpx
from fastapi import HTTPException

from ..gateway import get_robot_gateway
from .control_access import require_bound_vehicle
from .control_leases import control_lease_manager


DRIVE_COMMANDS = frozenset(("forward", "backward", "left", "right", "turn_l", "turn_r", "rotate_left", "rotate_right", "stop"))


async def select_operator_mode(db, binding, user, path, mode, stop):
    manager = control_lease_manager
    permissions = {"robots.control"} | ({"maps.manage"} if mode == "mapping" else set())
    if not permissions <= user.permissions:
        raise HTTPException(403, "缺少机器人控制或建图权限")
    if mode not in {"manual", "mapping"}:
        raise HTTPException(409, "该模式尚未接入受控运动入口")
    if not binding[1]:
        raise HTTPException(503, "未配置车端地址")
    generation = manager.generation + 1
    stopped = await stop()
    if (stopped.get("controller_confirmed") is not True or stopped.get("stationary_confirmed") is not True
            or stopped.get("errors") or generation != manager.generation
            or require_bound_vehicle(db, binding[0]) != binding):
        raise HTTPException(409, "未确认车辆静止或控制权已变化，请核对后重试")
    run_id, lease = uuid4().hex, None
    try:
        lease = await manager.acquire_for_run(binding, user.id, "robots.control", run_id,
                                              additional_permissions=permissions, purpose=mode)
        if manager.current is not lease or require_bound_vehicle(db, binding[0]) != binding:
            raise HTTPException(409, "车辆绑定或控制权已变化")
        wire = {"control_lease": lease.identity()}
        if path == "mode":
            wire["mode"] = mode
        result = await get_robot_gateway(binding[1]).operator_request(path, wire)
        if (not isinstance(result, dict) or result.get("ok") is not True
                or (path == "mode" and result.get("mode") != mode) or manager.current is not lease
                or require_bound_vehicle(db, binding[0]) != binding):
            raise HTTPException(409, "车端模式或控制权未确认")
        return {**result, "drive_id": run_id}
    except (httpx.HTTPError, RuntimeError, ValueError, HTTPException, asyncio.CancelledError) as exc:
        if lease is not None:
            await manager.release_run(run_id, lease=lease)
        if isinstance(exc, (HTTPException, asyncio.CancelledError)):
            raise
        raise HTTPException(409, "车端未确认驾驶模式，请核对车辆状态") from exc


async def send_drive(binding, user, command, drive_id, sequence, *, client=None):
    if (not isinstance(drive_id, str) or not 1 <= len(drive_id) <= 128
            or command not in DRIVE_COMMANDS or type(sequence) is not int or not 1 <= sequence <= 2**53 - 1):
        raise HTTPException(422, "手动指令或序号无效")
    lease = control_lease_manager.current
    if (lease is None or lease.purpose not in ("manual", "mapping") or lease.run_id != drive_id
            or (lease.robot_id, lease.address, lease.user_id) != (*binding, user.id)
            or not lease.permissions <= user.permissions):
        raise HTTPException(409, "驾驶控制权已失效，请重新选择驾驶模式")
    if sequence <= lease.drive_sequence:
        raise HTTPException(409, "已收到更新的驾驶指令")
    lease.drive_sequence = sequence
    try:
        wire = {
            "command": command, "drive_seq": sequence, "control_lease": lease.identity(),
        }
        gateway = get_robot_gateway(binding[1])
        if client is None:
            result = await gateway.operator_request("command", wire)
        else:
            response = await client.post(gateway.base_url + "/api/command", json=wire)
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        raise HTTPException(409, "车端未确认手动指令") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise HTTPException(409, "车端未确认手动指令")
    return result
