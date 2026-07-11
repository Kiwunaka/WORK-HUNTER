from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Mapping


USER_AGENT = "WorkHunter/0.1 (personal@example.invalid)"
TRACKING_QUERY_KEYS = frozenset({"gclid", "yclid"})
_SAFE_TRACKING_QUERY_KEY_BYTES = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


def _query_component_bytes(value: str) -> bytes:
    encoded = value.replace("+", " ").encode("utf-8", errors="surrogatepass")
    return urllib.parse.unquote_to_bytes(encoded)


def _is_tracking_query_key(key: bytes) -> bool:
    if not key or any(byte not in _SAFE_TRACKING_QUERY_KEY_BYTES for byte in key):
        return False
    lowered = key.decode("ascii").lower()
    return lowered.startswith("utm_") or lowered in TRACKING_QUERY_KEYS


def _canonicalize_query(query: str) -> str:
    pairs: list[tuple[bytes, bytes]] = []
    if query:
        for field in query.split("&"):
            if not field:
                continue
            raw_key, _, raw_value = field.partition("=")
            key = _query_component_bytes(raw_key)
            if _is_tracking_query_key(key):
                continue
            pairs.append((key, _query_component_bytes(raw_value)))
    return urllib.parse.urlencode(sorted(pairs), doseq=True)


def canonicalize_job_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            _canonicalize_query(parsed.query),
            "",
        )
    )


def fetch_url(
    url: str,
    *,
    timeout: int = 20,
    headers: Mapping[str, str] | None = None,
) -> str:
    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc.reason}") from exc


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def absolute_url(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, href)


def last_path_part(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return parsed.path.rstrip("/").split("/")[-1]
