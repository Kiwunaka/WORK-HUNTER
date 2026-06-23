from __future__ import annotations

from typing import Any


def candidate_completeness_from_facts(facts: list[dict[str, Any]]) -> dict[str, Any]:
    confirmed = [fact for fact in facts if fact.get("status") in {"confirmed", "verified"}]
    required = {"experience", "skills", "constraints"}
    confirmed_keys = {str(fact.get("key") or "") for fact in confirmed}
    missing = sorted(required - confirmed_keys)
    return {
        "status": "complete" if not missing else "incomplete",
        "total_facts": len(facts),
        "confirmed_facts": len(confirmed),
        "missing": missing,
        "score": int(100 * len(required - set(missing)) / len(required)),
    }
