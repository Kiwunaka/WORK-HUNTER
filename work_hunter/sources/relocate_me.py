from __future__ import annotations

import re
import urllib.parse
from dataclasses import replace
from typing import Any, Callable

from ..models import Job
from .common import absolute_url, clean_text, fetch_url, last_path_part


BASE_URL = "https://relocate.me"


class RelocateMeSource:
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
        fetch_details = bool(self.config.get("fetch_details", True))
        jobs: list[Job] = []
        seen: set[str] = set()
        for page in range(1, pages + 1):
            page_jobs = parse_relocate_list_html(self.fetcher(_list_url(page)))
            for job in page_jobs:
                if job.source_id in seen:
                    continue
                seen.add(job.source_id)
                if fetch_details:
                    try:
                        job = parse_relocate_detail_html(self.fetcher(job.url), base_job=job)
                    except Exception:
                        pass
                jobs.append(job)
                if limit is not None and len(jobs) >= limit:
                    return jobs
        return jobs


def parse_relocate_list_html(html: str) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    for match in re.finditer(
        r'<a\b[^>]+href=["\'](?P<href>/(?:international-jobs|remote)/[^"\']+)["\'][^>]*>(?P<title>.*?)</a>',
        html or "",
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = match.group("href")
        if "job-search-guide" in href or href.rstrip("/") in {"/international-jobs", "/remote"}:
            continue
        title = clean_text(match.group("title"))
        if not title or title.lower() in {"jobs", "all jobs"} or _is_promo_card(href, title):
            continue
        url = absolute_url(BASE_URL, href)
        source_id = last_path_part(url)
        if source_id in seen:
            continue
        seen.add(source_id)
        block = _surrounding_block(html, match.start(), match.end())
        description = clean_text(block)
        jobs.append(
            Job(
                source="relocate_me",
                source_id=source_id,
                url=url,
                title=title,
                company=_extract_first(block, (r'class=["\'][^"\']*job__company[^"\']*["\'][^>]*>(.*?)</',)),
                location=_extract_first(block, (r'class=["\'][^"\']*job__location[^"\']*["\'][^>]*>(.*?)</',)),
                remote=_remote(f"{title} {description}"),
                description=description or title,
            )
        )
    return jobs


def parse_relocate_detail_html(html: str, *, base_job: Job) -> Job:
    company = _extract_first(
        html,
        (
            r'class=["\'][^"\']*job-info__company[^"\']*["\'][^>]*>.*?<a[^>]*>(.*?)</a>',
            r'class=["\'][^"\']*company[^"\']*["\'][^>]*>(.*?)</',
        ),
    )
    location = _extract_first(
        html,
        (
            r'class=["\'][^"\']*job-info__country[^"\']*["\'][^>]*>.*?<p[^>]*>(.*?)</p>',
            r'class=["\'][^"\']*location[^"\']*["\'][^>]*>(.*?)</',
        ),
    )
    description = _extract_first(
        html,
        (
            r'class=["\'][^"\']*job-info__description[^"\']*["\'][^>]*>(.*?)</div>',
            r'<article[^>]*>(.*?)</article>',
        ),
    )
    title = _extract_first(html, (r"<h1[^>]*>(.*?)</h1>",)) or base_job.title
    return replace(
        base_job,
        title=title,
        company=company or base_job.company,
        location=location or base_job.location,
        remote=_remote(f"{title} {location} {description}") or base_job.remote,
        description=description or base_job.description,
    )


def _list_url(page: int) -> str:
    params = urllib.parse.urlencode({"page": str(page)})
    return f"{BASE_URL}/international-jobs?{params}"


def _extract_first(html: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        match = re.search(pattern, html or "", flags=re.IGNORECASE | re.DOTALL)
        if match:
            return clean_text(match.group(1))
    return ""


def _remote(text: str) -> bool | None:
    lowered = (text or "").lower()
    if any(word in lowered for word in ("remote", "work from home", "anywhere")):
        return True
    return None


def _is_promo_card(href: str, title: str) -> bool:
    lowered = f"{href} {title}".lower()
    return "paid option" in lowered or "curated visa sponsorship" in lowered


def _surrounding_block(html: str, start: int, end: int) -> str:
    open_match = None
    for tag in ("article", "li", "section", "div"):
        matches = list(re.finditer(rf"<{tag}\b[^>]*>", html[:start], flags=re.IGNORECASE | re.DOTALL))
        if matches:
            candidate = matches[-1]
            if open_match is None or candidate.start() > open_match.start():
                open_match = candidate
    if open_match is None:
        return html[max(0, start - 400): min(len(html), end + 400)]
    tag_match = re.match(r"<([a-z0-9]+)", open_match.group(0), flags=re.IGNORECASE)
    tag = tag_match.group(1) if tag_match else "div"
    close_match = re.search(rf"</{tag}>", html[end:], flags=re.IGNORECASE)
    if close_match:
        return html[open_match.start(): end + close_match.end()]
    return html[open_match.start(): min(len(html), end + 800)]
