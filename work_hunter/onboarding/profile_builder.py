from __future__ import annotations

from typing import Any

from ..memory.candidate_profile import build_candidate_map


def build_profile_from_facts(profile: dict[str, Any], facts: list[dict[str, Any]], completeness: dict[str, Any]) -> dict[str, Any]:
    return build_candidate_map(profile=profile, facts=facts, completeness=completeness)
