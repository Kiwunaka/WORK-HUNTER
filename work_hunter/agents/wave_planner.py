from __future__ import annotations

from typing import Any, Iterable, Mapping

from .agent_profiles import WAVE_PROFILES, wave_profile_by_name
from .task_splitter import route_task_to_wave


def plan_agent_waves(tasks: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {profile.name: [] for profile in WAVE_PROFILES}
    blocked: list[dict[str, Any]] = []
    for task in tasks:
        task_copy = dict(task)
        wave_name = route_task_to_wave(str(task_copy.get("task_type") or ""))
        if not wave_name:
            blocked.append({**task_copy, "reason": "unknown_task_type"})
            continue
        grouped[wave_name].append(task_copy)

    waves: list[dict[str, Any]] = []
    for profile in WAVE_PROFILES:
        chunk: list[dict[str, Any]] = []
        for task in grouped[profile.name]:
            chunk.append(task)
            if len(chunk) == profile.max_agents:
                waves.append(_wave_payload(profile.name, chunk))
                chunk = []
        if chunk:
            waves.append(_wave_payload(profile.name, chunk))

    return {"waves": waves, "blocked": blocked}


def _wave_payload(name: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    profile = wave_profile_by_name(name)
    if profile is None:
        raise ValueError(f"Unknown agent wave: {name}")
    return {
        "name": profile.name,
        "max_agents": profile.max_agents,
        "model": profile.model,
        "reasoning": profile.reasoning,
        "tasks": list(tasks),
    }
