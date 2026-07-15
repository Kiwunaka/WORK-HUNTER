from __future__ import annotations

import copy
import csv
import importlib.metadata
import importlib.util
import io
import json
import os
import re
import smtplib
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .config import (
    active_hh_config,
    active_profile,
    clear_config_secret_value,
    config_path,
    database_path,
    default_config,
    load_config,
    mask_secrets,
    merge_config_snapshot_changes,
    merge_masked_config,
    reconcile_config_snapshot,
    save_config,
    update_config,
)
from .letters import chat_completion, draft_cover_letter, draft_cover_letter_ai
from .models import (
    ApplyPlan,
    HHCampaignItem,
    HHContact,
    HHEmployer,
    HHNegotiation,
    HHResume,
    Job,
    LetterDraft,
)
from .hh_agent.approval import ApprovalQueue
from .hh_agent.apply_from_file import ApplyFromFileRow, load_apply_from_file
from .hh_agent.chat_service import HHChatAgentService
from .hh_agent.events import (
    detect_hh_agent_items,
    export_hh_agent_agenda_markdown,
    export_hh_agent_calendar_ics,
)
from .hh_agent.forms import detect_manual_form_url, draft_form_review, normalize_form_mode
from .hh_agent.notifications import agenda_notification_event
from .hh_agent.persona import persona_from_profile
from .hh_agent.policy import VacancyPolicy
from .hh_agent.research import HHVacancyResearchService
from .hh_agent.resume_templates import (
    build_hh_batch_preset_matrix as build_resume_batch_preset_matrix,
    draft_hh_resume_payload_from_template,
)
from .hh_transport import HHTransportError
from .hh_transport.backends import CallbackConfigBackend
from .resume_payloads import load_hh_resume_payload, validate_hh_resume_payload
from .safety import READ_ONLY_HTTP_METHODS, is_literal_confirmation, require_mutation_confirmation
from .scoring import score_job
from .sources.getmatch import parse_getmatch_offer
from .sources import (
    PUBLIC_BOARD_SOURCE_NAMES,
    PUBLIC_BOARD_SPECS,
    GeekJobSource,
    GetmatchSource,
    HHSource,
    HabrSource,
    PublicJobBoardSource,
    RelocateMeSource,
    TelegramSource,
)
from .sources.hh import HHApplyClient
from .sources.common import canonicalize_job_url, clean_text, fetch_url
from .storage import Storage


PACKAGE_ROOT = Path(__file__).resolve().parent
_HH_IDENTITY_WRITE_LOCK = threading.RLock()


def _read_distribution_text(
    distribution: importlib.metadata.Distribution,
    filename: str,
) -> str | None:
    try:
        text = distribution.read_text(filename)
    except (OSError, TypeError, UnicodeError, ValueError):
        return None
    return text if isinstance(text, str) else None


def _same_resolved_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def _file_url_path(url: str) -> Path | None:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme.lower() != "file" or parsed.query or parsed.fragment:
        return None

    url_path = parsed.path
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        url_path = f"//{parsed.netloc}{url_path}"
    try:
        path = Path(urllib.request.url2pathname(url_path))
        return path if path.is_absolute() else None
    except (OSError, TypeError, ValueError):
        return None


def _editable_distribution_matches(
    distribution: importlib.metadata.Distribution,
    project_root: Path,
) -> bool:
    direct_url_text = _read_distribution_text(distribution, "direct_url.json")
    if not direct_url_text:
        return False
    try:
        direct_url = json.loads(direct_url_text)
    except (json.JSONDecodeError, RecursionError, UnicodeError):
        return False
    if not isinstance(direct_url, dict):
        return False
    directory_info = direct_url.get("dir_info")
    url = direct_url.get("url")
    if (
        not isinstance(directory_info, dict)
        or directory_info.get("editable") is not True
        or not isinstance(url, str)
    ):
        return False
    source_path = _file_url_path(url)
    return source_path is not None and _same_resolved_path(source_path, project_root)


def _matching_wheel_version(
    distribution: importlib.metadata.Distribution,
    package_root: Path,
) -> str | None:
    wheel_text = _read_distribution_text(distribution, "WHEEL")
    if not wheel_text or not any(
        line.startswith("Wheel-Version:") for line in wheel_text.splitlines()
    ):
        return None
    try:
        installed_package = Path(str(distribution.locate_file("work_hunter")))
        version = distribution.version
    except (OSError, TypeError, UnicodeError, ValueError):
        return None
    if not _same_resolved_path(installed_package, package_root):
        return None
    return version if isinstance(version, str) and version else None


def _package_details(package_root: Path | None = None) -> dict[str, Any]:
    package_root = package_root or PACKAGE_ROOT
    version = __version__
    install_mode = "source"
    try:
        distributions = list(importlib.metadata.distributions(name="work-hunter"))
    except (
        importlib.metadata.PackageNotFoundError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        distributions = []

    wheel_version: str | None = None
    for distribution in distributions:
        if _editable_distribution_matches(distribution, package_root.parent):
            install_mode = "editable"
            break
        if wheel_version is None:
            wheel_version = _matching_wheel_version(distribution, package_root)
    if install_mode != "editable" and wheel_version is not None:
        install_mode = "wheel"
        version = wheel_version

    static_required = ("index.html", "app.js", "app.css", "manifest.json", "sw.js")
    static_dir = package_root / "web" / "static"
    missing_static = [name for name in static_required if not (static_dir / name).is_file()]
    migrations_dir = package_root / "migrations"
    migrations = sorted(path.name for path in migrations_dir.glob("*.sql"))
    return {
        "status": "ok" if not missing_static and migrations else "error",
        "version": version,
        "install_mode": install_mode,
        "editable": install_mode == "editable",
        "static": {
            "status": "ok" if not missing_static else "missing",
            "missing": missing_static,
        },
        "migrations": {
            "status": "ok" if migrations else "missing",
            "files": migrations,
        },
    }


def _reset_hh_identity_lock_after_fork() -> None:
    global _HH_IDENTITY_WRITE_LOCK
    _HH_IDENTITY_WRITE_LOCK = threading.RLock()


_register_at_fork = getattr(os, "register_at_fork", None)
if callable(_register_at_fork):
    _register_at_fork(after_in_child=_reset_hh_identity_lock_after_fork)


def _format_experience(about: dict[str, Any]) -> str:
    experience_text = ""
    for exp in about.get("experience", []):
        role = exp.get("role", "")
        project = exp.get("project", "")
        details = exp.get("details", [])
        tech = exp.get("tech", [])
        experience_text += f"\n• {role} — {project}\n"
        for detail in details[:3]:
            experience_text += f"  - {detail}\n"
        if tech:
            experience_text += f"  Технологии: {', '.join(tech)}\n"
    return experience_text


def _build_vacancy_candidate_prompt(job: Job, about: dict[str, Any], experience_text: str) -> str:
    return (
        f"Вакансия:\n"
        f"- Позиция: {job.title}\n"
        f"- Компания: {job.company or 'не указана'}\n"
        f"- Описание: {job.description or 'нет описания'}\n"
        f"- Зарплата: {job.salary_text or 'не указана'}\n"
        f"- Удалёнка: {'да' if job.remote else 'нет/не указано'}\n"
        f"- Локация: {job.location or 'не указана'}\n"
        f"\nПрофиль кандидата:\n"
        f"- Резюме: {about.get('summary', '')}\n"
        f"- Навыки: {', '.join(about.get('all_skills', []))}\n"
        f"\nОпыт:\n{experience_text}"
    )


def _parse_fit_json(raw: str) -> dict[str, Any]:
    try:
        match = re.search(r'\{[^{}]*"score"\s*:\s*(\d+)[^{}]*\}', raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
            return {
                "score": min(100, max(0, int(parsed.get("score", 0)))),
                "reasoning": str(parsed.get("reasoning", "")),
            }
    except (json.JSONDecodeError, ValueError, KeyError):
        pass
    numbers = re.findall(r'\b(\d+)\b', raw)
    score = min(100, max(0, int(numbers[-1]))) if numbers else 0
    return {"score": score, "reasoning": raw.strip()}


def _parse_search_json(raw: str) -> dict[str, Any]:
    try:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except json.JSONDecodeError:
        pass
    return {"queries": raw.strip().split()}


def _extract_description(html_content: str) -> str:
    patterns = [
        r'data-qa\s*=\s*["\']vacancy-description["\'][^>]*>(.*?)</div>',
        r'class\s*=\s*["\'].*?vacancy-description.*?["\'][^>]*>(.*?)</div>',
        r'class\s*=\s*["\'].*?job-description.*?["\'][^>]*>(.*?)</div>',
        r'class\s*=\s*["\']description["\'][^>]*>(.*?)</div>',
        r'<article[^>]*>(.*?)</article>',
        r'<section[^>]*class\s*=\s*["\'].*?(?:vacancy|job|description).*?["\'][^>]*>(.*?)</section>',
    ]
    for pattern in patterns:
        match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
        if match:
            return clean_text(match.group(1))
    return ""


def _strip_markdown_json(raw: str) -> str:
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    return raw.strip()


def _unique_jobs(jobs: list[Job], seen: set[str], limit: int | None = None) -> list[Job]:
    unique: list[Job] = []
    for job in jobs:
        key = _job_dedupe_key(job)
        if key in seen:
            continue
        seen.add(key)
        unique.append(job)
        if limit is not None and len(unique) >= limit:
            break
    return unique


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _job_dedupe_key(job: Job) -> str:
    title = re.sub(r"\s+", " ", (job.title or "").strip().lower())
    company = re.sub(r"\s+", " ", (job.company or "").strip().lower())
    if title and company:
        return f"title-company:{title}|{company}"
    if job.url:
        return f"url:{canonicalize_job_url(job.url.strip())}"
    return f"{job.source}:{job.source_id}"


def _first_id(items: list[dict[str, Any]]) -> str | None:
    for item in items:
        item_id = item.get("id")
        if item_id:
            return str(item_id)
    return None


def _hh_resume_from_payload(payload: dict[str, Any]) -> HHResume:
    status = payload.get("status") or {}
    status_id = str(status.get("id") or payload.get("status_id") or "")
    can_publish = payload.get("can_publish_or_update")
    if can_publish is None:
        can_publish = status_id == "published"
    return HHResume(
        id=str(payload.get("id") or ""),
        title=str(payload.get("title") or ""),
        url=str(payload.get("url") or ""),
        alternate_url=str(payload.get("alternate_url") or payload.get("url") or ""),
        status_id=status_id,
        status_name=str(status.get("name") or payload.get("status_name") or ""),
        can_publish_or_update=bool(can_publish),
        total_views=int(payload.get("total_views") or 0),
        new_views=int(payload.get("new_views") or 0),
    )


def _text_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or value.get("name") or "")
    return str(value or "")


def _phone_numbers(contacts: dict[str, Any]) -> str:
    phones = contacts.get("phones") or contacts.get("phone_numbers") or []
    values: list[str] = []
    for phone in phones:
        if isinstance(phone, dict):
            values.append(str(phone.get("formatted") or phone.get("number") or ""))
        else:
            values.append(str(phone))
    return ", ".join(v for v in values if v)


def _campaign_counts() -> dict[str, int]:
    return {"ready": 0, "skipped": 0, "error": 0, "applied": 0}


HH_RELATION_APPLIED_VALUES = {
    "got_response",
    "got_invitation",
    "got_rejection",
}


HH_APPLY_ERROR_OUTCOMES = {
    "already_applied",
    "limit_exceeded",
    "overall_limit",
    "test_required",
    "too_long_message",
    "archived",
    "resume_archived",
    "vacancy_archived",
}


HH_API_LAB_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
HH_API_LAB_BLOCKED_PATH_PARTS = ("/token", "/oauth")


SOURCE_CAPABILITIES: dict[str, dict[str, str]] = {
    "hh": {
        "search": "official_api",
        "detail": "official_api",
        "apply": "official_api",
        "auth": "oauth",
    },
    "habr": {
        "search": "frontend_json",
        "detail": "listing_payload",
        "apply": "external_page",
        "auth": "none",
    },
    "geekjob": {
        "search": "public_json",
        "detail": "listing_payload",
        "apply": "external_page",
        "auth": "none",
    },
    "getmatch": {
        "search": "public_json",
        "detail": "public_json",
        "apply": "personal_auth_recon",
        "auth": "browser_session",
    },
    "relocate_me": {
        "search": "html_listing",
        "detail": "html_detail",
        "apply": "external_page",
        "auth": "none",
    },
    "rvc": {
        "search": "personal_auth_recon",
        "detail": "personal_auth_recon",
        "apply": "personal_auth_recon",
        "auth": "browser_session",
    },
    "hirehi": {
        "search": "public_html",
        "detail": "public_html",
        "apply": "external_page",
        "auth": "none",
    },
    "careerspace": {
        "search": "public_html_or_browser",
        "detail": "public_html_or_browser",
        "apply": "external_page",
        "auth": "none",
    },
    "another_it": {
        "search": "public_html",
        "detail": "public_html",
        "apply": "external_page",
        "auth": "none",
    },
    "jabka": {
        "search": "public_html",
        "detail": "public_html",
        "apply": "external_page",
        "auth": "none",
    },
    "telegram": {
        "search": "public_channels",
        "detail": "message_payload",
        "apply": "external_contact",
        "auth": "none",
    },
}


DEFAULT_SYNC_SOURCE_NAMES = ["hh", "habr", "geekjob", "telegram", *PUBLIC_BOARD_SOURCE_NAMES]


def _safe_preset_params(params: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in params.items():
        lowered = key.lower()
        if any(part in lowered for part in ("token", "secret", "password", "api_key")):
            continue
        safe[key] = value
    return safe


def _normalize_hh_api_lab_request(
    *,
    method: str,
    path: str,
    params: Any = None,
    body: Any = None,
) -> dict[str, Any]:
    normalized_method = method.strip().upper()
    if normalized_method not in HH_API_LAB_ALLOWED_METHODS:
        raise ValueError(f"Unsupported HH API Lab method: {method}")
    raw_path = path.strip()
    if not raw_path:
        raise ValueError("HH API Lab path is required")
    if re.search(r"\s", raw_path):
        raise ValueError("HH API Lab path must not contain whitespace")
    parsed = urllib.parse.urlsplit(raw_path)
    if parsed.scheme or parsed.netloc:
        raise ValueError("HH API Lab path must be relative, for example /me")
    if parsed.fragment:
        raise ValueError("HH API Lab path must not include a URL fragment")
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        raise ValueError("HH API Lab path must start with a single /")
    lowered_path = parsed.path.lower()
    if any(part in lowered_path for part in HH_API_LAB_BLOCKED_PATH_PARTS):
        raise ValueError("HH API Lab does not allow token/oauth endpoints")
    normalized_params = _normalize_hh_api_lab_params(params)
    query_params = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query_params.update(normalized_params)
    normalized_body = _normalize_hh_api_lab_body(body)
    return {
        "method": normalized_method,
        "path": parsed.path,
        "params": query_params,
        "body": normalized_body,
    }


def _normalize_hh_api_call_request(
    *,
    method: str,
    path: str,
    body: Any = None,
) -> dict[str, Any]:
    try:
        return _normalize_hh_api_lab_request(method=method, path=path, body=body)
    except ValueError as exc:
        raise ValueError(str(exc).replace("HH API Lab", "HH API call")) from exc


def _hh_error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, HHTransportError):
        return {
            "status": "error",
            "code": exc.code,
            "status_code": exc.status_code,
            "error": str(exc),
            "payload": mask_secrets(exc.payload),
        }
    return {"status": "error", "error": str(exc)}


def _normalize_hh_api_lab_params(params: Any) -> dict[str, Any]:
    if params in (None, ""):
        return {}
    if not isinstance(params, dict):
        raise ValueError("HH API Lab params must be a JSON object")
    return {
        str(key): value
        for key, value in params.items()
        if value is not None and str(value) != ""
    }


def _normalize_hh_api_lab_body(body: Any) -> Any:
    if body in (None, ""):
        return None
    if isinstance(body, (dict, list)):
        return body
    raise ValueError("HH API Lab body must be a JSON object or array")


def _string_list(value: Any) -> list[str] | None:
    if value in (None, ""):
        return None
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _strict_non_negative_int(value: Any, *, field: str) -> int:
    message = f"{field} must be a non-negative integer"
    if value is None or value == "":
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(message)
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(message) from exc
    if parsed < 0:
        raise ValueError(message)
    return parsed


def _parse_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "y", "remote", "да"}:
        return True
    if lowered in {"0", "false", "no", "n", "нет"}:
        return False
    return None


def _hh_job_from_vacancy_payload(payload: dict[str, Any]) -> Job:
    salary = payload.get("salary") or {}
    salary_from = salary.get("from") if isinstance(salary, dict) else None
    salary_to = salary.get("to") if isinstance(salary, dict) else None
    currency = str(salary.get("currency") or "") if isinstance(salary, dict) else ""
    if salary_from and salary_to:
        salary_text = f"{salary_from}-{salary_to} {currency}".strip()
    elif salary_from:
        salary_text = f"from {salary_from} {currency}".strip()
    elif salary_to:
        salary_text = f"up to {salary_to} {currency}".strip()
    else:
        salary_text = ""
    snippet = payload.get("snippet") or {}
    description = " ".join(
        str(part or "")
        for part in (snippet.get("requirement"), snippet.get("responsibility"))
        if part
    )
    return Job(
        source="hh",
        source_id=str(payload.get("id") or ""),
        url=str(payload.get("alternate_url") or ""),
        title=str(payload.get("name") or ""),
        company=str((payload.get("employer") or {}).get("name") or ""),
        salary_text=salary_text,
        salary_from=salary_from,
        salary_to=salary_to,
        currency=currency,
        location=str((payload.get("area") or {}).get("name") or ""),
        remote=(payload.get("schedule") or {}).get("id") == "remote",
        description=description,
        published_at=str(payload.get("published_at") or ""),
    )


def _hh_job_from_apply_file_row(row: ApplyFromFileRow) -> Job:
    vacancy_id = str(row.vacancy_id or "").strip()
    url = row.url or (f"https://hh.ru/vacancy/{vacancy_id}" if vacancy_id else "")
    title = row.title or f"HH vacancy {vacancy_id}"
    return Job(
        source="hh",
        source_id=vacancy_id,
        url=url,
        title=title,
        company=row.company,
        description="",
    )


def _getmatch_job_from_detail(payload: dict[str, Any]) -> Job | None:
    try:
        return parse_getmatch_offer(payload)
    except Exception:
        return None


def _compact_hh_search_params(params: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in params.items()
        if value is not None and value != "" and value != [] and value != {}
    }


RESUME_CLONE_OMIT_FIELDS = {
    "id",
    "url",
    "alternate_url",
    "created_at",
    "updated_at",
    "download",
    "actions",
    "status",
    "can_publish_or_update",
    "total_views",
    "new_views",
}


def _clone_resume_payload(payload: dict[str, Any], *, title: str | None = None) -> dict[str, Any]:
    clone = {
        key: value
        for key, value in payload.items()
        if key not in RESUME_CLONE_OMIT_FIELDS
    }
    if title is not None:
        clone["title"] = title
    return clone


class _SafeFormatDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def _format_apply_from_file_template(template: str, row: ApplyFromFileRow) -> str:
    if not template:
        return ""
    context = {
        **{str(key): str(value) for key, value in row.raw.items()},
        "row_key": row.row_key,
        "source_index": str(row.source_index),
        "vacancy_id": row.vacancy_id,
        "url": row.url,
        "resume_id": row.resume_id,
        "title": row.title,
        "company": row.company,
    }
    return template.format_map(_SafeFormatDict(context)).strip()


def _participant_type(message: dict[str, Any]) -> str:
    author = message.get("author") or {}
    if isinstance(author, dict):
        return str(author.get("participant_type") or author.get("type") or "")
    return str(author or "")


def _last_message_from_employer(messages: list[dict[str, Any]]) -> bool:
    for message in reversed(messages):
        if not message.get("text"):
            continue
        return _participant_type(message) == "employer"
    return False


def _negotiation_reply_context(payload: dict[str, Any]) -> dict[str, str]:
    vacancy = payload.get("vacancy") or {}
    employer = payload.get("employer") or vacancy.get("employer") or {}
    resume = payload.get("resume") or {}
    state = payload.get("state") or {}
    return {
        "negotiation_id": str(payload.get("id") or ""),
        "chat_id": str(payload.get("chat_id") or ""),
        "vacancy_id": _text_id(vacancy),
        "vacancy_name": str(vacancy.get("name") or vacancy.get("title") or ""),
        "employer_id": _text_id(employer),
        "employer_name": str(employer.get("name") or ""),
        "resume_id": _text_id(resume),
        "resume_title": str(resume.get("title") or ""),
        "state": _text_id(state),
    }


def _format_reply_template(template: str, context: dict[str, str]) -> str:
    return template.format_map(_SafeFormatDict(context)).strip()


