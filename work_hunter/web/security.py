from __future__ import annotations

import ipaddress
from http import HTTPStatus
from urllib.parse import urlsplit


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def _host_and_port(value: str) -> tuple[str, int | None]:
    raw = value.strip()
    if raw == "::1":
        return "::1", None
    parsed = urlsplit(f"//{raw}")
    return (parsed.hostname or "").lower(), parsed.port


def _is_loopback(value: str) -> bool:
    host, _ = _host_and_port(value)
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def ensure_loopback_listener(host: str) -> None:
    if not _is_loopback(host):
        raise ValueError("Work Hunter UI must bind to a loopback host")


def request_boundary_error(
    *,
    method: str,
    host_header: str,
    origin_header: str | None,
    content_type: str | None,
    listener_host: str,
    listener_port: int,
) -> tuple[HTTPStatus, str, str] | None:
    host, host_port = _host_and_port(host_header)
    if not _is_loopback(host) or host_port != listener_port:
        return HTTPStatus.FORBIDDEN, "host_not_loopback", "Loopback Host header required."
    if origin_header:
        origin = urlsplit(origin_header)
        origin_port = origin.port or (443 if origin.scheme == "https" else 80)
        if origin.scheme != "http" or not _is_loopback(origin.netloc) or origin_port != listener_port:
            return HTTPStatus.FORBIDDEN, "cross_origin_request", "Cross-origin requests are blocked."
    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        media_type = (content_type or "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            return HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "json_required", "application/json is required."
    return None
