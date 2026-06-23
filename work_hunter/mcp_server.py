from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import mcp.server.stdio
from mcp.server import Server
from mcp.types import TextContent, Tool

from .campaigns.policy import campaign_policy_gate
from .config import mask_secrets
from .hh_agent.mcp_handlers import HHMCPToolHandlers
from .services import WorkHunter
from .sources import PUBLIC_BOARD_SOURCE_NAMES


SERVER_NAME = "work-hunter"
MCP_SOURCE_CHOICES = ["hh", "habr", "geekjob", "telegram", *PUBLIC_BOARD_SOURCE_NAMES]

app = Server(SERVER_NAME)
_root: Path = Path.cwd()


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_jobs",
            description="Collect fresh jobs from enabled sources and store them locally.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sources": {
                        "type": "array",
                        "items": {"type": "string", "enum": MCP_SOURCE_CHOICES},
                    },
                    "limit": {"type": "integer", "minimum": 1},
                    "score": {"type": "boolean", "default": True},
                },
            },
        ),
        Tool(
            name="score_jobs",
            description="Score stored jobs against the active profile.",
            inputSchema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1}},
            },
        ),
        Tool(
            name="list_jobs",
            description="List stored jobs sorted by score and recency.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "default": 20},
                    "source": {"type": "string", "enum": MCP_SOURCE_CHOICES},
                    "status": {"type": "string"},
                    "min_score": {"type": "integer"},
                },
            },
        ),
        Tool(
            name="get_job",
            description="Get one job with score reasons and red flags.",
            inputSchema={
                "type": "object",
                "properties": {"job_id": {"type": "integer"}},
                "required": ["job_id"],
            },
        ),
        Tool(
            name="prepare_cover_letter",
            description="Create and store a deterministic cover-letter draft for a job.",
            inputSchema={
                "type": "object",
                "properties": {"job_id": {"type": "integer"}},
                "required": ["job_id"],
            },
        ),
        Tool(
            name="apply_hh",
            description="Prepare an HH apply plan. Real apply is blocked through MCP.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "integer"},
                    "resume_id": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": True},
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="prepare_apply_plan",
            description="Prepare a local apply plan for a job without sending a real application.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "integer"},
                    "resume_id": {"type": "string"},
                    "letter": {"type": "string"},
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="build_application_pack",
            description="Build a masked application pack preview for policy review.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "integer"},
                    "resume_variant": {"type": "object"},
                    "cover_letter": {"type": "string"},
                    "short_message": {"type": "string"},
                    "source_payload": {"type": "object"},
                    "campaign_policy": {"type": "object"},
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="preview_application",
            description="Preview a job application plan and optional masked application pack without submitting.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "integer"},
                    "resume_id": {"type": "string"},
                    "letter": {"type": "string"},
                    "resume_variant": {"type": "object"},
                    "short_message": {"type": "string"},
                    "source_payload": {"type": "object"},
                    "campaign_policy": {"type": "object"},
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="apply_job",
            description="Prepare an application. Real submission is blocked through MCP.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "integer"},
                    "resume_id": {"type": "string"},
                    "letter": {"type": "string"},
                    "form": {"type": "object"},
                    "dry_run": {"type": "boolean", "default": True},
                    "submit": {"type": "boolean", "default": False},
                    "confirm": {"type": "boolean", "default": False},
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="mark_job",
            description="Mark a job status in the local pipeline.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "integer"},
                    "status": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["job_id", "status"],
            },
        ),
        Tool(
            name="daily_report",
            description="Summarize source counts, source errors, and top matching jobs.",
            inputSchema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "default": 10}},
            },
        ),
        Tool(
            name="candidate_onboarding_next",
            description="Return candidate onboarding completeness and the next unanswered profile question.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="candidate_add_fact",
            description="Add an unconfirmed candidate fact from onboarding or a generic fact payload.",
            inputSchema={
                "type": "object",
                "properties": {
                    "question_id": {"type": "string"},
                    "category": {"type": "string"},
                    "key": {"type": "string"},
                    "answer": {"type": "string"},
                    "value": {},
                    "source": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        ),
        Tool(
            name="candidate_confirm_fact",
            description="Confirm a candidate fact so it may be used in truthful resume variants.",
            inputSchema={
                "type": "object",
                "properties": {"fact_id": {"type": "integer"}},
                "required": ["fact_id"],
            },
        ),
        Tool(
            name="candidate_profile_summary",
            description="Summarize candidate facts and completeness for the active profile.",
            inputSchema={
                "type": "object",
                "properties": {"status": {"type": "string"}},
            },
        ),
        Tool(
            name="resume_build_variant",
            description="Build a truthful resume variant for a job using confirmed candidate facts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "integer"},
                    "resume_id": {"type": "integer"},
                },
                "required": ["job_id", "resume_id"],
            },
        ),
        Tool(
            name="campaign_plan",
            description="Plan an HH campaign from stored scored vacancies without sending applications.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 100},
                    "min_score": {"type": "integer", "default": 0},
                    "skip_tests": {"type": "boolean", "default": False},
                    "ai_filter_mode": {"type": "string", "default": "off"},
                    "resume_id": {"type": "string"},
                    "daily_cap": {"type": "integer"},
                },
            },
        ),
        Tool(
            name="campaign_review",
            description="Review planned campaign runs and items.",
            inputSchema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "integer"},
                    "limit": {"type": "integer", "default": 20},
                },
            },
        ),
        Tool(
            name="campaign_run",
            description="Run an enabled HH campaign only when confirm is true.",
            inputSchema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "integer"},
                    "confirm": {"type": "boolean", "default": False},
                    "preset": {"type": "string"},
                    "campaign_policy": {"type": "object"},
                },
                "required": ["run_id"],
            },
        ),
        Tool(
            name="campaign_pause",
            description="Pause campaign execution through the kill switch.",
            inputSchema={
                "type": "object",
                "properties": {"reason": {"type": "string"}},
            },
        ),
        Tool(
            name="campaign_kill",
            description="Engage the campaign kill switch.",
            inputSchema={
                "type": "object",
                "properties": {"reason": {"type": "string"}},
            },
        ),
        Tool(
            name="campaign_replay",
            description="Return replay timeline events for a campaign run.",
            inputSchema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "integer"},
                    "source": {"type": "string"},
                    "event_type": {"type": "string"},
                },
                "required": ["run_id"],
            },
        ),
        Tool(
            name="browser_check_session",
            description="Check Browser Session Lab status for a source.",
            inputSchema={
                "type": "object",
                "properties": {"source": {"type": "string", "enum": MCP_SOURCE_CHOICES}},
            },
        ),
        Tool(
            name="source_status",
            description="Return source adapter readiness and apply maturity badges.",
            inputSchema={
                "type": "object",
                "properties": {"source": {"type": "string", "enum": MCP_SOURCE_CHOICES}},
            },
        ),
        Tool(
            name="source_certification_matrix",
            description="Return L5/L6 external source certification evidence readiness without submitting applications.",
            inputSchema={
                "type": "object",
                "properties": {
                    "level": {"type": "integer", "minimum": 0, "maximum": 6, "default": 5},
                    "sources": {
                        "type": "array",
                        "items": {"type": "string", "enum": MCP_SOURCE_CHOICES},
                    },
                    "evidence": {"type": "object"},
                },
            },
        ),
        Tool(
            name="source_certification_plan",
            description="Return safe next actions for collecting missing external source certification evidence without promoting or submitting applications.",
            inputSchema={
                "type": "object",
                "properties": {
                    "level": {"type": "integer", "minimum": 0, "maximum": 6, "default": 5},
                    "sources": {
                        "type": "array",
                        "items": {"type": "string", "enum": MCP_SOURCE_CHOICES},
                    },
                    "evidence": {"type": "object"},
                },
            },
        ),
        Tool(
            name="source_certification_evidence",
            description="Record external source certification evidence without promoting maturity or submitting applications.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": MCP_SOURCE_CHOICES},
                    "level": {"type": "integer", "minimum": 0, "maximum": 6, "default": 5},
                    "evidence": {"type": "object"},
                },
                "required": ["source", "evidence"],
            },
        ),
        Tool(
            name="source_certify",
            description="Promote an external source to certified L5/L6 maturity only when certification evidence is complete.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": MCP_SOURCE_CHOICES},
                    "level": {"type": "integer", "minimum": 0, "maximum": 6, "default": 5},
                    "evidence": {"type": "object"},
                },
                "required": ["source"],
            },
        ),
        Tool(
            name="source_external_apply_target",
            description="Configure an external apply target session, URL, method, and payload template without promoting maturity.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": MCP_SOURCE_CHOICES},
                    "session": {"type": "string"},
                    "url": {"type": "string"},
                    "method": {"type": "string", "default": "POST"},
                    "payload_template": {"type": "object"},
                    "level": {"type": "integer", "minimum": 0, "maximum": 6, "default": 5},
                },
                "required": ["source", "session", "url"],
            },
        ),
        Tool(
            name="source_external_apply_from_har",
            description="Import a local HAR and configure the best detected external apply endpoint without promoting maturity or submitting.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": MCP_SOURCE_CHOICES},
                    "path": {"type": "string"},
                    "hosts": {"type": "array", "items": {"type": "string"}},
                    "level": {"type": "integer", "minimum": 0, "maximum": 6, "default": 5},
                },
                "required": ["source", "path"],
            },
        ),
        Tool(
            name="source_redaction_scan",
            description="Run and record a source-specific external apply redaction scan as certification evidence without promoting maturity.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": MCP_SOURCE_CHOICES},
                    "payload": {"type": "object"},
                    "text": {"type": "string"},
                    "level": {"type": "integer", "minimum": 0, "maximum": 6, "default": 5},
                },
                "required": ["source"],
            },
        ),
        Tool(
            name="source_sync",
            description="Sync one or more sources and optionally score imported jobs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": MCP_SOURCE_CHOICES},
                    "sources": {
                        "type": "array",
                        "items": {"type": "string", "enum": MCP_SOURCE_CHOICES},
                    },
                    "limit": {"type": "integer", "minimum": 1},
                    "score": {"type": "boolean", "default": True},
                },
            },
        ),
        Tool(
            name="source_prepare_apply",
            description="Prepare a source-specific apply plan without submitting.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "integer"},
                    "resume_id": {"type": "string"},
                    "letter": {"type": "string"},
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="source_dry_run_apply",
            description="Dry-run an external source form fill. Never submits.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "integer"},
                    "form": {"type": "object"},
                    "resume_variant": {"type": "object"},
                    "cover_letter": {"type": "string"},
                    "short_message": {"type": "string"},
                    "campaign_policy": {"type": "object"},
                    "extra_answers": {"type": "object"},
                },
                "required": ["job_id", "form"],
            },
        ),
        *HHMCPToolHandlers.tool_definitions(),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    args = arguments or {}
    service = WorkHunter(_root)
    try:
        hh_handlers = HHMCPToolHandlers(service)
        if hh_handlers.can_handle(name):
            return _json(hh_handlers.handle(name, args))
        if name == "search_jobs":
            result = service.sync_sources(
                sources=args.get("sources"),
                limit=args.get("limit"),
            )
            if args.get("score", True):
                result["scored"] = service.score_jobs()
            return _json(result)
        if name == "score_jobs":
            return _json({"scored": service.score_jobs(limit=args.get("limit", 10000))})
        if name == "list_jobs":
            jobs = service.list_jobs(
                limit=args.get("limit", 20),
                source=args.get("source"),
                status=args.get("status"),
                min_score=args.get("min_score"),
            )
            return _json([job.to_dict() for job in jobs])
        if name == "get_job":
            job = service.get_job(int(args["job_id"]))
            return _json(job.to_dict() if job else {"error": "not_found"})
        if name == "prepare_cover_letter":
            draft = service.prepare_letter(int(args["job_id"]))
            return _json(draft.to_dict())
        if name == "apply_hh":
            if args.get("dry_run", True) is False:
                return _json(
                    {
                        "status": "blocked",
                        "message": "MCP cannot send real HH applications. Use the local UI confirm-apply flow.",
                    }
                )
            return _json(
                service.prepare_apply_plan(
                    int(args["job_id"]),
                    resume_id=args.get("resume_id"),
                )
            )
        if name == "prepare_apply_plan":
            result = service.prepare_apply_plan(
                int(args["job_id"]),
                resume_id=args.get("resume_id"),
                letter=args.get("letter"),
            )
            return _json(result)
        if name == "build_application_pack":
            return _json(
                service.build_application_pack(
                    int(args["job_id"]),
                    resume_variant=dict(args.get("resume_variant") or {}),
                    cover_letter=str(args.get("cover_letter") or ""),
                    short_message=str(args.get("short_message") or ""),
                    source_payload=dict(args.get("source_payload") or {}),
                    campaign_policy=dict(args.get("campaign_policy") or {}),
                )
            )
        if name == "preview_application":
            return _json(_preview_application(service, args))
        if name == "apply_job":
            return _json(_apply_job(service, args))
        if name == "mark_job":
            service.mark_job(
                int(args["job_id"]),
                str(args["status"]),
                str(args.get("note") or ""),
            )
            return _json({"status": "ok"})
        if name == "daily_report":
            return _json(service.daily_report(limit=args.get("limit", 10)))
        if name == "candidate_onboarding_next":
            return _json(_candidate_onboarding_next(service))
        if name == "candidate_add_fact":
            return _json(_candidate_add_fact(service, args))
        if name == "candidate_confirm_fact":
            return _json(service.confirm_candidate_fact(int(args["fact_id"])))
        if name == "candidate_profile_summary":
            return _json(
                {
                    "completeness": service.candidate_completeness(),
                    "facts": service.candidate_facts(status=args.get("status")),
                }
            )
        if name == "resume_build_variant":
            return _json(service.build_resume_variant(int(args["job_id"]), int(args["resume_id"])))
        if name == "campaign_plan":
            return _json(
                service.plan_hh_campaign(
                    limit=int(args.get("limit") or 100),
                    min_score=int(args.get("min_score") or 0),
                    skip_tests=bool(args.get("skip_tests", False)),
                    ai_filter_mode=str(args.get("ai_filter_mode") or "off"),
                    resume_id=args.get("resume_id"),
                    daily_cap=args.get("daily_cap"),
                )
            )
        if name == "campaign_review":
            return _json(_campaign_review(service, args))
        if name == "campaign_run":
            return _json(_campaign_run(service, args))
        if name == "campaign_pause":
            return _json(service.pause_hh_agent(reason=str(args.get("reason") or "mcp")))
        if name == "campaign_kill":
            result = service.pause_hh_agent(reason=str(args.get("reason") or "mcp_kill"))
            return _json({**result, "status": "killed"})
        if name == "campaign_replay":
            return _json(
                service.replay_for_run(
                    int(args["run_id"]),
                    source=args.get("source"),
                    event_type=args.get("event_type"),
                )
            )
        if name == "browser_check_session":
            return _json(service.browser_lab_status(str(args.get("source") or "getmatch")))
        if name == "source_status":
            report = service.source_capabilities()
            source = str(args.get("source") or "")
            return _json({source: report.get(source)} if source else report)
        if name == "source_certification_matrix":
            raw_sources = args.get("sources")
            sources = [str(source) for source in raw_sources] if isinstance(raw_sources, list) else None
            return _json(
                service.source_certification_matrix(
                    level=int(args.get("level") or 5),
                    sources=sources,
                    evidence=dict(args.get("evidence") or {}),
                )
            )
        if name == "source_certification_plan":
            raw_sources = args.get("sources")
            sources = [str(source) for source in raw_sources] if isinstance(raw_sources, list) else None
            return _json(
                service.source_certification_plan(
                    level=int(args.get("level") or 5),
                    sources=sources,
                    evidence=dict(args.get("evidence") or {}),
                )
            )
        if name == "source_certification_evidence":
            return _json(
                service.record_source_certification_evidence(
                    str(args.get("source") or ""),
                    level=int(args.get("level") or 5),
                    evidence=dict(args.get("evidence") or {}),
                )
            )
        if name == "source_certify":
            return _json(
                service.promote_source_certification(
                    str(args.get("source") or ""),
                    level=int(args.get("level") or 5),
                    evidence=mask_secrets(dict(args.get("evidence") or {})),
                )
            )
        if name == "source_external_apply_target":
            return _json(
                service.configure_source_external_apply_target(
                    str(args.get("source") or ""),
                    session=str(args.get("session") or ""),
                    url=str(args.get("url") or ""),
                    method=str(args.get("method") or "POST"),
                    payload_template=dict(args.get("payload_template") or {}),
                    level=int(args.get("level") or 5),
                )
            )
        if name == "source_external_apply_from_har":
            raw_hosts = args.get("hosts")
            hosts = {str(host) for host in raw_hosts} if isinstance(raw_hosts, list) else None
            return _json(
                service.configure_source_external_apply_from_har(
                    str(args.get("source") or ""),
                    Path(str(args.get("path") or "")),
                    allowed_hosts=hosts,
                    level=int(args.get("level") or 5),
                )
            )
        if name == "source_redaction_scan":
            return _json(
                service.record_source_redaction_scan(
                    str(args.get("source") or ""),
                    payload=dict(args.get("payload") or {}),
                    text=str(args.get("text") or ""),
                    level=int(args.get("level") or 5),
                )
            )
        if name == "source_sync":
            sources = _source_args(args)
            result = service.sync_sources(sources=sources, limit=args.get("limit"))
            if args.get("score", True):
                result["scored"] = service.score_jobs()
            return _json(result)
        if name == "source_prepare_apply":
            return _json(
                {
                    **service.prepare_apply_plan(
                        int(args["job_id"]),
                        resume_id=args.get("resume_id"),
                        letter=args.get("letter"),
                    ),
                    "submit": False,
                }
            )
        if name == "source_dry_run_apply":
            return _json(
                service.external_apply_dry_run(
                    int(args["job_id"]),
                    form=dict(args.get("form") or {}),
                    resume_variant=dict(args.get("resume_variant") or {}),
                    cover_letter=str(args.get("cover_letter") or ""),
                    short_message=str(args.get("short_message") or ""),
                    campaign_policy=dict(args.get("campaign_policy") or {}),
                    extra_answers=dict(args.get("extra_answers") or {}),
                )
            )
        return _json({"error": f"Unknown tool: {name}"})
    except Exception as exc:
        return _json({"error": str(exc)})


