from __future__ import annotations

import itertools
import re
import string
from typing import Any

from ..resume_payloads import load_hh_resume_payload_text, validate_hh_resume_payload


MACHINE_SPECIFIC_KEY_PARTS = ("script", "command", "path", "file", "directory", "dir")
SECRET_KEY_PARTS = ("token", "secret", "password", "api_key")


def render_resume_template(
    template: str,
    *,
    profile: dict[str, Any] | None = None,
    about: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> str:
    values = build_resume_template_context(profile=profile, about=about, context=context)
    return string.Formatter().vformat(template, (), _TemplateValues(values))


def draft_hh_resume_payload_from_template(
    template: str,
    *,
    profile: dict[str, Any] | None = None,
    about: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    markdown = render_resume_template(template, profile=profile, about=about, context=context)
    payload = load_hh_resume_payload_text(markdown, suffix=".md")
    validation = validate_hh_resume_payload(payload)
    return {
        "status": "draft",
        "markdown": markdown,
        "payload": validation["payload"],
        "validation": validation,
    }


def build_resume_template_context(
    *,
    profile: dict[str, Any] | None = None,
    about: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = profile or {}
    about = about or {}
    values: dict[str, Any] = {}
    values.update(_flatten("profile", profile))
    values.update(_flatten("about", about))

    name = _text(profile.get("name") or about.get("name"))
    first_name, last_name = _split_name(name)
    skills = _list_text(about.get("all_skills")) or _list_text(
        list(_list(profile.get("must_have_skills"))) + list(_list(profile.get("nice_to_have_skills")))
    )
    desired_roles = _list_text(profile.get("desired_roles") or profile.get("queries"))
    professional_roles = _list_text(profile.get("professional_roles") or profile.get("professional_role"))
    experience = _format_experience(about.get("experience"))

    values.update(
        {
            "name": name,
            "first_name": _text(profile.get("first_name")) or first_name,
            "last_name": _text(profile.get("last_name")) or last_name,
            "title": _text(profile.get("title")) or _text(profile.get("position")) or desired_roles,
            "summary": _text(about.get("summary")) or _text(profile.get("summary")),
            "skills": skills,
            "skill_set": skills,
            "desired_roles": desired_roles,
            "queries": _list_text(profile.get("queries")),
            "locations": _list_text(profile.get("locations")),
            "salary_min": _text(profile.get("salary_min")),
            "email": _contact_value(profile, "email"),
            "phone": _contact_value(profile, "phone") or _contact_value(profile, "phone_numbers"),
            "area": _text(profile.get("area") or profile.get("hh_area")),
            "professional_roles": professional_roles,
            "experience": experience,
        }
    )
    if context:
        values.update(_flatten("", context))
        values.update(context)
    return {key: _stringify(value) for key, value in values.items()}


def build_hh_batch_preset_matrix(spec: dict[str, Any]) -> dict[str, Any]:
    defaults, dropped = _sanitize_defaults(dict(spec.get("defaults") or {}))
    resumes = _list(spec.get("resumes") or spec.get("resume_ids"))
    searches = _list(spec.get("search_presets") or spec.get("searches"))
    letters = _list(spec.get("letters") or spec.get("letter_templates"))
    limits = _list(spec.get("limits"))
    if not resumes:
        resumes = [""]
    if not searches:
        searches = [""]
    if not letters:
        letters = [""]
    if not limits:
        limits = [""]

    items: list[dict[str, Any]] = []
    for resume_id, search_preset, letter_template, limit in itertools.product(resumes, searches, letters, limits):
        params = dict(defaults)
        if resume_id:
            params["resume_id"] = str(resume_id)
        if search_preset:
            params["search_preset"] = str(search_preset)
        if letter_template:
            params["letter_template"] = str(letter_template)
        if limit != "":
            params["limit"] = _maybe_int(limit)
        name_parts = [str(part) for part in (resume_id, search_preset, letter_template, limit) if str(part)]
        items.append({"name": "/".join(name_parts) or "default", "params": params})

    return {
        "status": "ok",
        "count": len(items),
        "items": items,
        "dropped_defaults": sorted(dropped),
    }


class _TemplateValues(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return ""


def _flatten(prefix: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    flattened: dict[str, Any] = {}
    for key, item in value.items():
        safe_key = _safe_key(str(key))
        if not safe_key:
            continue
        flat_key = f"{prefix}_{safe_key}" if prefix else safe_key
        flattened[flat_key] = item
        if isinstance(item, dict):
            flattened.update(_flatten(flat_key, item))
    return flattened


def _sanitize_defaults(params: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    safe: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in params.items():
        lowered = str(key).lower()
        if any(part in lowered for part in SECRET_KEY_PARTS) or any(
            part in lowered for part in MACHINE_SPECIFIC_KEY_PARTS
        ):
            dropped.append(str(key))
            continue
        safe[key] = value
    return safe, dropped


def _contact_value(profile: dict[str, Any], key: str) -> str:
    direct = _text(profile.get(key))
    if direct:
        return direct
    contacts = profile.get("contacts") or profile.get("contact") or {}
    if isinstance(contacts, dict):
        return _text(contacts.get(key))
    return ""


def _format_experience(value: Any) -> str:
    lines: list[str] = []
    for item in _list(value):
        if isinstance(item, dict):
            position = _text(item.get("position") or item.get("role") or item.get("title"))
            company = _text(item.get("company") or item.get("project"))
            period = _text(item.get("period") or item.get("dates"))
            description = _text(item.get("description") or item.get("details") or item.get("summary"))
            head = " @ ".join(part for part in (position, company) if part)
            if period:
                head = f"{head} ({period})" if head else period
            if description:
                lines.append(f"- {head}: {description}" if head else f"- {description}")
            elif head:
                lines.append(f"- {head}")
        else:
            text = _text(item)
            if text:
                lines.append(f"- {text}")
    return "\n".join(lines)


def _list_text(value: Any) -> str:
    return ", ".join(_text(item) for item in _list(value) if _text(item))


def _list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [value]


def _split_name(name: str) -> tuple[str, str]:
    parts = [part for part in name.split() if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _safe_key(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower())).strip("_")


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return _list_text(value)
    if isinstance(value, dict):
        return ", ".join(f"{key}: {_stringify(item)}" for key, item in value.items() if _stringify(item))
    if value is None:
        return ""
    return str(value)


def _text(value: Any) -> str:
    return _stringify(value).strip()


def _maybe_int(value: Any) -> Any:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return value
