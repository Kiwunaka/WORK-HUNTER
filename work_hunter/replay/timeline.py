from __future__ import annotations

from typing import Any

from .events import normalize_timeline_event


def build_timeline(events: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = [normalize_timeline_event(event) if not event.get("timestamp") else dict(event) for event in events]
    normalized.sort(key=lambda item: (str(item.get("timestamp") or ""), int(item.get("id") or 0)))
    return {"events": normalized, "count": len(normalized)}
