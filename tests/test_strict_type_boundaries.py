from __future__ import annotations

import pytest

from work_hunter.services import WorkHunter


def test_hh_web_search_url_rejects_invalid_salary_before_search(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    search_calls: list[dict[str, object]] = []

    def fake_search(**kwargs):
        search_calls.append(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(app, "search_hh_vacancies", fake_search)

    with pytest.raises(ValueError):
        app.hh_web_search_url("https://hh.ru/search/vacancy?salary=not-an-integer")

    assert search_calls == []


def test_hh_web_search_url_preserves_missing_empty_zero_and_numeric_salary(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    salaries: list[int | None] = []

    def fake_search(**kwargs):
        salaries.append(kwargs["salary"])
        return {"status": "ok"}

    monkeypatch.setattr(app, "search_hh_vacancies", fake_search)

    for url in (
        "https://hh.ru/search/vacancy?text=python",
        "https://hh.ru/search/vacancy?salary=",
        "https://hh.ru/search/vacancy?salary=0",
        "https://hh.ru/search/vacancy?salary=150000",
    ):
        assert app.hh_web_search_url(url)["status"] == "ok"

    assert salaries == [None, None, None, 150000]


def test_scan_events_invalid_limit_records_error_without_calling_scanner(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    scan_calls: list[tuple[str, int | None]] = []

    def fake_scan(*, status, limit):
        scan_calls.append((status, limit))
        return {"status": "ok"}

    monkeypatch.setattr(app, "scan_hh_agent_events", fake_scan)

    with pytest.raises(ValueError):
        app.run_hh_agent_operation("scan-events", {"limit": "not-an-integer"})

    assert scan_calls == []
    run = app.storage.list_hh_agent_mcp_runs()[-1]
    assert run.status == "error"
    assert run.error


def test_scan_events_distinguishes_missing_and_numeric_limit(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    scan_calls: list[tuple[str, int | None]] = []

    def fake_scan(*, status, limit):
        scan_calls.append((status, limit))
        return {"status": "ok"}

    monkeypatch.setattr(app, "scan_hh_agent_events", fake_scan)

    app.run_hh_agent_operation("scan-events", {})
    app.run_hh_agent_operation("scan-events", {"limit": "7"})

    assert scan_calls == [("active", None), ("active", 7)]


@pytest.mark.parametrize("bad_count", ["not-an-integer", -1, True, 1.5])
def test_run_strategy_rejects_invalid_result_count_before_downstream_actions(
    monkeypatch,
    tmp_path,
    bad_count,
):
    app = WorkHunter(tmp_path)
    search_calls: list[dict[str, object]] = []
    score_calls: list[bool] = []
    campaign_calls: list[dict[str, object]] = []
    strategy = {
        "name": "strict-count",
        "source": "hh",
        "queries": [{"text": "python"}],
        "daily_limit": 10,
        "min_score": 0,
    }

    monkeypatch.setattr(app, "_strategy_spec", lambda name: strategy)

    def fake_search(**kwargs):
        search_calls.append(kwargs)
        return {"status": "ok", "count": bad_count}

    def fake_score():
        score_calls.append(True)
        return 1

    def fake_campaign(**kwargs):
        campaign_calls.append(kwargs)
        return {"status": "planned"}

    monkeypatch.setattr(app, "search_hh_vacancies", fake_search)
    monkeypatch.setattr(app, "score_jobs", fake_score)
    monkeypatch.setattr(app, "plan_hh_campaign", fake_campaign)

    with pytest.raises(ValueError, match="Search result count must be a non-negative integer"):
        app.run_strategy("strict-count", dry_run=False, confirm=True)

    assert len(search_calls) == 1
    assert score_calls == []
    assert campaign_calls == []


@pytest.mark.parametrize(
    ("search_result", "expected_count", "expected_campaign_limit"),
    [
        ({"status": "ok", "count": "2"}, 2, 2),
        ({"status": "ok"}, 0, 1),
        ({"status": "ok", "count": 0}, 0, 1),
    ],
)
def test_run_strategy_accepts_valid_missing_and_zero_result_count(
    monkeypatch,
    tmp_path,
    search_result,
    expected_count,
    expected_campaign_limit,
):
    app = WorkHunter(tmp_path)
    score_calls: list[bool] = []
    campaign_calls: list[dict[str, object]] = []
    strategy = {
        "name": "strict-count",
        "source": "hh",
        "queries": [{"text": "python"}],
        "daily_limit": 10,
        "min_score": 0,
    }

    monkeypatch.setattr(app, "_strategy_spec", lambda name: strategy)
    monkeypatch.setattr(app, "search_hh_vacancies", lambda **kwargs: dict(search_result))

    def fake_score():
        score_calls.append(True)
        return 3

    def fake_campaign(**kwargs):
        campaign_calls.append(kwargs)
        return {"status": "planned", "id": 12}

    monkeypatch.setattr(app, "score_jobs", fake_score)
    monkeypatch.setattr(app, "plan_hh_campaign", fake_campaign)

    result = app.run_strategy("strict-count", dry_run=False, confirm=True)

    assert result["imported"] == expected_count
    assert score_calls == [True]
    assert campaign_calls[0]["limit"] == expected_campaign_limit
