from __future__ import annotations

from typing import Any

SENSITIVE_BROWSER_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-xsrf-token",
    "x-csrf-token",
    "xsrf-token",
    "csrf-token",
}


def redact_browser_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            redacted[key] = "***" if lowered in SENSITIVE_BROWSER_KEYS else redact_browser_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_browser_payload(item) for item in value]
    return value
