import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.services import relative_motion as motion
from app.services.assistant_tools import MoveRelative


@pytest.mark.parametrize("value",[-.1,0,.21,float("nan"),float("inf")])
def test_relative_command_rejects_unbounded_distance(value):
    with pytest.raises(ValidationError):
        MoveRelative(action="move_relative",distance_m=value)
    with pytest.raises(ValidationError):
        MoveRelative(action="move_relative",distance_m=.2,speed=1)


@pytest.fixture
def field(monkeypatch):
    events=[]
    def status(x=0,mode="manual",age=.02):
        return {"ok":True,"status":{"mode":mode,"emergency_stop":False,"speed_scale":.8,
            "lidar":{"ok":True},"odom":{"ok":True},"tf":{"ok":True},
            "relative_motion":{"x":x,"y":0,"yaw":0,"age_sec":age,"stamp":100+x,
                               "frame_id":"odom","ok":True,"cruise_speed_mps":.28}}}
    manager=SimpleNamespace(current=None)
    lease=SimpleNamespace(drive_sequence=0,identity=lambda:{"controller_id":"c","control_epoch":"e","owner":"o"})
    samples=[status(mode="stop"),status(),status(),status(.1),status(.21)]
    class Gateway:
        base_url="http://car"
        fail_stop=False
        async def get_status(self,*args):
            return samples.pop(0)
        async def stop(self):
            events.append("stop")
            if self.fail_stop and manager.current:
                raise RuntimeError("stop response lost")
            return {"stationary_confirmed":True}
        async def operator_request(self,path,payload):
            events.append(("mode",payload["mode"]))
            return {"ok":True}
        @asynccontextmanager
        async def _client(self,timeout):
            async def get(url):
                measured=await self.get_status("r")
                return SimpleNamespace(raise_for_status=lambda:None,json=lambda:measured["status"])
            async def post(url,json):
                events.append(("scale",json["scale"]))
                return SimpleNamespace(raise_for_status=lambda:None)
            yield SimpleNamespace(post=post,get=get)
    gateway=Gateway()
    async def acquire(*args,**kwargs):
        manager.current=lease
        return lease
    async def release(*args,**kwargs):
        events.append("release");manager.current=None
    async def drive(binding,user,command,run_id,sequence,**kwargs):
        events.append(command);lease.drive_sequence=sequence
    manager.acquire_for_run=acquire;manager.release_run=release
    monkeypatch.setattr(motion,"get_robot_gateway",lambda address:gateway)
    monkeypatch.setattr(motion,"control_lease_manager",manager)
    monkeypatch.setattr(motion,"send_drive",drive)
    async def fast_sleep(seconds):pass
    monkeypatch.setattr(motion.asyncio,"sleep",fast_sleep)
    return SimpleNamespace(events=events,samples=samples,status=status,gateway=gateway,manager=manager)


def test_distance_feedback_stops_and_releases_before_success(field):
    value=asyncio.run(motion.execute_relative(("r","http://car"),SimpleNamespace(id="admin"),{"distance_m":.2},"run",lambda:None))
    assert value["state"]=="completed"
    assert field.events.count("forward")==2
    assert field.events[-2:]==["release",("scale",.8)]
    assert field.events.count("stop")==2 and field.manager.current is None
    assert abs(next(v[1] for v in field.events if isinstance(v,tuple) and v[0]=="scale")*.28-.10)<1e-9


def test_stale_odometry_stops_before_any_drive(field):
    field.samples[1]=field.status(age=.7)
    with pytest.raises(HTTPException):
        asyncio.run(motion.execute_relative(("r","http://car"),SimpleNamespace(id="admin"),{"distance_m":.2},"run",lambda:None))
    assert "forward" not in field.events
    assert field.events.count("stop")==2 and field.manager.current is None


def test_lost_stop_response_still_ends_renewals(field):
    field.gateway.fail_stop=True
    with pytest.raises(RuntimeError):
        asyncio.run(motion.execute_relative(("r","http://car"),SimpleNamespace(id="admin"),{"distance_m":.2},"run",lambda:None))
    assert field.manager.current is None and field.events[-1]=="release"
