from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ..models import Job
from .common import absolute_url, canonicalize_job_url, clean_text, fetch_url


@dataclass(frozen=True)
class PublicBoardSpec:
    name: str
    base_url: str
    paths: tuple[str, ...] = ("/",)
    query_params: tuple[str, ...] = ("q",)
    page_param: str = "page"


PUBLIC_BOARD_SPECS: dict[str, PublicBoardSpec] = {
    "hirehi": PublicBoardSpec(
        name="hirehi",
        base_url="https://hirehi.ru",
        paths=(
            "/vacancies/python",
            "/vacancies/backend",
            "/vacancies/development",
            "/vacancies/fullstack",
            "/vacancies",
        ),
        query_params=("q",),
    ),
    "relocate_me": PublicBoardSpec(
        name="relocate_me",
        base_url="https://relocate.me",
        paths=(
            "/international-jobs",
            "/international-jobs/software-developer",
            "/international-jobs/backend",
            "/international-jobs/python",
            "/remote-jobs",
        ),
        query_params=("query",),
    ),
    "rvc": PublicBoardSpec(
        name="rvc",
        base_url="https://app.rvc.global",
        paths=("/", "/vacancy", "/vacancy/view"),
        query_params=("q",),
    ),
    "getmatch": PublicBoardSpec(
        name="getmatch",
        base_url="https://getmatch.ru",
        paths=("/vacancies", "/jobs"),
        query_params=("q",),
    ),
    "careerspace": PublicBoardSpec(
        name="careerspace",
        base_url="https://careerspace.app",
        paths=("/", "/jobs", "/vacancies"),
        query_params=("q",),
    ),
    "another_it": PublicBoardSpec(
        name="another_it",
        base_url="https://another-it.ru",
        paths=("/", "/jobs", "/vacancies"),
        query_params=("q",),
    ),
    "jabka": PublicBoardSpec(
        name="jabka",
        base_url="https://jabka.work",
        paths=("/", "/jobs"),
        query_params=("q",),
    ),
    "indeed": PublicBoardSpec(
        name="indeed",
        base_url="https://www.indeed.com",
        paths=("/jobs?fromage=7", "/jobs?l=Remote&fromage=7"),
        query_params=("q",),
        page_param="start",
    ),
}

PUBLIC_BOARD_SOURCE_NAMES = tuple(PUBLIC_BOARD_SPECS)
PAYLOAD_SOURCE_ID_KEYS = (
    "vacancy_id",
    "vacancyId",
    "job_id",
    "jobId",
    "offer_id",
    "offerId",
    "external_id",
    "externalId",
    "source_id",
    "sourceId",
    "id",
    "identifier",
    "uuid",
    "@id",
)


