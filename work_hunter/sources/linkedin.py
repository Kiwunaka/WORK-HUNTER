from __future__ import annotations

import html as html_lib
import re
import urllib.parse
from typing import Any, Callable

from ..models import Job
from .common import canonicalize_job_url, clean_text, fetch_url


SEARCH_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/"
    "seeMoreJobPostings/search"
)


class LinkedInSource:
    """Collect public LinkedIn job cards without account cookies."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        fetcher: Callable[[str], str] | None = None,
    ) -> None:
        self.config = config
        self.fetcher = fetch_url if fetcher is None else fetcher

    def collect(
        self,
        profile: dict[str, Any],
        limit: int | None = None,
    ) -> list[Job]:
        queries = _profile_queries(profile)
        locations = _locations(self.config, profile)
        pages = max(1, int(self.config.get("pages", 1) or 1))
        page_size = max(1, int(self.config.get("page_size", 25) or 25))
        jobs: list[Job] = []
        seen: set[str] = set()
        for query in queries:
            for location in locations:
                for page in range(pages):
                    url = linkedin_search_url(
                        query=query,
                        location=location,
                        start=page * page_size,
                        config=self.config,
                    )
                    page_jobs = parse_linkedin_search_html(self.fetcher(url))
                    if _is_remote_location(location):
                        for job in page_jobs:
                            job.remote = True
                    if not page_jobs:
                        break
                    for job in page_jobs:
                        if job.source_id in seen:
                            continue
                        seen.add(job.source_id)
                        jobs.append(job)
                        if limit is not None and len(jobs) >= limit:
                            return jobs
        return jobs


def linkedin_search_url(
    *,
    query: str,
    location: str,
    start: int,
    config: dict[str, Any],
) -> str:
    params: dict[str, str] = {
        "keywords": query,
        "location": location,
        "start": str(max(0, start)),
    }
    geo_id = str(config.get("geo_id") or "").strip()
    if geo_id:
        params["geoId"] = geo_id
    seconds = int(config.get("date_posted_seconds", 0) or 0)
    if seconds > 0:
        params["f_TPR"] = f"r{seconds}"
    if bool(config.get("remote_only", False)) or _is_remote_location(location):
        params["f_WT"] = "2"
    return f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"


def parse_linkedin_search_html(payload: str) -> list[Job]:
    """Parse LinkedIn guest-search cards into the common job model."""

    jobs: list[Job] = []
    for block in _job_card_blocks(payload):
        source_id = _first_match(
            block,
            (
                r'data-entity-urn=["\']urn:li:jobPosting:(\d+)',
                r'/jobs/view/[^?"\']*?-(\d+)(?:\?|["\'])',
                r'/jobs/view/(\d+)(?:\?|["\'])',
            ),
        )
        href = _first_match(
            block,
            (
                r'<a[^>]+class=["\'][^"\']*base-card__full-link[^"\']*["\'][^>]+href=["\']([^"\']+)',
                r'<a[^>]+href=["\']([^"\']+/jobs/view/[^"\']+)["\']',
            ),
        )
        title = _class_text(block, "base-search-card__title")
        if not title:
            title = _class_text(block, "sr-only")
        if not source_id or not href or not title:
            continue
        company = _class_text(block, "base-search-card__subtitle")
        location = _class_text(block, "job-search-card__location")
        published_at = _first_match(
            block,
            (r'<time[^>]+datetime=["\']([^"\']+)',),
        )
        parsed_href = urllib.parse.urlsplit(html_lib.unescape(href))
        stable_url = urllib.parse.urlunsplit(
            (parsed_href.scheme, parsed_href.netloc, parsed_href.path, "", "")
        )
        jobs.append(
            Job(
                source="linkedin",
                source_id=source_id,
                url=canonicalize_job_url(stable_url),
                title=title,
                company=company,
                location=location,
                remote="remote" in f"{title} {location}".casefold(),
                description=clean_text(block),
                published_at=published_at,
            )
        )
    return _dedupe(jobs)


def _job_card_blocks(payload: str) -> list[str]:
    starts = [
        match.start()
        for match in re.finditer(
            r'<(?:div|a)\b[^>]*class=["\'][^"\']*\b(?:base-card|job-search-card)\b',
            payload or "",
            flags=re.IGNORECASE,
        )
    ]
    blocks: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(payload)
        block = payload[start:end]
        if "jobPosting:" in block or "/jobs/view/" in block:
            blocks.append(block)
    return blocks


def _class_text(block: str, class_name: str) -> str:
    match = re.search(
        rf'<[^>]+class=["\'][^"\']*\b{re.escape(class_name)}\b[^"\']*["\'][^>]*>(.*?)</[^>]+>',
        block,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return clean_text(match.group(1)) if match else ""


def _first_match(block: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        match = re.search(pattern, block, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return clean_text(html_lib.unescape(match.group(1)))
    return ""


def _profile_queries(profile: dict[str, Any]) -> list[str]:
    raw = profile.get("queries") or profile.get("desired_roles") or [""]
    if isinstance(raw, str):
        raw = [raw]
    queries = [str(item).strip() for item in raw if str(item).strip()]
    return queries or [""]


def _locations(config: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    raw = config.get("locations") or profile.get("locations") or [""]
    if isinstance(raw, str):
        raw = [raw]
    locations = [str(item).strip() for item in raw if str(item).strip()]
    return locations or [""]


def _is_remote_location(value: str) -> bool:
    lowered = value.casefold().strip()
    return lowered in {"remote", "удаленно", "удалённо", "удаленка", "удалёнка"}


def _dedupe(jobs: list[Job]) -> list[Job]:
    unique: list[Job] = []
    seen: set[str] = set()
    for job in jobs:
        if job.source_id in seen:
            continue
        seen.add(job.source_id)
        unique.append(job)
    return unique
