from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import sqlite3
import pytest

from work_hunter.hh_autopilot.config import parse_autopilot_settings, policy_hash
from work_hunter.hh_autopilot.repository import AutopilotRepository
from work_hunter.hh_transport.errors import HHNetworkError
from tests.test_hh_autopilot_repository import _filter_decision
from work_hunter.services import (
    WorkHunter,
    _hh_engine_context,
    _hh_policy_material,
    _stable_hh_resume_payload,
    _build_hh_engine,
    _published_hh_resumes,
)


class _PublishedResumeClient:
    def list_resumes(self):
        return [
            {
                "id": "resume-1",
                "status": {"id": "published"},
                "title": "Python Developer",
            }
        ]


def test_transient_resume_listing_error_is_not_misreported_as_missing_token():
    class OfflineClient:
        def list_resumes(self):
            raise HHNetworkError("temporary network failure")

    with pytest.raises(HHNetworkError):
        _published_hh_resumes(OfflineClient())


def test_sender_owns_connection_and_concurrent_journal_writes_are_serialized(tmp_path, monkeypatch):
    monkeypatch.setattr(WorkHunter, "_hh_client_for_account", lambda self, account: _PublishedResumeClient())
    app = WorkHunter(root=tmp_path)
    repo = AutopilotRepository(app.storage)
    context = _hh_engine_context(app, repo, "default", _PublishedResumeClient())
    engine = _build_hh_engine(app, repo, "default", context=context)
    def unexpected_resume_reload(self):
        raise AssertionError("dispatch must reuse the run's published resumes")
    monkeypatch.setattr(_PublishedResumeClient, "list_resumes", unexpected_resume_reload)
    assert engine.executor.policy_hash_provider("default") == context.policy_hash
    app.config["sources"]["hh"]["autopilot"]["limits"]["per_run_success"] += 1
    assert engine.executor.policy_hash_provider("default") != context.policy_hash
    lease = repo.acquire_lease("default", "pipeline-test", ttl_seconds=600)
    run = repo.create_run("default", trigger="schedule", policy_hash=context.policy_hash, fencing_token=lease.fencing_token)
    item = repo.create_item(run.id, "default", "test-vacancy", "resume-1", "test")
    barrier = Barrier(2, timeout=5)

    def sender():
        with engine.sender_factory(context, lease) as worker:
            conn = worker.repository.conn
            assert conn is not repo.conn
            assert worker.sender_factory is None  # No second coordinator/run.
            barrier.wait()
            for _ in range(20):
                worker.repository.append_event(item.id, "sender-test", run_id=run.id, fencing_token=lease.fencing_token)
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(sender)
        barrier.wait()
        for _ in range(20):
            repo.append_event(item.id, "producer-test", run_id=run.id, fencing_token=lease.fencing_token)
        future.result(timeout=5)
    events = repo.list_events(item.id)
    assert sum(e['reason_code'] in ('sender-test', 'producer-test') for e in events) == 40


def test_runtime_context_wires_blacklist_and_enabled_application_flows(tmp_path):
    app = WorkHunter(root=tmp_path)
    application = app.config["sources"]["hh"]["autopilot"]["application"]
    application.update({"screening_mode": "ai", "form_mode": "profile_grounded"})
    app.storage.upsert_hh_employer_blacklist(
        employer_id="blocked-employer",
        employer_name="Blocked Inc",
        reason="fast rejection",
    )

    context = _hh_engine_context(
        app,
        AutopilotRepository(app.storage),
        "default",
        _PublishedResumeClient(),
    )

    assert context.filter_context["blacklist"]["employer_ids"] == ["blocked-employer"]
    assert context.filter_context["supported_application_capabilities"] == [
        "direct",
        "screening",
        "form",
    ]

    application.update({"screening_mode": "off", "form_mode": "off"})
    restricted = _hh_engine_context(
        app,
        AutopilotRepository(app.storage),
        "default",
        _PublishedResumeClient(),
    )

    assert restricted.filter_context["supported_application_capabilities"] == ["direct"]


