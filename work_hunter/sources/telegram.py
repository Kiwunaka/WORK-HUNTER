from __future__ import annotations

import re
from typing import Any

from ..models import Job
from .common import clean_text, fetch_url

TELEGRAM_CHANNELS = [
    "myjob_it",
    "job_channel_it",
    "rabota_it",
    "dev_jobs",
    "python_jobs_ru",
    "remote_job_search",
    "it_jobs_ru",
    "forpython",
    "jobforjunior",
    "fordevops",
    "forfrontend",
    "fordataengineer",
    "remotegeekjob",
    "foranalysts",
    "forproducts",
]


class TelegramSource:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.channels = config.get("channels", TELEGRAM_CHANNELS)

    def collect(self, profile: dict[str, Any], limit: int | None = None) -> list[Job]:
        keywords = [k.lower() for k in (profile.get("queries") or [])]
        skills = [s.lower() for s in (profile.get("must_have_skills") or [])]
        stop_words = [w.lower() for w in (profile.get("stop_words") or [])]
        jobs: list[Job] = []
        seen: set[str] = set()

        for channel in self.channels:
            if limit is not None and len(jobs) >= limit:
                break
            try:
                for job in _parse_channel(channel, keywords, skills, stop_words):
                    if job.source_id in seen:
                        continue
                    jobs.append(job)
                    seen.add(job.source_id)
                    if limit is not None and len(jobs) >= limit:
                        break
            except Exception:
                continue

        return jobs


def _parse_channel(
    channel: str,
    keywords: list[str],
    skills: list[str],
    stop_words: list[str],
) -> list[Job]:
    url = f"https://t.me/s/{channel}"
    try:
        html = fetch_url(url)
    except Exception:
        return []

    jobs: list[Job] = []
    messages = re.findall(
        r'<div class="tgme_widget_message_wrap[^"]*">.*?</div>\s*</div>\s*</div>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    for msg in messages:
        text = clean_text(msg).replace("\n", " ")
        if len(text) < 50:
            continue

        if stop_words and any(w in text.lower() for w in stop_words):
            continue

        if keywords or skills:
            all_terms = keywords + skills
            if all_terms and not any(t in text.lower() for t in all_terms):
                continue

        source_id = _extract_msg_id(msg)
        if not source_id:
            continue

        title = _extract_title(text)
        company = _extract_company(text)
        salary_text = _extract_salary(text)
        location = _extract_location(text)
        remote = _is_remote(text)
        link = _extract_link(msg)

        jobs.append(
            Job(
                source="telegram",
                source_id=f"{channel}_{source_id}",
                url=link or f"https://t.me/s/{channel}/{source_id}",
                title=title or text[:100],
                company=company,
                salary_text=salary_text,
                location=location,
                remote=remote,
                description=text[:1500],
            )
        )

    return jobs


def _extract_msg_id(msg: str) -> str:
    match = re.search(r'data-post="[^/]+/(\d+)"', msg)
    return match.group(1) if match else ""


def _extract_title(text: str) -> str:
    patterns = [
        r"#(\w+(?:[\s_]\w+)*)",
        r"(Senior|Middle|Junior|Lead)\s+[\w\s]+?(?:Developer|Engineer|Analyst|Manager|Designer|DevOps|QA)",
        r"(Python|Java|Go|Rust|C\+\+|JavaScript|TypeScript|React|Vue|Angular|Node\.js|\.NET|PHP|Ruby|Swift|Kotlin|Flutter|Dart)\s+[\w\s]+?(?:разраб|dev|developer|engineer|программист)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return ""


def _extract_company(text: str) -> str:
    patterns = [
        r"компани[ия]\s+['\"]?([\w\s.-]+)['\"]?",
        r"Company:\s*([\w\s.-]+)",
        r"в\s+['\"]?([\w\s&.-]+)['\"]?\s+(?:ищ|треб|нуж)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()[:100]
    return ""


def _extract_salary(text: str) -> str:
    patterns = [
        r"(?:зп|зарплата|salary|💰|💵)\s*:?\s*(?:до\s*)?([\d\s,.]+(?:\s*[-–—]\s*[\d\s,.]+)?\s*(?:k|K|₽|руб|rub|USD|EUR|€|\$)?)",
        r"([\d\s,.]+(?:\s*[-–—]\s*[\d\s,.]+)?\s*(?:k|K|₽|руб|rub|USD|EUR|€|\$))",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _extract_location(text: str) -> str:
    patterns = [
        r"(?:📍|локация|location|город)\s*:?\s*([\w\s.-]+)",
        r"(Москва|Санкт-Петербург|Новосибирск|Екатеринбург|Казань|Нижний Новгород|Краснодар|Сочи|Минск|Киев|Алматы)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _is_remote(text: str) -> bool:
    remote_words = ["удалён", "удален", "remote", "удаленно", "удалённо"]
    return any(w in text.lower() for w in remote_words)


def _extract_link(msg: str) -> str:
    match = re.search(r'href="(https?://[^"]+)"', msg)
    if match:
        url = match.group(1)
        if "t.me" not in url:
            return url
    return ""
