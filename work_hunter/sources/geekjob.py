from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from ..models import Job
from .common import absolute_url, clean_text, fetch_url, last_path_part


BASE_URL = "https://geekjob.ru"

GEEKJOB_CHANNELS = [
    "geekjobs",
    "jobforjunior",
    "forchiefs",
    "jobfortm",
    "forallmarketing",
    "jobforpr",
    "forgamedev",
    "remotegeekjob",
    "forproducts",
    "foranalysts",
    "forallsales",
    "fordesigner",
    "forallmedia",
    "forhr",
    "forproducer",
    "forallqa",
    "fordevops",
    "forfrontend",
    "forallmobile",
    "forpython",
    "alljvmjobs",
    "forcpp",
    "forcsharp",
    "jobforphp",
    "forgoandrust",
    "forruby",
]


class GeekJobSource:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def collect(self, profile: dict[str, Any], limit: int | None = None) -> list[Job]:
        queries = profile.get("queries") or profile.get("must_have_skills") or [""]
        pages = max(1, int(self.config.get("pages", 1)))
        jobs: list[Job] = []
        seen: set[str] = set()
        for query in queries:
            for page in range(1, pages + 1):
                page_jobs = []
                if self.config.get("json", True):
                    try:
                        page_jobs = parse_geekjob_json(fetch_url(_json_search_url(query, page)))
                    except Exception:
                        page_jobs = []
                if not page_jobs and self.config.get("fallback_web", True):
                    page_jobs = parse_geekjob_html(fetch_url(_search_url(query, page)))
                for job in page_jobs:
                    if job.source_id not in seen:
                        jobs.append(job)
                        seen.add(job.source_id)
                    if limit is not None and len(jobs) >= limit:
                        return jobs
        if not jobs and self.config.get("fallback_unfiltered", True):
            for page in range(1, pages + 1):
                for job in parse_geekjob_html(fetch_url(_search_url("", page))):
                    if job.source_id not in seen:
                        jobs.append(job)
                        seen.add(job.source_id)
                    if limit is not None and len(jobs) >= limit:
                        return jobs
        return jobs

    def detail(self, source_id: str) -> Job:
        vacancy_id = urllib.parse.quote(str(source_id))
        url = f"{BASE_URL}/vacancy/{vacancy_id}"
        return parse_geekjob_detail_html(fetch_url(url), source_id=str(source_id), url=url)

    def apply_mechanism(self, source_id: str) -> dict[str, Any]:
        vacancy_id = urllib.parse.quote(str(source_id))
        url = f"{BASE_URL}/vacancy/{vacancy_id}"
        return parse_geekjob_apply_mechanism(fetch_url(url), source_id=str(source_id), url=url)


def _search_url(query: str, page: int) -> str:
    path = "/vacancies" if page == 1 else f"/vacancies/{page}"
    if query:
        return f"{BASE_URL}{path}?{urllib.parse.urlencode({'qs': query})}"
    return f"{BASE_URL}{path}"


def _json_search_url(query: str, page: int) -> str:
    params = {"format": "json", "page": str(page)}
    if query:
        params["qs"] = query
    return f"{BASE_URL}/vacancies?{urllib.parse.urlencode(params)}"


def parse_geekjob_json(payload: str | dict[str, Any]) -> list[Job]:
    data = json.loads(payload) if isinstance(payload, str) else payload
    jobs: list[Job] = []
    for raw in data.get("data") or []:
        link = clean_text(str(raw.get("link") or raw.get("url") or ""))
        title = clean_text(str(raw.get("position") or raw.get("vacancy") or raw.get("title") or ""))
        if not link or not title:
            continue
        salary = raw.get("salary")
        salary_text = clean_text(str((salary or {}).get("string") or "")) if isinstance(salary, dict) else clean_text(str(salary or ""))
        salary_from, salary_to, currency = _salary_details(salary)
        description_parts = [
            clean_text(str(raw.get("description") or "")),
            ", ".join(_string_items(raw.get("keywords"))),
            ", ".join(_string_items(raw.get("experience"))),
        ]
        description = clean_text(" ".join(part for part in description_parts if part))
        job_format = raw.get("jobFormat") if isinstance(raw.get("jobFormat"), dict) else {}
        jobs.append(
            Job(
                source="geekjob",
                source_id=last_path_part(link),
                url=absolute_url(BASE_URL, link),
                title=title,
                company=clean_text(str(raw.get("company") or "")),
                salary_text=salary_text,
                salary_from=salary_from,
                salary_to=salary_to,
                currency=currency,
                location=clean_text(str(raw.get("location") or "")),
                remote=bool(job_format.get("remote")) if "remote" in job_format else _remote_from_text(f"{title} {description}"),
                description=description or title,
                published_at=clean_text(str(raw.get("date") or "")),
            )
        )
    return jobs


