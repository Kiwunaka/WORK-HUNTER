from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from work_hunter.hh_autopilot.repository import (
    AutopilotRepository,
    LeaseKeeper,
    LostLease,
    RepositoryAuthorizationDenied,
    StaleWrite,
)
from work_hunter.hh_autopilot.search import (
    HHSearchProvider,
    SearchPage,
    SearchRequest,
    normalize_vacancy,
)
from work_hunter.storage import Storage


@dataclass(frozen=True)
class RunLease:
    run: Any
    lease: Any


@pytest.fixture
def repo(tmp_path: Path):
    storage = Storage(tmp_path / "search.db")
    yield AutopilotRepository(storage)
    storage.close()


def _context(
    repo: AutopilotRepository,
    *,
    trigger: str = "manual",
    policy_hash: str = "hash",
    owner: str = "owner",
) -> RunLease:
    grant_id = None
    if trigger != "shadow":
        repo.create_grants([("default", policy_hash, "operator", "test")])
        grant = repo.active_grant("default")
        assert grant is not None
        grant_id = grant.id
    lease = repo.acquire_lease("default", owner, ttl_seconds=3600)
    assert lease is not None
    run = repo.create_run(
        "default",
        trigger=trigger,
        policy_hash=policy_hash,
        grant_id=grant_id,
        fencing_token=lease.fencing_token,
    )
    return RunLease(run=run, lease=lease)


def _request(ctx: RunLease, **changes: Any) -> SearchRequest:
    values: dict[str, Any] = {
        "account_id": "default",
        "run_id": ctx.run.id,
        "resume_id": "r-1",
        "query_key": "preset:python",
        "params": {"text": "python"},
        "per_page": 2,
        "max_pages": 20,
        "remaining_budget": 20,
        "policy_hash": ctx.run.policy_hash,
        "fencing_token": ctx.lease.fencing_token,
        "mode": "shadow" if ctx.run.trigger == "shadow" else "live",
    }
    values.update(changes)
    return SearchRequest(**values)


def _recovery_context(
    repo: AutopilotRepository,
    previous: RunLease,
    *,
    policy_hash: str = "hash",
    owner: str = "recovery-owner",
) -> RunLease:
    repo.release_lease(previous.lease)
    lease = repo.acquire_lease("default", owner, ttl_seconds=3600)
    assert lease is not None
    grant = repo.active_grant("default")
    assert grant is not None
    run = repo.create_run(
        "default",
        trigger="recovery",
        policy_hash=policy_hash,
        grant_id=grant.id,
        fencing_token=lease.fencing_token,
    )
    return RunLease(run=run, lease=lease)


def _vacancy(vacancy_id: str, **changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": vacancy_id,
        "name": f"Python <b>{vacancy_id}</b>",
        "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}",
        "employer": {"id": "e-1", "name": " ACME&nbsp; Ltd "},
        "area": {"id": "1", "name": "Москва"},
        "salary": {"from": 100000, "to": 200000, "currency": "RUR", "gross": True},
        "schedule": {"id": "remote", "name": "Удаленная работа"},
        "employment": {"id": "full", "name": "Полная занятость"},
        "experience": {"id": "between1And3", "name": "1–3 года"},
        "professional_roles": [{"id": "96", "name": "Программист"}],
        "key_skills": [{"name": "Python"}],
        "snippet": {
            "requirement": "<highlighttext>Python</highlighttext> &amp; SQL",
            "responsibility": "Build\n services",
        },
        "published_at": "2026-07-15T12:00:00+0300",
        "archived": False,
        "has_test": False,
        "response_letter_required": True,
    }
    value.update(changes)
    return value


class FakePages:
    def __init__(self, pages: list[SearchPage]):
        self.pages = pages
        self.requested: list[int] = []
        self.recommended: list[tuple[str, int]] = []

    def search_vacancies_page(self, params: dict[str, Any]) -> SearchPage:
        page = params["page"]
        assert type(page) is int
        self.requested.append(page)
        return self.pages[page]

    def search_recommended_vacancies_page(
        self, resume_id: str, params: dict[str, Any]
    ) -> SearchPage:
        page = params["page"]
        assert type(page) is int
        self.recommended.append((resume_id, page))
        return self.pages[page]


