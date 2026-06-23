from __future__ import annotations

import pytest

from work_hunter.external_sessions import ExternalHTTPResponse, import_external_session_from_har
from work_hunter.models import Job, JobScore
from work_hunter.models import Resume
from work_hunter.campaigns.policy import campaign_policy_gate
from work_hunter.campaigns.presets import CAMPAIGN_STAGES, PER_VACANCY_STATES, main_python_backend_preset
from work_hunter.services import WorkHunter


EXTERNAL_APPLY_SOURCES = ["geekjob", "habr", "getmatch", "hirehi", "careerspace", "jabka"]
EXTERNAL_APPLY_HOSTS = {
    "geekjob": "geekjob.ru",
    "habr": "career.habr.com",
    "getmatch": "getmatch.ru",
    "hirehi": "hirehi.ru",
    "careerspace": "careerspace.app",
    "jabka": "jabka.work",
}


class FakeHHCampaignClient:
    apply_calls: list[tuple[str, str, str]] = []

    def __init__(self, config):
        self.config = config

    def has_token(self):
        return True

    def get_vacancy(self, vacancy_id: str):
        return {"id": vacancy_id, "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}"}

    def suitable_resumes(self, vacancy_id: str):
        return [{"id": "resume-1", "title": "Backend"}]

    def list_resumes(self):
        return [{"id": "resume-1", "title": "Backend"}]

    def apply(self, vacancy_id: str, resume_id: str, message: str):
        self.apply_calls.append((vacancy_id, resume_id, message))
        return {"status": "created", "status_code": 201}


def _hh_job(app: WorkHunter, source_id: str, score: int = 95) -> int:
    job_id = app.storage.upsert_job(
        Job(
            source="hh",
            source_id=source_id,
            url=f"https://hh.ru/vacancy/{source_id}",
            title=f"Python {source_id}",
            company="Acme",
        )
    )
    app.storage.save_score(JobScore(job_id=job_id, total_score=score))
    return job_id


def _external_job(app: WorkHunter, source: str = "hirehi", score: int = 95) -> int:
    job_id = app.storage.upsert_job(
        Job(
            source=source,
            source_id="ext-1",
            url=f"https://{source}.example/jobs/ext-1",
            title=f"Python {source}",
            company="Acme",
            description="FastAPI PostgreSQL",
        )
    )
    app.storage.save_score(JobScore(job_id=job_id, total_score=score))
    return job_id


def _active_resume(app: WorkHunter) -> int:
    return app.storage.save_resume(
        Resume(
            name="Base",
            body="Python backend resume.",
            profile_id="default",
            is_active=True,
            canonical={"title": "Python Backend", "skills": ["python"], "experience": []},
        )
    )


def _import_external_session(app: WorkHunter, source: str = "hirehi") -> None:
    host = EXTERNAL_APPLY_HOSTS[source]
    har_path = app.root / f"{source}.har"
    har_path.write_text(
        (
            '{"log":{"entries":[{"request":{"url":"https://'
            f'{host}/api/applications'
            '","headers":[{"name":"Cookie","value":"sid=secret"}]}}]}}'
        ),
        encoding="utf-8",
    )
    import_external_session_from_har(app.root, source, har_path, allowed_hosts={host})


def _import_hirehi_session(app: WorkHunter) -> None:
    _import_external_session(app, "hirehi")


def _external_apply_evidence(source: str = "hirehi") -> dict[str, object]:
    return {
        "tests": {"status": "passed", "command": f"python -m pytest tests/test_{source}_apply.py"},
        "replay": {"status": "passed", "run_id": 1},
        "redaction": {"status": "passed", "scan_id": f"{source}-redaction"},
        "dry_run": {"status": "dry_run_ready", "event_id": 2},
    }


def _certify_external_l6(app: WorkHunter, source: str = "hirehi") -> None:
    host = EXTERNAL_APPLY_HOSTS[source]
    app.config["sources"][source]["external_apply"] = {
        "certified": True,
        "level": 6,
        "session": source,
        "method": "POST",
        "url": f"https://{host}/api/applications",
        "evidence": _external_apply_evidence(source),
        "payload_template": {
            "jobId": "{source_id}",
            "coverLetter": "{cover_letter}",
            "resume": "{resume_body}",
        },
    }
    app.save_config(app.config)


def _certify_hirehi_l6(app: WorkHunter) -> None:
    _certify_external_l6(app, "hirehi")


