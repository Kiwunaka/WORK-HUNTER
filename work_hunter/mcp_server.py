from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import mcp.server.stdio
from mcp.server import Server
from mcp.types import TextContent, Tool

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
        if name == "doctor":
            return _json(service.doctor())
        if name == "source_capabilities":
            return _json(service.source_capabilities())
        if name == "hh_auth_status":
            return _json(service.hh_auth_status())
        if name == "list_strategies":
            return _json(service.list_strategies())
        if name == "run_strategy":
            return _json(
                service.run_strategy(
                    str(args.get("name") or "active-profile"),
                    dry_run=not bool(args.get("confirm")),
                    confirm=bool(args.get("confirm")),
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
    except Exception as exc:
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
