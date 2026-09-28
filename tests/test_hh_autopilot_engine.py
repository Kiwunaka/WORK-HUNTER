from __future__ import annotations

from dataclasses import dataclass, replace
from contextlib import contextmanager
from copy import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from threading import Barrier, Event, Lock, get_ident

import pytest

from work_hunter.hh_autopilot.config import (
    default_autopilot_config,
    parse_autopilot_settings,
)
from work_hunter.hh_autopilot.engine import (
    EngineRunContext,
    EngineSearchMapping,
    HHAutopilot,
)
from work_hunter.hh_autopilot.repository import LeaseRecord, RunRecord
from work_hunter.hh_autopilot.search import normalize_vacancy
from work_hunter.hh_autopilot.types import (
    AIDecision,
    AutopilotState,
    ExecutionResult,
    FilterDecision,
    LiteralConfirmation,
    LiveAuthorization,
    NormalizedVacancy,
    RankScore,
    RankedCandidate,
    RankingDecision,
    RetryStage,
    RunRequest,
    SearchResult,
)


NOW = datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc)


@dataclass
class FakeItem:
    id: int
    vacancy_id: str
    resume_id: str = "r-1"
    account_id: str = "default"
    state: AutopilotState = AutopilotState.DISCOVERED
    retry_stage: RetryStage = RetryStage.ELIGIBILITY
    version: int = 0
    deterministic_score: float | None = None
    published_at: str = "2026-07-16T08:00:00+00:00"
    active_attempt_id: int | None = None
    filter_data: dict | None = None
    ranking_decision: RankingDecision | None = None
    eligibility_retries: int = 0


class FakeRepository:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.items: list[FakeItem] = []
        self.shadow_results = 0
        self.paused = False
        self.stop_requested = False
        self.runs: dict[int, RunRecord] = {}

    def active_grant(self, _account_id: str):
        return SimpleNamespace(id=7)

    def matching_application(self, account_id, vacancy):
        return None

    def save_dispatch_resume_snapshot(self, item_id, *, resume, candidate, expected_version, fencing_token):
        item = self.get_item(item_id)
        assert item.version == expected_version
        assert resume["id"] == item.resume_id
        self.trace.append(f"snapshot:{item.vacancy_id}")

    def acquire_lease(self, account_id, owner_token, *, ttl_seconds, now):
        self.trace.append("lease")
        return LeaseRecord(
            account_id=account_id,
            owner_token=owner_token,
            fencing_token=1,
            expires_at="2026-07-16T10:00:00+00:00",
            updated_at=NOW.isoformat(),
        )

    def release_lease(self, _lease) -> bool:
        self.trace.append("release")
        return True

    def create_run(
        self,
        account_id,
        *,
        trigger,
        policy_hash,
        grant_id=None,
        fencing_token=0,
        counters=None,
        status="running",
    ):
        run = RunRecord(
            id=len(self.runs) + 1,
            account_id=account_id,
            trigger=trigger,
            status=status,
            grant_id=grant_id,
            policy_hash=policy_hash,
            fencing_token=fencing_token,
            counters=counters or {},
            error="",
            started_at=NOW.isoformat(),
            finished_at="",
            created_at=NOW.isoformat(),
        )
        self.runs[run.id] = run
        self.trace.append("run")
        return run

    def finish_run(self, run_id, *, status="completed", counters=None, error="", fencing_token=None):
        run = replace(
            self.runs[run_id],
            status=status,
            counters=counters or {},
            error=error,
            finished_at=NOW.isoformat(),
        )
        self.runs[run_id] = run
        return run

    def recover_stale_applying(self, account_id, fencing_token, *, run_id, now):
        self.trace.append("recovery")
        return []

    def due_reconciliation_items(self, account_id, *, now, limit=1000):
        return []

    def list_due_items(self, account_id, *, states, now, limit, resume_ids=None, vacancy_ids=None):
        wanted = set(states)
        resumes = set(resume_ids) if resume_ids is not None else None
        vacancies = set(vacancy_ids) if vacancy_ids is not None else None
        return [item for item in self.items if item.state in wanted and (resumes is None or item.resume_id in resumes) and (vacancies is None or item.vacancy_id in vacancies)][:limit]

    def transition_item(
        self,
        item_id,
        expected_version,
        target,
        reason,
        metadata=None,
        *,
        run_id=None,
        fencing_token=None,
    ):
        item = self.get_item(item_id)
        assert item is not None and item.version == expected_version
        item.state = target
        item.version += 1
        if reason == "retry_due":
            self.trace.append(f"retry:{item.vacancy_id}")
        return item

    def create_item(self, origin_run_id, account_id, vacancy_id, resume_id, query_key):
        existing = next(
            (
                item
                for item in self.items
                if item.account_id == account_id
                and item.vacancy_id == vacancy_id
                and item.resume_id == resume_id
            ),
            None,
        )
        if existing is not None:
            return existing
        item = FakeItem(
            id=len(self.items) + 1,
            account_id=account_id,
            vacancy_id=vacancy_id,
            resume_id=resume_id,
        )
        self.items.append(item)
        return item

    def record_filter_decision(
        self,
        item_id,
        *,
        expected_version,
        decision,
        run_id,
        published_at="",
        fencing_token=None,
    ):
        item = self.get_item(item_id)
        assert item is not None and item.version == expected_version
        item.state = AutopilotState.ELIGIBLE if decision.passed else AutopilotState.SKIPPED
        item.version += 1
        item.published_at = published_at
        item.filter_data = decision.to_dict()
        return item

    def record_ranking_decision(
        self,
        item_id,
        *,
        expected_version,
        decision,
        run_id,
        fencing_token=None,
    ):
        item = self.get_item(item_id)
        assert item is not None and item.version == expected_version
        item.state = AutopilotState.RANKED
        item.deterministic_score = decision.score
        item.ranking_decision = decision
        item.version += 1
        return item

    def eligibility_retry_count(self, item_id):
        item = self.get_item(item_id)
        assert item is not None
        return item.eligibility_retries

    def schedule_eligibility_retry(
        self,
        item_id,
        *,
        expected_version,
        decision,
        attempt_number,
        max_attempts,
        next_attempt_at,
        run_id,
        fencing_token,
        now,
    ):
        item = self.get_item(item_id)
        assert item is not None and item.version == expected_version
        item.eligibility_retries = attempt_number
        item.ranking_decision = decision
        item.state = (
            AutopilotState.DEAD
            if attempt_number >= max_attempts
            else AutopilotState.RETRY_WAIT
        )
        item.version += 1
        return item

    def item_ranking_decision(self, item_id):
        item = self.get_item(item_id)
        assert item is not None
        return item.ranking_decision

    def adopt_ranked_candidates_for_retry(
        self,
        account_id,
        vacancy_id,
        *,
        run_id,
        fencing_token,
        active_resume_ids,
        now,
    ):
        if any(
            item.account_id == account_id
            and item.vacancy_id == vacancy_id
            and item.resume_id in active_resume_ids
            and item.state in {AutopilotState.DISCOVERED, AutopilotState.ELIGIBLE, AutopilotState.RETRY_WAIT}
            for item in self.items
        ):
            return ()
        return tuple(
            RankedCandidate(
                account_id=item.account_id,
                vacancy_id=item.vacancy_id,
                resume_id=item.resume_id,
                published_at=item.published_at,
                decision=item.ranking_decision,
                item_id=item.id,
                expected_version=item.version,
            )
            for item in self.items
            if item.account_id == account_id
            and item.vacancy_id == vacancy_id
            and item.resume_id in active_resume_ids
            and item.state is AutopilotState.RANKED
            and item.ranking_decision is not None
        )

    def finalize_ranked_candidates(
        self,
        expected_versions,
        *,
        selected_item_ids,
        resume_policy,
        run_id,
        fencing_token=None,
    ):
        selected = set(selected_item_ids)
        result = []
        for item_id, version in expected_versions.items():
            item = self.get_item(item_id)
            assert item is not None and item.version == version
            item.state = (
                AutopilotState.READY
                if item.id in selected
                else AutopilotState.SKIPPED
            )
            item.version += 1
            result.append(item)
        return tuple(result)

    def ready_items(self, account_id, *, limit, resume_ids=None, vacancy_ids=None):
        resumes = set(resume_ids) if resume_ids is not None else None
        vacancies = set(vacancy_ids) if vacancy_ids is not None else None
        return [
            item
            for item in sorted(self.items, key=lambda value: value.id)
            if item.account_id == account_id and item.state is AutopilotState.READY
            and (resumes is None or item.resume_id in resumes)
            and (vacancies is None or item.vacancy_id in vacancies)
        ][:limit]

    def get_item(self, item_id):
        return next((item for item in self.items if item.id == item_id), None)

    def run_stop_requested(self, _run_id):
        return self.stop_requested

    def append_event(self, item_id, reason, metadata=None, **kwargs):
        self.trace.append(f"event:{item_id}:{reason}")

    def pause_active(self, _account_id):
        return self.paused

    def kill_switch_active(self, _account_id):
        return False

    def save_shadow_result(self, *args, **kwargs):
        self.shadow_results += 1
        return SimpleNamespace(id=self.shadow_results)

    def count_shadow_results(self, _run_id):
        return self.shadow_results

    def count_items(self):
        return len(self.items)

    def count_reservations(self):
        return 0

    def seed_retry(self, vacancy_id: str) -> None:
        self.items.append(
            FakeItem(
                id=len(self.items) + 1,
                vacancy_id=vacancy_id,
                state=AutopilotState.RETRY_WAIT,
                retry_stage=RetryStage.APPLICATION,
            )
        )


