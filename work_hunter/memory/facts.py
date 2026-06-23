from __future__ import annotations

from typing import Any


def confirmed_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [fact for fact in facts if fact.get("status") in {"confirmed", "verified"}]
