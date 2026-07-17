from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

from tests.fakes.hh_autopilot_server import FakeHHServer
from tests.test_hh_autopilot_executor import _make_case
from work_hunter.hh_autopilot.reconcile import HHApplicationReconciler
from work_hunter.hh_autopilot.repository import AutopilotRepository
from work_hunter.hh_autopilot.types import AutopilotState
from work_hunter.sources.hh import HHApplyClient
from work_hunter.storage import Storage


UTC = timezone.utc


@pytest.fixture
def fake_hh() -> Iterator[FakeHHServer]:
    with FakeHHServer() as server:
        yield server


def _client(server: FakeHHServer) -> HHApplyClient:
    return HHApplyClient(
        {
            "access_token": "private-access-token",
            "api_base_url": server.base_url,
            "hh_user_agent": "work-hunter-contract-test",
            "negotiation_statuses": ["active"],
            "timeout": 2,
        }
    )


class _ReadOnlyNegotiations:
    def __init__(self, client: HHApplyClient) -> None:
        self._client = client

    def negotiation_snapshots(self, account_id: str):
        return self._client.negotiation_snapshots(account_id)


def test_real_hh_client_creates_application_and_journals_only_safe_fields(
    fake_hh: FakeHHServer,
) -> None:
    fake_hh.scenario("v-created", "created")

    outcome = _client(fake_hh).apply_outcome(
        "v-created",
        "r-private",
        "private cover letter",
        timeout_seconds=2,
    )

    assert outcome.code == "applied"
    assert outcome.status_code == 201
    assert outcome.payload == {"id": "n-1"}
    assert fake_hh.application_post_count == 1
    journal = fake_hh.requests
    assert len(journal) == 1
    assert (journal[0].method, journal[0].path) == ("POST", "/negotiations")
    assert (journal[0].vacancy_id, journal[0].resume_id) == (
        "v-created",
        "r-private",
    )
    assert "private-access-token" not in repr(journal)
    assert "private cover letter" not in repr(journal)
    assert not hasattr(journal, "append")


@pytest.mark.restart
def test_socket_close_after_accept_reconciles_without_a_second_post(
    fake_hh: FakeHHServer,
    tmp_path,
) -> None:
    fake_hh.scenario("v-1", "accepted_then_close")
    case = _make_case(tmp_path)
    client = _client(fake_hh)
    case.executor.transport = client
    try:
        sent = case.executor.execute(
            case.item.id,
            case.authorization,
            case.lease,
            now=case.now,
        )

        assert sent.state is AutopilotState.RECONCILING
        assert sent.attempt_id is not None
        assert fake_hh.application_post_count == 1
        held = case.repo.active_reservation_for_attempt(sent.attempt_id)
        assert held is not None and held.state.value == "held"

        provenance = case.repo.recovery_provenance(sent.attempt_id)
        reconciled = HHApplicationReconciler(
            case.repo,
            _ReadOnlyNegotiations(client),
            settings_provider=lambda: case.settings,
        ).reconcile(
            sent.item_id,
            provenance,
            case.lease,
            now=case.now + timedelta(seconds=60),
        )

        assert reconciled.outcome == "applied"
        consumed = case.repo.get_reservation(held.id)
        assert consumed is not None and consumed.state.value == "consumed"
        assert case.repo.count_applications("default", "v-1", "r-1") == 1
        assert fake_hh.application_post_count == 1
    finally:
        case.storage.close()


@pytest.mark.concurrency
def test_account_lease_allows_one_owner_and_keeps_other_account_independent(
    fake_hh: FakeHHServer,
    tmp_path,
) -> None:
    database_path = tmp_path / "work-hunter.db"
    Storage(database_path).close()
    barrier = Barrier(3)
    now = datetime.now(UTC).replace(microsecond=0)
    client = _client(fake_hh)

    def dispatch(candidate: tuple[str, str]) -> tuple[str, bool]:
        account_id, owner = candidate
        storage = Storage(database_path)
        repository = AutopilotRepository(storage)
        try:
            barrier.wait(timeout=5)
            lease = repository.acquire_lease(
                account_id,
                owner,
                ttl_seconds=60,
                now=now,
            )
            if lease is None:
                return account_id, False
            outcome = client.apply_outcome(
                f"v-{account_id}-{owner}",
                f"r-{owner}",
                f"private message for {owner}",
                timeout_seconds=2,
            )
            return account_id, outcome.code == "applied"
        finally:
            storage.close()

    candidates = (
        ("default", "owner-a"),
        ("default", "owner-b"),
        ("secondary", "owner-c"),
    )
    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = tuple(pool.map(dispatch, candidates))

    assert sum(sent for account, sent in outcomes if account == "default") == 1
    assert sum(sent for account, sent in outcomes if account == "secondary") == 1
    assert fake_hh.application_post_count == 2
