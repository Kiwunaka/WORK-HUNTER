from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.models import Job
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


def _external_apply_evidence() -> dict[str, object]:
    return {
        "tests": {"status": "passed", "command": "python -m pytest tests/test_hirehi_apply.py"},
        "replay": {"status": "passed", "run_id": 42},
        "redaction": {"status": "passed", "scan_id": "redaction-hirehi"},
        "dry_run": {"status": "dry_run_ready", "event_id": 1001},
    }


def _configure_external_apply(app: WorkHunter, source: str = "hirehi") -> None:
    app.config["sources"][source]["external_apply"] = {
        "certified": True,
        "level": 5,
        "session": source,
        "method": "POST",
        "url": f"https://{source}.example/apply",
        "payload_template": {"jobId": "{source_id}"},
    }
    app.save_config(app.config)


def test_source_adapter_registry_covers_priority_sources_and_maturity():
    from work_hunter.sources.registry import source_status_report

    report = source_status_report({"hh": {"enabled": True}, "jabka": {"enabled": True}})

    for source in ["hh", "geekjob", "habr", "getmatch", "hirehi", "careerspace", "jabka"]:
        assert source in report

    assert report["hh"]["adapter_status"] == "implemented"
    assert report["hh"]["adapter_class"].endswith(".HHSource")
    assert report["hh"]["level"] == 6
    assert report["hh"]["readiness_badge"] == "L6 real apply"
    assert report["hh"]["blockers"] == []

    assert report["jabka"]["adapter_status"] == "generic_public_board"
    assert report["jabka"]["adapter_class"].endswith(".PublicJobBoardSource")
    assert report["jabka"]["level"] == 2
    assert "real_apply_maturity_below_l4" in report["jabka"]["blockers"]


