from __future__ import annotations

import html as html_lib
import re
from typing import Any

from .common import absolute_url, clean_text


APPLY_HINTS = (
    "apply",
    "application",
    "applications",
    "candidate",
    "cover",
    "letter",
    "message",
    "motivation",
    "respond",
    "response",
    "reply",
    "send",
)
LOGIN_HINTS = ("login", "signin", "sign-in", "auth", "password", "session")
SEARCH_HINTS = ("search", "filter", "subscribe", "newsletter")
IGNORED_FIELD_TYPES = {"hidden", "submit", "button", "reset", "file", "image"}


def detect_apply_mechanism(
    html: str,
    *,
    source: str,
    source_id: str,
    url: str,
    base_url: str,
    form_signature: str | None = None,
) -> dict[str, Any]:
    signature = form_signature or f"{source}_apply_form:v1"
    apply_url = _extract_apply_url(html, base_url=base_url, fallback=url)
    ranked = sorted(
        (
            (_score_apply_form(form), form)
            for form in _extract_forms(html, url=url, base_url=base_url)
            if not form.get("has_password")
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < 2:
        return {
            "status": "not_detected",
            "source_id": str(source_id),
            "mechanism": "external_page",
            "apply_url": apply_url,
            "form_signature": "unknown",
            "detector": "apply_forms:v1",
            "form": {"form_url": apply_url, "fields": []},
            "cover_letter_required": False,
            "test_required": _text_mentions_test(html),
            "captcha": _text_mentions_captcha(html),
        }

    form = ranked[0][1]
    fields = list(form.get("fields") or [])
    cover_letter_required = any(
        bool(field.get("required")) and _field_mentions_cover_letter(field)
        for field in fields
    )
    return {
        "status": "detected",
        "source_id": str(source_id),
        "mechanism": "form",
        "apply_url": apply_url if apply_url != url else form["form_url"],
        "form_signature": signature,
        "detector": "apply_forms:v1",
        "form": {
            "form_url": form["form_url"],
            "method": form["method"],
            "fields": fields,
            "form_signature": signature,
        },
        "cover_letter_required": cover_letter_required,
        "test_required": _text_mentions_test(html),
        "captcha": _text_mentions_captcha(html),
    }


def _extract_forms(html: str, *, url: str, base_url: str) -> list[dict[str, Any]]:
    forms: list[dict[str, Any]] = []
    for match in re.finditer(r"<form\b(?P<attrs>[^>]*)>(?P<body>.*?)</form>", html or "", flags=re.IGNORECASE | re.DOTALL):
        attrs = _html_attrs(match.group("attrs"))
        body = match.group("body")
        fields = _extract_form_fields(body)
        has_password = bool(re.search(r"<input\b[^>]*\btype\s*=\s*['\"]?password", body, flags=re.IGNORECASE))
        if not fields:
            continue
        forms.append(
            {
                "form_url": absolute_url(base_url, attrs.get("action") or url),
                "method": str(attrs.get("method") or "POST").strip().upper(),
                "fields": fields,
                "attrs": attrs,
                "body_text": clean_text(body),
                "has_password": has_password,
            }
        )
    return forms


def _extract_form_fields(html: str) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    patterns = (
        (r"<input\b(?P<attrs>[^>]*)>", "text"),
        (r"<textarea\b(?P<attrs>[^>]*)>", "textarea"),
        (r"<select\b(?P<attrs>[^>]*)>", "select"),
    )
    for pattern, default_type in patterns:
        for match in re.finditer(pattern, html or "", flags=re.IGNORECASE | re.DOTALL):
            attrs = _html_attrs(match.group("attrs"))
            name = clean_text(str(attrs.get("name") or attrs.get("id") or ""))
            if not name:
                continue
            input_type = clean_text(str(attrs.get("type") or default_type)).lower()
            if input_type in IGNORED_FIELD_TYPES:
                continue
            label = clean_text(
                str(
                    attrs.get("aria-label")
                    or attrs.get("placeholder")
                    or attrs.get("title")
                    or name
                )
            )
            fields.append(
                {
                    "name": name,
                    "label": label,
                    "type": input_type,
                    "required": _bool_attr(attrs, "required"),
                }
            )
    return fields


def _score_apply_form(form: dict[str, Any]) -> int:
    attrs = form.get("attrs") if isinstance(form.get("attrs"), dict) else {}
    fields = list(form.get("fields") or [])
    action = str(form.get("form_url") or "").lower()
    attr_text = " ".join(str(value) for value in attrs.values()).lower()
    field_text = " ".join(f"{field.get('name') or ''} {field.get('label') or ''}" for field in fields).lower()
    body_text = clean_text(str(form.get("body_text") or "")).lower()
    haystack = f"{action} {attr_text} {field_text} {body_text}"

    score = 0
    if str(form.get("method") or "").upper() == "POST":
        score += 1
    if any(hint in haystack for hint in APPLY_HINTS):
        score += 4
    if any(_field_mentions_cover_letter(field) for field in fields):
        score += 4
    if any(_field_mentions(field, ("email", "mail")) for field in fields):
        score += 1
    if any(_field_mentions(field, ("name", "fullname", "full_name", "phone", "contact")) for field in fields):
        score += 1
    if any(hint in haystack for hint in SEARCH_HINTS):
        score -= 3
    if any(hint in action for hint in LOGIN_HINTS) and not any(hint in haystack for hint in APPLY_HINTS):
        score -= 6
    return score


def _extract_apply_url(html: str, *, base_url: str, fallback: str) -> str:
    for match in re.finditer(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", html or "", flags=re.IGNORECASE | re.DOTALL):
        attrs = _html_attrs(match.group("attrs"))
        href = clean_text(str(attrs.get("href") or ""))
        if not href:
            continue
        text = clean_text(match.group("body")).lower()
        haystack = f"{href} {text}".lower()
        if any(part in haystack for part in APPLY_HINTS):
            return absolute_url(base_url, href)
    return fallback


def _html_attrs(raw_attrs: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(
        r"""(?P<name>[a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)'|(?P<bare>[^\s"'=<>`]+)))?""",
        raw_attrs or "",
    ):
        name = match.group("name").lower()
        value = match.group("double") or match.group("single") or match.group("bare") or ""
        attrs[name] = html_lib.unescape(value)
    return attrs


def _bool_attr(attrs: dict[str, str], name: str) -> bool:
    return name in attrs and str(attrs.get(name) or name).lower() not in {"false", "0", "no"}


def _field_mentions(field: dict[str, Any], terms: tuple[str, ...]) -> bool:
    text = f"{field.get('name') or ''} {field.get('label') or ''}".lower()
    return any(term in text for term in terms)


def _field_mentions_cover_letter(field: dict[str, Any]) -> bool:
    return _field_mentions(
        field,
        ("cover", "letter", "message", "motivation", "comment", "response"),
    )


def _text_mentions_test(text: str) -> bool:
    lowered = clean_text(text).lower()
    return any(part in lowered for part in ("test task", "screening task", "coding task", "take-home"))


def _text_mentions_captcha(text: str) -> bool:
    lowered = clean_text(text).lower()
    return "captcha" in lowered or "recaptcha" in lowered or "hcaptcha" in lowered
