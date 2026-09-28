from __future__ import annotations

import random
import logging
import traceback
from collections import deque
from contextlib import nullcontext
from queue import Empty, Queue, SimpleQueue
from threading import Event, Lock, Thread
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from .config import AutopilotSettings
from .ranking import (
    NoQualifyingCandidatesError,
    basic_requirements_decision,
    select_resume,
)
from .repository import CooldownActive, LostLease, RepositoryAuthorizationDenied
from .types import (
    AutopilotState,
    ExecutionResult,
    FilterDecision,
    LiteralConfirmation,
    LiveAuthorization,
    NormalizedVacancy,
    RankedCandidate,
    RankingDecision,
    RetryStage,
    RunReport,
    RunRequest,
    SearchRequest,
    canary_reference,
)
from work_hunter.hh_transport.errors import HHTransportError
from work_hunter.hh_transport.challenge_urls import sanitize_hh_challenge_url


_CAPTCHA_READ_LOCK = Lock()


def read_with_captcha(operation, solver, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except HHTransportError as error:
        if _hh_access_reason(error) != "hh_captcha_required" or solver is None:
            raise
        with _CAPTCHA_READ_LOCK:
            try:
                return operation(*args, **kwargs)
            except HHTransportError as current:
                if _hh_access_reason(current) != "hh_captcha_required":
                    raise
                solver(current)
                # HH can unlock the API while its browser page still shows
                # a challenge. The repeated GET is authoritative.
                return operation(*args, **kwargs)


def _hh_access_reason(error: Exception) -> str | None:
    """Account/access failures must stop the run, not reject individual jobs."""
    if not isinstance(error, HHTransportError):
        return None
    if error.status_code == 403 and error.code == "captcha_required":
        return "hh_captcha_required"
    status_code = error.status_code
    return {401: "hh_auth_required", 403: "hh_access_forbidden", 429: "hh_rate_limited"}.get(status_code) if status_code is not None else None


def _hh_access_event_metadata(error: HHTransportError, reason: str) -> dict[str, Any]:
    """Return the status and allowlisted challenge pointers for an access event."""
    metadata: dict[str, Any] = {"status_code": error.status_code}
    if reason != "hh_captcha_required":
        return metadata
    payload = error.payload if isinstance(error.payload, Mapping) else {}
    challenge = payload.get("challenge_metadata")
    if not isinstance(challenge, Mapping):
        return metadata
    for key in ("captcha_url", "location"):
        value = _safe_hh_challenge_url(challenge.get(key))
        if value:
            metadata[key] = value
    request_id = _safe_hh_request_id(challenge.get("request_id"))
    if request_id:
        metadata["request_id"] = request_id
    return metadata


def _safe_hh_request_id(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > 200:
        return ""
    if any(ord(character) < 0x20 or ord(character) > 0x7E for character in value):
        return ""
    return value


def _safe_hh_challenge_url(value: Any) -> str:
    return sanitize_hh_challenge_url(value)


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
    dispatch_seen: set[int] = field(default_factory=set)
    fresh_ranked: set[int] = field(default_factory=set)
    delay_due: bool = False

    def as_dict(self) -> dict[str, int]:
        return {
            "applied": self.applied,
            "manual": self.manual,
            "retry_wait": self.retry_wait,
            "skipped": self.skipped,
            "internal_errors": self.internal_errors,
        }


def _active_vacancy_ids(context):
    ids = context.settings.filters.get("allowed_vacancy_ids") or ()
    return tuple(ids) if ids else None


class _DispatchPipeline:
    """One sender with its own thread-owned service/connection, never another run."""

    def __init__(self, factory, request, context, run, lease, authorization, *, producer, pool):
        self.factory = factory
        self.request, self.context, self.run = request, context, run
        self.lease, self.authorization = lease, authorization
        self.queue = Queue()
        self.queued: set[int] = set()
        self.stop = Event()
        self.counters = _Counters()
        self.status = "completed"
        self.error = ""
        self.producer, self.pool = producer, pool
        self.preparing = {}
        self.thread = Thread(target=self._consume, name="hh-single-sender")

    def start(self) -> None:
        self.thread.start()

    def enqueue(self, items, fresh_ids) -> None:
        self.collect_letters()
        for item in items:
            if item.id in fresh_ids and item.id not in self.queued and not self.stop.is_set():
                if len(self.preparing) + self.queue.unfinished_tasks >= 20:
                    break
                self.queued.add(item.id)
                if self.producer.letter_preparer is None:
                    self.queue.put(item.id)
                    continue
                mapping = next(value for value in self.context.mappings if value.resume_id == item.resume_id)
                self.producer.repository.save_dispatch_resume_snapshot(
                    item.id, resume={**mapping.resume, "id": mapping.resume_id},
                    candidate=self.context.candidate_profile, expected_version=item.version,
                    fencing_token=self.lease.fencing_token,
                )
                def prepare_and_enqueue(current=item):
                    self.producer.letter_preparer(current, self.context, self.run, self.lease)
                    if not self.stop.is_set():
                        self.queue.put(current.id)
                future = self.pool.submit(prepare_and_enqueue)
                self.preparing[future] = item.id

    def collect_letters(self) -> None:
        for future in list(self.preparing):
            if not future.done():
                continue
            item_id = self.preparing.pop(future)
            try:
                future.result()
            except Exception as exc:
                item = self.producer.repository.get_item(item_id)
                self.producer._stop_on_hh_access_error(exc, item, self.run, self.lease)
                self.producer.repository.append_event(
                    item_id, "cover_letter_retry", {"error_type": type(exc).__name__},
                    run_id=self.run.id, fencing_token=self.lease.fencing_token,
                )
                self.producer.flush_ranking_usage()

    def drain(self) -> None:
        while (self.preparing or self.queue.unfinished_tasks) and not self.stop.is_set():
            self.collect_letters()
            self.producer._touch_lease(self.lease)
            if self.producer._stop_requested(self.run, self.authorization):
                self.stop.set()
                break
            self.stop.wait(0.2)

    def finish(self, *, cancel: bool) -> None:
        if cancel:
            self.stop.set()
        if not cancel:
            self.drain()
        for future in self.preparing:
            future.cancel()
        self.queue.put(None)

    def _consume(self) -> None:
        try:
            with self.factory(self.context, self.lease) as engine:
                engine._pipeline_stop = self.stop
                while True:
                    if engine._stop_requested(self.run, self.authorization):
                        self.status = "interrupted"
                        self.stop.set()
                        break
                    try:
                        item_id = self.queue.get(timeout=0.2)
                    except Empty:
                        continue
                    if item_id is None:
                        break
                    self.counters.fresh_ranked.add(item_id)
                    try:
                        self.status = engine._dispatch(
                            self.request, self.context, self.run, self.lease,
                            self.authorization, self.counters,
                            only_fresh=True, max_items=1, item_ids={item_id},
                        )
                    finally:
                        self.queue.task_done()
                    if self.status != "completed" or self.counters.applied >= self.context.settings.limits.per_run_success:
                        self.stop.set()
                        break
        except Exception as exc:
            frames = [f"{frame.name}:{frame.lineno}" for frame in traceback.extract_tb(exc.__traceback__)]
            logging.getLogger(__name__).error("Sender failed error_type=%s frames=%s", type(exc).__name__, "/".join(frames))
            self.status = "failed"
            self.error = _hh_access_reason(exc) or f"dispatch_worker_failed:{type(exc).__name__}"
            self.stop.set()


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
        retry_random_source: Callable[[], float] = random.random,
        ranking_workers: int = 1,
        flush_ranking_usage: Callable[[], None] = lambda: None,
        sender_factory: Callable[..., Any] | None = None,
        letter_preparer: Callable[..., None] | None = None,
        captcha_solver: Callable[[HHTransportError], bool] | None = None,
    ) -> None:
        callables = {
            "context_provider": context_provider,
            "vacancy_loader": vacancy_loader,
            "owner_token_factory": owner_token_factory,
            "clock": clock,
            "sleeper": sleeper,
            "delay_source": delay_source,
            "retry_random_source": retry_random_source,
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
        self.retry_random_source = retry_random_source
        if type(ranking_workers) is not int or not 1 <= ranking_workers <= 10:
            raise ValueError("ranking_workers must be between 1 and 10")
        self.ranking_workers = ranking_workers
        self.flush_ranking_usage = flush_ranking_usage
        self._ranking_events: SimpleQueue[tuple[int, int, int, str]] = SimpleQueue()
        self.sender_factory = sender_factory
        self.letter_preparer = letter_preparer
        self.captcha_solver = captcha_solver
        self._ai_pool: ThreadPoolExecutor | None = None
        self._pipeline_stop: Event | None = None

    def run(self, request: RunRequest) -> RunReport:
        if not isinstance(request, RunRequest):
            raise TypeError("request must be a RunRequest")
        context = self.context_provider(request.account_id)
        if not isinstance(context, EngineRunContext):
            raise TypeError("context_provider returned an invalid context")
        if request.success_limit is not None:
            context = replace(context, settings=replace(context.settings, limits=replace(
                context.settings.limits,
                per_run_success=min(context.settings.limits.per_run_success, request.success_limit),
            )))
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
        lease_keeper = None
        provider = self.search_provider
        previous_keeper = getattr(provider, "lease_keeper", None)
        pipeline = None
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
            # Shadow-прогон по 20 страницам идёт дольше lease (120с).
            # Поднимаем fencing заранее через keeper: renew продлевает lease
            # до истечения, иначе renew_lease уронит прогон с LostLease.
            # Продление ДО создания run: run фиксирует уже свежий fencing.
            # TTL 600 = максимум конфига; lease в policy_hash не входит,
            # так что на авторизацию не влияет.
            from .repository import AutopilotRepository, LeaseKeeper

            lease_keeper = None
            provider = self.search_provider
            if isinstance(
                getattr(provider, "repository", None), AutopilotRepository
            ):
                lease_keeper = LeaseKeeper(
                    provider.repository,
                    lease,
                    ttl_seconds=600,
                    renewal_margin_seconds=context.settings.lease.renewal_margin_seconds,
                )
                provider.lease_keeper = lease_keeper
                lease = lease_keeper.ensure_current()
            if request.trigger == "shadow":
                self._discover(request, context, run, lease, counters, shadow=True)
            else:
                if self.sender_factory is not None and isinstance(authorization, LiveAuthorization):
                    self._ai_pool = ThreadPoolExecutor(max_workers=self.ranking_workers, thread_name_prefix="muse")
                    pipeline = _DispatchPipeline(self.sender_factory, request, context, run, lease, authorization,
                                                 producer=self, pool=self._ai_pool)
                    self._pipeline_stop = pipeline.stop
                    pipeline.start()

                def dispatch_pending(*, drain: bool = False) -> bool:
                    nonlocal status
                    if pipeline is not None:
                        self._touch_lease(lease)
                        while True:
                            ready_items = self.repository.ready_items(
                                run.account_id, limit=context.settings.limits.per_run_success,
                                resume_ids=(mapping.resume_id for mapping in context.mappings),
                                vacancy_ids=_active_vacancy_ids(context),
                            )
                            waiting = any(item.id in counters.fresh_ranked and item.id not in pipeline.queued for item in ready_items)
                            pipeline.enqueue(ready_items, counters.fresh_ranked)
                            if not drain or pipeline.stop.is_set():
                                break
                            pipeline.drain()
                            if not waiting:
                                break
                        status = pipeline.status
                        return not pipeline.stop.is_set()
                    status = self._dispatch(
                        request, context, run, lease, authorization, counters,
                        max_items=None if drain else 1,
                        only_fresh=self.ranking_workers > 1 and not drain,
                    )
                    return (
                        status == "completed"
                        and counters.applied < context.settings.limits.per_run_success
                    )

                while True:
                    if isinstance(authorization, LiveAuthorization):
                        self._recover_and_retry(
                            context, run, lease, counters, on_ready=dispatch_pending,
                        )
                    if status == "completed" and not self._stop_requested(run, authorization) and request.trigger == "schedule":
                        self._discover(
                            request, context, run, lease, counters, shadow=False,
                            on_ready=dispatch_pending,
                        )
                    elif request.trigger in {"manual", "canary"}:
                        self._prepare_exact(request, context, run, lease, counters)
                    assert authorization is not None
                    if status == "completed":
                        dispatch_pending(drain=True)
                    applied = pipeline.counters.applied if pipeline is not None else counters.applied
                    if (not request.continuous or request.trigger != "schedule"
                            or status != "completed" or self._stop_requested(run, authorization)
                            or applied >= context.settings.limits.per_run_success):
                        break
                    cycle = self.repository.get_owned_search_cycle(run.id)
                    if cycle is not None:
                        self.repository.complete_search_cycle(cycle.id, lease.fencing_token)
                    # One process/run keeps its total cap. No new daily budget,
                    # no duplicate worker, and stop/lease checks during idle time.
                    for _ in range(30):
                        if self._stop_requested(run, authorization):
                            status = "interrupted"
                            break
                        self.sleeper(1)
                        self._touch_lease(lease)
                    if status != "completed":
                        break
                    counters.dispatch_seen.clear()
                    counters.fresh_ranked.clear()
                    if pipeline is not None:
                        pipeline.queued.clear()
                        pipeline.counters.dispatch_seen.clear()
                        pipeline.counters.fresh_ranked.clear()
        except LostLease:
            status = "interrupted"
            error = "lease_lost"
        except Exception as exc:
            status = "failed"
            error = _hh_access_reason(exc) or f"{type(exc).__name__}: {exc}"[:500]
        finally:
            if pipeline is not None:
                assert run is not None
                try:
                    pipeline.finish(cancel=status != "completed" or self.repository.run_stop_requested(run.id))
                except Exception as exc:
                    status, error = "failed", _hh_access_reason(exc) or type(exc).__name__
                    pipeline.finish(cancel=True)
                while pipeline.thread.is_alive():
                    pipeline.thread.join(timeout=0.2)
                    self._touch_lease(lease)
                for name in ("applied", "manual", "retry_wait", "skipped", "internal_errors"):
                    setattr(counters, name, getattr(counters, name) + getattr(pipeline.counters, name))
                if pipeline.error:
                    status, error = "failed", pipeline.error
                elif status == "completed":
                    status = pipeline.status
                self._pipeline_stop = None
            if self._ai_pool is not None:
                self._ai_pool.shutdown(wait=True, cancel_futures=True)
                self._ai_pool = None
            self.flush_ranking_usage()
            if lease_keeper is not None:
                provider.lease_keeper = previous_keeper
            if run is not None:
                # finish_run проверяет fence; если lease уже протух во время
                # долгого shadow-прогона — это не провал прогона, результаты
                # уже сохранены. Фиксируем completed вместо interrupted.
                try:
                    self.repository.finish_run(
                        run.id,
                        status=status,
                        counters=counters.as_dict(),
                        error=error,
                        fencing_token=(lease.fencing_token if status != "interrupted" or error != "lease_lost" else None),
                    )
                except LostLease:
                    if run is not None and request.trigger == "shadow":
                        self.repository.finish_run(
                            run.id,
                            status="completed",
                            counters=counters.as_dict(),
                            error="",
                            fencing_token=None,
                        )
                    else:
                        raise
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

    def _recover_and_retry(
        self, context, run, lease, counters: _Counters,
        *, on_ready: Callable[[], bool] | None = None,
    ) -> None:
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
        if self.ranking_workers > 1:
            self._refresh_ready(context, run, lease, counters, on_ready=on_ready)
        if on_ready is not None and not on_ready():
            return
        if not self._recover_unfinished_selection(
            context, run, lease, counters, on_ready=on_ready,
        ):
            return
        now = self._now()
        due = self.repository.list_due_items(
            run.account_id,
            states=(AutopilotState.RETRY_WAIT,),
            now=now,
            limit=context.settings.limits.per_run_success,
            resume_ids=(mapping.resume_id for mapping in context.mappings),
            vacancy_ids=_active_vacancy_ids(context),
        )
        eligibility_items = []
        previous_eligibility_decisions = {}
        for item in due:
            target = {
                RetryStage.ELIGIBILITY: AutopilotState.ELIGIBLE,
                RetryStage.APPLICATION: AutopilotState.READY,
                RetryStage.RECONCILIATION: AutopilotState.RECONCILING,
            }[item.retry_stage]
            if item.retry_stage is RetryStage.ELIGIBILITY:
                previous_eligibility_decisions[item.id] = (
                    self.repository.item_ranking_decision(item.id)
                )
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
            elif activated.state is AutopilotState.ELIGIBLE:
                eligibility_items.append(activated)
        if eligibility_items:
            self._retry_eligibility(
                eligibility_items,
                context,
                run,
                lease,
                counters,
                now=now,
                previous_decisions=previous_eligibility_decisions,
                on_ready=on_ready,
            )
        if on_ready is not None:
            on_ready()

    def _recover_unfinished_selection(
        self, context, run, lease, counters,
        *, on_ready: Callable[[], bool] | None = None,
    ) -> bool:
        """Re-evaluate selection left by an exited run, even outside search pages."""
        grouped: dict[str, list[Any]] = {}
        items = self.repository.list_due_items(
            run.account_id,
            states=(AutopilotState.DISCOVERED, AutopilotState.ELIGIBLE, AutopilotState.RANKED),
            now=self._now(),
            # Restore a small batch before fresh search, not an entire run's
            # quota of stale candidates requiring individual HH requests.
            limit=min(context.settings.limits.per_run_success, self.ranking_workers),
            resume_ids=(mapping.resume_id for mapping in context.mappings),
            vacancy_ids=_active_vacancy_ids(context),
        )
        for item in items:
            if getattr(item, "last_run_id", run.id) == run.id:
                continue
            grouped.setdefault(item.vacancy_id, []).append(item)
        if self.ranking_workers > 1:
            matches = {}
            mappings = {mapping.resume_id: mapping for mapping in context.mappings}
            for vacancy_id, competing_items in grouped.items():
                if self.repository.run_stop_requested(run.id):
                    return False
                matches[vacancy_id] = [(vacancy_id, mappings[item.resume_id])
                                      for item in competing_items if item.resume_id in mappings]
            self._evaluate_parallel(
                matches, RunRequest(run.account_id, "retry"), context, run, lease, counters,
                on_ready=on_ready, load_vacancies=True,
            )
            return not self.repository.run_stop_requested(run.id)
        for vacancy_id, competing_items in grouped.items():
            if self.repository.run_stop_requested(run.id):
                return False
            for item in competing_items:
                mapping = next((value for value in context.mappings
                                if value.resume_id.strip().casefold() == item.resume_id), None)
                if mapping is None:
                    continue
                lease = self._touch_lease(lease)
                vacancy = self._captcha_read(self.vacancy_loader, item.vacancy_id)
                if not isinstance(vacancy, NormalizedVacancy) or vacancy.id != item.vacancy_id:
                    raise ValueError("vacancy loader returned a different vacancy")
                self._evaluate(
                    vacancy, mapping, context, run, lease, counters, shadow=False,
                )
                if self.repository.run_stop_requested(run.id):
                    return False
            lease = self._touch_lease(lease)
            candidates = self.repository.adopt_ranked_candidates_for_retry(
                run.account_id, vacancy_id, run_id=run.id,
                fencing_token=lease.fencing_token, now=self._now(),
                active_resume_ids=[mapping.resume_id for mapping in context.mappings],
            )
            if candidates:
                self._finalize_ranked(
                    {vacancy_id: list(candidates)}, set(), context, run, lease,
                )
            if on_ready is not None and not on_ready():
                return False
        return True

    def _captcha_read(self, operation, *args, **kwargs):
        return read_with_captcha(operation, self.captcha_solver, *args, **kwargs)

    def _stop_on_hh_access_error(self, error, item, run, lease) -> None:
        reason = _hh_access_reason(error)
        if reason is None:
            return
        if self._pipeline_stop is not None:
            self._pipeline_stop.set()
        lease = self._touch_lease(lease)
        self.repository.append_event(
            item.id, reason, _hh_access_event_metadata(error, reason),
            run_id=run.id, fencing_token=lease.fencing_token,
        )
        raise error

    @staticmethod
    def _basic_requirements_mode(context: EngineRunContext) -> bool:
        """Return whether only hard requirements should admit a candidate."""
        return str(context.settings.ranking.get("mode", "ranked")).strip().casefold() == "basic_requirements"

    def _selection_read_failure(self, item, error, run, lease, counters) -> None:
        """An unavailable/removed vacancy must not abort unrelated selection."""
        self._stop_on_hh_access_error(error, item, run, lease)
        lease = self._touch_lease(lease)
        if getattr(error, "status_code", None) == 404:
            self.repository.transition_item(
                item.id, item.version, AutopilotState.SKIPPED, "vacancy_not_found",
                {"status_code": 404}, run_id=run.id, fencing_token=lease.fencing_token,
            )
            counters.skipped += 1
        else:
            self.repository.append_event(
                item.id, "vacancy_read_retry", {"error_type": type(error).__name__},
                run_id=run.id, fencing_token=lease.fencing_token,
            )
            counters.retry_wait += 1

    def _retry_eligibility(
        self,
        items,
        context,
        run,
        lease,
        counters: _Counters,
        *,
        now: datetime,
        previous_decisions,
        on_ready: Callable[[], bool] | None = None,
    ) -> None:
        touched: set[str] = set()
        evaluations = (
            self._parallel_eligibility_decisions(items, context, run, lease)
            if self.ranking_workers > 1
            else ((item, None) for item in items)
        )
        for item, prepared in evaluations:
            if self.repository.run_stop_requested(run.id):
                evaluations.close()
                return
            lease = self._touch_lease(lease)
            touched.add(item.vacancy_id)
            mapping = next(
                (
                    value
                    for value in context.mappings
                    if value.resume_id.strip().casefold() == item.resume_id
                ),
                None,
            )
            if mapping is None:
                self.repository.transition_item(
                    item.id,
                    item.version,
                    AutopilotState.DEAD,
                    "retry_resume_unavailable",
                    run_id=run.id,
                    fencing_token=lease.fencing_token,
                )
                counters.skipped += 1
                continue
            try:
                if isinstance(prepared, Exception):
                    raise prepared
                if prepared is not None:
                    decision = prepared
                else:
                    vacancy = self._captcha_read(self.vacancy_loader, item.vacancy_id)
                    if (
                        not isinstance(vacancy, NormalizedVacancy)
                        or vacancy.id != item.vacancy_id
                    ):
                        raise ValueError("vacancy loader returned a different vacancy")
                    filter_decision = FilterDecision(
                        passed=item.filter_data["passed"],
                        reason=item.filter_data["reason"],
                        evidence=item.filter_data["evidence"],
                    )
                    if self._basic_requirements_mode(context):
                        decision = basic_requirements_decision(filter_decision)
                    else:
                        score = self.deterministic_ranker.score(
                            vacancy,
                            mapping.resume,
                            context.candidate_profile,
                            context.settings.ranking["weights"],
                        )
                        decision = self.ranking_policy.decide(
                            filter_decision,
                            score,
                            vacancy,
                            mapping.resume,
                            context.candidate_profile,
                        )
            except Exception as exc:
                self._stop_on_hh_access_error(exc, item, run, lease)
                if self.repository.run_stop_requested(run.id):
                    return
                lease = self._touch_lease(lease)
                decision = previous_decisions.get(item.id)
                if decision is None or not decision.retry:
                    self.repository.transition_item(
                        item.id,
                        item.version,
                        AutopilotState.DEAD,
                        "eligibility_retry_context_unavailable",
                        run_id=run.id,
                        fencing_token=lease.fencing_token,
                    )
                    counters.skipped += 1
                    continue
            if self.repository.run_stop_requested(run.id):
                return
            lease = self._touch_lease(lease)
            now = self._now()
            if decision.retry:
                self._schedule_eligibility_retry(
                    item,
                    decision,
                    context,
                    run,
                    lease,
                    counters,
                    now=now,
                )
                continue
            ranked = self.repository.record_ranking_decision(
                item.id,
                expected_version=item.version,
                decision=decision,
                run_id=run.id,
                fencing_token=lease.fencing_token,
            )
            if not decision.ready:
                self.repository.transition_item(
                    ranked.id,
                    ranked.version,
                    AutopilotState.SKIPPED,
                    decision.reason,
                    run_id=run.id,
                    fencing_token=lease.fencing_token,
                )
                counters.skipped += 1
            else:
                counters.fresh_ranked.add(item.id)

            self._finalize_retry_vacancy(item.vacancy_id, context, run, lease)
            if on_ready is not None and not on_ready():
                evaluations.close()
                return

        if self.repository.run_stop_requested(run.id):
            return
        for vacancy_id in sorted(touched):
            self._finalize_retry_vacancy(vacancy_id, context, run, lease)

    def _finalize_retry_vacancy(self, vacancy_id, context, run, lease) -> None:
        lease = self._touch_lease(lease)
        candidates = self.repository.adopt_ranked_candidates_for_retry(
            run.account_id, vacancy_id, run_id=run.id,
            fencing_token=lease.fencing_token,
            active_resume_ids=[mapping.resume_id for mapping in context.mappings],
            now=self._now(),
        )
        if candidates:
            self._finalize_ranked({vacancy_id: list(candidates)}, set(), context, run, lease)

    def _parallel_eligibility_decisions(self, items, context, run, lease):
        """Share ten workers across resumes; all repository I/O stays on the owner."""
        mappings = {value.resume_id.strip().casefold(): value for value in context.mappings}
        def jobs():
            for item in self._fair_order(items, lambda value: value.resume_id):
                mapping = mappings.get(item.resume_id)
                if mapping is None:
                    yield item, item.id, None
                    continue
                try:
                    vacancy = self._captcha_read(self.vacancy_loader, item.vacancy_id)
                    if not isinstance(vacancy, NormalizedVacancy) or vacancy.id != item.vacancy_id:
                        raise ValueError("vacancy loader returned a different vacancy")
                    filtered = self.hard_filter.evaluate(
                        vacancy, mapping.resume, context.candidate_profile, context.filter_context,
                    )
                    if self._basic_requirements_mode(context):
                        args = basic_requirements_decision(filtered)
                    else:
                        score = self.deterministic_ranker.score(
                            vacancy, mapping.resume, context.candidate_profile, context.settings.ranking["weights"],
                        )
                        args = (filtered, score, vacancy, mapping.resume, context.candidate_profile)
                except Exception as exc:
                    self._stop_on_hh_access_error(exc, item, run, lease)
                    args = exc
                yield item, item.id, args
        yield from self._ranking_results(jobs(), context, run, lease)

    @staticmethod
    def _fair_order(values, key):
        """Round-robin admission, without reserving idle workers for a resume."""
        queues = {}
        for value in values:
            queues.setdefault(key(value), deque()).append(value)
        while any(queues.values()):
            for queue in queues.values():
                if queue:
                    yield queue.popleft()

    def _ranking_results(self, jobs, context, run, lease):
        """Workers only call AI; the owner persists results and sends serially.

        Admit a bounded look-ahead and persist completed results while loading
        subsequent vacancies. Closing cancels unstarted work and drains active
        calls before releasing the lease.
        """
        pending = {}
        immediate = []
        pool_context = nullcontext(self._ai_pool) if self._ai_pool is not None else ThreadPoolExecutor(max_workers=self.ranking_workers, thread_name_prefix="muse-ranking")
        with pool_context as pool:
            try:
                for token, item_id, args in jobs:
                    lease = self._touch_lease(lease)
                    if self._stop_requested(run, None):
                        break
                    if isinstance(args, RankingDecision):
                        # Basic-requirements decisions are already complete;
                        # stream them to the owner so hard-filtered vacancies
                        # can start letter preparation before discovery ends.
                        yield token, args
                    elif args is None or isinstance(args, Exception):
                        immediate.append((token, args))
                    elif context.settings.ranking["ai_mode"] == "off":
                        try:
                            decision = self.ranking_policy.decide(*args)
                        except Exception as exc:
                            decision = exc
                        yield token, decision
                    else:
                        future = pool.submit(self._rank_in_worker, item_id, run.id, lease.fencing_token, args)
                        pending[future] = token
                    while len(pending) >= self.ranking_workers * 2:
                        lease = self._touch_lease(lease)
                        if self._stop_requested(run, None):
                            return
                        completed, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                        for future in completed:
                            token = pending.pop(future)
                            try:
                                decision = future.result()
                            except Exception as exc:
                                decision = exc
                            yield token, decision
                for result in immediate:
                    if self._stop_requested(run, None):
                        break
                    yield result
                while pending:
                    lease = self._touch_lease(lease)
                    if self._stop_requested(run, None):
                        break
                    completed, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                    for future in completed:
                        token = pending.pop(future)
                        try:
                            decision = future.result()
                        except Exception as exc:
                            decision = exc
                        yield token, decision
            finally:
                for future in pending:
                    future.cancel()
                while pending:
                    lease = self._touch_lease(lease)
                    completed, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                    for future in completed:
                        pending.pop(future)
                self._touch_lease(lease)

    def _rank_in_worker(self, item_id, run_id, fencing_token, args):
        self._ranking_events.put((item_id, run_id, fencing_token, "ai_evaluation_started"))
        try:
            return self.ranking_policy.decide(*args)
        finally:
            self._ranking_events.put((item_id, run_id, fencing_token, "ai_evaluation_finished"))

    def _refresh_ready(self, context, run, lease, counters, *, on_ready=None) -> None:
        """Revalidate a previous run's selected queue in the shared AI pool."""
        if hasattr(self.repository, "adopt_ready_items"):
            self.repository.adopt_ready_items(
                run.account_id, run_id=run.id, fencing_token=lease.fencing_token,
                limit=context.settings.limits.per_run_success,
                resume_ids=(mapping.resume_id for mapping in context.mappings),
                vacancy_ids=_active_vacancy_ids(context),
            )
        items = [item for item in self.repository.ready_items(
            run.account_id, limit=context.settings.limits.per_run_success,
            resume_ids=(mapping.resume_id for mapping in context.mappings),
            vacancy_ids=_active_vacancy_ids(context),
        ) if item.id not in counters.dispatch_seen]
        evaluations = self._parallel_eligibility_decisions(items, context, run, lease)
        try:
            for item, decision in evaluations:
                if self._stop_requested(run, None):
                    return
                lease = self._touch_lease(lease)
                item = self.repository.get_item(item.id)
                if isinstance(decision, HHTransportError) and decision.status_code == 404:
                    self._selection_read_failure(item, decision, run, lease, counters)
                elif decision is None or isinstance(decision, Exception) or decision.retry:
                    counters.dispatch_seen.add(item.id)
                    counters.retry_wait += 1
                    self.repository.append_event(
                        item.id, "ready_revalidation_retry", {},
                        run_id=run.id, fencing_token=lease.fencing_token,
                    )
                elif decision.ready:
                    counters.fresh_ranked.add(item.id)
                else:
                    self.repository.transition_item(
                        item.id, item.version, AutopilotState.SKIPPED,
                        decision.reason, decision.to_dict(), run_id=run.id,
                        fencing_token=lease.fencing_token,
                    )
                    counters.skipped += 1
                if on_ready is not None and not on_ready():
                    return
        finally:
            evaluations.close()

    def _schedule_eligibility_retry(
        self,
        item,
        decision,
        context,
        run,
        lease,
        counters: _Counters,
        *,
        now: datetime,
    ):
        attempt_number = self.repository.eligibility_retry_count(item.id) + 1
        retry = context.settings.retry
        base = min(
            int(retry["max_delay_seconds"]),
            int(retry["base_delay_seconds"]) * (2 ** max(0, attempt_number - 1)),
        )
        jitter = float(retry["jitter_ratio"])
        sample = float(self.retry_random_source())
        if not 0.0 <= sample <= 1.0:
            raise ValueError("retry_random_source must return a value in 0..1")
        delay = base * ((1.0 - jitter) + 2.0 * jitter * sample)
        updated = self.repository.schedule_eligibility_retry(
            item.id,
            expected_version=item.version,
            decision=decision,
            attempt_number=attempt_number,
            max_attempts=int(retry["max_attempts"]),
            next_attempt_at=now + timedelta(seconds=delay),
            run_id=run.id,
            fencing_token=lease.fencing_token,
            now=now,
        )
        if updated.state is AutopilotState.RETRY_WAIT:
            counters.retry_wait += 1
        else:
            counters.skipped += 1
        return updated

    def _discover(
        self, request, context, run, lease, counters, *, shadow: bool,
        on_ready: Callable[[], bool] | None = None,
    ) -> None:
        allowed = _active_vacancy_ids(context)
        if allowed is not None and not shadow:
            # A reviewed batch is an explicit worklist: search ranking/paging
            # must not decide which of its IDs reach the preparation workers.
            curated_grouped = {
                vacancy_id: [(vacancy_id, mapping) for mapping in context.mappings]
                for vacancy_id in allowed
            }
            self._evaluate_parallel(
                curated_grouped, request, context, run, lease, counters,
                on_ready=on_ready, load_vacancies=True,
            )
            return
        remaining = context.settings.search.max_results_per_run
        grouped: dict[str, list[tuple[NormalizedVacancy, EngineSearchMapping]]] = {}
        seen: set[tuple[str, str]] = set()
        for mapping in context.mappings:
            if self._stop_requested(run, request.authorization):
                return
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
                if self._stop_requested(run, request.authorization):
                    return
                allowed = _active_vacancy_ids(context)
                if allowed is not None and vacancy.id not in allowed:
                    continue
                identity = (mapping.resume_id, vacancy.id)
                if identity in seen:
                    continue
                seen.add(identity)
                grouped.setdefault(vacancy.id, []).append((vacancy, mapping))
        if not shadow and self.ranking_workers > 1:
            self._evaluate_parallel(grouped, request, context, run, lease, counters, on_ready=on_ready)
            return
        # Compare every discovered resume for one vacancy, then dispatch it.
        # Slow AI decisions for unrelated vacancies no longer hold ready sends.
        for vacancy_id, matches in grouped.items():
            ranked: dict[str, list[RankedCandidate]] = {}
            blocked: set[str] = set()
            for vacancy, mapping in matches:
                if self._stop_requested(run, request.authorization):
                    return
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
                if on_ready is not None and not on_ready():
                    return

    def _evaluate_parallel(self, grouped, request, context, run, lease, counters, *, on_ready=None, load_vacancies=False) -> None:
        """Finalize each vacancy after its competing resumes, not the whole batch."""
        matches = [match for values in grouped.values() for match in values]
        items = {}
        # Register every competitor before an early result can seal a vacancy.
        # This is local DB work only; full HH reads happen lazily below.
        for vacancy, mapping in matches:
            if self._stop_requested(run, request.authorization):
                return
            vacancy_id = vacancy if load_vacancies else vacancy.id
            items[(vacancy_id, mapping.resume_id)] = self.repository.create_item(
                run.id, run.account_id, vacancy_id, mapping.resume_id, mapping.query_key,
            )
        def jobs():
            loaded = {}
            for vacancy, mapping in self._fair_order(matches, lambda value: value[1].resume_id):
                if self._stop_requested(run, request.authorization):
                    return
                vacancy_id = vacancy if load_vacancies else vacancy.id
                item = items[(vacancy_id, mapping.resume_id)]
                if item.state is not AutopilotState.DISCOVERED:
                    continue
                if load_vacancies:
                    if vacancy_id not in loaded:
                        try:
                            current = self._captcha_read(self.vacancy_loader, vacancy_id)
                            if not isinstance(current, NormalizedVacancy) or current.id != vacancy_id:
                                raise ValueError("vacancy loader returned a different vacancy")
                            loaded[vacancy_id] = current
                        except (HHTransportError, ValueError) as exc:
                            loaded[vacancy_id] = exc
                    vacancy = loaded[vacancy_id]
                    if isinstance(vacancy, Exception):
                        self._selection_read_failure(item, vacancy, run, lease, counters)
                        continue
                decision = self.hard_filter.evaluate(
                    vacancy, mapping.resume, context.candidate_profile, context.filter_context,
                )
                if not decision.passed:
                    self._evaluate(vacancy, mapping, context, run, lease, counters, shadow=False)
                    continue
                if self._basic_requirements_mode(context):
                    prepared = basic_requirements_decision(decision)
                else:
                    score = self.deterministic_ranker.score(
                        vacancy, mapping.resume, context.candidate_profile, context.settings.ranking["weights"],
                    )
                    prepared = (
                        decision, score, vacancy, mapping.resume, context.candidate_profile,
                    )
                yield (vacancy, mapping), item.id, prepared
        evaluations = self._ranking_results(jobs(), context, run, lease)
        try:
            for (vacancy, mapping), decision in evaluations:
                if self._stop_requested(run, request.authorization):
                    return
                if isinstance(decision, Exception):
                    raise decision
                self._evaluate(
                    vacancy, mapping, context, run, lease, counters,
                    shadow=False, prepared_decision=decision,
                )
                self._finalize_retry_vacancy(vacancy.id, context, run, lease)
                if on_ready is not None and not on_ready():
                    return
            # Includes previously ranked candidates and vacancies filtered without AI.
            for vacancy_id in grouped:
                if self._stop_requested(run, request.authorization):
                    return
                self._finalize_retry_vacancy(vacancy_id, context, run, lease)
        finally:
            evaluations.close()

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
        vacancy = self._captcha_read(self.vacancy_loader, request.vacancy_id)
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
        prepared_decision=None,
    ) -> tuple[RankedCandidate | None, bool]:
        if not shadow:
            # Сначала занимаем item: терминальные (skipped/dead/applied)
            # не переоцениваем. AI-ранжирование дорогое, а после смены
            # правил поиска сюда попадают сотни уже отклонённых вакансий.
            lease = self._touch_lease(lease)
            item = self.repository.create_item(
                run.id,
                run.account_id,
                vacancy.id,
                mapping.resume_id,
                mapping.query_key,
            )
            if item.state is not AutopilotState.DISCOVERED:
                return None, False
        filter_decision = self.hard_filter.evaluate(
            vacancy,
            mapping.resume,
            context.candidate_profile,
            context.filter_context,
        )
        score = None
        ranking_decision = None
        if filter_decision.passed:
            if prepared_decision is not None:
                score = prepared_decision.rank_score
                ranking_decision = prepared_decision
            elif self._basic_requirements_mode(context):
                ranking_decision = basic_requirements_decision(filter_decision)
                score = ranking_decision.rank_score
            else:
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
        # Stop may arrive during a slow model call, before the next loop check.
        if self.repository.run_stop_requested(run.id):
            return None, False
        lease = self._touch_lease(lease)
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
        if ranking_decision.retry:
            self._schedule_eligibility_retry(
                item,
                ranking_decision,
                context,
                run,
                lease,
                counters,
                now=self._now(),
            )
            return None, True
        item = self.repository.record_ranking_decision(
            item.id,
            expected_version=item.version,
            decision=ranking_decision,
            run_id=run.id,
            fencing_token=lease.fencing_token,
        )
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
        counters.fresh_ranked.add(item.id)
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
        lease = self._touch_lease(lease)
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

    def _dispatch(self, request, context, run, lease, authorization, counters, *, max_items=None, only_fresh=False, item_ids=None) -> str:
        if self._stop_requested(run, authorization):
            return "interrupted"
        if counters.applied >= context.settings.limits.per_run_success:
            return "completed"
        queue_limit = context.settings.limits.per_run_success + len(counters.dispatch_seen)
        if hasattr(self.repository, "adopt_ready_items"):
            # ready-элементы прерванного прогона принадлежат старому run;
            # prepare_dispatch их отвергнет, пока не передадим текущему.
            self.repository.adopt_ready_items(
                run.account_id,
                run_id=run.id,
                fencing_token=lease.fencing_token,
                limit=queue_limit,
                resume_ids=(mapping.resume_id for mapping in context.mappings),
                vacancy_ids=_active_vacancy_ids(context),
            )
        ready = self.repository.ready_items(
            run.account_id,
            limit=queue_limit,
            resume_ids=(mapping.resume_id for mapping in context.mappings),
            vacancy_ids=_active_vacancy_ids(context),
        )
        if isinstance(authorization, LiteralConfirmation):
            ready = [
                item
                for item in ready
                if item.resume_id == request.resume_id
                and item.vacancy_id == request.vacancy_id
            ][:1 if request.trigger == "canary" else len(ready)]
        handled = 0
        for item in ready:
            if item_ids is not None and item.id not in item_ids:
                continue
            if item.id in counters.dispatch_seen:
                continue
            if only_fresh and item.id not in counters.fresh_ranked:
                continue
            if max_items is not None and handled >= max_items:
                break
            handled += 1
            if counters.applied >= context.settings.limits.per_run_success:
                break
            if self._stop_requested(run, authorization):
                return "interrupted"
            if counters.delay_due:
                self.sleeper(
                    self.delay_source(
                        context.settings.limits.send_delay_min_seconds,
                        context.settings.limits.send_delay_max_seconds,
                    )
                )
                counters.delay_due = False
                if self._stop_requested(run, authorization):
                    return "interrupted"
            counters.dispatch_seen.add(item.id)
            dispatch_stage = "vacancy_preflight"
            try:
                mapping = next(value for value in context.mappings if value.resume_id.strip().casefold() == item.resume_id)
                lease = self._touch_lease(lease)
                # Search snippets can omit pay and other mandatory conditions.
                # Refresh hard facts for every send, without repeating fresh AI ranking.
                vacancy = self._captcha_read(self.vacancy_loader, item.vacancy_id)
                decision = self.hard_filter.evaluate(
                    vacancy, mapping.resume, context.candidate_profile, context.filter_context,
                )
                if not decision.passed:
                    self.repository.transition_item(
                        item.id, item.version, AutopilotState.SKIPPED,
                        decision.reason, decision.to_dict(),
                        run_id=run.id, fencing_token=lease.fencing_token,
                    )
                    counters.skipped += 1
                    continue
                duplicate = self.repository.matching_application(item.account_id, vacancy)
                if duplicate is not None:
                    self.repository.transition_item(
                        item.id, item.version, AutopilotState.SKIPPED,
                        "duplicate_vacancy", {"applied_vacancy_id": duplicate},
                        run_id=run.id, fencing_token=lease.fencing_token,
                    )
                    counters.skipped += 1
                    continue
                if getattr(item, "origin_run_id", run.id) != run.id and item.id not in counters.fresh_ranked:
                    dispatch_stage = "ranking_revalidation"
                    # A previous run's ready queue was selected under older
                    # filters/ranking. Never dispatch it using a new grant alone.
                    accepted = decision.passed
                    if accepted:
                        if self._basic_requirements_mode(context):
                            decision = basic_requirements_decision(decision)
                        else:
                            score = self.deterministic_ranker.score(
                                vacancy, mapping.resume, context.candidate_profile,
                                context.settings.ranking["weights"],
                            )
                            decision = self.ranking_policy.decide(
                                decision, score, vacancy, mapping.resume, context.candidate_profile,
                            )
                            if decision.retry:
                                counters.retry_wait += 1
                                continue
                        accepted = decision.ready
                    if not accepted:
                        self.repository.transition_item(
                            item.id, item.version, AutopilotState.SKIPPED,
                            decision.reason, decision.to_dict(),
                            run_id=run.id, fencing_token=lease.fencing_token,
                        )
                        counters.skipped += 1
                        continue
                dispatch_stage = "resume_snapshot"
                self.repository.save_dispatch_resume_snapshot(
                    item.id, resume={**mapping.resume, "id": mapping.resume_id}, candidate=context.candidate_profile,
                    expected_version=item.version, fencing_token=lease.fencing_token,
                )
                dispatch_stage = "execute"
                self.repository.append_event(
                    item.id, "cover_letter_preparing", {},
                    run_id=run.id, fencing_token=lease.fencing_token,
                )
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
            except Exception as exc:
                self._stop_on_hh_access_error(exc, item, run, lease)
                if dispatch_stage == "vacancy_preflight" and isinstance(exc, HHTransportError) and exc.status_code == 404:
                    self._selection_read_failure(item, exc, run, lease, counters)
                    continue
                # Do not log exception text/locals: HTTP errors may contain credentials.
                frames = [f"{frame.name}:{frame.lineno}" for frame in traceback.extract_tb(exc.__traceback__)]
                logging.getLogger(__name__).error(
                    "Dispatch failed item=%s stage=%s error_type=%s frames=%s",
                    item.id, dispatch_stage, type(exc).__name__, "/".join(frames),
                )
                self.repository.append_event(
                    item.id, "dispatch_internal_error",
                    {"stage": dispatch_stage, "error_type": type(exc).__name__, "frames": frames},
                    run_id=run.id, fencing_token=lease.fencing_token,
                )
                counters.internal_errors += 1
                continue
            if not isinstance(result, ExecutionResult):
                counters.internal_errors += 1
                continue
            self.repository.append_event(
                item.id, "dispatch_finished", {"outcome": result.outcome_code},
                run_id=run.id, fencing_token=lease.fencing_token,
            )
            counters.delay_due = result.remote_post_dispatched
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
        if self._pipeline_stop is not None and self._pipeline_stop.is_set():
            return True
        if self.repository.run_stop_requested(run.id):
            return True
        if isinstance(authorization, LiveAuthorization):
            return self.repository.pause_active(
                run.account_id
            ) or self.repository.kill_switch_active(run.account_id)
        return False

    def _touch_lease(self, lease):
        """Продлить lease перед записью после долгих операций.

        AI-ранжирование одной вакансии занимает десятки секунд, а TTL lease
        по умолчанию 120с. Без явного продления первая же запись после
        такой паузы падает с LostLease и live-прогон обрывается.
        """
        self.flush_ranking_usage()
        keeper = getattr(self.search_provider, "lease_keeper", None)
        if keeper is not None:
            lease = keeper.ensure_current()
        while not self._ranking_events.empty():
            item_id, run_id, fence, reason = self._ranking_events.get_nowait()
            self.repository.append_event(
                item_id, reason, {"workers_total": self.ranking_workers},
                run_id=run_id, fencing_token=fence,
            )
        return lease

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