def test_unfinished_selection_is_not_an_inflight_application(tmp_path):
    app = WorkHunter(root=tmp_path)
    repo = AutopilotRepository(app.storage)
    lease = repo.acquire_lease("default", "selection-test", ttl_seconds=300)
    run = repo.create_run("default", trigger="schedule", policy_hash="hash", fencing_token=lease.fencing_token)
    item = repo.create_item(run.id, "default", "v-unfinished", "resume-1", "preset")
    repo.record_filter_decision(item.id, expected_version=item.version,
        decision=_filter_decision(),
        run_id=run.id, fencing_token=lease.fencing_token)

    context = _hh_engine_context(app, repo, "default", _PublishedResumeClient())

    assert "v-unfinished" not in context.filter_context["history"]["active_vacancy_ids"]


def test_policy_material_ignores_volatile_resume_counters(tmp_path):
    app = WorkHunter(root=tmp_path)
    config = app.config
    settings = parse_autopilot_settings(config)
    base = {
        "id": "resume-1",
        "status": {"id": "published"},
        "title": "Python Developer",
        "total_views": 10,
        "new_views": 3,
        "updated_at": "2026-09-17T08:36:49+0300",
        "next_publish_at": "2026-09-17T12:36:49+0300",
        "actions": [{"id": "raise"}],
        "can_publish_or_update": True,
        "age": {"days": 1},
        "photo": {
            "id": "163977528",
            "small": "https://img.hhcdn.ru/photo/1.jpeg?t=100&h=AAA",
        },
    }

    def projection(payload):
        return _hh_policy_material(
            app,
            config,
            "default",
            resumes=[_stable_hh_resume_payload(payload)],
        )

    baseline = policy_hash(settings, "default", projection(base))
    volatile_changed = policy_hash(
        settings,
        "default",
        projection(
            {
                **base,
                "total_views": 999,
                "new_views": 500,
                "next_publish_at": "2026-10-01T12:36:49+0300",
                "can_publish_or_update": False,
                "photo": {
                    "id": "163977528",
                    "small": "https://img.hhcdn.ru/photo/1.jpeg?t=999&h=ZZZ",
                },
            }
        ),
    )
    content_changed = policy_hash(
        settings,
        "default",
        projection({**base, "title": "Data Engineer"}),
    )

    assert baseline == volatile_changed
    assert baseline != content_changed


def test_policy_material_tracks_selected_resumes_only(tmp_path):
    app = WorkHunter(root=tmp_path)
    config = app.config
    config["sources"]["hh"]["autopilot"]["accounts"][0]["resume_queries"] = [
        {"resume_id": "resume-1", "preset_names": []},
    ]
    settings = parse_autopilot_settings(config)
    selected = {
        "id": "resume-1",
        "status": {"id": "published"},
        "title": "Python Developer",
        "content_hash": "resume-v1",
    }
    unselected = {
        "id": "resume-2",
        "status": {"id": "published"},
        "title": "Unselected draft",
        "content_hash": "draft-v1",
    }

    material = _hh_policy_material(
        app,
        config,
        "default",
        resumes=[_stable_hh_resume_payload(selected), _stable_hh_resume_payload(unselected)],
    )
    assert [resume["id"] for resume in material.resumes] == ["resume-1"]
    baseline = policy_hash(settings, "default", material)

    unselected_changed = _hh_policy_material(
        app,
        config,
        "default",
        resumes=[
            _stable_hh_resume_payload(selected),
            _stable_hh_resume_payload({**unselected, "content_hash": "draft-v2"}),
        ],
    )
    assert policy_hash(settings, "default", unselected_changed) == baseline

    selected_changed = _hh_policy_material(
        app,
        config,
        "default",
        resumes=[
            _stable_hh_resume_payload({**selected, "content_hash": "resume-v2"}),
            _stable_hh_resume_payload(unselected),
        ],
    )
    assert policy_hash(settings, "default", selected_changed) != baseline
