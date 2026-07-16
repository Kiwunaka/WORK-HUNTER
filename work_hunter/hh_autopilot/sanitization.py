from __future__ import annotations

import html
import re
from typing import Literal


_HTML_DECODE_LIMIT = 6
_SPACES = re.compile(r"\s+")
_MARKUP = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_DANGEROUS_MARKUP = re.compile(
    r"<\s*/?\s*(?:script|style|iframe|object|embed|svg|math)\b",
    re.IGNORECASE,
)
_CREDENTIAL = re.compile(
    r"""
    (?<![A-Za-z0-9_])
    (?:bearer|basic)\s+[^\s,;]+
    |
    (?<![A-Za-z0-9_])
    ["']?
    (?:
        access[_-]?token
        |refresh[_-]?token
        |authorization
        |set[_-]?cookie
        |cookie
        |session(?:[_-]?id)?
        |password
        |passwd
        |otp
        |api[_-]?key
        |client[_-]?secret
        |credential(?:s)?
        |secret
        |proxy(?:[_-]?(?:url|username|password|credential|credentials))?
    )
    ["']?
    \s*[:=]\s*
    ["']?[^\s,;"'}]+
    |
    \b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@
    """,
    re.IGNORECASE | re.VERBOSE,
)
_EMAIL = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)")
_ADDRESS = re.compile(
    r"""
    \b\d{1,6}\s+
    (?:[\w.'-]+\s+){0,5}
    (?:
        street|st|road|rd|avenue|ave|boulevard|blvd|lane|ln|drive|dr
        |улица|ул|проспект|пр-т|переулок|пер
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def decode_html_fixed_point(value: str, *, field: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    if "\0" in value:
        raise ValueError(f"{field} must not contain NUL")
    current = value
    for _ in range(_HTML_DECODE_LIMIT):
        decoded = html.unescape(current)
        if decoded == current:
            return decoded
        current = decoded
    if html.unescape(current) == current:
        return current
    raise ValueError(f"{field} contains unstable nested HTML entities")


def contains_sensitive_text(value: str) -> bool:
    if any(
        pattern.search(value) is not None
        for pattern in (_CREDENTIAL, _EMAIL, _ADDRESS)
    ):
        return True
    for match in _PHONE.finditer(value):
        candidate = match.group().strip()
        if re.fullmatch(r"\d{4,}\s*-\s*\d{4,}", candidate):
            continue
        if sum(character.isdigit() for character in candidate) >= 10:
            return True
    return False


def sanitize_text(
    value: str,
    *,
    field: str,
    maximum: int,
    allow_empty: bool = False,
    markup: Literal["reject", "strip"] = "reject",
    sensitive: Literal["reject", "redact"] = "reject",
    overflow: Literal["reject", "truncate"] = "reject",
    canonical: bool = False,
) -> str:
    decoded = decode_html_fixed_point(value, field=field)
    has_markup = (
        _MARKUP.search(decoded) is not None
        or "<" in decoded
        or ">" in decoded
    )
    if has_markup:
        if markup == "reject" or _DANGEROUS_MARKUP.search(decoded):
            raise ValueError(f"{field} must not contain markup")
        decoded = _MARKUP.sub(" ", decoded)
        if "<" in decoded or ">" in decoded:
            raise ValueError(f"{field} contains malformed markup")
    normalized = _SPACES.sub(" ", decoded).strip()
    if canonical:
        normalized = normalized.casefold()
    if not normalized and not allow_empty:
        raise ValueError(f"{field} must not be empty")
    if "\0" in normalized:
        raise ValueError(f"{field} must not contain NUL")
    if contains_sensitive_text(normalized):
        if sensitive == "redact":
            return "redacted"
        raise ValueError(f"{field} must not contain credentials or personal data")
    if len(normalized) > maximum:
        if overflow == "truncate":
            normalized = normalized[:maximum]
        else:
            raise ValueError(f"{field} must be at most {maximum} characters")
    return normalized


def phrase_matches_fact(term: str, fact: str) -> bool:
    normalized_term = _SPACES.sub(" ", term).strip().casefold()
    normalized_fact = _SPACES.sub(" ", fact).strip().casefold()
    if (
        not normalized_term
        or not normalized_fact
        or normalized_fact == "redacted"
    ):
        return False
    start = 0
    while True:
        index = normalized_fact.find(normalized_term, start)
        if index < 0:
            return False
        before_ok = (
            not normalized_term[0].isalnum()
            or index == 0
            or not normalized_fact[index - 1].isalnum()
        )
        end = index + len(normalized_term)
        after_ok = (
            not normalized_term[-1].isalnum()
            or end == len(normalized_fact)
            or not normalized_fact[end].isalnum()
        )
        if before_ok and after_ok:
            return True
        start = index + 1


__all__ = [
    "contains_sensitive_text",
    "decode_html_fixed_point",
    "phrase_matches_fact",
    "sanitize_text",
]