def test_campaign_package_exposes_default_preset_stages_states_and_policy_gate():
    preset = main_python_backend_preset()

    assert preset["name"] == "main-python-backend"
    assert preset["enabled"] is False
    assert preset["real_apply"] is False
    assert preset["sources"] == ["hh", "geekjob", "habr", "getmatch", "hirehi", "careerspace", "jabka"]
    assert preset["min_score"] == 75
    assert preset["daily_cap"] == 25
    assert preset["per_company_cap"] == 1
    assert preset["skip_tests"] is True
    assert preset["time_window"] == {"enabled": True, "from": "09:00", "to": "21:00"}
    assert CAMPAIGN_STAGES == ["draft", "planned", "reviewed", "enabled", "running", "paused", "completed", "cancelled", "failed"]
    assert "ready_to_apply" in PER_VACANCY_STATES
    assert "manual_review" in PER_VACANCY_STATES

    blocked = campaign_policy_gate(
        preset={**preset, "enabled": True, "real_apply": True},
        source_level=4,
        job_score=90,
        item={"has_test": False, "unknown_form": False, "captcha": False, "duplicate_employer": False},
        counters={"daily": 0, "source": 0, "company": 0},
        kill_switch_paused=False,
        resume_variant_valid=True,
        cover_letter_generated=True,
        payload_preview_saved=True,
        session_valid=True,
        audit_initialized=True,
    )
    allowed = campaign_policy_gate(
        preset={**preset, "enabled": True, "real_apply": True},
        source_level=6,
        job_score=90,
        item={"has_test": False, "unknown_form": False, "captcha": False, "duplicate_employer": False},
        counters={"daily": 0, "source": 0, "company": 0},
        kill_switch_paused=False,
        resume_variant_valid=True,
        cover_letter_generated=True,
        payload_preview_saved=True,
        session_valid=True,
        audit_initialized=True,
    )

    assert blocked == {"can_apply": False, "reasons": ["source_maturity_below_l5"]}
    assert allowed == {"can_apply": True, "reasons": []}


@pytest.mark.parametrize(
    ("override", "expected_reason"),
    [
        ({"preset": {"enabled": False}}, "campaign_disabled"),
        ({"preset": {"real_apply": False}}, "real_apply_disabled"),
        ({"job_score": 50}, "below_min_score"),
        ({"item": {"has_test": True}}, "blocked_test"),
        ({"item": {"unknown_form": True}}, "blocked_unknown_form"),
        ({"item": {"captcha": True}}, "blocked_captcha_or_challenge"),
        ({"item": {"challenge": True}}, "blocked_captcha_or_challenge"),
        ({"item": {"blacklisted": True}}, "blacklisted"),
        ({"item": {"duplicate_employer": True}}, "duplicate_employer"),
        ({"counters": {"daily": 25}}, "daily_cap_reached"),
        ({"counters": {"source": 10}}, "source_cap_reached"),
        ({"counters": {"company": 1}}, "company_cap_reached"),
        ({"resume_variant_valid": False}, "resume_variant_invalid"),
        ({"cover_letter_generated": False}, "cover_letter_missing"),
        ({"payload_preview_saved": False}, "payload_preview_missing"),
        ({"session_valid": False}, "session_invalid"),
        ({"audit_initialized": False}, "audit_missing"),
        ({"kill_switch_paused": True}, "kill_switch_paused"),
    ],
)
def test_campaign_policy_gate_blocks_each_guardrail(override, expected_reason):
    preset = {
        **main_python_backend_preset(),
        "enabled": True,
        "real_apply": True,
        "per_source_cap": {"hh": 10},
    }
    item = {
        "source": "hh",
        "has_test": False,
        "unknown_form": False,
        "captcha": False,
        "challenge": False,
        "blacklisted": False,
        "duplicate_employer": False,
    }
    counters = {"daily": 0, "source": 0, "company": 0}
    kwargs = {
        "preset": {**preset, **override.get("preset", {})},
        "source_level": 6,
        "job_score": override.get("job_score", 90),
        "item": {**item, **override.get("item", {})},
        "counters": {**counters, **override.get("counters", {})},
        "kill_switch_paused": override.get("kill_switch_paused", False),
        "resume_variant_valid": override.get("resume_variant_valid", True),
        "cover_letter_generated": override.get("cover_letter_generated", True),
        "payload_preview_saved": override.get("payload_preview_saved", True),
        "session_valid": override.get("session_valid", True),
        "audit_initialized": override.get("audit_initialized", True),
    }

    result = campaign_policy_gate(**kwargs)

    assert result["can_apply"] is False
    assert expected_reason in result["reasons"]


