from __future__ import annotations

from typing import Any


def evidence_status_for_fact(fact: dict[str, Any]) -> str:
    status = str(fact.get("status") or "").strip().lower()
    source = str(fact.get("source") or "").strip().lower()
    if status in {"confirmed", "verified", "verified_by_user"}:
        return "verified_by_user"
    if status in {"rejected", "forbidden", "forbidden_to_claim"}:
        return "forbidden_to_claim"
    if status == "derived_from_resume" or source in {"resume", "resume_import", "resume_parse"}:
        return "derived_from_resume"
    if status == "derived_from_chat":
        return "derived_from_chat"
    return "needs_confirmation"
