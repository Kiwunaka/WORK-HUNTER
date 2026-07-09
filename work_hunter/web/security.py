from __future__ import annotations

import ipaddress
import socket
from http import HTTPStatus
from urllib.parse import urlsplit


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def _is_visible_ascii(value: str) -> bool:
    return bool(value) and all(0x21 <= ord(char) <= 0x7E for char in value)


def _canonical_ipv4_loopback(host: str) -> str | None:
    if host.casefold() == "localhost":
        return "localhost"
    try:
        address = ipaddress.IPv4Address(host)
    except ValueError:
        return None
    return str(address) if address.is_loopback else None


def _host_authority(value: str) -> tuple[str, int] | None:
    if not _is_visible_ascii(value) or any(char in value for char in "@/?#"):
        return None
    try:
        parsed = urlsplit(f"//{value}")
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.netloc.endswith(":")
    ):
        return None
    canonical_host = _canonical_ipv4_loopback(host)
    if canonical_host is None:
        return None
    return canonical_host, port if port is not None else 80


def _http_origin(value: str) -> tuple[str, int] | None:
    if not _is_visible_ascii(value):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "http"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.netloc.endswith(":")
    ):
        return None
    canonical_host = _canonical_ipv4_loopback(host)
    if canonical_host is None:
        return None
    return canonical_host, port if port is not None else 80


def ensure_loopback_listener(host: str) -> None:
    error = ValueError("Work Hunter UI must bind to an IPv4 loopback host")
    if not host or host != host.strip():
        raise error
    if host.casefold() == "localhost":
        try:
            addresses = socket.getaddrinfo(
                host,
                None,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise error from exc
        if not addresses:
            raise error
        for family, _, _, _, socket_address in addresses:
            try:
                address = ipaddress.IPv4Address(socket_address[0])
            except (ValueError, IndexError, TypeError) as exc:
                raise error from exc
            if family != socket.AF_INET or not address.is_loopback:
                raise error
        return
    if _canonical_ipv4_loopback(host) is None:
        raise error


def request_boundary_error(
    *,
    method: str,
    host_header: str,
    origin_header: str | None,
    content_type: str | None,
    listener_host: str,
    listener_port: int,
) -> tuple[HTTPStatus, str, str] | None:
    listener = _canonical_ipv4_loopback(listener_host)
    host = _host_authority(host_header)
    if listener is None or host is None or host[1] != listener_port:
        return HTTPStatus.FORBIDDEN, "host_not_loopback", "Loopback Host header required."
    if origin_header is not None:
        origin = _http_origin(origin_header)
        if origin is None or origin != host:
            return HTTPStatus.FORBIDDEN, "cross_origin_request", "Cross-origin requests are blocked."
    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        media_type = (content_type or "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            return HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "json_required", "application/json is required."
    return None