class PublicJobBoardSource:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        source_name: str,
        spec: PublicBoardSpec | None = None,
        fetcher: Callable[[str], str] | None = None,
        root: str | Path | None = None,
    ):
        self.config = config
        self.source_name = source_name
        self.spec = spec or PUBLIC_BOARD_SPECS[source_name]
        self.fetcher = fetch_url if fetcher is None else fetcher
        self.root = Path(root) if root is not None else Path.cwd()

    def collect(self, profile: dict[str, Any], limit: int | None = None) -> list[Job]:
        jobs: list[Job] = []
        seen: set[str] = set()
        for url in self._candidate_urls(profile):
            html = self._fetch(url)
            for job in parse_public_board_html(html, source=self.source_name, base_url=self.base_url):
                key = (
                    canonicalize_job_url(job.url)
                    if job.url
                    else f"{job.source}:{job.source_id}"
                )
                if key in seen:
                    continue
                seen.add(key)
                jobs.append(job)
                if limit is not None and len(jobs) >= limit:
                    return jobs
        return jobs

    def _fetch(self, url: str) -> str:
        try:
            return self.fetcher(url)
        except Exception:
            if not bool(self.config.get("browser_fallback", False)):
                raise
            return self._fetch_with_browser(url)

    def _fetch_with_browser(self, url: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                'Browser search requires Playwright: pip install -e ".[browser]"'
            ) from exc
        profile_dir = self.root / ".work-hunter" / "browser" / self.source_name
        profile_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(profile_dir),
                headless=bool(self.config.get("headless", False)),
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                timeout_ms = max(
                    5_000,
                    int(self.config.get("timeout_seconds", 45) or 45) * 1000,
                )
                page.set_default_timeout(timeout_ms)
                page.goto(url, wait_until="domcontentloaded")
                wait_seconds = max(
                    0,
                    int(self.config.get("login_wait_seconds", 45) or 0),
                )
                for _ in range(wait_seconds):
                    if not _browser_challenge_visible(page):
                        break
                    if bool(self.config.get("headless", False)):
                        break
                    page.wait_for_timeout(1000)
                return page.content()
            finally:
                context.close()

    @property
    def base_url(self) -> str:
        return str(self.config.get("base_url") or self.spec.base_url).rstrip("/")

    def _candidate_urls(self, profile: dict[str, Any]) -> list[str]:
        paths = tuple(self.config["paths"] if "paths" in self.config else self.spec.paths)
        query_params = tuple(
            self.config["query_params"] if "query_params" in self.config else self.spec.query_params
        )
        page_param = str(self.config.get("page_param") or self.spec.page_param or "")
        pages = max(1, int(self.config.get("pages", 1) or 1))
        queries = _profile_queries(profile)
        urls: list[str] = []
        seen: set[str] = set()
        for path in paths:
            for page in range(1, pages + 1):
                page_path = str(path)
                if "{page}" in page_path:
                    page_path = page_path.format(page=page)
                if query_params:
                    for query in queries:
                        for query_param in query_params:
                            params = {query_param: query}
                            if page > 1 and page_param and "{page}" not in str(path):
                                params[page_param] = str(page)
                            self._append_url(urls, seen, page_path, params)
                else:
                    params = {}
                    if page > 1 and page_param and "{page}" not in str(path):
                        params[page_param] = str(page)
                    self._append_url(urls, seen, page_path, params)
        return urls

    def _append_url(
        self,
        urls: list[str],
        seen: set[str],
        path: str,
        params: dict[str, str],
    ) -> None:
        url = absolute_url(f"{self.base_url}/", path)
        if params:
            separator = "&" if urllib.parse.urlsplit(url).query else "?"
            url = f"{url}{separator}{urllib.parse.urlencode(params)}"
        if url not in seen:
            seen.add(url)
            urls.append(url)


def public_board_default_config(name: str) -> dict[str, Any]:
    spec = PUBLIC_BOARD_SPECS[name]
    return {
        "enabled": True,
        "base_url": spec.base_url,
        "paths": list(spec.paths),
        "query_params": list(spec.query_params),
        "page_param": spec.page_param,
        "pages": 1,
    }


def _browser_challenge_visible(page: Any) -> bool:
    title = str(page.title() or "").casefold()
    url = str(page.url or "").casefold()
    return any(
        marker in f"{title} {url}"
        for marker in (
            "additional verification",
            "just a moment",
            "captcha",
            "challenge",
        )
    )


def parse_public_board_html(html: str, *, source: str, base_url: str) -> list[Job]:
    jobs: list[Job] = []
    jobs.extend(_parse_json_ld_jobs(html, source=source, base_url=base_url))
    jobs.extend(_parse_next_data_jobs(html, source=source, base_url=base_url))
    jobs.extend(_parse_anchor_jobs(html, source=source, base_url=base_url))
    return _dedupe_jobs(jobs)


def extract_jobs_from_json_like(data: Any, *, source: str, base_url: str) -> list[Job]:
    jobs: list[Job] = []
    for item in _walk_dicts(data):
        job = _job_from_json_candidate(item, source=source, base_url=base_url)
        if job is not None:
            jobs.append(job)
    return _dedupe_jobs(jobs)


def _profile_queries(profile: dict[str, Any]) -> list[str]:
    raw = profile.get("queries") or profile.get("must_have_skills") or [""]
    if isinstance(raw, str):
        raw = [raw]
    queries = [str(item).strip() for item in raw if str(item).strip()]
    return queries or [""]


def _parse_json_ld_jobs(html: str, *, source: str, base_url: str) -> list[Job]:
    jobs: list[Job] = []
    for raw in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html or "",
        flags=re.IGNORECASE | re.DOTALL,
    ):
        data = _loads_json(raw)
        if data is None:
            continue
        for item in _iter_job_postings(data):
            job = _job_from_job_posting(item, source=source, base_url=base_url)
            if job is not None:
                jobs.append(job)
    return jobs


