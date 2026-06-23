from __future__ import annotations

from typing import Any

from ..memory.claims import evidence_status_for_fact


def fact_evidence_status(fact: dict[str, Any]) -> str:
    return evidence_status_for_fact(fact)
