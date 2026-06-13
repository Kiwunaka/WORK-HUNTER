from __future__ import annotations

import html
import re
import urllib.parse
from collections import Counter
from typing import Any, Callable

from .sources.common import fetch_url


SENSITIVE_QUERY_PARTS = (
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "session",
    "api_key",
    "apikey",
)

API_HINTS = (
    "/api/",
    "/graphql",
    "/trpc",
    "/candidate",
    "/candidates",
    "/vacancy",
    "/vacancies",
    "/jobs",
    "/offers",
    "/applications",
    "/apply",
    "/cvs",
)

JOB_HINTS = ("vacancy", "vacancies", "job", "jobs", "offer", "offers", "browse", "search")
APPLY_HINTS = ("apply", "application", "applications", "cover_letter", "response", "respond")
PROFILE_HINTS = ("profile", "me", "account", "resume", "cv", "cvs")


def discover_api_candidates_from_url(
    url: str,
    *,
    fetcher: Callable[[str], str] = fetch_url,
    allowed_hosts: set[str] | None = None,
    max_scripts: int = 12,
) -> dict[str, Any]:
    html_text = fetcher(url)
    scripts = _script_urls(url, html_text)[:max_scripts]
    documents: list[tuple[str, str]] = [(url, html_text)]
    for script_url in scripts:
        try:
            documents.append((script_url, fetcher(script_url)))
        except Exception:
            continue

    endpoints = _dedupe_endpoints(
        endpoint
        for source_url, text in documents
        for endpoint in _extract_endpoint_candidates(text, base_url=source_url, allowed_hosts=allowed_hosts)
    )
    by_tag: Counter[str] = Counter()
    for endpoint in endpoints:
        by_tag.update(endpoint["tags"])
    return {
        "url": url,
        "scripts_found": len(scripts),
        "scripts_scanned": max(0, len(documents) - 1),
        "total_endpoints": len(endpoints),
        "by_tag": dict(by_tag),
        "endpoints": endpoints,
    }


def _script_urls(base_url: str, text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r'<script\b[^>]*\bsrc=["\']([^"\']+)["\']', text or "", flags=re.IGNORECASE):
        script_url = urllib.parse.urljoin(base_url, html.unescape(match.group(1)))
        if script_url not in seen:
            seen.add(script_url)
            urls.append(script_url)
    return urls


def _extract_endpoint_candidates(
    text: str,
    *,
    base_url: str,
    allowed_hosts: set[str] | None,
) -> list[dict[str, Any]]:
    candidates: list[str] = []
    api_origins = _api_origins(text or "")
    candidates.extend(_js_base_concat_candidates(text or ""))
    patterns = (
        r'https?://[^"\'`<>\s\\]+',
        r'(?<!:)["\'](?P<path>/(?:api|graphql|trpc|candidate|candidates|vacancy|vacancies|jobs?|offers?|applications?|apply|cvs)[^"\'`<>\s\\]*)["\']',
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text or "", flags=re.IGNORECASE):
            raw = match.groupdict().get("path") or match.group(0)
            raw = raw.strip("\"'`),;")
            if _looks_interesting(raw):
                candidates.append(raw)
                if match.groupdict().get("path"):
                    candidates.extend(f"{origin}{raw}" for origin in api_origins)

    endpoints: list[dict[str, Any]] = []
    for raw in candidates:
        endpoint_url = urllib.parse.urljoin(base_url, html.unescape(raw))
        if allowed_hosts and not _host_allowed(endpoint_url, allowed_hosts):
            continue
        sanitized = _sanitize_url(endpoint_url)
        endpoints.append(
            {
                "url": sanitized,
                "host": urllib.parse.urlsplit(sanitized).netloc,
                "tags": _tags_for_url(sanitized),
            }
        )
    return endpoints


def _js_base_concat_candidates(text: str) -> list[str]:
    bases: dict[str, str] = {}
    for match in re.finditer(
        r'\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*["\'](https?://[^"\']+)["\']',
        text,
    ):
        bases[match.group(1)] = match.group(2).rstrip("/")
    candidates: list[str] = []
    for name, base in bases.items():
        pattern = rf'\b{re.escape(name)}\s*\+\s*["\'](?P<path>/[^"\']+)["\']'
        for match in re.finditer(pattern, text):
            path = match.group("path")
            if _looks_interesting(path):
                candidates.append(f"{base}{path}")
    return candidates


def _api_origins(text: str) -> list[str]:
    origins: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r'https?://[^"\'`<>\s\\]+', text, flags=re.IGNORECASE):
        raw = html.unescape(match.group(0)).strip("\"'`),;")
        parsed = urllib.parse.urlsplit(raw)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        if not parsed.scheme or not parsed.netloc:
            continue
        if not (_host_looks_like_api(host) or path == "/api" or path.startswith("/api/")):
            continue
        origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        if origin in seen:
            continue
        seen.add(origin)
        origins.append(origin)
    return origins


def _host_looks_like_api(host: str) -> bool:
    first_label = host.split(".", 1)[0]
    return first_label == "api" or first_label.endswith("-api") or first_label.startswith("api-")


def _looks_interesting(value: str) -> bool:
    lowered = value.lower()
    return any(hint in lowered for hint in API_HINTS)


def _host_allowed(url: str, allowed_hosts: set[str]) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    for allowed in allowed_hosts:
        item = allowed.lower()
        if host == item or host.endswith(f".{item}"):
            return True
    return False


def _sanitize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    safe_query = [
        (key, "***" if _sensitive_query_key(key) else value)
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


def _sensitive_query_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_QUERY_PARTS)


def _tags_for_url(url: str) -> list[str]:
    lowered = url.lower()
    tags: list[str] = []
    if any(hint in lowered for hint in JOB_HINTS):
        tags.append("jobs")
    if any(hint in lowered for hint in APPLY_HINTS):
        tags.append("apply")
    if any(hint in lowered for hint in PROFILE_HINTS):
        tags.append("profile")
    if "/api/" in lowered or "/graphql" in lowered or "/trpc" in lowered:
        tags.append("api")
    return tags


def _dedupe_endpoints(endpoints: Any) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for endpoint in endpoints:
        url = endpoint["url"]
        if url in seen:
            continue
        seen.add(url)
        unique.append(endpoint)
    return unique
