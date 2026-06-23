from __future__ import annotations

from typing import Any


def evidence_links_for_fact(fact: dict[str, Any]) -> list[str]:
    evidence = fact.get("evidence") or {}
    links = evidence.get("links") if isinstance(evidence, dict) else None
    if isinstance(links, list):
        return [str(item) for item in links if str(item).strip()]
    return []
