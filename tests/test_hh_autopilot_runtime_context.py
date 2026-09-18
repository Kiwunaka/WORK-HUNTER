from __future__ import annotations

from work_hunter.hh_autopilot.config import parse_autopilot_settings, policy_hash
from work_hunter.hh_autopilot.repository import AutopilotRepository
from work_hunter.services import (
    WorkHunter,
    _hh_engine_context,
    _hh_policy_material,
    _stable_hh_resume_payload,
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
