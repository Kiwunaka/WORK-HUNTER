from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any


REQUIRED_HH_RESUME_FIELDS = ("title", "first_name", "last_name", "area", "professional_roles")


def load_hh_resume_payload(path: str | Path) -> dict[str, Any]:
    source_path = Path(path)
    text = source_path.read_text(encoding="utf-8")
    return load_hh_resume_payload_text(text, suffix=source_path.suffix)


def load_hh_resume_payload_text(text: str, *, suffix: str = ".md") -> dict[str, Any]:
    suffix = suffix.lower()
    if suffix == ".json":
        payload = json.loads(text)
    elif suffix == ".toml":
        payload = tomllib.loads(text)
    elif suffix in {".md", ".markdown"}:
        payload = _parse_markdown_resume(text)
    else:
        raise ValueError(f"Unsupported resume payload file type: {suffix}")
    if not isinstance(payload, dict):
        raise ValueError("Resume payload must be a JSON/TOML object.")
    return normalize_hh_resume_payload(payload)


def normalize_hh_resume_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    if "role" in normalized and "title" not in normalized:
        normalized["title"] = normalized.pop("role")
    if "position" in normalized and "title" not in normalized:
        normalized["title"] = normalized.pop("position")
    if "professional_role" in normalized and "professional_roles" not in normalized:
        normalized["professional_roles"] = normalized.pop("professional_role")
    if "area" in normalized:
        normalized["area"] = _id_object(normalized["area"])
    if "professional_roles" in normalized:
        normalized["professional_roles"] = [_id_object(item) for item in _as_list(normalized["professional_roles"])]
    contact = list(_as_list(normalized.get("contact", [])))
    email = str(normalized.pop("email", "") or "").strip()
    phone = str(normalized.pop("phone", "") or "").strip()
    if email:
        contact.append({"type": {"id": "email"}, "value": email})
    if phone:
        contact.append({"type": {"id": "cell"}, "value": {"formatted": phone}})
    if contact:
        normalized["contact"] = contact
    return normalized


def validate_hh_resume_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_hh_resume_payload(payload)
    missing = [field for field in REQUIRED_HH_RESUME_FIELDS if not _has_value(normalized.get(field))]
    warnings: list[str] = []
    if not _has_value(normalized.get("contact")):
        warnings.append("contact is empty; HH may reject or create a low-quality resume.")
    if not _has_value(normalized.get("skills")) and not _has_value(normalized.get("skill_set")):
        warnings.append("skills/skill_set is empty; imported resume may be weak.")
    return {"valid": not missing, "missing": missing, "warnings": warnings, "payload": normalized}


def _parse_markdown_resume(text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    section = ""
    section_lines: dict[str, list[str]] = {}
    section_aliases = {
        "about me": "about",
        "profile": "summary",
        "summary": "summary",
        "about": "about",
        "skills": "skills",
        "skill set": "skills",
        "skill_set": "skills",
        "experience": "experience",
        "work experience": "experience",
    }
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# "):
            payload.setdefault("title", line[2:].strip())
            section = ""
            continue
        if line.startswith("## "):
            raw_section = line[3:].strip().lower()
            section = section_aliases.get(raw_section, raw_section)
            section_lines.setdefault(section, [])
            continue
        if ":" in line and not line.startswith(("http://", "https://")):
            key, value = line.split(":", 1)
            normalized_key = key.strip().lower().replace("-", "_").replace(" ", "_")
            payload[normalized_key] = value.strip()
            continue
        if section:
            section_lines.setdefault(section, []).append(line)
    skills = section_lines.get("skills") or section_lines.get("skill_set") or []
    if skills and "skills" not in payload:
        payload["skills"] = "\n".join(skills)
    summary = section_lines.get("summary") or section_lines.get("about") or []
    if summary and "skills" not in payload:
        payload["skills"] = "\n".join(summary)
    if summary and "description" not in payload:
        payload["description"] = "\n".join(summary)
    experience = section_lines.get("experience") or []
    if experience and "experience" not in payload:
        payload["experience"] = "\n".join(experience)
    return payload


def _id_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"id": str(value).strip()}


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [value]


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_value(item) for item in value)
    return True
