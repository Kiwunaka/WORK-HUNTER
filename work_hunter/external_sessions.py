from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .api_recon import _trim_json, redact_sensitive_value
from .config import data_dir


SESSION_FILENAME = "external_sessions.json"
SESSION_HEADER_NAMES = {
    "accept",
    "authorization",
    "content-type",
    "cookie",
    "csrf-token",
    "origin",
    "referer",
    "user-agent",
    "x-csrf-token",
    "x-requested-with",
    "x-xsrf-token",
}
SENSITIVE_HEADER_PARTS = ("authorization", "cookie", "csrf", "token", "session")


@dataclass(frozen=True)
class ExternalHTTPResponse:
    status: int
    headers: dict[str, str]
    body: str
    error: str = ""


def sessions_file(root: str | Path) -> Path:
    return data_dir(root) / SESSION_FILENAME


def import_external_session_from_har(
    root: str | Path,
    name: str,
    har_path: str | Path,
    *,
    allowed_hosts: set[str],
) -> dict[str, Any]:
    with Path(har_path).open("r", encoding="utf-8-sig") as fh:
        har = json.load(fh)
    headers_by_host: dict[str, dict[str, str]] = {}
    for entry in ((har.get("log") or {}).get("entries") or []):
        request = entry.get("request") or {}
        url = str(request.get("url") or "")
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if not host or not _host_allowed(host, allowed_hosts):
            continue
        captured = _capture_headers(request.get("headers") or [])
        if captured:
            headers_by_host.setdefault(host, {}).update(captured)
    sessions = _load_sessions(root)
    sessions.setdefault("sessions", {})[name] = {
        "name": name,
        "hosts": sorted(headers_by_host),
        "headers_by_host": headers_by_host,
    }
    _save_sessions(root, sessions)
    return _session_summary(sessions["sessions"][name])


def list_external_sessions(root: str | Path) -> list[dict[str, Any]]:
    sessions = _load_sessions(root).get("sessions") or {}
    return [_session_summary(session) for _, session in sorted(sessions.items())]


def show_external_session(root: str | Path, name: str) -> dict[str, Any]:
    session = _load_sessions(root).get("sessions", {}).get(name)
    if not session:
        return {"status": "missing", "name": name}
    return _session_summary(session)


def call_external_session(
    root: str | Path,
    name: str,
    method: str,
    url: str,
    *,
    data: str | None = None,
    real: bool = False,
    unsafe_lab: bool = False,
    requester: Callable[..., ExternalHTTPResponse] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    session = _load_sessions(root).get("sessions", {}).get(name)
    if not session:
        return {"status": "blocked", "reason": "session_not_found", "name": name}
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    headers = _headers_for_host(session, host)
    if headers is None:
        return {
            "status": "blocked",
            "reason": "host_not_in_session",
            "name": name,
            "host": host,
            "allowed_hosts": session.get("hosts") or [],
        }
    request_view = {
        "method": method.upper(),
        "url": url,
        "headers": _mask_headers(headers),
        "data": _mask_data(data),
    }
    if not real:
        return {"status": "planned", "name": name, "request": request_view}
    if _is_mutating_method(method) and not unsafe_lab:
        return {
            "status": "blocked",
            "reason": "mutating_real_call_requires_unsafe_lab",
            "name": name,
            "request": request_view,
        }
    request = requester or request_with_external_session
    response = request(method.upper(), url, headers=headers, data=data, timeout=timeout)
    return {
        "status": "ok" if 200 <= response.status < 300 else "http_error",
        "name": name,
        "request": request_view,
        "response": _response_view(response),
    }


def request_with_external_session(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    data: str | None,
    timeout: int,
) -> ExternalHTTPResponse:
    body = data.encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            text = response.read(4096).decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            return ExternalHTTPResponse(
                status=int(response.status),
                headers={key.lower(): value for key, value in response.headers.items()},
                body=text,
            )
    except urllib.error.HTTPError as exc:
        text = exc.read(4096).decode("utf-8", errors="replace")
        return ExternalHTTPResponse(
            status=int(exc.code),
            headers={key.lower(): value for key, value in exc.headers.items()},
            body=text,
        )
    except urllib.error.URLError as exc:
        return ExternalHTTPResponse(status=0, headers={}, body="", error=str(exc.reason))


def _load_sessions(root: str | Path) -> dict[str, Any]:
    path = sessions_file(root)
    if not path.exists():
        return {"sessions": {}}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _save_sessions(root: str | Path, sessions: dict[str, Any]) -> None:
    path = sessions_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(sessions, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _capture_headers(headers: list[dict[str, Any]]) -> dict[str, str]:
    captured: dict[str, str] = {}
    for header in headers:
        name = str(header.get("name") or "")
        value = str(header.get("value") or "")
        if name and name.lower() in SESSION_HEADER_NAMES and value:
            captured[name] = value
    return captured


def _session_summary(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": session.get("name") or "",
        "hosts": list(session.get("hosts") or []),
        "headers": {
            host: _mask_headers(headers)
            for host, headers in (session.get("headers_by_host") or {}).items()
        },
    }


def _headers_for_host(session: dict[str, Any], host: str) -> dict[str, str] | None:
    headers_by_host = session.get("headers_by_host") or {}
    if host in headers_by_host:
        return dict(headers_by_host[host])
    return None


def _is_mutating_method(method: str) -> bool:
    return method.upper() in {"POST", "PUT", "PATCH", "DELETE"}


def _host_allowed(host: str, allowed_hosts: set[str]) -> bool:
    return any(host == item.lower() or host.endswith(f".{item.lower()}") for item in allowed_hosts)


def _mask_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: "***" if _sensitive_header(key) else value
        for key, value in headers.items()
    }


def _sensitive_header(name: str) -> bool:
    lowered = name.lower()
    return any(part in lowered for part in SENSITIVE_HEADER_PARTS)


def _mask_data(data: str | None) -> str:
    if not data:
        return ""
    return redact_sensitive_value(data)


def _response_view(response: ExternalHTTPResponse) -> dict[str, Any]:
    content_type = response.headers.get("content-type") or ""
    safe_body = redact_sensitive_value(response.body)
    return {
        "status": response.status,
        "content_type": content_type,
        "json_preview": _json_preview(safe_body, content_type),
        "body_preview": safe_body[:500],
        "error": response.error,
    }


def _json_preview(body: str, content_type: str) -> Any:
    text = (body or "").strip()
    if not text:
        return None
    if "json" not in content_type.lower() and not text.startswith(("{", "[")):
        return None
    try:
        return _trim_json(json.loads(text))
    except json.JSONDecodeError:
        return None