class FakeAuthorizer:
    def issue_live_authorization(self, account_id, config, *, run_id, fencing_token):
        return LiveAuthorization(7, "applications", account_id, run_id, fencing_token, "policy")


class FakeSearch:
    def __init__(self, trace: list[str], vacancies: list[NormalizedVacancy]) -> None:
        self.trace = trace
        self.vacancies = vacancies

    def collect(self, request):
        self.trace.append("search:page:0")
        return SearchResult(tuple(self.vacancies), 1, 1, len(self.vacancies), len(self.vacancies))


class FakeFilter:
    def __init__(self, trace: list[str], rejected: set[str] | None = None) -> None:
        self.trace = trace
        self.rejected = rejected or set()

    def evaluate(self, vacancy, resume, candidate, context):
        self.trace.append(f"filter:{vacancy.id}")
        if vacancy.id in self.rejected:
            return FilterDecision(
                False,
                "hard_filter:excluded_keywords",
                {"matched": ("blocked",)},
            )
        return FilterDecision(
            True,
            "hard_filters_passed",
            {
                "checks": (
                    "vacancy_open",
                    "history",
                    "blacklists",
                    "keywords_and_roles",
                    "area_and_relocation",
                    "work_format",
                    "experience",
                    "salary",
                    "candidate_constraints",
                    "application_capabilities",
                )
            },
        )


class FakeRanker:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace

    def score(self, vacancy, resume, candidate, weights):
        self.trace.append(f"rank:{vacancy.id}")
        return RankScore(
            80.0,
            {
                "role": 80.0,
                "skills": 80.0,
                "experience": 80.0,
                "salary": 80.0,
                "work_format": 80.0,
                "area": 80.0,
                "industry": 80.0,
            },
            weights,
        )


