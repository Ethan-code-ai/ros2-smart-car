from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes.media import _owned_upload, _read_permission
from app.services.storage import normalize_key


@pytest.mark.parametrize("key", [
    "../secret", "maps/../secret", "/absolute", "C:/secret", "C:secret",
    "\\\\host/share", "maps/file:stream", "maps/./image", "maps/folder./image", "maps/CON.png",
])
def test_storage_rejects_platform_path_escapes(key):
    with pytest.raises(ValueError):
        normalize_key(key)


@pytest.mark.parametrize("key", ["maps/real.png", "incidents/real/snapshot.jpg", "uploads/other/photo.png"])
def test_generic_upload_cannot_replace_business_evidence(key):
    user = SimpleNamespace(id="actor", permissions={"maps.manage", "incidents.manage"})
    with pytest.raises(HTTPException) as error:
        _owned_upload(key, user)
    assert error.value.status_code == 403


def test_upload_and_read_respect_owner_and_business_permission():
    user = SimpleNamespace(id="actor", permissions={"maps.manage"})
    assert _owned_upload("uploads/actor/photo.png", user) == "uploads/actor/photo.png"
    _read_permission("uploads/actor/photo.png", user)
    with pytest.raises(HTTPException):
        _read_permission("incidents/item/snapshot.jpg", user)
    user.permissions = {"maps.read"}
    with pytest.raises(HTTPException):
        _owned_upload("uploads/actor/photo.png", user)
