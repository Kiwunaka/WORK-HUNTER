from __future__ import annotations

from typing import Any

from mcp.types import Tool

from ..safety import is_literal_confirmation


class HHMCPToolHandlers:
    TOOL_NAMES = {
        "hh_whoami",
        "hh_list_resumes",
        "hh_search_vacancies",
        "hh_get_vacancy",
        "hh_analyze_vacancy",
        "hh_research_vacancies",
        "hh_apply_vacancy",
        "hh_research_and_apply",
    }

    def __init__(self, service: Any):
        self.service = service

    @classmethod
    def tool_definitions(cls) -> list[Tool]:
        return [
            Tool(
                name="hh_whoami",
                description="Show current HH account identity through the configured token.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="hh_list_resumes",
                description="List locally synced HH resumes.",
                inputSchema={
                    "type": "object",
                    "properties": {"sync": {"type": "boolean", "default": False}},
                },
            ),
            Tool(
                name="hh_search_vacancies",
                description="Search HH vacancies through the HH API. Stores an MCP run audit record.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "area": {"type": "string"},
                        "per_page": {"type": "integer", "default": 20},
                        "page": {"type": "integer", "default": 0},
                    },
                },
            ),
            Tool(
                name="hh_get_vacancy",
                description="Fetch one HH vacancy by id.",
                inputSchema={
                    "type": "object",
                    "properties": {"vacancy_id": {"type": "string"}},
                    "required": ["vacancy_id"],
                },
            ),
            Tool(
                name="hh_analyze_vacancy",
                description="Analyze one HH vacancy. Current implementation records an audited placeholder until LLM service is wired into MCP.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "vacancy_id": {"type": "string"},
                        "resume_id": {"type": "string"},
                    },
                    "required": ["vacancy_id"],
                },
            ),
            Tool(
                name="hh_research_vacancies",
                description="Research HH vacancies in dry-run mode.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "limit": {"type": "integer", "default": 20},
                        "resume_id": {"type": "string"},
                    },
                },
            ),
            Tool(
                name="hh_apply_vacancy",
                description="Plan an HH vacancy application. Real apply is blocked unless explicitly enabled later through approval/confirm flow.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "vacancy_id": {"type": "string"},
                        "resume_id": {"type": "string"},
                        "confirm_apply": {"type": "boolean", "default": False},
                    },
                    "required": ["vacancy_id"],
                },
            ),
            Tool(
                name="hh_research_and_apply",
                description="Research and plan HH applications in dry-run mode.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "limit": {"type": "integer", "default": 20},
                        "resume_id": {"type": "string"},
                        "confirm_apply": {"type": "boolean", "default": False},
                    },
                },
            ),
        ]

    def can_handle(self, name: str) -> bool:
        return name in self.TOOL_NAMES

    def handle(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        run_id = self.service.storage.start_hh_agent_mcp_run(name, args)
        try:
            result = self._handle(name, args, run_id=run_id)
            self.service.storage.finish_hh_agent_mcp_run(run_id, status="ok", output=result)
            result.setdefault("mcp_run_id", run_id)
            return result
        except Exception as exc:
            self.service.storage.finish_hh_agent_mcp_run(run_id, status="error", error=str(exc))
            raise

    def _handle(self, name: str, args: dict[str, Any], *, run_id: int) -> dict[str, Any]:
        if name == "hh_whoami":
            return self.service.hh_whoami()
        if name == "hh_list_resumes":
            if args.get("sync"):
                self.service.sync_hh_resumes()
            return {"items": [resume.to_dict() for resume in self.service.storage.list_hh_resumes()]}
        if name == "hh_search_vacancies":
            params = _search_params(args)
            return self.service.hh_call_api("GET", "/vacancies", data=params)
        if name == "hh_get_vacancy":
            return self.service.hh_call_api("GET", f"/vacancies/{args['vacancy_id']}")
        if name == "hh_analyze_vacancy":
            return {
                "status": "planned",
                "vacancy_id": str(args["vacancy_id"]),
                "resume_id": str(args.get("resume_id") or ""),
                "message": "LLM-backed MCP analysis is wired in the next implementation slice.",
            }
        if name == "hh_research_vacancies":
            return self.service.run_hh_research_operation(
                text=str(args.get("text") or ""),
                limit=int(args.get("limit") or 20),
                resume_id=str(args.get("resume_id") or ""),
                run_id=run_id,
                plan_apply=False,
                confirm_apply=False,
            )
        if name == "hh_apply_vacancy":
            if args.get("confirm_apply"):
                return {
                    "status": "blocked",
                    "vacancy_id": str(args["vacancy_id"]),
                    "message": "MCP real apply is blocked until approval/confirm flow is enabled.",
                }
            return {
                "status": "planned",
                "vacancy_id": str(args["vacancy_id"]),
                "resume_id": str(args.get("resume_id") or ""),
                "dry_run": True,
            }
        if name == "hh_research_and_apply":
            return self.service.run_hh_research_operation(
                text=str(args.get("text") or ""),
                limit=int(args.get("limit") or 20),
                resume_id=str(args.get("resume_id") or ""),
                run_id=run_id,
                plan_apply=True,
                confirm_apply=is_literal_confirmation(args.get("confirm_apply")),
            )
        raise KeyError(name)


def _search_params(args: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "text": str(args.get("text") or ""),
        "per_page": int(args.get("per_page") or 20),
        "page": int(args.get("page") or 0),
    }
    if args.get("area"):
        params["area"] = str(args["area"])
    return params