class FakeRankingPolicy:
    def __init__(self, retries: int = 0) -> None:
        self.retries = retries

    def decide(self, filter_decision, rank_score, vacancy, resume, candidate):
        if self.retries:
            self.retries -= 1
            return RankingDecision(
                False,
                True,
                "ai_unavailable",
                rank_score,
                AIDecision(
                    available=False,
                    suitable=None,
                    confidence=None,
                    reason="ai_unavailable",
                ),
            )
        return RankingDecision(True, False, "deterministic_score", rank_score)


class FakeExecutor:
    def __init__(self, trace: list[str], repo: FakeRepository) -> None:
        self.trace = trace
        self.repo = repo
        self.outcomes: dict[str, str] = {}
        self.after_first_post = None
        self.posted: list[str] = []

    def execute(self, item_id, authorization, lease, now=None):
        item = self.repo.get_item(item_id)
        assert item is not None
        self.trace.append(f"post:{item.vacancy_id}")
        self.posted.append(item.vacancy_id)
        code = self.outcomes.get(item.vacancy_id, "applied")
        if code == "manual_captcha":
            state = AutopilotState.MANUAL_CHALLENGE
            sent = True
        elif code == "internal_error":
            state = AutopilotState.RETRY_WAIT
            sent = False
        else:
            state = AutopilotState.APPLIED
            sent = True
        item.state = state
        if len(self.posted) == 1 and self.after_first_post is not None:
            self.after_first_post()
        return ExecutionResult(item.id, item.id, item.id, state, code, sent)


class FakeReconciler:
    def reconcile(self, item_id, provenance, lease, now=None):
        raise AssertionError("no reconciliation item expected")


def vacancy(vacancy_id: str) -> NormalizedVacancy:
    return normalize_vacancy(
        {
            "id": vacancy_id,
            "name": f"Vacancy {vacancy_id}",
            "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}",
            "employer": {"id": "e-1", "name": "Test"},
            "area": {"id": "1", "name": "Moscow"},
            "schedule": {"id": "remote", "name": "Remote"},
            "employment": {"id": "full", "name": "Full"},
            "experience": {"id": "between1And3", "name": "1-3"},
            "published_at": "2026-07-16T08:00:00+00:00",
            "archived": False,
            "has_test": False,
            "response_letter_required": False,
        }
    )


@dataclass
class EngineCase:
    trace: list[str]
    repo: FakeRepository
    search: FakeSearch
    filter: FakeFilter
    executor: FakeExecutor
    context: EngineRunContext
    engine: HHAutopilot

    def live_request(self) -> RunRequest:
        return RunRequest("default", "schedule")


def make_case(
    vacancies: list[NormalizedVacancy] | None = None,
    *,
    rejected: set[str] | None = None,
    ranking_retries: int = 0,
) -> EngineCase:
    trace: list[str] = []
    repo = FakeRepository(trace)
    raw = default_autopilot_config()
    raw["accounts"][0].update(
        {"enabled": True, "paused": False, "authorization_generation": 1}
    )
    raw["limits"].update(
        {"per_run_success": 10, "send_delay_min_seconds": 1, "send_delay_max_seconds": 1}
    )
    settings = parse_autopilot_settings(raw)
    mapping = EngineSearchMapping(
        resume_id="r-1",
        resume={"id": "r-1"},
        query_key="preset",
        params={"text": "python"},
    )
    context = EngineRunContext(
        raw_config=raw,
        settings=settings,
        policy_hash="policy",
        candidate_profile={},
        mappings=(mapping,),
    )
    search = FakeSearch(trace, vacancies or [vacancy("v-1")])
    hard_filter = FakeFilter(trace, rejected)
    executor = FakeExecutor(trace, repo)
    engine = HHAutopilot(
        repository=repo,
        authorizer=FakeAuthorizer(),
        search_provider=search,
        hard_filter=hard_filter,
        deterministic_ranker=FakeRanker(trace),
        ranking_policy=FakeRankingPolicy(ranking_retries),
        executor=executor,
        reconciler=FakeReconciler(),
        context_provider=lambda _account_id: context,
        vacancy_loader=lambda vacancy_id: next(
            (item for item in search.vacancies if item.id == vacancy_id),
            vacancy(vacancy_id),
        ),
        owner_token_factory=lambda: "engine-test",
        clock=lambda: NOW,
        sleeper=lambda _seconds: None,
        delay_source=lambda low, high: low,
        retry_random_source=lambda: 0.5,
    )
    return EngineCase(trace, repo, search, hard_filter, executor, context, engine)


def test_regional_copy_of_applied_vacancy_is_skipped_before_post() -> None:
    case = make_case([vacancy("copy"), vacancy("new")])
    case.repo.matching_application = lambda _account, current: "original" if current.id == "copy" else None

    report = case.engine.run(case.live_request())

    assert report.applied == 1
    assert case.executor.posted == ["new"]
    assert next(item for item in case.repo.items if item.vacancy_id == "copy").state is AutopilotState.SKIPPED


def test_previous_run_ready_item_is_rechecked_before_dispatch() -> None:
    case = make_case([vacancy("stale")], rejected={"stale"})
    item = FakeItem(id=1, vacancy_id="stale", state=AutopilotState.READY)
    item.origin_run_id = 999
    case.repo.items.append(item)
    report = case.engine.run(case.live_request())
    assert item.state is AutopilotState.SKIPPED
    assert "post:stale" not in case.trace
    assert report.applied == 0


