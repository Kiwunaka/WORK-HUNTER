from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .api_discovery import _sanitize_url, discover_api_candidates_from_url
from .sources.common import USER_AGENT, fetch_url


@dataclass(frozen=True)
class ProbeHTTPResponse:
    status: int
    headers: dict[str, str]
    body: str
    error: str = ""


def probe_api_candidates_from_url(
    url: str,
    *,
    fetcher: Callable[[str], str] = fetch_url,
    requester: Callable[..., ProbeHTTPResponse] | None = None,
    allowed_hosts: set[str] | None = None,
    max_scripts: int = 12,
    limit: int = 25,
    timeout: int = 10,
) -> dict[str, Any]:
    discovery = discover_api_candidates_from_url(
        url,
        fetcher=fetcher,
        allowed_hosts=allowed_hosts,
        max_scripts=max_scripts,
    )
    probe = probe_api_endpoints(
        _prioritized_endpoints(discovery["endpoints"]),
        requester=requester or request_endpoint_get,
        limit=limit,
        timeout=timeout,
    )
    return {
        "url": url,
        "discovery": discovery,
        "probe": probe,
    }


def probe_api_endpoints(
    endpoints: Iterable[str | dict[str, Any]],
    *,
    requester: Callable[..., ProbeHTTPResponse] | None = None,
    limit: int | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    request = requester or request_endpoint_get
    probes: list[dict[str, Any]] = []
    for item in endpoints:
        url = item["url"] if isinstance(item, dict) else str(item)
        if not url:
            continue
        response = request(url, timeout=timeout)
        probes.append(_probe_result(url, response))
        if limit is not None and len(probes) >= limit:
            break
    by_access: Counter[str] = Counter(probe["access"] for probe in probes)
    return {
        "total_probed": len(probes),
        "by_access": dict(by_access),
        "probes": probes,
    }


def request_endpoint_get(url: str, *, timeout: int = 10) -> ProbeHTTPResponse:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(4096).decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            return ProbeHTTPResponse(
                status=int(response.status),
                headers={key.lower(): value for key, value in response.headers.items()},
                body=body,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        return ProbeHTTPResponse(
            status=int(exc.code),
            headers={key.lower(): value for key, value in exc.headers.items()},
            body=body,
        )
    except urllib.error.URLError as exc:
        return ProbeHTTPResponse(status=0, headers={}, body="", error=str(exc.reason))


def _prioritized_endpoints(endpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        endpoint
        for _, endpoint in sorted(
            enumerate(endpoints),
            key=lambda item: (_endpoint_priority(item[1]), item[0]),
        )
    ]


def _endpoint_priority(endpoint: dict[str, Any]) -> int:
    tags = set(endpoint.get("tags") or [])
    score = 0
    if "api" not in tags:
        score += 100
    if "jobs" not in tags:
        score += 10
    if "apply" in tags:
        score -= 5
    if "profile" in tags:
        score -= 2
    return score


def _probe_result(url: str, response: ProbeHTTPResponse) -> dict[str, Any]:
    content_type = response.headers.get("content-type") or response.headers.get("Content-Type") or ""
    return {
        "url": _sanitize_url(url),
        "status": response.status,
        "access": _access_for_status(response.status),
        "content_type": content_type,
        "json_preview": _json_preview(response.body, content_type),
        "error": response.error,
    }


def _access_for_status(status: int) -> str:
    if 200 <= status < 300:
        return "public"
    if status in {401, 403}:
        return "auth_required"
    if status in {400, 405, 415, 422}:
        return "method_or_payload_required"
    if status == 404:
        return "missing"
    if 300 <= status < 400:
        return "redirect"
    if status == 0:
        return "network_error"
    return "unknown"


def _json_preview(body: str, content_type: str) -> Any:
    text = (body or "").strip()
    if not text:
        return None
    if "json" not in content_type.lower() and not text.startswith(("{", "[")):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return _trim_json(parsed)


def _trim_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        return {str(key): _trim_json(item, depth=depth + 1) for key, item in list(value.items())[:12]}
    if isinstance(value, list):
        return [_trim_json(item, depth=depth + 1) for item in value[:5]]
    if isinstance(value, str) and len(value) > 240:
        return f"{value[:237]}..."
    return value