def test_hh_campaign_kill_switch_blocks_planning_before_run_creation(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHCampaignClient)
    app = WorkHunter(root=tmp_path)
    app.pause_hh_agent(reason="operator_stop")
    _hh_job(app, "blocked")

    result = app.plan_hh_campaign(limit=10, min_score=70, daily_cap=3)

    assert result["status"] == "blocked"
    assert result["reason"] == "campaign_kill_switch_paused"
    assert app.storage.list_hh_campaign_runs() == []
    events = app.storage.list_replay_events(source="hh", event_type="campaign_blocked")
    assert events[0]["data"]["reason"] == "campaign_kill_switch_paused"


def test_hh_campaign_daily_cap_limits_ready_items_and_marks_overflow(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHCampaignClient)
    app = WorkHunter(root=tmp_path)
    for source_id in ["first", "second", "third"]:
        _hh_job(app, source_id)

    run = app.plan_hh_campaign(limit=10, min_score=70, daily_cap=2)

    items = app.storage.list_hh_campaign_items(run["id"])
    ready = [item for item in items if item.status == "ready"]
    skipped = [item for item in items if item.reason == "daily_cap_reached"]
    assert run["filters"]["daily_cap"] == 2
    assert run["counts"] == {"ready": 2, "skipped": 1, "error": 0, "applied": 0}
    assert len(ready) == 2
    assert len(skipped) == 1


def test_hh_campaign_confirm_stops_immediately_when_paused(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHCampaignClient)
    FakeHHCampaignClient.apply_calls = []
    app = WorkHunter(root=tmp_path)
    _hh_job(app, "ready")
    run = app.plan_hh_campaign(limit=10, min_score=70)
    app.pause_hh_agent(reason="operator_stop")

    result = app.confirm_hh_campaign(run["id"], confirm=True)

    items = app.storage.list_hh_campaign_items(run["id"])
    assert result["status"] == "blocked"
    assert result["reason"] == "campaign_kill_switch_paused"
    assert items[0].status == "ready"
    assert FakeHHCampaignClient.apply_calls == []


def test_hh_campaign_records_replay_for_plan_and_apply(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHCampaignClient)
    FakeHHCampaignClient.apply_calls = []
    app = WorkHunter(root=tmp_path)
    _hh_job(app, "ready")

    run = app.plan_hh_campaign(limit=10, min_score=70, daily_cap=1)
    result = app.confirm_hh_campaign(run["id"], confirm=True)

    events = app.replay_for_run(run["id"])["events"]
    event_types = [event["event_type"] for event in events]
    assert result["counts"]["applied"] == 1
    assert "campaign_planned" in event_types
    assert "campaign_apply_result" in event_types


