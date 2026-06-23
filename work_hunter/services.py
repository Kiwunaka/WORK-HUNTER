from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import platform
import re
import shutil
import smtplib
import sqlite3
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from .config import (
    active_hh_config,
    active_profile,
    config_path,
    database_path,
    default_config,
    load_config,
    mask_secrets,
    preserve_masked_secrets,
    save_config,
)
from .applications.pack_builder import build_application_pack_preview, build_application_policy
from .applications.source_payload import safe_source_payload
from .agents import write_agent_orchestrator_assets
from .external_sessions import call_external_session, show_external_session
from .ai import ai_status as build_ai_status, ai_test as run_ai_test, chat_completion
from .browser_lab import (
    browser_lab_dry_run_form_fill as build_browser_lab_dry_run_form_fill,
    browser_lab_execute_dry_run_form_fill as execute_browser_lab_dry_run_form_fill,
    browser_lab_import_har as import_browser_lab_har,
    browser_lab_map_form as map_browser_lab_form,
    browser_lab_open_login as plan_browser_lab_open_login,
    browser_lab_setup as setup_browser_lab,
    browser_lab_status as build_browser_lab_status,
)
from .letters import draft_cover_letter, draft_cover_letter_ai, human_cover_letter_variants
from .models import (
    ApplyPlan,
    CalendarEvent,
    HHAgentEvent,
    HHAgentTask,
    HHCampaignItem,
    HHContact,
    HHEmployer,
    HHNegotiation,
    HHResume,
    Job,
    LetterDraft,
    Resume,
)
from .memory.candidate_profile import build_candidate_map
from .onboarding.questions import onboarding_questions as build_onboarding_questions
from .onboarding.completeness import candidate_completeness_from_facts
from .ops_import.writer import write_ops_import
from .resume_engine import (
    canonicalize_resume_text,
    diff_resume_text,
    import_resume_file,
    merge_resume_canonical_with_claims,
)
from .resumes.ats_optimizer import build_resume_variant_metadata
from .security.redaction import redact_secrets
from .security.secret_scan import find_secret_markers
from .hh_agent.approval import ApprovalQueue
from .hh_agent.apply_from_file import ApplyFromFileRow, load_apply_from_file
from .hh_agent.events import (
    detect_hh_agent_items,
    export_hh_agent_agenda_markdown,
    export_hh_agent_calendar_ics,
)
from .hh_agent.forms import detect_manual_form_url, draft_form_review, normalize_form_mode
from .hh_agent.notifications import agenda_notification_event
from .hh_agent.persona import persona_from_profile
from .hh_agent.resume_templates import (
    build_hh_batch_preset_matrix as build_resume_batch_preset_matrix,
    draft_hh_resume_payload_from_template,
)
from .interview_prep import (
    INTERVIEW_STAGES,
    PIPELINE_AUTOMATION,
    build_after_interview_assets,
    build_stage_focus,
)
from .resume_payloads import load_hh_resume_payload, validate_hh_resume_payload
from .scoring import score_job
from .sources.getmatch import parse_getmatch_offer
from .sources import (
    PUBLIC_BOARD_SOURCE_NAMES,
    PUBLIC_BOARD_SPECS,
    GeekJobSource,
    GetmatchSource,
    HHApplicantToolAdapter,
    HHSource,
    HabrSource,
    PublicJobBoardSource,
    RelocateMeSource,
    TelegramSource,
)
from .sources.registry import EXTERNAL_APPLY_EVIDENCE_REQUIREMENTS, source_status_report
from .sources.hh import HHApplyClient
from .sources.common import clean_text, fetch_url
from .storage import Storage


def _format_experience(about: dict[str, Any]) -> str:
    experience_text = ""
    for exp in about.get("experience", []):
        role = exp.get("role", "")
        project = exp.get("project", "")
        details = exp.get("details", [])
        tech = exp.get("tech", [])
        experience_text += f"\nвЂў {role} вЂ” {project}\n"
        for detail in details[:3]:
            experience_text += f"  - {detail}\n"
        if tech:
            experience_text += f"  РўРµС…РЅРѕР»РѕРіРёРё: {', '.join(tech)}\n"
    return experience_text


