from __future__ import annotations


def apply_policy_for_level(level: int) -> dict[str, bool]:
    value = max(0, min(6, int(level or 0)))
    return {
        "can_apply": value >= 5,
        "can_campaign_apply": value >= 6,
        "requires_confirmation": 5 <= value < 6,
    }
