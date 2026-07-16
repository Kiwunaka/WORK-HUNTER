from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from types import SimpleNamespace

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
    AutopilotState,
    ExecutionResult,
    FilterDecision,
    LiteralConfirmation,
    LiveAuthorization,
    NormalizedVacancy,
    RankScore,
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

    def list_due_items(self, account_id, *, states, now, limit):
        wanted = set(states)
        return [item for item in self.items if item.state in wanted][:limit]

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
        item.version += 1
        return item

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

    def ready_items(self, account_id, *, limit):
        return [
            item
            for item in sorted(self.items, key=lambda value: value.id)
            if item.account_id == account_id and item.state is AutopilotState.READY
        ][:limit]

    def get_item(self, item_id):
        return next((item for item in self.items if item.id == item_id), None)

    def run_stop_requested(self, _run_id):
        return self.stop_requested

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
    def decide(self, filter_decision, rank_score, vacancy, resume, candidate):
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
        ranking_policy=FakeRankingPolicy(),
        executor=executor,
        reconciler=FakeReconciler(),
        context_provider=lambda _account_id: context,
        vacancy_loader=lambda vacancy_id: next(
            item for item in search.vacancies if item.id == vacancy_id
        ),
        owner_token_factory=lambda: "engine-test",
        clock=lambda: NOW,
        sleeper=lambda _seconds: None,
        delay_source=lambda low, high: low,
    )
    return EngineCase(trace, repo, search, hard_filter, executor, context, engine)


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
    assert next(item for item in case.repo.items if item.vacancy_id == "v-filtered").state is AutopilotState.SKIPPED


def test_manual_captcha_does_not_abort_unrelated_vacancy() -> None:
    case = make_case([vacancy("v-captcha"), vacancy("v-ok")])
    case.executor.outcomes["v-captcha"] = "manual_captcha"

    report = case.engine.run(case.live_request())

    assert report.applied == 1
    assert report.manual == 1
    assert case.executor.posted == ["v-captcha", "v-ok"]


def test_pause_between_items_stops_next_post_and_keeps_first_result() -> None:
    case = make_case([vacancy("v-1"), vacancy("v-2")])
    case.executor.after_first_post = lambda: setattr(case.repo, "paused", True)

    report = case.engine.run(case.live_request())

    assert report.applied == 1
    assert report.status == "interrupted"
    assert case.executor.posted == ["v-1"]


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
