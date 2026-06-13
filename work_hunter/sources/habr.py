from __future__ import annotations

import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any

from ..models import Job
from .common import absolute_url, clean_text, fetch_url, last_path_part


BASE_URL = "https://career.habr.com"
API_URL = f"{BASE_URL}/api/frontend/vacancies"
CURRENCY_MAP = {
    "rur": "RUB",
    "rub": "RUB",
    "usd": "USD",
    "eur": "EUR",
    "kzt": "KZT",
    "uah": "UAH",
    "gbp": "GBP",
}


class HabrSource:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def collect(self, profile: dict[str, Any], limit: int | None = None) -> list[Job]:
        queries = profile.get("queries") or profile.get("must_have_skills") or [""]
        jobs: list[Job] = []
        seen: set[str] = set()
        for query in queries:
            try:
                api_jobs = parse_habr_api_response(self._fetch_api_page(query))
            except Exception:
                api_jobs = []
            for job in api_jobs:
                if job.source_id not in seen:
                    jobs.append(job)
                    seen.add(job.source_id)
                if limit is not None and len(jobs) >= limit:
                    return jobs[:limit]
            if not api_jobs and self.config.get("fallback_web", True):
                html = self._fetch_search_page(query)
                rss_url = extract_rss_url(html)
                fallback_jobs = parse_habr_rss(fetch_url(absolute_url(BASE_URL, rss_url))) if rss_url else []
                if not fallback_jobs:
                    fallback_jobs = parse_habr_html(html)
                for job in fallback_jobs:
                    if job.source_id not in seen:
                        jobs.append(job)
                        seen.add(job.source_id)
                    if limit is not None and len(jobs) >= limit:
                        return jobs[:limit]
        return jobs

    def _fetch_api_page(self, query: str) -> dict[str, Any]:
        per_page = max(1, int(self.config.get("per_page", 25) or 25))
        params = {
            "page": 1,
            "per_page": per_page,
            "type": "all",
            "sort": self.config.get("sort", "date"),
        }
        if query:
            params["q"] = query
        url = f"{API_URL}?{urllib.parse.urlencode(params)}"
        return json.loads(fetch_url(url, headers={"Accept": "application/json"}))

    def _fetch_search_page(self, query: str) -> str:
        params = {
            "type": "all",
            "q": query,
        }
        url = f"{BASE_URL}/vacancies?{urllib.parse.urlencode(params)}"
        return fetch_url(url)


