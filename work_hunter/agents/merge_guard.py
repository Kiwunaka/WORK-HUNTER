from __future__ import annotations

import fnmatch
import re
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from .agent_profiles import PATCH_WAVE

EXACT_FORBIDDEN_PATHS = {
    "work_hunter/browser/session_store.py",
    "work_hunter/ai/redaction.py",
    "work_hunter/campaigns/policy.py",
}
FORBIDDEN_GLOBS = ("work_hunter/sources/*/apply*.py",)
STORAGE_MIGRATION_HINTS = (
    "alter table",
    "create table",
    "drop table",
    "pragma user_version",
    "schema_version",
    "migration",
)


def guard_patch_wave_scope(
    *,
    paths: Sequence[str],
    prompt: str = "",
    content_by_path: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    content = dict(content_by_path or {})

    for raw_path in paths:
        normalized, traversed = _normalize_path(raw_path)
        if traversed:
            _append_once(reasons, "path_traversal")
        if normalized in EXACT_FORBIDDEN_PATHS:
            _append_once(reasons, f"forbidden_path:{normalized}")
        for pattern in FORBIDDEN_GLOBS:
            if fnmatch.fnmatch(normalized, pattern):
                _append_once(reasons, f"forbidden_glob:{pattern}")
        if normalized == "work_hunter/storage.py" and _looks_like_storage_migration(
            content.get(raw_path, "") or content.get(normalized, "")
        ):
            _append_once(reasons, "storage_migration")

    prompt_text = str(prompt or "")
    for topic in PATCH_WAVE.forbidden_topics:
        if _contains_forbidden_topic(prompt_text, topic):
            _append_once(reasons, f"forbidden_topic:{topic}")

    return {"allowed": not reasons, "reasons": reasons}


def _normalize_path(path: str) -> tuple[str, bool]:
    raw = str(path or "").replace("\\", "/").strip()
    parts = [part for part in PurePosixPath(raw).parts if part not in {"", "."}]
    traversed = ".." in parts
    safe_parts = [part for part in parts if part != ".."]
    return "/".join(safe_parts).lstrip("/"), traversed


def _looks_like_storage_migration(content: str) -> bool:
    lowered = str(content or "").casefold()
    return any(hint in lowered for hint in STORAGE_MIGRATION_HINTS)


def _contains_forbidden_topic(text: str, topic: str) -> bool:
    lowered = text.casefold()
    normalized = topic.casefold()
    if " " in normalized:
        return normalized in lowered
    if normalized == "cookies":
        return bool(re.search(r"\bcookies?\b", lowered))
    if normalized == "secrets":
        return bool(re.search(r"\bsecrets?\b", lowered))
    return bool(re.search(rf"\b{re.escape(normalized)}\b", lowered))


def _append_once(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)
