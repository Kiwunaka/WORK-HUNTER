from __future__ import annotations

import json
import locale
import os
import re
import shutil
import subprocess
import urllib.parse
from typing import Any

from ..hh_transport import HHApiSession
from ..models import Job
from .common import USER_AGENT, absolute_url, clean_text, fetch_url


HH_API_URL = "https://api.hh.ru/vacancies"
HH_API_BASE = "https://api.hh.ru"
HH_SEARCH_URL = "https://hh.ru/search/vacancy"


class HHSource:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def collect(self, profile: dict[str, Any], limit: int | None = None) -> list[Job]:
        if self._access_token():
            try:
                return self._collect_api(profile, limit)
            except RuntimeError:
                if not self.config.get("web_fallback", True):
                    raise
        return self._collect_web(profile, limit)

    def _access_token(self) -> str:
        return str(os.environ.get("HH_ACCESS_TOKEN") or self.config.get("access_token") or "")

    def _collect_api(self, profile: dict[str, Any], limit: int | None = None) -> list[Job]:
        queries = profile.get("queries") or profile.get("desired_roles") or [""]
        per_page = min(int(self.config.get("per_page", 25)), 100)
        pages = max(1, int(self.config.get("pages", 1)))
        if limit is not None:
            per_page = min(per_page, max(1, limit))
        jobs: list[Job] = []
        seen: set[str] = set()
        for query in queries:
            for page in range(pages):
                params = {
                    "text": query,
                    "area": self.config.get("area", 113),
                    "per_page": per_page,
                    "page": page,
                }
                if profile.get("salary_min"):
                    params["salary"] = int(profile["salary_min"])
                url = f"{HH_API_URL}?{urllib.parse.urlencode(params)}"
                user_agent = str(self.config.get("hh_user_agent") or USER_AGENT)
                token = self._access_token()
                headers = {
                    "Accept": "application/json",
                    "HH-User-Agent": user_agent,
                    "User-Agent": user_agent,
                }
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                payload = json.loads(
                    fetch_url(
                        url,
                        headers=headers,
                    )
                )
                for item in payload.get("items", []):
                    job = _job_from_item(item)
                    if job.source_id not in seen:
                        jobs.append(job)
                        seen.add(job.source_id)
                    if limit is not None and len(jobs) >= limit:
                        return jobs
        return jobs

    def _collect_web(self, profile: dict[str, Any], limit: int | None = None) -> list[Job]:
        queries = profile.get("queries") or profile.get("desired_roles") or [""]
        pages = max(1, int(self.config.get("pages", 1)))
        jobs: list[Job] = []
        seen: set[str] = set()
        for query in queries:
            for page in range(pages):
                params = {
                    "text": query,
                    "area": self.config.get("area", 113),
                    "page": page,
                }
                url = f"{HH_SEARCH_URL}?{urllib.parse.urlencode(params)}"
                html = fetch_url(url, headers={"User-Agent": self._web_user_agent()})
                for job in _jobs_from_html(html):
                    if job.source_id not in seen:
                        jobs.append(job)
                        seen.add(job.source_id)
                    if limit is not None and len(jobs) >= limit:
                        return jobs
        return jobs

    def _web_user_agent(self) -> str:
        return str(self.config.get("web_user_agent") or USER_AGENT)


def _salary_text(item: dict[str, Any]) -> tuple[str, int | None, int | None, str]:
    salary = item.get("salary") or {}
    if not salary:
        return "", None, None, ""
    salary_from = salary.get("from")
    salary_to = salary.get("to")
    currency = salary.get("currency") or ""
    if salary_from and salary_to:
        text = f"{salary_from}-{salary_to} {currency}"
    elif salary_from:
        text = f"from {salary_from} {currency}"
    elif salary_to:
        text = f"up to {salary_to} {currency}"
    else:
        text = ""
    return text, salary_from, salary_to, currency


def _job_from_item(item: dict[str, Any]) -> Job:
    salary_text, salary_from, salary_to, currency = _salary_text(item)
    snippet = item.get("snippet") or {}
    description = " ".join(
        str(part or "")
        for part in (snippet.get("requirement"), snippet.get("responsibility"))
        if part
    )
    schedule = (item.get("schedule") or {}).get("id", "")
    return Job(
        source="hh",
        source_id=str(item.get("id", "")),
        url=item.get("alternate_url", ""),
        title=item.get("name", ""),
        company=(item.get("employer") or {}).get("name", ""),
        salary_text=salary_text,
        salary_from=salary_from,
        salary_to=salary_to,
        currency=currency,
        location=(item.get("area") or {}).get("name", ""),
        remote=schedule == "remote",
        description=description,
        published_at=item.get("published_at", ""),
    )


