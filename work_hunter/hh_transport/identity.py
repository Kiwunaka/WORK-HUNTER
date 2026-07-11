from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass
class HHIdentity:
    access_token: str = ""
    refresh_token: str = ""
    client_id: str = ""
    client_secret: str = ""
    access_expires_at: datetime | None = None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "HHIdentity":
        return cls(
            access_token=str(config.get("access_token") or ""),
            refresh_token=str(config.get("refresh_token") or ""),
            client_id=str(config.get("client_id") or ""),
            client_secret=str(config.get("client_secret") or ""),
            access_expires_at=_parse_datetime(str(config.get("access_expires_at") or "")),
        )

    def to_config_patch(self) -> dict[str, str]:
        patch = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        if self.access_expires_at is not None:
            patch["access_expires_at"] = self.access_expires_at.isoformat()
        return patch

    def has_access_token(self) -> bool:
        return bool(self.access_token)

    def authorization_header(self) -> dict[str, str]:
        if not self.access_token:
            return {}
        return {"Authorization": f"Bearer {self.access_token}"}

    def is_access_expired(self, *, now: datetime | None = None, skew_seconds: int = 60) -> bool:
        if self.access_expires_at is None:
            return False
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return (self.access_expires_at - current).total_seconds() <= skew_seconds

    def refresh_payload(self) -> dict[str, str]:
        if not self.refresh_token:
            raise RuntimeError("HH refresh token is required")
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }
        if self.client_id:
            payload["client_id"] = self.client_id
        if self.client_secret:
            payload["client_secret"] = self.client_secret
        return payload

    def update_from_token_response(self, payload: dict[str, Any]) -> None:
        access_token = str(payload.get("access_token") or "")
        refresh_token = str(payload.get("refresh_token") or "")
        if access_token:
            self.access_token = access_token
        if refresh_token:
            self.refresh_token = refresh_token
        expires_at = payload.get("expires_at") or payload.get("access_expires_at")
        if expires_at:
            self.access_expires_at = _parse_datetime(str(expires_at))
        elif payload.get("expires_in") is not None:
            try:
                seconds = int(payload["expires_in"])
            except (TypeError, ValueError):
                seconds = 0
            if seconds > 0:
                self.access_expires_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
