from __future__ import annotations

from typing import Any, Callable

from ..llm.structured import StructuredOutputSchema, send_structured_chat
from ..models import HHApplicationAttempt, HHVacancyAnalysis
from .dedupe import build_vacancy_dedupe_key, normalize_text
from .policy import VacancyPolicy
from .types import ApplicationAttemptResult, PrecheckResult, VacancyAnalysisResult, VacancySearchResult


APPLIED_RELATIONS = {"got_response", "response", "already_applied", "negotiations", "invitation"}

VACANCY_ANALYSIS_SCHEMA = StructuredOutputSchema(
    name="hh_vacancy_analysis",
    schema={
        "type": "object",
        "properties": {
            "score": {"type": "integer"},
            "recommended_action": {"type": "string"},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "risk_flags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["score", "recommended_action", "reasons", "risk_flags"],
    },
)


class HHVacancyResearchService:
    def __init__(
        self,
        *,
        client: Any,
        storage: Any,
        ai_config: dict[str, Any] | None = None,
        policy: VacancyPolicy | None = None,
        persona: dict[str, Any] | None = None,
        structured_chat: Callable[..., Any] = send_structured_chat,
        apply_callback: Callable[..., dict[str, Any]] | None = None,
    ):
        self.client = client
        self.storage = storage
        self.ai_config = ai_config or {}
        self.policy = policy or VacancyPolicy.from_mapping({})
        self.persona = persona or {}
        self.structured_chat = structured_chat
        self.apply_callback = apply_callback

    def search_vacancies(self, params: dict[str, Any]) -> list[VacancySearchResult]:
        return [_search_result_from_payload(item) for item in self.client.search_vacancies(params)]

    def get_similar_vacancies(self, vacancy_id: str) -> list[VacancySearchResult]:
        return [_search_result_from_payload(item) for item in self.client.get_similar_vacancies(vacancy_id)]

    def get_vacancy_details(self, vacancy_id: str) -> dict[str, Any]:
        return dict(self.client.get_vacancy(vacancy_id))

    def analyze_vacancy(
        self,
        vacancy: dict[str, Any],
        *,
        resume_id: str = "",
        run_id: int | None = None,
    ) -> VacancyAnalysisResult:
        vacancy_id = str(vacancy.get("id") or vacancy.get("vacancy_id") or "")
        reply = self.structured_chat(
            [{"role": "user", "content": _analysis_prompt(vacancy, self.policy, self.persona)}],
            self.ai_config,
            VACANCY_ANALYSIS_SCHEMA,
        )
        parsed = reply.parsed
        result = VacancyAnalysisResult(
            vacancy_id=vacancy_id,
            score=int(parsed.get("score") or 0),
            recommended_action=str(parsed.get("recommended_action") or "skip"),
            reasons=[str(item) for item in parsed.get("reasons") or []],
            risk_flags=[str(item) for item in parsed.get("risk_flags") or []],
            raw_result=parsed,
        )
        self.storage.save_hh_vacancy_analysis(
            HHVacancyAnalysis(
                run_id=run_id,
                vacancy_id=vacancy_id,
                resume_id=resume_id,
                policy_hash=self.policy.hash(),
                score=result.score,
                recommended_action=result.recommended_action,
                reasons=result.reasons,
                risk_flags=result.risk_flags,
                model=reply.model,
                raw_result=reply.to_dict(),
            )
        )
        return result

    def plan_apply_vacancy(
        self,
        vacancy: dict[str, Any],
        *,
        resume_id: str,
        run_id: int | None = None,
        analysis: VacancyAnalysisResult | None = None,
        letter: str = "",
    ) -> ApplicationAttemptResult:
        vacancy_id = str(vacancy.get("id") or vacancy.get("vacancy_id") or "")
        precheck = run_hard_prechecks(
            vacancy,
            resume_id=resume_id,
            policy=self.policy,
            storage=self.storage,
        )
        if not precheck.passed:
            return self._save_attempt(
                run_id=run_id,
                vacancy_id=vacancy_id,
                resume_id=resume_id,
                status="blocked",
                reason=precheck.reason,
                letter=letter,
                raw_result=precheck.to_dict(),
            )
        if analysis is not None and analysis.score < self.policy.min_score:
            return self._save_attempt(
                run_id=run_id,
                vacancy_id=vacancy_id,
                resume_id=resume_id,
                status="blocked",
                reason="below_min_score",
                letter=letter,
                raw_result=analysis.to_dict(),
            )
        return self._save_attempt(
            run_id=run_id,
            vacancy_id=vacancy_id,
            resume_id=resume_id,
            status="planned",
            reason="dry_run",
            letter=letter or self.policy.force_message,
            raw_result={
                "precheck": precheck.to_dict(),
                "analysis": analysis.to_dict() if analysis else {},
            },
        )

    def apply_vacancy(
        self,
        vacancy: dict[str, Any],
        *,
        resume_id: str,
        job_id: int | None = None,
        run_id: int | None = None,
        letter: str = "",
        confirm: bool = False,
    ) -> ApplicationAttemptResult:
        vacancy_id = str(vacancy.get("id") or vacancy.get("vacancy_id") or "")
        if not confirm:
            return self._save_attempt(
                run_id=run_id,
                vacancy_id=vacancy_id,
                resume_id=resume_id,
                status="blocked",
                reason="explicit_confirmation_required",
                letter=letter,
                raw_result={"requires_confirmation": True},
            )
        if self.apply_callback is None or job_id is None:
            return self._save_attempt(
                run_id=run_id,
                vacancy_id=vacancy_id,
                resume_id=resume_id,
                status="blocked",
                reason="confirm_apply_callback_required",
                letter=letter,
                raw_result={"job_id": job_id},
            )
        result = self.apply_callback(job_id, resume_id=resume_id, letter=letter, confirm=True)
        result_status = str(result.get("status") or "")
        status = "applied" if result_status in {"applied", "created", "redirect"} else result_status or "completed"
        return self._save_attempt(
            run_id=run_id,
            vacancy_id=vacancy_id,
            resume_id=resume_id,
            status=status,
            reason="confirmed",
            letter=letter,
            raw_result=result,
        )

    def _save_attempt(
        self,
        *,
        run_id: int | None,
        vacancy_id: str,
        resume_id: str,
        status: str,
        reason: str,
        letter: str,
        raw_result: dict[str, Any],
    ) -> ApplicationAttemptResult:
        self.storage.save_hh_application_attempt(
            HHApplicationAttempt(
                run_id=run_id,
                vacancy_id=vacancy_id,
                resume_id=resume_id,
                status=status,
                reason=reason,
                letter=letter,
                raw_result=raw_result,
            )
        )
        return ApplicationAttemptResult(
            vacancy_id=vacancy_id,
            resume_id=resume_id,
            status=status,
            reason=reason,
            raw_result=raw_result,
        )


def run_hard_prechecks(
    vacancy: dict[str, Any],
    *,
    resume_id: str,
    policy: VacancyPolicy,
    storage: Any | None = None,
    local_application_exists: bool = False,
) -> PrecheckResult:
    vacancy_id = str(vacancy.get("id") or vacancy.get("vacancy_id") or "")
    dedupe_key = build_vacancy_dedupe_key(vacancy)
    text = _vacancy_text(vacancy)
    employer = vacancy.get("employer") or {}
    employer_id = normalize_text(str(employer.get("id") or vacancy.get("employer_id") or ""))
    employer_name = normalize_text(str(employer.get("name") or vacancy.get("employer_name") or ""))

    if vacancy.get("archived"):
        return _blocked("archived", dedupe_key)
    if _has_test(vacancy):
        return _blocked("test_required", dedupe_key, ["test_required"])
    if _has_manual_form(vacancy):
        return _blocked("manual_form_required", dedupe_key, ["manual_form_required"])
    if _already_applied(vacancy):
        return _blocked("already_applied", dedupe_key)
    if local_application_exists:
        return _blocked("existing_local_application", dedupe_key)
    if _matches_any(policy.excluded_employers, employer_id, employer_name):
        return _blocked("excluded_employer", dedupe_key)
    if _matches_text(policy.excluded_keywords, text):
        return _blocked("excluded_keyword", dedupe_key)
    if _matches_text(policy.excluded_texts, text):
        return _blocked("excluded_text", dedupe_key)
    if storage is not None:
        skipped_reason = _previous_skip_reason(storage, resume_id=resume_id, vacancy_id=vacancy_id)
        if skipped_reason:
            return _blocked(skipped_reason, dedupe_key)
        if storage.get_hh_response_dedupe(resume_id=resume_id, dedupe_key=dedupe_key):
            return _blocked("dedupe_hit", dedupe_key)
    return PrecheckResult(status="passed", dedupe_key=dedupe_key)


def _search_result_from_payload(item: dict[str, Any]) -> VacancySearchResult:
    employer = item.get("employer") or {}
    return VacancySearchResult(
        vacancy_id=str(item.get("id") or ""),
        name=str(item.get("name") or ""),
        employer_name=str(employer.get("name") or item.get("employer_name") or ""),
        alternate_url=str(item.get("alternate_url") or ""),
        raw=item,
    )


def _analysis_prompt(vacancy: dict[str, Any], policy: VacancyPolicy, persona: dict[str, Any] | None = None) -> str:
    persona_text = ""
    if persona:
        persona_text = f"Candidate persona:\n{persona}\n"
    return (
        "Analyze this HH vacancy for the candidate policy.\n"
        f"{persona_text}"
        f"Policy:\n{policy.to_canonical_dict()}\n"
        f"Vacancy:\n{vacancy}\n"
        "Return score 0-100, recommended_action apply/skip/ask, reasons, risk_flags."
    )


def _blocked(reason: str, dedupe_key: str, risk_flags: list[str] | None = None) -> PrecheckResult:
    return PrecheckResult(status="blocked", reason=reason, dedupe_key=dedupe_key, risk_flags=risk_flags or [])


def _vacancy_text(vacancy: dict[str, Any]) -> str:
    snippet = vacancy.get("snippet") or {}
    parts = [
        vacancy.get("name"),
        vacancy.get("title"),
        vacancy.get("description"),
        snippet.get("requirement"),
        snippet.get("responsibility"),
        vacancy.get("snippet"),
    ]
    return normalize_text(" ".join(str(part or "") for part in parts if part))


def _has_test(vacancy: dict[str, Any]) -> bool:
    test = vacancy.get("test") or {}
    return bool(vacancy.get("has_test") or test.get("required") or test.get("id"))


def _has_manual_form(vacancy: dict[str, Any]) -> bool:
    return bool(
        vacancy.get("response_url")
        or vacancy.get("apply_alternate_url")
        or vacancy.get("manual_form_required")
    )


def _already_applied(vacancy: dict[str, Any]) -> bool:
    relations = vacancy.get("relations") or []
    for relation in relations:
        if isinstance(relation, dict):
            relation_id = str(relation.get("id") or relation.get("type") or "")
        else:
            relation_id = str(relation)
        if relation_id in APPLIED_RELATIONS:
            return True
    return False


def _matches_any(candidates: list[str], *values: str) -> bool:
    normalized_candidates = {normalize_text(candidate) for candidate in candidates}
    return any(value and value in normalized_candidates for value in values)


def _matches_text(needles: list[str], text: str) -> bool:
    return any(normalize_text(needle) in text for needle in needles if normalize_text(needle))


def _previous_skip_reason(storage: Any, *, resume_id: str, vacancy_id: str) -> str:
    for item in storage.list_hh_skipped_vacancies():
        if item.resume_id == resume_id and item.vacancy_id == vacancy_id:
            return item.reason
    return ""
