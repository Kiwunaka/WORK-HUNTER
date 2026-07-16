from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from .config import AutopilotSettings
from .ranking import NoQualifyingCandidatesError, select_resume
from .repository import CooldownActive, LostLease, RepositoryAuthorizationDenied
from .types import (
    AutopilotState,
    ExecutionResult,
    LiteralConfirmation,
    LiveAuthorization,
    NormalizedVacancy,
    RankedCandidate,
    RetryStage,
    RunReport,
    RunRequest,
    SearchRequest,
    canary_reference,
)


@dataclass(frozen=True)
class EngineSearchMapping:
    resume_id: str
    resume: Mapping[str, Any]
    query_key: str
    params: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.resume_id.strip() or not self.query_key.strip():
            raise ValueError("search mapping requires resume_id and query_key")
        if not isinstance(self.resume, Mapping) or not isinstance(self.params, Mapping):
            raise TypeError("search mapping resume and params must be mappings")


@dataclass(frozen=True)
class EngineRunContext:
    raw_config: dict[str, Any]
    settings: AutopilotSettings
    policy_hash: str
    candidate_profile: Mapping[str, Any]
    mappings: Sequence[EngineSearchMapping]
    filter_context: Mapping[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not isinstance(self.raw_config, dict):
            raise TypeError("raw_config must be a dictionary")
        if not isinstance(self.settings, AutopilotSettings):
            raise TypeError("settings must be AutopilotSettings")
        if not isinstance(self.policy_hash, str) or not self.policy_hash.strip():
            raise ValueError("policy_hash must be nonempty text")
        if not isinstance(self.candidate_profile, Mapping):
            raise TypeError("candidate_profile must be a mapping")
        mappings = tuple(self.mappings)
        if any(not isinstance(value, EngineSearchMapping) for value in mappings):
            raise TypeError("mappings must contain EngineSearchMapping values")
        object.__setattr__(self, "mappings", mappings)
        if self.filter_context is None:
            object.__setattr__(self, "filter_context", {})
        elif not isinstance(self.filter_context, Mapping):
            raise TypeError("filter_context must be a mapping")


@dataclass
class _Counters:
    applied: int = 0
    manual: int = 0
    retry_wait: int = 0
    skipped: int = 0
    internal_errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "applied": self.applied,
            "manual": self.manual,
            "retry_wait": self.retry_wait,
            "skipped": self.skipped,
            "internal_errors": self.internal_errors,
        }