def test_vacancy_allowlist_preserves_unselected_ready_and_retry_work() -> None:
    # The curated vacancy is absent from ordinary search results.
    case = make_case([vacancy("devops"), vacancy("lead")])
    ready_devops = FakeItem(id=1, vacancy_id="devops", state=AutopilotState.READY)
    ready_lead = FakeItem(id=2, vacancy_id="lead", state=AutopilotState.READY)
    retry_lead = FakeItem(id=3, vacancy_id="lead-retry", state=AutopilotState.RETRY_WAIT)
    case.repo.items.extend((ready_devops, ready_lead, retry_lead))
    settings = replace(case.context.settings, filters={
        **case.context.settings.filters, "allowed_vacancy_ids": ["ai"],
    })
    case.engine.context_provider = lambda _account: replace(case.context, settings=settings)

    report = case.engine.run(case.live_request())

    assert report.applied == 1
    assert "post:ai" in case.trace
    assert "post:devops" not in case.trace and "post:lead" not in case.trace
    assert "search:page:0" not in case.trace
    assert "filter:devops" not in case.trace and "filter:lead" not in case.trace
    assert ready_devops.state is AutopilotState.READY
    assert ready_lead.state is AutopilotState.READY
    assert retry_lead.state is AutopilotState.RETRY_WAIT


@pytest.mark.parametrize("phase", ["fresh", "retry", "ready"])
def test_basic_requirements_mode_bypasses_score_and_ai(monkeypatch, phase) -> None:
    case = make_case(ranking_retries=1 if phase == "retry" else 0)
    if phase == "retry":
        first = case.engine.run(case.live_request())
        assert first.retry_wait == 1
    elif phase == "ready":
        case.search.vacancies = []
        old = FakeItem(id=1, vacancy_id="old", state=AutopilotState.READY)
        old.origin_run_id = 999
        case.repo.items.append(old)
    settings = replace(
        case.context.settings,
        ranking={**case.context.settings.ranking, "mode": "basic_requirements"},
    )
    context = replace(case.context, settings=settings)
    case.engine.context_provider = lambda _account: context
    monkeypatch.setattr(
        case.engine.deterministic_ranker,
        "score",
        lambda *args, **kwargs: pytest.fail("basic requirements must not score"),
    )
    monkeypatch.setattr(
        case.engine.ranking_policy,
        "decide",
        lambda *args, **kwargs: pytest.fail("basic requirements must not call AI policy"),
    )

    trigger = "retry" if phase == "retry" else "schedule"
    report = case.engine.run(RunRequest("default", trigger))

    assert report.status == "completed"
    assert report.applied == 1


def test_live_run_orders_retry_search_filter_rank_and_post() -> None:
    case = make_case(
        [vacancy("v-pass"), vacancy("v-filtered")],
        rejected={"v-filtered"},
    )
    case.repo.seed_retry("v-retry")

    report = case.engine.run(case.live_request())

    assert case.trace.index("retry:v-retry") < case.trace.index("search:page:0")
    assert case.trace.index("filter:v-pass") < case.trace.index("rank:v-pass")
    assert case.trace.index("rank:v-pass") < case.trace.index("post:v-pass")
    assert report.applied == 2
    assert case.trace.index("post:v-pass") < case.trace.index("filter:v-filtered")
    assert next(item for item in case.repo.items if item.vacancy_id == "v-filtered").state is AutopilotState.SKIPPED


@pytest.mark.parametrize("workers", [1, 2])
def test_fresh_selection_rechecks_full_vacancy_facts_before_post(workers) -> None:
    current = vacancy("v-changed")
    case = make_case([current])
    case.engine.ranking_workers = workers

    def load_full_vacancy(vacancy_id):
        case.filter.rejected.add(vacancy_id)
        return current

    case.engine.vacancy_loader = load_full_vacancy
    report = case.engine.run(case.live_request())
    assert report.applied == 0
    assert case.executor.posted == []
    assert case.repo.items[0].state is AutopilotState.SKIPPED


@pytest.mark.parametrize("retry_phase", [False, True])
def test_parallel_ranking_shared_pool_with_single_writer_and_dispatcher(monkeypatch, retry_phase) -> None:
    jobs = 2 if retry_phase else 3
    case = make_case([vacancy(f"v-{i}") for i in range(jobs)], ranking_retries=10 if retry_phase else 0)
    mappings = tuple(replace(case.context.mappings[0], resume_id=f"r-{i}", resume={"id": f"r-{i}"}) for i in range(5))
    context = replace(case.context, mappings=mappings)
    case.engine.context_provider = lambda _: context
    if retry_phase:
        first = case.engine.run(case.live_request())
        assert first.retry_wait == 10
        assert first.applied == 0
    case.engine.ranking_workers = 10
    owner = get_ident()
    barrier, lock = Barrier(10, timeout=5), Lock()
    active, peak, finished = {}, {}, []
    total_peak = 0
    original_decide = case.engine.ranking_policy.decide

    def decide(filter_decision, score, job, resume, candidate):
        nonlocal total_peak
        assert get_ident() != owner
        role = resume['id']
        with lock:
            active[role] = active.get(role, 0) + 1
            peak[role] = max(peak.get(role, 0), active[role])
            total_peak = max(total_peak, sum(active.values()))
        if job.id in {'v-0', 'v-1'}:
            barrier.wait()
        result = original_decide(filter_decision, score, job, resume, candidate)
        with lock:
            active[role] -= 1
            finished.append((job.id, role))
        return result

    def on_owner(fn):
        def wrapped(*args, **kwargs):
            assert get_ident() == owner
            return fn(*args, **kwargs)
        return wrapped

    for name in ('create_item', 'append_event', 'record_filter_decision', 'record_ranking_decision', 'finalize_ranked_candidates'):
        monkeypatch.setattr(case.repo, name, on_owner(getattr(case.repo, name)))
    original_execute = case.executor.execute

    def execute(*args, **kwargs):
        assert get_ident() == owner
        item = case.repo.get_item(args[0])
        assert len([job for job, role in finished if job == item.vacancy_id]) == 5
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(case.engine.ranking_policy, 'decide', decide)
    monkeypatch.setattr(case.executor, 'execute', execute)
    case.engine.flush_ranking_usage = on_owner(lambda: None)
    report = case.engine.run(case.live_request())
    assert report.status == 'completed', report
    assert total_peak == 10
    assert all(peak[f'r-{i}'] >= 2 for i in range(5))
    assert report.internal_errors == 0
    assert report.applied == jobs
    assert len(case.executor.posted) == len(set(case.executor.posted)) == jobs


