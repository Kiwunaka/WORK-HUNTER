from types import SimpleNamespace

import pytest

from work_hunter.services import WorkHunter
from work_hunter.hh_autopilot.types import RunReport


def setup_supervisor(tmp_path, monkeypatch, error):
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"]["autopilot"]["limits"]["per_run_success"] = 5
    trace, requests, sleeps = [], [], []
    repo = SimpleNamespace(
        pause_active=lambda _: False, kill_switch_active=lambda _: False,
        get_lease=lambda _: None, run_stop_requested=lambda _: False,
        get_run=lambda _: SimpleNamespace(error=error),
    )
    def recover(**kwargs):
        trace.append("recover")
        return SimpleNamespace(busy=0, failed=0)
    def run(request):
        trace.append("run")
        requests.append(request)
        return RunReport("default", "schedule", "failed" if len(requests) == 1 else "completed",
                         run_id=len(requests), applied=2 if len(requests) == 1 else 3)
    app._hh_autopilot_components = SimpleNamespace(repository=repo,
        recovery=SimpleNamespace(run=recover), engine=SimpleNamespace(run=run))
    monkeypatch.setattr("work_hunter.services.time.sleep", sleeps.append)
    return app, repo, trace, requests, sleeps


def test_transient_restart_recovers_first_and_preserves_remaining_budget(tmp_path, monkeypatch):
    app, repo, trace, requests, sleeps = setup_supervisor(tmp_path, monkeypatch, "HHNetworkError: network_error")
    result = app._run_hh_with_restarts("default")
    assert result["status"] == "completed"
    assert trace == ["recover", "run", "recover", "run"]
    assert [request.success_limit for request in requests] == [5, 3]
    assert sum(sleeps) == 30


@pytest.mark.parametrize("error", ["hh_captcha_required", "hh_auth_required", "hh_access_forbidden", "policy_hash_mismatch"])
def test_access_and_policy_failures_do_not_restart(tmp_path, monkeypatch, error):
    app, repo, trace, requests, sleeps = setup_supervisor(tmp_path, monkeypatch, error)
    assert app._run_hh_with_restarts("default")["status"] == "failed"
    assert len(requests) == 1
    assert sleeps == []


def test_pause_during_backoff_prevents_restart(tmp_path, monkeypatch):
    app, repo, trace, requests, sleeps = setup_supervisor(tmp_path, monkeypatch, "hh_rate_limited")
    repo.pause_active = lambda _: bool(sleeps)
    assert app._run_hh_with_restarts("default")["status"] == "interrupted"
    assert len(requests) == 1


def test_success_limit_can_only_reduce_engine_cap():
    # Exercise the existing actual engine fixture rather than repeat its logic.
    from dataclasses import replace
    from tests.test_hh_autopilot_engine import make_case, vacancy
    case = make_case([vacancy("one"), vacancy("two")])
    report = case.engine.run(replace(case.live_request(), success_limit=1))
    assert report.applied == 1


def test_campaign_budget_deduplicates_pending_external_and_manual(tmp_path):
    import json
    app = WorkHunter(tmp_path)
    knowledge = tmp_path / ".work-hunter/knowledge"
    knowledge.mkdir(parents=True)
    app.config["sources"]["hh"]["campaign_ledger_path"] = ".work-hunter/knowledge/campaign.json"
    (knowledge / "campaign.json").write_text(json.dumps({
        "account": "default", "baseline_reservation_id": 80, "target_new_applications": 500,
        "additional_budget_reserved_external_ids": [81, 85, 85],
        "non_countable_hh_reservation_ids": [81],
        "additional_non_hh_new_sends": [
            {"source": "habr", "source_id": "a"}, {"source": "habr", "source_id": "a"},
        ],
    }), encoding="utf-8")
    rows = [{"id": 81, "source": "dispatch", "state": "consumed"},
            {"id": 82, "source": "dispatch", "state": "held"},
            {"id": 83, "source": "dispatch", "state": "reserved"},
            {"id": 84, "source": "dispatch", "state": "released"}]
    app._storage = SimpleNamespace(conn=SimpleNamespace(execute=lambda *a: SimpleNamespace(fetchall=lambda: rows)))
    assert app._hh_campaign_remaining("default", 500) == 496
