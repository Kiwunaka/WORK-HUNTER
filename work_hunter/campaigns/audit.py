from __future__ import annotations

from typing import Any

from ..config import mask_secrets


def campaign_audit_record(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"event_type": event_type, "data": mask_secrets(data)}