@pytest.mark.parametrize("retry_phase", [False, True])
def test_stop_during_parallel_ranking_never_dispatches(monkeypatch, retry_phase) -> None:
    case = make_case([vacancy('first'), vacancy('second')], ranking_retries=2 if retry_phase else 0)
    if retry_phase:
        assert case.engine.run(case.live_request()).retry_wait == 2
    case.engine.ranking_workers = 10
    original = case.engine.ranking_policy.decide

    def decide(*args):
        case.repo.stop_requested = True
        return original(*args)

    monkeypatch.setattr(case.engine.ranking_policy, 'decide', decide)
    report = case.engine.run(case.live_request())
    assert report.applied == 0
    assert case.executor.posted == []
    assert not any(item.state is AutopilotState.READY for item in case.repo.items)


@pytest.mark.parametrize("phase", ["discovery", "retry", "ready"])
def test_single_resume_uses_ten_workers_and_ranks_while_sender_is_busy(monkeypatch, phase) -> None:
    case = make_case([vacancy(str(i)) for i in range(15)], ranking_retries=15 if phase == "retry" else 0)
    context = replace(case.context, settings=replace(case.context.settings,
                      limits=replace(case.context.settings.limits, per_run_success=20)))
    case.engine.context_provider = lambda _: context
    if phase == "retry":
        assert case.engine.run(case.live_request()).retry_wait == 15
    elif phase == "ready":
        for i in range(15):
            item = FakeItem(id=i + 1, vacancy_id=str(i), state=AutopilotState.READY)
            item.origin_run_id = 999
            case.repo.items.append(item)
    case.engine.ranking_workers = 10
    sending, finished = Event(), Event()
    barrier, lock = Barrier(10, timeout=5), Lock()
    completed = []
    original_decide, original_execute = case.engine.ranking_policy.decide, case.executor.execute

    def decide(filtered, score, job, resume, candidate):
        if job.id != "0":
            assert sending.wait(5), "first ready vacancy must send before the batch completes"
            if int(job.id) <= 10:
                barrier.wait()  # Ten concurrent calls for ONE resume.
        result = original_decide(filtered, score, job, resume, candidate)
        with lock:
            completed.append(job.id)
            if len(completed) == 15:
                finished.set()
        return result

    def execute(*args, **kwargs):
        if not sending.is_set():
            sending.set()
            assert finished.wait(5), "ranking workers must keep draining the queue while sender blocks"
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(case.engine.ranking_policy, "decide", decide)
    monkeypatch.setattr(case.executor, "execute", execute)
    report = case.engine.run(case.live_request())
    assert report.status == "completed", report
    assert report.internal_errors == 0
    assert report.applied == 15
    assert len(set(case.executor.posted)) == 15


@pytest.mark.parametrize("send_cap", [1, 10])
def test_continuous_sender_does_not_block_next_discovery(monkeypatch, send_cap) -> None:
    case = make_case([vacancy("old"), vacancy("new")])
    context = replace(case.context, settings=replace(case.context.settings,
                      limits=replace(case.context.settings.limits, per_run_success=send_cap)))
    case.engine.context_provider = lambda _: context
    old = FakeItem(id=1, vacancy_id="old", state=AutopilotState.READY)
    old.origin_run_id = 999
    case.repo.items.append(old)
    case.engine.ranking_workers = 10
    sender_started, next_evaluated = Event(), Event()
    owner = get_ident()
    sender_threads = []
    original_decide, original_execute = case.engine.ranking_policy.decide, case.executor.execute

    @contextmanager
    def factory(context, lease):
        assert get_ident() != owner
        sender = copy(case.engine)
        sender.sender_factory = None
        sender_threads.append(get_ident())
        yield sender

    def decide(filtered, score, job, resume, candidate):
        if job.id == "new":
            assert sender_started.wait(5)
            next_evaluated.set()
        return original_decide(filtered, score, job, resume, candidate)

    def execute(item_id, *args, **kwargs):
        assert get_ident() == sender_threads[0]
        if item_id == old.id:
            sender_started.set()
            assert next_evaluated.wait(5), "new discovery must rank while old queue is sending"
        return original_execute(item_id, *args, **kwargs)

    case.engine.sender_factory = factory
    monkeypatch.setattr(case.engine.ranking_policy, "decide", decide)
    monkeypatch.setattr(case.executor, "execute", execute)
    report = case.engine.run(case.live_request())
    assert report.status == "completed", report
    assert report.internal_errors == 0
    assert report.applied == min(send_cap, 2)
    assert len(case.executor.posted) == len(set(case.executor.posted)) == report.applied
    assert len(sender_threads) == 1


@pytest.mark.parametrize("phase", ["selection", "sender"])
def test_hh_forbidden_stops_pipeline_and_preserves_ready_queue(phase) -> None:
    from work_hunter.hh_transport.errors import HHForbiddenError

    case = make_case([vacancy("old"), vacancy("other")])
    case.search.vacancies = []
    case.engine.ranking_workers = 10
    for number, name in enumerate(("old", "other"), start=1):
        item = FakeItem(id=number, vacancy_id=name, state=AutopilotState.READY)
        item.origin_run_id = 999
        case.repo.items.append(item)
    forbidden_reads = []

    def forbidden(vacancy_id):
        forbidden_reads.append(vacancy_id)
        raise HHForbiddenError("HH API error 403", status_code=403)

    @contextmanager
    def factory(context, lease):
        sender = copy(case.engine)
        sender.sender_factory = None
        if phase == "sender":
            sender.vacancy_loader = forbidden
        yield sender

    case.engine.sender_factory = factory
    if phase == "selection":
        case.engine.vacancy_loader = forbidden
    report = case.engine.run(case.live_request())

    assert report.status == "failed"
    assert case.repo.runs[report.run_id].error == "hh_access_forbidden"
    assert report.internal_errors == 0
    assert len(forbidden_reads) == 1
    assert case.executor.posted == []
    assert all(item.state is AutopilotState.READY for item in case.repo.items)
    assert case.trace[-1] == "release"


