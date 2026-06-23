from __future__ import annotations

import re
from typing import Any

from ..browser.redaction import redact_browser_payload
from ..product_lock import redact_product_lock_text


def redact_secrets(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return redact_browser_payload(value)
    text = redact_product_lock_text(str(value or ""))
    patterns = [
        (r"(?i)\b(client_secret\s*=\s*)[^\s&]+", r"\1***"),
        (r"(?i)\b(api_key\s*=\s*)[^\s&]+", r"\1***"),
        (r"(?i)\b(access_token\s*=\s*)[^\s&]+", r"\1***"),
        (r"(?i)\b(refresh_token\s*=\s*)[^\s&]+", r"\1***"),
        (r"(?i)\b(session(?:id|_id)?\s*=\s*)[^\s&]+", r"\1***"),
        (r"(?i)\bsk-[A-Za-z0-9][A-Za-z0-9_-]{2,}\b", "***"),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"(?i)access_token=\*\*\*", "redacted=***", text)
    text = re.sub(r"(?i)refresh_token=\*\*\*", "redacted=***", text)
    return text