def test_search_walks_three_pages_and_stably_deduplicates(repo: AutopilotRepository) -> None:
    ctx = _context(repo)
    transport = FakePages(
        [
            SearchPage([_vacancy("1"), _vacancy("2")], 0, 3, 2, 5),
            SearchPage([_vacancy("2"), _vacancy("3")], 1, 3, 2, 5),
            SearchPage([_vacancy("4")], 2, 3, 2, 5),
        ]
    )

    result = HHSearchProvider(transport, repo).collect(_request(ctx))

    assert transport.requested == [0, 1, 2]
    assert [vacancy.id for vacancy in result.vacancies] == ["1", "2", "3", "4"]
    assert repo.count_search_results(cycle_id=result.cycle_id) == 4
    assert repo.count_items() == 4


def test_duplicate_within_one_page_uses_first_occurrence(
    repo: AutopilotRepository,
) -> None:
    ctx = _context(repo)
    first = _vacancy("same", name="First title")
    second = _vacancy("same", name="Second title")
    result = HHSearchProvider(
        FakePages([SearchPage([first, second], 0, 1, 2, 2)]), repo
    ).collect(_request(ctx))

    assert [(item.id, item.title) for item in result.vacancies] == [
        ("same", "First title")
    ]
    assert repo.count_search_results(cycle_id=result.cycle_id) == 1
    assert repo.count_items() == 1


@pytest.mark.parametrize(
    ("pages", "request_changes", "expected_pages", "expected_ids"),
    [
        (
            [
                SearchPage([_vacancy("1"), _vacancy("2")], 0, 3, 2, 6),
                SearchPage([_vacancy("3"), _vacancy("4")], 1, 3, 2, 6),
            ],
            {"max_pages": 1},
            [0],
            ["1", "2"],
        ),
        (
            [SearchPage([_vacancy("1")], 0, 9, 2, 20)],
            {},
            [0],
            ["1"],
        ),
        (
            [SearchPage([_vacancy("1"), _vacancy("2")], 0, 9, 2, 2)],
            {},
            [0],
            ["1", "2"],
        ),
        (
            [SearchPage([_vacancy("1"), _vacancy("2")], 0, 1, 2, 99)],
            {},
            [0],
            ["1", "2"],
        ),
        (
            [SearchPage([_vacancy("1"), _vacancy("2")], 0, 3, 2, 6)],
            {"remaining_budget": 1},
            [0],
            ["1"],
        ),
    ],
)
def test_stop_rules_are_independent_and_budget_is_a_persistence_limit(
    repo: AutopilotRepository,
    pages: list[SearchPage],
    request_changes: dict[str, Any],
    expected_pages: list[int],
    expected_ids: list[str],
) -> None:
    ctx = _context(repo)
    transport = FakePages(pages)

    result = HHSearchProvider(transport, repo).collect(_request(ctx, **request_changes))

    assert transport.requested == expected_pages
    assert [vacancy.id for vacancy in result.vacancies] == expected_ids
    assert repo.count_search_results(cycle_id=result.cycle_id) == len(expected_ids)
    assert repo.count_items() == len(expected_ids)


def test_zero_budget_performs_no_remote_io(repo: AutopilotRepository) -> None:
    ctx = _context(repo)
    transport = FakePages([])

    result = HHSearchProvider(transport, repo).collect(_request(ctx, remaining_budget=0))

    assert transport.requested == []
    assert result.vacancies == ()
    assert repo.count_items() == 0


def test_recommendations_use_resume_page_adapter_and_control_params(
    repo: AutopilotRepository,
) -> None:
    ctx = _context(repo)
    transport = FakePages([SearchPage([_vacancy("1")], 0, 1, 7, 1)])
    request = _request(
        ctx,
        query_key="recommendations",
        params={"page": 91, "per_page": 99, "order_by": "publication_time"},
        per_page=7,
    )

    HHSearchProvider(transport, repo).collect(request)

    assert transport.recommended == [("r-1", 0)]
    assert transport.requested == []


