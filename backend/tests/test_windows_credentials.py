import os
import secrets

import pytest

from app.services import assistant_provider, windows_credentials as vault


@pytest.mark.skipif(os.name != "nt", reason="Windows Credential Manager required")
def test_profile_credentials_use_windows_vault_and_do_not_fall_back(monkeypatch):
    profile = secrets.token_hex(32)
    monkeypatch.setenv(vault.PROFILE_ENV, profile)
    monkeypatch.setenv("ASSISTANT_API_KEY", "environment-value")
    monkeypatch.setenv("ROBOT_SERVICE_TOKEN", "environment-value")
    try:
        assert vault.read_credentials(profile) == {}
        assert vault.machine_token() == ""
        with pytest.raises(assistant_provider.ProviderUnavailable):
            assistant_provider.configured_provider()
        values = {"assistant_url": "https://example.invalid/v1", "assistant_model": "测试模型",
                  "assistant_key": secrets.token_urlsafe(32), "robot_token": secrets.token_urlsafe(32)}
        vault.write_credentials(profile, values)
        assert vault.read_credentials(profile) == values
        assert assistant_provider.configured_provider().key == values["assistant_key"]
        assert vault.machine_token() == values["robot_token"]
    finally:
        vault.delete_credentials(profile)
    assert not vault.read_credentials(profile)