def _hh_apply_error_outcome(result: dict[str, Any]) -> str:
    error = str(result.get("error") or "")
    if error in HH_APPLY_ERROR_OUTCOMES:
        return error
    errors = result.get("errors") or []
    if isinstance(errors, list):
        for item in errors:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or item.get("type") or "")
            if value in HH_APPLY_ERROR_OUTCOMES:
                return value
    return "error"


def _parse_ai_filter_response(raw: str, *, mode: str) -> dict[str, Any]:
    try:
        data = json.loads(_strip_markdown_json(raw))
    except json.JSONDecodeError:
        return {
            "mode": mode,
            "suitable": False,
            "needs_review": True,
            "reason": "AI filter returned invalid JSON.",
            "raw": raw,
        }
    suitable = data.get("suitable")
    if not isinstance(suitable, bool):
        return {
            "mode": mode,
            "suitable": False,
            "needs_review": True,
            "reason": "AI filter response did not include a boolean suitable field.",
            "raw": raw,
        }
    return {
        "mode": mode,
        "suitable": suitable,
        "reason": str(data.get("reason") or data.get("reasoning") or ""),
    }


def _parse_hh_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _extract_public_emails(html: str) -> list[str]:
    seen: set[str] = set()
    emails: list[str] = []
    for match in re.finditer(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", html or ""):
        email = match.group(0).strip().strip(".,;:!?)(").lower()
        if email not in seen:
            seen.add(email)
            emails.append(email)
    return emails


def _latest_snapshots_by_employer(snapshots: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        employer_id = str(snapshot.get("employer_id") or "")
        if employer_id:
            latest[employer_id] = snapshot
    return latest


def _format_followup_template(template: str, context: dict[str, Any]) -> str:
    class SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return ""

    return template.format_map(SafeDict(context)).strip()


def _hh_question_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    containers: list[Any] = [
        payload.get("questions"),
        payload.get("items"),
        (payload.get("test") or {}).get("questions") if isinstance(payload.get("test"), dict) else None,
        (payload.get("test") or {}).get("items") if isinstance(payload.get("test"), dict) else None,
        (payload.get("questionnaire") or {}).get("questions")
        if isinstance(payload.get("questionnaire"), dict)
        else None,
    ]
    questions: list[dict[str, Any]] = []
    for container in containers:
        if not isinstance(container, list):
            continue
        for item in container:
            if isinstance(item, dict):
                questions.append(item)
    return questions


def _hh_question_required(payload: dict[str, Any]) -> bool:
    error_code = str(payload.get("error") or payload.get("code") or "").lower()
    if error_code in {"test_required", "questions_required", "question_required"}:
        return True
    if payload.get("has_test") or payload.get("test_required") or payload.get("questions_required"):
        return True
    test = payload.get("test")
    if isinstance(test, dict) and (test.get("required") or test.get("exists")):
        return True
    return bool(_hh_question_items(payload))


def _question_text(question: dict[str, Any]) -> str:
    for key in ("text", "question", "title", "label", "name"):
        value = str(question.get(key) or "").strip()
        if value:
            return clean_text(value)
    return json.dumps(question, ensure_ascii=False)


def send_email_message(message: dict[str, Any], smtp_config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = smtp_config or {}
    host = str(config.get("host") or "")
    from_email = str(config.get("from_email") or config.get("username") or "")
    if not host or not from_email:
        raise RuntimeError("SMTP host and from_email are required.")
    port = int(config.get("port") or 587)
    email_message = EmailMessage()
    email_message["From"] = from_email
    email_message["To"] = str(message["to"])
    email_message["Subject"] = str(message["subject"])
    email_message.set_content(str(message["body"]))
    with smtplib.SMTP(host, port, timeout=int(config.get("timeout") or 20)) as smtp:
        if config.get("starttls", True):
            smtp.starttls()
        username = str(config.get("username") or "")
        password = str(config.get("password") or "")
        if username and password:
            smtp.login(username, password)
        smtp.send_message(email_message)
    return {"status": "sent", "to": str(message["to"])}


def _has_provable_hh_user_identity(
    profile: dict[str, Any],
    *,
    root: Path,
) -> bool:
    if any(
        str(profile.get(key) or "").strip()
        for key in ("access_token", "refresh_token")
    ):
        return True
    cookie_path_raw = str(profile.get("hh_cookie_file") or "").strip()
    if not cookie_path_raw:
        return False
    cookie_path = Path(cookie_path_raw)
    if not cookie_path.is_absolute():
        cookie_path = root / cookie_path
    try:
        return cookie_path.is_file() and cookie_path.stat().st_size > 0
    except OSError:
        return False


def _effective_hh_auth_profile_ids(
    config: dict[str, Any],
    *,
    root: Path,
) -> list[str]:
    effective: set[str] = set()
    sources = config.get("sources") or {}
    legacy_hh = sources.get("hh") if isinstance(sources, dict) else None
    if isinstance(legacy_hh, dict) and _has_provable_hh_user_identity(
        legacy_hh,
        root=root,
    ):
        effective.add("default")

    profiles = config.get("hh_account_profiles") or {}
    if isinstance(profiles, dict):
        for raw_profile_id, profile in profiles.items():
            if not isinstance(raw_profile_id, str) or not isinstance(profile, dict):
                continue
            profile_id = raw_profile_id.strip()
            if not profile_id or profile_id.casefold() == "legacy":
                continue
            if _has_provable_hh_user_identity(profile, root=root):
                effective.add(profile_id)
    return sorted(effective)


class WorkHunter:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else Path.cwd()
        self.config_path = config_path(self.root)
        self.config = load_config(self.config_path)
        self._config_baseline = copy.deepcopy(self.config)
        self._config_aliases: list[dict[str, Any]] = []
        self._storage: Storage | None = None

    @property
    def storage(self) -> Storage:
        if self._storage is None:
            storage = Storage(database_path(self.root))
            self._storage = storage
            profile_ids = _effective_hh_auth_profile_ids(
                self.config,
                root=self.root,
            )
            if len(profile_ids) == 1:
                try:
                    storage.reassign_legacy_application_account(profile_ids[0])
                except sqlite3.IntegrityError:
                    pass
        return self._storage

    def application_identity_migration_report(self) -> dict[str, Any]:
        rows = self.storage.list_legacy_application_identities()
        return mask_secrets(
            {
                "status": "ok" if not rows else "needs_reassignment",
                "sentinel_count": len(rows),
                "rows": rows,
            }
        )

    def active_profile_id(self) -> str:
        selected = self.config.get("profile", "default")
        return selected if isinstance(selected, str) and selected else "default"

    def init(self, *, overwrite: bool = False) -> Path:
        _ = self.storage
        if overwrite or not self.config_path.exists():
            self._replace_config(self.config)
        return self.config_path

    def doctor(self) -> dict[str, Any]:
        config_exists = self.config_path.exists()
        db_path = database_path(self.root)
        package = _package_details()
        profile_info = self.active_profile_info()
        profile_data = profile_info["data"] if isinstance(profile_info.get("data"), dict) else {}
        hh_api = self.hh_auth_status()
        hh_web = self.hh_web_status()
        sources = self.source_capabilities()
        source_rows = {
            row["source"]: row
            for row in self.storage.list_sources()
        }
        source_status = {
            name: {
                **capability,
                "last_sync_at": source_rows.get(name, {}).get("last_sync_at", ""),
                "last_error": source_rows.get(name, {}).get("last_error", ""),
            }
            for name, capability in sources.items()
        }
        missing_deps = [
            name
            for name, module in {
                "mcp": "mcp",
                "requests": "requests",
                "starlette": "starlette",
            }.items()
            if importlib.util.find_spec(module) is None
        ]
        optional_deps = {
            "playwright": importlib.util.find_spec("playwright") is not None,
            "beautifulsoup4": importlib.util.find_spec("bs4") is not None,
            "uvicorn": importlib.util.find_spec("uvicorn") is not None,
        }
        try:
            self.storage.conn.execute("SELECT 1").fetchone()
            db_status = "ok"
            db_error = ""
        except Exception as exc:
            db_status = "error"
            db_error = str(exc)

        mcp_import_ok = importlib.util.find_spec("work_hunter.mcp_server") is not None
        ui_config = self.config.get("ui") or {}
        ui_host = str(ui_config.get("host") or "127.0.0.1")
        enabled_sources = [
            name
            for name, source_config in (self.config.get("sources") or {}).items()
            if isinstance(source_config, dict) and source_config.get("enabled", False)
        ]
        blocked: list[str] = []
        warnings: list[str] = []
        if missing_deps:
            blocked.append("missing_core_dependencies")
        if db_status != "ok":
            blocked.append("database_unavailable")
        if package["static"]["status"] != "ok":
            blocked.append("missing_ui_static")
        if package["migrations"]["status"] != "ok":
            blocked.append("missing_migrations")
        if not config_exists:
            warnings.append("config_missing")
        if hh_api.get("status") != "ok":
            warnings.append("hh_api_not_ready")
        if hh_web.get("status") not in {"ok", "configured", "not_configured"}:
            warnings.append("hh_web_not_ready")
        if ui_host not in {"127.0.0.1", "localhost", "::1"}:
            warnings.append("ui_host_not_local")

        next_actions = self._doctor_next_actions(hh_api, hh_web, missing_deps)
        if not config_exists:
            next_actions.insert(0, "work-hunter init")

        return {
            "status": "ok" if not blocked else "blocked",
            "core": {
                "python": {
                    "status": "ok" if sys.version_info >= (3, 11) else "blocked",
                    "version": sys.version.split()[0],
                },
                "package": package,
                "dependencies": {
                    "status": "ok" if not missing_deps else "missing",
                    "missing": missing_deps,
                    "optional": optional_deps,
                },
                "config": {
                    "status": "ok" if config_exists else "missing",
                    "path": str(self.config_path),
                },
                "database": {
                    "status": db_status,
                    "path": str(db_path),
                    "error": db_error,
                },
                "mcp": {"status": "ok" if mcp_import_ok else "error"},
                "ui": {
                    "status": "ok" if ui_host in {"127.0.0.1", "localhost", "::1"} else "warning",
                    "host": ui_host,
                    "port": int(ui_config.get("port") or 8787),
                },
            },
            "profile": {
                "status": "ok" if profile_data.get("queries") or profile_data.get("desired_roles") else "warning",
                "active": profile_info.get("active"),
                "queries": len(profile_data.get("queries") or []),
                "must_have_skills": len(profile_data.get("must_have_skills") or []),
            },
            "hh_api": hh_api,
            "hh_web": hh_web,
            "sources": {
                "enabled": enabled_sources,
                "capabilities": source_status,
            },
            "recommended_mode": "api_first" if hh_api.get("status") == "ok" else "configure_hh_api",
            "blocked": blocked,
            "warnings": warnings,
            "next_actions": next_actions,
        }

    def _doctor_next_actions(
        self,
        hh_api: dict[str, Any],
        hh_web: dict[str, Any],
        missing_deps: list[str],
    ) -> list[str]:
        actions: list[str] = []
        if missing_deps:
            actions.append('python -m pip install -e ".[dev,browser,ui]"')
        if hh_api.get("status") != "ok":
            actions.extend(str(item) for item in hh_api.get("actions") or [])
        if hh_web.get("status") in {"not_configured", "missing_cookie_file"}:
            actions.append("work-hunter hh web import-cookies ./cookies.txt")
        actions.append('work-hunter hh search --text "python backend" --area 1 --limit 20')
        return _dedupe_strings(actions)

    def save_config(self, config: dict[str, Any]) -> None:
        with _HH_IDENTITY_WRITE_LOCK:
            baseline = copy.deepcopy(self._config_baseline)
            desired = copy.deepcopy(config)

            def merge_changes(fresh: dict[str, Any]) -> None:
                merged = merge_config_snapshot_changes(baseline, desired, fresh)
                fresh.clear()
                fresh.update(merged)

            updated = update_config(
                self.config_path,
                merge_changes,
                fallback=baseline,
            )
            previous_config = self.config
            self._accept_config(updated, config)
            self._remember_config_alias(previous_config)
            self._remember_config_alias(config)
            self.config = config

    def _replace_config(self, config: dict[str, Any]) -> None:
        with _HH_IDENTITY_WRITE_LOCK:
            replacement = copy.deepcopy(config)
            save_config(self.config_path, replacement)
            self._accept_config(replacement, config)
            self.config = config

    def _accept_config(
        self,
        updated: dict[str, Any],
        *aliases: dict[str, Any],
        baseline: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = copy.deepcopy(updated)
        synchronized: set[int] = set()
        for mapping in (self.config, *self._config_aliases, *aliases):
            mapping_id = id(mapping)
            if mapping_id in synchronized:
                continue
            mapping.clear()
            mapping.update(copy.deepcopy(snapshot))
            synchronized.add(mapping_id)
        self._config_baseline = copy.deepcopy(
            snapshot if baseline is None else baseline
        )
        return self.config

    def _remember_config_alias(self, mapping: dict[str, Any]) -> None:
        if all(alias is not mapping for alias in self._config_aliases):
            self._config_aliases.append(mapping)

    def _update_config_fresh(
        self,
        update: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        with _HH_IDENTITY_WRITE_LOCK:
            updated = update_config(
                self.config_path,
                update,
                fallback=self.config,
            )
            return self._accept_config(updated)

    def update_config_from_client(self, patch: dict[str, Any]) -> dict[str, Any]:
        def apply_patch(config: dict[str, Any]) -> None:
            updated = merge_masked_config(config, patch)
            config.clear()
            config.update(updated)

        self._update_config_fresh(apply_patch)
        return mask_secrets(self.config)

    def clear_config_secret(
        self,
        path: str,
        *,
        confirm: object = False,
    ) -> dict[str, Any]:
        blocked = require_mutation_confirmation(
            confirm,
            code="config_secret_clear_requires_confirmation",
            message="Clearing a configuration secret requires explicit confirmation.",
            risk_flags=("configuration_secret_clear",),
            context={"path": path},
        )
        if blocked is not None:
            return blocked
        def clear_secret(config: dict[str, Any]) -> None:
            updated = clear_config_secret_value(config, path)
            config.clear()
            config.update(updated)

        try:
            self._update_config_fresh(clear_secret)
        except ValueError:
            return {
                "status": "blocked",
                "code": "config_secret_path_not_allowed",
                "message": "Configuration secret path is not allowlisted.",
                "path": path,
            }
        return {"status": "ok", "path": path}

    def reset_config_defaults(self) -> None:
        self._replace_config(default_config())

    def sync_sources(
        self,
        *,
        sources: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        source_names = sources or DEFAULT_SYNC_SOURCE_NAMES
        result: dict[str, Any] = {}
        profile = active_profile(self.config)
        max_results = limit if limit is not None else int(self.config.get("research", {}).get("max_results", 0) or 0)
        total_unique = 0
        seen: set[str] = set()
        for source_name in source_names:
            if max_results > 0 and total_unique >= max_results:
                result[source_name] = {"status": "skipped", "count": 0, "reason": "research_limit_reached"}
                continue
            source_config = self.config["sources"].get(source_name, {})
            if not source_config.get("enabled", False):
                result[source_name] = {"status": "disabled", "count": 0}
                continue
            try:
                if source_name == "hh":
                    source_config, backend = self._hh_runtime()
                    collector = self._collector(
                        source_name,
                        source_config,
                        backend=backend,
                    )
                else:
                    collector = self._collector(source_name, source_config)
                remaining = None if max_results <= 0 else max_results - total_unique
                fetch_limit = None if remaining is None else min(max_results, remaining + len(seen))
                jobs = collector.collect(profile, limit=fetch_limit)
                jobs = _unique_jobs(jobs, seen, remaining)
                total_unique += len(jobs)
                count = self.storage.upsert_jobs(jobs)
                self.storage.record_source(source_name, enabled=True)
                result[source_name] = {"status": "ok", "count": count}
            except Exception as exc:
                message = str(exc)
                self.storage.record_source(
                    source_name,
                    enabled=True,
                    last_error=message,
                )
                result[source_name] = {"status": "error", "count": 0, "error": message}
        return result

    def score_jobs(self, *, limit: int = 10000) -> int:
        profile_id = self.active_profile_id()
        jobs = self.storage.list_jobs(limit=limit, profile_id=profile_id)
        count = 0
        for job in jobs:
            score = score_job(job, active_profile(self.config))
            score.job_id = job.id
            score.profile_id = profile_id
            self.storage.save_score(score)
            count += 1
        return count

    def source_capabilities(self) -> dict[str, dict[str, Any]]:
        sources = self.config.get("sources") or {}
        result: dict[str, dict[str, Any]] = {}
        for source_name, capabilities in SOURCE_CAPABILITIES.items():
            source_config = sources.get(source_name) or {}
            apply_capability = str(capabilities.get("apply") or "")
            auth_capability = str(capabilities.get("auth") or "none")
            is_hh = source_name == "hh"
            requires_auth = auth_capability not in {"none", ""}
            requires_confirmation = apply_capability in {
                "official_api",
                "personal_auth_recon",
                "external_contact",
            }
            risk_flags: list[str] = []
            if requires_confirmation:
                risk_flags.append("live_apply_or_contact")
            if apply_capability == "personal_auth_recon":
                risk_flags.append("unverified_personal_auth_adapter")
            result[source_name] = {
                **capabilities,
                "preferred_transport": "api" if is_hh else "public_fetch",
                "fallback_transport": "web_cookie" if is_hh else "external_page",
                "requires_auth": requires_auth,
                "requires_confirmation": requires_confirmation,
                "risk_flags": risk_flags,
                "enabled": bool(source_config.get("enabled", False)),
            }
        return result

    def list_jobs(
        self,
        *,
        limit: int = 100,
        status: str | None = None,
        source: str | None = None,
        min_score: int | None = None,
    ) -> list[Job]:
        return self.storage.list_jobs(
            limit=limit,
            status=status,
            source=source,
            min_score=min_score,
            profile_id=self.active_profile_id(),
        )

    def get_job(self, job_id: int) -> Job | None:
        return self.storage.get_job(job_id, profile_id=self.active_profile_id())

    def prepare_letter(self, job_id: int) -> LetterDraft:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        body = draft_cover_letter(job, active_profile(self.config))
        draft = LetterDraft(job_id=job_id, body=body)
        self.storage.save_letter(draft)
        return draft

    def prepare_letter_ai(self, job_id: int) -> LetterDraft:
        """Generate an AI-powered cover letter using the about section."""
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        profile = active_profile(self.config)
        about = self.config.get("about", {})
        ai_config = self.config.get("ai", {})
        body = draft_cover_letter_ai(job, profile, about, ai_config)
        draft = LetterDraft(job_id=job_id, body=body)
        self.storage.save_letter(draft)
        return draft

    def chat(self, messages: list[dict[str, str]], job_id: int | None = None) -> str:
        """Chat with AI assistant, optionally providing job context."""
        ai_config = self.config.get("ai", {})

        if job_id is not None:
            job = self.storage.get_job(job_id)
            if job:
                score = self.storage.get_score(job_id, self.active_profile_id())
                score_text = ""
                if score:
                    score_text = (
                        f"\nScore: total={score.total_score}, skills={score.skills_score}, "
                        f"salary={score.salary_score}, remote={score.remote_score}\n"
                    )
                system_msg = {
                    "role": "system",
                    "content": (
                        "Ты — AI-ассистент Work Hunter, помогающий с поиском IT-работы. "
                        "Пользователь обсуждает конкретную вакансию. "
                        "Твоя задача: анализировать её, давать советы по fit-у, "
                        "помогать готовиться к собеседованию, писать сопроводительные.\n\n"
                        f"=== ВАКАНСИЯ ===\n"
                        f"Позиция: {job.title}\n"
                        f"Компания: {job.company or 'не указана'}\n"
                        f"Описание: {(job.description or 'нет')[:1500]}\n"
                        f"Зарплата: {job.salary_text or 'не указана'}\n"
                        f"Локация: {job.location or 'не указана'}\n"
                        f"Удалёнка: {'да' if job.remote else 'нет/не указано'}\n"
                        f"Источник: {job.source}{score_text}\n"
                        "Отвечай кратко (2-5 предложений если не просят развёрнуто), "
                        "по делу, на русском. Не выдумывай информацию о пользователе."
                    ),
                }
                messages = [system_msg] + messages
        else:
            system_msg = {
                "role": "system",
                "content": (
                    "Ты — AI-ассистент Work Hunter, персональный помощник по поиску IT-работы. "
                    "Твои возможности: анализировать вакансии, давать советы по резюме, "
                    "помогать готовиться к собеседованиям, оценивать fit позиции. "
                    "Отвечай на русском, кратко и по делу (2-5 предложений если не просят развёрнуто). "
                    "Если пользователь спрашивает про конкретную вакансию — предложи ему сначала "
                    "выбрать её в списке для контекста."
                ),
            }
            messages = [system_msg] + messages

        return chat_completion(messages, ai_config)

    def switch_profile(self, profile_id: str) -> dict[str, Any]:
        """Switch the active search profile."""
        def switch(config: dict[str, Any]) -> None:
            profiles = config.get("profiles", {})
            if profile_id not in profiles:
                available = list(profiles.keys())
                raise ValueError(
                    f"Profile '{profile_id}' not found. "
                    f"Available: {', '.join(available)}"
                )
            config["profile"] = profile_id

        updated = self._update_config_fresh(switch)
        return updated["profiles"][profile_id]

    def update_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        """Update the active profile's search preferences (queries, skills, stop_words)."""
        allowed = ["queries", "desired_roles", "must_have_skills", "nice_to_have_skills",
                   "stop_words", "desired_salary", "desired_cities"]

        def update_active_profile(config: dict[str, Any]) -> None:
            profile_id = config.get("profile", "default")
            profiles = config.get("profiles", {})
            if profile_id not in profiles:
                raise ValueError(f"Profile '{profile_id}' not found")
            for key in allowed:
                if key in data:
                    profiles[profile_id][key] = data[key]

        updated = self._update_config_fresh(update_active_profile)
        profile_id = updated.get("profile", "default")
        return updated["profiles"][profile_id]

    def active_profile_info(self) -> dict[str, Any]:
        """Return the active profile id and data."""
        profile_id = self.config.get("profile", "default")
        profile_data = active_profile(self.config)
        profiles = self.config.get("profiles", {})
        return {
            "active": profile_id,
            "available": list(profiles.keys()),
            "data": profile_data,
        }

    def hh_config(self) -> dict[str, Any]:
        return active_hh_config(self.config)

    def _persist_hh_identity_patch(
        self,
        account_name: str,
        patch: dict[str, Any],
    ) -> None:
        def merge_identity(config: dict[str, Any]) -> None:
            accounts = config.setdefault("hh_account_profiles", {})
            account_config = accounts.setdefault(account_name, {})
            account_config.update(patch)
            if account_name == "default":
                sources = config.setdefault("sources", {})
                source_config = sources.setdefault("hh", {})
                source_config.update(patch)

        with _HH_IDENTITY_WRITE_LOCK:
            baseline = copy.deepcopy(self._config_baseline)
            desired = copy.deepcopy(self.config)
            updated = update_config(
                self.config_path,
                merge_identity,
                fallback=self.config,
            )
            reconciled = merge_config_snapshot_changes(
                baseline,
                desired,
                updated,
            )
            merge_identity(reconciled)
            self._accept_config(reconciled, baseline=updated)

    def _hh_runtime(self) -> tuple[dict[str, Any], CallbackConfigBackend]:
        with _HH_IDENTITY_WRITE_LOCK:
            baseline = copy.deepcopy(self._config_baseline)
            desired = copy.deepcopy(self.config)
            fresh, reconciled = reconcile_config_snapshot(
                self.config_path,
                baseline,
                desired,
            )
            self._accept_config(reconciled, baseline=fresh)
            config_snapshot = copy.deepcopy(self.config)
        account_name = str(config_snapshot.get("hh_account_profile") or "default")
        config = active_hh_config(config_snapshot)

        def save_identity_patch(patch: dict[str, Any]) -> None:
            self._persist_hh_identity_patch(account_name, patch)

        backend = CallbackConfigBackend(config, save_identity_patch)
        return config, backend

    def _hh_client(self) -> HHApplyClient:
        config, backend = self._hh_runtime()
        return HHApplyClient(config, backend=backend)

    def list_hh_account_profiles(self) -> dict[str, Any]:
        accounts = self.config.get("hh_account_profiles") or {}
        items: list[dict[str, Any]] = []
        active_account = str(self.config.get("hh_account_profile") or "default")
        names = sorted(accounts.keys(), key=lambda item: (item != active_account, item))
        for name in names:
            account = accounts.get(name) or {}
            items.append(
                {
                    "name": name,
                    "active": name == active_account,
                    "has_access_token": bool(str(account.get("access_token") or "")),
                    "has_refresh_token": bool(str(account.get("refresh_token") or "")),
                    "has_client_credentials": bool(
                        str(account.get("client_id") or "")
                        and str(account.get("client_secret") or "")
                    ),
                    "access_expires_at": str(account.get("access_expires_at") or ""),
                }
            )
        return {"active": active_account, "accounts": items}

    def save_hh_account_profile(self, name: str, **values: Any) -> dict[str, Any]:
        if not name.strip():
            raise ValueError("HH account profile name is required")
        account_keys = (
            "access_token",
            "refresh_token",
            "access_expires_at",
            "client_id",
            "client_secret",
        )

        def save_account(config: dict[str, Any]) -> None:
            accounts = dict(config.get("hh_account_profiles") or {})
            account = dict(accounts.get(name) or {})
            for key in account_keys:
                if key in values and values[key] is not None:
                    account[key] = values[key]
            accounts[name] = account
            config["hh_account_profiles"] = accounts

        updated = self._update_config_fresh(save_account)
        account = updated["hh_account_profiles"][name]
        return {
            "name": name,
            "active": name == updated.get("hh_account_profile", "default"),
            "has_access_token": bool(str(account.get("access_token") or "")),
            "has_refresh_token": bool(str(account.get("refresh_token") or "")),
        }

    def use_hh_account_profile(self, name: str) -> dict[str, Any]:
        def use_account(config: dict[str, Any]) -> None:
            accounts = config.get("hh_account_profiles") or {}
            if name not in accounts:
                raise ValueError(f"HH account profile '{name}' not found")
            config["hh_account_profile"] = name

        updated = self._update_config_fresh(use_account)
        return {"active": name, "profile": updated.get("profile", "default")}

    def latest_letter(self, job_id: int) -> LetterDraft | None:
        return self.storage.get_latest_letter(job_id)

    def mark_job(self, job_id: int, status: str, note: str = "") -> None:
        if self.storage.get_job(job_id) is None:
            raise ValueError(f"Job {job_id} not found")
        self.storage.set_status(job_id, status, note)

    def apply_hh(
        self,
        job_id: int,
        *,
        resume_id: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        if dry_run:
            return self.prepare_apply_plan(job_id, resume_id=resume_id)
        return {
            "status": "blocked",
            "message": "Real HH apply requires POST /api/jobs/{id}/confirm-apply with confirm=true.",
        }

    def apply_hh_from_file(
        self,
        path: str | Path,
        *,
        resume_id: str | None = None,
        template: str = "",
        dry_run: bool = True,
        confirm: bool = False,
        limit: int | None = None,
        min_interval_seconds: float = 0.0,
        skip_processed: bool = True,
    ) -> dict[str, Any]:
        source_path = str(Path(path))
        rows = load_apply_from_file(path)
        if not dry_run and not confirm:
            return {
                "status": "blocked",
                "count": 0,
                "message": "Explicit confirm=True is required before applying from file.",
                "rows": [],
            }

        report_rows: list[dict[str, Any]] = []
        processed_count = 0
        min_interval = max(0.0, float(min_interval_seconds or 0.0))
        last_send_at = 0.0
        for row in rows:
            can_process = limit is None or processed_count < limit
            if not dry_run and can_process and row.enabled and row.vacancy_id and last_send_at and min_interval > 0:
                elapsed = time.monotonic() - last_send_at
                if elapsed < min_interval:
                    time.sleep(min_interval - elapsed)
            row_result = self._apply_hh_from_file_row(
                row,
                source_path=source_path,
                default_resume_id=resume_id or "",
                template=template,
                dry_run=dry_run,
                confirm=confirm,
                can_process=can_process,
                skip_processed=skip_processed,
            )
            if row_result["status"] in {"planned", "applied", "external_redirect"}:
                processed_count += 1
            if not dry_run and row_result.get("will_send"):
                last_send_at = time.monotonic()
            row_result.pop("will_send", None)
            report_rows.append(row_result)

        if dry_run:
            status = "planned" if processed_count else "empty"
        else:
            statuses = {item["status"] for item in report_rows}
            status = "completed" if statuses and statuses <= {"applied", "external_redirect", "skipped", "skipped_processed"} else "partial"
        return {
            "status": status,
            "count": processed_count,
            "source_path": source_path,
            "dry_run": dry_run,
            "rows": report_rows,
        }

    def _apply_hh_from_file_row(
        self,
        row: ApplyFromFileRow,
        *,
        source_path: str,
        default_resume_id: str,
        template: str,
        dry_run: bool,
        confirm: bool,
        can_process: bool,
        skip_processed: bool,
    ) -> dict[str, Any]:
        selected_resume = row.resume_id or default_resume_id
        base = {
            "row_key": row.row_key,
            "source_index": row.source_index,
            "vacancy_id": row.vacancy_id,
            "resume_id": selected_resume,
            "enabled": row.enabled,
        }
        if not row.enabled:
            return {**base, "status": "skipped", "reason": "disabled"}
        if not row.vacancy_id:
            return {**base, "status": "invalid", "reason": "vacancy_id_missing"}
        if not can_process:
            return {**base, "status": "skipped", "reason": "limit_reached"}

        existing = self.storage.get_hh_apply_from_file_state(source_path=source_path, row_key=row.row_key)
        if not dry_run and skip_processed and existing and existing.get("status") in {"applied", "external_redirect"}:
            return {**base, "status": "skipped_processed", "state": existing}

        job_id = self.storage.upsert_job(_hh_job_from_apply_file_row(row))
        letter = row.letter or _format_apply_from_file_template(template, row)
        if dry_run:
            result = {**base, "status": "planned", "job_id": job_id, "letter": letter}
            self.storage.save_hh_apply_from_file_state(
                source_path=source_path,
                row_key=row.row_key,
                vacancy_id=row.vacancy_id,
                resume_id=selected_resume,
                status="planned",
                result=result,
            )
            return result

        apply_result = self.confirm_apply(
            job_id,
            resume_id=selected_resume or None,
            letter=letter,
            confirm=confirm,
        )
        status = str(apply_result.get("status") or "error")
        result = {
            **base,
            "status": status,
            "job_id": job_id,
            "letter": letter,
            "result": apply_result,
            "will_send": True,
        }
        self.storage.save_hh_apply_from_file_state(
            source_path=source_path,
            row_key=row.row_key,
            vacancy_id=row.vacancy_id,
            resume_id=selected_resume,
            status=status,
            result=result,
        )
        return result

    def prepare_apply_plan(
        self,
        job_id: int,
        *,
        resume_id: str | None = None,
        letter: str | None = None,
    ) -> dict[str, Any]:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        letter_body = self._application_letter(job_id, letter)
        if job.source != "hh":
            return self._prepare_external_apply_plan(
                job,
                job_id=job_id,
                resume_id=resume_id,
                letter_body=letter_body,
            )

        client = self._hh_client()
        if not client.has_token():
            return self._store_apply_plan(ApplyPlan(
                job_id=job_id,
                source=job.source,
                mode="api",
                resume_id=resume_id,
                letter=letter_body,
                risk_flags=["hh_token_missing"],
                status="blocked",
                external_url=job.url,
                raw_result={"message": "HH access token is required for exact API apply."},
            ).to_dict())

        vacancy = client.get_vacancy(job.source_id)
        selected_resume = resume_id or _first_id(client.suitable_resumes(job.source_id))
        if not selected_resume:
            selected_resume = _first_id(client.list_resumes())

        risk_flags: list[str] = []
        if vacancy.get("response_letter_required"):
            risk_flags.append("letter_required")
        if vacancy.get("has_test") or vacancy.get("test"):
            risk_flags.append("test_required")
        if vacancy.get("archived"):
            risk_flags.append("archived")
        relations = list(vacancy.get("relations") or [])
        if relations:
            risk_flags.append("has_relations")
        if any(relation in HH_RELATION_APPLIED_VALUES for relation in relations):
            risk_flags.append("already_applied")
        if not selected_resume:
            risk_flags.append("no_resume")

        return self._store_apply_plan(ApplyPlan(
            job_id=job_id,
            source=job.source,
            mode="api",
            resume_id=selected_resume,
            letter=letter_body,
            risk_flags=risk_flags,
            requires_confirmation=True,
            status="ready" if selected_resume else "blocked",
            external_url=str(vacancy.get("alternate_url") or job.url),
            raw_result={"vacancy": vacancy},
        ).to_dict())

    def _store_apply_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        plan_id = self.storage.save_apply_plan(plan)
        plan["id"] = plan_id
        plan["plan_id"] = plan_id
        return plan

    def _prepare_external_apply_plan(
        self,
        job: Job,
        *,
        job_id: int,
        resume_id: str | None,
        letter_body: str,
    ) -> dict[str, Any]:
        capabilities = self.source_capabilities().get(
            job.source,
            {
                "search": "unknown",
                "detail": "unknown",
                "apply": "external_page",
                "auth": "unknown",
                "enabled": False,
            },
        )
        apply_capability = str(capabilities.get("apply") or "")
        mode = "external_api_recon" if apply_capability == "personal_auth_recon" else "external_page"
        risk_flags = ["external_manual_apply"]
        raw_result: dict[str, Any] = {
            "message": "Source does not have a configured direct apply API.",
            "capabilities": capabilities,
            "apply": {},
        }
        external_url = job.url
        if apply_capability == "personal_auth_recon":
            risk_flags.append("personal_auth_required")
            raw_result["message"] = (
                "Apply flow requires your own authenticated browser session. "
                "Use api-recon-har to map the exact endpoint before enabling direct send."
            )

        if job.source == "getmatch":
            detail = self._getmatch_apply_detail(job)
            if detail.get("ok"):
                source_detail = detail["payload"]
                source_job = _getmatch_job_from_detail(source_detail)
                if source_job is not None:
                    external_url = source_job.url or external_url
                    if source_job.description and source_job.description != job.description:
                        self.storage.update_job_description(job_id, source_job.description)
                apply_meta = {
                    "cover_letter_required": bool(source_detail.get("cover_letter_required")),
                    "cover_letter_placeholder": clean_text(str(source_detail.get("cover_letter_placeholder") or "")),
                    "application": source_detail.get("application"),
                }
                raw_result["source_detail"] = source_detail
                raw_result["apply"] = apply_meta
                risk_flags.append("source_detail_api_available")
                if apply_meta["cover_letter_required"]:
                    risk_flags.append("letter_required")
            else:
                raw_result["source_detail_error"] = detail.get("error")
                risk_flags.append("source_detail_api_error")

        raw_result["next_actions"] = self._external_apply_next_actions(
            job=job,
            external_url=external_url,
            capabilities=capabilities,
            apply_meta=dict(raw_result.get("apply") or {}),
        )

        return self._store_apply_plan(ApplyPlan(
            job_id=job_id,
            source=job.source,
            mode=mode,
            resume_id=resume_id,
            letter=letter_body,
            risk_flags=list(dict.fromkeys(risk_flags)),
            status="external",
            external_url=external_url,
            raw_result=raw_result,
        ).to_dict())

    def _getmatch_apply_detail(self, job: Job) -> dict[str, Any]:
        try:
            source = GetmatchSource((self.config.get("sources") or {}).get("getmatch") or {})
            return {"ok": True, "payload": source.get_offer(job.source_id)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _external_apply_next_actions(
        self,
        *,
        job: Job,
        external_url: str,
        capabilities: dict[str, Any],
        apply_meta: dict[str, Any],
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = [
            {
                "type": "open_url",
                "url": external_url,
            }
        ]
        placeholder = clean_text(str(apply_meta.get("cover_letter_placeholder") or ""))
        if placeholder:
            actions.append(
                {
                    "type": "cover_letter_hint",
                    "message": placeholder,
                }
            )
        elif apply_meta.get("cover_letter_required"):
            actions.append(
                {
                    "type": "cover_letter_hint",
                    "message": "Cover letter is required by this source.",
                }
            )
        if capabilities.get("apply") == "personal_auth_recon":
            host = urllib.parse.urlsplit(external_url or job.url).hostname or job.source
            actions.append(
                {
                    "type": "api_recon_har",
                    "host": host,
                    "command": f"work-hunter api-recon-har <session.har> --host {host}",
                    "message": "Export a HAR from your own logged-in browser session, then run this command to map the apply endpoint.",
                }
            )
        return actions

    def confirm_apply(
        self,
        job_id: int,
        *,
        resume_id: str | None = None,
        letter: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        if not confirm:
            return {
                "status": "blocked",
                "message": "Explicit confirmation is required before sending a real application.",
            }

        plan = self.prepare_apply_plan(job_id, resume_id=resume_id, letter=letter)
        if plan.get("status") != "ready":
            return plan
        if plan.get("source") != "hh":
            return {
                "status": "blocked",
                "message": "Only HH vacancies can be sent directly through the API.",
                "plan": plan,
            }

        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        selected_resume = str(plan.get("resume_id") or "")
        if not selected_resume:
            return {
                "status": "blocked",
                "message": "No suitable resume was selected for this vacancy.",
                "plan": plan,
            }

        client = self._hh_client()
        result = client.apply(job.source_id, selected_resume, str(plan.get("letter") or ""))
        if result.get("status") == "created":
            self.storage.save_application(job_id, "applied", json.dumps(result, ensure_ascii=False))
            self.storage.set_status(job_id, "applied", "HH API application sent")
            plan["status"] = "applied"
        elif result.get("status") == "redirect":
            self.storage.save_application(job_id, "external_redirect", json.dumps(result, ensure_ascii=False))
            plan["status"] = "external_redirect"
            plan["external_url"] = result.get("location") or plan.get("external_url", "")
        else:
            plan["status"] = _hh_apply_error_outcome(result)
        plan["raw_result"] = result
        return plan

    def confirm_apply_plan(self, plan_id: int, *, confirm: bool = False) -> dict[str, Any]:
        stored_plan = self.storage.get_apply_plan(plan_id)
        if stored_plan is None:
            return {
                "status": "error",
                "code": "apply_plan_not_found",
                "message": f"Apply plan {plan_id} was not found.",
            }
        if not confirm:
            return {
                "status": "blocked",
                "message": "Explicit confirmation is required before sending a real application.",
                "plan": stored_plan,
            }
        if stored_plan.get("status") != "ready":
            return {
                "status": "blocked",
                "message": "Only ready apply plans can be confirmed.",
                "plan": stored_plan,
            }
        result = self.confirm_apply(
            int(stored_plan["job_id"]),
            resume_id=str(stored_plan.get("resume_id") or "") or None,
            letter=str(stored_plan.get("letter") or ""),
            confirm=True,
        )
        self.storage.update_apply_plan_status(
            plan_id,
            str(result.get("status") or "error"),
            confirmed=True,
        )
        result["plan_id"] = plan_id
        return result

    def detect_hh_question_requirements(self, payload: dict[str, Any]) -> dict[str, Any]:
        questions = _hh_question_items(payload)
        return {
            "required": _hh_question_required(payload),
            "count": len(questions),
            "questions": questions,
        }

    def plan_hh_test_answers(
        self,
        vacancy_id: str,
        *,
        resume_id: str,
    ) -> dict[str, Any]:
        client = self._hh_client()
        if not client.has_token():
            return {"status": "blocked", "message": "HH access token is required.", "vacancy_id": vacancy_id}
        vacancy = client.get_vacancy(vacancy_id)
        resume = client.get_resume(resume_id) if resume_id else {}
        requirement = self.detect_hh_question_requirements(vacancy)
        if not requirement["required"]:
            return {
                "status": "no_test",
                "vacancy_id": vacancy_id,
                "resume_id": resume_id,
                "count": 0,
                "answers": [],
                "requires_confirmation": True,
            }
        answers: list[dict[str, Any]] = []
        for index, question in enumerate(requirement["questions"], start=1):
            question_id = str(question.get("id") or question.get("key") or index)
            answers.append(
                {
                    "question_id": question_id,
                    "answer": self._draft_hh_test_answer(vacancy, resume, question),
                }
            )
        if not answers:
            return {
                "status": "needs_review",
                "vacancy_id": vacancy_id,
                "resume_id": resume_id,
                "count": 0,
                "answers": [],
                "message": "HH indicates a test is required, but no questions were available in the vacancy payload.",
                "requires_confirmation": True,
            }
        return {
            "status": "planned",
            "vacancy_id": vacancy_id,
            "resume_id": resume_id,
            "count": len(answers),
            "answers": answers,
            "requires_confirmation": True,
        }

    def confirm_hh_test_answers(
        self,
        vacancy_id: str,
        *,
        resume_id: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        plan = self.plan_hh_test_answers(vacancy_id, resume_id=resume_id)
        if plan.get("status") != "planned":
            return plan
        if not confirm:
            return {
                "status": "blocked",
                "message": "Explicit confirm=True is required before submitting HH test answers.",
                "plan": plan,
            }
        client = self._hh_client()
        result = client.submit_vacancy_test(vacancy_id, resume_id, list(plan["answers"]))
        return {"status": "submitted", "vacancy_id": vacancy_id, "resume_id": resume_id, "result": result}

    def detect_hh_manual_form(self, payload: dict[str, Any]) -> dict[str, Any]:
        form_url = detect_manual_form_url(payload)
        return {
            "required": bool(form_url or payload.get("manual_form_required") or payload.get("response_url")),
            "form_url": form_url,
        }

    def review_hh_manual_form(
        self,
        form: dict[str, Any],
        *,
        vacancy: dict[str, Any] | None = None,
        resume: dict[str, Any] | None = None,
        extra_answers: dict[str, str] | None = None,
        submit: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        vacancy = vacancy or {}
        resume = resume or {}
        form_mode = normalize_form_mode(str(self.hh_config().get("form_mode") or "manual"))
        vacancy_id = _text_id(vacancy)
        resume_id = str(resume.get("id") or "")
        persona = persona_from_profile(active_profile(self.config), self.config.get("about", {})).to_dict()
        review = draft_form_review(
            form,
            persona=persona,
            resume=resume,
            vacancy=vacancy,
            extra_answers=extra_answers,
        )
        payload = {
            "form": form,
            "form_mode": form_mode,
            "form_url": review.form_url,
            "vacancy": vacancy,
            "resume": resume,
            "submit_requested": submit,
        }
        result: dict[str, Any]

        if form_mode == "off":
            result = {
                "status": "blocked",
                "reason": "form_mode_off",
                "form_mode": form_mode,
                "review": review.to_dict(),
            }
            review_id = self.storage.save_hh_form_review(
                vacancy_id=vacancy_id,
                resume_id=resume_id,
                status="blocked",
                payload=payload,
                result=result,
            )
            return {**result, "review_id": review_id}

        if submit and not confirm:
            result = {
                "status": "blocked",
                "reason": "explicit_confirmation_required",
                "requires_confirmation": True,
                "form_mode": form_mode,
                "review": review.to_dict(),
            }
            review_id = self.storage.save_hh_form_review(
                vacancy_id=vacancy_id,
                resume_id=resume_id,
                status="blocked",
                payload=payload,
                result=result,
            )
            return {**result, "review_id": review_id}

        needs_approval = bool(review.unknown_fields) or form_mode == "manual" or submit
        if needs_approval:
            reason = "unknown_form_fields" if review.unknown_fields else "manual_form_review"
            pending_payload = {
                "form_url": review.form_url,
                "answers": review.answers,
                "unknown_fields": review.unknown_fields,
                "risk_flags": review.risk_flags,
                "vacancy_id": vacancy_id,
                "resume_id": resume_id,
                "form_mode": form_mode,
                "submit_requested": submit,
            }
            pending_id = ApprovalQueue(self.storage).escalate_to_user(
                action_type="form_submit",
                payload=pending_payload,
                confidence=review.confidence,
                reason=reason,
            )
            result = {
                "status": "needs_approval",
                "reason": reason,
                "pending_message_id": pending_id,
                "requires_confirmation": True,
                "form_mode": form_mode,
                "review": review.to_dict(),
            }
        else:
            result = {
                "status": "planned",
                "reason": "ready_for_review",
                "requires_confirmation": True,
                "form_mode": form_mode,
                "review": review.to_dict(),
            }

        review_id = self.storage.save_hh_form_review(
            vacancy_id=vacancy_id,
            resume_id=resume_id,
            status=str(result["status"]),
            payload=payload,
            result=result,
        )
        return {**result, "review_id": review_id}

    def _draft_hh_test_answer(
        self,
        vacancy: dict[str, Any],
        resume: dict[str, Any],
        question: dict[str, Any],
    ) -> str:
        prompt = (
            "Draft a concise, honest answer to the HH vacancy test question. "
            "Use only the vacancy and resume context. Do not invent specific facts. "
            "Return only the answer text.\n"
            f"Question: {_question_text(question)}\n"
            f"Vacancy: {json.dumps(vacancy, ensure_ascii=False)}\n"
            f"Resume: {json.dumps(resume, ensure_ascii=False)}"
        )
        try:
            answer = chat_completion(
                [
                    {
                        "role": "system",
                        "content": "You help draft truthful job application test answers.",
                    },
                    {"role": "user", "content": prompt},
                ],
                self.config.get("ai", {}),
            )
        except Exception:
            answer = (
                "Спасибо за вопрос. Мой опыт релевантен этой вакансии: я работал с похожими "
                "задачами и готов подробнее обсудить конкретные требования."
            )
        return clean_text(answer)

    def sync_hh_resumes(self) -> dict[str, Any]:
        client = self._hh_client()
        if not client.has_token():
            return {"status": "blocked", "count": 0, "message": "HH access token is required."}
        count = 0
        try:
            payloads = client.list_resumes()
        except Exception as exc:
            return {**_hh_error_payload(exc), "count": 0}
        for payload in payloads:
            resume = _hh_resume_from_payload(payload)
            if not resume.id:
                continue
            self.storage.upsert_hh_resume(resume)
            count += 1
        return {"status": "ok", "count": count}

    def hh_whoami(self) -> dict[str, Any]:
        client = self._hh_client()
        if not client.has_token():
            return {"status": "blocked", "message": "HH access token is required."}
        try:
            return client.whoami()
        except Exception as exc:
            return _hh_error_payload(exc)

    def hh_auth_status(self) -> dict[str, Any]:
        hh_config = self.hh_config()
        has_access_token = bool(str(hh_config.get("access_token") or ""))
        has_refresh_token = bool(str(hh_config.get("refresh_token") or ""))
        client_credentials_configured = bool(
            str(hh_config.get("client_id") or "")
            and str(hh_config.get("client_secret") or "")
        )
        actions: list[str] = []
        if not has_access_token:
            actions.append("Set sources.hh.access_token")
            return {
                "status": "missing_access_token",
                "authorized": False,
                "refresh_ready": has_refresh_token,
                "client_credentials_configured": client_credentials_configured,
                "access_expires_at": str(hh_config.get("access_expires_at") or ""),
                "actions": actions,
            }

        client = self._hh_client()
        try:
            me_payload = client.whoami()
            me = me_payload.get("me") if me_payload.get("status") == "ok" else me_payload
            if isinstance(me_payload, dict) and me_payload.get("status") == "error":
                raise RuntimeError(str(me_payload.get("error") or "HH API auth check failed"))
        except Exception as exc:
            if has_refresh_token:
                actions.append("Run hh-refresh-token, then retry hh-auth-status.")
            else:
                actions.append("Refresh or replace sources.hh.access_token.")
            return {
                "status": "invalid_access_token",
                "authorized": False,
                "refresh_ready": has_refresh_token,
                "client_credentials_configured": client_credentials_configured,
                "access_expires_at": str(hh_config.get("access_expires_at") or ""),
                "error": str(exc),
                "actions": actions,
            }

        if not has_refresh_token:
            actions.append("Add sources.hh.refresh_token to enable automatic token refresh.")
        return {
            "status": "ok",
            "authorized": True,
            "refresh_ready": has_refresh_token,
            "client_credentials_configured": client_credentials_configured,
            "access_expires_at": str(hh_config.get("access_expires_at") or ""),
            "me": me,
            "actions": actions,
        }

    def import_hh_token(
        self,
        *,
        access_token: str,
        refresh_token: str = "",
        client_id: str = "",
        client_secret: str = "",
        access_expires_at: str = "",
        profile: str | None = None,
    ) -> dict[str, Any]:
        name = profile or str(self.config.get("hh_account_profile") or "default")
        result = self.save_hh_account_profile(
            name,
            access_token=access_token,
            refresh_token=refresh_token or None,
            access_expires_at=access_expires_at or None,
            client_id=client_id or None,
            client_secret=client_secret or None,
        )
        if self.config.get("hh_account_profile") != name:
            self.use_hh_account_profile(name)
        whoami: dict[str, Any]
        try:
            whoami = self.hh_whoami()
        except Exception as exc:
            whoami = {"status": "error", "error": str(exc)}
        return {
            "status": "ok",
            "profile": name,
            "has_access_token": bool(access_token),
            "has_refresh_token": bool(refresh_token),
            "whoami": whoami,
            "secrets_redacted": True,
            "account": result,
        }

    def hh_web_status(self) -> dict[str, Any]:
        hh_config = self.hh_config()
        cookie_file_raw = str(hh_config.get("hh_cookie_file") or "")
        if not cookie_file_raw:
            return {
                "status": "not_configured",
                "has_cookie_file": False,
                "has_xsrf": False,
                "can_load_resumes_page": False,
                "resumes_page_status": "not_configured",
                "can_search_url": False,
                "can_tests": False,
                "can_chatik": False,
                "resume_hashes": [],
                "actions": ["work-hunter hh web import-cookies ./cookies.txt"],
            }
        cookie_file = Path(cookie_file_raw)
        if not cookie_file.is_absolute():
            cookie_file = self.root / cookie_file
        if not cookie_file.exists():
            return {
                "status": "missing_cookie_file",
                "cookie_file": str(cookie_file),
                "has_cookie_file": False,
                "has_xsrf": False,
                "can_load_resumes_page": False,
                "resumes_page_status": "missing_cookie_file",
                "can_search_url": False,
                "can_tests": False,
                "can_chatik": False,
                "resume_hashes": [],
                "actions": ["refresh cookies", "work-hunter hh web import-cookies ./cookies.txt"],
            }
        text = cookie_file.read_text(encoding="utf-8", errors="replace")
        cookie_count = sum(
            1
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        has_xsrf = "_xsrf" in text
        resume_hashes = sorted(set(re.findall(r"resume_hash[\"'=:\s]+([A-Za-z0-9_-]+)", text)))
        return {
            "status": "ok" if has_xsrf else "configured",
            "cookie_file": str(cookie_file),
            "has_cookie_file": True,
            "cookie_count": cookie_count,
            "has_xsrf": has_xsrf,
            "can_load_resumes_page": has_xsrf,
            "resumes_page_status": "configured_not_live_checked" if has_xsrf else "missing_xsrf",
            "can_search_url": True,
            "can_tests": has_xsrf,
            "can_chatik": has_xsrf,
            "resume_hashes": resume_hashes,
            "actions": [] if has_xsrf else ["refresh cookies", "run hh web status"],
        }

    def import_hh_web_cookies(self, path: str | Path) -> dict[str, Any]:
        cookie_file = Path(path)
        if not cookie_file.is_absolute():
            cookie_file = self.root / cookie_file
        if not cookie_file.exists():
            return {
                "status": "error",
                "code": "cookie_file_missing",
                "message": f"Cookie file not found: {cookie_file}",
                "next_actions": ["export cookies.txt from the browser", "retry import-cookies"],
            }
        def save_cookie_path(config: dict[str, Any]) -> None:
            hh_sources = config.setdefault("sources", {}).setdefault("hh", {})
            hh_sources["hh_cookie_file"] = str(cookie_file)

        self._update_config_fresh(save_cookie_path)
        status = self.hh_web_status()
        return {
            "status": "ok",
            "cookie_file": str(cookie_file),
            "cookie_count": status.get("cookie_count", 0),
            "has_xsrf": bool(status.get("has_xsrf")),
            "can_load_resumes_page": bool(status.get("can_load_resumes_page")),
            "resumes_page_status": status.get("resumes_page_status", "not_checked"),
            "resume_hashes": status.get("resume_hashes", []),
            "next_actions": status.get("actions", []),
        }

    def hh_web_search_url(self, search_url: str, *, limit: int = 20) -> dict[str, Any]:
        parsed = urllib.parse.urlparse(search_url)
        params = urllib.parse.parse_qs(parsed.query)
        salary_values = params.get("salary")
        if not parsed.netloc.endswith("hh.ru"):
            return {
                "status": "blocked",
                "code": "unsupported_search_url",
                "message": "Only hh.ru search URLs are supported.",
            }
        salary = int(salary_values[0] or 0) or None if salary_values else None
        return self.search_hh_vacancies(
            text=(params.get("text") or [""])[0] or None,
            area=params.get("area") or None,
            professional_role=params.get("professional_role") or None,
            industry=params.get("industry") or None,
            salary=salary,
            schedule=(params.get("schedule") or [""])[0] or None,
            experience=(params.get("experience") or [""])[0] or None,
            employment=params.get("employment") or None,
            limit=limit,
        )

    def refresh_hh_token(self) -> dict[str, Any]:
        client = self._hh_client()
        token = client.refresh_token()
        access_token = str(token.get("access_token") or "")
        if not access_token:
            raise RuntimeError("HH refresh response did not include access_token")
        hh_config = client.config
        return {
            "status": "ok",
            "access_token": access_token,
            "refresh_token": hh_config.get("refresh_token", ""),
            "access_expires_at": hh_config.get("access_expires_at", ""),
        }

    def update_hh_resumes(self, *, confirm: bool = False) -> dict[str, Any]:
        blocked = require_mutation_confirmation(
            confirm,
            code="resume_mutation_requires_confirmation",
            message="HH resume mutations require explicit confirmation.",
            risk_flags=("resume_mutation", "external_mutating_request"),
        )
        if blocked is not None:
            return blocked
        client = self._hh_client()
        if not client.has_token():
            return {"status": "blocked", "count": 0, "message": "HH access token is required."}
        updated: list[str] = []
        for payload in client.list_resumes():
            resume = _hh_resume_from_payload(payload)
            if resume.id:
                self.storage.upsert_hh_resume(resume)
            if not resume.id or not resume.can_publish_or_update:
                continue
            client.update_resume(resume.id)
            updated.append(resume.id)
        return {"status": "ok", "count": len(updated), "updated": updated}

    def create_hh_resume(
        self,
        payload: dict[str, Any],
        *,
        dry_run: bool = True,
        publish: bool = False,
        validate: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        validation = validate_hh_resume_payload(payload) if validate else None
        payload_to_create = validation["payload"] if validation else payload
        if validation and not validation["valid"]:
            return {"status": "invalid", "payload": payload_to_create, "validation": validation}
        if not dry_run:
            blocked = require_mutation_confirmation(
                confirm,
                code="resume_mutation_requires_confirmation",
                message="HH resume mutations require explicit confirmation.",
                risk_flags=("resume_mutation", "external_mutating_request"),
            )
            if blocked is not None:
                return blocked
        if dry_run:
            result = {"status": "dry_run", "payload": payload_to_create}
            if validation:
                result["validation"] = validation
            return result
        client = self._hh_client()
        if not client.has_token():
            result = {
                "status": "blocked",
                "message": "HH access token is required.",
                "payload": payload_to_create,
            }
            if validation:
                result["validation"] = validation
            return result
        result = client.create_resume(payload_to_create)
        resume_id = str(result.get("id") or "")
        if publish and resume_id:
            result["publish_result"] = client.publish_resume(resume_id)
        if validation:
            result["validation"] = validation
        return result

    def create_hh_resume_from_file(
        self,
        path: str | Path,
        *,
        dry_run: bool = True,
        publish: bool = False,
        validate: bool = True,
        confirm: bool = False,
    ) -> dict[str, Any]:
        payload = load_hh_resume_payload(path)
        return self.create_hh_resume(
            payload,
            dry_run=dry_run,
            publish=publish,
            validate=validate,
            confirm=confirm,
        )

    def preview_hh_resume_template(
        self,
        template: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not template.strip():
            raise ValueError("Resume template is required")
        profile = active_profile(self.config)
        about = self.config.get("about", {})
        render_context = dict(context or {})
        hh_source = (self.config.get("sources") or {}).get("hh") or {}
        if not profile.get("area") and not profile.get("hh_area"):
            render_context.setdefault("area", str(hh_source.get("area") or ""))
        draft = draft_hh_resume_payload_from_template(
            template,
            profile=profile,
            about=about,
            context=render_context,
        )
        draft["status"] = "dry_run"
        return draft

    def build_hh_batch_preset_matrix(self, spec: dict[str, Any]) -> dict[str, Any]:
        return build_resume_batch_preset_matrix(spec)

    def clone_hh_resume(
        self,
        resume_id: str,
        *,
        title: str | None = None,
        dry_run: bool = True,
        publish: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            blocked = require_mutation_confirmation(
                confirm,
                code="resume_mutation_requires_confirmation",
                message="HH resume mutations require explicit confirmation.",
                risk_flags=("resume_mutation", "external_mutating_request"),
            )
            if blocked is not None:
                return blocked
        client = self._hh_client()
        if not client.has_token():
            return {"status": "blocked", "message": "HH access token is required.", "resume_id": resume_id}
        payload = _clone_resume_payload(client.get_resume(resume_id), title=title)
        return self.create_hh_resume(
            payload,
            dry_run=dry_run,
            publish=publish,
            confirm=confirm,
        )

    def sync_hh_negotiations(self, *, status: str = "active") -> dict[str, Any]:
        client = self._hh_client()
        if not client.has_token():
            return {"status": "blocked", "count": 0, "message": "HH access token is required."}
        count = 0
        for payload in client.list_negotiations(status=status):
            self._save_hh_negotiation_payload(payload)
            count += 1
        return {"status": "ok", "count": count}

    def enrich_hh_employers(self, *, limit: int | None = None) -> dict[str, Any]:
        enriched: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        employers = [employer for employer in self.storage.list_hh_employers() if employer.site_url]
        if limit is not None:
            employers = employers[:limit]
        for employer in employers:
            try:
                html = fetch_url(employer.site_url)
                text = clean_text(html)
                emails = _extract_public_emails(html)
                self.storage.save_hh_employer_snapshot(
                    employer_id=employer.id,
                    site_url=employer.site_url,
                    html=html,
                    text=text,
                    emails=emails,
                    status="ok",
                )
                enriched.append(
                    {
                        "employer_id": employer.id,
                        "employer_name": employer.name,
                        "site_url": employer.site_url,
                        "emails": emails,
                    }
                )
            except Exception as exc:
                message = str(exc)
                self.storage.save_hh_employer_snapshot(
                    employer_id=employer.id,
                    site_url=employer.site_url,
                    html="",
                    text="",
                    emails=[],
                    status="error",
                    error=message,
                )
                errors.append(
                    {
                        "employer_id": employer.id,
                        "employer_name": employer.name,
                        "site_url": employer.site_url,
                        "error": message,
                    }
                )
        return {
            "status": "ok" if not errors else "partial",
            "count": len(enriched),
            "employers": enriched,
            "errors": errors,
        }

    def plan_hh_email_followups(
        self,
        *,
        template: str,
        subject: str = "Follow-up",
        limit: int | None = None,
    ) -> dict[str, Any]:
        snapshots = _latest_snapshots_by_employer(self.storage.list_hh_employer_snapshots())
        followups: list[dict[str, Any]] = []
        for employer in self.storage.list_hh_employers():
            snapshot = snapshots.get(employer.id)
            if not snapshot:
                continue
            for email in snapshot.get("emails") or []:
                context = {
                    "employer_id": employer.id,
                    "employer_name": employer.name,
                    "site_url": employer.site_url or snapshot.get("site_url") or "",
                    "email": email,
                }
                body = _format_followup_template(template, context)
                if not body:
                    continue
                followups.append(
                    {
                        "employer_id": employer.id,
                        "employer_name": employer.name,
                        "to": str(email),
                        "subject": subject,
                        "body": body,
                    }
                )
                if limit is not None and len(followups) >= limit:
                    return {"status": "planned", "count": len(followups), "followups": followups}
        return {"status": "planned", "count": len(followups), "followups": followups}

    def send_hh_email_followups(
        self,
        *,
        template: str,
        subject: str = "Follow-up",
        limit: int | None = None,
        confirm: bool = False,
        sender: Any | None = None,
    ) -> dict[str, Any]:
        plan = self.plan_hh_email_followups(template=template, subject=subject, limit=limit)
        if not confirm:
            return {
                "status": "blocked",
                "message": "Explicit confirm=True is required before sending email follow-ups.",
                "plan": plan,
            }
        send = sender or (lambda message: send_email_message(message, self.config.get("smtp", {})))
        sent: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for followup in plan["followups"]:
            try:
                result = send(followup)
                sent.append({**followup, "result": result})
                self.storage.save_hh_email_followup(
                    employer_id=str(followup["employer_id"]),
                    employer_name=str(followup["employer_name"]),
                    to_email=str(followup["to"]),
                    subject=str(followup["subject"]),
                    body=str(followup["body"]),
                    status="sent",
                    raw_result=result,
                )
            except Exception as exc:
                error = {**followup, "error": str(exc)}
                errors.append(error)
                self.storage.save_hh_email_followup(
                    employer_id=str(followup["employer_id"]),
                    employer_name=str(followup["employer_name"]),
                    to_email=str(followup["to"]),
                    subject=str(followup["subject"]),
                    body=str(followup["body"]),
                    status="error",
                    raw_result=error,
                )
        return {
            "status": "sent" if not errors else "partial",
            "count": len(sent),
            "sent": sent,
            "errors": errors,
        }

    def reply_hh_employers(
        self,
        *,
        template: str,
        status: str = "active",
        limit: int | None = None,
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict[str, Any]:
        client = self._hh_client()
        if not client.has_token():
            return {"status": "blocked", "count": 0, "message": "HH access token is required."}
        if not template.strip():
            raise ValueError("Reply template is required")
        if not dry_run and not confirm:
            return {
                "status": "blocked",
                "count": 0,
                "message": "Explicit confirm=True is required before sending employer replies.",
            }

        replies: list[dict[str, Any]] = []
        for payload in client.list_negotiations(status=status):
            self._save_hh_negotiation_payload(payload)
            context = _negotiation_reply_context(payload)
            negotiation_id = context["negotiation_id"]
            if not negotiation_id:
                continue
            messages = client.list_negotiation_messages(negotiation_id)
            if not _last_message_from_employer(messages) and payload.get("viewed_by_opponent", True):
                continue
            message = _format_reply_template(template, context)
            if not message:
                continue
            replies.append(
                {
                    "negotiation_id": negotiation_id,
                    "chat_id": context["chat_id"],
                    "vacancy_id": context["vacancy_id"],
                    "vacancy_name": context["vacancy_name"],
                    "employer_id": context["employer_id"],
                    "employer_name": context["employer_name"],
                    "resume_id": context["resume_id"],
                    "message": message,
                }
            )
            if limit is not None and len(replies) >= limit:
                break

        if dry_run:
            return {"status": "planned", "count": len(replies), "replies": replies}

        sent: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for reply in replies:
            try:
                result = client.send_negotiation_message(
                    str(reply["negotiation_id"]),
                    str(reply["message"]),
                    chat_id=str(reply.get("chat_id") or "") or None,
                )
                sent.append({**reply, "result": result})
            except Exception as exc:
                errors.append({**reply, "error": str(exc)})
        status_value = "sent" if not errors else "partial"
        return {
            "status": status_value,
            "count": len(sent),
            "sent": sent,
            "errors": errors,
        }

    def plan_hh_reply(
        self,
        *,
        negotiation_id: str,
        template: str = "",
        status: str = "active",
        delay_minutes: int = 0,
    ) -> dict[str, Any]:
        negotiation_id = negotiation_id.strip()
        if not negotiation_id:
            raise ValueError("Negotiation id is required")
        client = self._hh_client()
        if not client.has_token():
            return {"status": "blocked", "message": "HH access token is required."}
        negotiation: dict[str, Any] | None = None
        for payload in client.list_negotiations(status=status):
            self._save_hh_negotiation_payload(payload)
            if str(payload.get("id") or "") == negotiation_id:
                negotiation = payload
                break
        if negotiation is None:
            return {
                "status": "blocked",
                "code": "negotiation_not_found",
                "message": f"Negotiation {negotiation_id} was not found in status={status}.",
            }
        persona = persona_from_profile(active_profile(self.config), self.config.get("about", {}))
        service = HHChatAgentService(storage=self.storage, client=client)
        result = service.plan_reply(
            negotiation,
            persona=persona.to_dict(),
            template=template,
            delay_minutes=delay_minutes,
        )
        if result.get("outbox_id"):
            result["plan_id"] = result["outbox_id"]
            result["requires_confirmation"] = True
            result.setdefault("risk_flags", result.get("reply", {}).get("risk_flags", []))
            result["next_actions"] = [
                f"work-hunter hh reply confirm --plan-id {result['outbox_id']} --confirm"
            ]
        return result

    def confirm_hh_reply(self, plan_id: int, *, confirm: bool = False) -> dict[str, Any]:
        item = self.storage.get_hh_agent_outbox(plan_id)
        if item is None or item.channel != "hh_reply":
            return {"status": "blocked", "code": "reply_plan_not_found", "plan_id": plan_id}
        if item.status == "sent":
            return {"status": "sent", "plan_id": plan_id, "result": item.payload.get("send_result") or {}}
        if item.status == "pending_approval":
            return {
                "status": "blocked",
                "code": "approval_required",
                "plan_id": plan_id,
                "pending_message_id": item.payload.get("pending_message_id"),
            }
        if not confirm:
            return {
                "status": "blocked",
                "code": "confirm_required",
                "message": "Explicit --confirm is required before sending HH chat replies.",
                "plan_id": plan_id,
                "requires_confirmation": True,
                "preview": (item.payload.get("reply") or {}).get("message", ""),
            }
        reply = item.payload.get("reply") or {}
        client = self._hh_client()
        if not client.has_token():
            return {"status": "blocked", "message": "HH access token is required.", "plan_id": plan_id}
        try:
            result = client.send_negotiation_message(
                str(reply.get("negotiation_id") or item.target),
                str(reply.get("message") or ""),
                chat_id=str(reply.get("chat_id") or "") or None,
            )
        except Exception as exc:
            payload = {**item.payload, "error": str(exc)}
            self.storage.update_hh_agent_outbox(item.id, status="error", payload=payload)
            return {"status": "error", "plan_id": plan_id, "error": str(exc)}
        payload = {**item.payload, "send_result": result}
        self.storage.update_hh_agent_outbox(item.id, status="sent", payload=payload)
        return {"status": "sent", "plan_id": plan_id, "result": mask_secrets(result)}

    def plan_hh_negotiation_cleanup(
        self,
        *,
        status: str = "active",
        max_age_days: int | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        client = self._hh_client()
        if not client.has_token():
            return {"status": "blocked", "count": 0, "message": "HH access token is required."}
        now_dt = _parse_hh_datetime(now or "") or datetime.now().astimezone()
        actions: list[dict[str, Any]] = []
        for payload in client.list_negotiations(status=status):
            self._save_hh_negotiation_payload(payload)
            state = _text_id(payload.get("state"))
            updated_at = str(payload.get("updated_at") or "")
            updated_dt = _parse_hh_datetime(updated_at)
            vacancy = payload.get("vacancy") or {}
            employer = payload.get("employer") or vacancy.get("employer") or {}
            reason = ""
            if state == "discard":
                reason = "discarded"
            elif max_age_days is not None and updated_dt is not None:
                if (now_dt - updated_dt).days > max_age_days:
                    reason = "stale"
            if not reason:
                continue
            action = {
                "action": "cancel",
                "reason": reason,
                "negotiation_id": str(payload.get("id") or ""),
                "state": state,
                "updated_at": updated_at,
                "vacancy_id": _text_id(vacancy),
                "vacancy_name": str(vacancy.get("name") or ""),
                "employer_id": _text_id(employer),
                "employer_name": str(employer.get("name") or ""),
                "chat_id": str(payload.get("chat_id") or ""),
            }
            actions.append(action)
            self.storage.save_hh_cleanup_event(
                negotiation_id=str(action["negotiation_id"]),
                action=str(action["action"]),
                reason=reason,
                status="planned",
                vacancy_id=str(action["vacancy_id"]),
                employer_id=str(action["employer_id"]),
                raw_result={"source_status": status, "updated_at": updated_at},
            )
        return {"status": "planned", "count": len(actions), "actions": actions}

    def confirm_hh_negotiation_cleanup(
        self,
        *,
        status: str = "active",
        max_age_days: int | None = None,
        blacklist: bool = False,
        decline_message: str = "",
        now: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        plan = self.plan_hh_negotiation_cleanup(status=status, max_age_days=max_age_days, now=now)
        if plan.get("status") != "planned":
            return plan
        if not confirm:
            return {
                "status": "blocked",
                "message": "Explicit confirmation is required before cleaning HH negotiations.",
                "plan": plan,
            }
        client = self._hh_client()
        completed: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for action in plan["actions"]:
            try:
                cancel_result = client.cancel_negotiation(
                    str(action["negotiation_id"]),
                    message=decline_message,
                )
                result = {**action, "cancel_result": cancel_result}
                employer_id = str(action.get("employer_id") or "")
                if blacklist and employer_id:
                    result["blacklist_result"] = client.blacklist_employer(employer_id)
                completed.append(result)
                self.storage.save_hh_cleanup_event(
                    negotiation_id=str(action["negotiation_id"]),
                    action=str(action["action"]),
                    reason=str(action["reason"]),
                    status="completed",
                    vacancy_id=str(action.get("vacancy_id") or ""),
                    employer_id=employer_id,
                    raw_result=result,
                )
            except Exception as exc:
                error = {**action, "error": str(exc)}
                errors.append(error)
                self.storage.save_hh_cleanup_event(
                    negotiation_id=str(action["negotiation_id"]),
                    action=str(action["action"]),
                    reason=str(action["reason"]),
                    status="error",
                    vacancy_id=str(action.get("vacancy_id") or ""),
                    employer_id=str(action.get("employer_id") or ""),
                    raw_result=error,
                )
        return {
            "status": "completed" if not errors else "partial",
            "count": len(completed),
            "completed": completed,
            "errors": errors,
        }

    def _save_hh_negotiation_payload(self, payload: dict[str, Any]) -> None:
        vacancy = payload.get("vacancy") or {}
        employer = payload.get("employer") or vacancy.get("employer") or {}
        resume = payload.get("resume") or {}
        vacancy_id = _text_id(vacancy)
        employer_id = _text_id(employer)
        self.storage.upsert_hh_negotiation(
            HHNegotiation(
                id=str(payload.get("id") or ""),
                state=_text_id(payload.get("state")),
                vacancy_id=vacancy_id,
                employer_id=employer_id,
                chat_id=str(payload.get("chat_id") or ""),
                resume_id=_text_id(resume),
            )
        )
        if employer_id:
            self.storage.upsert_hh_employer(
                HHEmployer(
                    id=employer_id,
                    name=str(employer.get("name") or ""),
                    type=str(employer.get("type") or ""),
                    description=str(employer.get("description") or ""),
                    site_url=str(employer.get("site_url") or ""),
                    alternate_url=str(employer.get("alternate_url") or ""),
                )
            )
        contacts = vacancy.get("contacts") or {}
        if contacts:
            self.storage.upsert_hh_contact(
                HHContact(
                    vacancy_id=vacancy_id,
                    employer_id=employer_id,
                    employer_name=str(employer.get("name") or ""),
                    name=str(contacts.get("name") or ""),
                    email=str(contacts.get("email") or ""),
                    phone_numbers=_phone_numbers(contacts),
                )
            )

    def clear_hh_skipped_vacancies(self) -> dict[str, Any]:
        return {"status": "ok", "count": self.storage.clear_hh_skipped_vacancies()}

    def hh_call_api(
        self,
        method: str,
        path: str,
        data: Any = None,
        *,
        confirm: bool = False,
    ) -> dict[str, Any]:
        request = _normalize_hh_api_call_request(method=method, path=path, body=data)
        safe_input = {
            "method": request["method"],
            "path": request["path"],
            "params": mask_secrets(request.get("params") or {}),
            "body": mask_secrets(request.get("body") or {}),
            "confirmed_by_user": is_literal_confirmation(confirm),
        }
        if request["method"] not in READ_ONLY_HTTP_METHODS:
            blocked = require_mutation_confirmation(
                confirm,
                code="mutation_requires_confirm",
                message="HH API mutating calls require --confirm.",
                risk_flags=("external_mutating_request",),
                context=safe_input,
            )
            if blocked is not None:
                return blocked
        client = self._hh_client()
        if not client.has_token():
            return {"status": "blocked", "message": "HH access token is required.", **safe_input}
        run_id = self.storage.start_hh_agent_mcp_run("hh_api_call", safe_input)
        try:
            result = client.request_json(
                str(request["method"]),
                str(request["path"]),
                data=request.get("body"),
                params=dict(request.get("params") or {}),
            )
        except Exception as exc:
            output = {**_hh_error_payload(exc), "operation_id": run_id, **safe_input}
            self.storage.finish_hh_agent_mcp_run(run_id, status="error", output=output, error=str(exc))
            return output
        output = {
            "status": "ok",
            "operation_id": run_id,
            **safe_input,
            "result": mask_secrets(result),
        }
        self.storage.finish_hh_agent_mcp_run(run_id, status="ok", output=output)
        return output

    def hh_operator_summary(self) -> dict[str, Any]:
        resumes = self.storage.list_hh_resumes()
        negotiations = self.storage.list_hh_negotiations()
        contacts = self.storage.list_hh_contacts()
        skipped = self.storage.list_hh_skipped_vacancies()
        skipped_by_reason: dict[str, int] = {}
        for item in skipped:
            skipped_by_reason[item.reason] = skipped_by_reason.get(item.reason, 0) + 1
        recommendations: list[str] = []
        if skipped_by_reason:
            top_reason = max(skipped_by_reason, key=lambda reason: skipped_by_reason[reason])
            recommendations.append(
                f"Review skipped reason '{top_reason}' before the next campaign."
            )
        if not resumes:
            recommendations.append("Sync HH resumes before planning applications.")
        if not negotiations:
            recommendations.append("Sync HH negotiations to keep the employer inbox current.")
        return {
            "resumes": {
                "total": len(resumes),
                "publishable": sum(1 for resume in resumes if resume.can_publish_or_update),
                "new_views": sum(resume.new_views for resume in resumes),
            },
            "negotiations": {"total": len(negotiations)},
            "contacts": {"total": len(contacts)},
            "skipped": {
                "total": len(skipped),
                "by_reason": dict(sorted(skipped_by_reason.items())),
            },
            "recommendations": recommendations,
        }

    def scan_hh_agent_events(
        self,
        *,
        messages: list[dict[str, Any]] | None = None,
        status: str = "active",
        now: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        source_messages = messages if messages is not None else self._load_hh_negotiation_messages(status=status, limit=limit)
        detected = detect_hh_agent_items(source_messages, now=now)
        saved_events: list[dict[str, Any]] = []
        saved_tasks: list[dict[str, Any]] = []
        for event in detected.events:
            event.id = self.storage.upsert_hh_agent_event(event)
            saved_events.append(event.to_dict())
        for task in detected.tasks:
            task.id = self.storage.upsert_hh_agent_task(task)
            saved_tasks.append(task.to_dict())
        return {
            "status": "ok",
            "counts": {"events": len(saved_events), "tasks": len(saved_tasks)},
            "events": saved_events,
            "tasks": saved_tasks,
        }

    def _load_hh_negotiation_messages(
        self,
        *,
        status: str = "active",
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        client = self._hh_client()
        if not client.has_token():
            raise RuntimeError("HH access token is required to scan negotiation messages.")
        messages: list[dict[str, Any]] = []
        for payload in client.list_negotiations(status=status):
            self._save_hh_negotiation_payload(payload)
            negotiation_id = str(payload.get("id") or "")
            if not negotiation_id:
                continue
            for message in client.list_negotiation_messages(negotiation_id):
                enriched = dict(message)
                enriched.setdefault("negotiation_id", negotiation_id)
                enriched.setdefault("vacancy", payload.get("vacancy") or {})
                enriched.setdefault("employer", payload.get("employer") or (payload.get("vacancy") or {}).get("employer") or {})
                messages.append(enriched)
                if limit is not None and len(messages) >= limit:
                    return messages
        return messages

    def list_hh_agent_events(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.storage.list_hh_agent_events(status=status, limit=limit)]

    def list_hh_agent_tasks(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.storage.list_hh_agent_tasks(status=status, limit=limit)]

    def hh_agent_agenda(self, *, limit: int = 10) -> dict[str, Any]:
        limit = max(1, min(100, int(limit)))
        events = self.storage.list_hh_agent_events()
        tasks = self.storage.list_hh_agent_tasks()
        return {
            "events": {
                "total": len(events),
                "recent": [item.to_dict() for item in events[:limit]],
            },
            "tasks": {
                "total": len(tasks),
                "recent": [item.to_dict() for item in tasks[:limit]],
            },
            "notification": agenda_notification_event(events=events, tasks=tasks).to_dict(),
        }

    def export_hh_agent_calendar_ics(self) -> str:
        return export_hh_agent_calendar_ics(
            self.storage.list_hh_agent_events(),
            self.storage.list_hh_agent_tasks(),
        )

    def export_hh_agent_agenda_markdown(self) -> str:
        return export_hh_agent_agenda_markdown(
            self.storage.list_hh_agent_events(),
            self.storage.list_hh_agent_tasks(),
        )

    def pause_hh_agent(self, *, reason: str = "manual") -> dict[str, Any]:
        paused_at = datetime.now().astimezone().replace(microsecond=0).isoformat()

        def pause(config: dict[str, Any]) -> None:
            agent_config = dict(config.get("hh_agent") or {})
            agent_config["paused"] = True
            agent_config["pause_reason"] = reason
            agent_config["paused_at"] = paused_at
            config["hh_agent"] = agent_config

        self._update_config_fresh(pause)
        return {"status": "paused", "paused": True, "reason": reason}

    def resume_hh_agent(self, *, reason: str = "manual") -> dict[str, Any]:
        resumed_at = datetime.now().astimezone().replace(microsecond=0).isoformat()

        def resume(config: dict[str, Any]) -> None:
            agent_config = dict(config.get("hh_agent") or {})
            agent_config["paused"] = False
            agent_config["resume_reason"] = reason
            agent_config["resumed_at"] = resumed_at
            config["hh_agent"] = agent_config

        self._update_config_fresh(resume)
        return {"status": "resumed", "paused": False, "reason": reason}

    def hh_agent_preflight(self, *, live_auth: bool = False) -> dict[str, Any]:
        hh_config = self.hh_config()
        agent_config = self.config.get("hh_agent") or {}
        has_access_token = bool(str(hh_config.get("access_token") or ""))
        has_refresh_token = bool(str(hh_config.get("refresh_token") or ""))
        has_cookie_file = bool(str(hh_config.get("hh_cookie_file") or ""))
        has_android_user_agent = bool(str(hh_config.get("android_user_agent") or ""))
        pending = self.storage.list_hh_pending_messages(status="pending")
        actions: list[str] = []
        if not has_access_token:
            actions.append("Set sources.hh.access_token or configure a refresh flow.")
        if not has_refresh_token:
            actions.append("Add sources.hh.refresh_token for automatic token refresh.")
        if not self.storage.list_hh_resumes():
            actions.append("Sync HH resumes before running application flows.")
        if pending:
            actions.append("Review pending approvals before the next send/apply operation.")
        auth: dict[str, Any]
        if live_auth:
            auth = self.hh_auth_status()
        else:
            auth = {
                "status": "token_configured_unverified" if has_access_token else "missing_access_token",
                "authorized": False,
                "configured": has_access_token,
                "verified": False,
                "refresh_ready": has_refresh_token,
                "client_credentials_configured": bool(
                    str(hh_config.get("client_id") or "")
                    and str(hh_config.get("client_secret") or "")
                ),
                "access_expires_at": str(hh_config.get("access_expires_at") or ""),
                "actions": actions,
            }
        auth_ready = bool(auth.get("authorized")) if live_auth else has_access_token
        return {
            "status": "ok" if auth_ready else "blocked",
            "auth": auth,
            "profile": {
                "active": str(self.config.get("profile") or "default"),
                "hh_account": str(self.config.get("hh_account_profile") or "default"),
            },
            "agent": {
                "paused": bool(agent_config.get("paused", False)),
                "pause_reason": str(agent_config.get("pause_reason") or ""),
            },
            "capabilities": {
                "api_token": has_access_token,
                "refresh_token": has_refresh_token,
                "cookie_file": has_cookie_file,
                "android_user_agent": has_android_user_agent,
                "agent_audit": True,
                "approval_queue": True,
                "events_tasks": True,
                "chat_outbox": True,
                "webhook_delivery": True,
            },
            "counts": {
                "pending_approvals": len(pending),
                "mcp_runs": len(self.storage.list_hh_agent_mcp_runs()),
                "operation_logs": len(self.storage.list_hh_operation_logs()),
                "ai_decisions": len(self.storage.list_hh_ai_decisions()),
                "agent_events": len(self.storage.list_hh_agent_events()),
                "agent_tasks": len(self.storage.list_hh_agent_tasks()),
                "outbox": len(self.storage.list_hh_agent_outbox()),
                "webhooks": len(self.storage.list_hh_agent_webhooks()),
            },
            "actions": actions,
        }

    def hh_agent_digest(self, *, limit: int = 10) -> dict[str, Any]:
        limit = max(1, min(100, int(limit)))
        runs = self.storage.list_hh_agent_mcp_runs()
        pending = self.storage.list_hh_pending_messages()
        decisions = self.storage.list_hh_ai_decisions()
        operation_logs = self.storage.list_hh_operation_logs()
        outbox = self.storage.list_hh_agent_outbox()
        webhooks = self.storage.list_hh_agent_webhooks()
        by_status: dict[str, int] = {}
        for message in pending:
            by_status[message.status] = by_status.get(message.status, 0) + 1
        outbox_by_status: dict[str, int] = {}
        for outbox_item in outbox:
            outbox_by_status[outbox_item.status] = outbox_by_status.get(outbox_item.status, 0) + 1
        webhooks_by_status: dict[str, int] = {}
        for webhook in webhooks:
            webhooks_by_status[webhook.status] = webhooks_by_status.get(webhook.status, 0) + 1
        return {
            "status": "ok",
            "summary": self.hh_operator_summary(),
            "approvals": {
                "total": len(pending),
                "by_status": dict(sorted(by_status.items())),
                "recent": [item.to_dict() for item in pending[-limit:]][::-1],
            },
            "runs": {
                "total": len(runs),
                "recent": [item.to_dict() for item in runs[-limit:]][::-1],
            },
            "outbox": {
                "total": len(outbox),
                "by_status": dict(sorted(outbox_by_status.items())),
                "recent": [item.to_dict() for item in outbox[-limit:]][::-1],
            },
            "webhooks": {
                "total": len(webhooks),
                "by_status": dict(sorted(webhooks_by_status.items())),
                "recent": [item.to_dict() for item in webhooks[-limit:]][::-1],
            },
            "agenda": self.hh_agent_agenda(limit=limit),
            "ai_decisions": {
                "total": len(decisions),
                "recent": [item.to_dict() for item in decisions[-limit:]][::-1],
            },
            "operation_logs": {
                "total": len(operation_logs),
                "recent": [item.to_dict() for item in operation_logs[-limit:]][::-1],
            },
        }

    def list_hh_agent_operations(self, *, limit: int = 50) -> dict[str, Any]:
        limit = max(1, min(500, int(limit)))
        runs = self.storage.list_hh_agent_mcp_runs()
        logs = self.storage.list_hh_operation_logs()
        return {
            "runs": [item.to_dict() for item in runs[-limit:]][::-1],
            "logs": [item.to_dict() for item in logs[-limit:]][::-1],
        }

    def hh_agent_operation_status(self, operation_id: int) -> dict[str, Any]:
        run = self.storage.get_hh_agent_mcp_run(operation_id)
        if run is None:
            raise ValueError(f"HH agent operation {operation_id} not found")
        return {
            "operation": run.to_dict(),
            "logs": [item.to_dict() for item in self.storage.list_hh_operation_logs(operation_id)],
        }

    def cancel_hh_agent_operation(self, operation_id: int, *, reason: str = "cancelled") -> dict[str, Any]:
        run = self.storage.get_hh_agent_mcp_run(operation_id)
        if run is None:
            raise ValueError(f"HH agent operation {operation_id} not found")
        if run.finished_at and run.status != "running":
            return {
                "status": "already_finished",
                "operation": run.to_dict(),
                "message": "Operation already has a final status.",
            }
        payload = {"cancelled": True, "reason": reason}
        self.storage.finish_hh_agent_mcp_run(operation_id, status="cancelled", output=payload)
        self.storage.append_hh_operation_log(
            operation_id=operation_id,
            level="warning",
            message="HH agent operation cancellation requested",
            payload=payload,
        )
        return self.hh_agent_operation_status(operation_id)

    def run_hh_research_operation(
        self,
        *,
        text: str = "",
        limit: int = 20,
        resume_id: str = "",
        run_id: int | None = None,
        plan_apply: bool = False,
        confirm_apply: bool = False,
        min_score: int | None = None,
    ) -> dict[str, Any]:
        if confirm_apply:
            return {
                "status": "blocked",
                "reason": "real_apply_blocked",
                "message": "Research operations only produce dry-run apply plans.",
                "counts": {"planned": 0, "blocked": 1, "applied": 0},
                "items": [],
            }
        limit = max(1, min(int(limit or 20), 100))
        client = self._hh_research_client()
        if hasattr(client, "has_token") and not client.has_token():
            return {
                "status": "blocked",
                "reason": "hh_access_token_required",
                "message": "HH access token is required for live HH research.",
                "counts": {"planned": 0, "blocked": 0, "applied": 0},
                "items": [],
            }
        service = self._build_hh_research_service(client=client, min_score=min_score)
        search_params = self._hh_research_search_params(text=text, limit=limit)
        results = service.search_vacancies(search_params)
        items: list[dict[str, Any]] = []
        counts: dict[str, int] = {
            "analyzed": 0,
            "planned": 0,
            "blocked": 0,
            "applied": 0,
            "errors": 0,
        }
        for search_result in results[:limit]:
            item = search_result.to_dict()
            try:
                vacancy = service.get_vacancy_details(search_result.vacancy_id)
                analysis = service.analyze_vacancy(vacancy, resume_id=resume_id, run_id=run_id)
                counts["analyzed"] += 1
                item.update(
                    {
                        "score": analysis.score,
                        "recommended_action": analysis.recommended_action,
                        "reasons": analysis.reasons,
                        "risk_flags": analysis.risk_flags,
                    }
                )
                if plan_apply:
                    attempt = service.plan_apply_vacancy(
                        vacancy,
                        resume_id=resume_id,
                        run_id=run_id,
                        analysis=analysis,
                    )
                    item.update(
                        {
                            "attempt_status": attempt.status,
                            "attempt_reason": attempt.reason,
                        }
                    )
                    counts[attempt.status] = counts.get(attempt.status, 0) + 1
                else:
                    item["attempt_status"] = "not_planned"
            except Exception as exc:
                counts["errors"] += 1
                item.update(
                    {
                        "status": "error",
                        "error": str(exc),
                        "score": 0,
                        "recommended_action": "ask",
                        "reasons": [],
                        "risk_flags": ["research_error"],
                        "attempt_status": "error",
                    }
                )
            items.append(item)
        status = "ok" if counts["errors"] == 0 else "partial"
        return {
            "status": status,
            "text": text,
            "limit": limit,
            "resume_id": resume_id,
            "counts": counts,
            "items": items,
        }

    def _hh_research_client(self) -> Any:
        return self._hh_client()

    def _build_hh_research_service(
        self,
        *,
        client: Any,
        min_score: int | None = None,
    ) -> HHVacancyResearchService:
        profile = active_profile(self.config)
        about = self.config.get("about", {})
        return HHVacancyResearchService(
            client=client,
            storage=self.storage,
            ai_config=self.config.get("ai", {}),
            policy=self._hh_research_policy(min_score=min_score),
            persona=persona_from_profile(profile, about).to_dict(),
        )

    def _hh_research_policy(self, *, min_score: int | None = None) -> VacancyPolicy:
        profile = active_profile(self.config)
        research_config = self.config.get("research") or {}
        policy_config = dict((self.config.get("hh_agent") or {}).get("policy") or {})
        policy_config.setdefault("must_have", profile.get("must_have_skills") or [])
        policy_config.setdefault("nice_to_have", profile.get("nice_to_have_skills") or [])
        policy_config.setdefault("excluded_keywords", profile.get("stop_words") or [])
        policy_config["min_score"] = int(
            min_score
            if min_score is not None
            else policy_config.get("min_score", research_config.get("min_score", 0))
            or 0
        )
        return VacancyPolicy.from_mapping(policy_config)

    def _hh_research_search_params(self, *, text: str, limit: int) -> dict[str, Any]:
        hh_config = self.hh_config()
        params: dict[str, Any] = {
            "text": text,
            "per_page": limit,
            "page": 0,
        }
        if hh_config.get("area"):
            params["area"] = hh_config.get("area")
        return params

    def run_hh_agent_operation(self, operation: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        operation = operation.strip().replace("_", "-")
        allowed = {
            "digest",
            "preflight",
            "sync-resumes",
            "update-resumes",
            "sync-negotiations",
            "scan-events",
            "refresh-token",
            "clear-skipped",
            "research-vacancies",
            "research-and-apply",
        }
        if operation not in allowed:
            raise ValueError(f"Unsupported HH agent operation: {operation}")
        if bool((self.config.get("hh_agent") or {}).get("paused", False)) and operation not in {"digest", "preflight"}:
            return {
                "status": "paused",
                "operation": operation,
                "message": "HH agent is paused. Run resume before active operations.",
            }
        run_id = self.storage.start_hh_agent_mcp_run(f"hh_agent:{operation}", params)
        self.storage.append_hh_operation_log(
            operation_id=run_id,
            level="info",
            message=f"Started HH agent operation {operation}",
            payload=params,
        )
        try:
            if operation == "digest":
                result = self.hh_agent_digest(limit=int(params.get("limit", 10)))
            elif operation == "preflight":
                result = self.hh_agent_preflight(live_auth=bool(params.get("live_auth", False)))
            elif operation == "sync-resumes":
                result = self.sync_hh_resumes()
            elif operation == "update-resumes":
                result = self.update_hh_resumes(
                    confirm=is_literal_confirmation(params.get("confirm")),
                )
            elif operation == "sync-negotiations":
                result = self.sync_hh_negotiations(status=str(params.get("status") or "active"))
            elif operation == "scan-events":
                limit_value = params.get("limit")
                scan_limit = None if limit_value is None else int(limit_value)
                result = self.scan_hh_agent_events(
                    status=str(params.get("status") or "active"),
                    limit=scan_limit,
                )
            elif operation == "refresh-token":
                result = refresh_result = self.refresh_hh_token()
                result = {
                    key: value
                    for key, value in refresh_result.items()
                    if key not in {"access_token", "refresh_token"}
                }
            elif operation == "research-vacancies":
                result = self.run_hh_research_operation(
                    text=str(params.get("text") or ""),
                    limit=int(params.get("limit") or 20),
                    resume_id=str(params.get("resume_id") or ""),
                    run_id=run_id,
                    plan_apply=False,
                    confirm_apply=is_literal_confirmation(params.get("confirm_apply")),
                    min_score=_optional_int(params.get("min_score")),
                )
            elif operation == "research-and-apply":
                result = self.run_hh_research_operation(
                    text=str(params.get("text") or ""),
                    limit=int(params.get("limit") or 20),
                    resume_id=str(params.get("resume_id") or ""),
                    run_id=run_id,
                    plan_apply=True,
                    confirm_apply=is_literal_confirmation(params.get("confirm_apply")),
                    min_score=_optional_int(params.get("min_score")),
                )
            else:
                result = self.clear_hh_skipped_vacancies()
        except Exception as exc:
            self.storage.finish_hh_agent_mcp_run(run_id, status="error", output={}, error=str(exc))
            self.storage.append_hh_operation_log(
                operation_id=run_id,
                level="error",
                message=f"HH agent operation {operation} failed",
                payload={"error": str(exc)},
            )
            raise
        self.storage.finish_hh_agent_mcp_run(run_id, status=str(result.get("status") or "ok"), output=result)
        self.storage.append_hh_operation_log(
            operation_id=run_id,
            level="info",
            message=f"Finished HH agent operation {operation}",
            payload={"status": result.get("status") or "ok"},
        )
        return {"operation_id": run_id, "operation": operation, "result": result}

    def list_hh_approvals(self, *, status: str | None = None) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.storage.list_hh_pending_messages(status=status)]

    def approve_hh_approval(self, message_id: int, *, reason: str = "approved") -> dict[str, Any]:
        ApprovalQueue(self.storage).approve(message_id, reason=reason)
        return self._hh_approval_payload(message_id)

    def reject_hh_approval(self, message_id: int, *, reason: str = "rejected") -> dict[str, Any]:
        ApprovalQueue(self.storage).reject(message_id, reason=reason)
        return self._hh_approval_payload(message_id)

    def modify_hh_approval(
        self,
        message_id: int,
        *,
        instruction: str,
        payload_patch: dict[str, Any] | None = None,
        regenerate_hook: Any | None = None,
    ) -> dict[str, Any]:
        ApprovalQueue(self.storage).modify(
            message_id,
            instruction=instruction,
            payload_patch=payload_patch,
            regenerate=regenerate_hook,
        )
        return self._hh_approval_payload(message_id)

    def flag_hh_approval(self, message_id: int, *, reason: str = "flagged") -> dict[str, Any]:
        ApprovalQueue(self.storage).flag(message_id, reason=reason)
        return self._hh_approval_payload(message_id)

    def list_hh_letter_templates(self) -> list[dict[str, Any]]:
        return self.storage.list_hh_letter_templates()

    def save_hh_letter_template(self, *, name: str, body: str) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("Template name is required")
        return self.storage.upsert_hh_letter_template(name=name, body=body)

    def delete_hh_letter_template(self, name: str) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("Template name is required")
        return {"status": "ok", "deleted": self.storage.delete_hh_letter_template(name)}

    def list_hh_employer_blacklist(self) -> list[dict[str, Any]]:
        return self.storage.list_hh_employer_blacklist()

    def save_hh_employer_blacklist(
        self,
        *,
        employer_id: str,
        employer_name: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        employer_id = employer_id.strip()
        employer_name = employer_name.strip()
        if not employer_id and employer_name:
            employer_id = f"name:{employer_name.lower()}"
        if not employer_id:
            raise ValueError("Employer id or name is required")
        return self.storage.upsert_hh_employer_blacklist(
            employer_id=employer_id,
            employer_name=employer_name,
            reason=reason,
        )

    def delete_hh_employer_blacklist(self, employer_id: str) -> dict[str, Any]:
        employer_id = employer_id.strip()
        if not employer_id:
            raise ValueError("Employer id is required")
        return {"status": "ok", "deleted": self.storage.delete_hh_employer_blacklist(employer_id)}

    def list_hh_api_lab_quick_calls(self) -> list[dict[str, Any]]:
        profile = active_profile(self.config)
        query = str((profile.get("queries") or profile.get("desired_roles") or [""])[0] or "")
        hh_config = self.hh_config()
        return [
            {
                "id": "me",
                "label": "/me",
                "method": "GET",
                "path": "/me",
                "params": {},
                "body": None,
            },
            {
                "id": "resumes",
                "label": "/resumes/mine",
                "method": "GET",
                "path": "/resumes/mine",
                "params": {},
                "body": None,
            },
            {
                "id": "negotiations",
                "label": "/negotiations",
                "method": "GET",
                "path": "/negotiations",
                "params": {"status": "active"},
                "body": None,
            },
            {
                "id": "vacancies",
                "label": "/vacancies",
                "method": "GET",
                "path": "/vacancies",
                "params": {
                    "text": query,
                    "area": hh_config.get("area", 113),
                    "per_page": min(int(hh_config.get("per_page", 25) or 25), 20),
                },
                "body": None,
            },
        ]

    def hh_api_lab_call(
        self,
        *,
        method: str = "GET",
        path: str = "/me",
        params: Any = None,
        body: Any = None,
        quick: str = "",
        confirm: bool = False,
    ) -> dict[str, Any]:
        request = self._hh_api_lab_quick_request(quick) if quick else {
            "method": method,
            "path": path,
            "params": params,
            "body": body,
        }
        if not quick:
            request = _normalize_hh_api_lab_request(**request)
        elif params or body:
            request = _normalize_hh_api_lab_request(
                method=str(request["method"]),
                path=str(request["path"]),
                params={**dict(request.get("params") or {}), **_normalize_hh_api_lab_params(params)},
                body=body if body is not None else request.get("body"),
            )
        safe_input = {
            "method": request["method"],
            "path": request["path"],
            "params": mask_secrets(request.get("params") or {}),
            "body": mask_secrets(request.get("body") or {}),
            "quick": quick,
            "confirmed_by_user": is_literal_confirmation(confirm),
        }
        if request["method"] not in READ_ONLY_HTTP_METHODS:
            blocked = require_mutation_confirmation(
                confirm,
                code="hh_api_lab_mutation_requires_confirmation",
                message="Mutating HH API Lab calls require explicit confirmation.",
                risk_flags=("hh_api_mutation", "external_mutating_request"),
                context=safe_input,
            )
            if blocked is not None:
                return blocked
        client = self._hh_client()
        if not client.has_token():
            return {
                "status": "blocked",
                "message": "HH access token is required.",
                **safe_input,
            }
        run_id = self.storage.start_hh_agent_mcp_run("hh_api_lab_call", safe_input)
        self.storage.append_hh_operation_log(
            operation_id=run_id,
            level="info",
            message="Started HH API Lab call",
            payload=safe_input,
        )
        try:
            result = client.request_json(
                str(request["method"]),
                str(request["path"]),
                data=request.get("body"),
                params=dict(request.get("params") or {}),
            )
        except Exception as exc:
            output = {"status": "error", "operation_id": run_id, "error": str(exc), **safe_input}
            self.storage.finish_hh_agent_mcp_run(run_id, status="error", output=output, error=str(exc))
            self.storage.append_hh_operation_log(
                operation_id=run_id,
                level="error",
                message="Finished HH API Lab call with error",
                payload={**safe_input, "status": "error"},
            )
            return output
        output = {
            "status": "ok",
            "operation_id": run_id,
            **safe_input,
            "result": mask_secrets(result),
        }
        self.storage.finish_hh_agent_mcp_run(run_id, status="ok", output=output)
        self.storage.append_hh_operation_log(
            operation_id=run_id,
            level="info",
            message="Finished HH API Lab call",
            payload={**safe_input, "status": "ok"},
        )
        return output

    def _hh_api_lab_quick_request(self, quick: str) -> dict[str, Any]:
        quick_key = quick.strip().lower().lstrip("/")
        aliases = {
            "me": "me",
            "resumes": "resumes",
            "resumes/mine": "resumes",
            "negotiations": "negotiations",
            "vacancies": "vacancies",
        }
        quick_id = aliases.get(quick_key)
        if not quick_id:
            raise ValueError(f"Unknown HH API Lab quick call: {quick}")
        for item in self.list_hh_api_lab_quick_calls():
            if item["id"] == quick_id:
                return _normalize_hh_api_lab_request(
                    method=str(item["method"]),
                    path=str(item["path"]),
                    params=dict(item.get("params") or {}),
                    body=item.get("body"),
                )
        raise ValueError(f"Unknown HH API Lab quick call: {quick}")

    def list_hh_api_lab_snippets(self) -> list[dict[str, Any]]:
        return [mask_secrets(item) for item in self.storage.list_hh_api_lab_snippets()]

    def save_hh_api_lab_snippet(
        self,
        *,
        name: str,
        method: str,
        path: str,
        params: Any = None,
        body: Any = None,
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("Snippet name is required")
        request = _normalize_hh_api_lab_request(method=method, path=path, params=params, body=body)
        snippet = self.storage.upsert_hh_api_lab_snippet(
            name=name,
            method=str(request["method"]),
            path=str(request["path"]),
            params=dict(request.get("params") or {}),
            body=request.get("body") or {},
        )
        return mask_secrets(snippet)

    def delete_hh_api_lab_snippet(self, name: str) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("Snippet name is required")
        return {"status": "ok", "deleted": self.storage.delete_hh_api_lab_snippet(name)}

    def _hh_approval_payload(self, message_id: int) -> dict[str, Any]:
        for item in self.storage.list_hh_pending_messages():
            if item.id == message_id:
                return item.to_dict()
        raise ValueError(f"HH approval {message_id} not found")

    def save_hh_campaign_preset(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        if not name.strip():
            raise ValueError("Preset name is required")
        safe_params = _safe_preset_params(params)

        def save_preset(config: dict[str, Any]) -> None:
            presets = dict(config.get("hh_campaign_presets") or {})
            presets[name] = safe_params
            config["hh_campaign_presets"] = presets

        self._update_config_fresh(save_preset)
        return {"name": name, "params": safe_params}

    def get_hh_campaign_preset(self, name: str) -> dict[str, Any]:
        presets = self.config.get("hh_campaign_presets") or {}
        if name not in presets:
            raise ValueError(f"HH campaign preset '{name}' not found")
        return {"name": name, "params": dict(presets[name])}

    def list_hh_campaign_presets(self) -> list[dict[str, Any]]:
        presets = self.config.get("hh_campaign_presets") or {}
        return [
            {"name": name, "params": dict(params)}
            for name, params in sorted(presets.items())
        ]

    def delete_hh_campaign_preset(self, name: str) -> dict[str, Any]:
        def delete_preset(config: dict[str, Any]) -> None:
            presets = dict(config.get("hh_campaign_presets") or {})
            presets.pop(name, None)
            config["hh_campaign_presets"] = presets

        self._update_config_fresh(delete_preset)
        return {"status": "ok"}

    def list_strategies(self) -> dict[str, Any]:
        presets = self.storage.list_search_presets()
        active_name = str(self.config.get("profile") or "default")
        generated = self._strategy_spec(active_name)
        names = {item["name"] for item in presets}
        if generated and generated["name"] not in names:
            presets.append(
                {
                    "name": generated["name"],
                    "source": generated.get("source", "hh"),
                    "params": generated,
                    "dry_run_checked_at": "",
                    "last_live_run_at": "",
                    "last_result": {},
                    "enabled": True,
                    "generated": True,
                }
            )
        return {"status": "ok", "strategies": presets}

    def run_strategy(
        self,
        name: str,
        *,
        dry_run: bool = True,
        confirm: bool = False,
        resume_id: str | None = None,
    ) -> dict[str, Any]:
        spec = self._strategy_spec(name)
        if not spec:
            return {"status": "blocked", "code": "strategy_not_found", "name": name}
        queries = list(spec.get("queries") or [])
        if not queries:
            return {"status": "blocked", "code": "strategy_has_no_queries", "name": name}
        daily_limit = int(spec.get("daily_limit") or spec.get("limit") or 40)
        min_score = int(spec.get("min_score") or 0)
        if dry_run and not confirm:
            result = {
                "status": "planned",
                "name": name,
                "dry_run": True,
                "queries": queries,
                "daily_limit": daily_limit,
                "min_score": min_score,
                "requires_confirmation": True,
                "next_actions": [f"work-hunter strategy run {name} --confirm"],
            }
            self.storage.upsert_search_preset(name=name, source=str(spec.get("source") or "hh"), params=spec)
            self.storage.update_search_preset_result(name, dry_run=True, result=result)
            return result
        if not confirm:
            return {
                "status": "blocked",
                "code": "confirm_required",
                "message": "Use --confirm to run strategy searches and write imported jobs.",
                "name": name,
            }

        remaining = max(1, daily_limit)
        query_results: list[dict[str, Any]] = []
        imported_total = 0
        for query in queries:
            if remaining <= 0:
                break
            if not isinstance(query, dict):
                query = {"text": str(query)}
            query_result = self.search_hh_vacancies(
                text=str(query.get("text") or ""),
                area=_string_list(query.get("area") or query.get("areas")),
                professional_role=_string_list(query.get("professional_role")),
                industry=_string_list(query.get("industry")),
                salary=_optional_int(query.get("salary")),
                schedule=str(query.get("schedule") or "") or None,
                experience=str(query.get("experience") or "") or None,
                employment=_string_list(query.get("employment")),
                limit=remaining,
            )
            query_results.append(query_result)
            count = _strict_non_negative_int(
                query_result.get("count"),
                field="Search result count",
            )
            imported_total += count
            remaining -= count
            if query_result.get("status") not in {"ok", "blocked"} and count == 0:
                break
        if imported_total == 0 and query_results and any(item.get("status") != "ok" for item in query_results):
            result = {
                "status": "blocked",
                "name": name,
                "dry_run": False,
                "imported": 0,
                "queries": query_results,
                "message": "Strategy search did not import jobs; fix auth/search errors before campaign planning.",
            }
            self.storage.upsert_search_preset(name=name, source=str(spec.get("source") or "hh"), params=spec)
            self.storage.update_search_preset_result(name, dry_run=False, result=result)
            return result
        scored = self.score_jobs()
        campaign = self.plan_hh_campaign(
            limit=min(daily_limit, max(imported_total, 1)),
            min_score=min_score,
            resume_id=resume_id or str(spec.get("resume_id") or "") or None,
        )
        result = {
            "status": "ok",
            "name": name,
            "dry_run": False,
            "imported": imported_total,
            "scored": scored,
            "campaign": campaign,
            "queries": query_results,
            "next_actions": [
                f"work-hunter hh campaign confirm --run-id {campaign.get('id')} --confirm"
                if campaign.get("id")
                else "work-hunter hh campaign plan --limit 20"
            ],
        }
        self.storage.upsert_search_preset(name=name, source=str(spec.get("source") or "hh"), params=spec)
        self.storage.update_search_preset_result(name, dry_run=False, result=result)
        return result

    def strategy_report(self, name: str) -> dict[str, Any]:
        preset = self.storage.get_search_preset(name)
        spec = self._strategy_spec(name)
        if preset is None and spec:
            preset = {
                "name": name,
                "source": spec.get("source", "hh"),
                "params": spec,
                "dry_run_checked_at": "",
                "last_live_run_at": "",
                "last_result": {},
                "enabled": True,
            }
        if preset is None:
            return {"status": "blocked", "code": "strategy_not_found", "name": name}
        min_score = int((preset.get("params") or {}).get("min_score") or 0)
        jobs = self.list_jobs(
            limit=20,
            source=str(preset.get("source") or "hh"),
            min_score=min_score,
        )
        return {
            "status": "ok",
            "strategy": preset,
            "top_jobs": [job.to_dict() for job in jobs],
            "counts": {
                "top_jobs": len(jobs),
                "applications": len(
                    {item.job_id for item in self.storage.list_applications()}
                ),
            },
        }

    def _strategy_spec(self, name: str) -> dict[str, Any] | None:
        strategies = self.config.get("search_strategies") or {}
        if isinstance(strategies, dict) and isinstance(strategies.get(name), dict):
            spec = dict(strategies[name])
            spec.setdefault("name", name)
            spec.setdefault("source", "hh")
            return spec
        preset = self.storage.get_search_preset(name)
        if preset:
            params = dict(preset.get("params") or {})
            params.setdefault("name", name)
            params.setdefault("source", preset.get("source") or "hh")
            return params
        active_name = str(self.config.get("profile") or "default")
        if name in {active_name, "active", "active-profile", "default"}:
            profile = active_profile(self.config)
            hh_config = self.hh_config()
            area = str(hh_config.get("area") or 113)
            queries = [{"text": str(query), "area": [area]} for query in profile.get("queries") or []]
            return {
                "name": active_name,
                "source": "hh",
                "queries": queries,
                "exclude": "|".join(str(item) for item in profile.get("stop_words") or []),
                "min_score": 50,
                "daily_limit": 40,
                "dry_run_required": True,
            }
        return None

    def search_hh_vacancies(
        self,
        *,
        text: str | None = None,
        area: list[str] | None = None,
        professional_role: list[str] | None = None,
        industry: list[str] | None = None,
        salary: int | None = None,
        schedule: str | None = None,
        experience: str | None = None,
        employment: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        search_field: list[str] | None = None,
        employer_id: list[str] | None = None,
        excluded_employer_id: list[str] | None = None,
        only_with_salary: bool = False,
        limit: int = 20,
        page: int = 0,
        order_by: str | None = None,
        period: int | None = None,
        currency: str | None = None,
        no_magic: bool = False,
        premium: bool = False,
    ) -> dict[str, Any]:
        hh_config = self.hh_config()
        client = self._hh_client()
        if not client.has_token():
            if hh_config.get("web_fallback", True):
                return self._search_hh_vacancies_web_fallback(
                    text=text,
                    area=area,
                    salary=salary,
                    limit=limit,
                    reason="missing_access_token",
                )
            return {
                "status": "blocked",
                "count": 0,
                "message": "HH access token is required.",
                "next_actions": ["work-hunter hh auth import-token"],
            }
        total_limit = max(1, int(limit))
        per_page = min(100, total_limit)
        imported_jobs: list[Job] = []
        seen: set[str] = set()
        current_page = max(0, int(page))
        base_params = _compact_hh_search_params(
            {
                "text": text,
                "area": area,
                "professional_role": professional_role,
                "industry": industry,
                "salary": salary,
                "schedule": schedule,
                "experience": experience,
                "employment": employment,
                "date_from": date_from,
                "date_to": date_to,
                "search_field": search_field,
                "employer_id": employer_id,
                "excluded_employer_id": excluded_employer_id,
                "only_with_salary": True if only_with_salary else None,
                "order_by": order_by,
                "period": period,
                "currency": currency,
                "no_magic": True if no_magic else None,
                "premium": True if premium else None,
                "per_page": per_page,
            }
        )
        while len(imported_jobs) < total_limit:
            search_params = {**base_params, "page": current_page}
            try:
                payloads = client.search_vacancies(search_params)
            except Exception as exc:
                if hh_config.get("web_fallback", True):
                    return self._search_hh_vacancies_web_fallback(
                        text=text,
                        area=area,
                        salary=salary,
                        limit=limit,
                        reason="api_error",
                        api_error=str(exc),
                    )
                return {
                    **_hh_error_payload(exc),
                    "source": "hh",
                    "transport": "api",
                    "count": len(imported_jobs),
                    "job_ids": [job.id for job in imported_jobs if job.id is not None],
                    "search_params": {**base_params, "page": current_page},
                }
            if not payloads:
                break
            for payload in payloads:
                job = _hh_job_from_vacancy_payload(payload)
                if not job.source_id or job.source_id in seen:
                    continue
                seen.add(job.source_id)
                job.id = self.storage.upsert_job(job)
                imported_jobs.append(job)
                if len(imported_jobs) >= total_limit:
                    break
            if len(payloads) < per_page:
                break
            current_page += 1
        return {
            "status": "ok",
            "source": "hh",
            "transport": "api",
            "count": len(imported_jobs),
            "job_ids": [job.id for job in imported_jobs if job.id is not None],
            "search_params": {**base_params, "page": page},
            "next_actions": [
                "work-hunter score",
                "work-hunter list --source hh --limit 20 --min-score 50",
                "work-hunter hh campaign plan --limit 20",
            ],
        }

    def _search_hh_vacancies_web_fallback(
        self,
        *,
        text: str | None,
        area: list[str] | None,
        salary: int | None,
        limit: int,
        reason: str,
        api_error: str = "",
    ) -> dict[str, Any]:
        hh_config, backend = self._hh_runtime()
        hh_config = dict(hh_config)
        if area:
            hh_config["area"] = area[0]
        hh_config["per_page"] = min(100, max(1, int(limit)))
        profile = {
            "queries": [text or ""] if text else (self.active_profile_info().get("data") or {}).get("queries", [""]),
            "salary_min": salary or 0,
        }
        imported_jobs: list[Job] = []
        try:
            for job in HHSource(hh_config, backend=backend).collect(
                profile,
                limit=max(1, int(limit)),
            ):
                job.id = self.storage.upsert_job(job)
                imported_jobs.append(job)
        except Exception as exc:
            return {
                "status": "blocked",
                "source": "hh",
                "transport": "web",
                "fallback_reason": reason,
                "count": 0,
                "message": "HH web fallback failed.",
                "error": str(exc),
                "api_error": api_error,
                "next_actions": [
                    "work-hunter hh auth import-token",
                    "work-hunter hh web import-cookies ./cookies.txt",
                ],
            }
        return {
            "status": "ok",
            "source": "hh",
            "transport": "web",
            "fallback_reason": reason,
            "count": len(imported_jobs),
            "job_ids": [job.id for job in imported_jobs if job.id is not None],
            "api_error": api_error,
            "next_actions": [
                "work-hunter score",
                "work-hunter list --source hh --limit 20 --min-score 50",
                "work-hunter hh auth import-token",
            ],
        }

    def plan_hh_search_campaign(
        self,
        *,
        text: str | None = None,
        area: list[str] | None = None,
        professional_role: list[str] | None = None,
        industry: list[str] | None = None,
        salary: int | None = None,
        schedule: str | None = None,
        experience: str | None = None,
        employment: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        search_field: list[str] | None = None,
        employer_id: list[str] | None = None,
        excluded_employer_id: list[str] | None = None,
        only_with_salary: bool = False,
        limit: int = 100,
        page: int = 0,
        min_score: int = 0,
        skip_tests: bool = False,
        ai_filter_mode: str = "off",
        resume_id: str | None = None,
    ) -> dict[str, Any]:
        client = self._hh_client()
        if not client.has_token():
            return {"status": "blocked", "count": 0, "message": "HH access token is required."}
        search_params = _compact_hh_search_params(
            {
                "text": text,
                "area": area,
                "professional_role": professional_role,
                "industry": industry,
                "salary": salary,
                "schedule": schedule,
                "experience": experience,
                "employment": employment,
                "date_from": date_from,
                "date_to": date_to,
                "search_field": search_field,
                "employer_id": employer_id,
                "excluded_employer_id": excluded_employer_id,
                "only_with_salary": True if only_with_salary else None,
                "per_page": limit,
                "page": page,
            }
        )
        imported_jobs: list[Job] = []
        profile_id = self.active_profile_id()
        for payload in client.search_vacancies(search_params):
            job = _hh_job_from_vacancy_payload(payload)
            if not job.source_id:
                continue
            job.id = self.storage.upsert_job(job)
            job.score = self.storage.get_score(job.id, profile_id)
            imported_jobs.append(job)

        filters = {
            "mode": "hh_search",
            "search_params": search_params,
            "limit": limit,
            "min_score": min_score,
            "skip_tests": skip_tests,
            "ai_filter_mode": ai_filter_mode,
            "resume_id": resume_id or "",
        }
        run_id = self.storage.create_hh_campaign_run(filters=filters)
        counts = _campaign_counts()
        for job in imported_jobs:
            item = self._plan_hh_campaign_item(
                run_id,
                job,
                min_score=min_score,
                skip_tests=skip_tests,
                ai_filter_mode=ai_filter_mode,
                resume_id=resume_id,
            )
            self.storage.save_hh_campaign_item(item)
            if item.status in counts:
                counts[item.status] += 1
            else:
                counts["error"] += 1
        self.storage.update_hh_campaign_run(run_id, status="planned", counts=counts)
        run = self.storage.get_hh_campaign_run(run_id)
        payload = run.to_dict() if run else {"id": run_id, "status": "planned", "counts": counts}
        payload["imported"] = len(imported_jobs)
        return payload

    def plan_hh_campaign(
        self,
        *,
        limit: int = 100,
        min_score: int = 0,
        skip_tests: bool = False,
        ai_filter_mode: str = "off",
        resume_id: str | None = None,
    ) -> dict[str, Any]:
        filters = {
            "limit": limit,
            "min_score": min_score,
            "skip_tests": skip_tests,
            "ai_filter_mode": ai_filter_mode,
            "resume_id": resume_id or "",
        }
        run_id = self.storage.create_hh_campaign_run(filters=filters)
        counts = _campaign_counts()
        jobs = self.list_jobs(limit=limit, source="hh")
        for job in jobs:
            item = self._plan_hh_campaign_item(
                run_id,
                job,
                min_score=min_score,
                skip_tests=skip_tests,
                ai_filter_mode=ai_filter_mode,
                resume_id=resume_id,
            )
            self.storage.save_hh_campaign_item(item)
            if item.status in counts:
                counts[item.status] += 1
            elif item.status == "ready":
                counts["ready"] += 1
            else:
                counts["error"] += 1
        self.storage.update_hh_campaign_run(run_id, status="planned", counts=counts)
        run = self.storage.get_hh_campaign_run(run_id)
        return run.to_dict() if run else {"id": run_id, "status": "planned", "counts": counts}

    def _plan_hh_campaign_item(
        self,
        run_id: int,
        job: Job,
        *,
        min_score: int,
        skip_tests: bool,
        ai_filter_mode: str,
        resume_id: str | None,
    ) -> HHCampaignItem:
        job_id = int(job.id or 0)
        score = job.score.total_score if job.score else 0
        if min_score and score < min_score:
            return HHCampaignItem(
                run_id=run_id,
                job_id=job_id,
                vacancy_id=job.source_id,
                status="skipped",
                reason="below_min_score",
            )
        if self.storage.get_application(job_id) is not None:
            return HHCampaignItem(
                run_id=run_id,
                job_id=job_id,
                vacancy_id=job.source_id,
                status="skipped",
                reason="already_applied",
            )
        previous_skip = self._hh_previous_skip_reason(job.source_id, resume_id=resume_id)
        if previous_skip:
            return HHCampaignItem(
                run_id=run_id,
                job_id=job_id,
                vacancy_id=job.source_id,
                status="skipped",
                reason="skipped_before",
                resume_id=resume_id or "",
                raw_result={"previous_skip_reason": previous_skip},
            )
        try:
            plan = self.prepare_apply_plan(job_id, resume_id=resume_id)
        except Exception as exc:
            return HHCampaignItem(
                run_id=run_id,
                job_id=job_id,
                vacancy_id=job.source_id,
                status="error",
                reason=str(exc),
            )
        risk_flags = list(plan.get("risk_flags") or [])
        for terminal_reason in ("archived", "already_applied", "has_relations"):
            if terminal_reason in risk_flags:
                return HHCampaignItem(
                    run_id=run_id,
                    job_id=job_id,
                    vacancy_id=job.source_id,
                    status="skipped",
                    reason=terminal_reason,
                    resume_id=str(plan.get("resume_id") or resume_id or ""),
                    letter=str(plan.get("letter") or ""),
                    risk_flags=risk_flags,
                    raw_result=plan,
                )
        if skip_tests and "test_required" in risk_flags:
            return HHCampaignItem(
                run_id=run_id,
                job_id=job_id,
                vacancy_id=job.source_id,
                status="skipped",
                reason="test_required",
                resume_id=str(plan.get("resume_id") or ""),
                letter=str(plan.get("letter") or ""),
                risk_flags=risk_flags,
                raw_result=plan,
            )
        ai_filter_mode = (ai_filter_mode or "off").lower()
        if ai_filter_mode in {"light", "heavy"}:
            ai_filter = self._hh_ai_filter_decision(job, mode=ai_filter_mode)
            plan["ai_filter"] = ai_filter
            if ai_filter.get("needs_review"):
                return HHCampaignItem(
                    run_id=run_id,
                    job_id=job_id,
                    vacancy_id=job.source_id,
                    status="error",
                    reason="needs_review",
                    resume_id=str(plan.get("resume_id") or ""),
                    letter=str(plan.get("letter") or ""),
                    risk_flags=risk_flags,
                    raw_result=plan,
                )
            if not ai_filter["suitable"]:
                return HHCampaignItem(
                    run_id=run_id,
                    job_id=job_id,
                    vacancy_id=job.source_id,
                    status="skipped",
                    reason="ai_rejected",
                    resume_id=str(plan.get("resume_id") or ""),
                    letter=str(plan.get("letter") or ""),
                    risk_flags=risk_flags,
                    raw_result=plan,
                )
        status = str(plan.get("status") or "error")
        if status not in {"ready", "blocked", "external"}:
            status = "error"
        return HHCampaignItem(
            run_id=run_id,
            job_id=job_id,
            vacancy_id=job.source_id,
            status="ready" if status == "ready" else "error",
            reason="" if status == "ready" else status,
            resume_id=str(plan.get("resume_id") or ""),
            letter=str(plan.get("letter") or ""),
            risk_flags=risk_flags,
            raw_result=plan,
        )

    def _hh_ai_filter_decision(self, job: Job, *, mode: str) -> dict[str, Any]:
        ai_config = self.config.get("ai", {})
        context = {
            "title": job.title,
            "company": job.company,
            "salary": job.salary_text,
            "location": job.location,
            "remote": job.remote,
            "description": job.description,
        }
        if mode == "heavy":
            prompt = (
                "Decide if this HH vacancy is suitable for the active candidate profile. "
                "Use title, company, salary, location, remote flag, and description. "
                "Return strict JSON: {\"suitable\": true|false, \"reason\": \"short reason\"}.\n"
                f"Vacancy: {json.dumps(context, ensure_ascii=False)}\n"
                f"Profile: {json.dumps(active_profile(self.config), ensure_ascii=False)}"
            )
        else:
            prompt = (
                "Decide if this HH vacancy is suitable using only the role/title and explicit skills. "
                "Return strict JSON: {\"suitable\": true|false, \"reason\": \"short reason\"}.\n"
                f"Vacancy: {json.dumps(context, ensure_ascii=False)}\n"
                f"Profile: {json.dumps(active_profile(self.config), ensure_ascii=False)}"
            )
        try:
            raw = chat_completion(
                [
                    {
                        "role": "system",
                        "content": "You are a conservative job-search relevance filter. Return only JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                ai_config,
            )
        except Exception as exc:
            return {
                "mode": mode,
                "suitable": False,
                "needs_review": True,
                "reason": f"AI filter failed: {exc}",
            }
        return _parse_ai_filter_response(raw, mode=mode)

    def _hh_previous_skip_reason(self, vacancy_id: str, *, resume_id: str | None = None) -> str:
        wanted_resume_ids = {"", str(resume_id or "")}
        for item in self.storage.list_hh_skipped_vacancies():
            if item.vacancy_id == vacancy_id and item.resume_id in wanted_resume_ids:
                return item.reason or "skipped"
        return ""

    def confirm_hh_campaign(self, run_id: int, *, confirm: bool = False) -> dict[str, Any]:
        run = self.storage.get_hh_campaign_run(run_id)
        if run is None:
            raise ValueError(f"HH campaign run {run_id} not found")
        if not confirm:
            return {
                "status": "blocked",
                "message": "Explicit confirmation is required before sending a campaign.",
                "id": run_id,
            }
        counts = _campaign_counts()
        for item in self.storage.list_hh_campaign_items(run_id):
            if item.status != "ready":
                if item.status in counts:
                    counts[item.status] += 1
                continue
            result = self.confirm_apply(
                item.job_id,
                resume_id=item.resume_id or None,
                letter=item.letter,
                confirm=True,
            )
            status = str(result.get("status") or "error")
            if status == "applied":
                counts["applied"] += 1
                self.storage.update_hh_campaign_item(
                    item.id,
                    status="applied",
                    raw_result=result,
                )
            else:
                counts["error"] += 1
                self.storage.update_hh_campaign_item(
                    item.id,
                    status="error",
                    reason=status,
                    raw_result=result,
                )
        self.storage.update_hh_campaign_run(
            run_id,
            status="confirmed",
            counts=counts,
            finished=True,
        )
        updated = self.storage.get_hh_campaign_run(run_id)
        return updated.to_dict() if updated else {"id": run_id, "status": "confirmed", "counts": counts}

    def _application_letter(self, job_id: int, letter: str | None = None) -> str:
        if letter is not None:
            return letter
        latest = self.storage.get_latest_letter(job_id)
        if latest:
            return latest.body
        return self.prepare_letter(job_id).body

    def daily_report(self, *, limit: int = 10) -> dict[str, Any]:
        jobs = self.list_jobs(limit=limit, min_score=1)
        top = [job.to_dict() for job in jobs]
        by_source: dict[str, int] = {}
        for job in self.list_jobs(limit=10000):
            by_source[job.source] = by_source.get(job.source, 0) + 1
        return {
            "total_jobs": sum(by_source.values()),
            "by_source": by_source,
            "top": top,
            "sources": self.storage.list_sources(),
        }

    def resume_tips(self, job_id: int) -> str:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        about = self.config.get("about", {})
        ai_config = self.config.get("ai", {})
        experience_text = _format_experience(about)
        system_prompt = (
            "ты карьерный консультант. Проанализируй вакансию и профиль кандидата. "
            "Дай 3-4 конкретных совета что подсветить в резюме: какие проекты, "
            "какие навыки, как описать опыт чтобы лучше подходить."
        )
        user_prompt = _build_vacancy_candidate_prompt(job, about, experience_text)
        try:
            return chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                ai_config,
            )
        except Exception as exc:
            raise ValueError(f"AI error: {exc}")

    def ats_resume(self, job_id: int) -> str:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        about = self.config.get("about", {})
        ai_config = self.config.get("ai", {})
        experience_text = _format_experience(about)
        system_prompt = (
            "ты эксперт по ATS-оптимизации резюме. Сгенерируй резюме в markdown формате "
            "под конкретную вакансию. Используй только реальный опыт кандидата. "
            "Начни с самых релевантных проектов. Вплети ключевые слова из вакансии естественно."
        )
        user_prompt = f"""{_build_vacancy_candidate_prompt(job, about, experience_text)}

Сгенерируй резюме в формате:
# Имя
## Навыки
## Опыт
(используй markdown)"""
        try:
            return chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                ai_config,
            )
        except Exception as exc:
            raise ValueError(f"AI error: {exc}")

    def ats_audit(self, resume_text: str, job_id: int | None = None) -> str:
        ai_config = self.config.get("ai", {})
        system_prompt = (
            "ты ATS-аудитор. Проанализируй резюме на совместимость с ATS: "
            "форматирование (колонки/таблицы не читаются), ключевые слова, "
            "контактные данные, читаемость. Дай оценку и конкретные исправления."
        )
        user_prompt = f"Резюме для анализа:\n\n{resume_text}"
        if job_id is not None:
            job = self.storage.get_job(job_id)
            if job:
                user_prompt += (
                    f"\n\nВакансия для контекста:\n"
                    f"- Позиция: {job.title}\n"
                    f"- Компания: {job.company or 'не указана'}\n"
                    f"- Описание: {job.description or 'нет описания'}\n"
                )
        try:
            return chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                ai_config,
            )
        except Exception as exc:
            raise ValueError(f"AI error: {exc}")

    def summarize_job(self, job_id: int) -> str:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        ai_config = self.config.get("ai", {})
        system_prompt = (
            "сожми описание вакансии до 2-3 предложений. "
            "Оставь: роль, ключевой стек, вилка зп, главная особенность/плюшка. "
            "Язык: русский."
        )
        user_prompt = (
            f"Вакансия:\n"
            f"- Позиция: {job.title}\n"
            f"- Компания: {job.company or 'не указана'}\n"
            f"- Зарплата: {job.salary_text or 'не указана'}\n"
            f"- Локация: {job.location or 'не указана'}\n"
            f"- Удалёнка: {'да' if job.remote else 'нет/не указано'}\n"
            f"- Описание: {job.description or 'нет описания'}\n"
        )
        try:
            return chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                ai_config,
            )
        except Exception as exc:
            raise ValueError(f"AI error: {exc}")

    def ai_fit(self, job_id: int) -> dict[str, Any]:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        profile = active_profile(self.config)
        about = self.config.get("about", {})
        ai_config = self.config.get("ai", {})
        experience_text = _format_experience(about)
        system_prompt = (
            "ты эксперт по карьерному фиту. Оцени насколько кандидат подходит на вакансию. "
            "Верни ответ строго в формате JSON: "
            '{"score": <число 0-100>, "reasoning": "<краткое обоснование на русском>"}'
        )
        user_prompt = f"""Вакансия:
- Позиция: {job.title}
- Компания: {job.company or 'не указана'}
- Описание: {job.description or 'нет описания'}
- Зарплата: {job.salary_text or 'не указана'}
- Удалёнка: {'да' if job.remote else 'нет/не указано'}
- Локация: {job.location or 'не указана'}

Профиль:
- Целевая роль: {profile.get('title', '')}
- Навыки: {', '.join(profile.get('must_have_skills', []) + profile.get('nice_to_have_skills', []))}

О кандидате:
- Резюме: {about.get('summary', '')}
- Все навыки: {', '.join(about.get('all_skills', []))}

Опыт:
{experience_text}"""
        try:
            raw = chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                ai_config,
            )
            parsed = _parse_fit_json(raw)
            return parsed
        except Exception as exc:
            raise ValueError(f"AI error: {exc}")

    def interview_questions(self, job_id: int) -> str:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        ai_config = self.config.get("ai", {})
        system_prompt = (
            "сгенерируй 4-5 вопросов которые стоит задать работодателю на собеседовании "
            "по этой вакансии: про команду, стек, процессы, ожидания, рост. Язык: русский."
        )
        user_prompt = (
            f"Вакансия:\n"
            f"- Позиция: {job.title}\n"
            f"- Компания: {job.company or 'не указана'}\n"
            f"- Описание: {job.description or 'нет описания'}\n"
            f"- Зарплата: {job.salary_text or 'не указана'}\n"
            f"- Локация: {job.location or 'не указана'}\n"
            f"- Удалёнка: {'да' if job.remote else 'нет/не указано'}\n"
        )
        try:
            return chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                ai_config,
            )
        except Exception as exc:
            raise ValueError(f"AI error: {exc}")

    def experience_pitch(self, job_id: int) -> str:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        about = self.config.get("about", {})
        ai_config = self.config.get("ai", {})
        experience_text = _format_experience(about)
        system_prompt = (
            "ты карьерный коуч. Выбери из опыта кандидата самый релевантный проект "
            "для этой вакансии и напиши STAR-питч (Situation, Task, Action, Result) "
            "на 4-5 предложений. Только реальный опыт из предоставленного."
        )
        user_prompt = f"""Вакансия:
- Позиция: {job.title}
- Компания: {job.company or 'не указана'}
- Описание: {job.description or 'нет описания'}

Опыт кандидата:
{experience_text}"""
        try:
            return chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                ai_config,
            )
        except Exception as exc:
            raise ValueError(f"AI error: {exc}")

    def fetch_full_description(self, job_id: int) -> dict[str, Any]:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        try:
            html = fetch_url(job.url)
        except Exception as exc:
            raise ValueError(f"Failed to fetch URL: {exc}")
        description = _extract_description(html)
        if description:
            self.storage.update_job_description(job_id, description)
            return {"description": description, "updated": True}
        return {"description": job.description or "", "updated": False}

    def search_by_description(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        ai_config = self.config.get("ai", {})
        jobs: list[Job] = []
        try:
            system_prompt = (
                "ты парсер поисковых запросов. Извлеки из текста: ключевые слова для поиска, "
                "желаемую роль, навыки. Верни JSON: "
                '{"queries": [...], "desired_skills": [...], "remote_only": bool}'
            )
            raw = chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Запрос: {query}"},
                ],
                ai_config,
            )
            parsed = _parse_search_json(raw)
            keywords = parsed.get("queries", [query.split()[:5]])
            remote_only = parsed.get("remote_only", False)
            jobs = self.storage.search_jobs(
                keywords,
                limit=limit,
                profile_id=self.active_profile_id(),
            )
            if remote_only:
                jobs = [j for j in jobs if j.remote]
        except Exception:
            words = query.strip().split()[:5]
            jobs = self.storage.search_jobs(
                words,
                limit=limit,
                profile_id=self.active_profile_id(),
            )
        return [job.to_dict() for job in jobs]

    def export_jobs(self, status: str | None = None, source: str | None = None, format: str = "json") -> str:
        jobs = self.list_jobs(limit=100000, status=status, source=source)
        if format == "csv":
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["id", "source", "title", "company", "salary", "location", "remote", "score", "status", "url"])
            for job in jobs:
                score = job.score.total_score if job.score else 0
                writer.writerow([
                    job.id,
                    job.source,
                    job.title,
                    job.company,
                    job.salary_text,
                    job.location,
                    "yes" if job.remote else "no",
                    score,
                    job.status,
                    job.url,
                ])
            return buf.getvalue()
        if format == "jsonl":
            return "\n".join(json.dumps(job.to_dict(), ensure_ascii=False) for job in jobs) + ("\n" if jobs else "")
        return json.dumps([job.to_dict() for job in jobs], ensure_ascii=False)

    def export_applications(self, *, format: str = "jsonl") -> str:
        rows: list[dict[str, Any]] = []
        for app in self.storage.list_applications():
            job = self.get_job(app.job_id)
            item = app.to_dict()
            if job:
                item["job"] = {
                    "id": job.id,
                    "source": job.source,
                    "source_id": job.source_id,
                    "title": job.title,
                    "company": job.company,
                    "url": job.url,
                    "score": job.score.total_score if job.score else 0,
                }
            rows.append(mask_secrets(item))
        if format == "csv":
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["id", "job_id", "status", "applied_at", "title", "company", "source", "url", "notes"])
            for item in rows:
                job_data = item.get("job")
                job_payload = job_data if isinstance(job_data, dict) else {}
                writer.writerow([
                    item.get("id"),
                    item.get("job_id"),
                    item.get("status"),
                    item.get("applied_at"),
                    job_payload.get("title", ""),
                    job_payload.get("company", ""),
                    job_payload.get("source", ""),
                    job_payload.get("url", ""),
                    item.get("notes", ""),
                ])
            return buf.getvalue()
        if format == "json":
            return json.dumps(rows, ensure_ascii=False)
        return "\n".join(json.dumps(item, ensure_ascii=False) for item in rows) + ("\n" if rows else "")

    def export_report(self, *, since: str = "", format: str = "json") -> str:
        report: dict[str, Any] = {
            "status": "ok",
            "since": since,
            "daily": self.daily_report(limit=20),
            "stats": self.storage.get_stats(profile_id=self.active_profile_id()),
            "doctor": self.doctor(),
        }
        if format == "md":
            lines = [
                "# Work Hunter Report",
                "",
                f"Since: {since or 'all time'}",
                f"Total jobs: {report['stats'].get('total_jobs', 0)}",
                f"Applications: {report['stats'].get('total_applications', 0)}",
                "",
                "## Top Jobs",
            ]
            for job in report["daily"].get("top", [])[:10]:
                score = (job.get("score") or {}).get("total_score") or 0
                lines.append(f"- [{score}] {job.get('title')} @ {job.get('company')} - {job.get('url')}")
            return "\n".join(lines) + "\n"
        return json.dumps(mask_secrets(report), ensure_ascii=False)

    def import_jobs_file(self, path: str | Path) -> dict[str, Any]:
        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = self.root / file_path
        if not file_path.exists():
            return {"status": "blocked", "code": "file_not_found", "path": str(file_path)}
        suffix = file_path.suffix.lower()
        rows: list[dict[str, Any]] = []
        if suffix == ".csv":
            with file_path.open("r", encoding="utf-8-sig", newline="") as fh:
                rows = [dict(row) for row in csv.DictReader(fh)]
        elif suffix == ".jsonl":
            rows = [
                json.loads(line)
                for line in file_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        elif suffix == ".json":
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            rows = payload if isinstance(payload, list) else list(payload.get("jobs") or [])
        else:
            return {"status": "blocked", "code": "unsupported_format", "path": str(file_path)}
        imported = 0
        errors: list[dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            try:
                source = str(row.get("source") or "import")
                source_id = str(row.get("source_id") or row.get("id") or row.get("url") or f"row-{index}")
                title = str(row.get("title") or row.get("name") or "")
                url = str(row.get("url") or row.get("alternate_url") or "")
                if not title or not url:
                    raise ValueError("title and url are required")
                job = Job(
                    source=source,
                    source_id=source_id,
                    url=url,
                    title=title,
                    company=str(row.get("company") or row.get("employer") or ""),
                    salary_text=str(row.get("salary") or row.get("salary_text") or ""),
                    location=str(row.get("location") or ""),
                    remote=_parse_bool(row.get("remote")),
                    description=str(row.get("description") or row.get("snippet") or ""),
                    published_at=str(row.get("published_at") or ""),
                )
                self.storage.upsert_job(job)
                imported += 1
            except Exception as exc:
                errors.append({"row": index, "error": str(exc)})
        return {
            "status": "ok" if not errors else "partial",
            "path": str(file_path),
            "count": imported,
            "errors": errors,
        }

    def parse_job_structure(self, job_id: int) -> dict[str, Any]:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        ai_config = self.config.get("ai", {})
        system_prompt = (
            "Ты парсер вакансий. Извлеки из текста вакансии структурированную информацию. "
            "Верни ТОЛЬКО валидный JSON без markdown-обёртки. "
            "Поля: tech_stack (список технологий), experience_years (строка, например '1-3'), "
            "must_have (список обязательных требований), nice_to_have (список желательных), "
            "benefits (список бенефитов), seniority_level (junior/middle/senior/lead), "
            "company_type (product/outsource/startup/enterprise), real_remote (true/false)."
        )
        user_prompt = (job.description or "")[:2000]
        try:
            raw = chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                ai_config,
            )
            clean = _strip_markdown_json(raw)
            return json.loads(clean)
        except Exception as exc:
            return {"error": str(exc)}

    def market_trends(self, limit: int = 50) -> str:
        jobs = self.list_jobs(limit=limit)
        ai_config = self.config.get("ai", {})
        job_texts: list[str] = []
        for job in jobs:
            desc = (job.description or "")[:200]
            job_texts.append(f"- {job.title} ({job.company or '—'}): {desc}")
        system_prompt = (
            "Ты аналитик IT-рынка труда. Проанализируй список вакансий и ответь на вопросы: "
            "1) Какие технологии сейчас самые востребованные (топ-10)? "
            "2) Какие роли/специализации чаще всего ищут? "
            "3) Какие зарплатные вилки преобладают? "
            "4) Какие тренды заметны? "
            "Отвечай структурированно, на русском."
        )
        user_prompt = "\n".join(job_texts)
        try:
            return chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                ai_config,
            )
        except Exception as exc:
            return f"Ошибка: {exc}"

    def gap_analysis(self, resume_id: int, job_id: int) -> str:
        resume = self.storage.get_resume(resume_id)
        if resume is None:
            raise ValueError(f"Resume {resume_id} not found")
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        ai_config = self.config.get("ai", {})
        system_prompt = (
            "Ты карьерный консультант. Сравни резюме кандидата с требованиями вакансии. "
            "Найди: 1) Какие навыки из вакансии отсутствуют в резюме (skills gap) "
            "2) Какие навыки есть но не подсвечены/спрятаны "
            "3) Как переформулировать существующий опыт чтобы лучше матчить вакансию "
            "4) Что конкретно добавить в резюме. "
            "Отвечай по делу, 5-8 пунктов. На русском."
        )
        user_prompt = (
            f"Вакансия:\n"
            f"- Позиция: {job.title}\n"
            f"- Компания: {job.company or 'не указана'}\n"
            f"- Описание: {job.description or 'нет описания'}\n"
            f"\nРезюме:\n{resume.body}"
        )
        try:
            return chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                ai_config,
            )
        except Exception as exc:
            return f"Ошибка: {exc}"

    def ats_score_resume(self, resume_text: str) -> dict[str, Any]:
        ai_config = self.config.get("ai", {})
        system_prompt = (
            "Ты эксперт по ATS (Applicant Tracking Systems). Оцени резюме на совместимость с ATS. "
            "Критерии: 1) Отсутствие таблиц/колонок/изображений "
            "2) Наличие ключевых слов 3) Стандартные заголовки разделов ('Опыт', 'Навыки', 'Образование') "
            "4) Читаемость парсерами 5) Контактная информация. "
            "Верни ТОЛЬКО JSON: {\"score\": число 0-100, \"issues\": [список проблем], \"suggestions\": [список советов]}."
        )
        try:
            raw = chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": resume_text},
                ],
                ai_config,
            )
            clean = _strip_markdown_json(raw)
            return json.loads(clean)
        except Exception:
            return {"score": 0, "issues": ["AI error"], "suggestions": []}

    def smart_classify(self, job_id: int) -> dict[str, Any]:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        ai_config = self.config.get("ai", {})
        system_prompt = (
            "Ты классификатор IT-вакансий. Проанализируй описание и верни ТОЛЬКО JSON: "
            "{\"seniority_level\": \"junior/middle/senior/lead\", "
            "\"real_remote\": true/false, \"has_salary\": true/false, "
            "\"red_flags\": [список возможных красных флагов в вакансии: токсичные формулировки, завышенные требования, 'мы семья' и т.д.], "
            "\"company_type\": \"product/outsource/startup/enterprise/unknown\", "
            "\"tags\": [ключевые теги]}."
        )
        user_prompt = (
            f"Вакансия:\n"
            f"- Позиция: {job.title}\n"
            f"- Компания: {job.company or 'не указана'}\n"
            f"- Описание: {job.description or 'нет описания'}\n"
            f"- Зарплата: {job.salary_text or 'не указана'}"
        )
        try:
            raw = chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                ai_config,
            )
            clean = _strip_markdown_json(raw)
            return json.loads(clean)
        except Exception as exc:
            return {"error": str(exc)}

    def interview_stage_prep(self, job_id: int, stage: str) -> str:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        ai_config = self.config.get("ai", {})
        stage_prompts = {
            "hr": (
                "Подготовь к HR-скринингу: какие вопросы зададут про мотивацию, зп ожидания, причины ухода. "
                "Какие вопросы задать про компанию, команду, процессы."
            ),
            "tech": (
                "Подготовь к техническому собесу: какие технологии спросят, типичные задачи, что повторить. "
                "Составь список тем по описанию вакансии."
            ),
            "final": (
                "Подготовь к финальному собесу: вопросы про культуру, рост, ожидания. "
                "Как показать fit с командой."
            ),
            "offer": (
                "Подготовь к обсуждению оффера: как обсуждать зп, какие бенефиты просить, как торговаться."
            ),
        }
        system_prompt = stage_prompts.get(stage, stage_prompts["hr"])
        user_prompt = (
            f"Вакансия:\n"
            f"- Позиция: {job.title}\n"
            f"- Компания: {job.company or 'не указана'}\n"
            f"- Описание: {(job.description or '')[:1000]}"
        )
        try:
            return chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                ai_config,
            )
        except Exception as exc:
            return f"Ошибка: {exc}"

    def behavior_suggest(self) -> str:
        stats = self.storage.get_behavior_stats()
        if not stats.get("total_actions"):
            return "Пока недостаточно данных для анализа поведения."
        ai_config = self.config.get("ai", {})
        system_prompt = (
            "Ты аналитик поведения пользователя. На основе истории его действий с вакансиями "
            "(сохранения, скрытия, отклики) предложи 2-3 правила для авто-фильтрации. "
            "Например: 'ты часто скрываешь вакансии где в названии есть X — добавить X в стоп-слова' "
            "или 'ты сохраняешь все вакансии компании Y — создать алерт на Y'. "
            "Конкретно, по делу, на русском."
        )
        user_prompt = f"История действий пользователя:\n{json.dumps(stats, ensure_ascii=False, indent=2)}"
        try:
            return chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                ai_config,
            )
        except Exception as exc:
            return f"Ошибка: {exc}"

    def classify_jobs_batch(self, job_ids: list[int]) -> dict[str, Any]:
        total = len(job_ids)
        remote_count = 0
        with_salary = 0
        by_level: dict[str, int] = {"junior": 0, "middle": 0, "senior": 0}
        for job_id in job_ids:
            job = self.storage.get_job(job_id)
            if job is None:
                continue
            title_lower = (job.title or "").lower()
            desc_lower = (job.description or "").lower()
            junior_kw = ["junior", "джуниор", "начинающий", "стажер", "intern", "trainee",
                         "РјР»Р°РґС€РёР№"]
            senior_kw = ["senior", "сеньор", "ведущий", "lead", "тимлид", "tech lead",
                         "team lead", "руководитель", "architect"]
            is_junior = any(kw in title_lower for kw in junior_kw)
            is_senior = any(kw in title_lower for kw in senior_kw)
            if is_junior:
                by_level["junior"] += 1
            elif is_senior:
                by_level["senior"] += 1
            else:
                by_level["middle"] += 1
            remote_kw = ["удален", "remote", "удалён"]
            if any(kw in title_lower or kw in desc_lower for kw in remote_kw):
                remote_count += 1
            if job.salary_from is not None or job.salary_to is not None:
                with_salary += 1
        return {
            "total": total,
            "remote": remote_count,
            "with_salary": with_salary,
            "by_level": by_level,
        }

    def _collector(
        self,
        source_name: str,
        source_config: dict[str, Any],
        *,
        backend: CallbackConfigBackend | None = None,
    ):
        if source_name == "hh":
            return HHSource(source_config, backend=backend)
        if source_name == "habr":
            return HabrSource(source_config)
        if source_name == "geekjob":
            return GeekJobSource(source_config)
        if source_name == "telegram":
            return TelegramSource(source_config)
        if source_name == "getmatch":
            return GetmatchSource(source_config)
        if source_name == "relocate_me":
            return RelocateMeSource(source_config)
        if source_name in PUBLIC_BOARD_SPECS:
            return PublicJobBoardSource(
                source_config,
                source_name=source_name,
                spec=PUBLIC_BOARD_SPECS[source_name],
            )
        raise ValueError(f"Unknown source: {source_name}")
