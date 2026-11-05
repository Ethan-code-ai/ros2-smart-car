"""媒体资源接口：预签名上传地址与本地文件回退。

对应项目文档中的 /api/v1/media/presign、/api/v1/media/complete，
存储实现见 services/storage.py（MinIO 优先，未配置时使用本地目录）。
"""
from typing import Optional
import hashlib
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...models import User
from ...services.ops import record_audit
from ...services.storage import get_storage, normalize_key
from ..deps import get_current_user, get_db

router = APIRouter(prefix="/media", tags=["media"])

_CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "json": "application/json",
    "yaml": "text/yaml",
    "yml": "text/yaml",
    "pgm": "image/x-portable-graymap",
}


class PresignIn(BaseModel):
    key: str = Field(min_length=1, max_length=512)
    content_type: str = ""
    expires_in: int = Field(default=900, ge=60, le=86400)


class CompleteIn(BaseModel):
    key: str = Field(min_length=1, max_length=512)
    size: int = Field(default=0, ge=0)
    sha256: str = ""
    content_type: str = ""
    ref_type: Optional[str] = None
    ref_id: Optional[str] = None


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": "BAD_REQUEST", "message": str(exc)})


def _upload_permission(user):
    if user.permissions.isdisjoint({"maps.manage", "incidents.manage"}):
        raise HTTPException(403, "缺少媒体上传权限")


def _owned_upload(key, user):
    _upload_permission(user)
    try:
        safe = normalize_key(key)
    except ValueError as exc:
        raise _bad_request(exc)
    if not safe.startswith(f"uploads/{user.id}/"):
        raise HTTPException(403, "只能写入本人的上传暂存区")
    return safe


def _read_permission(key, user):
    if key.startswith(f"uploads/{user.id}/"):
        return
    permission = "maps.read" if key.startswith("maps/") else "incidents.read" if key.startswith("incidents/") else None
    if permission is None or permission not in user.permissions:
        raise HTTPException(403, "没有此资源的读取权限")


@router.post("/presign")
def presign(payload: PresignIn, user: User = Depends(get_current_user)) -> dict:
    _upload_permission(user)
    try:
        filename = normalize_key(payload.key).rsplit("/", 1)[-1]
        key = f"uploads/{user.id}/{uuid4().hex}/{filename}"
        # Uploads pass through the authenticated host for both storage backends.
        return {"backend": get_storage().backend, "key": key, "method": "PUT",
                "url": f"/api/v1/media/object/{key}", "expires_in": payload.expires_in}
    except ValueError as exc:
        raise _bad_request(exc)


@router.post("/complete")
def complete(
    payload: CompleteIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    storage = get_storage()
    try:
        key = _owned_upload(payload.key, user)
    except ValueError as exc:
        raise _bad_request(exc)
    data = storage.get(key)
    if data is None:
        raise HTTPException(404, "上传对象不存在")
    digest = hashlib.sha256(data).hexdigest()
    if payload.size != len(data) or (payload.sha256 and payload.sha256 != digest):
        raise HTTPException(409, "上传对象大小或摘要不匹配")
    record_audit(
        db,
        user,
        "media.complete",
        "media",
        None,
        after={
            "key": key,
            "size": payload.size,
            "sha256": digest,
            "ref_type": payload.ref_type,
            "ref_id": payload.ref_id,
        },
    )
    db.commit()
    return {
        "key": key,
        "size": payload.size,
        "sha256": digest,
        "content_type": payload.content_type,
        "backend": storage.backend,
        "ref_type": payload.ref_type,
        "ref_id": payload.ref_id,
    }


async def _store_object(key: str, request: Request) -> dict:
    data = await request.body()
    try:
        return get_storage().put(key, data, content_type=request.headers.get("content-type", ""))
    except ValueError as exc:
        raise _bad_request(exc)


@router.put("/object/{key:path}")
async def upload_object_put(
    key: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> dict:
    return await _store_object(_owned_upload(key, user), request)


@router.post("/object/{key:path}")
async def upload_object_post(
    key: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> dict:
    return await _store_object(_owned_upload(key, user), request)


@router.get("/object/{key:path}")
def get_object(key: str, user: User = Depends(get_current_user)) -> Response:
    try:
        key = normalize_key(key)
        _read_permission(key, user)
        data = get_storage().get(key)
    except ValueError as exc:
        raise _bad_request(exc)
    if data is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "资源不存在"})
    suffix = key.rsplit(".", 1)[-1].lower() if "." in key else ""
    return Response(content=data, media_type=_CONTENT_TYPES.get(suffix, "application/octet-stream"))