def test_source_status_api_exposes_registry_report(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hirehi"]["enabled"] = True
    app.save_config(app.config)

    with _server(tmp_path) as base:
        status = _get_json(base, "/api/source-status")

    assert status["hirehi"]["enabled"] is True
    assert status["hirehi"]["adapter_status"] == "generic_public_board"
    assert status["hirehi"]["can_prepare_apply"] is True
    assert status["hirehi"]["can_real_apply"] is False


def test_source_certification_audit_reports_missing_local_evidence(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_external_apply(app)

    audit = app.source_certification_audit("hirehi", level=5)

    assert audit["source"] == "hirehi"
    assert audit["requested_level"] == 5
    assert audit["ready"] is False
    assert audit["missing"] == ["tests", "replay", "redaction", "dry_run"]
    assert audit["evidence"]["session"]["status"] == "present"
    assert audit["evidence"]["url"]["status"] == "present"
    assert audit["promotion_payload"] is None


def test_source_certification_audit_rejects_synthetic_replay_redaction_and_dry_run_evidence(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_external_apply(app)

    audit = app.source_certification_audit("hirehi", level=5, evidence=_external_apply_evidence())

    assert audit["ready"] is False
    assert audit["missing"] == ["replay", "redaction", "dry_run"]
    assert audit["evidence"]["tests"]["status"] == "passed"
    assert audit["evidence"]["replay"]["status"] == "missing"
    assert audit["evidence"]["redaction"]["status"] == "missing"
    assert audit["evidence"]["dry_run"]["status"] == "missing"
    assert audit["promotion_payload"] is None


def test_source_certification_audit_builds_promotion_payload_from_replay_evidence(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_external_apply(app)
    dry_run_id = app.record_replay_event(
        source="hirehi",
        event_type="external_apply_dry_run",
        title="External dry run",
        data={"status": "dry_run_ready", "submit": False, "screenshots": {"before": "before.png"}},
    )
    redaction_id = app.record_replay_event(
        source="hirehi",
        event_type="redaction_scan",
        title="Redaction scan",
        data={"status": "ok", "changed": True},
    )

    audit = app.source_certification_audit(
        "hirehi",
        level=5,
        evidence={"tests": {"status": "passed", "command": "python -m pytest tests/test_hirehi_apply.py"}},
    )

    assert audit["ready"] is True
    assert audit["missing"] == []
    assert audit["evidence"]["dry_run"]["event_id"] == dry_run_id
    assert audit["evidence"]["replay"]["event_id"] == dry_run_id
    assert audit["evidence"]["redaction"]["event_id"] == redaction_id
    assert audit["promotion_payload"]["external_apply"]["level"] == 5
    assert audit["promotion_payload"]["external_apply"]["evidence"]["tests"]["status"] == "passed"
    assert audit["promotion_payload"]["external_apply"]["evidence"]["dry_run"]["status"] == "dry_run_ready"


def test_source_certification_audit_binds_dry_run_host_and_form_signature(monkeypatch, tmp_path):
    html = """
    <main>
      <form action="/responses/gj1" method="post">
        <input type="text" name="full_name" required>
        <input type="email" name="email" required>
        <textarea name="cover_letter" required></textarea>
      </form>
    </main>
    """

    def fake_fetch(url: str, **kwargs):
        assert url == "https://geekjob.ru/vacancy/gj1"
        return html

    monkeypatch.setattr("work_hunter.sources.geekjob.fetch_url", fake_fetch)
    app = WorkHunter(root=tmp_path)
    app.config["profiles"]["default"].update({"name": "Test Candidate", "email": "me@example.test"})
    app.save_config(app.config)
    app.configure_source_external_apply_target(
        "geekjob",
        session="geekjob",
        url="https://geekjob.ru/responses/{source_id}",
        method="post",
        payload_template={"jobId": "{source_id}", "coverLetter": "{cover_letter}"},
        level=5,
    )
    job_id = app.storage.upsert_job(
        Job(
            source="geekjob",
            source_id="gj1",
            url="https://geekjob.ru/vacancy/gj1",
            title="Backend Python Developer",
            company="Acme",
        )
    )
    dry_run = app.external_apply_dry_run(job_id, form={}, cover_letter="Hi")
    app.record_source_redaction_scan("geekjob", payload={"headers": {"Authorization": "Bearer secret"}}, level=5)

    audit = app.source_certification_audit(
        "geekjob",
        level=5,
        evidence={"tests": {"status": "passed", "command": "pytest geekjob"}},
    )

    assert dry_run["status"] == "dry_run_ready"
    assert audit["ready"] is True
    assert audit["evidence"]["dry_run"]["host"] == "geekjob.ru"
    assert audit["evidence"]["dry_run"]["form_signature"] == "geekjob_apply_form:v1"
    assert audit["promotion_payload"]["external_apply"]["certification_context"]["target_host"] == "geekjob.ru"
    assert audit["promotion_payload"]["external_apply"]["certification_context"]["form_signature"] == "geekjob_apply_form:v1"


def test_source_certification_audit_api_returns_packaged_evidence(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_external_apply(app)
    app.record_replay_event(
        source="hirehi",
        event_type="external_apply_dry_run",
        data={"status": "dry_run_ready", "submit": False},
    )
    app.record_replay_event(
        source="hirehi",
        event_type="redaction_scan",
        data={"status": "passed"},
    )

    with _server(tmp_path) as base:
        audit = _post_json(
            base,
            "/api/sources/hirehi/certification-audit",
            {"level": 5, "evidence": {"tests": {"status": "passed", "command": "pytest hirehi"}}},
        )

    assert audit["ready"] is True
    assert audit["promotion_payload"]["source"] == "hirehi"
    assert audit["promotion_payload"]["external_apply"]["evidence"]["tests"]["command"] == "pytest hirehi"


def test_record_source_certification_evidence_persists_without_promoting(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_external_apply(app)

    result = app.record_source_certification_evidence(
        "hirehi",
        evidence={
            "tests": {"status": "passed", "command": "pytest hirehi Authorization: Bearer secret"},
            "ignored": {"status": "passed"},
        },
        level=5,
    )

    assert result["status"] == "recorded"
    assert result["recorded"] == ["tests"]
    assert result["audit"]["missing"] == ["replay", "redaction", "dry_run"]
    assert result["capabilities"]["level"] == 2
    assert result["capabilities"]["can_real_apply"] is False
    assert "Authorization: Bearer secret" not in app.config["sources"]["hirehi"]["external_apply"]["evidence"]["tests"]["command"]
    assert "ignored" not in app.config["sources"]["hirehi"]["external_apply"]["evidence"]


def test_source_certification_evidence_api_records_tests_evidence(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_external_apply(app)

    with _server(tmp_path) as base:
        result = _post_json(
            base,
            "/api/sources/hirehi/certification-evidence",
            {"level": 5, "evidence": {"tests": {"status": "passed", "command": "pytest hirehi"}}},
        )

    assert result["status"] == "recorded"
    assert result["recorded"] == ["tests"]
    assert result["audit"]["missing"] == ["replay", "redaction", "dry_run"]
    assert result["capabilities"]["level"] == 2


def test_configure_source_external_apply_target_persists_without_promoting(tmp_path):
    app = WorkHunter(root=tmp_path)

    result = app.configure_source_external_apply_target(
        "hirehi",
        session="hirehi",
        url="https://hirehi.example/apply",
        method="post",
        payload_template={"jobId": "{source_id}"},
        level=5,
    )

    assert result["status"] == "configured"
    assert result["target"]["method"] == "POST"
    assert result["target"]["session"] == "hirehi"
    assert result["target"]["url"] == "https://hirehi.example/apply"
    assert result["audit"]["missing"] == ["tests", "replay", "redaction", "dry_run"]
    assert result["capabilities"]["level"] == 2
    assert result["capabilities"]["can_real_apply"] is False


def test_source_external_apply_target_api_configures_session_and_url(tmp_path):
    with _server(tmp_path) as base:
        result = _post_json(
            base,
            "/api/sources/hirehi/external-apply-target",
            {
                "session": "hirehi",
                "url": "https://hirehi.example/apply",
                "method": "post",
                "payload_template": {"jobId": "{source_id}"},
            },
        )

    assert result["status"] == "configured"
    assert result["target"]["method"] == "POST"
    assert result["audit"]["missing"] == ["tests", "replay", "redaction", "dry_run"]
    assert result["capabilities"]["level"] == 2


def test_source_external_apply_from_har_api_configures_getmatch_target(tmp_path):
    har_path = tmp_path / "getmatch.har"
    har_path.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "method": "POST",
                                "url": "https://getmatch.ru/api/applications",
                                "headers": [{"name": "Cookie", "value": "sid=secret"}],
                                "postData": {
                                    "text": json.dumps(
                                        {
                                            "offer_id": "34397",
                                            "cover_letter": "Hi",
                                            "resume_id": "resume-secret",
                                        }
                                    )
                                },
                            },
                            "response": {
                                "status": 201,
                                "headers": [],
                                "content": {"mimeType": "application/json", "text": "{}"},
                            },
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    with _server(tmp_path) as base:
        result = _post_json(
            base,
            "/api/sources/getmatch/external-apply-from-har",
            {"path": str(har_path), "hosts": ["getmatch.ru"], "level": 5},
        )

    assert result["status"] == "configured"
    assert result["configured"]["target"]["url"] == "https://getmatch.ru/api/applications"
    assert result["configured"]["target"]["payload_template"]["offer_id"] == "{source_id}"
    assert "resume-secret" not in json.dumps(result, ensure_ascii=False)


def test_record_source_redaction_scan_creates_certification_evidence(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_external_apply(app)
    app.record_replay_event(
        source="hirehi",
        event_type="external_apply_dry_run",
        data={"status": "dry_run_ready", "submit": False},
    )

    result = app.record_source_redaction_scan(
        "hirehi",
        payload={
            "headers": {"Authorization": "Bearer secret-token"},
            "url": "https://hirehi.example/apply?access_token=secret-token",
        },
        level=5,
    )

    serialized = json.dumps(result, ensure_ascii=False)
    events = app.storage.list_replay_events(event_type="external_apply_redaction_scan")
    assert result["status"] == "recorded"
    assert result["audit"]["missing"] == ["tests"]
    assert result["findings"]["changed"] is True
    assert "Authorization" in result["findings"]["markers"]
    assert events[0]["data"]["status"] == "passed"
    assert events[0]["data"]["changed"] is True
    assert "secret-token" not in serialized
    assert "secret-token" not in json.dumps(events, ensure_ascii=False)


def test_source_redaction_scan_api_records_certification_evidence(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_external_apply(app)
    app.record_replay_event(
        source="hirehi",
        event_type="external_apply_dry_run",
        data={"status": "dry_run_ready", "submit": False},
    )

    with _server(tmp_path) as base:
        result = _post_json(
            base,
            "/api/sources/hirehi/redaction-scan",
            {
                "payload": {"headers": {"Cookie": "sid=secret-cookie"}},
                "text": "client_secret=secret-cookie",
            },
        )

    assert result["status"] == "recorded"
    assert result["audit"]["missing"] == ["tests"]
    assert "Cookie" in result["findings"]["markers"]
    assert "secret-cookie" not in json.dumps(result, ensure_ascii=False)


def test_source_certification_matrix_summarizes_priority_source_readiness(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_external_apply(app)
    app.record_replay_event(
        source="hirehi",
        event_type="external_apply_dry_run",
        data={"status": "dry_run_ready", "submit": False},
    )
    app.record_replay_event(
        source="hirehi",
        event_type="redaction_scan",
        data={"status": "passed"},
    )

    matrix = app.source_certification_matrix(
        level=5,
        sources=["hirehi", "jabka"],
        evidence={"hirehi": {"tests": {"status": "passed", "command": "pytest hirehi"}}},
    )

    assert matrix["status"] == "partial"
    assert matrix["requested_level"] == 5
    assert matrix["summary"] == {"total": 2, "ready": 1, "blocked": 1}
    assert list(matrix["sources"]) == ["hirehi", "jabka"]
    assert matrix["sources"]["hirehi"]["ready"] is True
    assert matrix["sources"]["jabka"]["ready"] is False
    assert matrix["missing_by_source"]["jabka"] == ["session", "url", "tests", "replay", "redaction", "dry_run"]
    assert matrix["promotion_payloads"]["hirehi"]["external_apply"]["level"] == 5


def test_source_certification_matrix_api_reports_mixed_sources(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_external_apply(app)
    app.record_replay_event(
        source="hirehi",
        event_type="external_apply_dry_run",
        data={"status": "dry_run_ready", "submit": False},
    )
    app.record_replay_event(
        source="hirehi",
        event_type="redaction_scan",
        data={"status": "passed"},
    )

    with _server(tmp_path) as base:
        matrix = _post_json(
            base,
            "/api/sources/certification-matrix",
            {
                "level": 5,
                "sources": ["hirehi", "jabka"],
                "evidence": {"hirehi": {"tests": {"status": "passed", "command": "pytest hirehi"}}},
            },
        )

    assert matrix["status"] == "partial"
    assert matrix["sources"]["hirehi"]["ready"] is True
    assert matrix["sources"]["jabka"]["ready"] is False
    assert matrix["missing_by_source"]["jabka"] == ["session", "url", "tests", "replay", "redaction", "dry_run"]


def test_source_certification_plan_builds_safe_evidence_actions(tmp_path):
    app = WorkHunter(root=tmp_path)

    plan = app.source_certification_plan(level=5, sources=["hirehi"])
    hirehi = plan["sources"]["hirehi"]
    requirements = [action["requirement"] for action in hirehi["actions"]]
    commands = "\n".join(action["command"] for action in hirehi["actions"])

    assert plan["status"] == "blocked"
    assert hirehi["ready"] is False
    assert hirehi["missing"] == ["session", "url", "tests", "replay", "redaction", "dry_run"]
    assert requirements == ["session", "url", "tests", "replay", "redaction", "dry_run"]
    assert all(action["safe"] is True for action in hirehi["actions"])
    assert "work-hunter browser login hirehi" in commands
    assert "work-hunter source external-apply-from-har hirehi <session.har>" in commands
    assert "python -m pytest" in commands
    assert "work-hunter source redaction-scan hirehi" in commands
    assert "work-hunter source certify" not in commands


def test_source_certification_plan_api_returns_next_actions(tmp_path):
    with _server(tmp_path) as base:
        plan = _post_json(base, "/api/sources/certification-plan", {"level": 5, "sources": ["jabka"]})

    assert plan["status"] == "blocked"
    assert plan["sources"]["jabka"]["actions"][0]["requirement"] == "session"
    assert plan["sources"]["jabka"]["actions"][0]["surface"] == "browser_lab"


def test_promote_source_certification_applies_ready_audit_payload(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_external_apply(app)
    dry_run_id = app.record_replay_event(
        source="hirehi",
        event_type="external_apply_dry_run",
        data={"status": "dry_run_ready", "submit": False},
    )
    app.record_replay_event(
        source="hirehi",
        event_type="redaction_scan",
        data={"status": "ok"},
    )

    result = app.promote_source_certification(
        "hirehi",
        level=5,
        evidence={"tests": {"status": "passed", "command": "pytest hirehi"}},
    )
    capabilities = app.source_capabilities()["hirehi"]

    assert result["status"] == "certified"
    assert result["source"] == "hirehi"
    assert result["level"] == 5
    assert result["audit"]["ready"] is True
    assert capabilities["level"] == 5
    assert capabilities["can_real_apply"] is True
    assert app.config["sources"]["hirehi"]["external_apply"]["evidence"]["dry_run"]["event_id"] == dry_run_id


def test_changing_external_apply_target_invalidates_certification(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_external_apply(app)
    app.record_replay_event(
        source="hirehi",
        event_type="external_apply_dry_run",
        data={"status": "dry_run_ready", "submit": False},
    )
    app.record_replay_event(
        source="hirehi",
        event_type="redaction_scan",
        data={"status": "ok"},
    )
    certified = app.promote_source_certification(
        "hirehi",
        level=5,
        evidence={"tests": {"status": "passed", "command": "pytest hirehi"}},
    )

    changed = app.configure_source_external_apply_target(
        "hirehi",
        session="hirehi",
        url="https://hirehi.example/new-apply",
        method="POST",
        payload_template={"jobId": "{source_id}"},
    )

    external_apply = app.config["sources"]["hirehi"]["external_apply"]
    assert certified["status"] == "certified"
    assert changed["status"] == "configured"
    assert changed["certification_invalidated"] is True
    assert external_apply["url"] == "https://hirehi.example/new-apply"
    assert external_apply.get("certified") is not True
    assert "evidence" not in external_apply
    assert app.source_capabilities()["hirehi"]["can_real_apply"] is False


def test_promote_source_certification_blocks_incomplete_audit(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_external_apply(app)

    result = app.promote_source_certification("hirehi", level=5)

    assert result["status"] == "blocked"
    assert result["reason"] == "source_certification_evidence_missing"
    assert result["audit"]["missing"] == ["tests", "replay", "redaction", "dry_run"]
    assert app.source_capabilities()["hirehi"]["level"] == 2
    assert "evidence" not in app.config["sources"]["hirehi"]["external_apply"]


def test_promote_source_certification_api_certifies_ready_source(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_external_apply(app)
    app.record_replay_event(
        source="hirehi",
        event_type="external_apply_dry_run",
        data={"status": "dry_run_ready", "submit": False},
    )
    app.record_replay_event(
        source="hirehi",
        event_type="redaction_scan",
        data={"status": "ok"},
    )

    with _server(tmp_path) as base:
        result = _post_json(
            base,
            "/api/sources/hirehi/certify",
            {"level": 5, "evidence": {"tests": {"status": "passed", "command": "pytest hirehi"}}},
        )

    assert result["status"] == "certified"
    assert result["capabilities"]["level"] == 5
    assert result["capabilities"]["can_real_apply"] is True


def test_promote_source_certification_redacts_provided_evidence(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_external_apply(app)
    app.record_replay_event(
        source="hirehi",
        event_type="external_apply_dry_run",
        data={"status": "dry_run_ready", "submit": False},
    )
    app.record_replay_event(
        source="hirehi",
        event_type="redaction_scan",
        data={"status": "ok"},
    )

    result = app.promote_source_certification(
        "hirehi",
        level=5,
        evidence={"tests": {"status": "passed", "command": "pytest hirehi --api-key sk-secret"}},
    )

    serialized = json.dumps({"result": result, "config": app.config}, ensure_ascii=False)
    assert result["status"] == "certified"
    assert "sk-secret" not in serialized
    assert result["audit"]["evidence"]["tests"]["command"] == "pytest hirehi --api-key ***"
    assert app.config["sources"]["hirehi"]["external_apply"]["evidence"]["tests"]["command"] == "pytest hirehi --api-key ***"


def test_source_status_report_promotes_certified_external_apply_levels():
    from work_hunter.sources.registry import source_status_report

    manual = source_status_report(
        {
            "hirehi": {
                "enabled": True,
                "external_apply": {
                    "certified": True,
                    "level": 5,
                    "session": "hirehi",
                    "url": "https://hirehi.example/apply",
                    "evidence": _external_apply_evidence(),
                },
            }
        }
    )["hirehi"]
    campaign = source_status_report(
        {
            "hirehi": {
                "enabled": True,
                "external_apply": {
                    "certified": True,
                    "level": 6,
                    "session": "hirehi",
                    "url": "https://hirehi.example/apply",
                    "evidence": _external_apply_evidence(),
                },
            }
        }
    )["hirehi"]

    assert manual["level"] == 5
    assert manual["readiness_badge"] == "L5 manual-confirm apply"
    assert manual["can_real_apply"] is True
    assert manual["can_campaign_apply"] is False
    assert "real_apply_maturity_below_l4" not in manual["blockers"]
    assert campaign["level"] == 6
    assert campaign["readiness_badge"] == "L6 campaign apply"
    assert campaign["can_campaign_apply"] is True


def test_source_status_report_refuses_external_apply_certification_without_evidence():
    from work_hunter.sources.registry import source_status_report

    status = source_status_report(
        {
            "hirehi": {
                "enabled": True,
                "external_apply": {
                    "certified": True,
                    "level": 6,
                    "session": "hirehi",
                    "url": "https://hirehi.example/apply",
                },
            }
        }
    )["hirehi"]

    assert status["level"] == 2
    assert status["readiness_badge"] == "L2 apply plan"
    assert status["can_real_apply"] is False
    assert status["can_campaign_apply"] is False
    assert "real_apply_maturity_below_l4" in status["blockers"]
    assert "external_apply_tests_missing" in status["blockers"]
    assert "external_apply_replay_missing" in status["blockers"]
    assert "external_apply_redaction_missing" in status["blockers"]
    assert "external_apply_dry_run_missing" in status["blockers"]


def test_source_status_report_certified_external_apply_requires_url_and_session():
    from work_hunter.sources.registry import source_status_report
    from work_hunter.source_adapters.registry import source_adapter_status_report

    missing = source_status_report(
        {
            "hirehi": {
                "enabled": True,
                "external_apply": {"certified": True, "level": 6},
            }
        }
    )["hirehi"]
    ready = source_status_report(
        {
            "hirehi": {
                "enabled": True,
                "external_apply": {
                    "certified": True,
                    "level": 6,
                    "session": "hirehi",
                    "url": "https://hirehi.example/apply",
                    "evidence": _external_apply_evidence(),
                },
            }
        }
    )["hirehi"]
    sdk_missing = source_adapter_status_report(
        {
            "hirehi": {
                "enabled": True,
                "external_apply": {"certified": True, "level": 5, "url": "https://hirehi.example/apply"},
            }
        }
    )["hirehi"]

    assert "external_apply_session_missing" in missing["blockers"]
    assert "external_apply_url_missing" in missing["blockers"]
    assert "external_apply_tests_missing" in missing["blockers"]
    assert missing["can_real_apply"] is False
    assert missing["can_campaign_apply"] is False
    assert missing["real_apply_channel"] == ""
    assert ready["can_real_apply"] is True
    assert ready["can_campaign_apply"] is True
    assert ready["real_apply_channel"] == "certified_external_session"
    assert sdk_missing["policy"] == {"can_apply": False, "can_campaign_apply": False, "requires_confirmation": False}


def test_source_adapter_sdk_normalizes_capabilities_and_apply_policy():
    from work_hunter.source_adapters.apply_policy import apply_policy_for_level
    from work_hunter.source_adapters.capabilities import normalize_source_capabilities
    from work_hunter.source_adapters.registry import source_adapter_registry, source_adapter_status_report

    registry = source_adapter_registry()
    report = source_adapter_status_report(
        {
            "hirehi": {
                "enabled": True,
                "external_apply": {
                    "certified": True,
                    "level": 5,
                    "session": "hirehi",
                    "url": "https://hirehi.example/apply",
                    "evidence": _external_apply_evidence(),
                },
            }
        }
    )

    assert set(registry) >= {"hh", "geekjob", "habr", "getmatch", "hirehi", "careerspace", "jabka"}
    assert registry["hh"].name == "hh"
    assert registry["hh"].capabilities().apply == "official_api"
    assert normalize_source_capabilities({"search": "personal_auth_recon", "detail": "public_json", "apply": "external_page", "auth": "none"}).to_dict() == {
        "search": "authenticated_browser",
        "detail": "public_json",
        "apply": "external_link",
        "auth": "none",
    }
    assert report["getmatch"]["capabilities"]["search"] == "authenticated_browser"
    assert report["hirehi"]["maturity"]["level"] == 5
    assert report["hirehi"]["policy"] == {"can_apply": True, "can_campaign_apply": False, "requires_confirmation": True}
    assert apply_policy_for_level(6) == {"can_apply": True, "can_campaign_apply": True, "requires_confirmation": False}


def test_registered_source_adapter_methods_return_safe_plans_instead_of_not_implemented():
    from work_hunter.source_adapters.registry import source_adapter_registry

    adapter = source_adapter_registry()["hirehi"]
    plan = adapter.prepare_apply(
        {"source_id": "job-1", "url": "https://hirehi.ru/jobs/job-1"},
        {"id": 10, "cover_letter": "Hi"},
    )
    dry_run = adapter.dry_run_apply(plan)
    blocked = adapter.apply(plan, {"can_apply": False})

    assert plan["status"] == "external_link"
    assert plan["submit"] is False
    assert plan["requires_manual_submit"] is True
    assert dry_run["status"] == "manual_review_required"
    assert dry_run["submit"] is False
    assert blocked == {
        "status": "blocked",
        "source": "hirehi",
        "reason": "source_adapter_real_apply_not_enabled",
        "submit": False,
    }


def test_registered_source_adapter_apply_returns_manual_handoff_when_l5_policy_allows_apply():
    from work_hunter.source_adapters.registry import source_adapter_registry, source_adapter_status_report

    source_configs = {
        "hirehi": {
            "enabled": True,
            "external_apply": {
                "certified": True,
                "level": 5,
                "session": "hirehi",
                "url": "https://hirehi.example/apply",
                "evidence": _external_apply_evidence(),
            },
        }
    }
    adapter = source_adapter_registry(source_configs)["hirehi"]
    policy = source_adapter_status_report(source_configs)["hirehi"]["policy"]
    plan = adapter.prepare_apply(
        {"source_id": "job-1", "url": "https://hirehi.ru/jobs/job-1"},
        {"id": "pack-1", "cover_letter": "Hi"},
    )

    handoff = adapter.apply(plan, policy)

    assert policy == {"can_apply": True, "can_campaign_apply": False, "requires_confirmation": True}
    assert handoff == {
        "status": "manual_submit_ready",
        "source": "hirehi",
        "source_id": "job-1",
        "external_url": "https://hirehi.ru/jobs/job-1",
        "application_pack_id": "pack-1",
        "submit": False,
        "requires_confirmation": True,
        "final_submit_requires_user": True,
        "actions": [{"type": "open_url", "url": "https://hirehi.ru/jobs/job-1"}],
        "plan": plan,
    }


def test_registered_source_adapter_delegates_search_and_detail_to_source_client():
    from work_hunter.source_adapters.base import RegisteredSourceAdapter

    class FakeSourceClient:
        def collect(self, profile, limit=None):
            assert profile["queries"] == ["python"]
            assert limit == 2
            return [
                type(
                    "JobLike",
                    (),
                    {
                        "source": "hirehi",
                        "source_id": "job-1",
                        "url": "https://hirehi.ru/jobs/job-1",
                        "title": "Python Backend",
                        "to_dict": lambda self: {
                            "source": self.source,
                            "source_id": self.source_id,
                            "url": self.url,
                            "title": self.title,
                        },
                    },
                )()
            ]

        def detail(self, source_id):
            assert source_id == "job-1"
            return {"source_id": source_id, "title": "Python Backend", "description": "FastAPI"}

    adapter = RegisteredSourceAdapter(
        name="hirehi",
        raw_capabilities={"search": "public_html", "detail": "public_html", "apply": "external_page", "auth": "none"},
        status_payload={"enabled": True},
        source_client=FakeSourceClient(),
    )

    search = adapter.search({"query": "python", "limit": 2})
    detail = adapter.detail("job-1")

    assert search == [
        {
            "source": "hirehi",
            "source_id": "job-1",
            "url": "https://hirehi.ru/jobs/job-1",
            "title": "Python Backend",
        }
    ]
    assert detail == {
        "status": "ready",
        "source": "hirehi",
        "source_id": "job-1",
        "detail": {"source_id": "job-1", "title": "Python Backend", "description": "FastAPI"},
    }


def test_source_adapter_registry_wires_public_board_client_from_config(monkeypatch):
    from work_hunter.source_adapters.registry import source_adapter_registry

    fetched: list[str] = []

    def fake_fetch(url, *args, **kwargs):
        del args, kwargs
        fetched.append(url)
        return """
        <article>
          <a href="/jobs/python-backend">Python Backend @ Acme</a>
          <p>Remote FastAPI role</p>
        </article>
        """

    monkeypatch.setattr("work_hunter.sources.public_boards.fetch_url", fake_fetch)

    adapter = source_adapter_registry(
        {
            "hirehi": {
                "enabled": True,
                "base_url": "https://hirehi.example",
                "paths": ["/jobs"],
                "query_params": ["q"],
                "pages": 1,
            }
        }
    )["hirehi"]

    search = adapter.search({"query": "python", "limit": 1})

    assert fetched == ["https://hirehi.example/jobs?q=python"]
    assert adapter.status()["enabled"] is True
    assert len(search) == 1
    assert {
        "source": search[0]["source"],
        "source_id": search[0]["source_id"],
        "url": search[0]["url"],
        "title": search[0]["title"],
        "company": search[0]["company"],
        "location": search[0]["location"],
        "remote": search[0]["remote"],
        "description": search[0]["description"],
    } == {
        "source": "hirehi",
        "source_id": "python-backend",
        "url": "https://hirehi.example/jobs/python-backend",
        "title": "Python Backend @ Acme",
        "company": "Acme",
        "location": "Remote",
        "remote": True,
        "description": "Python Backend @ Acme Remote FastAPI role",
    }


def test_registered_source_adapter_detail_uses_get_offer_when_available():
    from work_hunter.source_adapters.base import RegisteredSourceAdapter

    class FakeGetmatchClient:
        def get_offer(self, offer_id):
            assert offer_id == "offer-42"
            return {"id": "offer-42", "position": "Python Backend", "company": {"name": "Acme"}}

    adapter = RegisteredSourceAdapter(
        name="getmatch",
        raw_capabilities={"search": "public_json", "detail": "public_json", "apply": "personal_auth_recon", "auth": "browser_session"},
        status_payload={"enabled": True},
        source_client=FakeGetmatchClient(),
    )

    assert adapter.detail("offer-42") == {
        "status": "ready",
        "source": "getmatch",
        "source_id": "offer-42",
        "detail": {"id": "offer-42", "position": "Python Backend", "company": {"name": "Acme"}},
    }


class _server:
    def __init__(self, root):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(root))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


def _get_json(base: str, path: str):
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(base: str, path: str, payload: dict):
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))
