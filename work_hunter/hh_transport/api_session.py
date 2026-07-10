from __future__ import annotations

import threading
import time
import urllib.parse
from typing import Any

import requests

from .backends import ConfigBackend, DictConfigBackend
from .errors import (
    HHAuthError,
    HHForbiddenError,
    HHRateLimitError,
    HHTransportError,
    HHValidationError,
)
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
        self.min_interval = float(config.get("min_interval", 0) or 0)
        self._last_request_at = 0.0
        self._lock = threading.Lock()
        self.http = requests.Session()
        self.user_agent = str(
            config.get("hh_user_agent")
            or config.get("android_user_agent")
            or build_android_user_agent()
        )

    def request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self.request(method, path, **kwargs)
        success = 200 <= response.status_code < 300
        payload = _response_json(response, strict=success)
        if not success:
            raise _error_from_response(response.status_code, payload)
        return payload

    def request(self, method: str, path: str, **kwargs: Any):
        return self._request(method, path, retry_on_auth=True, **kwargs)

    def _request(self, method: str, path: str, *, retry_on_auth: bool, **kwargs: Any):
        if not self.identity.access_token:
            if self.identity.refresh_token:
                self.refresh_token()
            else:
                raise HHAuthError("HH access token is required", code="auth_missing")
        if self.identity.is_access_expired() and self.identity.refresh_token:
            self.refresh_token()
        response = self._send(method, path, **kwargs)
        if response.status_code == 401 and retry_on_auth and self.identity.refresh_token:
            self.refresh_token()
            return self._request(method, path, retry_on_auth=False, **kwargs)
        return response

    def _send(self, method: str, path: str, **kwargs: Any):
        with self._lock:
            if self.min_interval > 0 and self._last_request_at:
                elapsed = time.monotonic() - self._last_request_at
                if elapsed < self.min_interval:
                    time.sleep(self.min_interval - elapsed)
            try:
                response = self.http.request(
                    method.upper(),
                    f"{self.base_url}{path}",
                    headers={**self.headers(), **kwargs.pop("headers", {})},
                    timeout=kwargs.pop("timeout", self.timeout),
                    allow_redirects=kwargs.pop("allow_redirects", False),
                    **kwargs,
                )
            except requests.RequestException as exc:
                raise _network_error(method, path, exc) from exc
            self._last_request_at = time.monotonic()
            return response

    def refresh_token(self) -> dict[str, Any]:
        try:
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
        except requests.RequestException as exc:
            raise _network_error("POST", "/token", exc) from exc
        success = 200 <= response.status_code < 300
        payload = _response_json(response, strict=success)
        if not success:
            raise _error_from_response(response.status_code, payload)
        self.identity.update_from_token_response(payload)
        self.backend.save(self.identity.to_config_patch())
        return payload

    def whoami(self) -> dict[str, Any]:
        return self.request_json("GET", "/me")

    def list_resumes(self) -> list[dict[str, Any]]:
        data = self.request_json("GET", "/resumes/mine")
        return list(data.get("items") or [])

    def get_resume(self, resume_id: str) -> dict[str, Any]:
        return self.request_json("GET", f"/resumes/{resume_id}")

    def search_vacancies(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        data = self.request_json("GET", "/vacancies", params=params)
        return list(data.get("items") or [])

    def get_vacancy(self, vacancy_id: str) -> dict[str, Any]:
        return self.request_json("GET", f"/vacancies/{vacancy_id}")

    def suitable_resumes(self, vacancy_id: str) -> list[dict[str, Any]]:
        data = self.request_json("GET", f"/vacancies/{vacancy_id}/suitable_resumes")
        return list(data.get("items") or [])

    def apply(self, vacancy_id: str, resume_id: str, message: str):
        return self.request(
            "POST",
            "/negotiations",
            data={
                "resume_id": resume_id,
                "vacancy_id": vacancy_id,
                "message": message or "",
            },
        )

    def list_negotiations(self, status: str = "active") -> list[dict[str, Any]]:
        data = self.request_json("GET", "/negotiations", params={"status": status})
        return list(data.get("items") or [])

    def list_negotiation_messages(self, negotiation_id: str) -> list[dict[str, Any]]:
        data = self.request_json("GET", f"/negotiations/{negotiation_id}/messages")
        if isinstance(data.get("items"), list):
            return list(data["items"])
        if isinstance(data.get("messages"), list):
            return list(data["messages"])
        return []

    def send_negotiation_message(
        self,
        negotiation_id: str,
        message: str,
        chat_id: str | None = None,
    ) -> dict[str, Any]:
        if chat_id:
            return self.request_json(
                "POST",
                f"/common/chats/{chat_id}/messages",
                json={"text": message},
            )
        return self.request_json(
            "POST",
            f"/negotiations/{negotiation_id}/messages",
            data={"message": message},
        )

    def headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
            "HH-User-Agent": self.user_agent,
            "X-HH-App-Active": "true",
        }
        headers.update(self.identity.authorization_header())
        return headers


def _network_error(
    method: str,
    path: str,
    exc: requests.RequestException,
) -> HHTransportError:
    safe_path = urllib.parse.urlsplit(path).path or "/"
    return HHTransportError(
        f"{method.upper()} {safe_path} failed: {type(exc).__name__}",
        code="network_error",
        payload={"method": method.upper(), "path": safe_path},
    )


def _parse_error(response: Any, exc: ValueError) -> HHTransportError:
    return HHTransportError(
        f"HH API response contained invalid JSON: {type(exc).__name__}",
        status_code=response.status_code,
        code="parse_error",
    )


def _response_json(response: Any, *, strict: bool) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        content = getattr(response, "content", None)
        text = getattr(response, "text", None) if content is None else None
        if strict and bool(content if content is not None else text):
            raise _parse_error(response, exc) from exc
        return {}
    return payload if isinstance(payload, dict) else {}


def _hh_error_code(payload: dict[str, Any]) -> str:
    errors = payload.get("errors") or []
    if errors and isinstance(errors[0], dict):
        return str(errors[0].get("value") or errors[0].get("type") or "unknown")
    return str(payload.get("error") or "unknown")


def _error_from_response(status_code: int, payload: dict[str, Any]) -> HHTransportError:
    code = "redirect" if 300 <= status_code < 400 else _hh_error_code(payload)
    message = f"HH API error {status_code}: {code}"
    if status_code == 401:
        return HHAuthError(message, status_code=status_code, code=code, payload=payload)
    if status_code == 403:
        return HHForbiddenError(message, status_code=status_code, code=code, payload=payload)
    if status_code == 429:
        return HHRateLimitError(message, status_code=status_code, code=code, payload=payload)
    if status_code == 400:
        return HHValidationError(message, status_code=status_code, code=code, payload=payload)
    return HHTransportError(message, status_code=status_code, code=code, payload=payload)


class HHApiTransport(HHApiSession):
    pass
