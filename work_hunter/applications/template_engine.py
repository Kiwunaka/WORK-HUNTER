from __future__ import annotations

from typing import Any


class SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_template(template: str, context: dict[str, Any]) -> str:
    return str(template or "").format_map(SafeDict({key: str(value) for key, value in context.items()})).strip()