def test_hh_captcha_event_metadata_is_allowlisted_and_keeps_state() -> None:
    from work_hunter.hh_autopilot.engine import _hh_access_event_metadata
    from work_hunter.hh_transport.errors import HHForbiddenError

    error = HHForbiddenError(
        "HH API error 403",
        status_code=403,
        code="captcha_required",
        payload={
            "challenge_metadata": {
                "captcha_url": (
                    "https://hh.ru/account/captcha?state=opaque-state"
                    "&token=secret"
                ),
                "location": "https://evil.example/captcha?token=secret",
                "request_id": "req-146",
                "raw_response_body": "secret",
            }
        },
    )

    assert _hh_access_event_metadata(error, "hh_captcha_required") == {
        "status_code": 403,
        "captcha_url": (
            "https://hh.ru/account/captcha?state=opaque-state"
            "&backurl=https%3A%2F%2Fhh.ru%2F"
        ),
        "request_id": "req-146",
    }


@pytest.mark.parametrize("state", [AutopilotState.DISCOVERED, AutopilotState.READY])
def test_stateful_captcha_during_vacancy_read_retries_get_without_duplicate_post(state) -> None:
    from work_hunter.hh_transport.errors import HHForbiddenError

    job = vacancy("v-captcha")
    case = make_case([job])
    case.search.vacancies = []
    case.engine.ranking_workers = 10
    item = FakeItem(id=1, vacancy_id=job.id, state=state)
    item.origin_run_id = item.last_run_id = 999
    case.repo.items.append(item)
    reads = 0
    solved = []

    def load(_vacancy_id):
        nonlocal reads
        reads += 1
        if reads <= 2:
            raise HHForbiddenError(
                "captcha required", status_code=403, code="captcha_required",
                payload={"challenge_metadata": {"captcha_url":
                    "https://hh.ru/account/captcha?state=opaque"}},
            )
        return job

    case.engine.vacancy_loader = load
    # The browser can report failure after HH has already unlocked the API.
    case.engine.captcha_solver = lambda error: solved.append(error.code) or False

    report = case.engine.run(case.live_request())
    assert report.status == "completed", report
    assert report.applied == 1
    assert solved == ["captcha_required"]
    assert case.executor.posted == [job.id]


def test_auto_captcha_accepts_only_official_stateful_url() -> None:
    from work_hunter.hh_transport.errors import HHForbiddenError
    from work_hunter.services import _hh_stateful_captcha_url

    def challenge(url):
        return HHForbiddenError(
            "captcha required", status_code=403, code="captcha_required",
            payload={"challenge_metadata": {"captcha_url": url}},
        )

    assert _hh_stateful_captcha_url(challenge(
        "https://hh.ru/account/captcha?state=opaque&token=secret"
    )) == "https://hh.ru/account/captcha?state=opaque&backurl=https%3A%2F%2Fhh.ru%2F"
    assert _hh_stateful_captcha_url(challenge("https://hh.ru/account/captcha")) == ""
    assert _hh_stateful_captcha_url(challenge(
        "https://evil.example/account/captcha?state=opaque"
    )) == ""
    assert _hh_stateful_captcha_url(challenge(
        "https://hh.ru:8443/account/captcha?state=opaque"
    )) == ""


def test_parallel_letters_are_ready_before_single_sender_and_queue_refills() -> None:
    jobs = [vacancy(f"v-{number}") for number in range(25)]
    case = make_case(jobs)
    case.search.vacancies = []
    case.engine.ranking_workers = 10
    context = replace(case.context, settings=replace(case.context.settings,
                      limits=replace(case.context.settings.limits, per_run_success=25)))
    case.engine.context_provider = lambda _: context
    for number, job in enumerate(jobs, start=1):
        item = FakeItem(id=number, vacancy_id=job.id, state=AutopilotState.READY)
        item.origin_run_id = 999
        case.repo.items.append(item)
    from threading import Barrier, Lock
    first_ten = Barrier(10)
    admission_lock, admitted = Lock(), 0
    prepared, preparing_threads, sender_threads = set(), set(), set()
    execute = case.executor.execute

    def prepare(item, context, run, lease):
        nonlocal admitted
        preparing_threads.add(get_ident())
        with admission_lock:
            admitted += 1
            early = admitted <= 10
        if early:
            first_ten.wait(timeout=5)
        prepared.add(item.id)

    def checked_execute(item_id, *args, **kwargs):
        assert item_id in prepared
        sender_threads.add(get_ident())
        return execute(item_id, *args, **kwargs)

    @contextmanager
    def factory(context, lease):
        yield copy(case.engine)

    case.engine.sender_factory = factory
    case.engine.letter_preparer = prepare
    case.executor.execute = checked_execute
    report = case.engine.run(case.live_request())
    assert report.status == "completed", report
    assert report.applied == 25
    assert len(preparing_threads) == 10
    assert len(sender_threads) == 1
    assert preparing_threads.isdisjoint(sender_threads)
    assert len(case.executor.posted) == len(set(case.executor.posted)) == 25


