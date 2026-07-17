from __future__ import annotations

from work_hunter.hh_autopilot.repository import AutopilotRepository
from work_hunter.services import WorkHunter, _hh_engine_context


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
