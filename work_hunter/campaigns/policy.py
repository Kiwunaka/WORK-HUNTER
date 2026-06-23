from __future__ import annotations

from typing import Any


def campaign_policy_gate(
    *,
    preset: dict[str, Any],
    source_level: int,
    job_score: int,
    item: dict[str, Any],
    counters: dict[str, int],
    kill_switch_paused: bool,
    resume_variant_valid: bool,
    cover_letter_generated: bool,
    payload_preview_saved: bool,
    session_valid: bool,
    audit_initialized: bool,
) -> dict[str, Any]:
    reasons: list[str] = []
    if int(source_level or 0) < 5:
        reasons.append("source_maturity_below_l5")
    if not preset.get("enabled"):
        reasons.append("campaign_disabled")
    if not preset.get("real_apply"):
        reasons.append("real_apply_disabled")
    if int(job_score or 0) < int(preset.get("min_score") or 0):
        reasons.append("below_min_score")
    if item.get("has_test") and preset.get("skip_tests", True):
        reasons.append("blocked_test")
    if item.get("unknown_form") and preset.get("skip_unknown_forms", True):
        reasons.append("blocked_unknown_form")
    if (item.get("captcha") or item.get("challenge")) and (preset.get("skip_captcha", True) or preset.get("skip_challenges", True)):
        reasons.append("blocked_captcha_or_challenge")
    if item.get("blacklisted") and preset.get("blacklist_enabled", True):
        reasons.append("blacklisted")
    if item.get("duplicate_employer"):
        reasons.append("duplicate_employer")
    if int(counters.get("daily") or 0) >= int(preset.get("daily_cap") or 0):
        reasons.append("daily_cap_reached")
    if int(counters.get("source") or 0) >= _source_cap(preset, str(item.get("source") or "")):
        reasons.append("source_cap_reached")
    if int(counters.get("company") or 0) >= int(preset.get("per_company_cap") or 0):
        reasons.append("company_cap_reached")
    if not resume_variant_valid:
        reasons.append("resume_variant_invalid")
    if not cover_letter_generated:
        reasons.append("cover_letter_missing")
    if not payload_preview_saved:
        reasons.append("payload_preview_missing")
    if not session_valid:
        reasons.append("session_invalid")
    if not audit_initialized:
        reasons.append("audit_missing")
    if kill_switch_paused:
        reasons.append("kill_switch_paused")
    return {"can_apply": not reasons, "reasons": reasons}


def _source_cap(preset: dict[str, Any], source: str) -> int:
    caps = preset.get("per_source_cap") or {}
    if not source:
        return 10**9
    return int(caps.get(source) or 10**9)
