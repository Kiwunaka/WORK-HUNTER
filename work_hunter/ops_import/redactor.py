from __future__ import annotations

from ..product_lock import redact_product_lock_text


def redact_ops_text(text: str) -> str:
    return redact_product_lock_text(text)
