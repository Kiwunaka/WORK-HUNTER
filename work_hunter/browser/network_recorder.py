from __future__ import annotations

from typing import Any

from ..config import mask_secrets


def record_application_flow_plan(source: str) -> dict:
    return {"status": "planned", "source": source, "submit": False}


def save_browser_recording(storage: Any, *, source: str, recording: dict[str, Any]) -> dict[str, Any]:
    source_name = str(source or "").strip().lower().replace("-", "_")
    safe_recording = mask_secrets(recording or {})
    recording_id = storage.add_browser_recording(source_name, safe_recording)
    return {
        "status": "recorded",
        "id": recording_id,
        "source": source_name,
        "recording": safe_recording,
        "submit": False,
    }