def _parse_next_data_jobs(html: str, *, source: str, base_url: str) -> list[Job]:
    jobs: list[Job] = []
    for raw in re.findall(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html or "",
        flags=re.IGNORECASE | re.DOTALL,
    ):
        data = _loads_json(raw)
        if data is not None:
            jobs.extend(extract_jobs_from_json_like(data, source=source, base_url=base_url))
    return jobs


def _parse_anchor_jobs(html: str, *, source: str, base_url: str) -> list[Job]:
    jobs: list[Job] = []
    for match in re.finditer(
        r'<a\b(?P<attrs>[^>]*\bhref=["\'](?P<href>[^"\']+)["\'][^>]*)>(?P<title>.*?)</a>',
        html or "",
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = html_lib.unescape(match.group("href")).strip()
        if not _looks_like_job_href(href):
            continue
        title = clean_text(match.group("title"))
        if not _looks_like_title(title):
            continue
        url = absolute_url(base_url, href)
        block = _surrounding_block(html, match.start(), match.end())
        description = clean_text(block) or title
        jobs.append(
            Job(
                source=source,
                source_id=_source_id_from_url_or_payload(source, url, {}),
                url=url,
                title=title,
                company=_company_from_title(title),
                salary_text=_extract_salary_text(description),
                location=_extract_location_text(description),
                remote=_is_remote(f"{title} {description}"),
                description=description,
            )
        )
    return jobs


def _loads_json(raw: str) -> Any:
    text = html_lib.unescape((raw or "").strip())
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _iter_job_postings(data: Any) -> Iterable[dict[str, Any]]:
    if isinstance(data, list):
        for item in data:
            yield from _iter_job_postings(item)
        return
    if not isinstance(data, dict):
        return
    item_type = data.get("@type")
    if isinstance(item_type, str):
        types = {item_type.lower()}
    elif isinstance(item_type, list):
        types = {str(item).lower() for item in item_type}
    else:
        types = set()
    if "jobposting" in types:
        yield data
    for value in data.values():
        if isinstance(value, (dict, list)):
            yield from _iter_job_postings(value)


def _walk_dicts(data: Any) -> Iterable[dict[str, Any]]:
    if isinstance(data, dict):
        yield data
        for value in data.values():
            yield from _walk_dicts(value)
    elif isinstance(data, list):
        for item in data:
            yield from _walk_dicts(item)


def _job_from_job_posting(payload: dict[str, Any], *, source: str, base_url: str) -> Job | None:
    title = _first_text(payload, ("title", "name"))
    url = _payload_url(payload, base_url)
    if not title:
        return None
    description = clean_text(str(payload.get("description") or title))
    salary_from, salary_to, currency, salary_text = _salary_from_payload(payload.get("baseSalary"))
    location = _location_from_payload(payload.get("jobLocation"))
    remote = _remote_from_payload(payload, f"{title} {description} {location}")
    return Job(
        source=source,
        source_id=_source_id_from_url_or_payload(source, url, payload),
        url=url,
        title=title,
        company=_entity_name(payload.get("hiringOrganization") or payload.get("organization")),
        salary_text=salary_text,
        salary_from=salary_from,
        salary_to=salary_to,
        currency=currency,
        location=location,
        remote=remote,
        description=description,
        published_at=_first_text(payload, ("datePosted", "validThrough")),
    )


def _job_from_json_candidate(payload: dict[str, Any], *, source: str, base_url: str) -> Job | None:
    title = _first_text(payload, ("title", "name", "vacancyTitle", "position", "role"))
    if not title or not _looks_like_title(title):
        return None
    url = _payload_url(payload, base_url)
    has_jobish_url = bool(url) and _looks_like_job_href(urllib.parse.urlsplit(url).path)
    has_context = any(
        key in payload
        for key in (
            "company",
            "companyName",
            "employer",
            "employerName",
            "hiringOrganization",
            "salary",
            "salaryText",
            "location",
            "city",
            "description",
            "remote",
            "isRemote",
        )
    )
    if not (has_jobish_url or has_context):
        return None
    company = _entity_name(
        payload.get("company")
        or payload.get("employer")
        or payload.get("hiringOrganization")
        or payload.get("organization")
        or payload.get("companyName")
        or payload.get("employerName")
    )
    salary_from, salary_to, currency, salary_text = _salary_from_payload(
        payload.get("baseSalary")
        or payload.get("salary")
        or payload.get("compensation")
        or payload.get("salaryText")
        or payload.get("salary_text")
    )
    description = _first_text(payload, ("description", "summary", "shortDescription", "text", "body"))
    location = _location_from_payload(payload.get("location") or payload.get("jobLocation") or payload.get("city"))
    remote = _remote_from_payload(payload, f"{title} {description} {location}")
    return Job(
        source=source,
        source_id=_source_id_from_url_or_payload(source, url, payload),
        url=url,
        title=title,
        company=company,
        salary_text=salary_text,
        salary_from=salary_from,
        salary_to=salary_to,
        currency=currency,
        location=location,
        remote=remote,
        description=description or title,
        published_at=_first_text(payload, ("published_at", "publishedAt", "datePosted", "createdAt")),
    )


def _payload_url(payload: dict[str, Any], base_url: str) -> str:
    raw = (
        payload.get("url")
        or payload.get("href")
        or payload.get("path")
        or payload.get("link")
        or payload.get("alternate_url")
        or payload.get("alternateUrl")
    )
    if isinstance(raw, dict):
        raw = raw.get("url") or raw.get("@id") or raw.get("href")
    if raw:
        return absolute_url(base_url, str(raw))
    slug = payload.get("slug")
    if slug:
        return absolute_url(base_url, str(slug))
    return ""


def _payload_source_id_text(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    return clean_text(str(value))


def _source_id_from_url_or_payload(source: str, url: str, payload: dict[str, Any]) -> str:
    for key in PAYLOAD_SOURCE_ID_KEYS:
        raw = payload.get(key)
        if isinstance(raw, dict):
            for nested_key in ("value", "id", "@id"):
                text = _payload_source_id_text(raw.get(nested_key))
                if text:
                    return text
            continue
        text = _payload_source_id_text(raw)
        if text:
            return text
    if url:
        canonical_url = canonicalize_job_url(url)
        digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:24]
        return f"{source}-{digest}"
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    return f"{source}-{digest}"


def _first_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        text = _text_value(value)
        if text:
            return text
    return ""


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return _first_text(value, ("name", "title", "value", "label", "text"))
    if isinstance(value, list):
        values = [_text_value(item) for item in value]
        return clean_text(", ".join(item for item in values if item))
    return clean_text(str(value))


def _entity_name(value: Any) -> str:
    return _text_value(value)


def _location_from_payload(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, list):
        return clean_text(", ".join(_location_from_payload(item) for item in value if _location_from_payload(item)))
    if isinstance(value, dict):
        address = value.get("address")
        parts = [
            _text_value(value.get("addressLocality")),
            _text_value(value.get("addressRegion")),
            _text_value(value.get("addressCountry")),
        ]
        if isinstance(address, dict):
            parts.extend(
                [
                    _text_value(address.get("addressLocality")),
                    _text_value(address.get("addressRegion")),
                    _text_value(address.get("addressCountry")),
                ]
            )
        direct = _first_text(value, ("name", "city", "location", "label"))
        if direct:
            parts.insert(0, direct)
        return clean_text(", ".join(part for part in parts if part))
    return clean_text(str(value))


def _salary_from_payload(value: Any) -> tuple[int | None, int | None, str, str]:
    if value is None:
        return None, None, "", ""
    if isinstance(value, str):
        return None, None, "", clean_text(value)
    if not isinstance(value, dict):
        return None, None, "", clean_text(str(value))
    currency = _text_value(value.get("currency") or value.get("currencyCode"))
    salary_value = value.get("value") if "value" in value else value
    if isinstance(salary_value, dict):
        salary_from = _int_or_none(
            salary_value.get("minValue")
            or salary_value.get("min")
            or salary_value.get("from")
            or salary_value.get("minimum")
        )
        salary_to = _int_or_none(
            salary_value.get("maxValue")
            or salary_value.get("max")
            or salary_value.get("to")
            or salary_value.get("maximum")
        )
        exact = _int_or_none(salary_value.get("value") or salary_value.get("amount"))
    else:
        salary_from = None
        salary_to = None
        exact = _int_or_none(salary_value)
    if salary_from and salary_to:
        salary_text = f"{salary_from}-{salary_to} {currency}".strip()
    elif salary_from:
        salary_text = f"from {salary_from} {currency}".strip()
    elif salary_to:
        salary_text = f"up to {salary_to} {currency}".strip()
    elif exact:
        salary_text = f"{exact} {currency}".strip()
    else:
        salary_text = _text_value(value.get("text") or value.get("label") or value.get("display"))
    return salary_from, salary_to, currency, salary_text


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(" ", "")))
    except ValueError:
        return None