def _candidate_onboarding_next(service: WorkHunter) -> dict[str, Any]:
    questions = service.onboarding_questions()
    completeness = service.candidate_completeness()
    missing = set(completeness.get("missing") or [])
    next_question = next((question for question in questions if question["id"] in missing), None)
    return {
        "status": completeness["status"],
        "completeness": completeness,
        "next_question": next_question,
        "questions": questions,
        "facts": service.candidate_facts(),
    }


def _candidate_add_fact(service: WorkHunter, args: dict[str, Any]) -> dict[str, Any]:
    question_id = str(args.get("question_id") or args.get("key") or "").strip()
    answer = args.get("answer", args.get("value", ""))
    question_ids = {question["id"] for question in service.onboarding_questions()}
    if question_id in question_ids:
        return service.answer_onboarding(
            question_id,
            str(answer),
            source=str(args.get("source") or "mcp"),
        )

    key = question_id or str(args.get("key") or "fact").strip() or "fact"
    category = str(args.get("category") or key).strip()
    profile_id = str(service.config.get("profile") or "default")
    fact_id = service.storage.save_candidate_fact(
        profile_id=profile_id,
        category=category,
        key=key,
        value=answer,
        confidence=float(args.get("confidence") or 0.7),
        source=str(args.get("source") or "mcp"),
        status="unconfirmed",
        evidence={"tool": "candidate_add_fact"},
    )
    fact = service.storage.list_candidate_facts(profile_id=profile_id)[-1]
    service.record_replay_event(
        source="candidate",
        event_type="candidate_fact_created",
        title="Candidate fact captured",
        data={"fact": fact},
    )
    return {"status": "recorded", "facts": [{"id": fact_id, **fact}]}


