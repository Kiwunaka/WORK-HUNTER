from __future__ import annotations

from typing import Any

import requests

from .backends import ConfigBackend, DictConfigBackend
from .identity import HHIdentity
from .user_agent import build_android_user_agent


class HHApiSession:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        backend: ConfigBackend | None = None,
        base_url: str = "https://api.hh.ru",
    ):
        self.config = config
        self.backend = backend or DictConfigBackend(config)
        self.identity = HHIdentity.from_config(config)
        self.base_url = str(config.get("api_base_url") or base_url).rstrip("/")
        self.timeout = int(config.get("timeout", 30))
        self.user_agent = str(
            config.get("hh_user_agent")
            or config.get("android_user_agent")
            or build_android_user_agent()
        )

    def request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self.request(method, path, **kwargs)
        payload = _response_json(response)
        if response.status_code >= 400:
            raise RuntimeError(f"HH API error {response.status_code}: {_hh_error_code(payload)}")
        return payload

    def request(self, method: str, path: str, **kwargs: Any):
        if not self.identity.access_token:
            raise RuntimeError("HH access token is required")
        return requests.request(
            method.upper(),
            f"{self.base_url}{path}",
            headers={**self.headers(), **kwargs.pop("headers", {})},
            timeout=kwargs.pop("timeout", self.timeout),
            allow_redirects=kwargs.pop("allow_redirects", False),
            **kwargs,
        )

    def refresh_token(self) -> dict[str, Any]:
        response = requests.request(
            "POST",
            f"{self.base_url}/token",
            headers={
                "Accept": "application/json",
                "User-Agent": self.user_agent,
                "HH-User-Agent": self.user_agent,
            },
            data=self.identity.refresh_payload(),
            timeout=self.timeout,
        )
        payload = _response_json(response)
        if response.status_code >= 400:
            raise RuntimeError(f"HH API error {response.status_code}: {_hh_error_code(payload)}")
        self.identity.update_from_token_response(payload)
        self.backend.save(self.identity.to_config_patch())
        return payload

    def headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
            "HH-User-Agent": self.user_agent,
        }
        headers.update(self.identity.authorization_header())
        return headers


def _response_json(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _hh_error_code(payload: dict[str, Any]) -> str:
    errors = payload.get("errors") or []
    if errors and isinstance(errors[0], dict):
        return str(errors[0].get("value") or errors[0].get("type") or "unknown")
    return str(payload.get("error") or "unknown")
