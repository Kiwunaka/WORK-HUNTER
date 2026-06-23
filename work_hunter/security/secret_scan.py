from __future__ import annotations

import re


SECRET_MARKERS = ("Authorization", "Cookie", "Set-Cookie", "client_secret", "api_key", "access_token", "refresh_token", "XSRF")


def find_secret_markers(text: str) -> list[str]:
    value = str(text or "")
    found: list[str] = []
    for marker in SECRET_MARKERS:
        if re.search(re.escape(marker), value, flags=re.IGNORECASE):
            found.append(marker)
    return found