def _preview_application(service: WorkHunter, args: dict[str, Any]) -> dict[str, Any]:
    job_id = int(args["job_id"])
    plan = service.prepare_apply_plan(
        job_id,
        resume_id=args.get("resume_id"),
        letter=args.get("letter"),
    )
    payload: dict[str, Any] = {
        "status": "preview",
        "job_id": job_id,
        "plan": {**plan, "submit": False},
        "submit": False,
    }
    if args.get("resume_variant") is not None or args.get("source_payload") is not None:
        payload["application_pack"] = service.build_application_pack(
            job_id,
            resume_variant=dict(args.get("resume_variant") or {}),
            cover_letter=str(args.get("letter") or args.get("cover_letter") or ""),
            short_message=str(args.get("short_message") or ""),
            source_payload=dict(args.get("source_payload") or {}),
            campaign_policy=dict(args.get("campaign_policy") or {}),
        )
    return payload


def _apply_job(service: WorkHunter, args: dict[str, Any]) -> dict[str, Any]:
    job_id = int(args["job_id"])
    wants_real_submit = (
        args.get("dry_run", True) is False
        or bool(args.get("submit", False))
        or bool(args.get("confirm", False))
    )
    if wants_real_submit:
        return {
            "status": "blocked",
            "reason": "real_apply_requires_campaign_policy",
            "message": "MCP cannot send real applications. Use the local UI confirm flow with explicit review.",
            "job_id": job_id,
            "submit": False,
        }
    if args.get("form"):
        return service.external_apply_dry_run(
            job_id,
            form=dict(args.get("form") or {}),
            cover_letter=str(args.get("letter") or args.get("cover_letter") or ""),
        )
    plan = service.prepare_apply_plan(
        job_id,
        resume_id=args.get("resume_id"),
        letter=args.get("letter"),
    )
    return {"status": "preview", "job_id": job_id, "plan": {**plan, "submit": False}, "submit": False}


