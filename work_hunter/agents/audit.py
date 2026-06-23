from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Sequence

from ..product_lock import redact_product_lock_text


def build_agent_audit_record(
    *,
    wave: str,
    task_type: str,
    model: str,
    reasoning: str,
    decision: str,
    prompt: str = "",
    output: str = "",
    reasons: Sequence[str] | None = None,
) -> dict[str, Any]:
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "wave": wave,
        "task_type": task_type,
        "model": model,
        "reasoning": reasoning,
        "decision": decision,
        "reasons": list(reasons or []),
        "prompt": redact_agent_text(prompt),
        "output": redact_agent_text(output),
    }


def redact_agent_text(text: str) -> str:
    value = redact_product_lock_text(str(text or ""))
    patterns = [
        (r"(?i)\b(client_secret\s*=\s*)[^\s&]+", r"\1***"),
        (r"(?i)\b(access_token\s*=\s*)[^\s&]+", r"\1***"),
        (r"(?i)\b(refresh_token\s*=\s*)[^\s&]+", r"\1***"),
        (r"(?i)\b(session(?:id|_id)?\s*=\s*)[^\s&]+", r"\1***"),
    ]
    for pattern, replacement in patterns:
        value = re.sub(pattern, replacement, value)
    return value
