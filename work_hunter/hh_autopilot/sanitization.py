from __future__ import annotations

import html
import re
import unicodedata
from datetime import date
from typing import Literal
from urllib.parse import unquote, urlsplit


_HTML_DECODE_LIMIT = 6
_PERCENT_DECODE_LIMIT = 6
_SECURITY_VIEW_LIMIT = 1_000_000
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
    """,
    re.IGNORECASE | re.VERBOSE,
)
_EMAIL_CANDIDATE = re.compile(
    r"""
    (?<![\w@])
    (?P<local>[\w.!#$%&'*+/=?^_`{|}~-]{1,64})
    @
    (?P<domain>[\w.-]{1,253})
    (?![\w@])
    """,
    re.UNICODE | re.VERBOSE,
)
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)")
_ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_ADDRESS_NUMBER_FIRST = re.compile(
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
_ADDRESS_STREET_FIRST = re.compile(
    r"""
    \b
    (?:[\w.'-]+\s+){1,6}
    (?:
        street|st|road|rd|avenue|ave|boulevard|blvd|lane|ln|drive|dr
    )\.?
    \s+\d{1,6}\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
_RUSSIAN_ADDRESS = re.compile(
    r"""
    \b
    (?:[\w.-]+(?:\s+[\w.-]+){0,3},\s*)?
    (?:улица|ул)\.?\s+
    [\w.'-]+(?:\s+[\w.'-]+){0,5}
    ,\s*(?:дом|д)\.?\s*\d{1,6}\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
_URL_CANDIDATE = re.compile(
    r"(?i)(?<![\w])(?:[a-z][a-z0-9+.-]{1,31})://[^\s<>\[\]{}\"']+"
)


def decode_html_fixed_point(value: str, *, field: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    if "\0" in value:
        raise ValueError(f"{field} must not contain NUL")
    if len(value) > _SECURITY_VIEW_LIMIT:
        raise ValueError(f"{field} exceeds the sanitizer security bound")
    current = value
    for _ in range(_HTML_DECODE_LIMIT):
        decoded = html.unescape(current)
        if decoded == current:
            return decoded
        current = decoded
    if html.unescape(current) == current:
        return current
    raise ValueError(f"{field} contains unstable nested HTML entities")


def _reject_hidden_controls(value: str, *, field: str) -> None:
    for character in value:
        category = unicodedata.category(character)
        if category in {"Cf", "Cs"} or (
            category == "Cc" and character not in "\t\n\r"
        ):
            raise ValueError(f"{field} contains hidden or control formatting")


def _percent_decode_fixed_point(value: str, *, field: str) -> str:
    current = value
    for _ in range(_PERCENT_DECODE_LIMIT):
        try:
            decoded = unquote(current, errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{field} contains invalid percent encoding") from exc
        decoded = unicodedata.normalize("NFKC", decoded)
        if len(decoded) > _SECURITY_VIEW_LIMIT:
            raise ValueError(f"{field} exceeds the sanitizer security bound")
        _reject_hidden_controls(decoded, field=field)
        if decoded == current:
            return decoded
        current = decoded
    try:
        stable = unicodedata.normalize(
            "NFKC",
            unquote(current, errors="strict"),
        )
    except UnicodeDecodeError as exc:
        raise ValueError(f"{field} contains invalid percent encoding") from exc
    if stable == current:
        return current
    raise ValueError(f"{field} contains unstable nested percent encoding")


def _security_view(value: str, *, field: str) -> str:
    _reject_hidden_controls(value, field=field)
    normalized = unicodedata.normalize("NFKC", value)
    if len(normalized) > _SECURITY_VIEW_LIMIT:
        raise ValueError(f"{field} exceeds the sanitizer security bound")
    _reject_hidden_controls(normalized, field=field)
    decoded = _percent_decode_fixed_point(normalized, field=field)
    return _SPACES.sub(" ", decoded).strip()


def _is_email_domain(domain: str) -> bool:
    domain = domain.rstrip(".")
    labels = domain.split(".")
    if len(labels) < 2 or any(not label for label in labels):
        return False
    encoded_labels: list[bytes] = []
    try:
        for label in labels:
            encoded = label.encode("idna")
            if (
                not encoded
                or len(encoded) > 63
                or encoded.startswith(b"-")
                or encoded.endswith(b"-")
            ):
                return False
            encoded_labels.append(encoded)
    except UnicodeError:
        return False
    return len(b".".join(encoded_labels)) <= 253


def _contains_email(value: str) -> bool:
    for match in _EMAIL_CANDIDATE.finditer(value):
        local = match.group("local")
        if (
            local.startswith(".")
            or local.endswith(".")
            or ".." in local
        ):
            continue
        if _is_email_domain(match.group("domain")):
            return True
    return False


def _contains_url_userinfo(value: str) -> bool:
    for match in _URL_CANDIDATE.finditer(value):
        candidate = match.group().rstrip(".,;!?)]}")
        candidate = _percent_decode_fixed_point(
            candidate,
            field="URL security view",
        )
        try:
            parsed = urlsplit(candidate)
        except ValueError:
            authority = candidate.split("://", 1)[-1].split("/", 1)[0]
            if "@" in authority:
                return True
            continue
        if parsed.netloc and (
            "@" in parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
        ):
            return True
    return False


def _without_valid_iso_dates(value: str) -> str:
    characters = list(value)
    for match in _ISO_DATE.finditer(value):
        try:
            date.fromisoformat(match.group())
        except ValueError:
            continue
        characters[match.start() : match.end()] = " " * len(match.group())
    return "".join(characters)


def _contains_phone(value: str) -> bool:
    phone_view = _without_valid_iso_dates(value)
    for match in _PHONE.finditer(phone_view):
        candidate = match.group().strip()
        if re.fullmatch(r"\d{4,}\s*-\s*\d{4,}", candidate):
            continue
        digits = sum(character.isdigit() for character in candidate)
        if 10 <= digits <= 15:
            return True
    return False


def _contains_sensitive_security_view(value: str) -> bool:
    if _CREDENTIAL.search(value) is not None:
        return True
    if _contains_email(value) or _contains_url_userinfo(value):
        return True
    if any(
        pattern.search(value) is not None
        for pattern in (
            _ADDRESS_NUMBER_FIRST,
            _ADDRESS_STREET_FIRST,
            _RUSSIAN_ADDRESS,
        )
    ):
        return True
    return _contains_phone(value)


def contains_sensitive_text(value: str) -> bool:
    if type(value) is not str:
        raise TypeError("value must be a string")
    try:
        view = _security_view(value, field="sensitive text")
    except ValueError:
        return True
    return _contains_sensitive_security_view(view)


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
    security_view = _security_view(decoded, field=field)
    has_markup = (
        _MARKUP.search(decoded) is not None
        or "<" in decoded
        or ">" in decoded
    )
    security_has_markup = (
        _MARKUP.search(security_view) is not None
        or "<" in security_view
        or ">" in security_view
    )
    if security_has_markup and not has_markup:
        raise ValueError(f"{field} contains encoded or compatibility markup")
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
    if _contains_sensitive_security_view(security_view):
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