def test_normalization_sanitizes_and_detaches_upstream_data() -> None:
    raw = _vacancy("1")

    vacancy = normalize_vacancy(raw)
    raw["name"] = "changed"
    raw["employer"]["name"] = "changed"

    assert vacancy.id == "1"
    assert vacancy.title == "Python 1"
    assert vacancy.employer_name == "ACME Ltd"
    assert vacancy.description == "Python & SQL Build services"
    assert "<" not in vacancy.to_dict()["description"]
    assert vacancy.job["title"] == "Python 1"


def test_shadow_search_is_isolated_from_all_live_tables(repo: AutopilotRepository) -> None:
    ctx = _context(repo, trigger="shadow")
    result = HHSearchProvider(
        FakePages([SearchPage([_vacancy("1")], 0, 1, 2, 1)]), repo
    ).collect(_request(ctx))

    assert repo.count_search_results(cycle_id=result.cycle_id) == 1
    for table in (
        "hh_autopilot_items",
        "hh_application_account_guards",
        "hh_application_attempts",
        "hh_autopilot_quota_reservations",
        "hh_autopilot_challenges",
        "hh_autopilot_shadow_results",
    ):
        assert repo.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_stale_lease_blocks_io_and_loss_during_io_blocks_commit(
    repo: AutopilotRepository,
) -> None:
    ctx = _context(repo)
    repo.release_lease(ctx.lease)
    transport = FakePages([SearchPage([_vacancy("1")], 0, 1, 2, 1)])

    with pytest.raises(LostLease):
        HHSearchProvider(transport, repo).collect(_request(ctx))
    assert transport.requested == []

    replacement = _context(repo, owner="replacement")

    class LoseDuringFetch(FakePages):
        def search_vacancies_page(self, params: dict[str, Any]) -> SearchPage:
            value = super().search_vacancies_page(params)
            repo.release_lease(replacement.lease)
            return value

    losing = LoseDuringFetch([SearchPage([_vacancy("2")], 0, 1, 2, 1)])
    with pytest.raises(LostLease):
        HHSearchProvider(losing, repo).collect(_request(replacement))
    assert repo.count_items() == 0


def test_page_metadata_mismatch_fails_without_advancing_checkpoint(
    repo: AutopilotRepository,
) -> None:
    ctx = _context(repo)
    transport = FakePages([SearchPage([_vacancy("1")], 1, 2, 2, 3)])

    with pytest.raises(ValueError, match="page"):
        HHSearchProvider(transport, repo).collect(_request(ctx))

    cycle = repo.get_owned_search_cycle(ctx.run.id)
    assert cycle is not None
    checkpoint = repo.get_checkpoint(cycle.id, "r-1", "preset:python")
    assert checkpoint is not None
    assert checkpoint.next_page == 0
    assert repo.count_items() == 0


def test_page_transaction_rolls_back_results_items_and_checkpoint(
    repo: AutopilotRepository,
) -> None:
    ctx = _context(repo)
    repo.conn.execute(
        """
        CREATE TRIGGER abort_search_item BEFORE INSERT ON hh_autopilot_items
        BEGIN SELECT RAISE(ABORT, 'abort search item'); END
        """
    )
    repo.conn.commit()

    with pytest.raises(Exception, match="abort search item"):
        HHSearchProvider(
            FakePages([SearchPage([_vacancy("1")], 0, 1, 2, 1)]), repo
        ).collect(_request(ctx))

    cycle = repo.get_owned_search_cycle(ctx.run.id)
    assert cycle is not None
    checkpoint = repo.get_checkpoint(cycle.id, "r-1", "preset:python")
    assert checkpoint is not None
    assert checkpoint.next_page == 0
    assert repo.count_search_results(cycle_id=cycle.id) == 0
    assert repo.count_items() == 0


