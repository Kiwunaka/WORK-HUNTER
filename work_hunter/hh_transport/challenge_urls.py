"""Small, shared URL sanitizer for HH challenge pointers."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_BACKURL = "https://hh.ru/"


def sanitize_hh_challenge_url(value: Any) -> str:
    """Return a safe HH URL, retaining only the official captcha state.

    HH uses the opaque ``state`` query value to bind the captcha page to the
    rejected API request.  All other query data is discarded.  The fixed
    backurl is required by HH's API documentation and never comes from input.
    """
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > 4096:
        return ""
    try:
        parts = urlsplit(value)
        host = parts.hostname.casefold().rstrip(".") if parts.hostname else ""
        port = parts.port
    except ValueError:
        return ""
    if (
        parts.scheme.casefold() not in {"http", "https"}
        or not host
        or (
            host != "hh.ru"
            and not host.endswith(".hh.ru")
            and host != "hh.kz"
            and not host.endswith(".hh.kz")
        )
        or parts.username is not None
        or parts.password is not None
        or any(ord(character) < 0x20 for character in parts.path)
    ):
        return ""
    netloc = host if port is None else f"{host}:{port}"
    path = parts.path or "/"
    if path == "/account/captcha":
        try:
            state_values = [
                item
                for key, item in parse_qsl(parts.query, keep_blank_values=True)
                if key == "state"
            ]
        except ValueError:
            return ""
        if len(state_values) > 1:
            return ""
        state = state_values[0] if state_values else ""
        if state and (
            len(state) > 4096
            or any(ord(character) < 0x20 for character in state)
        ):
            return ""
        query = [("state", state)] if state else []
        query.append(("backurl", _BACKURL))
        return urlunsplit(
            (parts.scheme.casefold(), netloc, path, urlencode(query), "")
        )
    return urlunsplit((parts.scheme.casefold(), netloc, path, "", ""))


def stateful_hh_captcha_url(value: Any) -> str:
    """Allow automatic solving only for HH's state-bound CAPTCHA page."""
    url = sanitize_hh_challenge_url(value)
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in {"hh.ru", "hh.kz"}
        or parts.port is not None
        or parts.path != "/account/captcha"
    ):
        return ""
    state = [item for key, item in parse_qsl(parts.query) if key == "state"]
    return url if len(state) == 1 and state[0] else ""