def _campaign_run(service: WorkHunter, args: dict[str, Any]) -> dict[str, Any]:
    run_id = int(args["run_id"])
    if bool(args.get("confirm", False)):
        policy_block = _mcp_campaign_policy_block(service, run_id, args)
        if policy_block is not None:
            return policy_block
    return service.confirm_enabled_hh_campaign(run_id, confirm=bool(args.get("confirm", False)))


def _mcp_campaign_policy_block(service: WorkHunter, run_id: int, args: dict[str, Any]) -> dict[str, Any] | None:
    run = service.storage.get_hh_campaign_run(run_id)
    if run is None:
        raise ValueError(f"HH campaign run {run_id} not found")
    preset_name = str(args.get("preset") or "").strip()
    if not preset_name:
        return _mcp_policy_block(run_id, ["mcp_preset_required"], run_status=run.status)
    try:
        preset = service.get_hh_campaign_preset(preset_name)["params"]
    except Exception:
        return _mcp_policy_block(run_id, ["mcp_preset_missing"], run_status=run.status, preset=preset_name)
    policy = _mcp_campaign_policy(run, preset, dict(args.get("campaign_policy") or {}))
    replay_events = service.storage.list_replay_events(run_id=run_id, limit=1)
    audit_initialized = bool(replay_events)
    policy_reasons: list[str] = []
    for item in service.storage.list_hh_campaign_items(run_id):
        if item.status != "ready":
            continue
        job = service.storage.get_job(item.job_id)
        if job is None:
            policy_reasons.append("job_missing")
            continue
        source_status = service.source_capabilities().get(job.source) or {}
        gate = campaign_policy_gate(
            preset=policy,
            source_level=int(source_status.get("level") or (6 if job.source == "hh" else 0)),
            job_score=int(job.score.total_score if job.score else 0),
            item={
                "source": job.source,
                "has_test": "test_required" in item.risk_flags,
                "unknown_form": "unknown_form" in item.risk_flags,
                "captcha": "captcha_or_challenge" in item.risk_flags,
                "challenge": "captcha_or_challenge" in item.risk_flags,
                "blacklisted": False,
                "duplicate_employer": False,
            },
            counters={"daily": 0, "source": 0, "company": 0},
            kill_switch_paused=False,
            resume_variant_valid=bool(item.resume_id),
            cover_letter_generated=bool(str(item.letter or "").strip()),
            payload_preview_saved=bool(item.raw_result),
            session_valid=True,
            audit_initialized=audit_initialized,
        )
        policy_reasons.extend(str(reason) for reason in gate.get("reasons") or [])
    policy_reasons = list(dict.fromkeys(policy_reasons))
    if policy_reasons:
        return _mcp_policy_block(run_id, policy_reasons, run_status=run.status, preset=preset_name)
    return None