TITLE_RE = re.compile(
    r'<a\b(?=[^>]*data-qa="serp-item__title")[^>]*href="(?P<href>[^"]+)"[^>]*>'
    r"(?P<body>.*?)</a>",
    re.DOTALL,
)


def _jobs_from_html(html: str) -> list[Job]:
    matches = list(TITLE_RE.finditer(html))
    jobs: list[Job] = []
    for index, match in enumerate(matches):
        chunk_end = matches[index + 1].start() if index + 1 < len(matches) else match.end() + 12000
        chunk = html[match.start():chunk_end]
        title = clean_text(match.group("body"))
        href = clean_text(match.group("href"))
        vacancy_id = _first_match(r"vacancyId=(\d+)", chunk) or _first_match(r"/vacancy/(\d+)", href)
        source_id = vacancy_id or href or title
        url = f"https://hh.ru/vacancy/{vacancy_id}" if vacancy_id else absolute_url("https://hh.ru", href)
        jobs.append(
            Job(
                source="hh",
                source_id=source_id,
                url=url,
                title=title,
                company=_extract_data_qa(chunk, "vacancy-serp__vacancy-employer-text"),
                salary_text=_extract_data_qa(chunk, "vacancy-serp__vacancy-compensation"),
                location=_extract_data_qa(chunk, "vacancy-serp__vacancy-address"),
                remote="vacancy-label-work-schedule-remote" in chunk or "Можно удалённо" in chunk,
                description=_hh_description(chunk),
            )
        )
    return jobs


def _extract_data_qa(chunk: str, data_qa: str) -> str:
    pattern = rf'data-qa="{re.escape(data_qa)}"[^>]*>(?P<value>.*?)</(?:span|div|a)>'
    return clean_text(_first_match(pattern, chunk, group="value") or "")


def _first_match(pattern: str, value: str, *, group: str | int = 1) -> str:
    match = re.search(pattern, value, flags=re.DOTALL)
    if not match:
        return ""
    return match.group(group)


def _hh_description(chunk: str) -> str:
    text = clean_text(chunk)
    for marker in ("Откликнуться", "Компании для вас", "По вашему запросу"):
        index = text.find(marker)
        if index >= 0:
            text = text[:index]
    return text[:1500].strip()


class HHApplicantToolAdapter:
    def __init__(self, config: dict[str, Any]):
        self.command = config.get("hh_applicant_tool_command", "hh-applicant-tool")
        self.config_dir = str(config.get("hh_applicant_tool_config_dir") or "")
        self.allow_broad_apply = bool(config.get("allow_broad_apply", False))

    def available(self) -> bool:
        return shutil.which(self.command) is not None

    def dry_run_for_job(self, job: Job, resume_id: str | None = None) -> dict[str, Any]:
        command = self._base_command(
            "apply-vacancies",
            "--search",
            job.title,
            "--per-page",
            "1",
            "--total-pages",
            "1",
            "--dry-run",
        )
        if resume_id:
            command.extend(["--resume-id", resume_id])
        return self._run(command)

    def apply_for_job(
        self,
        job: Job,
        *,
        resume_id: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        if dry_run:
            return self.dry_run_for_job(job, resume_id)
        if not self.allow_broad_apply:
            return {
                "status": "blocked",
                "message": (
                    "Real broad apply is disabled. Enable sources.hh.allow_broad_apply "
                    "after you verify the dry-run command."
                ),
            }
        command = self._base_command(
            "apply-vacancies",
            "--search",
            job.title,
            "--per-page",
            "1",
            "--total-pages",
            "1",
        )
        if resume_id:
            command.extend(["--resume-id", resume_id])
        return self._run(command)

    def _run(self, command: list[str]) -> dict[str, Any]:
        if not self.available():
            return {
                "status": "missing",
                "command": command,
                "message": (
                    "hh-applicant-tool is not installed or is not in PATH. "
                    "Install/auth it first, then retry."
                ),
            }
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=300,
            check=False,
        )
        return {
            "status": "ok" if completed.returncode == 0 else "error",
            "returncode": completed.returncode,
            "command": command,
            "stdout": _clean_process_output(completed.stdout)[-4000:],
            "stderr": _clean_process_output(completed.stderr)[-4000:],
        }

    def _base_command(self, operation: str, *args: str) -> list[str]:
        command = [self.command]
        if self.config_dir:
            command.extend(["--config-dir", self.config_dir])
        command.append(operation)
        command.extend(args)
        return command


