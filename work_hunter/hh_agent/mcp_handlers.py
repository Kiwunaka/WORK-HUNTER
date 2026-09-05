from __future__ import annotations

from typing import Any

from mcp.types import Tool

from ..models import Job
from ..safety import is_literal_confirmation
from ..sources.common import clean_text


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
        "hh_list_chats",
        "hh_reply_chats",
        "hh_web_profile",
        "hh_touch_resumes",
        "hh_set_job_search_active",
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
                description="Load and analyze one HH vacancy with the configured AI backend.",
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
                description="Plan or send one HH vacancy application with literal confirm_apply=true.",
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
            Tool(
                name="hh_list_chats",
                description="List HH applicant Chatik conversations awaiting a reply.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "max_pages": {"type": "integer", "minimum": 1, "maximum": 100},
                        "max_age_hours": {"type": "number", "minimum": 1},
                        "awaiting_only": {"type": "boolean", "default": True},
                        "limit": {"type": "integer", "minimum": 1},
                    },
                },
            ),
            Tool(
                name="hh_reply_chats",
                description=(
                    "Plan or send grounded AI/template replies through HH Chatik. "
                    "Sending and leaving discarded chats require literal confirm=true."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "template": {"type": "string"},
                        "use_ai": {"type": "boolean", "default": True},
                        "max_pages": {"type": "integer", "minimum": 1, "maximum": 100},
                        "max_age_hours": {"type": "number", "minimum": 1},
                        "history_limit": {"type": "integer", "minimum": 1, "maximum": 100},
                        "message_limit": {"type": "integer", "minimum": 1},
                        "limit": {"type": "integer", "minimum": 1},
                        "leave_discarded": {"type": "boolean", "default": True},
                        "confirm": {"type": "boolean", "default": False},
                    },
                },
            ),
            Tool(
                name="hh_web_profile",
                description="Read the HH applicant profile and resume hashes through browser cookies.",
                inputSchema={
                    "type": "object",
                    "properties": {"account": {"type": "string"}},
                },
            ),
            Tool(
                name="hh_touch_resumes",
                description="Plan or raise HH resumes through the applicant web transport.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "resume_hashes": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "confirm": {"type": "boolean", "default": False},
                    },
                },
            ),
            Tool(
                name="hh_set_job_search_active",
                description="Plan or set the HH profile status to looking_for_offers.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "confirm": {"type": "boolean", "default": False},
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
            vacancy_id = str(args["vacancy_id"])
            row = self.service.storage.conn.execute(
                "SELECT id FROM jobs WHERE source = 'hh' AND source_id = ?",
                (vacancy_id,),
            ).fetchone()
            if row is None:
                fetched = self.service.hh_call_api("GET", f"/vacancies/{vacancy_id}")
                payload = fetched.get("result") if fetched.get("status") == "ok" else None
                if not isinstance(payload, dict):
                    return fetched
                employer = payload.get("employer") or {}
                job_id = self.service.storage.upsert_job(
                    Job(
                        source="hh",
                        source_id=vacancy_id,
                        url=str(payload.get("alternate_url") or ""),
                        title=str(payload.get("name") or vacancy_id),
                        company=str(employer.get("name") or ""),
                        description=clean_text(str(payload.get("description") or "")),
                    )
                )
            else:
                job_id = int(row["id"])
            result = self.service.ai_fit(job_id)
            result["vacancy_id"] = vacancy_id
            result["resume_id"] = str(args.get("resume_id") or "")
            return result
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
            vacancy_id = str(args["vacancy_id"])
            row = self.service.storage.conn.execute(
                "SELECT id FROM jobs WHERE source = 'hh' AND source_id = ?",
                (vacancy_id,),
            ).fetchone()
            if row is not None:
                job_id = int(row["id"])
                if is_literal_confirmation(args.get("confirm_apply")):
                    return self.service.confirm_apply(
                        job_id,
                        resume_id=str(args.get("resume_id") or "") or None,
                        confirm=True,
                    )
                return self.service.prepare_apply_plan(
                    job_id,
                    resume_id=str(args.get("resume_id") or "") or None,
                )
            return {
                "status": "blocked",
                "vacancy_id": vacancy_id,
                "resume_id": str(args.get("resume_id") or ""),
                "message": "Vacancy is not in local storage. Sync or import it first.",
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
        if name == "hh_list_chats":
            return self.service.list_hh_chatik(
                account=str(args.get("account") or "") or None,
                max_pages=_optional_int(args.get("max_pages")),
                max_age_hours=_optional_float(args.get("max_age_hours")),
                awaiting_only=bool(args.get("awaiting_only", True)),
                limit=_optional_int(args.get("limit")),
            )
        if name == "hh_reply_chats":
            confirm = is_literal_confirmation(args.get("confirm"))
            return self.service.reply_hh_chatik(
                account=str(args.get("account") or "") or None,
                template=str(args.get("template") or ""),
                use_ai=bool(args.get("use_ai", True)),
                max_pages=_optional_int(args.get("max_pages")),
                max_age_hours=_optional_float(args.get("max_age_hours")),
                history_limit=_optional_int(args.get("history_limit")),
                message_limit=_optional_int(args.get("message_limit")),
                limit=_optional_int(args.get("limit")),
                leave_discarded=bool(args.get("leave_discarded", True)),
                dry_run=not confirm,
                confirm=confirm,
            )
        if name == "hh_web_profile":
            return self.service.hh_applicant_web_profile(
                account=str(args.get("account") or "") or None,
            )
        if name == "hh_touch_resumes":
            confirm = is_literal_confirmation(args.get("confirm"))
            hashes = args.get("resume_hashes")
            return self.service.touch_hh_resumes_web(
                account=str(args.get("account") or "") or None,
                resume_hashes=(
                    [str(value) for value in hashes]
                    if isinstance(hashes, list)
                    else None
                ),
                dry_run=not confirm,
                confirm=confirm,
            )
        if name == "hh_set_job_search_active":
            confirm = is_literal_confirmation(args.get("confirm"))
            return self.service.set_hh_job_search_active(
                account=str(args.get("account") or "") or None,
                dry_run=not confirm,
                confirm=confirm,
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


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
