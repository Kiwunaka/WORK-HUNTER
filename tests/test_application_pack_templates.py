from __future__ import annotations

from work_hunter.applications.cover_letter import application_template_names, render_cover_letter_template
from work_hunter.models import Job
from work_hunter.services import WorkHunter


def test_application_templates_cover_required_styles():
    assert application_template_names() == [
        "direct_human",
        "warm_recruiter",
        "technical_fit",
        "startup_fast",
        "remote_async",
        "career_switch_or_gap",
        "minimal",
        "strong_match",
        "low_context",
        "telegram_dm",
    ]

    rendered = render_cover_letter_template(
        "technical_fit",
        {
            "candidate_title": "Python Backend Engineer",
            "job_title": "FastAPI developer",
            "company": "Acme",
            "skills": "FastAPI, PostgreSQL",
        },
    )

    assert "FastAPI developer" in rendered
    assert "FastAPI, PostgreSQL" in rendered


def test_application_pack_exposes_plan_shape_and_preview_fields(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(source="geekjob", source_id="g1", url="https://geekjob.ru/v/g1", title="FastAPI backend", description="Python")
    )

    pack = app.build_application_pack(
        job_id,
        resume_variant={
            "id": 42,
            "body": "Python developer.",
            "diff": {"added_lines": ["FastAPI"], "removed_lines": []},
        },
        cover_letter="Hi",
        short_message="Short",
        source_payload={"fields": [{"name": "message", "value": "Hi"}], "form_signature": "known"},
        campaign_policy={"enabled": True, "real_apply": False, "min_score": 0},
    )

    assert pack["job_id"] == job_id
    assert pack["source"] == "geekjob"
    assert pack["resume_variant_id"] == "42"
    assert pack["cover_letter"] == "Hi"
    assert pack["short_message"] == "Short"
    assert pack["source_payload"]["form_signature"] == "known"
    assert pack["preview"]["resume_changes"] == ["FastAPI"]
    assert pack["preview"]["letter"] == "Hi"
    assert pack["preview"]["fields"] == [{"name": "message", "value": "Hi"}]
    assert pack["policy"] == {"can_apply": False, "reasons": ["real_apply_disabled"]}


def test_application_pack_policy_blocks_missing_assets_and_unsafe_forms(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(source="geekjob", source_id="g2", url="https://geekjob.ru/v/g2", title="FastAPI backend", description="Python")
    )

    pack = app.build_application_pack(
        job_id,
        resume_variant=None,
        cover_letter="",
        source_payload={"form_signature": "unknown", "captcha": True, "test_required": True},
        campaign_policy={"enabled": False, "real_apply": False, "min_score": 70},
    )

    assert pack["policy"]["can_apply"] is False
    assert pack["policy_reasons"] == [
        "campaign_disabled",
        "real_apply_disabled",
        "resume_variant_missing",
        "cover_letter_missing",
        "unknown_form",
        "captcha_or_challenge",
        "test_required",
        "below_min_score",
    ]


def test_application_pack_policy_allows_ready_real_apply_preview(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(source="geekjob", source_id="g3", url="https://geekjob.ru/v/g3", title="FastAPI backend", description="Python")
    )

    pack = app.build_application_pack(
        job_id,
        resume_variant={"id": 7, "body": "Python"},
        cover_letter="Hi",
        source_payload={"form_signature": "known"},
        campaign_policy={"enabled": True, "real_apply": True, "min_score": 0},
    )

    assert pack["policy"] == {"can_apply": True, "reasons": []}
