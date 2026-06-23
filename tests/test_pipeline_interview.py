from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.models import Job, JobScore
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


def _pipeline_job(app: WorkHunter) -> int:
    job_id = app.storage.upsert_job(
        Job(
            source="geekjob",
            source_id="pipe-1",
            url="https://geekjob.ru/vacancy/pipe-1",
            title="Backend Python Engineer",
            company="Acme",
            description="FastAPI, PostgreSQL, Redis, APIs and async workers.",
            salary_text="250000-320000 RUB",
            remote=True,
        )
    )
    app.storage.save_score(
        JobScore(
            job_id=job_id,
            total_score=86,
            skills_score=34,
            reasons=["FastAPI match", "Remote match"],
            red_flags=["Ask about on-call load"],
        )
    )
    return job_id


def test_pipeline_status_after_apply_suggests_manual_followup_and_calendar_hook(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = _pipeline_job(app)
    app.storage.save_application(job_id, "applied", "sent via campaign")
    app.storage.set_status(job_id, "applied", "sent via campaign")

    status = app.pipeline_status(job_id, now="2026-06-13T12:00:00+00:00")
    scheduled = app.schedule_pipeline_event(
        job_id,
        event_type="follow_up",
        event_at="2026-06-16T09:00:00+00:00",
    )

    assert status["stage"] == "applied_waiting"
    assert status["application"]["status"] == "applied"
    assert status["follow_up"]["status"] == "ready"
    assert status["follow_up"]["requires_manual_send"] is True
    assert status["next_actions"][0]["type"] == "send_follow_up"
    assert "Acme" in status["follow_up"]["body"]
    assert scheduled["status"] == "scheduled"
    assert scheduled["event"]["event_type"] == "follow_up"
    assert app.storage.list_events()[0].job_id == job_id
    assert app.replay_for_job(job_id)["events"][-1]["event_type"] == "pipeline_event_scheduled"


def test_interview_prep_pack_uses_job_profile_confirmed_facts_and_salary_script(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = _pipeline_job(app)
    app.config["profiles"]["default"].update(
        {
            "name": "Alex Candidate",
            "must_have_skills": ["Python", "FastAPI", "PostgreSQL"],
            "nice_to_have_skills": ["Redis", "Docker"],
            "salary_min": 250000,
        }
    )
    app.storage.save_candidate_fact(
        profile_id="default",
        category="experience",
        key="project",
        value="Built FastAPI billing APIs with PostgreSQL and Redis queues.",
        source="test",
        status="confirmed",
    )

    pack = app.interview_prep_pack(job_id, stage="tech")

    assert pack["status"] == "ready"
    assert pack["stage"] == "tech"
    assert "FastAPI" in pack["tech_stack"]
    assert pack["sections"]["stack_questions"]
    assert pack["star_answers"][0]["claim"].startswith("Built FastAPI")
    assert "250000" in pack["salary_script"]
    assert "Ask about on-call load" in pack["risk_notes"]


def test_interview_prep_pack_exposes_pipeline_automation_and_after_interview_assets(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = _pipeline_job(app)
    app.storage.save_candidate_fact(
        profile_id="default",
        category="achievement",
        key="impact",
        value="Reduced API latency by 30% while migrating async workers.",
        source="test",
        status="confirmed",
    )

    pack = app.interview_prep_pack(job_id, stage="final")

    assert pack["stages"] == ["hr", "tech", "final", "offer"]
    assert pack["pipeline_automation"] == [
        "applied_wait_3_days_follow_up",
        "reply_parse_schedule_interview_prep",
        "interview_generate_prep_pack",
        "after_interview_thank_you_follow_up",
    ]
    assert pack["sections"]["stage_focus"]["stage"] == "final"
    assert pack["sections"]["stage_focus"]["goals"]
    assert pack["after_interview"]["thank_you_template"].startswith("Hi Acme")
    assert "Reduced API latency" in pack["star_answers"][0]["claim"]
    assert pack["metadata"]["uses_confirmed_facts"] is True


def test_interview_prep_pack_normalizes_invalid_stage_and_excludes_unconfirmed_facts(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(
            source="geekjob",
            source_id="pipe-2",
            url="https://geekjob.ru/vacancy/pipe-2",
            title="Backend Python Engineer",
            company="Acme",
            description="FastAPI, PostgreSQL. Includes paid test task before interview.",
        )
    )
    app.storage.save_candidate_fact(
        profile_id="default",
        category="achievement",
        key="unconfirmed",
        value="Unconfirmed: led a 20-person platform migration.",
        source="test",
        status="unconfirmed",
    )

    pack = app.interview_prep_pack(job_id, stage="onsite")
    serialized = json.dumps(pack, ensure_ascii=False)

    assert pack["stage"] == "hr"
    assert pack["sections"]["stage_focus"]["stage"] == "hr"
    assert "Unconfirmed: led" not in serialized
    assert any("Clarify any test task scope" in note for note in pack["risk_notes"])
    assert pack["metadata"]["uses_confirmed_facts"] is False
    assert pack["salary_script"].startswith("I would like to align compensation")


def test_pipeline_web_api_exposes_status_prep_pack_and_event_creation(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = _pipeline_job(app)
    app.storage.save_application(job_id, "applied", "sent")

    with _server(tmp_path) as base:
        status = _get_json(base, f"/api/pipeline/jobs/{job_id}")
        pack = _post_json(base, f"/api/pipeline/jobs/{job_id}/prep-pack", {"stage": "hr"})
        event = _post_json(
            base,
            f"/api/pipeline/jobs/{job_id}/event",
            {"event_type": "interview", "event_at": "2026-06-17T12:00:00+00:00"},
        )

    assert status["stage"] == "applied_waiting"
    assert pack["stage"] == "hr"
    assert event["event"]["event_type"] == "interview"


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