def extract_rss_url(html: str) -> str | None:
    match = re.search(
        r'<link[^>]+type=["\']application/rss\+xml["\'][^>]+href=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None


def parse_habr_rss(xml_text: str) -> list[Job]:
    root = ET.fromstring(xml_text)
    jobs: list[Job] = []
    for item in root.findall(".//item"):
        raw_title = clean_text(item.findtext("title") or "")
        link = clean_text(item.findtext("link") or "")
        description = clean_text(item.findtext("description") or "")
        published_at = clean_text(item.findtext("pubDate") or "")
        if not raw_title or not link:
            continue
        jobs.append(
            Job(
                source="habr",
                source_id=last_path_part(link),
                url=link,
                title=_title_from_rss(raw_title),
                company=_company_from_description(description),
                location=_location_from_description(description),
                remote="можно удалённо" in description.lower() or "можно удаленно" in description.lower(),
                description=description,
                published_at=published_at,
            )
        )
    return jobs


def parse_habr_api_response(payload: dict[str, Any]) -> list[Job]:
    jobs: list[Job] = []
    for raw in payload.get("list") or []:
        title = clean_text(str(raw.get("title") or ""))
        href = str(raw.get("href") or "")
        source_id = str(raw.get("id") or last_path_part(href))
        if not title or not source_id:
            continue
        salary_from, salary_to, currency, salary_text = _salary_from_habr(raw.get("salary"))
        jobs.append(
            Job(
                source="habr",
                source_id=source_id,
                url=absolute_url(BASE_URL, href),
                title=title,
                company=clean_text(str((raw.get("company") or {}).get("title") or "")),
                salary_text=salary_text,
                salary_from=salary_from,
                salary_to=salary_to,
                currency=currency,
                location=", ".join(_titles(raw.get("locations"))),
                remote=bool(raw.get("remoteWork")),
                description=_habr_description(raw),
                published_at=clean_text(str((raw.get("publishedDate") or {}).get("date") or "")),
            )
        )
    return jobs


def parse_habr_html(html: str) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    for match in re.finditer(
        r'<a[^>]+href=["\'](/vacancies/\d+)["\'][^>]*>(.*?)</a>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href, raw_title = match.groups()
        title = clean_text(raw_title)
        if not title:
            continue
        url = absolute_url(BASE_URL, href)
        source_id = last_path_part(url)
        if source_id in seen:
            continue
        seen.add(source_id)
        jobs.append(
            Job(
                source="habr",
                source_id=source_id,
                url=url,
                title=title,
                description=title,
            )
        )
    return jobs


def _company_from_description(description: str) -> str:
    if not description:
        return ""
    quoted = re.search(r"Компания\s+[«\"]([^»\"]+)[»\"]", description)
    if quoted:
        return clean_text(quoted.group(1))
    parts = re.split(r"\s+[—-]\s+", description, maxsplit=1)
    return clean_text(parts[0])[:120]


def _title_from_rss(title: str) -> str:
    quoted = re.search(r"Требуется\s+[«\"]([^»\"]+)[»\"]", title)
    if quoted:
        return clean_text(quoted.group(1))
    return title


def _location_from_description(description: str) -> str:
    match = re.search(r"\.\s+([^\.]+)\.\s+(?:Полный|Неполный|Удаленная|Можно)", description)
    if match:
        location = clean_text(match.group(1))
        if "рабочий день" not in location.lower() and "удал" not in location.lower():
            return location
    return ""


def _salary_from_habr(value: Any) -> tuple[int | None, int | None, str, str]:
    if not isinstance(value, dict):
        return None, None, "", ""
    salary_from = _int_or_none(value.get("from"))
    salary_to = _int_or_none(value.get("to"))
    raw_currency = clean_text(str(value.get("currency") or "")).lower()
    currency = CURRENCY_MAP.get(raw_currency, raw_currency.upper())
    formatted = clean_text(str(value.get("formatted") or ""))
    if formatted:
        return salary_from, salary_to, currency, formatted
    if salary_from and salary_to:
        salary_text = f"{salary_from}-{salary_to} {currency}".strip()
    elif salary_from:
        salary_text = f"from {salary_from} {currency}".strip()
    elif salary_to:
        salary_text = f"up to {salary_to} {currency}".strip()
    else:
        salary_text = ""
    return salary_from, salary_to, currency, salary_text


def _habr_description(raw: dict[str, Any]) -> str:
    parts: list[str] = []
    divisions = ", ".join(_titles(raw.get("divisions")))
    skills = ", ".join(_titles(raw.get("skills")))
    employment = clean_text(str(raw.get("employment") or ""))
    qualification = clean_text(
        str(
            (raw.get("salaryQualification") or raw.get("qualification") or {}).get("title")
            if isinstance(raw.get("salaryQualification") or raw.get("qualification"), dict)
            else raw.get("salaryQualification") or raw.get("qualification") or ""
        )
    )
    if divisions:
        parts.append(f"Role: {divisions}")
    if skills:
        parts.append(f"Skills: {skills}")
    if employment:
        parts.append(f"Employment: {employment}")
    if qualification:
        parts.append(f"Level: {qualification}")
    return "\n".join(parts)


def _titles(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    titles: list[str] = []
    for item in items:
        if isinstance(item, dict):
            title = clean_text(str(item.get("title") or item.get("name") or ""))
        else:
            title = clean_text(str(item or ""))
        if title:
            titles.append(title)
    return titles


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(" ", "")))
    except ValueError:
        return None
