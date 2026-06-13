from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


URL_RE = re.compile(r"https?://[^\s\"'<>]+")
ALLOWED_FORM_MODES = {"off", "manual", "agent"}


@dataclass
class FormReview:
    form_url: str = ""
    answers: dict[str, str] = field(default_factory=dict)
    unknown_fields: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    risk_flags: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_form_mode(value: str | None) -> str:
    mode = str(value or "manual").strip().lower()
    if mode not in ALLOWED_FORM_MODES:
        raise ValueError(f"Unsupported HH form_mode: {value}")
    return mode


def detect_manual_form_url(payload: dict[str, Any]) -> str:
    for key in (
        "response_url",
        "form_url",
        "manual_form_url",
        "redirect_url",
        "location",
        "url",
    ):
        value = str(payload.get(key) or "").strip()
        if _looks_like_url(value):
            return value
    for value in _walk_values(payload):
        text = str(value or "")
        if not text:
            continue
        match = URL_RE.search(text)
        if match and _looks_like_form_url(match.group(0)):
            return match.group(0)
    return ""


def draft_form_review(
    form: dict[str, Any],
    *,
    persona: dict[str, Any] | None = None,
    resume: dict[str, Any] | None = None,
    vacancy: dict[str, Any] | None = None,
    extra_answers: dict[str, str] | None = None,
) -> FormReview:
    persona = persona or {}
    resume = resume or {}
    vacancy = vacancy or {}
    extra_answers = extra_answers or {}
    fields = list(_form_fields(form))
    answers: dict[str, str] = {}
    unknown_fields: list[dict[str, Any]] = []
    for index, field in enumerate(fields, start=1):
        normalized = _normalize_field(field, index)
        field_key = normalized["name"]
        explicit = extra_answers.get(field_key) or extra_answers.get(normalized["label"])
        answer = explicit or _draft_field_answer(normalized, persona=persona, resume=resume, vacancy=vacancy)
        if answer:
            answers[field_key] = answer
            continue
        if normalized["required"]:
            unknown_fields.append(normalized)
    risk_flags = ["manual_form_required"]
    if unknown_fields:
        risk_flags.append("unknown_form_fields")
    return FormReview(
        form_url=detect_manual_form_url(form),
        answers=answers,
        unknown_fields=unknown_fields,
        confidence=0.55 if unknown_fields else 0.85,
        risk_flags=risk_flags,
        payload={
            "fields": fields,
            "vacancy_id": _text_id(vacancy),
            "vacancy_name": _vacancy_name(vacancy),
            "resume_id": str(resume.get("id") or ""),
        },
    )


def _form_fields(form: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for key in ("fields", "questions", "items"):
        value = form.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item


def _normalize_field(field: dict[str, Any], index: int) -> dict[str, Any]:
    name = str(field.get("name") or field.get("id") or field.get("key") or f"field_{index}").strip()
    label = str(field.get("label") or field.get("title") or field.get("text") or name).strip()
    return {
        "name": name,
        "label": label,
        "type": str(field.get("type") or field.get("input_type") or "text"),
        "required": bool(field.get("required", True)),
        "options": field.get("options") or field.get("values") or [],
    }


def _draft_field_answer(
    field: dict[str, Any],
    *,
    persona: dict[str, Any],
    resume: dict[str, Any],
    vacancy: dict[str, Any],
) -> str:
    facts = persona.get("facts") or {}
    haystack = f"{field['name']} {field['label']}".strip().lower()
    if any(part in haystack for part in ("full_name", "fullname", "name", "fio", "\u0444\u0438\u043e", "\u0438\u043c\u044f")):
        return _first_fact(facts, "name", "full_name")
    if "email" in haystack or "e-mail" in haystack or "mail" in haystack:
        return _first_fact(facts, "email", "mail")
    if "phone" in haystack or "\u0442\u0435\u043b" in haystack:
        return _first_fact(facts, "phone", "telephone")
    if any(part in haystack for part in ("cover", "letter", "message", "comment", "motivation", "\u0441\u043e\u043f\u0440\u043e\u0432", "\u043f\u0438\u0441\u044c\u043c")):
        return _cover_letter_answer(persona=persona, resume=resume, vacancy=vacancy)
    if "salary" in haystack or "\u0437\u0430\u0440\u043f" in haystack:
        return _first_fact(facts, "desired_salary", "salary", "salary_min")
    if "github" in haystack:
        return _first_fact(facts, "github")
    if "linkedin" in haystack:
        return _first_fact(facts, "linkedin")
    if "portfolio" in haystack:
        return _first_fact(facts, "portfolio", "website")
    if "city" in haystack or "location" in haystack or "\u0433\u043e\u0440\u043e\u0434" in haystack:
        return _first_fact(facts, "city", "location", "locations")
    if "resume" in haystack or "\u0440\u0435\u0437\u044e\u043c" in haystack:
        return str(resume.get("title") or resume.get("id") or "")
    if "summary" in haystack or "about" in haystack or "\u043e \u0441\u0435\u0431\u0435" in haystack:
        return _first_fact(facts, "summary") or str(persona.get("body") or "").strip()
    return ""


def _cover_letter_answer(
    *,
    persona: dict[str, Any],
    resume: dict[str, Any],
    vacancy: dict[str, Any],
) -> str:
    facts = persona.get("facts") or {}
    summary = _first_fact(facts, "summary") or str(persona.get("body") or "").strip()
    vacancy_name = _vacancy_name(vacancy) or "this role"
    employer = _employer_name(vacancy)
    resume_title = str(resume.get("title") or "")
    parts = [f"Hello! I am interested in {vacancy_name}"]
    if employer:
        parts[0] += f" at {employer}"
    parts[0] += "."
    if summary:
        parts.append(summary)
    if resume_title:
        parts.append(f"My resume: {resume_title}.")
    return " ".join(parts).strip()


def _first_fact(facts: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = facts.get(key)
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value if item)
        value = str(value or "").strip()
        if value:
            return value
    return ""


def _text_id(item: dict[str, Any]) -> str:
    value = item.get("id")
    if isinstance(value, dict):
        return str(value.get("id") or value.get("name") or "")
    return str(value or "")


def _vacancy_name(vacancy: dict[str, Any]) -> str:
    return str(vacancy.get("name") or vacancy.get("title") or "")


def _employer_name(vacancy: dict[str, Any]) -> str:
    employer = vacancy.get("employer") or {}
    if isinstance(employer, dict):
        return str(employer.get("name") or "")
    return str(employer or "")


def _walk_values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for nested in value.values():
            yield from _walk_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_values(nested)
    else:
        yield value


def _looks_like_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _looks_like_form_url(value: str) -> bool:
    lowered = value.lower()
    return _looks_like_url(value) and any(part in lowered for part in ("form", "response", "vacancy_response", "apply"))