def _decode_process_output(value: bytes) -> str:
    for encoding in ("utf-8", locale.getpreferredencoding(False), "cp1251"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", errors="replace")


def _clean_process_output(value: bytes) -> str:
    text = _decode_process_output(value)
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class HHApplyClient:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.base_url = str(config.get("api_base_url") or HH_API_BASE).rstrip("/")
        self.session = HHApiSession(config, base_url=self.base_url)

    def has_token(self) -> bool:
        return self.session.identity.has_access_token()

    def whoami(self) -> dict[str, Any]:
        return self.session.whoami()

    def refresh_token(self) -> dict[str, Any]:
        return self.session.refresh_token()

    def get_vacancy(self, vacancy_id: str) -> dict[str, Any]:
        return self.session.get_vacancy(vacancy_id)

    def search_vacancies(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self.session.search_vacancies(params)

    def get_similar_vacancies(self, vacancy_id: str) -> list[dict[str, Any]]:
        data = self._request_json("GET", f"/vacancies/{vacancy_id}/similar_vacancies")
        return list(data.get("items") or [])

    def list_resumes(self) -> list[dict[str, Any]]:
        return self.session.list_resumes()

    def get_resume(self, resume_id: str) -> dict[str, Any]:
        return self.session.get_resume(resume_id)

    def create_resume(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json("POST", "/resumes", json=payload)

    def publish_resume(self, resume_id: str) -> dict[str, Any]:
        return self._request_json("POST", f"/resumes/{resume_id}/publish")

    def update_resume(self, resume_id: str) -> dict[str, Any]:
        return self.publish_resume(resume_id)

    def list_negotiations(self, status: str = "active") -> list[dict[str, Any]]:
        return self.session.list_negotiations(status=status)

    def list_negotiation_messages(self, negotiation_id: str) -> list[dict[str, Any]]:
        return self.session.list_negotiation_messages(negotiation_id)

    def send_negotiation_message(
        self,
        negotiation_id: str,
        message: str,
        chat_id: str | None = None,
    ) -> dict[str, Any]:
        return self.session.send_negotiation_message(negotiation_id, message, chat_id=chat_id)

    def cancel_negotiation(self, negotiation_id: str, message: str = "") -> dict[str, Any]:
        return self._request_json(
            "DELETE",
            f"/negotiations/active/{negotiation_id}",
            data={"with_decline_message": message},
        )

    def blacklist_employer(self, employer_id: str) -> dict[str, Any]:
        return self._request_json("PUT", f"/employers/blacklisted/{employer_id}")

    def suitable_resumes(self, vacancy_id: str) -> list[dict[str, Any]]:
        return self.session.suitable_resumes(vacancy_id)

    def apply(self, vacancy_id: str, resume_id: str, message: str) -> dict[str, Any]:
        response = self.session.apply(vacancy_id, resume_id, message)
        location = response.headers.get("Location", "")
        raw = _response_json(response)
        if response.status_code == 201:
            return {
                "status": "created",
                "status_code": response.status_code,
                "location": location,
                "raw_result": raw,
            }
        if response.status_code == 303:
            return {
                "status": "redirect",
                "status_code": response.status_code,
                "location": location,
                "raw_result": raw,
            }
        return {
            "status": "error",
            "status_code": response.status_code,
            "error": _hh_error_code(raw),
            "raw_result": raw,
        }

    def submit_vacancy_test(
        self,
        vacancy_id: str,
        resume_id: str,
        answers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/vacancies/{vacancy_id}/test",
            json={"resume_id": resume_id, "answers": answers},
        )

    def request_json(
        self,
        method: str,
        path: str,
        data: Any = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if params:
            kwargs["params"] = params
        if data is not None:
            kwargs["json"] = data
        return self._request_json(method.upper(), path, **kwargs)

    def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        return self.session.request_json(method, path, **kwargs)

    def _request(self, method: str, path: str, **kwargs: Any):
        return self.session.request(method, path, **kwargs)

    def _headers(self, token: str) -> dict[str, str]:
        user_agent = str(self.config.get("hh_user_agent") or USER_AGENT)
        return {
            "Accept": "application/json",
            "HH-User-Agent": user_agent,
            "User-Agent": user_agent,
            "Authorization": f"Bearer {token}",
        }

    def _access_token(self) -> str:
        return str(os.environ.get("HH_ACCESS_TOKEN") or self.config.get("access_token") or "")


def _response_json(response: Any) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _hh_error_code(payload: dict[str, Any]) -> str:
    errors = payload.get("errors") or []
    if errors and isinstance(errors[0], dict):
        return str(errors[0].get("value") or errors[0].get("type") or "unknown")
    return str(payload.get("error") or "unknown")
