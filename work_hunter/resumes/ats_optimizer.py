from __future__ import annotations

import re
from typing import Any

TECH_STOPWORDS = {
    "and",
    "the",
    "with",
    "for",
    "backend",
    "developer",
    "engineer",
    "production",
    "platform",
    "services",
    "apis",
    "api",
}


def build_resume_variant_metadata(
    *,
    vacancy_text: str,
    canonical: dict[str, Any],
    claims: list[dict[str, Any]],
    diff: dict[str, list[str]],
) -> dict[str, Any]:
    allowed_skills = {str(skill).casefold() for skill in canonical.get("skills", []) if str(skill).strip()}
    ats_keywords = extract_ats_keywords(vacancy_text, allowed_skills)
    unsupported_claims = unsupported_claim_keywords(vacancy_text, allowed_skills)
    risk_flags = []
    if unsupported_claims:
        risk_flags.append("unsupported_claims_present")
    return {
        "ats_keywords": ats_keywords,
        "changes_summary": _changes_summary(claims, diff),
        "risk_flags": risk_flags,
        "unsupported_claims": unsupported_claims,
    }


def extract_ats_keywords(vacancy_text: str, allowed_skills: set[str]) -> list[str]:
    tokens = set(_tokens(vacancy_text))
    return sorted(token for token in tokens if token in allowed_skills)


def unsupported_claim_keywords(vacancy_text: str, allowed_skills: set[str]) -> list[str]:
    tokens = set(_tokens(vacancy_text))
    return sorted(token for token in tokens if token not in allowed_skills and token not in TECH_STOPWORDS)


def _changes_summary(claims: list[dict[str, Any]], diff: dict[str, list[str]]) -> list[str]:
    summary = [
        f"Added confirmed fact: {claim.get('value')}"
        for claim in claims
        if str(claim.get("value") or "").strip()
    ]
    added = len(diff.get("added_lines") or [])
    removed = len(diff.get("removed_lines") or [])
    summary.append(f"Resume diff: {added} added / {removed} removed lines")
    return summary


def _tokens(text: str) -> list[str]:
    values: list[str] = []
    for token in re.split(r"[^A-Za-zА-Яа-я0-9_+#.-]+", str(text or "").casefold()):
        token = token.strip("._-")
        if len(token) < 2:
            continue
        values.append(token)
    return values