def test_external_campaign_plans_and_runs_certified_l6_source(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = _external_job(app, source="hirehi")
    _active_resume(app)
    _import_hirehi_session(app)
    _certify_hirehi_l6(app)
    calls: list[dict[str, object]] = []

    def fake_requester(method: str, url: str, *, headers: dict[str, str], data: str | None, timeout: int):
        calls.append({"method": method, "url": url, "headers": headers, "data": data})
        return ExternalHTTPResponse(status=201, headers={"content-type": "application/json"}, body='{"id":"app-1"}')

    missing_dry_run = app.plan_external_campaign("hirehi", limit=10, min_score=70, daily_cap=1)
    app.external_apply_dry_run(
        job_id,
        form={
            "form_url": "https://hirehi.example/apply/ext-1",
            "fields": [{"name": "cover_letter", "label": "Cover letter", "required": False}],
            "form_signature": "hirehi_apply_form:v1",
        },
        cover_letter="Hi",
        campaign_policy={"min_score": 70},
    )
    run = app.plan_external_campaign("hirehi", limit=10, min_score=70, daily_cap=1)
    blocked = app.confirm_external_campaign(run["id"], confirm=True, requester=fake_requester)
    app.enable_hh_campaign(run["id"])
    result = app.confirm_external_campaign(run["id"], confirm=True, requester=fake_requester)

    assert missing_dry_run["counts"] == {"ready": 0, "skipped": 1, "error": 0, "applied": 0}
    assert app.storage.list_hh_campaign_items(missing_dry_run["id"])[0].reason == "external_apply_dry_run_missing"
    assert run["filters"]["mode"] == "external_campaign"
    assert run["filters"]["source"] == "hirehi"
    assert run["counts"] == {"ready": 1, "skipped": 0, "error": 0, "applied": 0}
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "real_apply_requires_campaign_policy"
    assert blocked["run_status"] == "planned"
    assert blocked["submit"] is False
    assert result["status"] == "confirmed"
    assert result["counts"]["applied"] == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://hirehi.ru/api/applications"
    assert '"jobId": "ext-1"' in str(calls[0]["data"])
    assert app.storage.list_applications()[0].status == "external_applied"
    event_types = [event["event_type"] for event in app.replay_for_run(run["id"], source="hirehi")["events"]]
    assert "external_campaign_planned" in event_types
    assert "external_campaign_apply_result" in event_types


@pytest.mark.parametrize("source", EXTERNAL_APPLY_SOURCES)
def test_external_campaign_policy_apply_contract_covers_priority_sources(source: str, tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = _external_job(app, source=source)
    _active_resume(app)
    _import_external_session(app, source)
    _certify_external_l6(app, source)
    host = EXTERNAL_APPLY_HOSTS[source]
    calls: list[dict[str, object]] = []

    def fake_requester(method: str, url: str, *, headers: dict[str, str], data: str | None, timeout: int):
        calls.append({"method": method, "url": url, "headers": headers, "data": data})
        return ExternalHTTPResponse(status=201, headers={"content-type": "application/json"}, body='{"id":"app-1"}')

    app.external_apply_dry_run(
        job_id,
        form={
            "form_url": f"https://{host}/apply/ext-1",
            "fields": [{"name": "cover_letter", "label": "Cover letter", "required": False}],
            "form_signature": f"{source}_apply_form:v1",
        },
        cover_letter=f"Hi from Work Hunter for {source}",
        campaign_policy={"min_score": 70},
    )
    run = app.plan_external_campaign(source, limit=10, min_score=70, daily_cap=1)
    app.enable_hh_campaign(run["id"])
    result = app.confirm_external_campaign(run["id"], confirm=True, requester=fake_requester)

    assert run["filters"]["mode"] == "external_campaign"
    assert run["filters"]["source"] == source
    assert run["counts"] == {"ready": 1, "skipped": 0, "error": 0, "applied": 0}
    assert result["status"] == "confirmed"
    assert result["counts"]["applied"] == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == f"https://{host}/api/applications"
    event_types = [event["event_type"] for event in app.replay_for_run(run["id"], source=source)["events"]]
    assert "external_campaign_planned" in event_types
    assert "external_campaign_apply_result" in event_types


def test_external_campaign_rejects_dry_run_from_previous_apply_target(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = _external_job(app, source="hirehi")
    _active_resume(app)
    _import_hirehi_session(app)
    _certify_hirehi_l6(app)

    app.external_apply_dry_run(
        job_id,
        form={
            "form_url": "https://hirehi.example/apply/ext-1",
            "fields": [{"name": "cover_letter", "label": "Cover letter", "required": False}],
            "form_signature": "hirehi_apply_form:v1",
        },
        cover_letter="Hi",
        campaign_policy={"min_score": 70},
    )
    app.config["sources"]["hirehi"]["external_apply"]["url"] = "https://hirehi.ru/api/applications-v2"
    app.save_config(app.config)

    run = app.plan_external_campaign("hirehi", limit=10, min_score=70, daily_cap=1)
    item = app.storage.list_hh_campaign_items(run["id"])[0]

    assert run["counts"] == {"ready": 0, "skipped": 1, "error": 0, "applied": 0}
    assert item.reason == "external_apply_dry_run_target_mismatch"


def test_external_campaign_plan_requires_imported_session(tmp_path):
    app = WorkHunter(root=tmp_path)
    _external_job(app, source="hirehi")
    _active_resume(app)
    _certify_hirehi_l6(app)

    result = app.plan_external_campaign("hirehi", limit=10, min_score=70)

    assert result["status"] == "blocked"
    assert result["reason"] == "external_session_missing"
    assert app.storage.list_hh_campaign_runs() == []


def test_external_campaign_plan_requires_certification_evidence(tmp_path):
    app = WorkHunter(root=tmp_path)
    _external_job(app, source="hirehi")
    _active_resume(app)
    _import_hirehi_session(app)
    app.config["sources"]["hirehi"]["external_apply"] = {
        "certified": True,
        "level": 6,
        "session": "hirehi",
        "method": "POST",
        "url": "https://hirehi.ru/api/applications",
    }
    app.save_config(app.config)

    result = app.plan_external_campaign("hirehi", limit=10, min_score=70)

    assert result["status"] == "blocked"
    assert result["reason"] == "external_source_requires_l6_campaign_certification"
    assert result["readiness"]["level"] == 2
    assert "external_apply_dry_run_missing" in result["readiness"]["blockers"]
    assert app.storage.list_hh_campaign_runs() == []