def parse_geekjob_html(html: str) -> list[Job]:
    jobs: list[Job] = []
    for block in re.findall(
        r'<li class="collection-item avatar.*?</li>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        link_match = re.search(
            r'href=["\'](/vacancy/([a-zA-Z0-9]+))["\']',
            block,
            flags=re.IGNORECASE,
        )
        title_match = re.search(
            r'class=["\']title["\'][^>]*>(.*?)</a>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not link_match or not title_match:
            continue
        href, source_id = link_match.groups()
        company = _extract_company(block)
        salary_text = _extract_first(block, r'<span class="salary">(.*?)</span>')
        location = _extract_location(block)
        remote = "remote-label" in block.lower() or "remote" in block.lower()
        jobs.append(
            Job(
                source="geekjob",
                source_id=source_id,
                url=absolute_url(BASE_URL, href),
                title=clean_text(title_match.group(1)),
                company=company,
                salary_text=clean_text(salary_text),
                location=location,
                remote=remote,
                description=clean_text(block),
            )
        )
    return jobs


def parse_geekjob_detail_html(html: str, *, source_id: str, url: str) -> Job:
    title = _extract_html_first(
        html,
        (
            r"<h1\b[^>]*>(.*?)</h1>",
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            r"<title\b[^>]*>(.*?)</title>",
        ),
    )
    description = _extract_html_first(
        html,
        (r"<article\b[^>]*>(.*?)</article>", r"<section\b[^>]*>(.*?)</section>", r"<main\b[^>]*>(.*?)</main>"),
    ) or clean_text(html)
    company = _extract_html_first(
        html,
        (
            r'class=["\'][^"\']*(?:company-name|company_name|company)[^"\']*["\'][^>]*>(.*?)</',
            r'data-qa=["\'][^"\']*company[^"\']*["\'][^>]*>(.*?)</',
        ),
    )
    location = _extract_html_first(
        html,
        (
            r'class=["\'][^"\']*(?:location|address)[^"\']*["\'][^>]*>(.*?)</',
            r'data-qa=["\'][^"\']*(?:location|address)[^"\']*["\'][^>]*>(.*?)</',
        ),
    )
    return Job(
        source="geekjob",
        source_id=source_id,
        url=url,
        title=title or f"GeekJob vacancy {source_id}",
        company=company,
        location=location,
        remote=_remote_from_text(f"{title} {location} {description}"),
        description=description or title,
    )


def parse_geekjob_apply_mechanism(html: str, *, source_id: str, url: str) -> dict[str, Any]:
    forms = _extract_forms(html, url=url)
    apply_url = _extract_apply_url(html, fallback=url)
    if not forms:
        return {
            "status": "not_detected",
            "source_id": str(source_id),
            "mechanism": "external_page",
            "apply_url": apply_url,
            "form_signature": "unknown",
            "detector": "geekjob_apply_mechanism:v1",
            "form": {"form_url": apply_url, "fields": []},
            "cover_letter_required": False,
            "test_required": _text_mentions_test(html),
            "captcha": _text_mentions_captcha(html),
        }
    form = forms[0]
    fields = list(form.get("fields") or [])
    cover_letter_required = any(
        field.get("required") and _field_mentions_cover_letter(field)
        for field in fields
    )
    return {
        "status": "detected",
        "source_id": str(source_id),
        "mechanism": "form",
        "apply_url": apply_url,
        "form_signature": "geekjob_apply_form:v1",
        "detector": "geekjob_apply_mechanism:v1",
        "form": {
            "form_url": form["form_url"],
            "method": form["method"],
            "fields": fields,
            "form_signature": "geekjob_apply_form:v1",
        },
        "cover_letter_required": cover_letter_required,
        "test_required": _text_mentions_test(html),
        "captcha": _text_mentions_captcha(html),
    }


def _extract_first(block: str, pattern: str) -> str:
    match = re.search(pattern, block, flags=re.IGNORECASE | re.DOTALL)
    return clean_text(match.group(1)) if match else ""


def _extract_company(block: str) -> str:
    match = re.search(
        r'class=["\']truncate company-name["\'].*?<a[^>]*>(.*?)</a>',
        block,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return clean_text(match.group(1)) if match else ""


def _extract_location(block: str) -> str:
    match = re.search(
        r'<div class="info"><a[^>]*>\s*(.*?)<br',
        block,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return clean_text(match.group(1)) if match else ""


def _salary_details(value: Any) -> tuple[int | None, int | None, str]:
    if not isinstance(value, dict) or not isinstance(value.get("details"), dict):
        return None, None, ""
    details = value["details"]
    return (
        _int_or_none(details.get("min")),
        _int_or_none(details.get("max")),
        clean_text(str(details.get("cur") or "")),
    )


def _int_or_none(value: Any) -> int | None:
    if value in (None, "", 0):
        return None
    try:
        return int(float(str(value).replace(" ", "")))
    except ValueError:
        return None


def _string_items(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [clean_text(str(item)) for item in items if clean_text(str(item))]


def _extract_html_first(html: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        match = re.search(pattern, html or "", flags=re.IGNORECASE | re.DOTALL)
        if match:
            return clean_text(match.group(1))
    return ""


def _extract_forms(html: str, *, url: str) -> list[dict[str, Any]]:
    forms: list[dict[str, Any]] = []
    for match in re.finditer(r"<form\b(?P<attrs>[^>]*)>(?P<body>.*?)</form>", html or "", flags=re.IGNORECASE | re.DOTALL):
        attrs = _html_attrs(match.group("attrs"))
        body = match.group("body")
        fields = _extract_form_fields(body)
        if not fields:
            continue
        forms.append(
            {
                "form_url": absolute_url(BASE_URL, attrs.get("action") or url),
                "method": str(attrs.get("method") or "POST").strip().upper(),
                "fields": fields,
            }
        )
    return forms


def _extract_form_fields(html: str) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    patterns = (
        (r"<input\b(?P<attrs>[^>]*)>", "input"),
        (r"<textarea\b(?P<attrs>[^>]*)>", "textarea"),
        (r"<select\b(?P<attrs>[^>]*)>", "select"),
    )
    for pattern, default_type in patterns:
        for match in re.finditer(pattern, html or "", flags=re.IGNORECASE | re.DOTALL):
            attrs = _html_attrs(match.group("attrs"))
            name = clean_text(str(attrs.get("name") or attrs.get("id") or ""))
            if not name:
                continue
            input_type = clean_text(str(attrs.get("type") or default_type)).lower()
            if input_type in {"hidden", "submit", "button", "reset", "file"}:
                continue
            label = clean_text(str(attrs.get("aria-label") or attrs.get("placeholder") or attrs.get("title") or name))
            fields.append(
                {
                    "name": name,
                    "label": label,
                    "type": input_type,
                    "required": _bool_attr(attrs, "required"),
                }
            )
    return fields


def _extract_apply_url(html: str, *, fallback: str) -> str:
    for match in re.finditer(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", html or "", flags=re.IGNORECASE | re.DOTALL):
        attrs = _html_attrs(match.group("attrs"))
        href = clean_text(str(attrs.get("href") or ""))
        if not href:
            continue
        text = clean_text(match.group("body")).lower()
        haystack = f"{href} {text}".lower()
        if any(part in haystack for part in ("respond", "response", "apply", "отклик", "отправ")):
            return absolute_url(BASE_URL, href)
    return fallback


def _html_attrs(raw_attrs: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(
        r"""(?P<name>[a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)'|(?P<bare>[^\s"'=<>`]+)))?""",
        raw_attrs or "",
    ):
        name = match.group("name").lower()
        attrs[name] = match.group("double") or match.group("single") or match.group("bare") or ""
    return attrs


def _bool_attr(attrs: dict[str, str], name: str) -> bool:
    return name in attrs and str(attrs.get(name) or name).lower() not in {"false", "0", "no"}


def _field_mentions_cover_letter(field: dict[str, Any]) -> bool:
    text = f"{field.get('name') or ''} {field.get('label') or ''}".lower()
    return any(part in text for part in ("cover", "letter", "message", "motivation", "сопровод", "письм"))


def _text_mentions_test(text: str) -> bool:
    lowered = clean_text(text).lower()
    return any(part in lowered for part in ("test task", "screening task", "тестовое", "тестов"))


def _text_mentions_captcha(text: str) -> bool:
    lowered = clean_text(text).lower()
    return "captcha" in lowered or "капч" in lowered


def _remote_from_text(text: str) -> bool | None:
    lowered = (text or "").lower()
    if any(word in lowered for word in ("remote", "удален", "удалён")):
        return True
    return None
