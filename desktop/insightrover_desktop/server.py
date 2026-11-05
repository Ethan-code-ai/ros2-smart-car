import os
import re
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.staticfiles import StaticFiles

CAR_PATH=re.compile(r'^(status|map|video|mode|command|emergency-stop|speed|avoidance|search(?:/[\w/-]+)?|vision/[\w/-]+|mode-obstacle-stop-distance|mapping/[\w/-]+|navigation/[\w/-]+)$')


def initialize(config, admin=None):
    os.environ.update(config.environment())
    from . import VERSION
    config.backup_before_upgrade(VERSION)
    from app.db.base import Base
    from app.db.session import engine,SessionLocal
    from app.db.migrate import ensure_schema
    from app.models import User,Role,Robot,AppSetting
    from app.core.security import hash_password
    from app.seed import seed_session
    Base.metadata.create_all(engine)
    ensure_schema(engine)
    with SessionLocal() as db:
        # Never seed demo accounts or rewrite roles on a normal launch.
        if not db.query(User).first():
            if not admin:raise ValueError('首次使用请先设置管理员账户')
            username,password=admin['username'],admin['password']
            if not re.fullmatch(r'[A-Za-z0-9_-]{3,64}',username) or not 8<=len(password)<=128:
                raise ValueError('用户名须为 3–64 位字母、数字、下划线；密码至少 8 位')
            seed_session(db,users={})
            user=User(id=str(uuid4()),username=username,password_hash=hash_password(password),display_name='管理员',active=True)
            user.roles=[db.query(Role).filter(Role.name=='admin').one()]
            db.add(user)
        if not db.get(Robot,'r-001'):
            db.add(Robot(id='r-001',name='智巡一号',serial='r-001'))
        address=db.query(AppSetting).filter(AppSetting.key=='robot_web_url').one_or_none()
        if address is None:db.add(AppSetting(id=str(uuid4()),key='robot_web_url',value=config.car_url))
        else:address.value=config.car_url
        db.commit()
    (config.root/'data-version.txt').write_text(VERSION,encoding='utf-8')


def create_server(config, web_root, token, admin=None):
    from . import VERSION
    initialize(config,admin)
    from app.main import app
    from app.db.session import SessionLocal
    from app.models import AppSetting

    @app.get('/desktop/health')
    def health():
        return {'status':'ready','version':VERSION,'ip':config.value['ip']}

    @app.post('/desktop/quit')
    def quit_server(request:Request):
        if request.headers.get('X-InsightRover-Token')!=token:
            raise HTTPException(403,'无效的本机管理请求')
        app.state.exit_requested=True
        return {'ok':True}

    # All vehicle traffic is handled by the authenticated business router.
    # Do not reintroduce desktop-only raw proxies here.

    app.mount('/',StaticFiles(directory=Path(web_root),html=True),name='desktop-web')
    return app