def test_prepared_letter_reaches_sender_while_discovery_is_waiting() -> None:
    case = make_case([vacancy("first"), vacancy("second")])
    case.search.vacancies = []
    settings = replace(case.context.settings, ranking={
        **case.context.settings.ranking, "ai_mode": "off",
    })
    case.engine.context_provider = lambda _: replace(case.context, settings=settings)
    case.engine.ranking_workers = 10
    for item_id, vacancy_id in enumerate(("first", "second"), start=1):
        item = FakeItem(id=item_id, vacancy_id=vacancy_id, state=AutopilotState.READY)
        item.origin_run_id = 999
        case.repo.items.append(item)
    reading_second, first_sent = Event(), Event()

    def load(vacancy_id):
        if vacancy_id == "second":
            reading_second.set()
            assert first_sent.wait(timeout=3), "sender waited for discovery"
        return vacancy(vacancy_id)

    def prepare(item, *_args):
        if item.vacancy_id == "first":
            assert reading_second.wait(timeout=3)

    @contextmanager
    def factory(_context, _lease):
        yield copy(case.engine)

    case.engine.vacancy_loader = load
    case.engine.letter_preparer = prepare
    case.engine.sender_factory = factory
    case.executor.after_first_post = first_sent.set

    report = case.engine.run(case.live_request())

    assert report.status == "completed", report
    assert report.applied == 2
    assert case.executor.posted == ["first", "second"]


def test_continuous_run_searches_again_with_same_total_cap() -> None:
    case = make_case([vacancy("first"), vacancy("next")])
    case.search.vacancies = [vacancy("first")]
    context = replace(case.context, settings=replace(case.context.settings,
                      limits=replace(case.context.settings.limits, per_run_success=2)))
    case.engine.context_provider = lambda _: context
    def completed_cycle(run_id):
        case.search.vacancies = [vacancy("next")]
        return None
    case.repo.get_owned_search_cycle = completed_cycle
    report = case.engine.run(replace(case.live_request(), continuous=True))
    assert report.status == "completed"
    assert report.applied == 2
    assert case.executor.posted == ["first", "next"]
    assert len(case.repo.runs) == 1


@pytest.mark.parametrize("bad_markup", [False, True])
def test_deleted_orphan_does_not_abort_other_vacancies(monkeypatch, bad_markup) -> None:
    from work_hunter.hh_transport.errors import HHTransportError
    case = make_case([vacancy("gone"), vacancy("good")])
    case.search.vacancies = [vacancy("good")]
    case.engine.ranking_workers = 10
    old = FakeItem(id=1, vacancy_id="gone", state=AutopilotState.ELIGIBLE)
    old.last_run_id = 999
    case.repo.items.append(old)
    create = case.repo.create_item

    def recover(run_id, *args):
        item = create(run_id, *args)
        if item.id == old.id and item.last_run_id != run_id:
            item.state, item.last_run_id = AutopilotState.DISCOVERED, run_id
        return item

    def load(vacancy_id):
        if vacancy_id == "gone":
            if bad_markup:
                raise ValueError("description contains malformed markup")
            raise HHTransportError("not found", status_code=404)
        return vacancy(vacancy_id)

    monkeypatch.setattr(case.repo, "create_item", recover)
    case.engine.vacancy_loader = load
    report = case.engine.run(case.live_request())
    assert report.status == "completed", report
    assert old.state is (AutopilotState.DISCOVERED if bad_markup else AutopilotState.SKIPPED)
    assert case.executor.posted == ["good"]


@pytest.mark.parametrize("workers", [1, 10])
def test_interrupted_selection_outside_search_is_reevaluated(monkeypatch, workers) -> None:
    current = vacancy("v-orphan")
    case = make_case([current])
    case.engine.ranking_workers = workers
    case.search.vacancies = []
    case.engine.vacancy_loader = lambda vacancy_id: current
    item = FakeItem(id=1, vacancy_id=current.id, state=AutopilotState.ELIGIBLE)
    item.last_run_id = 999
    item.origin_run_id = 999
    case.repo.items.append(item)

    def recover(run_id, *args):
        if item.last_run_id != run_id:
            item.state = AutopilotState.DISCOVERED
            item.last_run_id = run_id
            item.version += 1
        return item

    monkeypatch.setattr(case.repo, "create_item", recover)
    report = case.engine.run(case.live_request())

    assert report.applied == 1
    assert case.trace.index("filter:v-orphan") < case.trace.index("rank:v-orphan")
    assert case.trace.index("rank:v-orphan") < case.trace.index("post:v-orphan")
    assert case.trace.count("post:v-orphan") == 1
    assert case.trace.count("rank:v-orphan") == 1


def test_streaming_compares_resumes_then_posts_before_next_vacancy() -> None:
    case = make_case([vacancy("first"), vacancy("second")])
    other = replace(case.context.mappings[0], resume_id="r-2", resume={"id": "r-2"})
    context = replace(case.context, mappings=(*case.context.mappings, other))
    case.engine.context_provider = lambda _account: context
    original = case.engine.deterministic_ranker.score

    def score(current, resume, candidate, weights):
        result = original(current, resume, candidate, weights)
        value = 90.0 if resume["id"] == "r-2" else 80.0
        return replace(result, score=value, components={key: value for key in result.components})

    case.engine.deterministic_ranker.score = score
    sleeps = []
    case.engine.sleeper = sleeps.append
    report = case.engine.run(case.live_request())

    assert report.applied == 2, case.repo.runs[report.run_id].error
    assert case.executor.posted == ["first", "second"]
    first_post = case.trace.index("post:first")
    assert case.trace[:first_post].count("rank:first") == 2
    assert first_post < case.trace.index("rank:second")
    assert {item.resume_id for item in case.repo.items if item.state is AutopilotState.APPLIED} == {"r-2"}
    assert sleeps == [context.settings.limits.send_delay_min_seconds]


@pytest.mark.parametrize("workers", [1, 10])
def test_streaming_stops_at_total_run_cap(workers) -> None:
    case = make_case([vacancy("first"), vacancy("second")])
    case.engine.ranking_workers = workers
    settings = replace(case.context.settings, limits=replace(case.context.settings.limits, per_run_success=1))
    context = replace(case.context, settings=settings)
    case.engine.context_provider = lambda _account: context

    report = case.engine.run(case.live_request())

    assert report.applied == 1
    assert len(case.executor.posted) == 1
    if workers == 1:
        assert case.executor.posted == ["first"]
        assert "rank:second" not in case.trace


