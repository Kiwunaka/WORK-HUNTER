from __future__ import annotations

from typing import Any


READ_ONLY_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_literal_confirmation(value: object) -> bool:
    return value is True


def require_mutation_confirmation(
    confirm: object,
    *,
    code: str,
    message: str,
    risk_flags: tuple[str, ...],
    context: dict[str, object] | None = None,
) -> dict[str, Any] | None:
    if is_literal_confirmation(confirm):
        return None
    return {
        "status": "blocked",
        "code": code,
        "message": message,
        "requires_confirmation": True,
        "risk_flags": list(risk_flags),
        **(context or {}),
    }
