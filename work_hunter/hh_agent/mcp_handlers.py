from __future__ import annotations

from typing import Any

from mcp.types import Tool

from ..llm.structured import send_structured_chat
from .policy import VacancyPolicy
from .research import HHVacancyResearchService


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
                description="Analyze one HH vacancy and persist an audited HH vacancy analysis.",
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
            result = self._handle(name, args)
            self.service.storage.finish_hh_agent_mcp_run(run_id, status="ok", output=result)
            result.setdefault("mcp_run_id", run_id)
            return result
        except Exception as exc:
            self.service.storage.finish_hh_agent_mcp_run(run_id, status="error", error=str(exc))
            raise

    def _handle(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
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
            resume_id = str(args.get("resume_id") or "")
            research = self._research_service()
            vacancy = research.get_vacancy_details(vacancy_id)
            analysis = research.analyze_vacancy(vacancy, resume_id=resume_id)
            return {
                "status": "analyzed",
                "vacancy_id": vacancy_id,
                "resume_id": resume_id,
                "analysis": analysis.to_dict(),
            }
        if name == "hh_research_vacancies":
            research = self._research_service()
            limit = int(args.get("limit") or 20)
            params = {"text": str(args.get("text") or ""), "per_page": limit, "page": 0}
            results = research.search_vacancies(params)[:limit]
            return {
                "status": "researched",
                "text": str(args.get("text") or ""),
                "limit": limit,
                "resume_id": str(args.get("resume_id") or ""),
                "items": [item.to_dict() for item in results],
                "count": len(results),
            }
        if name == "hh_apply_vacancy":
            if args.get("confirm_apply"):
                return {
                    "status": "blocked",
                    "vacancy_id": str(args["vacancy_id"]),
                    "message": "MCP real apply is blocked until approval/confirm flow is enabled.",
                }
            vacancy_id = str(args["vacancy_id"])
            resume_id = str(args.get("resume_id") or "")
            research = self._research_service()
            vacancy = _vacancy_or_minimal(research.get_vacancy_details(vacancy_id), vacancy_id)
            attempt = research.plan_apply_vacancy(
                vacancy,
                resume_id=resume_id,
                letter=str(args.get("letter") or ""),
            )
            return {
                "status": attempt.status,
                "vacancy_id": vacancy_id,
                "resume_id": resume_id,
                "dry_run": True,
                "attempt": attempt.to_dict(),
            }
        if name == "hh_research_and_apply":
            if args.get("confirm_apply"):
                return {
                    "status": "blocked",
                    "message": "MCP real apply is blocked until approval/confirm flow is enabled.",
                }
            research = self._research_service()
            limit = int(args.get("limit") or 20)
            resume_id = str(args.get("resume_id") or "")
            params = {"text": str(args.get("text") or ""), "per_page": limit, "page": 0}
            items = []
            counts = {"planned": 0, "blocked": 0, "applied": 0}
            for result in research.search_vacancies(params)[:limit]:
                vacancy = _vacancy_or_minimal(research.get_vacancy_details(result.vacancy_id), result.vacancy_id)
                attempt = research.plan_apply_vacancy(vacancy, resume_id=resume_id)
                attempt_payload = attempt.to_dict()
                if attempt.status in counts:
                    counts[attempt.status] += 1
                elif attempt.status == "blocked":
                    counts["blocked"] += 1
                items.append(
                    {
                        "vacancy_id": result.vacancy_id,
                        "vacancy": result.to_dict(),
                        "attempt": attempt_payload,
                    }
                )
            return {
                "status": "planned",
                "text": str(args.get("text") or ""),
                "limit": limit,
                "resume_id": resume_id,
                "counts": counts,
                "items": items,
            }
        raise KeyError(name)

    def _research_service(self) -> HHVacancyResearchService:
        config = getattr(self.service, "config", {}) or {}
        hh_agent = config.get("hh_agent") or {}
        policy = VacancyPolicy.from_mapping(dict(hh_agent.get("policy") or {}))
        try:
            persona = self.service.candidate_map()
        except Exception:
            persona = {}
        return HHVacancyResearchService(
            client=_HHMCPClient(self.service),
            storage=self.service.storage,
            ai_config=dict(config.get("ai") or {}),
            policy=policy,
            persona=persona,
            structured_chat=send_structured_chat,
        )


class _HHMCPClient:
    def __init__(self, service: Any):
        self.service = service

    def search_vacancies(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        payload = self.service.hh_call_api("GET", "/vacancies", data=params)
        items = payload.get("items") if isinstance(payload, dict) else None
        return list(items or [])

    def get_vacancy(self, vacancy_id: str) -> dict[str, Any]:
        return dict(self.service.hh_call_api("GET", f"/vacancies/{vacancy_id}"))

    def get_similar_vacancies(self, vacancy_id: str) -> list[dict[str, Any]]:
        payload = self.service.hh_call_api("GET", f"/vacancies/{vacancy_id}/similar_vacancies")
        items = payload.get("items") if isinstance(payload, dict) else None
        return list(items or [])


def _search_params(args: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "text": str(args.get("text") or ""),
        "per_page": int(args.get("per_page") or 20),
        "page": int(args.get("page") or 0),
    }
    if args.get("area"):
        params["area"] = str(args["area"])
    return params


def _vacancy_or_minimal(value: dict[str, Any], vacancy_id: str) -> dict[str, Any]:
    if value.get("id") or value.get("vacancy_id"):
        return value
    return {**value, "id": vacancy_id}