class HHAutopilot:
    """One coordinator around the existing search, policy and dispatch stages."""

    def __init__(
        self,
        *,
        repository: Any,
        authorizer: Any,
        search_provider: Any,
        hard_filter: Any,
        deterministic_ranker: Any,
        ranking_policy: Any,
        executor: Any,
        reconciler: Any,
        context_provider: Callable[[str], EngineRunContext],
        vacancy_loader: Callable[[str], NormalizedVacancy],
        owner_token_factory: Callable[[], str],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleeper: Callable[[float], None] = lambda _seconds: None,
        delay_source: Callable[[float, float], float] = lambda low, _high: low,
    ) -> None:
        callables = {
            "context_provider": context_provider,
            "vacancy_loader": vacancy_loader,
            "owner_token_factory": owner_token_factory,
            "clock": clock,
            "sleeper": sleeper,
            "delay_source": delay_source,
        }
        for name, value in callables.items():
            if not callable(value):
                raise TypeError(f"{name} must be callable")
        self.repository = repository
        self.authorizer = authorizer
        self.search_provider = search_provider
        self.hard_filter = hard_filter
        self.deterministic_ranker = deterministic_ranker
        self.ranking_policy = ranking_policy
        self.executor = executor
        self.reconciler = reconciler
        self.context_provider = context_provider
        self.vacancy_loader = vacancy_loader
        self.owner_token_factory = owner_token_factory
        self.clock = clock
        self.sleeper = sleeper
        self.delay_source = delay_source

    def run(self, request: RunRequest) -> RunReport:
        if not isinstance(request, RunRequest):
            raise TypeError("request must be a RunRequest")
        context = self.context_provider(request.account_id)
        if not isinstance(context, EngineRunContext):
            raise TypeError("context_provider returned an invalid context")
        now = self._now()
        lease = self.repository.acquire_lease(
            request.account_id,
            self.owner_token_factory(),
            ttl_seconds=context.settings.lease.ttl_seconds,
            now=now,
        )
        if lease is None:
            return RunReport.busy(request.account_id, request.trigger)

        run = None
        counters = _Counters()
        status = "completed"
        error = ""
        try:
            grant_id = self._run_grant_id(request)
            run = self.repository.create_run(
                request.account_id,
                trigger=request.trigger,
                policy_hash=context.policy_hash,
                grant_id=grant_id,
                fencing_token=lease.fencing_token,
            )
            authorization = self._authorization(request, context, run, lease)
            if request.trigger == "shadow":
                self._discover(request, context, run, lease, counters, shadow=True)
            else:
                if isinstance(authorization, LiveAuthorization):
                    self._recover_and_retry(context, run, lease, counters)
                if request.trigger == "schedule":
                    self._discover(request, context, run, lease, counters, shadow=False)
                elif request.trigger in {"manual", "canary"}:
                    self._prepare_exact(request, context, run, lease, counters)
                assert authorization is not None
                status = self._dispatch(
                    request,
                    context,
                    run,
                    lease,
                    authorization,
                    counters,
                )
        except LostLease:
            status = "interrupted"
            error = "lease_lost"
        except Exception as exc:
            status = "failed"
            error = type(exc).__name__
        finally:
            if run is not None:
                self.repository.finish_run(
                    run.id,
                    status=status,
                    counters=counters.as_dict(),
                    error=error,
                    fencing_token=(lease.fencing_token if status != "interrupted" or error != "lease_lost" else None),
                )
            self.repository.release_lease(lease)

        shadow_results = (
            self.repository.count_shadow_results(run.id)
            if run is not None and request.trigger == "shadow"
            else 0
        )
        return RunReport(
            account_id=request.account_id,
            trigger=request.trigger,
            status=status,
            run_id=None if run is None else run.id,
            shadow_results=shadow_results,
            **counters.as_dict(),
        )

    def _run_grant_id(self, request: RunRequest) -> int | None:
        if request.trigger in {"shadow", "manual", "canary"}:
            return None
        grant = self.repository.active_grant(request.account_id)
        if grant is None:
            raise RepositoryAuthorizationDenied("authorization_state_mismatch")
        return int(grant.id)

    def _authorization(self, request, context, run, lease):
        if request.trigger == "shadow":
            if request.authorization is not None:
                raise ValueError("shadow run cannot carry live authorization")
            return None
        if request.trigger in {"manual", "canary"}:
            authorization = request.authorization
            if type(authorization) is not LiteralConfirmation:
                raise ValueError("manual and canary runs require literal confirmation")
            if authorization.account_id.strip().casefold() != request.account_id:
                raise ValueError("literal confirmation account does not match request")
            if request.trigger == "canary":
                if request.resume_id is None or request.vacancy_id is None:
                    raise ValueError("canary requires one exact resume and vacancy")
                if authorization.reference_id != canary_reference(
                    request.account_id,
                    request.resume_id,
                    request.vacancy_id,
                ):
                    raise ValueError("canary confirmation is not bound to the exact target")
            return authorization
        return self.authorizer.issue_live_authorization(
            request.account_id,
            context.raw_config,
            run_id=run.id,
            fencing_token=lease.fencing_token,
        )

    def _recover_and_retry(self, context, run, lease, counters: _Counters) -> None:
        now = self._now()
        self.repository.recover_stale_applying(
            run.account_id,
            lease.fencing_token,
            run_id=run.id,
            now=now,
        )
        for item in self.repository.due_reconciliation_items(
            run.account_id,
            now=now,
        ):
            if item.active_attempt_id is None:
                counters.internal_errors += 1
                continue
            provenance = self.repository.recovery_provenance(item.active_attempt_id)
            try:
                self.reconciler.reconcile(item.id, provenance, lease, now=now)
            except Exception:
                counters.internal_errors += 1
        due = self.repository.list_due_items(
            run.account_id,
            states=(AutopilotState.RETRY_WAIT,),
            now=now,
            limit=context.settings.limits.per_run_success,
        )
        for item in due:
            target = {
                RetryStage.ELIGIBILITY: AutopilotState.ELIGIBLE,
                RetryStage.APPLICATION: AutopilotState.READY,
                RetryStage.RECONCILIATION: AutopilotState.RECONCILING,
            }[item.retry_stage]
            if hasattr(self.repository, "activate_due_retry"):
                activated = self.repository.activate_due_retry(
                    item.id,
                    item.version,
                    run_id=run.id,
                    fencing_token=lease.fencing_token,
                    now=now,
                )
            else:
                activated = self.repository.transition_item(
                    item.id,
                    item.version,
                    target,
                    "retry_due",
                    run_id=run.id,
                    fencing_token=lease.fencing_token,
                )
            if activated.state is AutopilotState.RECONCILING and activated.active_attempt_id:
                provenance = self.repository.recovery_provenance(
                    activated.active_attempt_id
                )
                self.reconciler.reconcile(
                    activated.id,
                    provenance,
                    lease,
                    now=now,
                )

    def _discover(self, request, context, run, lease, counters, *, shadow: bool) -> None:
        remaining = context.settings.search.max_results_per_run
        ranked: dict[str, list[RankedCandidate]] = {}
        blocked: set[str] = set()
        seen: set[tuple[str, str]] = set()
        for mapping in context.mappings:
            if remaining <= 0:
                break
            result = self.search_provider.collect(
                SearchRequest(
                    account_id=run.account_id,
                    run_id=run.id,
                    resume_id=mapping.resume_id,
                    query_key=mapping.query_key,
                    params=dict(mapping.params),
                    per_page=context.settings.search.per_page,
                    max_pages=context.settings.search.max_pages,
                    remaining_budget=remaining,
                    policy_hash=context.policy_hash,
                    fencing_token=lease.fencing_token,
                    mode="shadow" if shadow else "live",
                )
            )
            remaining = max(0, remaining - result.new_distinct_count)
            for vacancy in result.vacancies:
                identity = (mapping.resume_id, vacancy.id)
                if identity in seen:
                    continue
                seen.add(identity)
                candidate, retry = self._evaluate(
                    vacancy,
                    mapping,
                    context,
                    run,
                    lease,
                    counters,
                    shadow=shadow,
                )
                if retry:
                    blocked.add(vacancy.id)
                if candidate is not None:
                    ranked.setdefault(vacancy.id, []).append(candidate)
        if not shadow:
            self._finalize_ranked(ranked, blocked, context, run, lease)

    def _prepare_exact(self, request, context, run, lease, counters) -> None:
        if request.resume_id is None or request.vacancy_id is None:
            raise ValueError("literal run requires one exact resume and vacancy")
        mapping = next(
            (
                value
                for value in context.mappings
                if value.resume_id.strip().casefold() == request.resume_id
            ),
            None,
        )
        if mapping is None:
            raise ValueError("literal resume is not published for this account")
        vacancy = self.vacancy_loader(request.vacancy_id)
        if not isinstance(vacancy, NormalizedVacancy) or vacancy.id != request.vacancy_id:
            raise ValueError("vacancy loader returned a different vacancy")
        candidate, retry = self._evaluate(
            vacancy,
            mapping,
            context,
            run,
            lease,
            counters,
            shadow=False,
        )
        ranked = {} if candidate is None else {vacancy.id: [candidate]}
        self._finalize_ranked(
            ranked,
            {vacancy.id} if retry else set(),
            context,
            run,
            lease,
        )

    def _evaluate(
        self,
        vacancy,
        mapping,
        context,
        run,
        lease,
        counters,
        *,
        shadow,
    ) -> tuple[RankedCandidate | None, bool]:
        filter_decision = self.hard_filter.evaluate(
            vacancy,
            mapping.resume,
            context.candidate_profile,
            context.filter_context,
        )
        score = None
        ranking_decision = None
        if filter_decision.passed:
            score = self.deterministic_ranker.score(
                vacancy,
                mapping.resume,
                context.candidate_profile,
                context.settings.ranking["weights"],
            )
            ranking_decision = self.ranking_policy.decide(
                filter_decision,
                score,
                vacancy,
                mapping.resume,
                context.candidate_profile,
            )
        if shadow:
            self.repository.save_shadow_result(
                run.id,
                run.account_id,
                vacancy.id,
                mapping.resume_id,
                filter_data=filter_decision.to_dict(),
                deterministic_score=None if score is None else score.score,
                ai_data=None if ranking_decision is None else ranking_decision.to_dict(),
                would_apply=bool(ranking_decision and ranking_decision.ready),
                fencing_token=lease.fencing_token,
            )
            return None, False

        item = self.repository.create_item(
            run.id,
            run.account_id,
            vacancy.id,
            mapping.resume_id,
            mapping.query_key,
        )
        if item.state is not AutopilotState.DISCOVERED:
            return None, False
        item = self.repository.record_filter_decision(
            item.id,
            expected_version=item.version,
            decision=filter_decision,
            run_id=run.id,
            published_at=vacancy.published_at,
            fencing_token=lease.fencing_token,
        )
        if not filter_decision.passed:
            counters.skipped += 1
            return None, False
        assert ranking_decision is not None
        item = self.repository.record_ranking_decision(
            item.id,
            expected_version=item.version,
            decision=ranking_decision,
            run_id=run.id,
            fencing_token=lease.fencing_token,
        )
        if ranking_decision.retry:
            self.repository.transition_item(
                item.id,
                item.version,
                AutopilotState.RETRY_WAIT,
                ranking_decision.reason,
                run_id=run.id,
                fencing_token=lease.fencing_token,
            )
            counters.retry_wait += 1
            return None, True
        if not ranking_decision.ready:
            self.repository.transition_item(
                item.id,
                item.version,
                AutopilotState.SKIPPED,
                ranking_decision.reason,
                run_id=run.id,
                fencing_token=lease.fencing_token,
            )
            counters.skipped += 1
            return None, False
        return (
            RankedCandidate(
                account_id=run.account_id,
                vacancy_id=vacancy.id,
                resume_id=mapping.resume_id,
                published_at=vacancy.published_at,
                decision=ranking_decision,
                item_id=item.id,
                expected_version=item.version,
            ),
            False,
        )

    def _finalize_ranked(self, ranked, blocked, context, run, lease) -> None:
        resume_policy = str(context.settings.application["resume_policy"])
        for vacancy_id in sorted(ranked):
            candidates = ranked[vacancy_id]
            if vacancy_id in blocked or not candidates:
                continue
            try:
                selected = select_resume(candidates, resume_policy)
            except NoQualifyingCandidatesError:
                continue
            selected_values = (selected,) if isinstance(selected, RankedCandidate) else selected
            expected = {
                candidate.item_id: candidate.expected_version
                for candidate in candidates
                if candidate.item_id is not None and candidate.expected_version is not None
            }
            selected_ids = [
                candidate.item_id
                for candidate in selected_values
                if candidate.item_id is not None
            ]
            self.repository.finalize_ranked_candidates(
                expected,
                selected_item_ids=selected_ids,
                resume_policy=resume_policy,
                run_id=run.id,
                fencing_token=lease.fencing_token,
            )

    def _dispatch(self, request, context, run, lease, authorization, counters) -> str:
        ready = self.repository.ready_items(
            run.account_id,
            limit=context.settings.limits.per_run_success,
        )
        if isinstance(authorization, LiteralConfirmation):
            ready = [
                item
                for item in ready
                if item.resume_id == request.resume_id
                and item.vacancy_id == request.vacancy_id
            ][:1 if request.trigger == "canary" else len(ready)]
        delay_due = False
        for item in ready:
            if self._stop_requested(run, authorization):
                return "interrupted"
            if delay_due:
                self.sleeper(
                    self.delay_source(
                        context.settings.limits.send_delay_min_seconds,
                        context.settings.limits.send_delay_max_seconds,
                    )
                )
                delay_due = False
                if self._stop_requested(run, authorization):
                    return "interrupted"
            try:
                result = self.executor.execute(
                    item.id,
                    authorization,
                    lease,
                    now=self._now(),
                )
            except (CooldownActive, RepositoryAuthorizationDenied):
                return "interrupted"
            except LostLease:
                raise
            except Exception:
                counters.internal_errors += 1
                continue
            if not isinstance(result, ExecutionResult):
                counters.internal_errors += 1
                continue
            delay_due = result.remote_post_dispatched
            if result.state is AutopilotState.APPLIED:
                counters.applied += 1
            elif result.state is AutopilotState.MANUAL_CHALLENGE:
                counters.manual += 1
            elif result.state in {AutopilotState.RETRY_WAIT, AutopilotState.RECONCILING}:
                counters.retry_wait += 1
            elif result.state in {AutopilotState.SKIPPED, AutopilotState.DEAD}:
                counters.skipped += 1
            if result.outcome_code == "internal_error":
                counters.internal_errors += 1
        return "completed"

    def _stop_requested(self, run, authorization) -> bool:
        if self.repository.run_stop_requested(run.id):
            return True
        if isinstance(authorization, LiveAuthorization):
            return self.repository.pause_active(run.account_id) or self.repository.kill_switch_active(
                run.account_id
            )
        return False

    def _now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise TypeError("clock must return an aware datetime")
        return value


__all__ = [
    "EngineRunContext",
    "EngineSearchMapping",
    "HHAutopilot",
]