def _remote_from_payload(payload: dict[str, Any], text: str) -> bool | None:
    for key in ("remote", "isRemote", "is_remote", "allowRemote", "remoteAllowed"):
        if key in payload:
            return bool(payload.get(key))
    location_type = str(payload.get("jobLocationType") or "").lower()
    if "telecommute" in location_type or "remote" in location_type:
        return True
    return _is_remote(text)


def _is_remote(text: str) -> bool | None:
    lowered = (text or "").lower()
    if any(word in lowered for word in ("remote", "удален", "удалён", "удаленная", "удалённая", "remotely")):
        return True
    return None


def _looks_like_job_href(href: str) -> bool:
    lowered = (href or "").lower()
    if not lowered or lowered.startswith(("#", "mailto:", "tel:", "javascript:")):
        return False
    blocked = ("login", "signin", "signup", "register", "privacy", "terms", "blog", "company")
    if any(part in lowered for part in blocked):
        return False
    if _looks_like_category_href(lowered):
        return False
    return any(
        part in lowered
        for part in (
            "/vacancy",
            "/vacancies",
            "/job",
            "/jobs",
            "/position",
            "/positions",
            "/skills/",
            "/rc/clk",
            "/pagead/clk",
        )
    )


def _looks_like_category_href(href: str) -> bool:
    parsed = urllib.parse.urlsplit(href)
    path = parsed.path.strip("/").lower()
    parts = path.split("/")
    category_roots = {"vacancies", "jobs", "international-jobs"}
    category_slugs = {
        "analytics",
        "backend",
        "business-analyst",
        "catalog",
        "ci-cd",
        "cloud",
        "content-creative",
        "cpp",
        "crm-lifecycle",
        "data-analyst",
        "data-engineer",
        "design",
        "development",
        "devops",
        "dotnet",
        "frontend",
        "fullstack",
        "general-marketing",
        "go",
        "graphic-design",
        "iac",
        "illustration",
        "infrastructure",
        "java",
        "kotlin",
        "kubernetes",
        "manual-qa",
        "management",
        "marketing",
        "ml-ai",
        "mobile",
        "nodejs",
        "observability",
        "performance-marketing",
        "php",
        "product-analyst",
        "product-design",
        "product-manager",
        "project-manager",
        "python",
        "qa",
        "qa-automation",
        "rust",
        "security",
        "seo-aso-orm",
        "smm-community",
        "software-developer",
        "sre-platform",
        "system-analyst",
        "ux-ui",
        "web-design",
    }
    return len(parts) == 2 and parts[0] in category_roots and parts[1] in category_slugs