def test_stale_checkpoint_cas_cannot_replay_a_page(repo: AutopilotRepository) -> None:
    ctx = _context(repo)
    cycle = repo.create_search_cycle(
        account_id="default",
        run_id=ctx.run.id,
        policy_hash="hash",
        fencing_token=ctx.lease.fencing_token,
    )
    checkpoint = repo.ensure_search_checkpoint(cycle.id, "r-1", "preset:python")
    page = SearchPage([_vacancy("1")], 0, 1, 2, 1)
    normalized = [normalize_vacancy(_vacancy("1"))]
    repo.commit_search_page(
        checkpoint.id,
        expected_next_page=0,
        page=page,
        normalized=normalized,
        fencing_token=ctx.lease.fencing_token,
        mode="live",
        owner_run_id=ctx.run.id,
        expected_claim_version=cycle.claim_version,
        policy_hash="hash",
    )

    with pytest.raises(StaleWrite):
        repo.commit_search_page(
            checkpoint.id,
            expected_next_page=0,
            page=page,
            normalized=normalized,
            fencing_token=ctx.lease.fencing_token,
            mode="live",
            owner_run_id=ctx.run.id,
            expected_claim_version=cycle.claim_version,
            policy_hash="hash",
        )

    assert repo.count_search_results(cycle_id=cycle.id) == 1
    assert repo.count_items() == 1


def test_interrupted_cycle_claim_resumes_at_first_uncommitted_page(
    repo: AutopilotRepository,
) -> None:
    first = _context(repo)
    cycle = repo.create_search_cycle(
        "default", first.run.id, "hash", first.lease.fencing_token
    )
    checkpoint = repo.ensure_search_checkpoint(cycle.id, "r-1", "preset:python")
    repo.commit_search_page(
        checkpoint.id,
        expected_next_page=0,
        page=SearchPage([_vacancy("1")], 0, 2, 1, 2),
        normalized=[normalize_vacancy(_vacancy("1"))],
        fencing_token=first.lease.fencing_token,
        mode="live",
        owner_run_id=first.run.id,
        expected_claim_version=0,
        policy_hash="hash",
    )
    repo.interrupt_search_cycle(cycle.id, first.lease.fencing_token)
    recovery = _recovery_context(repo, first)

    claimed = repo.claim_search_cycle(
        cycle.id,
        expected_claim_version=0,
        new_run_id=recovery.run.id,
        policy_hash="hash",
        fencing_token=recovery.lease.fencing_token,
    )

    assert claimed is not None
    assert claimed.claim_version == 1
    assert repo.get_checkpoint(cycle.id, "r-1", "preset:python").next_page == 1
    pages = FakePages(
        [
            SearchPage([], 0, 2, 1, 2),
            SearchPage([_vacancy("2")], 1, 2, 1, 2),
        ]
    )
    result = HHSearchProvider(pages, repo).collect(
        _request(recovery, per_page=1, max_pages=2)
    )
    assert pages.requested == [1]
    assert [vacancy.id for vacancy in result.vacancies] == ["2"]
    assert repo.count_items() == 2


def test_claim_requires_exact_recovery_authorization_and_claim_cas(
    repo: AutopilotRepository,
) -> None:
    first = _context(repo)
    cycle = repo.create_search_cycle(
        "default", first.run.id, "hash", first.lease.fencing_token
    )
    repo.interrupt_search_cycle(cycle.id, first.lease.fencing_token)
    repo.release_lease(first.lease)
    lease = repo.acquire_lease("default", "replacement", ttl_seconds=3600)
    assert lease is not None
    grant = repo.active_grant("default")
    assert grant is not None
    manual = repo.create_run(
        "default",
        trigger="manual",
        policy_hash="hash",
        grant_id=grant.id,
        fencing_token=lease.fencing_token,
    )
    with pytest.raises(RepositoryAuthorizationDenied):
        repo.claim_search_cycle(
            cycle.id,
            expected_claim_version=0,
            new_run_id=manual.id,
            policy_hash="hash",
            fencing_token=lease.fencing_token,
        )
    recovery = repo.create_run(
        "default",
        trigger="recovery",
        policy_hash="hash",
        grant_id=grant.id,
        fencing_token=lease.fencing_token,
    )
    claimed = repo.claim_search_cycle(
        cycle.id,
        expected_claim_version=0,
        new_run_id=recovery.id,
        policy_hash="hash",
        fencing_token=lease.fencing_token,
    )
    assert claimed is not None
    with pytest.raises(StaleWrite):
        repo.claim_search_cycle(
            cycle.id,
            expected_claim_version=0,
            new_run_id=recovery.id,
            policy_hash="hash",
            fencing_token=lease.fencing_token,
        )