def test_ai_off_ready_queue_sends_before_loading_the_next_vacancy() -> None:
    case = make_case([vacancy("first"), vacancy("second")])
    case.engine.ranking_workers = 10
    settings = replace(
        case.context.settings,
        ranking={**case.context.settings.ranking, "ai_mode": "off"},
    )
    case.engine.context_provider = lambda _account: replace(case.context, settings=settings)
    for item_id, vacancy_id in enumerate(("first", "second"), start=1):
        item = FakeItem(id=item_id, vacancy_id=vacancy_id, state=AutopilotState.READY)
        item.origin_run_id = 999
        case.repo.items.append(item)
    original_loader = case.engine.vacancy_loader

    def load(vacancy_id):
        case.trace.append(f"load:{vacancy_id}")
        return original_loader(vacancy_id)

    case.engine.vacancy_loader = load
    report = case.engine.run(case.live_request())

    assert report.applied == 2
    assert case.trace.index("post:first") < case.trace.index("load:second")


def test_old_ready_retry_is_evaluated_only_once_per_run() -> None:
    case = make_case([vacancy("old"), vacancy("new")], ranking_retries=1)
    item = FakeItem(id=1, vacancy_id="old", state=AutopilotState.READY)
    item.origin_run_id = 999
    case.repo.items.append(item)

    report = case.engine.run(case.live_request())

    assert report.retry_wait == 1
    assert report.applied == 1
    assert case.executor.posted == ["new"]
    assert case.trace.count("rank:old") == 1


def test_ai_ranking_retry_is_reranked_and_dispatched() -> None:
    case = make_case([vacancy("v-ai-retry")], ranking_retries=1)

    first = case.engine.run(case.live_request())
    second = case.engine.run(RunRequest("default", "retry"))

    assert first.retry_wait == 1
    assert second.applied == 1
    assert case.executor.posted == ["v-ai-retry"]
    assert case.trace.count("rank:v-ai-retry") == 2


def test_eligibility_retry_refreshes_lease_after_slow_ai_before_writing() -> None:
    case = make_case([vacancy("v-ai-retry")], ranking_retries=1)
    case.engine.run(case.live_request())
    needs_refresh = False
    decide = case.engine.ranking_policy.decide
    record = case.repo.record_ranking_decision

    def slow_decision(*args):
        nonlocal needs_refresh
        needs_refresh = True
        return decide(*args)

    def touch(lease):
        nonlocal needs_refresh
        needs_refresh = False
        return lease

    def checked_record(*args, **kwargs):
        assert not needs_refresh, "lease must be checked after the model call"
        return record(*args, **kwargs)

    case.engine.ranking_policy.decide = slow_decision
    case.engine._touch_lease = touch
    case.repo.record_ranking_decision = checked_record

    report = case.engine.run(RunRequest("default", "retry"))

    assert report.status == "completed"
    assert report.applied == 1


def test_manual_captcha_does_not_abort_unrelated_vacancy() -> None:
    case = make_case([vacancy("v-captcha"), vacancy("v-ok")])
    case.executor.outcomes["v-captcha"] = "manual_captcha"

    report = case.engine.run(case.live_request())

    assert report.applied == 1
    assert report.manual == 1
    assert case.executor.posted == ["v-captcha", "v-ok"]


@pytest.mark.parametrize("workers", [1, 10])
def test_pause_between_items_stops_next_post_and_keeps_first_result(workers) -> None:
    case = make_case([vacancy("v-1"), vacancy("v-2")])
    case.engine.ranking_workers = workers
    case.executor.after_first_post = lambda: setattr(case.repo, "paused", True)

    report = case.engine.run(case.live_request())

    assert report.applied == 1
    assert report.status == "interrupted"
    assert len(case.executor.posted) == 1


def test_stop_during_discovery_does_not_rank_the_rest_or_dispatch() -> None:
    case = make_case([vacancy("v-1"), vacancy("v-2")])
    original = case.engine.ranking_policy.decide

    def stop_after_ranking(*args):
        decision = original(*args)
        case.repo.stop_requested = True
        return decision

    case.engine.ranking_policy.decide = stop_after_ranking
    report = case.engine.run(case.live_request())

    assert report.status == "interrupted"
    assert "rank:v-1" in case.trace
    assert "rank:v-2" not in case.trace
    assert case.executor.posted == []
    assert case.trace[-1] == "release"


def test_shadow_search_writes_no_live_items_or_reservations() -> None:
    case = make_case([vacancy("v-shadow")])

    report = case.engine.run(RunRequest("default", "shadow"))

    assert report.shadow_results == 1
    assert case.executor.posted == []
    assert case.repo.count_items() == 0
    assert case.repo.count_reservations() == 0


def test_canary_is_exactly_one_named_vacancy() -> None:
    case = make_case([vacancy("v-1"), vacancy("v-2")])
    confirmation = LiteralConfirmation("default", "canary:default:r-1:v-1")

    report = case.engine.run(
        RunRequest(
            "default",
            "canary",
            authorization=confirmation,
            resume_id="r-1",
            vacancy_id="v-1",
        )
    )

    assert report.applied == 1
    assert case.executor.posted == ["v-1"]


def test_internal_error_for_one_item_does_not_abort_the_rest() -> None:
    case = make_case([vacancy("v-bad"), vacancy("v-ok")])
    case.executor.outcomes["v-bad"] = "internal_error"

    report = case.engine.run(case.live_request())

    assert report.internal_errors == 1
    assert report.applied == 1
    assert case.executor.posted == ["v-bad", "v-ok"]
