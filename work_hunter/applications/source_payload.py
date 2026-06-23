from __future__ import annotations

from typing import Any

from ..config import mask_secrets


def safe_source_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return mask_secrets(dict(payload or {}))