def test_policy_mismatch_supersedes_and_resets_only_never_dispatched_items(
    repo: AutopilotRepository,
) -> None:
    first = _context(repo, policy_hash="old")
    cycle = repo.create_search_cycle(
        "default", first.run.id, "old", first.lease.fencing_token
    )
    checkpoint = repo.ensure_search_checkpoint(cycle.id, "r-1", "preset:python")
    repo.commit_search_page(
        checkpoint.id,
        expected_next_page=0,
        page=SearchPage([_vacancy("reset"), _vacancy("attempted")], 0, 2, 2, 4),
        normalized=[
            normalize_vacancy(_vacancy("reset")),
            normalize_vacancy(_vacancy("attempted")),
        ],
        fencing_token=first.lease.fencing_token,
        mode="live",
        owner_run_id=first.run.id,
        expected_claim_version=0,
        policy_hash="old",
    )
    rows = repo.conn.execute(
        "SELECT id, vacancy_id FROM hh_autopilot_items ORDER BY id"
    ).fetchall()
    ids = {row["vacancy_id"]: row["id"] for row in rows}
    repo.conn.execute(
        """
        UPDATE hh_autopilot_items SET state = 'ranked', filter_json = '{"pass":true}',
            deterministic_score = 91, ai_json = '{"ok":true}' WHERE id = ?
        """,
        (ids["reset"],),
    )
    repo.conn.execute(
        """
        UPDATE hh_autopilot_items SET state = 'applying', application_attempt_count = 1,
            filter_json = '{"keep":true}', deterministic_score = 88 WHERE id = ?
        """,
        (ids["attempted"],),
    )
    repo.conn.commit()
    other_run = repo.create_run(
        "default",
        trigger="manual",
        policy_hash="old",
        grant_id=first.run.grant_id,
        fencing_token=first.lease.fencing_token,
    )
    unrelated = repo.create_item(other_run.id, "default", "other", "r-1", "preset")
    repo.conn.execute(
        "UPDATE hh_autopilot_items SET state = 'ranked', deterministic_score = 77 WHERE id = ?",
        (unrelated.id,),
    )
    repo.conn.commit()
    repo.interrupt_search_cycle(cycle.id, first.lease.fencing_token)
    repo.create_grants([("default", "new", "operator", "policy-change")])
    recovery = _recovery_context(repo, first, policy_hash="new")

    claimed = repo.claim_search_cycle(
        cycle.id,
        expected_claim_version=0,
        new_run_id=recovery.run.id,
        policy_hash="new",
        fencing_token=recovery.lease.fencing_token,
    )

    assert claimed is None
    assert repo.get_search_cycle(cycle.id).status == "superseded"
    reset = repo.conn.execute(
        "SELECT * FROM hh_autopilot_items WHERE id = ?", (ids["reset"],)
    ).fetchone()
    attempted = repo.conn.execute(
        "SELECT * FROM hh_autopilot_items WHERE id = ?", (ids["attempted"],)
    ).fetchone()
    other = repo.conn.execute(
        "SELECT * FROM hh_autopilot_items WHERE id = ?", (unrelated.id,)
    ).fetchone()
    assert (reset["state"], reset["filter_json"], reset["deterministic_score"]) == (
        "discovered",
        "{}",
        None,
    )
    assert (attempted["state"], attempted["filter_json"]) == (
        "applying",
        '{"keep":true}',
    )
    assert (other["state"], other["deterministic_score"]) == ("ranked", 77)
    assert repo.list_events(ids["reset"])[-1]["reason_code"] == "search_policy_superseded"


