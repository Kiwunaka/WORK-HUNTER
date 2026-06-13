from __future__ import annotations

import json
import urllib.parse
from typing import Any, Callable

from ..models import Job
from .common import absolute_url, clean_text, fetch_url


BASE_URL = "https://getmatch.ru"
API_URL = f"{BASE_URL}/api/offers"


class GetmatchSource:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        fetcher: Callable[[str], str] | None = None,
    ):
        self.config = config
        self.fetcher = fetch_url if fetcher is None else fetcher

    def collect(self, profile: dict[str, Any], limit: int | None = None) -> list[Job]:
        del profile
        pages = max(1, int(self.config.get("pages", 1) or 1))
        per_page = max(1, int(self.config.get("per_page", 50) or 50))
        fetch_details = bool(self.config.get("fetch_details", True))
        jobs: list[Job] = []
        seen: set[str] = set()
        for page in range(pages):
            payload = json.loads(self.fetcher(_offers_url(per_page=per_page, offset=page * per_page, config=self.config)))
            for raw in payload.get("offers") or []:
                if raw.get("is_active") is False:
                    continue
                offer_id = str(raw.get("id") or "")
                if not offer_id or offer_id in seen:
                    continue
                seen.add(offer_id)
                if fetch_details:
                    try:
                        raw = json.loads(self.fetcher(f"{API_URL}/{urllib.parse.quote(offer_id)}"))
                    except Exception:
                        pass
                jobs.append(parse_getmatch_offer(raw))
                if limit is not None and len(jobs) >= limit:
                    return jobs
        return jobs

    def get_offer(self, offer_id: str) -> dict[str, Any]:
        return json.loads(self.fetcher(f"{API_URL}/{urllib.parse.quote(str(offer_id))}"))


def parse_getmatch_offer(raw: dict[str, Any]) -> Job:
    offer_id = str(raw.get("id") or "")
    title = clean_text(str(raw.get("position") or raw.get("title") or ""))
    salary_from = _int_or_none(raw.get("salary_display_from"))
    salary_to = _int_or_none(raw.get("salary_display_to"))
    currency = clean_text(str(raw.get("salary_currency") or ""))
    salary_text = clean_text(str(raw.get("salary_description") or ""))
    if not salary_text:
        if salary_from and salary_to:
            salary_text = f"{salary_from}-{salary_to} {currency}".strip()
        elif salary_from:
            salary_text = f"from {salary_from} {currency}".strip()
        elif salary_to:
            salary_text = f"up to {salary_to} {currency}".strip()
    description = _description(raw)
    return Job(
        source="getmatch",
        source_id=offer_id,
        url=absolute_url(BASE_URL, str(raw.get("url") or f"/vacancies/{offer_id}")),
        title=title,
        company=clean_text(str((raw.get("company") or {}).get("name") or "")),
        salary_text=salary_text,
        salary_from=salary_from,
        salary_to=salary_to,
        currency=currency,
        location=_locations(raw),
        remote=_remote(raw),
        description=description or title,
        published_at=clean_text(str(raw.get("published_at") or "")),
    )


def _offers_url(*, per_page: int, offset: int, config: dict[str, Any]) -> str:
    params: dict[str, str] = {"limit": str(per_page), "offset": str(offset)}
    if config.get("in_days"):
        params["pa"] = str(config["in_days"])
    return f"{API_URL}?{urllib.parse.urlencode(params)}"


def _description(raw: dict[str, Any]) -> str:
    parts = [
        clean_text(str(raw.get("offer_description") or "")),
        clean_text(str(raw.get("description") or "")),
        clean_text(str(raw.get("short_description") or "")),
        clean_text(str(raw.get("stack_description") or "")),
        ", ".join(_string_items(raw.get("stack"))),
        ", ".join(_skill_names(raw.get("skills_objects"))),
    ]
    if raw.get("cover_letter_required") is True:
        parts.append("Cover letter required")
    placeholder = clean_text(str(raw.get("cover_letter_placeholder") or ""))
    if placeholder:
        parts.append(f"Cover letter hint: {placeholder}")
    return clean_text(" ".join(part for part in parts if part))


def _locations(raw: dict[str, Any]) -> str:
    labels: list[str] = []
    for item in raw.get("location_items") or []:
        if not isinstance(item, dict) or item.get("exclude"):
            continue
        label = clean_text(str(item.get("label") or ""))
        if label:
            labels.append(label)
    for item in raw.get("display_locations") or []:
        if not isinstance(item, dict):
            continue
        parts = [clean_text(str(item.get("city") or "")), clean_text(str(item.get("country") or ""))]
        label = ", ".join(part for part in parts if part)
        if label:
            labels.append(label)
    for item in raw.get("location_requirements") or []:
        if not isinstance(item, dict) or item.get("exclude"):
            continue
        parts = [clean_text(str(item.get("city") or "")), clean_text(str(item.get("country") or ""))]
        label = ", ".join(part for part in parts if part)
        if label:
            labels.append(label)
    return ", ".join(dict.fromkeys(labels))


def _remote(raw: dict[str, Any]) -> bool | None:
    if raw.get("remote_options") is not None:
        return True
    for item in raw.get("location_items") or []:
        if isinstance(item, dict) and not item.get("exclude") and str(item.get("format") or "").lower() == "remote":
            return True
    return None


def _string_items(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [clean_text(str(item)) for item in items if clean_text(str(item))]


def _skill_names(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    names: list[str] = []
    for item in items:
        if isinstance(item, dict):
            name = clean_text(str(item.get("name") or item.get("title") or ""))
            if name:
                names.append(name)
    return names


def _int_or_none(value: Any) -> int | None:
    if value in (None, "", 0):
        return None
    try:
        return int(float(str(value).replace(" ", "")))
    except ValueError:
        return None
