from __future__ import annotations

import re
from typing import Any

from .claims import evidence_status_for_fact


def build_candidate_map(
    *,
    profile: dict[str, Any],
    facts: list[dict[str, Any]],
    completeness: dict[str, Any],
) -> dict[str, Any]:
    return {
        "identity": _identity(profile, facts),
        "target": _target(profile, facts),
        "skills": _skills(profile, facts),
        "experience": _experience(facts),
        "portfolio": _portfolio(facts),
        "resume_assets": _resume_assets(facts),
        "writing_style": _writing_style(facts),
        "claims": _claims(facts),
        "completeness": dict(completeness),
    }


def _identity(profile: dict[str, Any], facts: list[dict[str, Any]]) -> dict[str, Any]:
    identity_text = _fact_value(facts, "identity")
    return {
        "name": str(profile.get("name") or ""),
        "location": _first_value(_values_from_text(identity_text), default=str((profile.get("locations") or [""])[0] or "")),
        "timezone": str(profile.get("timezone") or "Europe/Moscow"),
        "languages": _values_from_text(identity_text, hints=("english", "russian", "русский", "английский")),
    }


def _target(profile: dict[str, Any], facts: list[dict[str, Any]]) -> dict[str, Any]:
    roles = _values_from_text(_fact_value(facts, "roles")) or [
        str(item).strip().lower()
        for item in profile.get("desired_roles", [])
        if str(item).strip()
    ]
    constraints = " ".join(_fact_value(facts, key) for key in ("constraints", "salary_format", "avoid", "forbidden_claims"))
    return {
        "roles": roles,
        "seniority": "",
        "salary_min": int(profile.get("salary_min") or 0),
        "remote": "remote" in constraints.casefold() or bool(profile.get("remote_only", False)),
        "relocation": "relocation" in constraints.casefold() and "no relocation" not in constraints.casefold(),
        "industries": [],
        "avoid": _values_from_text(_fact_value(facts, "avoid")) + _values_from_text(_fact_value(facts, "forbidden_claims")),
    }


def _skills(profile: dict[str, Any], facts: list[dict[str, Any]]) -> dict[str, list[str]]:
    hard = set(_values_from_text(_fact_value(facts, "stack")) + _values_from_text(_fact_value(facts, "skills")))
    if not hard:
        hard.update(str(item).strip().lower() for item in profile.get("must_have_skills", []) if str(item).strip())
        hard.update(str(item).strip().lower() for item in profile.get("nice_to_have_skills", []) if str(item).strip())
    return {
        "hard": sorted(hard),
        "soft": [],
        "tools": [],
        "frameworks": [],
        "databases": [],
        "cloud": [],
        "testing": [],
    }


def _experience(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    structured = _structured_experience(facts)
    if structured:
        return structured
    values = [_fact_value(facts, "experience"), _fact_value(facts, "achievements")]
    text = "\n".join(value for value in values if value)
    if not text:
        return []
    return [
        {
            "company": "",
            "role": "",
            "period": "",
            "projects": _values_from_text(_fact_value(facts, "projects")),
            "achievements": _values_from_text(_fact_value(facts, "achievements")),
            "tech": _values_from_text(_fact_value(facts, "stack")),
            "evidence": _evidence_for_keys(facts, {"experience", "achievements", "projects"}),
        }
    ]


def _structured_experience(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    evidence = _evidence_for_keys(facts, {"experience"})
    for fact in facts:
        if str(fact.get("key") or "") != "experience":
            continue
        value = fact.get("value")
        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            project = str(entry.get("project") or "").strip()
            details = entry.get("details") or []
            tech = entry.get("tech") or []
            rows.append(
                {
                    "company": str(entry.get("company") or "").strip(),
                    "role": str(entry.get("role") or "").strip(),
                    "period": str(entry.get("period") or "").strip(),
                    "projects": [project] if project else [],
                    "achievements": [str(item).strip() for item in details if str(item).strip()],
                    "tech": [str(item).strip().lower() for item in tech if str(item).strip()],
                    "evidence": evidence,
                }
            )
    return rows


def _portfolio(facts: list[dict[str, Any]]) -> dict[str, Any]:
    projects = _values_from_text(_fact_value(facts, "projects"))
    links = re.findall(r"https?://\S+", _fact_value(facts, "projects"))
    github = next((link for link in links if "github.com" in link.lower()), "")
    return {"github": github, "projects": projects, "links": links}


def _resume_assets(facts: list[dict[str, Any]]) -> dict[str, list[str]]:
    text = _fact_value(facts, "resume_assets")
    values = _values_from_text(text)
    return {
        "hh_resume_ids": [value for value in values if value.startswith("hh") or value.isdigit()],
        "pdfs": [value for value in values if value.endswith(".pdf")],
        "docx": [value for value in values if value.endswith(".docx")],
        "markdown": [value for value in values if value.endswith((".md", ".markdown"))],
        "json": [value for value in values if value.endswith(".json")],
    }


def _writing_style(facts: list[dict[str, Any]]) -> dict[str, Any]:
    text = _fact_value(facts, "writing_style")
    return {
        "tone": text.strip() or "human, concise, confident",
        "avoid": ["канцелярит", "роботский текст", "вода"],
    }


def _claims(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for fact in facts:
        key = str(fact.get("key") or "")
        status = "forbidden_to_claim" if key == "forbidden_claims" else evidence_status_for_fact(fact)
        claims.append(
            {
                "fact_id": fact.get("id"),
                "category": fact.get("category"),
                "key": key,
                "value": fact.get("value"),
                "status": status,
                "source": fact.get("source"),
                "evidence": fact.get("evidence") or {},
            }
        )
    return claims


def _fact_value(facts: list[dict[str, Any]], key: str) -> str:
    values = [str(fact.get("value") or "") for fact in facts if str(fact.get("key") or "") == key]
    return "\n".join(value for value in values if value)


def _values_from_text(text: str, *, hints: tuple[str, ...] = ()) -> list[str]:
    values = [
        item.strip().casefold()
        for item in re.split(r"[,;\n]+", str(text or ""))
        if item.strip()
    ]
    if hints:
        hinted = [value for value in values if any(hint.casefold() in value for hint in hints)]
        return sorted(dict.fromkeys(hinted))
    return sorted(dict.fromkeys(values))


def _first_value(values: list[str], *, default: str = "") -> str:
    return values[0] if values else default


def _evidence_for_keys(facts: list[dict[str, Any]], keys: set[str]) -> list[dict[str, Any]]:
    return [
        {
            "fact_id": fact.get("id"),
            "key": fact.get("key"),
            "status": evidence_status_for_fact(fact),
            "evidence": fact.get("evidence") or {},
        }
        for fact in facts
        if str(fact.get("key") or "") in keys
    ]