def _looks_like_title(title: str) -> bool:
    text = clean_text(title)
    if len(text) < 3 or len(text) > 180:
        return False
    lowered = text.lower()
    blocked = {
        "all jobs",
        "check it out",
        "jobs",
        "job",
        "looking to post a job?",
        "post your jobs",
        "sign in",
        "vacancies",
        "vacancy",
        "вакансии",
        "все вакансии",
        "добавить в telegram",
        "работа",
        "разместить вакансию",
        "login",
    }
    return lowered not in blocked


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


def _company_from_title(title: str) -> str:
    match = re.search(r"\s@\s(.+)$", title)
    if match:
        return clean_text(match.group(1))
    return ""


def _extract_salary_text(text: str) -> str:
    match = re.search(
        r"(?i)(?:от\s*)?[\d\s]{2,}(?:[.,]\d+)?\s*(?:-|–|—|до)?\s*[\d\s]*(?:[.,]\d+)?\s*(?:₽|руб|rub|usd|\$|eur|€)",
        text or "",
    )
    return clean_text(match.group(0)) if match else ""


def _extract_location_text(text: str) -> str:
    match = re.search(r"(?i)\b(remote|relocate|moscow|москва|санкт-петербург|spb|belgrade|tbilisi|europe|европа)\b", text or "")
    return clean_text(match.group(0)) if match else ""


def _dedupe_jobs(jobs: list[Job]) -> list[Job]:
    unique: list[Job] = []
    seen: set[str] = set()
    for job in jobs:
        key = canonicalize_job_url(job.url) if job.url else f"{job.source}:{job.source_id}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(job)
    return unique
