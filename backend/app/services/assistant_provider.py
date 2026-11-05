"""OpenAI-compatible Chat Completions adapter. Model output is untrusted."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from .windows_credentials import CredentialError, PROFILE_ENV, read_credentials


class ProviderUnavailable(RuntimeError):
    pass


class ChatCompletionsProvider:
    def __init__(self, url, model, key):
        endpoint = urlsplit(url)
        if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username is not None
                or endpoint.query or endpoint.fragment or not model or not key):
            raise ProviderUnavailable("模型连接配置无效")
        self.url, self.model, self.key = url.rstrip("/") + "/chat/completions", model, key
        self.deepseek = endpoint.hostname == "api.deepseek.com"
        self.last_response = {}

    async def complete(self, messages, tools):
        self.last_response = {}
        payload = {"model": self.model, "messages": messages, "tools": tools,
                   "tool_choice": "auto", "max_tokens": 1500}
        if self.deepseek:
            # Official DeepSeek endpoints enable thinking by default.
            # This adapter uses the bounded non-thinking tool protocol.
            payload["thinking"] = {"type": "disabled"}
        try:
            async with httpx.AsyncClient(timeout=12, trust_env=False, follow_redirects=False) as client:
                async with client.stream("POST", self.url, headers={"Authorization": "Bearer " + self.key},
                                         json=payload) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 128 * 1024:
                            raise ProviderUnavailable("模型响应过大")
            result = json.loads(body)
            message = result["choices"][0]["message"]
            if not isinstance(message, dict):
                raise ValueError()
            usage = result.get("usage") or {}
            self.last_response = {
                "model": result.get("model") if isinstance(result.get("model"), str) else self.model,
                "usage": {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                          if isinstance(usage, dict) and type(usage.get(key)) is int and usage[key] >= 0},
            }
            return message
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise ProviderUnavailable("模型未返回可用响应") from None


def configured_provider():
    if profile := os.getenv(PROFILE_ENV):
        try:
            value = read_credentials(profile)
        except CredentialError as exc:
            raise ProviderUnavailable(str(exc)) from None
        return ChatCompletionsProvider(value.get("assistant_url", ""), value.get("assistant_model", ""),
                                       value.get("assistant_key", ""))
    url = os.getenv("ASSISTANT_BASE_URL", "")
    model = os.getenv("ASSISTANT_MODEL", "")
    key = os.getenv("ASSISTANT_API_KEY", "")
    secret_file = os.getenv("ASSISTANT_API_KEY_FILE")
    if secret_file:
        try:
            with Path(secret_file).open(encoding="utf-8") as source:
                key = source.read(8193).strip()
            if len(key) > 8192:
                raise ValueError()
        except (OSError, ValueError):
            raise ProviderUnavailable("无法读取模型凭据") from None
    return ChatCompletionsProvider(url, model, key)
