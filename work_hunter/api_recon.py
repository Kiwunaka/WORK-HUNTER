from __future__ import annotations

import json
import re
import urllib.parse
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "csrf",
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "email",
    "session",
)

JOB_HINTS = ("vacancy", "vacancies", "job", "jobs", "position", "positions", "search")
APPLY_HINTS = ("apply", "application", "applications", "response", "respond", "cover", "negotiation")
PROFILE_HINTS = ("profile", "me", "account", "resume", "cv", "candidate")


@dataclass
class ApiEndpoint:
    method: str
    url: str
    status: int
    headers: dict[str, str]
    post_data: str
    response_mime: str
    response_size: int
    response_json_preview: Any
    tags: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_har(path: str | Path, *, allowed_hosts: set[str] | None = None) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8-sig") as fh:
        har = json.load(fh)
    endpoints = endpoint_inventory_from_har(har, allowed_hosts=allowed_hosts)
    by_host = Counter(urllib.parse.urlsplit(endpoint.url).netloc for endpoint in endpoints)
    by_tag: Counter[str] = Counter()
    for endpoint in endpoints:
        by_tag.update(endpoint.tags)
    return {
        "total_endpoints": len(endpoints),
        "by_host": dict(by_host),
        "by_tag": dict(by_tag),
        "endpoints": [endpoint.to_dict() for endpoint in endpoints],
    }


def endpoint_inventory_from_har(
    har: dict[str, Any],
    *,
    allowed_hosts: set[str] | None = None,
) -> list[ApiEndpoint]:
    endpoints: list[ApiEndpoint] = []
    for entry in ((har.get("log") or {}).get("entries") or []):
        request = entry.get("request") or {}
        response = entry.get("response") or {}
        raw_url = str(request.get("url") or "")
        if not raw_url:
            continue
        if allowed_hosts and not _host_allowed(raw_url, allowed_hosts):
            continue
        post_data = (request.get("postData") or {}).get("text") or ""
        content = response.get("content") or {}
        response_text = str(content.get("text") or "")
        response_mime = str(content.get("mimeType") or "")
        endpoint = ApiEndpoint(
            method=str(request.get("method") or "GET").upper(),
            url=_sanitize_url(raw_url),
            status=int(response.get("status") or 0),
            headers=_redact_headers(request.get("headers") or []),
            post_data=redact_sensitive_value(post_data),
            response_mime=response_mime,
            response_size=int(content.get("size") or len(response_text)),
            response_json_preview=_json_preview(response_text, response_mime),
            tags=_classify_endpoint(raw_url, post_data, response_text),
        )
        endpoints.append(endpoint)
    return _dedupe_endpoints(endpoints)


def redact_sensitive_value(value: str) -> str:
    text = str(value or "").lstrip("\ufeff")
    if not text:
        return ""
    parsed = _loads_json(text)
    if parsed is not None:
        return json.dumps(_redact_json(parsed), ensure_ascii=False, separators=(",", ":"))
    if _looks_like_secret_text(text):
        return "***"
    return text


def _redact_headers(headers: list[dict[str, Any]]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for header in headers:
        name = str(header.get("name") or "")
        if not name:
            continue
        value = str(header.get("value") or "")
        redacted[name] = "***" if _sensitive_key(name) else redact_sensitive_value(value)
    return redacted


def _sanitize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    safe_query = [
        (key, "***" if _sensitive_key(key) else value)
        for key, value in query
    ]
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(safe_query),
            "",
        )
    )


def _host_allowed(url: str, allowed_hosts: set[str]) -> bool:
    host = urllib.parse.urlsplit(url).hostname or ""
    normalized = host.lower()
    for allowed in allowed_hosts:
        item = allowed.lower()
        if normalized == item or normalized.endswith(f".{item}"):
            return True
    return False


def _classify_endpoint(url: str, post_data: str, response_text: str) -> list[str]:
    text = f"{url} {post_data} {response_text[:2000]}".lower()
    tags: list[str] = []
    if any(hint in text for hint in JOB_HINTS):
        tags.append("jobs")
    if any(hint in text for hint in APPLY_HINTS):
        tags.append("apply")
    if any(hint in text for hint in PROFILE_HINTS):
        tags.append("profile")
    if "/api/" in text or "application/json" in text:
        tags.append("api")
    return tags


def _json_preview(text: str, mime: str) -> Any:
    if not text:
        return None
    if "json" not in (mime or "").lower() and not text.lstrip().startswith(("{", "[")):
        return None
    parsed = _loads_json(text)
    if parsed is None:
        return None
    return _trim_json(_redact_json(parsed))


def _trim_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        return {
            str(key): _trim_json(item, depth=depth + 1)
            for key, item in list(value.items())[:12]
        }
    if isinstance(value, list):
        return [_trim_json(item, depth=depth + 1) for item in value[:5]]
    if isinstance(value, str) and len(value) > 240:
        return f"{value[:237]}..."
    return value


def _loads_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("***" if _sensitive_key(str(key)) else _redact_json(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str) and _looks_like_secret_text(value):
        return "***"
    return value


def _sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _looks_like_secret_text(text: str) -> bool:
    lowered = text.lower()
    if lowered.startswith(("bearer ", "basic ")):
        return True
    if any(part in lowered for part in ("sessionid=", "access_token=", "refresh_token=", "authorization=")):
        return True
    return bool(re.fullmatch(r"[A-Za-z0-9_\-]{32,}", text.strip()))


def _dedupe_endpoints(endpoints: list[ApiEndpoint]) -> list[ApiEndpoint]:
    unique: list[ApiEndpoint] = []
    seen: set[tuple[str, str]] = set()
    for endpoint in endpoints:
        key = (endpoint.method, endpoint.url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(endpoint)
    return unique