def test_duplicate_references_across_presets_and_resumes_share_live_items(
    repo: AutopilotRepository,
) -> None:
    ctx = _context(repo)
    provider = HHSearchProvider(
        FakePages([SearchPage([_vacancy("same")], 0, 1, 1, 1)]), repo
    )
    provider.collect(_request(ctx, per_page=1, query_key="preset:a"))
    HHSearchProvider(
        FakePages([SearchPage([_vacancy("same")], 0, 1, 1, 1)]), repo
    ).collect(_request(ctx, per_page=1, query_key="preset:b"))
    HHSearchProvider(
        FakePages([SearchPage([_vacancy("same")], 0, 1, 1, 1)]), repo
    ).collect(_request(ctx, per_page=1, query_key="preset:a", resume_id="r-2"))

    cycle = repo.get_owned_search_cycle(ctx.run.id)
    assert cycle is not None
    assert repo.count_search_results(cycle_id=cycle.id) == 3
    assert repo.count_items() == 2
    rows = repo.list_search_results(cycle_id=cycle.id)
    assert [(row.resume_id, row.query_key) for row in rows] == [
        ("r-1", "preset:a"),
        ("r-1", "preset:b"),
        ("r-2", "preset:a"),
    ]


def test_shadow_result_is_separate_exact_and_rejects_contradictory_replay(
    repo: AutopilotRepository,
) -> None:
    ctx = _context(repo, trigger="shadow")
    first = repo.save_shadow_result(
        ctx.run.id,
        "default",
        "v-1",
        "r-1",
        filter_data={"eligible": True},
        deterministic_score=90,
        ai_data={"decision": "apply"},
        would_apply=True,
        fencing_token=ctx.lease.fencing_token,
    )
    replay = repo.save_shadow_result(
        ctx.run.id,
        "default",
        "v-1",
        "r-1",
        filter_data={"eligible": True},
        deterministic_score=90,
        ai_data={"decision": "apply"},
        would_apply=True,
        fencing_token=ctx.lease.fencing_token,
    )
    assert replay == first
    assert repo.count_shadow_results(ctx.run.id) == 1
    with pytest.raises(StaleWrite):
        repo.save_shadow_result(
            ctx.run.id,
            "default",
            "v-1",
            "r-1",
            filter_data={"eligible": False},
            deterministic_score=90,
            would_apply=True,
            fencing_token=ctx.lease.fencing_token,
        )


def test_two_connections_converge_on_one_cycle_and_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "race.db"
    storage_a = Storage(path)
    storage_b = Storage(path)
    repo_a = AutopilotRepository(storage_a)
    repo_b = AutopilotRepository(storage_b)
    try:
        ctx = _context(repo_a)
        first = repo_a.create_search_cycle(
            "default", ctx.run.id, "hash", ctx.lease.fencing_token
        )
        second = repo_b.create_search_cycle(
            "default", ctx.run.id, "hash", ctx.lease.fencing_token
        )
        checkpoint_a = repo_a.ensure_search_checkpoint(first.id, "r-1", "preset")
        checkpoint_b = repo_b.ensure_search_checkpoint(second.id, "r-1", "preset")
        assert first.id == second.id
        assert checkpoint_a.id == checkpoint_b.id
        assert repo_a.count_search_cycles() == 1
        assert len(repo_a.list_search_checkpoints(first.id)) == 1
    finally:
        storage_b.close()
        storage_a.close()


