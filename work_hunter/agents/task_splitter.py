from __future__ import annotations

import re

from .agent_profiles import WAVE_PROFILES


def route_task_to_wave(task_type: str) -> str | None:
    normalized = _normalize_task_label(task_type)
    if not normalized:
        return None
    for profile in WAVE_PROFILES:
        allowed = {_normalize_task_label(item) for item in profile.allowed_tasks}
        if normalized in allowed:
            return profile.name
    return None


def _normalize_task_label(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()
