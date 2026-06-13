from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Mapping


USER_AGENT = "WorkHunter/0.1 (personal@example.invalid)"


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