@pytest.mark.parametrize(
    ("table", "column", "value", "reader"),
    [
        (
            "hh_autopilot_search_cycles",
            "claim_version",
            1.5,
            lambda repo, cycle, checkpoint: repo.get_search_cycle(cycle.id),
        ),
        (
            "hh_autopilot_search_checkpoints",
            "next_page",
            1.5,
            lambda repo, cycle, checkpoint: repo.get_checkpoint(
                cycle.id, "r-1", "preset"
            ),
        ),
    ],
)
def test_malformed_persisted_search_integers_fail_closed(
    repo: AutopilotRepository,
    table: str,
    column: str,
    value: float,
    reader: Any,
) -> None:
    ctx = _context(repo)
    cycle = repo.create_search_cycle(
        "default", ctx.run.id, "hash", ctx.lease.fencing_token
    )
    checkpoint = repo.ensure_search_checkpoint(cycle.id, "r-1", "preset")
    target = cycle.id if table.endswith("cycles") else checkpoint.id
    repo.conn.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (value, target))
    repo.conn.commit()
    with pytest.raises(StaleWrite):
        reader(repo, cycle, checkpoint)


def test_malformed_run_and_grant_provenance_fails_closed(
    repo: AutopilotRepository,
) -> None:
    ctx = _context(repo)
    repo.conn.execute(
        "UPDATE hh_autopilot_runs SET grant_id = 1.5 WHERE id = ?", (ctx.run.id,)
    )
    repo.conn.commit()
    with pytest.raises(StaleWrite):
        repo.create_search_cycle(
            "default", ctx.run.id, "hash", ctx.lease.fencing_token
        )


def test_malformed_active_grant_provenance_fails_closed(
    repo: AutopilotRepository,
) -> None:
    ctx = _context(repo)
    repo.conn.execute(
        "UPDATE hh_autopilot_grants SET generation = 1.5 WHERE id = ?",
        (ctx.run.grant_id,),
    )
    repo.conn.commit()
    with pytest.raises(StaleWrite):
        repo.create_search_cycle(
            "default", ctx.run.id, "hash", ctx.lease.fencing_token
        )


def test_lease_keeper_is_checked_before_every_remote_page(
    repo: AutopilotRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _context(repo)
    keeper = LeaseKeeper(
        repo,
        ctx.lease,
        ttl_seconds=3600,
        renewal_margin_seconds=0,
    )
    calls = 0
    original = keeper.ensure_current

    def counted(now=None):
        nonlocal calls
        calls += 1
        return original(now)

    monkeypatch.setattr(keeper, "ensure_current", counted)
    transport = FakePages(
        [
            SearchPage([_vacancy("1")], 0, 2, 1, 2),
            SearchPage([_vacancy("2")], 1, 2, 1, 2),
        ]
    )
    HHSearchProvider(transport, repo, keeper).collect(_request(ctx, per_page=1))
    assert calls == 2


def test_mutating_callers_after_page_creation_cannot_change_persisted_result(
    repo: AutopilotRepository,
) -> None:
    ctx = _context(repo)
    raw = _vacancy("1")
    page = SearchPage([raw], 0, 1, 1, 1)
    raw["name"] = "malicious <script>"
    raw["snippet"]["requirement"] = "token=secret"
    result = HHSearchProvider(FakePages([page]), repo).collect(
        _request(ctx, per_page=1)
    )
    result.vacancies[0].job["title"] = "caller mutation"
    stored = repo.list_search_results(cycle_id=result.cycle_id)[0].normalized
    assert stored["title"] == "Python 1"
    assert stored["job"]["title"] == "Python 1"
    assert "secret" not in str(stored)


@pytest.mark.parametrize(
    "factory",
    [
        lambda ctx: SearchRequest(
            "default", True, "r", "q", {}, 1, 1, 1, "hash", 1, "live"
        ),
        lambda ctx: SearchRequest(
            "default", ctx.run.id, "r", "q", {}, 0, 1, 1, "hash", 1, "live"
        ),
        lambda ctx: SearchRequest(
            "default", ctx.run.id, "r", "q", {}, 1, 1, 1, "hash", 1, "LIVE"
        ),
    ],
)
def test_public_validation_rejects_bool_bounds_and_non_exact_mode_before_begin(
    repo: AutopilotRepository, factory: Any
) -> None:
    ctx = _context(repo)
    with pytest.raises((TypeError, ValueError)):
        factory(ctx)
    assert repo.conn.in_transaction is False
