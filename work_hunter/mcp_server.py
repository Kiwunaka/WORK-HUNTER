from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import mcp.server.stdio
from mcp.server import Server
from mcp.types import TextContent, Tool

from .config import mask_secrets
from .hh_agent.mcp_handlers import HHMCPToolHandlers
from .models import CalendarEvent
from .safety import is_literal_confirmation
from .services import WorkHunter
from .sources import PUBLIC_BOARD_SOURCE_NAMES

SERVER_NAME = "work-hunter"
MCP_SOURCE_CHOICES = ["hh", "linkedin", "habr", "geekjob", "telegram", *PUBLIC_BOARD_SOURCE_NAMES]

app = Server(SERVER_NAME)
_root: Path = Path.cwd()


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="doctor",
            description="Report local installation, auth, source, UI, and MCP readiness.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="source_capabilities",
            description="List source capabilities, auth requirements, and enabled state.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="hh_auth_status",
            description="Check HH API auth status through the guarded local service.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="list_strategies",
            description="List stored and generated search strategies.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="run_strategy",
            description="Dry-run a search strategy by default. Live search import requires confirm=true.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "confirm": {"type": "boolean", "default": False},
                    "resume_id": {"type": "string"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="strategy_report",
            description="Report last strategy run and top matching local jobs.",
            inputSchema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        ),
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
            name="ats_resume_audit",
            description="Audit resume text for ATS compatibility, optionally against a stored job.",
            inputSchema={
                "type": "object",
                "properties": {
                    "resume_text": {"type": "string", "minLength": 1},
                    "job_id": {"type": "integer", "minimum": 1},
                },
                "required": ["resume_text"],
            },
        ),
        Tool(
            name="prepare_interview_brief",
            description=(
                "Prepare a Russian interview brief for a stored job: TL;DR, "
                "questions for the employer, and a candidate STAR pitch."
            ),
            inputSchema={
                "type": "object",
                "properties": {"job_id": {"type": "integer", "minimum": 1}},
                "required": ["job_id"],
            },
        ),
        Tool(
            name="save_calendar_event",
            description=(
                "Save an interview, follow-up, reminder, or task in the local "
                "Work Hunter calendar. This does not sync Google Calendar."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "integer", "minimum": 1},
                    "title": {"type": "string", "minLength": 1},
                    "event_type": {
                        "type": "string",
                        "enum": ["interview", "follow_up", "reminder", "task"],
                        "default": "interview",
                    },
                    "event_date": {"type": "string", "minLength": 1},
                    "notes": {"type": "string"},
                },
                "required": ["title", "event_date"],
            },
        ),
        Tool(
            name="apply_hh",
            description="Prepare or confirm an HH application through the shared apply flow.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "integer"},
                    "resume_id": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": True},
                    "confirm": {"type": "boolean", "default": False},
                    "letter": {"type": "string"},
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
            name="apply_job",
            description=(
                "Apply to an HH, LinkedIn, or external-board job. "
                "A real submission requires literal confirm=true."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "integer"},
                    "resume_id": {"type": "string"},
                    "letter": {"type": "string"},
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
            name="hh_autopilot_status",
            description="Read the masked HH autopilot status and quota view.",
            inputSchema={
                "type": "object",
                "properties": {"account": {"type": "string"}},
            },
        ),
        Tool(
            name="hh_autopilot_history",
            description="Read masked HH autopilot state history.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string"},
                    "vacancy_id": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
            },
        ),
        Tool(
            name="hh_autopilot_challenges",
            description="List masked HH autopilot challenges without resolving them.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
            },
        ),
        *HHMCPToolHandlers.tool_definitions(),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    args = arguments or {}
    service = WorkHunter(_root)
    try:
        if name == "hh_autopilot_status":
            return _json(mask_secrets(service.hh_autopilot_status(args.get("account"))))
        if name == "hh_autopilot_history":
            return _json(
                mask_secrets(
                    service.hh_autopilot_history(
                        account=args.get("account"),
                        vacancy_id=args.get("vacancy_id"),
                        limit=args.get("limit", 100),
                    )
                )
            )
        if name == "hh_autopilot_challenges":
            return _json(
                mask_secrets(
                    service.hh_autopilot_challenges(
                        account=args.get("account"),
                        limit=args.get("limit", 100),
                    )
                )
            )
        hh_handlers = HHMCPToolHandlers(service)
        if hh_handlers.can_handle(name):
            return _json(hh_handlers.handle(name, args))
        if name == "doctor":
            return _json(service.doctor())
        if name == "source_capabilities":
            return _json(service.source_capabilities())
        if name == "hh_auth_status":
            return _json(service.hh_auth_status())
        if name == "list_strategies":
            return _json(service.list_strategies())
        if name == "run_strategy":
            confirm = is_literal_confirmation(args.get("confirm"))
            return _json(
                service.run_strategy(
                    str(args.get("name") or "active-profile"),
                    dry_run=not confirm,
                    confirm=confirm,
                    resume_id=args.get("resume_id"),
                )
            )
        if name == "strategy_report":
            return _json(service.strategy_report(str(args.get("name") or "active-profile")))
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
        if name == "ats_resume_audit":
            resume_text = str(args.get("resume_text") or "").strip()
            if not resume_text:
                raise ValueError("resume_text is required")
            job_id = args.get("job_id")
            return _json(
                {
                    "content": service.ats_audit(
                        resume_text,
                        job_id=int(job_id) if job_id is not None else None,
                    )
                }
            )
        if name == "prepare_interview_brief":
            job_id = int(args["job_id"])
            job = service.get_job(job_id)
            if job is None:
                return _json({"error": "not_found", "job_id": job_id})
            return _json(
                {
                    "job": {
                        "id": job.id,
                        "title": job.title,
                        "company": job.company,
                        "url": job.url,
                    },
                    "tldr": service.summarize_job(job_id),
                    "questions": service.interview_questions(job_id),
                    "star_pitch": service.experience_pitch(job_id),
                }
            )
        if name == "save_calendar_event":
            title = str(args.get("title") or "").strip()
            event_date = str(args.get("event_date") or "").strip()
            if not title:
                raise ValueError("title is required")
            if not event_date:
                raise ValueError("event_date is required")
            try:
                datetime.fromisoformat(event_date)
            except ValueError as exc:
                raise ValueError("event_date must be ISO 8601") from exc
            job_id_value = args.get("job_id")
            job_id = int(job_id_value) if job_id_value is not None else None
            if job_id is not None and job_id <= 0:
                raise ValueError("job_id must be a positive integer")
            if job_id is not None and service.get_job(job_id) is None:
                return _json({"error": "job_not_found", "job_id": job_id})
            event_type = str(args.get("event_type") or "interview")
            if event_type not in {"interview", "follow_up", "reminder", "task"}:
                raise ValueError("unsupported event_type")
            event = CalendarEvent(
                job_id=job_id,
                title=title,
                event_type=event_type,
                event_date=event_date,
                notes=str(args.get("notes") or ""),
            )
            event_id = service.storage.save_event(event)
            return _json({"status": "saved", "calendar": "local", "event_id": event_id})
        if name == "apply_hh":
            confirm = is_literal_confirmation(args.get("confirm"))
            if args.get("dry_run", True) is False or confirm:
                return _json(
                    service.confirm_apply(
                        int(args["job_id"]),
                        resume_id=args.get("resume_id"),
                        letter=args.get("letter"),
                        confirm=confirm,
                    )
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
        if name == "apply_job":
            return _json(
                service.confirm_apply(
                    int(args["job_id"]),
                    resume_id=args.get("resume_id"),
                    letter=args.get("letter"),
                    confirm=is_literal_confirmation(args.get("confirm")),
                )
            )
        if name == "mark_job":
            service.mark_job(
                int(args["job_id"]),
                str(args["status"]),
                str(args.get("note") or ""),
            )
            return _json({"status": "ok"})
        if name == "daily_report":
            return _json(service.daily_report(limit=args.get("limit", 10)))
        return _json({"error": f"Unknown tool: {name}"})
    except Exception as exc:  # noqa: BLE001 - MCP failures must cross the JSON boundary.
        return _json({"error": str(exc)})


def _json(data: Any) -> list[TextContent]:
    return [
        TextContent(
            type="text",
            text=json.dumps(data, ensure_ascii=False, indent=2),
        )
    ]


async def _run() -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main(root: str | Path | None = None) -> None:
    global _root
    _root = Path(root) if root is not None else Path.cwd()
    asyncio.run(_run())