def _build_vacancy_candidate_prompt(job: Job, about: dict[str, Any], experience_text: str) -> str:
    return (
        f"Р’Р°РєР°РЅСЃРёСЏ:\n"
        f"- РџРѕР·РёС†РёСЏ: {job.title}\n"
        f"- РљРѕРјРїР°РЅРёСЏ: {job.company or 'РЅРµ СѓРєР°Р·Р°РЅР°'}\n"
        f"- РћРїРёСЃР°РЅРёРµ: {job.description or 'РЅРµС‚ РѕРїРёСЃР°РЅРёСЏ'}\n"
        f"- Р—Р°СЂРїР»Р°С‚Р°: {job.salary_text or 'РЅРµ СѓРєР°Р·Р°РЅР°'}\n"
        f"- РЈРґР°Р»С‘РЅРєР°: {'РґР°' if job.remote else 'РЅРµС‚/РЅРµ СѓРєР°Р·Р°РЅРѕ'}\n"
        f"- Р›РѕРєР°С†РёСЏ: {job.location or 'РЅРµ СѓРєР°Р·Р°РЅР°'}\n"
        f"\nРџСЂРѕС„РёР»СЊ РєР°РЅРґРёРґР°С‚Р°:\n"
        f"- Р РµР·СЋРјРµ: {about.get('summary', '')}\n"
        f"- РќР°РІС‹РєРё: {', '.join(about.get('all_skills', []))}\n"
        f"\nРћРїС‹С‚:\n{experience_text}"
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


def _job_dedupe_key(job: Job) -> str:
    title = re.sub(r"\s+", " ", (job.title or "").strip().lower())
    company = re.sub(r"\s+", " ", (job.company or "").strip().lower())
    if title and company:
        return f"title-company:{title}|{company}"
    if job.url:
        return f"url:{job.url.strip().lower().split('?')[0]}"
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


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _count_campaign_item(counts: dict[str, int], item: HHCampaignItem) -> None:
    if item.status in counts:
        counts[item.status] += 1
    else:
        counts["error"] += 1


def _with_replay_hashes(data: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(data)
    for key in ("prompt", "output"):
        value = enriched.get(key)
        if value is not None and f"{key}_hash" not in enriched:
            digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
            enriched[f"{key}_hash"] = f"sha256:{digest}"
        enriched.pop(key, None)
    return enriched


def _browser_lab_default_hosts(source: str, config: dict[str, Any]) -> set[str]:
    source_config = (config.get("sources") or {}).get(source) or {}
    hosts: set[str] = set()
    for key in ("base_url", "api_base_url"):
        value = str(source_config.get(key) or "")
        host = urllib.parse.urlsplit(value).hostname
        if host:
            hosts.add(host)
    if source == "hh":
        hosts.add("hh.ru")
    if not hosts and source:
        compact = source.replace("_", "")
        hosts.add(f"{compact}.ru" if source in {"hirehi"} else f"{compact}.work" if source == "jabka" else source)
    return hosts


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


HH_DIRECT_APPLY_HARD_BLOCKERS = {
    "test_required": "test_required",
    "questions_required": "questions_required",
    "question_required": "questions_required",
    "manual_questions_required": "questions_required",
    "captcha_required": "captcha_or_challenge",
    "challenge_required": "captcha_or_challenge",
    "captcha_or_challenge": "captcha_or_challenge",
    "archived": "archived",
    "vacancy_archived": "archived",
    "already_applied": "already_applied",
    "has_relations": "has_relations",
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
EXTERNAL_APPLY_CERTIFICATION_SOURCE_NAMES = ["geekjob", "habr", "getmatch", "hirehi", "careerspace", "jabka"]
PUBLIC_BOARD_APPLY_FORM_SOURCE_NAMES = {"hirehi", "careerspace", "another_it", "jabka"}


def _source_maturity_level(source_name: str, capabilities: dict[str, str]) -> int:
    if source_name == "hh" and capabilities.get("apply") == "official_api":
        return 6
    apply_capability = capabilities.get("apply", "")
    detail_capability = capabilities.get("detail", "")
    search_capability = capabilities.get("search", "")
    if apply_capability == "personal_auth_recon":
        return 2
    if apply_capability == "external_page":
        return 2 if detail_capability not in {"unknown", ""} else 1
    if search_capability and detail_capability:
        return 1
    return 0


def _relevant_candidate_facts(job: Job, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    text = f"{job.title} {job.description}".lower()
    relevant: list[dict[str, Any]] = []
    for fact in facts:
        value = str(fact.get("value") or "")
        tokens = [
            token.lower()
            for token in re.findall(r"[A-Za-zА-Яа-я0-9+#.]{3,}", value)
        ]
        if any(token in text for token in tokens):
            relevant.append(fact)
    return relevant


def _application_pack_policy_reasons(
    job: Job,
    source_payload: dict[str, Any],
    campaign_policy: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if "enabled" in campaign_policy and not campaign_policy.get("enabled"):
        reasons.append("campaign_disabled")
    if "real_apply" in campaign_policy and not campaign_policy.get("real_apply"):
        reasons.append("real_apply_disabled")
    if (
        campaign_policy.get("require_resume_variant", True)
        and not source_payload.get("resume_variant_id")
        and not source_payload.get("resume_id")
    ):
        reasons.append("resume_variant_missing")
    if not source_payload.get("cover_letter_present"):
        reasons.append("cover_letter_missing")
    if str(source_payload.get("form_signature") or "").lower() == "unknown":
        reasons.append("unknown_form")
    if source_payload.get("captcha") or source_payload.get("challenge"):
        reasons.append("captcha_or_challenge")
    if source_payload.get("test_required") or source_payload.get("has_test"):
        reasons.append("test_required")
    min_score = int(campaign_policy.get("min_score") or 0)
    score = job.score.total_score if job.score else 0
    if min_score and score < min_score:
        reasons.append("below_min_score")
    return list(dict.fromkeys(reasons))


def _external_form_source_payload(form: dict[str, Any]) -> dict[str, Any]:
    fields = list(form.get("fields") or [])
    return {
        "form_url": str(form.get("form_url") or form.get("url") or ""),
        "form_signature": str(form.get("form_signature") or ("known" if fields else "unknown")),
        "captcha": bool(form.get("captcha")),
        "challenge": bool(form.get("challenge")),
        "test_required": bool(form.get("test_required") or form.get("has_test")),
        "field_count": len(fields),
    }


def _external_apply_form_from_dry_run_event(data: dict[str, Any], *, fallback_url: str) -> dict[str, Any]:
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
    raw_result = plan.get("raw_result") if isinstance(plan.get("raw_result"), dict) else {}
    apply_meta = raw_result.get("apply") if isinstance(raw_result.get("apply"), dict) else {}
    form = apply_meta.get("form") if isinstance(apply_meta.get("form"), dict) else {}
    if form.get("fields"):
        return dict(form)
    dry_run = data.get("dry_run") if isinstance(data.get("dry_run"), dict) else {}
    return {
        "form_url": str(dry_run.get("form_url") or data.get("external_url") or fallback_url),
        "fields": [],
    }


def _external_apply_config(config: dict[str, Any], source: str) -> dict[str, Any]:
    source_config = (config.get("sources") or {}).get(source) or {}
    external_apply = source_config.get("external_apply") or {}
    return dict(external_apply) if isinstance(external_apply, dict) else {}


def _external_apply_target_fingerprint(external_apply: dict[str, Any]) -> str:
    target = {
        "session": str(external_apply.get("session") or ""),
        "url": str(external_apply.get("url") or ""),
        "method": str(external_apply.get("method") or "POST").upper(),
        "payload_template": external_apply.get("payload_template") or {},
    }
    return json.dumps(mask_secrets(target), ensure_ascii=False, sort_keys=True, default=str)


def _external_apply_level(external_apply: dict[str, Any]) -> int:
    if not external_apply.get("certified"):
        return 0
    try:
        return max(0, min(6, int(external_apply.get("level") or external_apply.get("maturity_level") or 0)))
    except (TypeError, ValueError):
        return 0


def _render_external_apply_template(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return value.format_map(_SafeFormatDict({key: str(val) for key, val in context.items()}))
    if isinstance(value, list):
        return [_render_external_apply_template(item, context) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _render_external_apply_template(item, context)
            for key, item in value.items()
        }
    return value


def _external_apply_payload_context(
    job: Job,
    *,
    resume_variant: dict[str, Any] | None,
    cover_letter: str,
    short_message: str,
    extra_answers: dict[str, str] | None,
) -> dict[str, Any]:
    resume = resume_variant or {}
    return {
        "job_id": job.id or "",
        "source": job.source,
        "source_id": job.source_id,
        "job_url": job.url,
        "title": job.title,
        "company": job.company,
        "cover_letter": cover_letter,
        "short_message": short_message,
        "resume_id": resume.get("id") or "",
        "resume_body": resume.get("body") or "",
        **(extra_answers or {}),
    }


def _dry_run_certification_context(data: dict[str, Any]) -> dict[str, Any]:
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
    raw_result = plan.get("raw_result") if isinstance(plan.get("raw_result"), dict) else {}
    apply_meta = raw_result.get("apply") if isinstance(raw_result.get("apply"), dict) else {}
    form_meta = apply_meta.get("form") if isinstance(apply_meta.get("form"), dict) else {}
    dry_run = data.get("dry_run") if isinstance(data.get("dry_run"), dict) else {}
    form_url = (
        dry_run.get("form_url")
        or form_meta.get("form_url")
        or data.get("external_url")
        or plan.get("external_url")
        or ""
    )
    host = urllib.parse.urlsplit(str(form_url or "")).hostname or ""
    form_signature = (
        apply_meta.get("form_signature")
        or form_meta.get("form_signature")
        or dry_run.get("form_signature")
        or ""
    )
    detector = apply_meta.get("detector") or apply_meta.get("adapter_version") or ""
    context: dict[str, Any] = {}
    if form_url:
        context["form_url"] = str(form_url)
    if host:
        context["host"] = host
    if form_signature:
        context["form_signature"] = str(form_signature)
    if detector:
        context["detector"] = str(detector)
    return context


def _source_certification_context(
    source: str,
    *,
    session: str,
    url: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    dry_run = evidence.get("dry_run") if isinstance(evidence.get("dry_run"), dict) else {}
    target_host = urllib.parse.urlsplit(str(url or "")).hostname or ""
    return mask_secrets(
        {
            "source": source,
            "session": session,
            "target_url": url,
            "target_host": target_host,
            "dry_run_host": dry_run.get("host") or "",
            "dry_run_form_url": dry_run.get("form_url") or "",
            "form_signature": dry_run.get("form_signature") or "",
            "detector": dry_run.get("detector") or "",
        }
    )


def _best_external_apply_endpoint(recon: dict[str, Any]) -> dict[str, Any] | None:
    endpoints = recon.get("endpoints") if isinstance(recon.get("endpoints"), list) else []
    candidates = [
        endpoint
        for endpoint in endpoints
        if "apply" in set(endpoint.get("tags") or [])
        and str(endpoint.get("method") or "GET").upper() in {"POST", "PUT", "PATCH"}
    ]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda endpoint: (
            0 if "api" in set(endpoint.get("tags") or []) else 1,
            0 if int(endpoint.get("status") or 0) in range(200, 300) else 1,
            str(endpoint.get("url") or ""),
        ),
    )[0]


def _safe_external_apply_endpoint_view(endpoint: dict[str, Any]) -> dict[str, Any]:
    return mask_secrets(
        {
            "method": str(endpoint.get("method") or "POST").upper(),
            "url": str(endpoint.get("url") or ""),
            "status": endpoint.get("status") or 0,
            "tags": list(endpoint.get("tags") or []),
            "response_mime": str(endpoint.get("response_mime") or ""),
        }
    )


def _external_apply_payload_template_from_post_data(post_data: str) -> tuple[dict[str, Any], list[str]]:
    try:
        parsed = json.loads(str(post_data or ""))
    except json.JSONDecodeError:
        return {}, []
    if not isinstance(parsed, dict):
        return {}, []
    template: dict[str, Any] = {}
    unknown: list[str] = []
    for key in parsed:
        mapped = _external_apply_payload_template_value(str(key))
        if mapped:
            template[str(key)] = mapped
        else:
            unknown.append(str(key))
    return template, unknown


def _external_apply_payload_template_value(key: str) -> str:
    lowered = key.lower()
    if any(part in lowered for part in ("job", "vacancy", "offer", "position")):
        return "{source_id}"
    if any(part in lowered for part in ("cover", "letter", "message", "response", "comment", "motivation")):
        return "{cover_letter}"
    if "short" in lowered:
        return "{short_message}"
    if any(part in lowered for part in ("resume", "cv")):
        return "{resume_id}"
    return ""


def _external_readiness_block_reason(readiness: dict[str, Any], *, fallback: str) -> str:
    blockers = set(readiness.get("blockers") or [])
    if "external_apply_session_missing" in blockers:
        return "external_apply_session_missing"
    if "external_apply_url_missing" in blockers:
        return "external_apply_url_missing"
    return fallback


def _certification_evidence_present(value: Any) -> bool:
    if value is True:
        return True
    if value in (False, None, ""):
        return False
    if isinstance(value, dict):
        status = str(value.get("status") or value.get("result") or "").strip().lower()
        if status in {"ok", "pass", "passed", "ready", "complete", "completed", "dry_run_ready", "executed_dry_run"}:
            return True
        return any(value.get(key) for key in ("id", "event_id", "run_id", "scan_id", "command", "path", "hash"))
    if isinstance(value, str):
        lowered = value.strip().lower()
        return bool(lowered) and lowered not in {"false", "no", "fail", "failed", "missing"}
    return bool(value)


def _missing_certification_evidence(requirement: str) -> dict[str, Any]:
    return {"status": "missing", "requirement": requirement}


def _source_certification_action(source: str, requirement: str) -> dict[str, Any]:
    source_name = str(source or "").strip().lower().replace("-", "_")
    requirement_name = str(requirement or "").strip().lower()
    actions: dict[str, dict[str, str]] = {
        "session": {
            "surface": "browser_lab",
            "command": f"work-hunter browser login {source_name}",
            "description": "Create or refresh the local browser session, then export/import a HAR if the source needs captured cookies.",
        },
        "url": {
            "surface": "api_recon",
            "command": f"work-hunter source external-apply-from-har {source_name} <session.har> --host <host>",
            "description": "Map the authenticated external apply endpoint from a redacted HAR before configuring a real target.",
        },
        "tests": {
            "surface": "pytest",
            "command": "python -m pytest tests\\test_source_adapter_registry.py tests\\test_external_apply_executor.py tests\\test_browser_session_lab.py -q",
            "description": "Run the certification safety tests, then record the exact command as tests evidence.",
        },
        "replay": {
            "surface": "replay",
            "command": f"GET /api/replay/runs/<run_id>?source={source_name}",
            "description": "Attach a replay timeline that proves the certification dry run path was exercised without submission.",
        },
        "redaction": {
            "surface": "redaction",
            "command": f"work-hunter source redaction-scan {source_name} --payload <payload.json> --text <sample>",
            "description": "Scan captured payloads and text samples so secrets are masked before any evidence is stored.",
        },
        "dry_run": {
            "surface": "browser_lab",
            "command": "POST /api/jobs/<job_id>/external-apply/dry-run",
            "description": "Execute a no-submit dry run for a representative job and store the replay event as dry_run evidence.",
        },
    }
    action = actions.get(
        requirement_name,
        {
            "surface": "manual",
            "command": f"work-hunter source certification-evidence {source_name} --evidence <evidence.json>",
            "description": "Record explicit certification evidence for this requirement.",
        },
    )
    return {
        "requirement": requirement_name,
        "safe": True,
        **action,
    }


def _latest_event_matching(events: list[dict[str, Any]], event_type: str) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("event_type") == event_type:
            return event
    return None


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


def _parse_pipeline_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _pipeline_datetime(value: str | None, *, fallback: str | None = None) -> datetime:
    parsed = _parse_pipeline_datetime(value)
    if parsed is not None:
        return parsed
    parsed_fallback = _parse_pipeline_datetime(fallback)
    if parsed_fallback is not None:
        return parsed_fallback
    return datetime.now().astimezone().replace(microsecond=0)


def _pipeline_due_at(value: str | None, days_after: int, *, fallback: str | None = None) -> str:
    return (_pipeline_datetime(value, fallback=fallback) + timedelta(days=max(1, int(days_after)))).isoformat()


def _candidate_fact_text(value: Any) -> str:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return clean_text("; ".join(_candidate_fact_text(item) for item in value if item))
    if isinstance(value, dict):
        parts = [str(item) for item in value.values() if item]
        return clean_text("; ".join(parts))
    return clean_text(str(value or ""))


def _candidate_profile_constraints(profile: dict[str, Any]) -> str:
    parts: list[str] = []
    locations = [str(item).strip() for item in profile.get("locations") or [] if str(item).strip()]
    stop_words = [str(item).strip() for item in profile.get("stop_words") or [] if str(item).strip()]
    salary_min = int(profile.get("salary_min") or 0)
    if locations:
        parts.append(f"locations: {', '.join(locations)}")
    if bool(profile.get("remote_only", False)):
        parts.append("remote only")
    elif any(item.casefold() == "remote" for item in locations):
        parts.append("remote preferred")
    if salary_min:
        parts.append(f"salary_min: {salary_min}")
    if stop_words:
        parts.append(f"avoid: {', '.join(stop_words)}")
    return "; ".join(parts)


def _dedup_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        cleaned = str(item or "").strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _pipeline_stage(job_status: str, application_status: str, events: list[CalendarEvent]) -> str:
    app_status = (application_status or "").lower()
    status = (job_status or "").lower()
    if app_status in {"rejected", "declined"} or status in {"rejected", "declined", "hidden"}:
        return "closed"
    if app_status == "offer" or status == "offer":
        return "offer"
    if any(event.event_type == "interview" for event in events):
        return "interview_scheduled"
    if app_status in {"interview", "tech_interview", "phone_screen"} or status in {
        "interview",
        "tech_interview",
        "phone_screen",
    }:
        return "interview"
    if app_status in {"response", "viewed"} or status in {"response", "viewed"}:
        return "response_received"
    if app_status == "external_manual_ready" or status == "external_manual_ready":
        return "manual_submit_ready"
    if app_status or status == "applied":
        return "applied_waiting"
    return "not_applied"


def _stage_label(stage: str) -> str:
    labels = {
        "not_applied": "Not applied",
        "manual_submit_ready": "Manual submit ready",
        "applied_waiting": "Applied, waiting",
        "response_received": "Reply received",
        "interview": "Interview flow",
        "interview_scheduled": "Interview scheduled",
        "offer": "Offer stage",
        "closed": "Closed",
    }
    return labels.get(stage, stage.replace("_", " ").title())


def _tech_stack_from_job(job: Job, profile: dict[str, Any]) -> list[str]:
    text = f"{job.title} {job.description}".lower()
    profile_skills = [
        str(item)
        for item in (profile.get("must_have_skills") or []) + (profile.get("nice_to_have_skills") or [])
    ]
    common = [
        "Python",
        "FastAPI",
        "Django",
        "Flask",
        "PostgreSQL",
        "Redis",
        "Docker",
        "Kubernetes",
        "SQL",
        "API",
        "Async",
        "Celery",
        "Kafka",
        "React",
        "TypeScript",
    ]
    candidates = profile_skills + common
    matched = [skill for skill in candidates if skill and skill.lower() in text]
    if not matched:
        matched = profile_skills[:5]
    return _dedup_keep_order(matched)[:10]


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


def _module_status(name: str) -> dict[str, Any]:
    found = importlib.util.find_spec(name) is not None
    return {"status": "ok" if found else "missing", "module": name}


def _playwright_chromium_status() -> dict[str, Any]:
    setup_command = "python -m playwright install chromium"
    if importlib.util.find_spec("playwright") is None:
        return {
            "status": "missing",
            "reason": "playwright_missing",
            "setup_command": 'pip install -e ".[browser]"',
        }
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
    except Exception as exc:
        return {
            "status": "warning",
            "reason": type(exc).__name__,
            "error": str(exc)[:200],
            "setup_command": setup_command,
        }
    if not executable.exists():
        return {
            "status": "missing",
            "reason": "chromium_missing",
            "executable": str(executable),
            "setup_command": setup_command,
        }
    return {"status": "ok", "executable": str(executable)}


def _cli_tool_status(command: str) -> dict[str, Any]:
    path = shutil.which(command)
    return {
        "status": "ok" if path else "missing",
        "command": command,
        "path": path or "",
    }


def _legacy_onboarding_question(question_id: str) -> dict[str, str] | None:
    legacy = {
        "skills": {
            "id": "skills",
            "title": "Skills",
            "prompt": "List strong technical skills that can be truthfully used in resumes.",
            "category": "skills",
        },
        "constraints": {
            "id": "constraints",
            "title": "Constraints",
            "prompt": "List salary, location, remote, schedule, and role constraints.",
            "category": "constraints",
        },
    }
    return legacy.get(str(question_id or ""))


class WorkHunter:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else Path.cwd()
        self.config_path = config_path(self.root)
        self.config = load_config(self.config_path)
        self.storage = Storage(database_path(self.root))

    def init(self, *, overwrite: bool = False) -> Path:
        if overwrite or not self.config_path.exists():
            save_config(self.config_path, self.config)
        return self.config_path

    def init_report(
        self,
        *,
        check: bool = False,
        refresh_docs: bool = False,
        with_ai: bool = False,
        with_browser: bool = False,
        import_wo: str | None = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        path = self.config_path if dry_run else self.init(overwrite=force)
        docs: list[str] = []
        agent_assets: dict[str, Any] | None = None
        if refresh_docs:
            if not dry_run:
                docs = self._write_product_docs()
            agent_assets = write_agent_orchestrator_assets(self.root, dry_run=dry_run)
        report: dict[str, Any] = {
            "status": "ok",
            "config_path": str(path),
            "check": bool(check),
            "dry_run": bool(dry_run),
            "force": bool(force),
            "windows_only": True,
            "ui": {
                "host": self.config.get("ui", {}).get("host", "127.0.0.1"),
                "port": self.config.get("ui", {}).get("port", 8787),
            },
            "docs": docs,
            "readiness": self.setup_readiness(),
        }
        if agent_assets is not None:
            report["agents"] = agent_assets
        if with_ai:
            report["ai"] = self.ai_status()
        if with_browser:
            report["browser"] = {
                "playwright_profile_root": str(self.root / ".work-hunter" / "browser-profiles"),
                "dry_run_required": True,
            }
        if import_wo:
            report["import_wo"] = write_ops_import(import_wo, self.root, dry_run=dry_run)
        return report

    def save_config(self, config: dict[str, Any]) -> None:
        self.config = preserve_masked_secrets(self.config, config)
        save_config(self.config_path, self.config)

    def reset_config_defaults(self) -> None:
        self.config = default_config()
        save_config(self.config_path, self.config)

    def _write_product_docs(self) -> list[str]:
        product_dir = self.root / "docs" / "product"
        product_dir.mkdir(parents=True, exist_ok=True)
        docs = {
            "WORK_HUNTER_VISION.md": (
                "# Work Hunter Vision\n\n"
                "Work Hunter is a private Windows-only local-first command center for one owner.\n"
                "It is not a SaaS product, public bot, or mass spam tool. It runs on the owner's "
                "machine, uses the owner's local accounts, and keeps operational data in local files "
                "and SQLite.\n\n"
                "The core product path is onboarding, candidate map, truthful resumes, vacancy search, "
                "fit scoring, human cover letters, application preview, real auto-apply when a source "
                "is certified, replay, reply tracking, and interview prep.\n\n"
                "The main feature is real auto-apply, but it is always controlled by source maturity, "
                "campaign policy, preview, audit, and safety gates.\n"
            ),
            "REQUIREMENTS.md": (
                "# Requirements\n\n"
                "- Windows 10/11 only.\n"
                "- CLI and local web UI bind to 127.0.0.1 by default.\n"
                "- Real apply is policy-gated, audited, and replayable.\n"
                "- Codex/OpenCode auth cache files are never read, copied, logged, or exported.\n"
                "- AI routes call official local runtimes or explicit API endpoints without scraping credentials.\n"
                "- Candidate claims must be backed by confirmed facts before generated resume variants use them.\n"
                "- Application packs must show resume variant, cover letter, short message, payload summary, risks, and preview.\n"
                "- External sources must stay below real auto-apply until certified with tests, replay, redaction, and dry-run evidence.\n"
            ),
            "SOURCE_PRIORITY.md": (
                "# Source Priority\n\n"
                "1. HH\n"
                "2. GeekJob\n"
                "3. Habr\n"
                "4. Getmatch\n"
                "5. hirehi\n"
                "6. careerspace\n"
                "7. jabka.work\n\n"
                "HH is the primary full-cycle source because it has the strongest official API, "
                "resume, negotiation, and apply surface. GeekJob, Habr, Getmatch, hirehi, "
                "careerspace, and jabka.work are priority external sources that must pass source "
                "maturity before campaign auto-apply.\n"
            ),
            "SAFETY_CONTRACT.md": (
                "# Safety Contract\n\n"
                "- Never read Codex or OpenCode auth cache files.\n"
                "- Never log tokens, cookies, auth headers, session ids, or API keys.\n"
                "- Unknown forms, tests, captcha, and challenges block real apply.\n"
                "- Non-certified source adapters cannot auto-apply.\n"
                "- Real apply requires an enabled campaign policy, min_score pass, caps, valid session, preview, audit, replay, and kill switch off.\n"
                "- The kill switch must stop planning and execution immediately.\n"
                "- Dry-run and preview paths must never submit an application.\n"
            ),
        }
        written: list[str] = []
        for name, content in docs.items():
            path = product_dir / name
            path.write_text(content, encoding="utf-8")
            written.append(str(path))
        return written

    def ai_status(self) -> dict[str, Any]:
        return build_ai_status(self.config.get("ai", {}))

    def ai_test(self, *, route: str | None = None, prompt: str = "ping", dry_run: bool = False) -> dict[str, Any]:
        result = run_ai_test(
            self.config.get("ai", {}),
            route=route,
            prompt=prompt,
            dry_run=dry_run,
        )
        return mask_secrets(result)

    def doctor_report(self) -> dict[str, Any]:
        checks = {
            "python": {
                "status": "ok" if sys.version_info >= (3, 11) else "error",
                "version": platform.python_version(),
                "executable": sys.executable,
            },
            "sqlite": {
                "status": "ok",
                "version": sqlite3.sqlite_version,
                "database_path": str(database_path(self.root)),
            },
            "config": {
                "status": "ok" if self.config_path.exists() else "missing",
                "path": str(self.config_path),
            },
            "data_dir": {
                "status": "ok" if self.config_path.parent.exists() else "missing",
                "path": str(self.config_path.parent),
            },
            "ui_bind": {
                "status": "ok" if self.config.get("ui", {}).get("host", "127.0.0.1") == "127.0.0.1" else "warning",
                "host": self.config.get("ui", {}).get("host", "127.0.0.1"),
                "port": self.config.get("ui", {}).get("port", 8787),
            },
        }
        status = "ok" if all(item["status"] in {"ok", "missing"} for item in checks.values()) else "warning"
        return {
            "status": status,
            "windows_only": True,
            "platform": platform.platform(),
            "checks": checks,
        }

    def setup_readiness(self) -> dict[str, Any]:
        doctor = self.doctor_report()
        checks = doctor["checks"]
        ai = self.ai_status()
        profile_root = self.root / ".work-hunter" / "browser-profiles"
        hh_agent = self.config.get("hh_agent") or {}
        return {
            "environment": {
                "windows_only": True,
                "platform": platform.platform(),
                "python": checks["python"]["status"],
                "python_version": checks["python"]["version"],
                "sqlite": checks["sqlite"]["status"],
                "sqlite_version": checks["sqlite"]["version"],
                "config": checks["config"]["status"],
                "data_dir": checks["data_dir"]["status"],
                "ui_bind": checks["ui_bind"],
                "dependencies": {
                    "requests": _module_status("requests"),
                    "mcp": _module_status("mcp"),
                    "starlette": _module_status("starlette"),
                },
            },
            "ai_runtime": {
                **ai,
                "cli_tools": {
                    "codex": _cli_tool_status("codex"),
                    "opencode": _cli_tool_status("opencode"),
                },
            },
            "browser": {
                "profile_root": str(profile_root),
                "profile_root_exists": profile_root.exists(),
                "playwright": _module_status("playwright"),
                "chromium": _playwright_chromium_status(),
                "sessions": {
                    "profile_root": str(profile_root),
                    "status": "ready" if profile_root.exists() else "missing",
                },
            },
            "candidate": self.candidate_completeness(),
            "sources": self.source_capabilities(),
            "implementation": self.implementation_readiness(),
            "safety": {
                "redaction_scan": "available",
                "audit_db": "ok" if database_path(self.root).exists() else "missing",
                "kill_switch": "paused" if bool(hh_agent.get("paused", False)) else "active",
            },
        }

    def implementation_readiness(self) -> dict[str, Any]:
        return {
            "source_campaign_apply": {
                "status": "ready_for_certified_sources",
                "ready_sources": ["hh", "certified_external_sources"],
                "ready": [
                    "hh_campaign_runner",
                    "external_campaign_runner",
                    "source_maturity_policy",
                    "external_apply_evidence_gate",
                    "source_certification_audit",
                    "source_certification_promotion",
                    "source_certification_matrix",
                    "source_external_apply_target_config",
                ],
                "gaps": [],
                "operational_requirements": ["per_source_certification_required"],
            },
            "browser_session_lab": {
                "status": "ready",
                "ready": ["profile_paths", "har_import", "form_mapping", "dry_run_plan", "playwright_fill_screenshot_executor"],
                "gaps": [],
                "operational_requirements": ["manual_login_or_har_session_required_for_authenticated_sources"],
            },
            "ai_runtime": {
                "status": "ready",
                "ready": ["runtime_registry_package", "route_status", "direct_chat_completion", "cli_route_detection"],
                "gaps": [],
            },
            "resume_exports": {
                "status": "ready",
                "ready": ["markdown_export", "docx_export", "pdf_export", "pdf_docx_import", "ats_metadata"],
                "gaps": [],
            },
        }

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
        jobs = self.storage.list_jobs(limit=limit)
        count = 0
        for job in jobs:
            score = score_job(job, active_profile(self.config))
            score.job_id = job.id
            self.storage.save_score(score)
            count += 1
        return count

    def source_capabilities(self) -> dict[str, dict[str, Any]]:
        return source_status_report(self.config.get("sources") or {})

    def source_certification_audit(
        self,
        source: str,
        *,
        level: int = 5,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source_name = str(source or "").strip().lower().replace("-", "_")
        requested_level = max(0, min(6, int(level or 0)))
        external_apply = _external_apply_config(self.config, source_name)
        configured_evidence = {}
        for key in ("evidence", "certification"):
            value = external_apply.get(key)
            if isinstance(value, dict):
                configured_evidence.update(mask_secrets(value))
        local_evidence = self._source_certification_local_evidence(source_name)
        provided_evidence = mask_secrets(dict(evidence or {}))
        requirement_evidence = {
            requirement: _missing_certification_evidence(requirement)
            for requirement in ("tests", "replay", "redaction", "dry_run")
        }
        requirement_evidence.update(
            {
                key: value
                for key, value in configured_evidence.items()
                if key == "tests" and key in requirement_evidence
            }
        )
        requirement_evidence.update(local_evidence)
        requirement_evidence.update(
            {
                key: value
                for key, value in provided_evidence.items()
                if key == "tests" and key in requirement_evidence
            }
        )
        missing: list[str] = []
        session = str(external_apply.get("session") or "")
        url = str(external_apply.get("url") or "")
        if not session:
            missing.append("session")
        if not url:
            missing.append("url")
        for requirement, value in requirement_evidence.items():
            if not _certification_evidence_present(value):
                missing.append(requirement)
        ready = not missing
        certification_context = _source_certification_context(
            source_name,
            session=session,
            url=url,
            evidence=requirement_evidence,
        )
        promotion_payload = None
        if ready:
            promotion_external_apply = {
                **external_apply,
                "certified": requested_level >= 5,
                "level": requested_level,
                "session": session,
                "url": url,
                "evidence": requirement_evidence,
                "certification_context": certification_context,
            }
            promotion_payload = {
                "source": source_name,
                "external_apply": promotion_external_apply,
            }
        return {
            "source": source_name,
            "requested_level": requested_level,
            "ready": ready,
            "missing": missing,
            "evidence": {
                "session": {"status": "present", "name": session} if session else _missing_certification_evidence("session"),
                "url": {"status": "present", "url": url} if url else _missing_certification_evidence("url"),
                **requirement_evidence,
            },
            "certification_context": certification_context,
            "promotion_payload": promotion_payload,
            "readiness": self.source_capabilities().get(source_name) or {},
        }

    def source_certification_matrix(
        self,
        *,
        level: int = 5,
        sources: list[str] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        requested_level = max(0, min(6, int(level or 0)))
        raw_sources = sources or EXTERNAL_APPLY_CERTIFICATION_SOURCE_NAMES
        source_names: list[str] = []
        for source in raw_sources:
            source_name = str(source or "").strip().lower().replace("-", "_")
            if source_name and source_name not in source_names:
                source_names.append(source_name)

        evidence_by_source = evidence or {}
        audits: dict[str, Any] = {}
        missing_by_source: dict[str, list[str]] = {}
        promotion_payloads: dict[str, Any] = {}
        ready_count = 0
        for source_name in source_names:
            source_evidence = evidence_by_source.get(source_name)
            if source_evidence is None:
                source_evidence = evidence_by_source.get(source_name.replace("_", "-"))
            audit = self.source_certification_audit(
                source_name,
                level=requested_level,
                evidence=dict(source_evidence) if isinstance(source_evidence, dict) else {},
            )
            audits[source_name] = audit
            if audit.get("ready"):
                ready_count += 1
                if audit.get("promotion_payload"):
                    promotion_payloads[source_name] = audit["promotion_payload"]
            else:
                missing_by_source[source_name] = list(audit.get("missing") or [])

        total = len(source_names)
        blocked_count = total - ready_count
        status = "ready" if blocked_count == 0 else "partial" if ready_count else "blocked"
        return {
            "status": status,
            "requested_level": requested_level,
            "sources": audits,
            "summary": {"total": total, "ready": ready_count, "blocked": blocked_count},
            "missing_by_source": missing_by_source,
            "promotion_payloads": promotion_payloads,
        }

    def source_certification_plan(
        self,
        *,
        level: int = 5,
        sources: list[str] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        matrix = self.source_certification_matrix(level=level, sources=sources, evidence=evidence)
        planned_sources: dict[str, Any] = {}
        for source_name, audit in (matrix.get("sources") or {}).items():
            missing = list(audit.get("missing") or [])
            planned_sources[source_name] = {
                "ready": bool(audit.get("ready")),
                "missing": missing,
                "actions": [
                    _source_certification_action(source_name, requirement)
                    for requirement in missing
                ],
                "promotion_payload_ready": bool(audit.get("promotion_payload")),
            }
        return {
            **matrix,
            "sources": planned_sources,
            "matrix": matrix,
        }

    def record_source_certification_evidence(
        self,
        source: str,
        *,
        evidence: dict[str, Any] | None = None,
        level: int = 5,
    ) -> dict[str, Any]:
        source_name = str(source or "").strip().lower().replace("-", "_")
        raw_evidence = evidence or {}
        recorded_evidence = {
            key: mask_secrets(value)
            for key, value in raw_evidence.items()
            if key in EXTERNAL_APPLY_EVIDENCE_REQUIREMENTS and isinstance(value, dict)
        }
        config = dict(self.config)
        sources = dict(config.get("sources") or {})
        source_config = dict(sources.get(source_name) or {})
        external_apply = dict(source_config.get("external_apply") or {})
        existing_evidence = dict(external_apply.get("evidence") or {})
        existing_evidence.update(recorded_evidence)
        external_apply["evidence"] = existing_evidence
        source_config["external_apply"] = external_apply
        sources[source_name] = source_config
        config["sources"] = sources
        self.save_config(config)
        audit = self.source_certification_audit(source_name, level=level)
        return {
            "status": "recorded" if recorded_evidence else "noop",
            "source": source_name,
            "recorded": sorted(recorded_evidence),
            "evidence": recorded_evidence,
            "audit": audit,
            "capabilities": self.source_capabilities().get(source_name) or {},
        }

    def configure_source_external_apply_target(
        self,
        source: str,
        *,
        session: str = "",
        url: str = "",
        method: str = "POST",
        payload_template: dict[str, Any] | None = None,
        level: int = 5,
    ) -> dict[str, Any]:
        source_name = str(source or "").strip().lower().replace("-", "_")
        normalized_method = str(method or "POST").strip().upper() or "POST"
        template = mask_secrets(payload_template or {})
        config = dict(self.config)
        sources = dict(config.get("sources") or {})
        source_config = dict(sources.get(source_name) or {})
        external_apply = dict(source_config.get("external_apply") or {})
        previous_target = _external_apply_target_fingerprint(external_apply)
        was_certified = bool(external_apply.get("certified"))
        external_apply.update(
            {
                "session": str(session or "").strip(),
                "url": str(url or "").strip(),
                "method": normalized_method,
                "payload_template": template,
            }
        )
        certification_invalidated = was_certified and previous_target != _external_apply_target_fingerprint(external_apply)
        if certification_invalidated:
            for key in (
                "certified",
                "evidence",
                "certification",
                "certification_context",
                "certification_hash",
            ):
                external_apply.pop(key, None)
        source_config["external_apply"] = external_apply
        sources[source_name] = source_config
        config["sources"] = sources
        self.save_config(config)
        audit = self.source_certification_audit(source_name, level=level)
        target = {
            "session": external_apply.get("session") or "",
            "url": external_apply.get("url") or "",
            "method": external_apply.get("method") or "POST",
            "payload_template": external_apply.get("payload_template") or {},
        }
        return {
            "status": "configured",
            "source": source_name,
            "target": mask_secrets(target),
            "certification_invalidated": certification_invalidated,
            "audit": audit,
            "capabilities": self.source_capabilities().get(source_name) or {},
        }

    def record_source_redaction_scan(
        self,
        source: str,
        *,
        payload: dict[str, Any] | None = None,
        text: str = "",
        level: int = 5,
    ) -> dict[str, Any]:
        source_name = str(source or "").strip().lower().replace("-", "_")
        raw_scan = {"payload": payload or {}, "text": str(text or "")}
        raw_serialized = json.dumps(raw_scan, ensure_ascii=False, sort_keys=True, default=str)
        redacted = mask_secrets(redact_secrets(raw_scan))
        redacted_serialized = json.dumps(redacted, ensure_ascii=False, sort_keys=True, default=str)
        markers = sorted(find_secret_markers(raw_serialized))
        changed = raw_serialized != redacted_serialized or bool(markers)
        event_id = self.record_replay_event(
            source=source_name,
            event_type="external_apply_redaction_scan",
            title="External apply redaction scan",
            summary="passed",
            data={
                "status": "passed",
                "changed": changed,
                "markers": markers,
                "redacted": redacted,
            },
        )
        audit = self.source_certification_audit(source_name, level=level)
        return {
            "status": "recorded",
            "source": source_name,
            "event_id": event_id,
            "findings": {"changed": changed, "markers": markers},
            "redacted": redacted,
            "audit": audit,
            "capabilities": self.source_capabilities().get(source_name) or {},
        }

    def _source_certification_local_evidence(self, source: str) -> dict[str, Any]:
        events = self.storage.list_replay_events(source=source)
        evidence: dict[str, Any] = {}
        dry_run = _latest_event_matching(
            [
                event
                for event in events
                if event.get("event_type") == "external_apply_dry_run"
                and (event.get("data") or {}).get("status") == "dry_run_ready"
                and (event.get("data") or {}).get("submit") is False
            ],
            "external_apply_dry_run",
        )
        if dry_run is None:
            dry_run = _latest_event_matching(
                [
                    event
                    for event in events
                    if event.get("event_type") == "browser_lab_form_execute_dry_run"
                    and (event.get("data") or {}).get("status") == "executed_dry_run"
                    and (event.get("data") or {}).get("submit") is False
                    and ((event.get("data") or {}).get("executor") or {}).get("status") == "ok"
                ],
                "browser_lab_form_execute_dry_run",
            )
        if dry_run is not None:
            dry_run_data = dry_run.get("data") or {}
            dry_run_screenshots = dry_run_data.get("screenshots") or (dry_run_data.get("executor") or {}).get("screenshots")
            dry_run_context = _dry_run_certification_context(dry_run_data)
            evidence["dry_run"] = {
                "status": str(dry_run_data.get("status") or "dry_run_ready"),
                "event_id": dry_run["id"],
                "event_type": dry_run["event_type"],
                "created_at": dry_run["created_at"],
                **dry_run_context,
            }
            if dry_run_screenshots:
                evidence["dry_run"]["screenshots"] = mask_secrets(dry_run_screenshots)
            evidence["replay"] = {
                "status": "passed",
                "event_id": dry_run["id"],
                "event_type": dry_run["event_type"],
                "created_at": dry_run["created_at"],
                **dry_run_context,
            }
        redaction = _latest_event_matching(
            [
                event
                for event in events
                if event.get("event_type") in {"redaction_scan", "external_apply_redaction_scan"}
                and _certification_evidence_present(event.get("data") or {})
            ],
            "redaction_scan",
        )
        if redaction is None:
            redaction = _latest_event_matching(
                [
                    event
                    for event in events
                    if event.get("event_type") == "external_apply_redaction_scan"
                    and _certification_evidence_present(event.get("data") or {})
                ],
                "external_apply_redaction_scan",
            )
        if redaction is not None:
            evidence["redaction"] = {
                "status": "passed",
                "event_id": redaction["id"],
                "event_type": redaction["event_type"],
                "created_at": redaction["created_at"],
            }
        return evidence

    def promote_source_certification(
        self,
        source: str,
        *,
        level: int = 5,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        audit = self.source_certification_audit(source, level=level, evidence=evidence)
        source_name = str(audit["source"])
        if not audit.get("ready") or not audit.get("promotion_payload"):
            return {
                "status": "blocked",
                "reason": "source_certification_evidence_missing",
                "source": source_name,
                "level": int(audit.get("requested_level") or 0),
                "audit": audit,
                "capabilities": self.source_capabilities().get(source_name) or {},
            }
        config = dict(self.config)
        sources = dict(config.get("sources") or {})
        source_config = dict(sources.get(source_name) or {})
        source_config["external_apply"] = dict(audit["promotion_payload"]["external_apply"])
        sources[source_name] = source_config
        config["sources"] = sources
        self.save_config(config)
        capabilities = self.source_capabilities().get(source_name) or {}
        return {
            "status": "certified",
            "source": source_name,
            "level": int(capabilities.get("level") or audit.get("requested_level") or 0),
            "audit": audit,
            "capabilities": capabilities,
        }

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
        )

    def get_job(self, job_id: int) -> Job | None:
        return self.storage.get_job(job_id)

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

    def cover_letter_preview(
        self,
        job_id: int,
        *,
        template: str = "A",
        use_for_campaign: bool = False,
    ) -> dict[str, Any]:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        preview = human_cover_letter_variants(
            job,
            active_profile(self.config),
            selected_template=template,
            use_for_campaign=use_for_campaign,
        )
        campaign_letter = dict(preview["campaign_letter"])
        if use_for_campaign:
            draft = LetterDraft(job_id=job_id, body=str(campaign_letter["body"]))
            self.storage.save_letter(draft)
            self.record_replay_event(
                job_id=job_id,
                source=job.source,
                event_type="cover_letter_generated",
                title="Сгенерировано сопроводительное",
                summary=f"Template: {campaign_letter['template']}, {len(campaign_letter['body'].split())} words",
                data={
                    "template": campaign_letter["template"],
                    "style": campaign_letter["name"],
                    "use_for_campaign": True,
                },
            )
        return preview

    def chat(self, messages: list[dict[str, str]], job_id: int | None = None) -> str:
        """Chat with AI assistant, optionally providing job context."""
        ai_config = self.config.get("ai", {})

        if job_id is not None:
            job = self.storage.get_job(job_id)
            if job:
                score = self.storage.get_score(job_id, self.config.get("profile", "default"))
                score_text = ""
                if score:
                    score_text = (
                        f"\nScore: total={score.total_score}, skills={score.skills_score}, "
                        f"salary={score.salary_score}, remote={score.remote_score}\n"
                    )
                system_msg = {
                    "role": "system",
                    "content": (
                        "РўС‹ вЂ” AI-Р°СЃСЃРёСЃС‚РµРЅС‚ Work Hunter, РїРѕРјРѕРіР°СЋС‰РёР№ СЃ РїРѕРёСЃРєРѕРј IT-СЂР°Р±РѕС‚С‹. "
                        "РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РѕР±СЃСѓР¶РґР°РµС‚ РєРѕРЅРєСЂРµС‚РЅСѓСЋ РІР°РєР°РЅСЃРёСЋ. "
                        "РўРІРѕСЏ Р·Р°РґР°С‡Р°: Р°РЅР°Р»РёР·РёСЂРѕРІР°С‚СЊ РµС‘, РґР°РІР°С‚СЊ СЃРѕРІРµС‚С‹ РїРѕ fit-Сѓ, "
                        "РїРѕРјРѕРіР°С‚СЊ РіРѕС‚РѕРІРёС‚СЊСЃСЏ Рє СЃРѕР±РµСЃРµРґРѕРІР°РЅРёСЋ, РїРёСЃР°С‚СЊ СЃРѕРїСЂРѕРІРѕРґРёС‚РµР»СЊРЅС‹Рµ.\n\n"
                        f"=== Р’РђРљРђРќРЎРРЇ ===\n"
                        f"РџРѕР·РёС†РёСЏ: {job.title}\n"
                        f"РљРѕРјРїР°РЅРёСЏ: {job.company or 'РЅРµ СѓРєР°Р·Р°РЅР°'}\n"
                        f"РћРїРёСЃР°РЅРёРµ: {(job.description or 'РЅРµС‚')[:1500]}\n"
                        f"Р—Р°СЂРїР»Р°С‚Р°: {job.salary_text or 'РЅРµ СѓРєР°Р·Р°РЅР°'}\n"
                        f"Р›РѕРєР°С†РёСЏ: {job.location or 'РЅРµ СѓРєР°Р·Р°РЅР°'}\n"
                        f"РЈРґР°Р»С‘РЅРєР°: {'РґР°' if job.remote else 'РЅРµС‚/РЅРµ СѓРєР°Р·Р°РЅРѕ'}\n"
                        f"РСЃС‚РѕС‡РЅРёРє: {job.source}{score_text}\n"
                        "РћС‚РІРµС‡Р°Р№ РєСЂР°С‚РєРѕ (2-5 РїСЂРµРґР»РѕР¶РµРЅРёР№ РµСЃР»Рё РЅРµ РїСЂРѕСЃСЏС‚ СЂР°Р·РІС‘СЂРЅСѓС‚Рѕ), "
                        "РїРѕ РґРµР»Сѓ, РЅР° СЂСѓСЃСЃРєРѕРј. РќРµ РІС‹РґСѓРјС‹РІР°Р№ РёРЅС„РѕСЂРјР°С†РёСЋ Рѕ РїРѕР»СЊР·РѕРІР°С‚РµР»Рµ."
                    ),
                }
                messages = [system_msg] + messages
        else:
            system_msg = {
                "role": "system",
                "content": (
                    "РўС‹ вЂ” AI-Р°СЃСЃРёСЃС‚РµРЅС‚ Work Hunter, РїРµСЂСЃРѕРЅР°Р»СЊРЅС‹Р№ РїРѕРјРѕС‰РЅРёРє РїРѕ РїРѕРёСЃРєСѓ IT-СЂР°Р±РѕС‚С‹. "
                    "РўРІРѕРё РІРѕР·РјРѕР¶РЅРѕСЃС‚Рё: Р°РЅР°Р»РёР·РёСЂРѕРІР°С‚СЊ РІР°РєР°РЅСЃРёРё, РґР°РІР°С‚СЊ СЃРѕРІРµС‚С‹ РїРѕ СЂРµР·СЋРјРµ, "
                    "РїРѕРјРѕРіР°С‚СЊ РіРѕС‚РѕРІРёС‚СЊСЃСЏ Рє СЃРѕР±РµСЃРµРґРѕРІР°РЅРёСЏРј, РѕС†РµРЅРёРІР°С‚СЊ fit РїРѕР·РёС†РёРё. "
                    "РћС‚РІРµС‡Р°Р№ РЅР° СЂСѓСЃСЃРєРѕРј, РєСЂР°С‚РєРѕ Рё РїРѕ РґРµР»Сѓ (2-5 РїСЂРµРґР»РѕР¶РµРЅРёР№ РµСЃР»Рё РЅРµ РїСЂРѕСЃСЏС‚ СЂР°Р·РІС‘СЂРЅСѓС‚Рѕ). "
                    "Р•СЃР»Рё РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ СЃРїСЂР°С€РёРІР°РµС‚ РїСЂРѕ РєРѕРЅРєСЂРµС‚РЅСѓСЋ РІР°РєР°РЅСЃРёСЋ вЂ” РїСЂРµРґР»РѕР¶Рё РµРјСѓ СЃРЅР°С‡Р°Р»Р° "
                    "РІС‹Р±СЂР°С‚СЊ РµС‘ РІ СЃРїРёСЃРєРµ РґР»СЏ РєРѕРЅС‚РµРєСЃС‚Р°."
                ),
            }
            messages = [system_msg] + messages

        return chat_completion(messages, ai_config)

    def switch_profile(self, profile_id: str) -> dict[str, Any]:
        """Switch the active search profile."""
        profiles = self.config.get("profiles", {})
        if profile_id not in profiles:
            available = list(profiles.keys())
            raise ValueError(
                f"Profile '{profile_id}' not found. "
                f"Available: {', '.join(available)}"
            )
        self.config["profile"] = profile_id
        save_config(self.config_path, self.config)
        return profiles[profile_id]

    def update_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        """Update the active profile's search preferences (queries, skills, stop_words)."""
        profile_id = self.config.get("profile", "default")
        profiles = self.config.get("profiles", {})
        if profile_id not in profiles:
            raise ValueError(f"Profile '{profile_id}' not found")
        allowed = ["queries", "desired_roles", "must_have_skills", "nice_to_have_skills",
                   "stop_words", "desired_salary", "desired_cities"]
        for key in allowed:
            if key in data:
                profiles[profile_id][key] = data[key]
        self.config["profiles"] = profiles
        save_config(self.config_path, self.config)
        return profiles[profile_id]

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

    def onboarding_questions(self) -> list[dict[str, Any]]:
        return build_onboarding_questions()

    def answer_onboarding(
        self,
        question_id: str,
        answer: str,
        *,
        source: str = "manual",
    ) -> dict[str, Any]:
        question = next((item for item in self.onboarding_questions() if item["id"] == question_id), None)
        if question is None:
            question = _legacy_onboarding_question(question_id)
        if question is None:
            raise ValueError(f"Unknown onboarding question: {question_id}")
        profile_id = str(self.config.get("profile") or "default")
        fact_id = self.storage.save_candidate_fact(
            profile_id=profile_id,
            category=str(question["category"]),
            key=str(question_id),
            value=answer,
            confidence=0.7,
            source=source,
            status="unconfirmed",
            evidence={"question": question["prompt"]},
        )
        fact = self.storage.list_candidate_facts(profile_id=profile_id)[-1]
        self.record_replay_event(
            source="candidate",
            event_type="candidate_fact_created",
            title="Candidate fact captured",
            data={"fact": fact},
        )
        return {"status": "recorded", "facts": [{"id": fact_id, **fact}]}

    def confirm_candidate_fact(self, fact_id: int) -> dict[str, Any]:
        fact = self.storage.update_candidate_fact_status(fact_id, "confirmed")
        if fact is None:
            raise ValueError(f"Candidate fact {fact_id} not found")
        self.record_replay_event(
            source="candidate",
            event_type="candidate_fact_confirmed",
            title="Candidate fact confirmed",
            data={"fact": fact},
        )
        return fact

    def candidate_facts(self, *, status: str | None = None) -> list[dict[str, Any]]:
        return self.storage.list_candidate_facts(
            profile_id=str(self.config.get("profile") or "default"),
            status=status,
        )

    def _candidate_config_facts(self, existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
        about = self.config.get("about") or {}
        profile = active_profile(self.config)
        profile_id = str(self.config.get("profile") or "default")
        existing_keys = {str(fact.get("key") or "") for fact in existing}
        facts: list[dict[str, Any]] = []

        skills = [
            str(skill).strip()
            for skill in about.get("all_skills") or []
            if str(skill).strip()
        ]
        experience = [
            item
            for item in about.get("experience") or []
            if isinstance(item, dict) and any(str(value).strip() for value in item.values() if value)
        ]
        has_candidate_baseline = bool(skills or experience)
        if not has_candidate_baseline:
            return facts

        if skills and not existing_keys.intersection({"skills", "stack"}):
            facts.append(
                {
                    "id": "config:skills",
                    "profile_id": profile_id,
                    "category": "profile",
                    "key": "skills",
                    "value": ", ".join(skills),
                    "confidence": 0.9,
                    "source": "config_about",
                    "status": "confirmed",
                    "evidence": {"config_path": "about.all_skills"},
                }
            )
        if experience and "experience" not in existing_keys:
            facts.append(
                {
                    "id": "config:experience",
                    "profile_id": profile_id,
                    "category": "profile",
                    "key": "experience",
                    "value": experience,
                    "confidence": 0.9,
                    "source": "config_about",
                    "status": "confirmed",
                    "evidence": {"config_path": "about.experience"},
                }
            )
        if "constraints" not in existing_keys:
            constraints = _candidate_profile_constraints(profile)
            if constraints:
                facts.append(
                    {
                        "id": "config:constraints",
                        "profile_id": profile_id,
                        "category": "preferences",
                        "key": "constraints",
                        "value": constraints,
                        "confidence": 0.8,
                        "source": "config_profile",
                        "status": "confirmed",
                        "evidence": {"config_path": f"profiles.{profile_id}"},
                    }
                )
        return facts

    def _candidate_evidence_facts(self, *, status: str | None = None) -> list[dict[str, Any]]:
        stored = self.candidate_facts()
        combined = [*stored, *self._candidate_config_facts(stored)]
        if status is None:
            return combined
        return [fact for fact in combined if str(fact.get("status") or "") == status]

    def candidate_completeness(self) -> dict[str, Any]:
        facts = self._candidate_evidence_facts()
        return candidate_completeness_from_facts(facts)

    def candidate_map(self) -> dict[str, Any]:
        return build_candidate_map(
            profile=active_profile(self.config),
            facts=self._candidate_evidence_facts(),
            completeness=self.candidate_completeness(),
        )

    def import_resume(self, path: str | Path, *, activate: bool = False) -> dict[str, Any]:
        imported = import_resume_file(path)
        if imported.get("status") != "imported":
            return imported
        profile_id = str(self.config.get("profile") or "default")
        resume_id = self.storage.save_resume(
            Resume(
                name=str(imported["name"]),
                body=str(imported["body"]),
                profile_id=profile_id,
                is_active=activate,
                source_format=str(imported["source_format"]),
                imported_from=str(imported["imported_from"]),
                canonical=dict(imported["canonical"]),
            )
        )
        if activate:
            self.storage.set_active_resume(resume_id)
        result = {
            "status": "imported",
            "id": resume_id,
            "name": imported["name"],
            "source_format": imported["source_format"],
            "imported_from": imported["imported_from"],
            "canonical": imported["canonical"],
        }
        self.record_replay_event(
            source="resume",
            event_type="resume_imported",
            title="Resume imported",
            data=result,
        )
        return result

    def build_resume_variant(self, job_id: int, resume_id: int) -> dict[str, Any]:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        resume = self.storage.get_resume(resume_id)
        if resume is None:
            raise ValueError(f"Resume {resume_id} not found")
        confirmed = self._candidate_evidence_facts(status="confirmed")
        if not confirmed:
            return {
                "status": "blocked",
                "reason": "no_confirmed_candidate_facts",
                "claims": [],
            }
        relevant = _relevant_candidate_facts(job, confirmed)
        if not relevant:
            relevant = confirmed[:3]
        claims = [
            {
                "id": fact["id"],
                "key": fact["key"],
                "value": fact["value"],
                "status": fact["status"],
                "source": fact["source"],
            }
            for fact in relevant
        ]
        body = resume.body.rstrip()
        body += "\n\nRelevant confirmed facts:\n"
        for fact in relevant:
            body += f"- {fact['value']}\n"
        base_canonical = resume.canonical or canonicalize_resume_text(resume.body, source_name=resume.name)
        canonical = merge_resume_canonical_with_claims(base_canonical, claims)
        diff = diff_resume_text(resume.body, body)
        metadata = build_resume_variant_metadata(
            vacancy_text=f"{job.title} {job.description}",
            canonical=canonical,
            claims=claims,
            diff=diff,
        )
        variant_id = self.storage.save_resume_variant(
            base_resume_id=resume_id,
            job_id=job_id,
            profile_id=resume.profile_id,
            name=f"{resume.name} / {job.title}",
            body=body,
            claims=claims,
            status="ready",
            policy_result={
                "truthful_claims_only": True,
                "diff": diff,
                "canonical": canonical,
                **metadata,
            },
        )
        result = {
            "status": "ready",
            "id": variant_id,
            "job_id": job_id,
            "base_resume_id": resume_id,
            "body": body,
            "canonical": canonical,
            "diff": diff,
            "claims": claims,
            **metadata,
        }
        self.record_replay_event(
            job_id=job_id,
            source=job.source,
            event_type="resume_variant_built",
            title="Resume variant built",
            data=result,
        )
        return result

    def build_application_pack(
        self,
        job_id: int,
        *,
        resume_variant: dict[str, Any] | None = None,
        cover_letter: str = "",
        short_message: str = "",
        source_payload: dict[str, Any] | None = None,
        campaign_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        payload = dict(source_payload or {})
        policy = dict(campaign_policy or {})
        resume_variant_data = resume_variant or {}
        payload.setdefault("resume_variant_id", resume_variant_data.get("id") or "")
        payload.setdefault("cover_letter_present", bool(str(cover_letter or "").strip()))
        reasons = _application_pack_policy_reasons(job, payload, policy)
        policy_status = "blocked_manual_review" if reasons else "ready"
        preview = build_application_pack_preview(
            job=job.to_dict(),
            resume_variant=resume_variant_data,
            cover_letter=cover_letter,
            short_message=short_message,
            source_payload=payload,
        )
        policy_result = build_application_policy(reasons)
        safe_payload = safe_source_payload(payload)
        resume_variant_id = str(resume_variant_data.get("id") or "")
        pack_id = self.storage.save_application_pack(
            job_id=job_id,
            source=job.source,
            resume_variant_id=resume_variant_id,
            cover_letter=cover_letter,
            short_message=short_message,
            payload=payload,
            preview=mask_secrets(preview),
            policy_status=policy_status,
            policy_reasons=reasons,
        )
        result = {
            "id": pack_id,
            "job_id": job_id,
            "source": job.source,
            "score": job.score.total_score if job.score else 0,
            "resume_variant_id": resume_variant_id,
            "cover_letter": cover_letter,
            "short_message": short_message,
            "source_payload": safe_payload,
            "policy_status": policy_status,
            "policy_reasons": reasons,
            "policy": policy_result,
            "preview": mask_secrets(preview),
        }
        self.record_replay_event(
            job_id=job_id,
            source=job.source,
            event_type="application_pack_built",
            title="Application pack built",
            summary=policy_status,
            data=result,
        )
        return result

    def external_apply_dry_run(
        self,
        job_id: int,
        *,
        form: dict[str, Any],
        resume_variant: dict[str, Any] | None = None,
        cover_letter: str = "",
        short_message: str = "",
        campaign_policy: dict[str, Any] | None = None,
        extra_answers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        if job.source == "hh":
            return {
                "status": "blocked",
                "reason": "hh_uses_official_apply",
                "message": "Use HH apply plan/confirm for official HH API vacancies.",
                "submit": False,
            }
        target_fingerprint = _external_apply_target_fingerprint(_external_apply_config(self.config, job.source))
        if form.get("fields"):
            plan = self._external_apply_supplied_form_plan(job, job_id=job_id, cover_letter=cover_letter, form=form)
        else:
            plan = self.prepare_apply_plan(job_id, letter=cover_letter)
            detected_form = ((plan.get("raw_result") or {}).get("apply") or {}).get("form")
            if isinstance(detected_form, dict) and detected_form.get("fields"):
                form = detected_form
        source_payload = _external_form_source_payload(form)
        preview_policy = {
            "enabled": True,
            "real_apply": True,
            "require_resume_variant": False,
            **(campaign_policy or {}),
        }
        pack = self.build_application_pack(
            job_id,
            resume_variant=resume_variant or {},
            cover_letter=cover_letter,
            short_message=short_message,
            source_payload=source_payload,
            campaign_policy=preview_policy,
        )
        if not form.get("fields"):
            reason = "external_apply_form_not_detected"
            risk_flags = list(
                dict.fromkeys(
                    [
                        *list(plan.get("risk_flags") or []),
                        reason,
                        *list(pack.get("policy_reasons") or []),
                    ]
                )
            )
            external_url = str(plan.get("external_url") or job.url)
            result = {
                "status": "blocked_manual_review",
                "reason": reason,
                "job_id": job_id,
                "source": job.source,
                "external_url": external_url,
                "submit": False,
                "requires_manual_confirm": True,
                "risk_flags": risk_flags,
                "plan": plan,
                "application_pack": pack,
                "external_apply_target_fingerprint": target_fingerprint,
                "dry_run": {
                    "status": "blocked_manual_review",
                    "reason": reason,
                    "source": job.source,
                    "form_url": external_url,
                    "submit": False,
                    "actions": [],
                    "risk_flags": ["unknown_form"],
                    "screenshots": {},
                },
                "actions": [{"type": "open_url", "url": external_url}],
                "screenshots": {},
            }
            self.record_replay_event(
                job_id=job_id,
                source=job.source,
                event_type="external_apply_dry_run",
                title="External apply dry run",
                summary="blocked_manual_review",
                data=result,
            )
            return mask_secrets(result)
        persona = persona_from_profile(active_profile(self.config), self.config.get("about") or {}).to_dict()
        dry_run = self.browser_lab_dry_run_form_fill(
            form,
            source=job.source,
            persona=persona,
            resume=resume_variant or {},
            vacancy=job.to_dict(),
            extra_answers={
                **(extra_answers or {}),
                "cover_letter": cover_letter,
                "short_message": short_message,
            },
        )
        risk_flags = list(
            dict.fromkeys(
                [
                    *list(plan.get("risk_flags") or []),
                    *list(dry_run.get("risk_flags") or []),
                    *list(pack.get("policy_reasons") or []),
                ]
            )
        )
        status = "dry_run_ready"
        if pack.get("policy_status") != "ready" or dry_run.get("status") != "dry_run_ready":
            status = "blocked_manual_review"
        result = {
            "status": status,
            "job_id": job_id,
            "source": job.source,
            "external_url": plan.get("external_url") or job.url,
            "submit": False,
            "requires_manual_confirm": True,
            "risk_flags": risk_flags,
            "plan": plan,
            "application_pack": pack,
            "external_apply_target_fingerprint": target_fingerprint,
            "dry_run": dry_run,
            "actions": dry_run.get("actions") or [],
            "screenshots": dry_run.get("screenshots") or {},
        }
        self.record_replay_event(
            job_id=job_id,
            source=job.source,
            event_type="external_apply_dry_run",
            title="External apply dry run",
            summary=status,
            data=result,
        )
        return mask_secrets(result)

    def _external_apply_supplied_form_plan(
        self,
        job: Job,
        *,
        job_id: int,
        cover_letter: str,
        form: dict[str, Any],
    ) -> dict[str, Any]:
        apply_meta = {
            "status": "supplied",
            "detector": "user_supplied_form",
            "form_signature": str(form.get("form_signature") or _external_form_source_payload(form).get("form_signature") or ""),
            "form": form,
        }
        return ApplyPlan(
            job_id=job_id,
            source=job.source,
            mode="external_page",
            letter=cover_letter,
            risk_flags=["external_manual_apply", "source_apply_form_supplied"],
            status="external",
            external_url=str(form.get("form_url") or job.url),
            raw_result={
                "message": "External apply form was supplied by the caller; no source discovery was performed.",
                "apply": apply_meta,
                "next_actions": [{"type": "dry_run_form_fill", "status": "ready"}],
            },
        ).to_dict()

    def confirm_external_apply(
        self,
        job_id: int,
        *,
        form: dict[str, Any],
        resume_variant: dict[str, Any] | None = None,
        cover_letter: str = "",
        short_message: str = "",
        campaign_policy: dict[str, Any] | None = None,
        extra_answers: dict[str, str] | None = None,
        confirm: bool = False,
        submit: bool = False,
        campaign_policy_apply: bool = False,
        requester: Any | None = None,
    ) -> dict[str, Any]:
        if not confirm:
            return {
                "status": "blocked",
                "reason": "confirmation_required",
                "message": "Explicit confirmation is required before preparing an external apply handoff.",
                "submit": False,
            }
        dry_run = self.external_apply_dry_run(
            job_id,
            form=form,
            resume_variant=resume_variant,
            cover_letter=cover_letter,
            short_message=short_message,
            campaign_policy=campaign_policy,
            extra_answers=extra_answers,
        )
        if dry_run.get("status") != "dry_run_ready":
            blocked = {
                **dry_run,
                "status": "blocked_manual_review",
                "submit": False,
                "final_submit_requires_user": True,
            }
            self.record_replay_event(
                job_id=job_id,
                source=str(dry_run.get("source") or ""),
                event_type="external_apply_confirm_blocked",
                title="External apply confirm blocked",
                summary="blocked_manual_review",
                data=blocked,
            )
            return blocked
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        if submit:
            return self._submit_certified_external_apply(
                job,
                dry_run=dry_run,
                resume_variant=resume_variant,
                cover_letter=cover_letter,
                short_message=short_message,
                campaign_policy=campaign_policy or {},
                extra_answers=extra_answers or {},
                campaign_policy_apply=campaign_policy_apply,
                requester=requester,
            )
        result = {
            **dry_run,
            "status": "manual_submit_ready",
            "submit": False,
            "final_submit_requires_user": True,
            "message": "External form is prepared for manual review; Work Hunter will not press submit.",
        }
        self.storage.save_application(
            job_id,
            "external_manual_ready",
            json.dumps(mask_secrets(result), ensure_ascii=False),
        )
        self.storage.set_status(job_id, "external_manual_ready", "External apply prepared; manual submit required")
        self.record_replay_event(
            job_id=job_id,
            source=str(result.get("source") or ""),
            event_type="external_apply_confirmed",
            title="External apply prepared",
            summary="manual_submit_ready",
            data=result,
        )
        return mask_secrets(result)

    def _submit_certified_external_apply(
        self,
        job: Job,
        *,
        dry_run: dict[str, Any],
        resume_variant: dict[str, Any] | None,
        cover_letter: str,
        short_message: str,
        campaign_policy: dict[str, Any],
        extra_answers: dict[str, str],
        campaign_policy_apply: bool = False,
        requester: Any | None = None,
    ) -> dict[str, Any]:
        job_id = int(job.id or 0)
        external_apply = _external_apply_config(self.config, job.source)
        readiness = self.source_capabilities().get(job.source) or {}
        level = int(readiness.get("level") or _external_apply_level(external_apply))
        pause_operation = "external_campaign_apply" if campaign_policy_apply else "external_apply_submit"
        pause_block = self._campaign_pause_block(operation=pause_operation)
        if pause_block is not None:
            blocked = {**pause_block, "source": job.source, "submit": False}
            self.record_replay_event(
                job_id=job_id,
                source=job.source,
                event_type="external_apply_submit_blocked",
                title="External apply submit blocked",
                summary=str(blocked.get("reason") or ""),
                data=blocked,
            )
            return blocked
        if campaign_policy_apply:
            if not readiness.get("can_campaign_apply"):
                return self._external_submit_blocked(
                    job,
                    reason=_external_readiness_block_reason(
                        readiness,
                        fallback="external_source_requires_l6_campaign_certification",
                    ),
                    level=level,
                    campaign_policy_apply=True,
                    readiness=readiness,
                )
            if not (campaign_policy.get("enabled") and campaign_policy.get("real_apply")):
                return self._external_submit_blocked(
                    job,
                    reason="external_campaign_policy_required",
                    level=level,
                    campaign_policy_apply=True,
                    readiness=readiness,
                )
        elif not readiness.get("can_real_apply"):
            return self._external_submit_blocked(
                job,
                reason=_external_readiness_block_reason(
                    readiness,
                    fallback="external_source_requires_l5_certification",
                ),
                level=level,
                campaign_policy_apply=False,
                readiness=readiness,
            )
        session = str(external_apply.get("session") or "")
        url_template = str(external_apply.get("url") or "")
        if not session:
            return self._external_submit_blocked(
                job,
                reason="external_apply_session_missing",
                level=level,
                readiness=readiness,
            )
        if not url_template:
            return self._external_submit_blocked(
                job,
                reason="external_apply_url_missing",
                level=level,
                readiness=readiness,
            )
        context = _external_apply_payload_context(
            job,
            resume_variant=resume_variant,
            cover_letter=cover_letter,
            short_message=short_message,
            extra_answers=extra_answers,
        )
        method = str(external_apply.get("method") or "POST").upper()
        url = _render_external_apply_template(url_template, context)
        payload_template = external_apply.get("payload_template") or {}
        payload = _render_external_apply_template(payload_template, context)
        data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        session_result = call_external_session(
            self.root,
            session,
            method,
            str(url),
            data=data,
            real=True,
            unsafe_lab=True,
            certified_apply=True,
            requester=requester,
            timeout=int(external_apply.get("timeout") or 20),
        )
        status = "external_applied" if session_result.get("status") == "ok" else "external_apply_failed"
        result = {
            **dry_run,
            "status": status,
            "submit": session_result.get("status") == "ok",
            "final_submit_requires_user": False,
            "campaign_policy_apply": bool(campaign_policy_apply),
            "certified_apply": {
                "level": level,
                "session": session,
                "method": method,
                "url": str(url),
            },
            "request": session_result.get("request") or {},
            "response": session_result.get("response") or {},
            "raw_result": session_result,
        }
        event_type = "external_apply_submitted" if status == "external_applied" else "external_apply_submit_failed"
        self.record_replay_event(
            job_id=job_id,
            source=job.source,
            event_type=event_type,
            title="External apply submitted" if status == "external_applied" else "External apply submit failed",
            summary=status,
            data=result,
        )
        if status == "external_applied":
            self.storage.save_application(
                job_id,
                "external_applied",
                json.dumps(mask_secrets(result), ensure_ascii=False),
            )
            self.storage.set_status(job_id, "applied", "External apply submitted through certified session")
        return mask_secrets(result)

    def _external_submit_blocked(
        self,
        job: Job,
        *,
        reason: str,
        level: int,
        campaign_policy_apply: bool = False,
        readiness: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "status": "blocked",
            "reason": reason,
            "source": job.source,
            "job_id": job.id,
            "submit": False,
            "campaign_policy_apply": bool(campaign_policy_apply),
            "level": level,
            "certified_apply": {"level": level},
        }
        if readiness is not None:
            payload["readiness"] = readiness
        self.record_replay_event(
            job_id=job.id,
            source=job.source,
            event_type="external_apply_submit_blocked",
            title="External apply submit blocked",
            summary=reason,
            data=payload,
        )
        return payload

    def record_replay_event(
        self,
        *,
        run_id: int | None = None,
        job_id: int | None = None,
        source: str = "",
        event_type: str,
        actor: str = "work_hunter",
        title: str = "",
        summary: str = "",
        data: dict[str, Any] | None = None,
    ) -> int:
        return self.storage.append_replay_event(
            run_id=run_id,
            job_id=job_id,
            source=source,
            event_type=event_type,
            actor=actor,
            title=str(mask_secrets(title)),
            summary=str(mask_secrets(summary)),
            data=mask_secrets(_with_replay_hashes(data or {})),
        )

    def replay_for_job(
        self,
        job_id: int,
        *,
        source: str | None = None,
        event_type: str | None = None,
    ) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "events": mask_secrets(
                self.storage.list_replay_events(job_id=job_id, source=source, event_type=event_type)
            ),
        }

    def replay_for_run(
        self,
        run_id: int,
        *,
        source: str | None = None,
        event_type: str | None = None,
    ) -> dict[str, Any]:
        run = self.storage.get_hh_campaign_run(run_id)
        return {
            "run": mask_secrets(run.to_dict() if run else {"id": run_id, "status": "missing"}),
            "events": mask_secrets(
                self.storage.list_replay_events(run_id=run_id, source=source, event_type=event_type)
            ),
        }

    def export_replay_markdown(
        self,
        *,
        job_id: int | None = None,
        run_id: int | None = None,
        source: str | None = None,
        event_type: str | None = None,
    ) -> str:
        events = self.storage.list_replay_events(
            job_id=job_id,
            run_id=run_id,
            source=source,
            event_type=event_type,
        )
        lines = ["# Replay Timeline", ""]
        if job_id is not None:
            lines.append(f"- job_id: {job_id}")
        if run_id is not None:
            lines.append(f"- run_id: {run_id}")
        lines.extend(
            [
                "- why selected: see selected/policy_decision events",
                "- what generated/sent: see resume_variant_built/application_pack_built/apply events",
                "- when sent/result: see created_at, summary, and status-bearing event data",
                "",
            ]
        )
        for event in events:
            lines.append(f"## {event['created_at']} · {event['event_type']} · {event['title'] or 'event'}")
            if event.get("summary"):
                lines.append(str(event["summary"]))
            data = mask_secrets(event.get("data") or {})
            if data:
                lines.append("```json")
                lines.append(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
                lines.append("```")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def security_status(self) -> dict[str, Any]:
        audit_logs = self.storage.list_audit_logs(limit=1)
        return {
            "doctor": self.doctor_report(),
            "ai": self.ai_status(),
            "hh_auth": self.hh_auth_status(),
            "source_capabilities": self.source_capabilities(),
            "pending_approvals": len(self.list_hh_approvals(status="pending")),
            "audit_logs": {
                "count": len(self.storage.list_audit_logs()),
                "latest_event_type": audit_logs[0]["event_type"] if audit_logs else "",
            },
        }

    def record_audit_log(
        self,
        *,
        event_type: str,
        actor: str = "work_hunter",
        data: dict[str, Any] | None = None,
    ) -> int:
        return self.storage.append_audit_log(
            event_type=event_type,
            actor=actor,
            data=mask_secrets(data or {}),
        )

    def browser_lab_status(self, source: str) -> dict[str, Any]:
        return build_browser_lab_status(self.root, source)

    def browser_lab_setup(self, source: str) -> dict[str, Any]:
        result = setup_browser_lab(self.root, source)
        self.record_replay_event(
            source=result["source"],
            event_type="browser_lab_setup",
            title="Browser lab profile prepared",
            data=result,
        )
        return result

    def browser_lab_open_login(self, source: str, *, login_url: str = "") -> dict[str, Any]:
        result = plan_browser_lab_open_login(self.root, source, login_url=login_url)
        self.record_replay_event(
            source=result["source"],
            event_type="browser_lab_open_login_planned",
            title="Browser login planned",
            data=result,
        )
        return result

    def browser_lab_import_har(
        self,
        source: str,
        har_path: str | Path,
        *,
        allowed_hosts: set[str] | None = None,
    ) -> dict[str, Any]:
        source_name = str(source or "").strip().lower().replace("-", "_")
        hosts = set(allowed_hosts or _browser_lab_default_hosts(source_name, self.config))
        result = import_browser_lab_har(self.root, source_name, har_path, allowed_hosts=hosts)
        self.record_replay_event(
            source=source_name,
            event_type="browser_lab_har_imported",
            title="Browser HAR imported",
            data=result,
        )
        return result

    def configure_source_external_apply_from_har(
        self,
        source: str,
        har_path: str | Path,
        *,
        allowed_hosts: set[str] | None = None,
        level: int = 5,
    ) -> dict[str, Any]:
        source_name = str(source or "").strip().lower().replace("-", "_")
        imported = self.browser_lab_import_har(source_name, har_path, allowed_hosts=allowed_hosts)
        if imported.get("status") != "imported":
            return {
                "status": "blocked",
                "reason": imported.get("reason") or "har_import_blocked",
                "source": source_name,
                "import": imported,
            }
        endpoint = _best_external_apply_endpoint(imported.get("recon") or {})
        if endpoint is None:
            return {
                "status": "blocked",
                "reason": "apply_endpoint_not_found",
                "source": source_name,
                "import": {
                    "status": imported.get("status"),
                    "network_recorder": imported.get("network_recorder") or {},
                },
                "recon_summary": {
                    "total_endpoints": (imported.get("recon") or {}).get("total_endpoints") or 0,
                    "by_tag": (imported.get("recon") or {}).get("by_tag") or {},
                },
            }
        payload_template, unknown_payload_keys = _external_apply_payload_template_from_post_data(
            str(endpoint.get("post_data") or "")
        )
        configured = self.configure_source_external_apply_target(
            source_name,
            session=source_name,
            url=str(endpoint.get("url") or ""),
            method=str(endpoint.get("method") or "POST"),
            payload_template=payload_template,
            level=level,
        )
        result = {
            "status": "configured",
            "source": source_name,
            "apply_endpoint": _safe_external_apply_endpoint_view(endpoint),
            "payload_template": payload_template,
            "unknown_payload_keys": unknown_payload_keys,
            "configured": configured,
            "import": {
                "status": imported.get("status"),
                "session": imported.get("session") or {},
                "network_recorder": imported.get("network_recorder") or {},
            },
        }
        self.record_replay_event(
            source=source_name,
            event_type="source_external_apply_target_configured_from_har",
            title="External apply target configured from HAR",
            summary="configured",
            data=result,
        )
        return mask_secrets(result)

    def browser_lab_map_form(
        self,
        form: dict[str, Any],
        *,
        source: str,
        persona: dict[str, Any] | None = None,
        resume: dict[str, Any] | None = None,
        vacancy: dict[str, Any] | None = None,
        extra_answers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return map_browser_lab_form(
            form,
            source=source,
            persona=persona,
            resume=resume,
            vacancy=vacancy,
            extra_answers=extra_answers,
        )

    def browser_lab_dry_run_form_fill(
        self,
        form: dict[str, Any],
        *,
        source: str,
        persona: dict[str, Any] | None = None,
        resume: dict[str, Any] | None = None,
        vacancy: dict[str, Any] | None = None,
        extra_answers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        result = build_browser_lab_dry_run_form_fill(
            self.root,
            form,
            source=source,
            persona=persona,
            resume=resume,
            vacancy=vacancy,
            extra_answers=extra_answers,
        )
        self.record_replay_event(
            source=str(source or ""),
            event_type="browser_lab_form_dry_run",
            title="Browser form dry run",
            summary=str(result.get("status") or ""),
            data=result,
        )
        return result

    def browser_lab_execute_dry_run_form_fill(
        self,
        form: dict[str, Any],
        *,
        source: str,
        persona: dict[str, Any] | None = None,
        resume: dict[str, Any] | None = None,
        vacancy: dict[str, Any] | None = None,
        extra_answers: dict[str, str] | None = None,
        executor: Any | None = None,
        headless: bool = True,
        timeout_ms: int = 15000,
    ) -> dict[str, Any]:
        result = execute_browser_lab_dry_run_form_fill(
            self.root,
            form,
            source=source,
            persona=persona,
            resume=resume,
            vacancy=vacancy,
            extra_answers=extra_answers,
            executor=executor,
            headless=headless,
            timeout_ms=timeout_ms,
        )
        self.record_replay_event(
            source=str(source or ""),
            event_type="browser_lab_form_execute_dry_run",
            title="Browser form dry run executed",
            summary=str(result.get("status") or ""),
            data=result,
        )
        return result

    def hh_config(self) -> dict[str, Any]:
        return active_hh_config(self.config)

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
        accounts = dict(self.config.get("hh_account_profiles") or {})
        account = dict(accounts.get(name) or {})
        for key in (
            "access_token",
            "refresh_token",
            "access_expires_at",
            "client_id",
            "client_secret",
        ):
            if key in values and values[key] is not None:
                account[key] = values[key]
        accounts[name] = account
        self.config["hh_account_profiles"] = accounts
        save_config(self.config_path, self.config)
        return {
            "name": name,
            "active": name == self.config.get("hh_account_profile", "default"),
            "has_access_token": bool(str(account.get("access_token") or "")),
            "has_refresh_token": bool(str(account.get("refresh_token") or "")),
        }

    def use_hh_account_profile(self, name: str) -> dict[str, Any]:
        accounts = self.config.get("hh_account_profiles") or {}
        if name not in accounts:
            raise ValueError(f"HH account profile '{name}' not found")
        self.config["hh_account_profile"] = name
        save_config(self.config_path, self.config)
        return {"active": name, "profile": self.config.get("profile", "default")}

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

        client = HHApplyClient(self.hh_config())
        if not client.has_token():
            return ApplyPlan(
                job_id=job_id,
                source=job.source,
                mode="api",
                resume_id=resume_id,
                letter=letter_body,
                risk_flags=["hh_token_missing"],
                status="blocked",
                external_url=job.url,
                raw_result={"message": "HH access token is required for exact API apply."},
            ).to_dict()

        vacancy = client.get_vacancy(job.source_id)
        selected_resume = resume_id or _first_id(client.suitable_resumes(job.source_id))
        if not selected_resume:
            selected_resume = _first_id(client.list_resumes())

        risk_flags: list[str] = []
        if vacancy.get("response_letter_required"):
            risk_flags.append("letter_required")
        if vacancy.get("has_test") or vacancy.get("test"):
            risk_flags.append("test_required")
        elif _hh_question_required(vacancy):
            risk_flags.append("questions_required")
        if vacancy.get("captcha") or vacancy.get("captcha_required") or vacancy.get("challenge") or vacancy.get("challenge_required"):
            risk_flags.append("captcha_or_challenge")
        if vacancy.get("archived"):
            risk_flags.append("archived")
        relations = list(vacancy.get("relations") or [])
        if relations:
            risk_flags.append("has_relations")
        if any(relation in HH_RELATION_APPLIED_VALUES for relation in relations):
            risk_flags.append("already_applied")
        if not selected_resume:
            risk_flags.append("no_resume")

        return ApplyPlan(
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
        ).to_dict()

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

        if job.source == "geekjob":
            mechanism = self._geekjob_apply_mechanism(job)
            self._merge_apply_mechanism(raw_result, risk_flags, mechanism)

        if job.source == "habr":
            mechanism = self._habr_apply_mechanism(job)
            self._merge_apply_mechanism(raw_result, risk_flags, mechanism)

        if job.source in PUBLIC_BOARD_APPLY_FORM_SOURCE_NAMES and apply_capability == "external_page":
            mechanism = self._public_board_apply_mechanism(job)
            self._merge_apply_mechanism(raw_result, risk_flags, mechanism)

        raw_result["next_actions"] = self._external_apply_next_actions(
            job=job,
            external_url=external_url,
            capabilities=capabilities,
            apply_meta=dict(raw_result.get("apply") or {}),
        )

        return ApplyPlan(
            job_id=job_id,
            source=job.source,
            mode=mode,
            resume_id=resume_id,
            letter=letter_body,
            risk_flags=list(dict.fromkeys(risk_flags)),
            status="external",
            external_url=external_url,
            raw_result=raw_result,
        ).to_dict()

    def _getmatch_apply_detail(self, job: Job) -> dict[str, Any]:
        try:
            source = GetmatchSource((self.config.get("sources") or {}).get("getmatch") or {})
            return {"ok": True, "payload": source.get_offer(job.source_id)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _geekjob_apply_mechanism(self, job: Job) -> dict[str, Any]:
        try:
            source = GeekJobSource((self.config.get("sources") or {}).get("geekjob") or {})
            return {"ok": True, "payload": source.apply_mechanism(job.source_id)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _habr_apply_mechanism(self, job: Job) -> dict[str, Any]:
        try:
            source = HabrSource((self.config.get("sources") or {}).get("habr") or {})
            return {"ok": True, "payload": source.apply_mechanism(job.source_id)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _public_board_apply_mechanism(self, job: Job) -> dict[str, Any]:
        try:
            source_config = (self.config.get("sources") or {}).get(job.source) or {}
            source = PublicJobBoardSource(
                source_config,
                source_name=job.source,
                spec=PUBLIC_BOARD_SPECS[job.source],
            )
            return {"ok": True, "payload": source.apply_mechanism(job.source_id)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _merge_apply_mechanism(
        self,
        raw_result: dict[str, Any],
        risk_flags: list[str],
        mechanism: dict[str, Any],
    ) -> None:
        if mechanism.get("ok"):
            apply_meta = dict(mechanism["payload"])
            raw_result["apply"] = apply_meta
            raw_result["source_detail"] = {"apply_mechanism": apply_meta}
            if apply_meta.get("status") == "detected" and apply_meta.get("form_signature") != "unknown":
                risk_flags.append("source_apply_form_detected")
            if apply_meta.get("cover_letter_required"):
                risk_flags.append("letter_required")
            if apply_meta.get("test_required"):
                risk_flags.append("test_required")
            if apply_meta.get("captcha"):
                risk_flags.append("captcha_or_challenge")
        else:
            raw_result["source_detail_error"] = mechanism.get("error")
            risk_flags.append("source_detail_api_error")

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
        form = apply_meta.get("form") if isinstance(apply_meta.get("form"), dict) else {}
        if form.get("fields"):
            actions.append(
                {
                    "type": "browser_lab_dry_run",
                    "source": job.source,
                    "form_signature": apply_meta.get("form_signature") or form.get("form_signature") or "known",
                    "form_url": form.get("form_url") or external_url,
                    "submit": False,
                    "field_count": len(form.get("fields") or []),
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
        risk_flags = list(plan.get("risk_flags") or [])
        hard_blocker = next(
            (HH_DIRECT_APPLY_HARD_BLOCKERS[flag] for flag in risk_flags if flag in HH_DIRECT_APPLY_HARD_BLOCKERS),
            "",
        )
        if hard_blocker:
            return {
                "status": "blocked_manual_review",
                "reason": hard_blocker,
                "message": "HH application requires manual review before real send.",
                "risk_flags": risk_flags,
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

        client = HHApplyClient(self.hh_config())
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
        client = HHApplyClient(self.hh_config())
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
        client = HHApplyClient(self.hh_config())
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
                "РЎРїР°СЃРёР±Рѕ Р·Р° РІРѕРїСЂРѕСЃ. РњРѕР№ РѕРїС‹С‚ СЂРµР»РµРІР°РЅС‚РµРЅ СЌС‚РѕР№ РІР°РєР°РЅСЃРёРё: СЏ СЂР°Р±РѕС‚Р°Р» СЃ РїРѕС…РѕР¶РёРјРё "
                "Р·Р°РґР°С‡Р°РјРё Рё РіРѕС‚РѕРІ РїРѕРґСЂРѕР±РЅРµРµ РѕР±СЃСѓРґРёС‚СЊ РєРѕРЅРєСЂРµС‚РЅС‹Рµ С‚СЂРµР±РѕРІР°РЅРёСЏ."
            )
        return clean_text(answer)

    def sync_hh_resumes(self) -> dict[str, Any]:
        client = HHApplyClient(self.hh_config())
        if not client.has_token():
            return {"status": "blocked", "count": 0, "message": "HH access token is required."}
        count = 0
        for payload in client.list_resumes():
            resume = _hh_resume_from_payload(payload)
            if not resume.id:
                continue
            self.storage.upsert_hh_resume(resume)
            count += 1
        return {"status": "ok", "count": count}

    def hh_whoami(self) -> dict[str, Any]:
        client = HHApplyClient(self.hh_config())
        if not client.has_token():
            return {"status": "blocked", "message": "HH access token is required."}
        return client.whoami()

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

        client = HHApplyClient(hh_config)
        try:
            me = client.whoami()
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

    def refresh_hh_token(self) -> dict[str, Any]:
        client = HHApplyClient(self.hh_config())
        token = client.refresh_token()
        access_token = str(token.get("access_token") or "")
        refresh_token = str(token.get("refresh_token") or "")
        if not access_token:
            raise RuntimeError("HH refresh response did not include access_token")
        active_account = str(self.config.get("hh_account_profile") or "default")
        if active_account == "default":
            hh_config = self.config["sources"]["hh"]
        else:
            accounts = dict(self.config.get("hh_account_profiles") or {})
            hh_config = dict(accounts.get(active_account) or {})
            accounts[active_account] = hh_config
            self.config["hh_account_profiles"] = accounts
        hh_config["access_token"] = access_token
        if refresh_token:
            hh_config["refresh_token"] = refresh_token
        expires_at = token.get("expires_at") or token.get("access_expires_at") or ""
        if expires_at:
            hh_config["access_expires_at"] = str(expires_at)
        save_config(self.config_path, self.config)
        return {
            "status": "ok",
            "access_token": access_token,
            "refresh_token": hh_config.get("refresh_token", ""),
            "access_expires_at": hh_config.get("access_expires_at", ""),
        }

    def update_hh_resumes(self) -> dict[str, Any]:
        client = HHApplyClient(self.hh_config())
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
        dry_run: bool = False,
        publish: bool = False,
        validate: bool = False,
    ) -> dict[str, Any]:
        validation = validate_hh_resume_payload(payload) if validate else None
        payload_to_create = validation["payload"] if validation else payload
        if validation and not validation["valid"]:
            return {"status": "invalid", "payload": payload_to_create, "validation": validation}
        client = HHApplyClient(self.hh_config())
        if not client.has_token():
            result = {
                "status": "blocked",
                "message": "HH access token is required.",
                "payload": payload_to_create,
            }
            if validation:
                result["validation"] = validation
            return result
        if dry_run:
            result = {"status": "dry_run", "payload": payload_to_create}
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
        dry_run: bool = False,
        publish: bool = False,
        validate: bool = True,
    ) -> dict[str, Any]:
        payload = load_hh_resume_payload(path)
        return self.create_hh_resume(
            payload,
            dry_run=dry_run,
            publish=publish,
            validate=validate,
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
    ) -> dict[str, Any]:
        client = HHApplyClient(self.hh_config())
        if not client.has_token():
            return {"status": "blocked", "message": "HH access token is required.", "resume_id": resume_id}
        payload = _clone_resume_payload(client.get_resume(resume_id), title=title)
        return self.create_hh_resume(payload, dry_run=dry_run, publish=publish)

    def sync_hh_negotiations(self, *, status: str = "active") -> dict[str, Any]:
        client = HHApplyClient(self.hh_config())
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
        client = HHApplyClient(self.hh_config())
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

    def plan_hh_negotiation_cleanup(
        self,
        *,
        status: str = "active",
        max_age_days: int | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        client = HHApplyClient(self.hh_config())
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
        client = HHApplyClient(self.hh_config())
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

    def hh_call_api(self, method: str, path: str, data: Any = None) -> dict[str, Any]:
        client = HHApplyClient(self.hh_config())
        if not client.has_token():
            return {"status": "blocked", "message": "HH access token is required."}
        return client.request_json(method.upper(), path, data=data)

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
            top_reason = max(skipped_by_reason, key=skipped_by_reason.get)
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
        client = HHApplyClient(self.hh_config())
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
        agent_config = dict(self.config.get("hh_agent") or {})
        agent_config["paused"] = True
        agent_config["pause_reason"] = reason
        agent_config["paused_at"] = datetime.now().astimezone().replace(microsecond=0).isoformat()
        self.config["hh_agent"] = agent_config
        save_config(self.config_path, self.config)
        return {"status": "paused", "paused": True, "reason": reason}

    def resume_hh_agent(self, *, reason: str = "manual") -> dict[str, Any]:
        agent_config = dict(self.config.get("hh_agent") or {})
        agent_config["paused"] = False
        agent_config["resume_reason"] = reason
        agent_config["resumed_at"] = datetime.now().astimezone().replace(microsecond=0).isoformat()
        self.config["hh_agent"] = agent_config
        save_config(self.config_path, self.config)
        return {"status": "resumed", "paused": False, "reason": reason}

    def _campaign_pause_block(self, *, operation: str, run_id: int | None = None) -> dict[str, Any] | None:
        agent_config = self.config.get("hh_agent") or {}
        if not bool(agent_config.get("paused", False)):
            return None
        payload: dict[str, Any] = {
            "status": "blocked",
            "reason": "campaign_kill_switch_paused",
            "message": "HH campaign kill switch is paused.",
            "operation": operation,
            "paused": True,
            "pause_reason": str(agent_config.get("pause_reason") or ""),
        }
        if run_id is not None:
            payload["id"] = run_id
        return payload

    def _hh_campaign_daily_cap(self, explicit: Any = None, *, source: str = "hh") -> int | None:
        cap = _positive_int(explicit)
        if cap is not None:
            return cap
        campaign_config = self.config.get("campaigns") or {}
        source_caps = campaign_config.get("per_source_caps") or {}
        cap = _positive_int(source_caps.get(source))
        if cap is not None:
            return cap
        cap = _positive_int(campaign_config.get("daily_cap"))
        if cap is not None:
            return cap
        hh_campaign_config = self.config.get("hh_campaign") or {}
        return _positive_int(hh_campaign_config.get("daily_cap"))

    def _record_hh_campaign_block(self, payload: dict[str, Any], *, run_id: int | None = None) -> None:
        self.record_replay_event(
            run_id=run_id,
            source="hh",
            event_type="campaign_blocked",
            title="HH campaign blocked",
            summary=str(payload.get("reason") or ""),
            data=payload,
        )

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
                "status": "token_configured" if has_access_token else "missing_access_token",
                "authorized": has_access_token,
                "refresh_ready": has_refresh_token,
                "client_credentials_configured": bool(
                    str(hh_config.get("client_id") or "")
                    and str(hh_config.get("client_secret") or "")
                ),
                "access_expires_at": str(hh_config.get("access_expires_at") or ""),
                "actions": actions,
            }
        return {
            "status": "ok" if has_access_token else "blocked",
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
        for item in outbox:
            outbox_by_status[item.status] = outbox_by_status.get(item.status, 0) + 1
        webhooks_by_status: dict[str, int] = {}
        for item in webhooks:
            webhooks_by_status[item.status] = webhooks_by_status.get(item.status, 0) + 1
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
                result = self.update_hh_resumes()
            elif operation == "sync-negotiations":
                result = self.sync_hh_negotiations(status=str(params.get("status") or "active"))
            elif operation == "scan-events":
                result = self.scan_hh_agent_events(
                    status=str(params.get("status") or "active"),
                    limit=None if params.get("limit") is None else int(params.get("limit")),
                )
            elif operation == "refresh-token":
                result = refresh_result = self.refresh_hh_token()
                result = {
                    key: value
                    for key, value in refresh_result.items()
                    if key not in {"access_token", "refresh_token"}
                }
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
        client = HHApplyClient(self.hh_config())
        safe_input = {
            "method": request["method"],
            "path": request["path"],
            "params": mask_secrets(request.get("params") or {}),
            "body": mask_secrets(request.get("body") or {}),
            "quick": quick,
        }
        if not client.has_token():
            return {
                "status": "blocked",
                "message": "HH access token is required.",
                **safe_input,
            }
        run_id = self.storage.start_hh_agent_mcp_run("hh_api_lab_call", safe_input)
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
            return output
        output = {
            "status": "ok",
            "operation_id": run_id,
            **safe_input,
            "result": mask_secrets(result),
        }
        self.storage.finish_hh_agent_mcp_run(run_id, status="ok", output=output)
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
        presets = dict(self.config.get("hh_campaign_presets") or {})
        presets[name] = _safe_preset_params(params)
        self.config["hh_campaign_presets"] = presets
        save_config(self.config_path, self.config)
        return {"name": name, "params": presets[name]}

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
        presets = dict(self.config.get("hh_campaign_presets") or {})
        presets.pop(name, None)
        self.config["hh_campaign_presets"] = presets
        save_config(self.config_path, self.config)
        return {"status": "ok"}

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
        daily_cap: int | None = None,
    ) -> dict[str, Any]:
        blocked = self._campaign_pause_block(operation="plan_hh_search_campaign")
        if blocked:
            self._record_hh_campaign_block(blocked)
            return blocked
        resolved_daily_cap = self._hh_campaign_daily_cap(daily_cap, source="hh")
        client = HHApplyClient(self.hh_config())
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
        for payload in client.search_vacancies(search_params):
            job = _hh_job_from_vacancy_payload(payload)
            if not job.source_id:
                continue
            job.id = self.storage.upsert_job(job)
            imported_jobs.append(job)

        filters = {
            "mode": "hh_search",
            "search_params": search_params,
            "limit": limit,
            "min_score": min_score,
            "skip_tests": skip_tests,
            "ai_filter_mode": ai_filter_mode,
            "resume_id": resume_id or "",
            "daily_cap": resolved_daily_cap or 0,
        }
        run_id = self.storage.create_hh_campaign_run(filters=filters)
        counts = _campaign_counts()
        ready_count = 0
        for job in imported_jobs:
            item = self._plan_hh_campaign_item(
                run_id,
                job,
                min_score=min_score,
                skip_tests=skip_tests,
                ai_filter_mode=ai_filter_mode,
                resume_id=resume_id,
            )
            if item.status == "ready":
                if resolved_daily_cap is not None and ready_count >= resolved_daily_cap:
                    item.status = "skipped"
                    item.reason = "daily_cap_reached"
                    item.raw_result = {**item.raw_result, "daily_cap": resolved_daily_cap}
                else:
                    ready_count += 1
            self.storage.save_hh_campaign_item(item)
            _count_campaign_item(counts, item)
        self.storage.update_hh_campaign_run(run_id, status="planned", counts=counts)
        run = self.storage.get_hh_campaign_run(run_id)
        payload = run.to_dict() if run else {"id": run_id, "status": "planned", "counts": counts}
        payload["imported"] = len(imported_jobs)
        self.record_replay_event(
            run_id=run_id,
            source="hh",
            event_type="campaign_planned",
            title="HH search campaign planned",
            summary=f"{counts['ready']} ready",
            data=payload,
        )
        return payload

    def plan_hh_campaign(
        self,
        *,
        limit: int = 100,
        min_score: int = 0,
        skip_tests: bool = False,
        ai_filter_mode: str = "off",
        resume_id: str | None = None,
        daily_cap: int | None = None,
    ) -> dict[str, Any]:
        blocked = self._campaign_pause_block(operation="plan_hh_campaign")
        if blocked:
            self._record_hh_campaign_block(blocked)
            return blocked
        resolved_daily_cap = self._hh_campaign_daily_cap(daily_cap, source="hh")
        filters = {
            "limit": limit,
            "min_score": min_score,
            "skip_tests": skip_tests,
            "ai_filter_mode": ai_filter_mode,
            "resume_id": resume_id or "",
            "daily_cap": resolved_daily_cap or 0,
        }
        run_id = self.storage.create_hh_campaign_run(filters=filters)
        counts = _campaign_counts()
        ready_count = 0
        jobs = self.storage.list_jobs(limit=limit, source="hh")
        for job in jobs:
            item = self._plan_hh_campaign_item(
                run_id,
                job,
                min_score=min_score,
                skip_tests=skip_tests,
                ai_filter_mode=ai_filter_mode,
                resume_id=resume_id,
            )
            if item.status == "ready":
                if resolved_daily_cap is not None and ready_count >= resolved_daily_cap:
                    item.status = "skipped"
                    item.reason = "daily_cap_reached"
                    item.raw_result = {**item.raw_result, "daily_cap": resolved_daily_cap}
                else:
                    ready_count += 1
            self.storage.save_hh_campaign_item(item)
            _count_campaign_item(counts, item)
        self.storage.update_hh_campaign_run(run_id, status="planned", counts=counts)
        run = self.storage.get_hh_campaign_run(run_id)
        payload = run.to_dict() if run else {"id": run_id, "status": "planned", "counts": counts}
        self.record_replay_event(
            run_id=run_id,
            source="hh",
            event_type="campaign_planned",
            title="HH campaign planned",
            summary=f"{counts['ready']} ready",
            data=payload,
        )
        return payload

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

    def plan_external_campaign(
        self,
        source: str,
        *,
        limit: int = 100,
        min_score: int = 0,
        daily_cap: int | None = None,
    ) -> dict[str, Any]:
        source_name = str(source or "").strip().lower().replace("-", "_")
        blocked = self._campaign_pause_block(operation="plan_external_campaign")
        if blocked:
            self._record_hh_campaign_block({**blocked, "source": source_name})
            return {**blocked, "source": source_name}
        readiness = self.source_capabilities().get(source_name) or {}
        if not readiness.get("can_campaign_apply"):
            return {
                "status": "blocked",
                "source": source_name,
                "reason": "external_source_requires_l6_campaign_certification",
                "readiness": readiness,
            }
        session_check = self._external_campaign_session_check(source_name)
        if session_check.get("status") != "ok":
            return {
                "status": "blocked",
                "source": source_name,
                "reason": session_check.get("reason") or "external_session_missing",
                "session": session_check,
            }
        filters = {
            "mode": "external_campaign",
            "source": source_name,
            "limit": limit,
            "min_score": min_score,
            "daily_cap": daily_cap or 0,
        }
        run_id = self.storage.create_hh_campaign_run(filters=filters)
        counts = _campaign_counts()
        ready_count = 0
        for job in self.storage.list_jobs(limit=limit, source=source_name):
            item = self._plan_external_campaign_item(
                run_id,
                job,
                min_score=min_score,
                source=source_name,
            )
            if item.status == "ready":
                if daily_cap is not None and ready_count >= daily_cap:
                    item.status = "skipped"
                    item.reason = "daily_cap_reached"
                    item.raw_result = {**item.raw_result, "daily_cap": daily_cap}
                else:
                    ready_count += 1
            self.storage.save_hh_campaign_item(item)
            _count_campaign_item(counts, item)
        self.storage.update_hh_campaign_run(run_id, status="planned", counts=counts)
        run = self.storage.get_hh_campaign_run(run_id)
        payload = run.to_dict() if run else {"id": run_id, "status": "planned", "counts": counts}
        self.record_replay_event(
            run_id=run_id,
            source=source_name,
            event_type="external_campaign_planned",
            title="External campaign planned",
            summary=f"{counts['ready']} ready",
            data=payload,
        )
        return payload

    def _external_campaign_session_check(self, source: str) -> dict[str, Any]:
        external_apply = _external_apply_config(self.config, source)
        session_name = str(external_apply.get("session") or "")
        if not session_name:
            return {"status": "blocked", "reason": "external_apply_session_missing"}
        session = show_external_session(self.root, session_name)
        hosts = list(session.get("hosts") or [])
        if not hosts:
            return {"status": "blocked", "reason": "external_session_missing", "session": session_name}
        url_host = urllib.parse.urlsplit(str(external_apply.get("url") or "")).hostname or ""
        if url_host and url_host.lower() not in {host.lower() for host in hosts}:
            return {
                "status": "blocked",
                "reason": "external_session_host_missing",
                "session": session_name,
                "host": url_host,
                "session_hosts": hosts,
            }
        return {"status": "ok", "session": session_name, "hosts": hosts}

    def _plan_external_campaign_item(
        self,
        run_id: int,
        job: Job,
        *,
        min_score: int,
        source: str,
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
        dry_run_event, dry_run_missing_reason = self._latest_external_apply_dry_run_event(job_id, source=source)
        if dry_run_event is None:
            return HHCampaignItem(
                run_id=run_id,
                job_id=job_id,
                vacancy_id=job.source_id,
                status="skipped",
                reason=dry_run_missing_reason,
                raw_result={"reason": dry_run_missing_reason},
            )
        dry_run_data = dict(dry_run_event.get("data") or {})
        form = _external_apply_form_from_dry_run_event(dry_run_data, fallback_url=job.url)
        if not form.get("fields"):
            return HHCampaignItem(
                run_id=run_id,
                job_id=job_id,
                vacancy_id=job.source_id,
                status="skipped",
                reason="external_apply_dry_run_missing",
                raw_result={"reason": "external_apply_dry_run_missing", "dry_run_event_id": dry_run_event["id"]},
            )
        resume_variant = self._active_resume_variant_payload()
        if not resume_variant:
            return HHCampaignItem(
                run_id=run_id,
                job_id=job_id,
                vacancy_id=job.source_id,
                status="error",
                reason="resume_required",
            )
        letter = str(
            self.cover_letter_preview(job_id, template="A", use_for_campaign=True)
            .get("campaign_letter", {})
            .get("body", "")
        )
        campaign_policy = {"enabled": True, "real_apply": True, "min_score": min_score}
        pack = self.build_application_pack(
            job_id,
            resume_variant=resume_variant,
            cover_letter=letter,
            source_payload=_external_form_source_payload(form),
            campaign_policy=campaign_policy,
        )
        if pack.get("policy_status") != "ready":
            return HHCampaignItem(
                run_id=run_id,
                job_id=job_id,
                vacancy_id=job.source_id,
                status="error",
                reason="application_pack_blocked",
                resume_id=str(resume_variant.get("id") or ""),
                letter=letter,
                risk_flags=list(pack.get("policy_reasons") or []),
                raw_result={"application_pack": pack},
            )
        return HHCampaignItem(
            run_id=run_id,
            job_id=job_id,
            vacancy_id=job.source_id,
            status="ready",
            resume_id=str(resume_variant.get("id") or ""),
            letter=letter,
            raw_result={
                "source": source,
                "resume_variant": resume_variant,
                "form": form,
                "campaign_policy": campaign_policy,
                "application_pack": pack,
                "dry_run_event_id": dry_run_event["id"],
            },
        )

    def _latest_external_apply_dry_run_event(self, job_id: int, *, source: str) -> tuple[dict[str, Any] | None, str]:
        events = self.storage.list_replay_events(job_id=job_id, source=source, event_type="external_apply_dry_run")
        expected_fingerprint = _external_apply_target_fingerprint(_external_apply_config(self.config, source))
        saw_ready_for_other_target = False
        for event in reversed(events):
            data = event.get("data") or {}
            if data.get("status") == "dry_run_ready" and data.get("submit") is False:
                if str(data.get("external_apply_target_fingerprint") or "") == expected_fingerprint:
                    return event, ""
                saw_ready_for_other_target = True
        reason = "external_apply_dry_run_target_mismatch" if saw_ready_for_other_target else "external_apply_dry_run_missing"
        return None, reason

    def confirm_external_campaign(
        self,
        run_id: int,
        *,
        confirm: bool = False,
        requester: Any | None = None,
    ) -> dict[str, Any]:
        run = self.storage.get_hh_campaign_run(run_id)
        if run is None:
            raise ValueError(f"Campaign run {run_id} not found")
        filters = run.filters or {}
        source_name = str(filters.get("source") or "").strip().lower().replace("-", "_")
        if filters.get("mode") != "external_campaign" or not source_name:
            raise ValueError(f"Campaign run {run_id} is not an external campaign")
        items = self.storage.list_hh_campaign_items(run_id)
        blocked = self._campaign_pause_block(operation="confirm_external_campaign", run_id=run_id)
        if blocked:
            counts = _campaign_counts()
            for item in items:
                _count_campaign_item(counts, item)
            self.storage.update_hh_campaign_run(run_id, status="blocked", counts=counts)
            blocked["counts"] = counts
            self._record_hh_campaign_block({**blocked, "source": source_name}, run_id=run_id)
            return blocked
        if not confirm:
            return {
                "status": "blocked",
                "message": "Explicit confirmation is required before sending an external campaign.",
                "id": run_id,
            }
        if run.status != "enabled":
            return {
                "status": "blocked",
                "reason": "real_apply_requires_campaign_policy",
                "message": "Real external campaign runs require an enabled campaign policy gate before submit.",
                "id": run_id,
                "run_status": run.status,
                "submit": False,
            }
        daily_cap = _positive_int(filters.get("daily_cap"))
        applied_count = 0
        counts = _campaign_counts()
        for item in items:
            if item.status != "ready":
                _count_campaign_item(counts, item)
                continue
            if daily_cap is not None and applied_count >= daily_cap:
                counts["skipped"] += 1
                result = {"status": "skipped", "reason": "daily_cap_reached", "daily_cap": daily_cap}
                self.storage.update_hh_campaign_item(
                    item.id,
                    status="skipped",
                    reason="daily_cap_reached",
                    raw_result=result,
                )
                continue
            raw = dict(item.raw_result or {})
            result = self.confirm_external_apply(
                item.job_id,
                form=dict(raw.get("form") or {}),
                resume_variant=dict(raw.get("resume_variant") or {}),
                cover_letter=item.letter,
                campaign_policy=dict(raw.get("campaign_policy") or {}),
                confirm=True,
                submit=True,
                campaign_policy_apply=True,
                requester=requester,
            )
            status = str(result.get("status") or "error")
            if status == "external_applied":
                applied_count += 1
                counts["applied"] += 1
                self.storage.update_hh_campaign_item(item.id, status="applied", raw_result=result)
            else:
                counts["error"] += 1
                self.storage.update_hh_campaign_item(item.id, status="error", reason=status, raw_result=result)
            self.record_replay_event(
                run_id=run_id,
                job_id=item.job_id,
                source=source_name,
                event_type="external_campaign_apply_result",
                title="External campaign apply result",
                summary=status,
                data={
                    "campaign_item_id": item.id,
                    "vacancy_id": item.vacancy_id,
                    "status": status,
                    "result": result,
                },
            )
        self.storage.update_hh_campaign_run(
            run_id,
            status="confirmed",
            counts=counts,
            finished=True,
        )
        updated = self.storage.get_hh_campaign_run(run_id)
        return updated.to_dict() if updated else {"id": run_id, "status": "confirmed", "counts": counts}

    def _active_resume_variant_payload(self) -> dict[str, Any]:
        profile_id = str(self.config.get("profile") or "default")
        for resume in self.storage.list_resumes(profile_id=profile_id):
            if resume.is_active:
                return {
                    "id": resume.id,
                    "title": resume.name,
                    "body": resume.body,
                    "canonical": resume.canonical,
                }
        return {}

    def enable_hh_campaign(self, run_id: int) -> dict[str, Any]:
        run = self.storage.get_hh_campaign_run(run_id)
        if run is None:
            raise ValueError(f"HH campaign run {run_id} not found")
        self.storage.update_hh_campaign_run(
            run_id,
            status="enabled",
            counts=dict(run.counts or {}),
        )
        updated = self.storage.get_hh_campaign_run(run_id)
        return updated.to_dict() if updated else {"id": run_id, "status": "enabled"}

    def confirm_enabled_hh_campaign(self, run_id: int, *, confirm: bool = False) -> dict[str, Any]:
        run = self.storage.get_hh_campaign_run(run_id)
        if run is None:
            raise ValueError(f"HH campaign run {run_id} not found")
        if not confirm:
            return self.confirm_hh_campaign(run_id, confirm=False)
        if run.status != "enabled":
            return {
                "status": "blocked",
                "reason": "real_apply_requires_campaign_policy",
                "message": "Real campaign runs require an enabled campaign policy gate before submit.",
                "id": run_id,
                "run_status": run.status,
                "submit": False,
            }
        return self.confirm_hh_campaign(run_id, confirm=True)

    def confirm_hh_campaign(self, run_id: int, *, confirm: bool = False) -> dict[str, Any]:
        run = self.storage.get_hh_campaign_run(run_id)
        if run is None:
            raise ValueError(f"HH campaign run {run_id} not found")
        items = self.storage.list_hh_campaign_items(run_id)
        blocked = self._campaign_pause_block(operation="confirm_hh_campaign", run_id=run_id)
        if blocked:
            counts = _campaign_counts()
            for item in items:
                _count_campaign_item(counts, item)
            self.storage.update_hh_campaign_run(run_id, status="blocked", counts=counts)
            blocked["counts"] = counts
            self._record_hh_campaign_block(blocked, run_id=run_id)
            return blocked
        if not confirm:
            return {
                "status": "blocked",
                "message": "Explicit confirmation is required before sending a campaign.",
                "id": run_id,
            }
        daily_cap = self._hh_campaign_daily_cap((run.filters or {}).get("daily_cap"), source="hh")
        applied_count = 0
        counts = _campaign_counts()
        for item in items:
            if item.status != "ready":
                _count_campaign_item(counts, item)
                continue
            blocked = self._campaign_pause_block(operation="confirm_hh_campaign", run_id=run_id)
            if blocked:
                self.storage.update_hh_campaign_run(run_id, status="blocked", counts=counts)
                blocked["counts"] = counts
                self._record_hh_campaign_block(blocked, run_id=run_id)
                return blocked
            if daily_cap is not None and applied_count >= daily_cap:
                counts["skipped"] += 1
                result = {"status": "skipped", "reason": "daily_cap_reached", "daily_cap": daily_cap}
                self.storage.update_hh_campaign_item(
                    item.id,
                    status="skipped",
                    reason="daily_cap_reached",
                    raw_result=result,
                )
                self.record_replay_event(
                    run_id=run_id,
                    job_id=item.job_id,
                    source="hh",
                    event_type="campaign_apply_skipped",
                    title="Campaign apply skipped",
                    summary="daily_cap_reached",
                    data={
                        "campaign_item_id": item.id,
                        "vacancy_id": item.vacancy_id,
                        "reason": "daily_cap_reached",
                        "daily_cap": daily_cap,
                    },
                )
                continue
            result = self.confirm_apply(
                item.job_id,
                resume_id=item.resume_id or None,
                letter=item.letter,
                confirm=True,
            )
            status = str(result.get("status") or "error")
            if status == "applied":
                applied_count += 1
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
            self.record_replay_event(
                run_id=run_id,
                job_id=item.job_id,
                source="hh",
                event_type="campaign_apply_result",
                title="Campaign apply result",
                summary=status,
                data={
                    "campaign_item_id": item.id,
                    "vacancy_id": item.vacancy_id,
                    "resume_id": item.resume_id,
                    "status": status,
                    "result": result,
                },
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
        jobs = self.storage.list_jobs(limit=limit, min_score=1)
        top = [job.to_dict() for job in jobs]
        by_source: dict[str, int] = {}
        for job in self.storage.list_jobs(limit=10000):
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
            "С‚С‹ РєР°СЂСЊРµСЂРЅС‹Р№ РєРѕРЅСЃСѓР»СЊС‚Р°РЅС‚. РџСЂРѕР°РЅР°Р»РёР·РёСЂСѓР№ РІР°РєР°РЅСЃРёСЋ Рё РїСЂРѕС„РёР»СЊ РєР°РЅРґРёРґР°С‚Р°. "
            "Р”Р°Р№ 3-4 РєРѕРЅРєСЂРµС‚РЅС‹С… СЃРѕРІРµС‚Р° С‡С‚Рѕ РїРѕРґСЃРІРµС‚РёС‚СЊ РІ СЂРµР·СЋРјРµ: РєР°РєРёРµ РїСЂРѕРµРєС‚С‹, "
            "РєР°РєРёРµ РЅР°РІС‹РєРё, РєР°Рє РѕРїРёСЃР°С‚СЊ РѕРїС‹С‚ С‡С‚РѕР±С‹ Р»СѓС‡С€Рµ РїРѕРґС…РѕРґРёС‚СЊ."
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
            "С‚С‹ СЌРєСЃРїРµСЂС‚ РїРѕ ATS-РѕРїС‚РёРјРёР·Р°С†РёРё СЂРµР·СЋРјРµ. РЎРіРµРЅРµСЂРёСЂСѓР№ СЂРµР·СЋРјРµ РІ markdown С„РѕСЂРјР°С‚Рµ "
            "РїРѕРґ РєРѕРЅРєСЂРµС‚РЅСѓСЋ РІР°РєР°РЅСЃРёСЋ. РСЃРїРѕР»СЊР·СѓР№ С‚РѕР»СЊРєРѕ СЂРµР°Р»СЊРЅС‹Р№ РѕРїС‹С‚ РєР°РЅРґРёРґР°С‚Р°. "
            "РќР°С‡РЅРё СЃ СЃР°РјС‹С… СЂРµР»РµРІР°РЅС‚РЅС‹С… РїСЂРѕРµРєС‚РѕРІ. Р’РїР»РµС‚Рё РєР»СЋС‡РµРІС‹Рµ СЃР»РѕРІР° РёР· РІР°РєР°РЅСЃРёРё РµСЃС‚РµСЃС‚РІРµРЅРЅРѕ."
        )
        user_prompt = f"""{_build_vacancy_candidate_prompt(job, about, experience_text)}

РЎРіРµРЅРµСЂРёСЂСѓР№ СЂРµР·СЋРјРµ РІ С„РѕСЂРјР°С‚Рµ:
# РРјСЏ
## РќР°РІС‹РєРё
## РћРїС‹С‚
(РёСЃРїРѕР»СЊР·СѓР№ markdown)"""
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
            "С‚С‹ ATS-Р°СѓРґРёС‚РѕСЂ. РџСЂРѕР°РЅР°Р»РёР·РёСЂСѓР№ СЂРµР·СЋРјРµ РЅР° СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚СЊ СЃ ATS: "
            "С„РѕСЂРјР°С‚РёСЂРѕРІР°РЅРёРµ (РєРѕР»РѕРЅРєРё/С‚Р°Р±Р»РёС†С‹ РЅРµ С‡РёС‚Р°СЋС‚СЃСЏ), РєР»СЋС‡РµРІС‹Рµ СЃР»РѕРІР°, "
            "РєРѕРЅС‚Р°РєС‚РЅС‹Рµ РґР°РЅРЅС‹Рµ, С‡РёС‚Р°РµРјРѕСЃС‚СЊ. Р”Р°Р№ РѕС†РµРЅРєСѓ Рё РєРѕРЅРєСЂРµС‚РЅС‹Рµ РёСЃРїСЂР°РІР»РµРЅРёСЏ."
        )
        user_prompt = f"Р РµР·СЋРјРµ РґР»СЏ Р°РЅР°Р»РёР·Р°:\n\n{resume_text}"
        if job_id is not None:
            job = self.storage.get_job(job_id)
            if job:
                user_prompt += (
                    f"\n\nР’Р°РєР°РЅСЃРёСЏ РґР»СЏ РєРѕРЅС‚РµРєСЃС‚Р°:\n"
                    f"- РџРѕР·РёС†РёСЏ: {job.title}\n"
                    f"- РљРѕРјРїР°РЅРёСЏ: {job.company or 'РЅРµ СѓРєР°Р·Р°РЅР°'}\n"
                    f"- РћРїРёСЃР°РЅРёРµ: {job.description or 'РЅРµС‚ РѕРїРёСЃР°РЅРёСЏ'}\n"
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
            "СЃРѕР¶РјРё РѕРїРёСЃР°РЅРёРµ РІР°РєР°РЅСЃРёРё РґРѕ 2-3 РїСЂРµРґР»РѕР¶РµРЅРёР№. "
            "РћСЃС‚Р°РІСЊ: СЂРѕР»СЊ, РєР»СЋС‡РµРІРѕР№ СЃС‚РµРє, РІРёР»РєР° Р·Рї, РіР»Р°РІРЅР°СЏ РѕСЃРѕР±РµРЅРЅРѕСЃС‚СЊ/РїР»СЋС€РєР°. "
            "РЇР·С‹Рє: СЂСѓСЃСЃРєРёР№."
        )
        user_prompt = (
            f"Р’Р°РєР°РЅСЃРёСЏ:\n"
            f"- РџРѕР·РёС†РёСЏ: {job.title}\n"
            f"- РљРѕРјРїР°РЅРёСЏ: {job.company or 'РЅРµ СѓРєР°Р·Р°РЅР°'}\n"
            f"- Р—Р°СЂРїР»Р°С‚Р°: {job.salary_text or 'РЅРµ СѓРєР°Р·Р°РЅР°'}\n"
            f"- Р›РѕРєР°С†РёСЏ: {job.location or 'РЅРµ СѓРєР°Р·Р°РЅР°'}\n"
            f"- РЈРґР°Р»С‘РЅРєР°: {'РґР°' if job.remote else 'РЅРµС‚/РЅРµ СѓРєР°Р·Р°РЅРѕ'}\n"
            f"- РћРїРёСЃР°РЅРёРµ: {job.description or 'РЅРµС‚ РѕРїРёСЃР°РЅРёСЏ'}\n"
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
            "С‚С‹ СЌРєСЃРїРµСЂС‚ РїРѕ РєР°СЂСЊРµСЂРЅРѕРјСѓ С„РёС‚Сѓ. РћС†РµРЅРё РЅР°СЃРєРѕР»СЊРєРѕ РєР°РЅРґРёРґР°С‚ РїРѕРґС…РѕРґРёС‚ РЅР° РІР°РєР°РЅСЃРёСЋ. "
            "Р’РµСЂРЅРё РѕС‚РІРµС‚ СЃС‚СЂРѕРіРѕ РІ С„РѕСЂРјР°С‚Рµ JSON: "
            '{"score": <С‡РёСЃР»Рѕ 0-100>, "reasoning": "<РєСЂР°С‚РєРѕРµ РѕР±РѕСЃРЅРѕРІР°РЅРёРµ РЅР° СЂСѓСЃСЃРєРѕРј>"}'
        )
        user_prompt = f"""Р’Р°РєР°РЅСЃРёСЏ:
- РџРѕР·РёС†РёСЏ: {job.title}
- РљРѕРјРїР°РЅРёСЏ: {job.company or 'РЅРµ СѓРєР°Р·Р°РЅР°'}
- РћРїРёСЃР°РЅРёРµ: {job.description or 'РЅРµС‚ РѕРїРёСЃР°РЅРёСЏ'}
- Р—Р°СЂРїР»Р°С‚Р°: {job.salary_text or 'РЅРµ СѓРєР°Р·Р°РЅР°'}
- РЈРґР°Р»С‘РЅРєР°: {'РґР°' if job.remote else 'РЅРµС‚/РЅРµ СѓРєР°Р·Р°РЅРѕ'}
- Р›РѕРєР°С†РёСЏ: {job.location or 'РЅРµ СѓРєР°Р·Р°РЅР°'}

РџСЂРѕС„РёР»СЊ:
- Р¦РµР»РµРІР°СЏ СЂРѕР»СЊ: {profile.get('title', '')}
- РќР°РІС‹РєРё: {', '.join(profile.get('must_have_skills', []) + profile.get('nice_to_have_skills', []))}

Рћ РєР°РЅРґРёРґР°С‚Рµ:
- Р РµР·СЋРјРµ: {about.get('summary', '')}
- Р’СЃРµ РЅР°РІС‹РєРё: {', '.join(about.get('all_skills', []))}

РћРїС‹С‚:
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

    def pipeline_status(
        self,
        job_id: int,
        *,
        now: str | None = None,
        followup_after_days: int = 3,
    ) -> dict[str, Any]:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        application = self.storage.get_application(job_id)
        events = [event for event in self.storage.list_events() if event.job_id == job_id]
        stage = _pipeline_stage(
            job.status,
            application.status if application is not None else "",
            events,
        )
        follow_up = self._pipeline_follow_up_suggestion(
            job,
            application=application,
            now=now,
            followup_after_days=followup_after_days,
        )
        next_actions = self._pipeline_next_actions(
            stage=stage,
            job=job,
            follow_up=follow_up,
            events=events,
        )
        return {
            "status": "ok",
            "stage": stage,
            "stage_label": _stage_label(stage),
            "job": job.to_dict(),
            "application": application.to_dict() if application is not None else None,
            "events": [event.to_dict() for event in events],
            "follow_up": follow_up,
            "next_actions": next_actions,
        }

    def _pipeline_follow_up_suggestion(
        self,
        job: Job,
        *,
        application: Any | None,
        now: str | None = None,
        followup_after_days: int = 3,
    ) -> dict[str, Any]:
        if application is None:
            return {
                "status": "not_ready",
                "reason": "no_application",
                "requires_manual_send": True,
            }
        applied_at = application.applied_at or application.updated_at
        due_at = _pipeline_due_at(applied_at, followup_after_days, fallback=now)
        subject = f"Follow-up: {job.title}"
        company = job.company or "the team"
        body = (
            f"Hi {company},\n\n"
            f"I wanted to follow up on my application for {job.title}. "
            "The role still looks relevant to my background, and I would be glad "
            "to discuss fit, next steps, or any additional context you need.\n\n"
            "Best regards"
        )
        return {
            "status": "ready",
            "type": "follow_up",
            "subject": subject,
            "body": body,
            "due_at": due_at,
            "requires_manual_send": True,
            "days_after_application": max(1, int(followup_after_days)),
        }

    def _pipeline_next_actions(
        self,
        *,
        stage: str,
        job: Job,
        follow_up: dict[str, Any],
        events: list[CalendarEvent],
    ) -> list[dict[str, Any]]:
        if stage == "not_applied":
            return [
                {
                    "type": "build_application_pack",
                    "title": "Build application pack",
                    "reason": "No application is stored for this job yet.",
                }
            ]
        if stage == "manual_submit_ready":
            return [
                {
                    "type": "manual_submit",
                    "title": "Review and submit manually",
                    "reason": "External apply is prepared but final submit requires the user.",
                }
            ]
        if stage == "applied_waiting":
            return [
                {
                    "type": "send_follow_up",
                    "title": "Send follow-up",
                    "reason": "Application is waiting for a reply.",
                    "due_at": follow_up.get("due_at", ""),
                    "requires_manual_send": True,
                },
                {
                    "type": "scan_replies",
                    "title": "Scan replies",
                    "reason": "A reply can move the job into interview prep.",
                },
            ]
        if stage in {"response_received", "interview"}:
            return [
                {
                    "type": "schedule_interview",
                    "title": "Schedule interview event",
                    "reason": "There is progress after application.",
                },
                {
                    "type": "build_interview_prep_pack",
                    "title": "Build interview prep pack",
                    "reason": "Prepare stack, STAR answers, company questions and salary script.",
                },
            ]
        if stage == "interview_scheduled":
            interview_events = [event.to_dict() for event in events if event.event_type == "interview"]
            return [
                {
                    "type": "build_interview_prep_pack",
                    "title": "Build interview prep pack",
                    "reason": "An interview is already on the calendar.",
                    "events": interview_events,
                }
            ]
        if stage == "offer":
            return [
                {
                    "type": "review_offer",
                    "title": "Review offer and negotiation points",
                    "reason": f"Prepare compensation and scope questions for {job.company or 'the company'}.",
                }
            ]
        return [
            {
                "type": "archive_or_note",
                "title": "Archive or add final note",
                "reason": "The pipeline item is closed.",
            }
        ]

    def schedule_pipeline_event(
        self,
        job_id: int,
        *,
        event_at: str,
        event_type: str = "follow_up",
        title: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        event_title = title.strip()
        if not event_title:
            label = "Interview" if event_type == "interview" else "Follow-up"
            event_title = f"{label}: {job.title}"
        event = CalendarEvent(
            job_id=job_id,
            title=event_title,
            event_type=event_type,
            event_date=event_at,
            notes=notes,
        )
        event.id = self.storage.save_event(event)
        payload = event.to_dict()
        self.record_replay_event(
            job_id=job_id,
            source=job.source,
            event_type="pipeline_event_scheduled",
            title="Pipeline event scheduled",
            summary=f"{event.event_type}: {event.title}",
            data=payload,
        )
        return {"status": "scheduled", "event": payload}

    def interview_prep_pack(self, job_id: int, *, stage: str = "tech") -> dict[str, Any]:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        stage_focus = build_stage_focus(stage)
        normalized_stage = str(stage_focus["stage"])
        profile = active_profile(self.config)
        confirmed_facts = self._candidate_evidence_facts(status="confirmed")
        tech_stack = _tech_stack_from_job(job, profile)
        fact_texts = [
            _candidate_fact_text(fact.get("value"))
            for fact in confirmed_facts
            if _candidate_fact_text(fact.get("value"))
        ]
        if not fact_texts:
            fallback = str((self.config.get("about") or {}).get("summary") or profile.get("summary") or "")
            if fallback:
                fact_texts = [fallback]
        stack_questions = [
            f"How would you describe your practical experience with {skill} for {job.title}?"
            for skill in tech_stack[:5]
        ]
        if not stack_questions:
            stack_questions = [
                f"Which parts of your background are strongest for {job.title}?",
            ]
        star_answers = [
            {
                "claim": fact,
                "answer": (
                    f"Situation: {fact}. "
                    f"Task: connect this work to {job.title}. "
                    "Action: explain what you personally owned and how you made decisions. "
                    "Result: name the measurable or observable outcome."
                ),
            }
            for fact in fact_texts[:4]
        ]
        salary_min = str(profile.get("salary_min") or "").strip()
        salary_phrase = (
            f"My target starts at {salary_min},"
            if salary_min
            else "I would like to align compensation with the role scope,"
        )
        salary_script = (
            f"{salary_phrase} based on responsibilities, seniority and the expected impact. "
            f"For {job.title}, I would first clarify scope, team process, on-call load and growth path, "
            "then discuss the final package."
        )
        risk_notes = list(job.score.red_flags if job.score else [])
        if "test" in (job.description or "").lower():
            risk_notes.append("Clarify any test task scope, deadline and review criteria before accepting.")
        pack = {
            "status": "ready",
            "stage": normalized_stage,
            "job": job.to_dict(),
            "tech_stack": tech_stack,
            "sections": {
                "stage_focus": stage_focus,
                "stack_questions": stack_questions,
                "experience_questions": [
                    "Which past project is closest to this role, and why?",
                    "What trade-off would you mention if asked about architecture decisions?",
                ],
                "behavioral_questions": [
                    "Tell me about a disagreement with a teammate and how you handled it.",
                    "Describe a time you had to ship under uncertainty.",
                ],
                "questions_for_company": [
                    "What does success look like in the first 90 days?",
                    "How are code reviews, ownership and incident response handled?",
                    "Which part of the product or platform needs the most attention now?",
                ],
            },
            "star_answers": star_answers,
            "salary_script": salary_script,
            "risk_notes": _dedup_keep_order(risk_notes),
            "stages": list(INTERVIEW_STAGES),
            "pipeline_automation": list(PIPELINE_AUTOMATION),
            "after_interview": build_after_interview_assets(job, stage=normalized_stage),
            "metadata": {
                "uses_confirmed_facts": bool(confirmed_facts),
                "fact_count": len(fact_texts),
                "stage": normalized_stage,
            },
            "follow_up_template": (
                f"Hi {job.company or 'team'}, thank you for the conversation. "
                "I appreciated the chance to learn more about the role. "
                "Happy to share any extra context if useful."
            ),
        }
        self.record_replay_event(
            job_id=job_id,
            source=job.source,
            event_type="interview_prep_pack_built",
            title="Interview prep pack built",
            summary=f"{normalized_stage} prep for {job.title}",
            data={
                "stage": normalized_stage,
                "tech_stack": tech_stack,
                "risk_notes": pack["risk_notes"],
            },
        )
        return pack

    def interview_questions(self, job_id: int) -> str:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        ai_config = self.config.get("ai", {})
        system_prompt = (
            "СЃРіРµРЅРµСЂРёСЂСѓР№ 4-5 РІРѕРїСЂРѕСЃРѕРІ РєРѕС‚РѕСЂС‹Рµ СЃС‚РѕРёС‚ Р·Р°РґР°С‚СЊ СЂР°Р±РѕС‚РѕРґР°С‚РµР»СЋ РЅР° СЃРѕР±РµСЃРµРґРѕРІР°РЅРёРё "
            "РїРѕ СЌС‚РѕР№ РІР°РєР°РЅСЃРёРё: РїСЂРѕ РєРѕРјР°РЅРґСѓ, СЃС‚РµРє, РїСЂРѕС†РµСЃСЃС‹, РѕР¶РёРґР°РЅРёСЏ, СЂРѕСЃС‚. РЇР·С‹Рє: СЂСѓСЃСЃРєРёР№."
        )
        user_prompt = (
            f"Р’Р°РєР°РЅСЃРёСЏ:\n"
            f"- РџРѕР·РёС†РёСЏ: {job.title}\n"
            f"- РљРѕРјРїР°РЅРёСЏ: {job.company or 'РЅРµ СѓРєР°Р·Р°РЅР°'}\n"
            f"- РћРїРёСЃР°РЅРёРµ: {job.description or 'РЅРµС‚ РѕРїРёСЃР°РЅРёСЏ'}\n"
            f"- Р—Р°СЂРїР»Р°С‚Р°: {job.salary_text or 'РЅРµ СѓРєР°Р·Р°РЅР°'}\n"
            f"- Р›РѕРєР°С†РёСЏ: {job.location or 'РЅРµ СѓРєР°Р·Р°РЅР°'}\n"
            f"- РЈРґР°Р»С‘РЅРєР°: {'РґР°' if job.remote else 'РЅРµС‚/РЅРµ СѓРєР°Р·Р°РЅРѕ'}\n"
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
            "С‚С‹ РєР°СЂСЊРµСЂРЅС‹Р№ РєРѕСѓС‡. Р’С‹Р±РµСЂРё РёР· РѕРїС‹С‚Р° РєР°РЅРґРёРґР°С‚Р° СЃР°РјС‹Р№ СЂРµР»РµРІР°РЅС‚РЅС‹Р№ РїСЂРѕРµРєС‚ "
            "РґР»СЏ СЌС‚РѕР№ РІР°РєР°РЅСЃРёРё Рё РЅР°РїРёС€Рё STAR-РїРёС‚С‡ (Situation, Task, Action, Result) "
            "РЅР° 4-5 РїСЂРµРґР»РѕР¶РµРЅРёР№. РўРѕР»СЊРєРѕ СЂРµР°Р»СЊРЅС‹Р№ РѕРїС‹С‚ РёР· РїСЂРµРґРѕСЃС‚Р°РІР»РµРЅРЅРѕРіРѕ."
        )
        user_prompt = f"""Р’Р°РєР°РЅСЃРёСЏ:
- РџРѕР·РёС†РёСЏ: {job.title}
- РљРѕРјРїР°РЅРёСЏ: {job.company or 'РЅРµ СѓРєР°Р·Р°РЅР°'}
- РћРїРёСЃР°РЅРёРµ: {job.description or 'РЅРµС‚ РѕРїРёСЃР°РЅРёСЏ'}

РћРїС‹С‚ РєР°РЅРґРёРґР°С‚Р°:
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
                "С‚С‹ РїР°СЂСЃРµСЂ РїРѕРёСЃРєРѕРІС‹С… Р·Р°РїСЂРѕСЃРѕРІ. РР·РІР»РµРєРё РёР· С‚РµРєСЃС‚Р°: РєР»СЋС‡РµРІС‹Рµ СЃР»РѕРІР° РґР»СЏ РїРѕРёСЃРєР°, "
                "Р¶РµР»Р°РµРјСѓСЋ СЂРѕР»СЊ, РЅР°РІС‹РєРё. Р’РµСЂРЅРё JSON: "
                '{"queries": [...], "desired_skills": [...], "remote_only": bool}'
            )
            raw = chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Р—Р°РїСЂРѕСЃ: {query}"},
                ],
                ai_config,
            )
            parsed = _parse_search_json(raw)
            keywords = parsed.get("queries", [query.split()[:5]])
            remote_only = parsed.get("remote_only", False)
            jobs = self.storage.search_jobs(keywords, limit=limit)
            if remote_only:
                jobs = [j for j in jobs if j.remote]
        except Exception:
            words = query.strip().split()[:5]
            jobs = self.storage.search_jobs(words, limit=limit)
        return [job.to_dict() for job in jobs]

    def export_jobs(self, status: str | None = None, source: str | None = None, format: str = "json") -> str:
        jobs = self.storage.list_jobs(limit=100000, status=status, source=source)
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
        return json.dumps([job.to_dict() for job in jobs], ensure_ascii=False)

    def parse_job_structure(self, job_id: int) -> dict[str, Any]:
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        ai_config = self.config.get("ai", {})
        system_prompt = (
            "РўС‹ РїР°СЂСЃРµСЂ РІР°РєР°РЅСЃРёР№. РР·РІР»РµРєРё РёР· С‚РµРєСЃС‚Р° РІР°РєР°РЅСЃРёРё СЃС‚СЂСѓРєС‚СѓСЂРёСЂРѕРІР°РЅРЅСѓСЋ РёРЅС„РѕСЂРјР°С†РёСЋ. "
            "Р’РµСЂРЅРё РўРћР›Р¬РљРћ РІР°Р»РёРґРЅС‹Р№ JSON Р±РµР· markdown-РѕР±С‘СЂС‚РєРё. "
            "РџРѕР»СЏ: tech_stack (СЃРїРёСЃРѕРє С‚РµС…РЅРѕР»РѕРіРёР№), experience_years (СЃС‚СЂРѕРєР°, РЅР°РїСЂРёРјРµСЂ '1-3'), "
            "must_have (СЃРїРёСЃРѕРє РѕР±СЏР·Р°С‚РµР»СЊРЅС‹С… С‚СЂРµР±РѕРІР°РЅРёР№), nice_to_have (СЃРїРёСЃРѕРє Р¶РµР»Р°С‚РµР»СЊРЅС‹С…), "
            "benefits (СЃРїРёСЃРѕРє Р±РµРЅРµС„РёС‚РѕРІ), seniority_level (junior/middle/senior/lead), "
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
        jobs = self.storage.list_jobs(limit=limit)
        ai_config = self.config.get("ai", {})
        job_texts: list[str] = []
        for job in jobs:
            desc = (job.description or "")[:200]
            job_texts.append(f"- {job.title} ({job.company or 'вЂ”'}): {desc}")
        system_prompt = (
            "РўС‹ Р°РЅР°Р»РёС‚РёРє IT-СЂС‹РЅРєР° С‚СЂСѓРґР°. РџСЂРѕР°РЅР°Р»РёР·РёСЂСѓР№ СЃРїРёСЃРѕРє РІР°РєР°РЅСЃРёР№ Рё РѕС‚РІРµС‚СЊ РЅР° РІРѕРїСЂРѕСЃС‹: "
            "1) РљР°РєРёРµ С‚РµС…РЅРѕР»РѕРіРёРё СЃРµР№С‡Р°СЃ СЃР°РјС‹Рµ РІРѕСЃС‚СЂРµР±РѕРІР°РЅРЅС‹Рµ (С‚РѕРї-10)? "
            "2) РљР°РєРёРµ СЂРѕР»Рё/СЃРїРµС†РёР°Р»РёР·Р°С†РёРё С‡Р°С‰Рµ РІСЃРµРіРѕ РёС‰СѓС‚? "
            "3) РљР°РєРёРµ Р·Р°СЂРїР»Р°С‚РЅС‹Рµ РІРёР»РєРё РїСЂРµРѕР±Р»Р°РґР°СЋС‚? "
            "4) РљР°РєРёРµ С‚СЂРµРЅРґС‹ Р·Р°РјРµС‚РЅС‹? "
            "РћС‚РІРµС‡Р°Р№ СЃС‚СЂСѓРєС‚СѓСЂРёСЂРѕРІР°РЅРЅРѕ, РЅР° СЂСѓСЃСЃРєРѕРј."
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
            return f"РћС€РёР±РєР°: {exc}"

    def gap_analysis(self, resume_id: int, job_id: int) -> str:
        resume = self.storage.get_resume(resume_id)
        if resume is None:
            raise ValueError(f"Resume {resume_id} not found")
        job = self.storage.get_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        ai_config = self.config.get("ai", {})
        system_prompt = (
            "РўС‹ РєР°СЂСЊРµСЂРЅС‹Р№ РєРѕРЅСЃСѓР»СЊС‚Р°РЅС‚. РЎСЂР°РІРЅРё СЂРµР·СЋРјРµ РєР°РЅРґРёРґР°С‚Р° СЃ С‚СЂРµР±РѕРІР°РЅРёСЏРјРё РІР°РєР°РЅСЃРёРё. "
            "РќР°Р№РґРё: 1) РљР°РєРёРµ РЅР°РІС‹РєРё РёР· РІР°РєР°РЅСЃРёРё РѕС‚СЃСѓС‚СЃС‚РІСѓСЋС‚ РІ СЂРµР·СЋРјРµ (skills gap) "
            "2) РљР°РєРёРµ РЅР°РІС‹РєРё РµСЃС‚СЊ РЅРѕ РЅРµ РїРѕРґСЃРІРµС‡РµРЅС‹/СЃРїСЂСЏС‚Р°РЅС‹ "
            "3) РљР°Рє РїРµСЂРµС„РѕСЂРјСѓР»РёСЂРѕРІР°С‚СЊ СЃСѓС‰РµСЃС‚РІСѓСЋС‰РёР№ РѕРїС‹С‚ С‡С‚РѕР±С‹ Р»СѓС‡С€Рµ РјР°С‚С‡РёС‚СЊ РІР°РєР°РЅСЃРёСЋ "
            "4) Р§С‚Рѕ РєРѕРЅРєСЂРµС‚РЅРѕ РґРѕР±Р°РІРёС‚СЊ РІ СЂРµР·СЋРјРµ. "
            "РћС‚РІРµС‡Р°Р№ РїРѕ РґРµР»Сѓ, 5-8 РїСѓРЅРєС‚РѕРІ. РќР° СЂСѓСЃСЃРєРѕРј."
        )
        user_prompt = (
            f"Р’Р°РєР°РЅСЃРёСЏ:\n"
            f"- РџРѕР·РёС†РёСЏ: {job.title}\n"
            f"- РљРѕРјРїР°РЅРёСЏ: {job.company or 'РЅРµ СѓРєР°Р·Р°РЅР°'}\n"
            f"- РћРїРёСЃР°РЅРёРµ: {job.description or 'РЅРµС‚ РѕРїРёСЃР°РЅРёСЏ'}\n"
            f"\nР РµР·СЋРјРµ:\n{resume.body}"
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
            return f"РћС€РёР±РєР°: {exc}"

    def ats_score_resume(self, resume_text: str) -> dict[str, Any]:
        ai_config = self.config.get("ai", {})
        system_prompt = (
            "РўС‹ СЌРєСЃРїРµСЂС‚ РїРѕ ATS (Applicant Tracking Systems). РћС†РµРЅРё СЂРµР·СЋРјРµ РЅР° СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚СЊ СЃ ATS. "
            "РљСЂРёС‚РµСЂРёРё: 1) РћС‚СЃСѓС‚СЃС‚РІРёРµ С‚Р°Р±Р»РёС†/РєРѕР»РѕРЅРѕРє/РёР·РѕР±СЂР°Р¶РµРЅРёР№ "
            "2) РќР°Р»РёС‡РёРµ РєР»СЋС‡РµРІС‹С… СЃР»РѕРІ 3) РЎС‚Р°РЅРґР°СЂС‚РЅС‹Рµ Р·Р°РіРѕР»РѕРІРєРё СЂР°Р·РґРµР»РѕРІ ('РћРїС‹С‚', 'РќР°РІС‹РєРё', 'РћР±СЂР°Р·РѕРІР°РЅРёРµ') "
            "4) Р§РёС‚Р°РµРјРѕСЃС‚СЊ РїР°СЂСЃРµСЂР°РјРё 5) РљРѕРЅС‚Р°РєС‚РЅР°СЏ РёРЅС„РѕСЂРјР°С†РёСЏ. "
            "Р’РµСЂРЅРё РўРћР›Р¬РљРћ JSON: {\"score\": С‡РёСЃР»Рѕ 0-100, \"issues\": [СЃРїРёСЃРѕРє РїСЂРѕР±Р»РµРј], \"suggestions\": [СЃРїРёСЃРѕРє СЃРѕРІРµС‚РѕРІ]}."
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
            "РўС‹ РєР»Р°СЃСЃРёС„РёРєР°С‚РѕСЂ IT-РІР°РєР°РЅСЃРёР№. РџСЂРѕР°РЅР°Р»РёР·РёСЂСѓР№ РѕРїРёСЃР°РЅРёРµ Рё РІРµСЂРЅРё РўРћР›Р¬РљРћ JSON: "
            "{\"seniority_level\": \"junior/middle/senior/lead\", "
            "\"real_remote\": true/false, \"has_salary\": true/false, "
            "\"red_flags\": [СЃРїРёСЃРѕРє РІРѕР·РјРѕР¶РЅС‹С… РєСЂР°СЃРЅС‹С… С„Р»Р°РіРѕРІ РІ РІР°РєР°РЅСЃРёРё: С‚РѕРєСЃРёС‡РЅС‹Рµ С„РѕСЂРјСѓР»РёСЂРѕРІРєРё, Р·Р°РІС‹С€РµРЅРЅС‹Рµ С‚СЂРµР±РѕРІР°РЅРёСЏ, 'РјС‹ СЃРµРјСЊСЏ' Рё С‚.Рґ.], "
            "\"company_type\": \"product/outsource/startup/enterprise/unknown\", "
            "\"tags\": [РєР»СЋС‡РµРІС‹Рµ С‚РµРіРё]}."
        )
        user_prompt = (
            f"Р’Р°РєР°РЅСЃРёСЏ:\n"
            f"- РџРѕР·РёС†РёСЏ: {job.title}\n"
            f"- РљРѕРјРїР°РЅРёСЏ: {job.company or 'РЅРµ СѓРєР°Р·Р°РЅР°'}\n"
            f"- РћРїРёСЃР°РЅРёРµ: {job.description or 'РЅРµС‚ РѕРїРёСЃР°РЅРёСЏ'}\n"
            f"- Р—Р°СЂРїР»Р°С‚Р°: {job.salary_text or 'РЅРµ СѓРєР°Р·Р°РЅР°'}"
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
                "РџРѕРґРіРѕС‚РѕРІСЊ Рє HR-СЃРєСЂРёРЅРёРЅРіСѓ: РєР°РєРёРµ РІРѕРїСЂРѕСЃС‹ Р·Р°РґР°РґСѓС‚ РїСЂРѕ РјРѕС‚РёРІР°С†РёСЋ, Р·Рї РѕР¶РёРґР°РЅРёСЏ, РїСЂРёС‡РёРЅС‹ СѓС…РѕРґР°. "
                "РљР°РєРёРµ РІРѕРїСЂРѕСЃС‹ Р·Р°РґР°С‚СЊ РїСЂРѕ РєРѕРјРїР°РЅРёСЋ, РєРѕРјР°РЅРґСѓ, РїСЂРѕС†РµСЃСЃС‹."
            ),
            "tech": (
                "РџРѕРґРіРѕС‚РѕРІСЊ Рє С‚РµС…РЅРёС‡РµСЃРєРѕРјСѓ СЃРѕР±РµСЃСѓ: РєР°РєРёРµ С‚РµС…РЅРѕР»РѕРіРёРё СЃРїСЂРѕСЃСЏС‚, С‚РёРїРёС‡РЅС‹Рµ Р·Р°РґР°С‡Рё, С‡С‚Рѕ РїРѕРІС‚РѕСЂРёС‚СЊ. "
                "РЎРѕСЃС‚Р°РІСЊ СЃРїРёСЃРѕРє С‚РµРј РїРѕ РѕРїРёСЃР°РЅРёСЋ РІР°РєР°РЅСЃРёРё."
            ),
            "final": (
                "РџРѕРґРіРѕС‚РѕРІСЊ Рє С„РёРЅР°Р»СЊРЅРѕРјСѓ СЃРѕР±РµСЃСѓ: РІРѕРїСЂРѕСЃС‹ РїСЂРѕ РєСѓР»СЊС‚СѓСЂСѓ, СЂРѕСЃС‚, РѕР¶РёРґР°РЅРёСЏ. "
                "РљР°Рє РїРѕРєР°Р·Р°С‚СЊ fit СЃ РєРѕРјР°РЅРґРѕР№."
            ),
            "offer": (
                "РџРѕРґРіРѕС‚РѕРІСЊ Рє РѕР±СЃСѓР¶РґРµРЅРёСЋ РѕС„С„РµСЂР°: РєР°Рє РѕР±СЃСѓР¶РґР°С‚СЊ Р·Рї, РєР°РєРёРµ Р±РµРЅРµС„РёС‚С‹ РїСЂРѕСЃРёС‚СЊ, РєР°Рє С‚РѕСЂРіРѕРІР°С‚СЊСЃСЏ."
            ),
        }
        system_prompt = stage_prompts.get(stage, stage_prompts["hr"])
        user_prompt = (
            f"Р’Р°РєР°РЅСЃРёСЏ:\n"
            f"- РџРѕР·РёС†РёСЏ: {job.title}\n"
            f"- РљРѕРјРїР°РЅРёСЏ: {job.company or 'РЅРµ СѓРєР°Р·Р°РЅР°'}\n"
            f"- РћРїРёСЃР°РЅРёРµ: {(job.description or '')[:1000]}"
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
            return f"РћС€РёР±РєР°: {exc}"

    def behavior_suggest(self) -> str:
        stats = self.storage.get_behavior_stats()
        if not stats.get("total_actions"):
            return "РџРѕРєР° РЅРµРґРѕСЃС‚Р°С‚РѕС‡РЅРѕ РґР°РЅРЅС‹С… РґР»СЏ Р°РЅР°Р»РёР·Р° РїРѕРІРµРґРµРЅРёСЏ."
        ai_config = self.config.get("ai", {})
        system_prompt = (
            "РўС‹ Р°РЅР°Р»РёС‚РёРє РїРѕРІРµРґРµРЅРёСЏ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ. РќР° РѕСЃРЅРѕРІРµ РёСЃС‚РѕСЂРёРё РµРіРѕ РґРµР№СЃС‚РІРёР№ СЃ РІР°РєР°РЅСЃРёСЏРјРё "
            "(СЃРѕС…СЂР°РЅРµРЅРёСЏ, СЃРєСЂС‹С‚РёСЏ, РѕС‚РєР»РёРєРё) РїСЂРµРґР»РѕР¶Рё 2-3 РїСЂР°РІРёР»Р° РґР»СЏ Р°РІС‚Рѕ-С„РёР»СЊС‚СЂР°С†РёРё. "
            "РќР°РїСЂРёРјРµСЂ: 'С‚С‹ С‡Р°СЃС‚Рѕ СЃРєСЂС‹РІР°РµС€СЊ РІР°РєР°РЅСЃРёРё РіРґРµ РІ РЅР°Р·РІР°РЅРёРё РµСЃС‚СЊ X вЂ” РґРѕР±Р°РІРёС‚СЊ X РІ СЃС‚РѕРї-СЃР»РѕРІР°' "
            "РёР»Рё 'С‚С‹ СЃРѕС…СЂР°РЅСЏРµС€СЊ РІСЃРµ РІР°РєР°РЅСЃРёРё РєРѕРјРїР°РЅРёРё Y вЂ” СЃРѕР·РґР°С‚СЊ Р°Р»РµСЂС‚ РЅР° Y'. "
            "РљРѕРЅРєСЂРµС‚РЅРѕ, РїРѕ РґРµР»Сѓ, РЅР° СЂСѓСЃСЃРєРѕРј."
        )
        user_prompt = f"РСЃС‚РѕСЂРёСЏ РґРµР№СЃС‚РІРёР№ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ:\n{json.dumps(stats, ensure_ascii=False, indent=2)}"
        try:
            return chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                ai_config,
            )
        except Exception as exc:
            return f"РћС€РёР±РєР°: {exc}"

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
            junior_kw = ["junior", "РґР¶СѓРЅРёРѕСЂ", "РЅР°С‡РёРЅР°СЋС‰РёР№", "СЃС‚Р°Р¶РµСЂ", "intern", "trainee",
                         "РјР»Р°РґС€РёР№"]
            senior_kw = ["senior", "СЃРµРЅСЊРѕСЂ", "РІРµРґСѓС‰РёР№", "lead", "С‚РёРјР»РёРґ", "tech lead",
                         "team lead", "СЂСѓРєРѕРІРѕРґРёС‚РµР»СЊ", "architect"]
            is_junior = any(kw in title_lower for kw in junior_kw)
            is_senior = any(kw in title_lower for kw in senior_kw)
            if is_junior:
                by_level["junior"] += 1
            elif is_senior:
                by_level["senior"] += 1
            else:
                by_level["middle"] += 1
            remote_kw = ["СѓРґР°Р»РµРЅ", "remote", "СѓРґР°Р»С‘РЅ"]
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

    def _collector(self, source_name: str, source_config: dict[str, Any]):
        if source_name == "hh":
            return HHSource(source_config)
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