def _mcp_campaign_policy(run: Any, preset: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "min_score": int((run.filters or {}).get("min_score") or 0),
        "daily_cap": 10**9,
        "per_company_cap": 10**9,
        "per_source_cap": {},
        "skip_tests": True,
        "skip_unknown_forms": True,
        "skip_captcha": True,
        "skip_challenges": True,
        "blacklist_enabled": True,
    }
    return {**defaults, **preset, **override}


def _mcp_policy_block(
    run_id: int,
    policy_reasons: list[str],
    *,
    run_status: str = "",
    preset: str = "",
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": "real_apply_requires_campaign_policy",
        "message": "MCP real campaign runs require an explicit enabled preset policy and passing audit gate.",
        "id": run_id,
        "run_status": run_status,
        "preset": preset,
        "policy_reasons": policy_reasons,
        "submit": False,
    }


def _campaign_review(service: WorkHunter, args: dict[str, Any]) -> dict[str, Any]:
    if args.get("run_id") is None:
        runs = service.storage.list_hh_campaign_runs(limit=int(args.get("limit") or 20))
        return {
            "runs": [run.to_dict() for run in runs],
        }
    run_id = int(args["run_id"])
    run = service.storage.get_hh_campaign_run(run_id)
    items = []
    for item in service.storage.list_hh_campaign_items(run_id):
        job = service.storage.get_job(item.job_id)
        items.append(
            {
                **item.to_dict(),
                "job": job.to_dict() if job is not None else None,
            }
        )
    return {
        "run": run.to_dict() if run is not None else {"id": run_id, "status": "missing"},
        "items": items,
    }


def _source_args(args: dict[str, Any]) -> list[str] | None:
    if args.get("sources"):
        return [str(source) for source in args["sources"]]
    if args.get("source"):
        return [str(args["source"])]
    return None


def _json(data: Any) -> list[TextContent]:
    return [
        TextContent(
            type="text",
            text=json.dumps(mask_secrets(data), ensure_ascii=False, indent=2),
        )
    ]


async def _run() -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main(root: str | Path | None = None) -> None:
    global _root
    _root = Path(root) if root is not None else Path.cwd()
    asyncio.run(_run())
