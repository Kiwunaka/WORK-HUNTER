from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.letters import human_cover_letter_variants
from work_hunter.models import Job
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


def _job() -> Job:
    return Job(
        source="geekjob",
        source_id="letter-1",
        url="https://geekjob.ru/vacancy/letter-1",
        title="Senior Python Backend Engineer",
        company="Acme",
        description="FastAPI, PostgreSQL, Redis, async workers, APIs, remote team.",
        remote=True,
    )


def _profile() -> dict:
    return {
        "name": "Alex Candidate",
        "title": "Python Backend Engineer",
        "must_have_skills": ["Python", "FastAPI", "PostgreSQL"],
        "nice_to_have_skills": ["Redis", "Docker"],
        "summary": "Built billing APIs with FastAPI, PostgreSQL and Redis queues.",
    }


def test_human_cover_letter_variants_follow_style_rules():
    preview = human_cover_letter_variants(_job(), _profile(), use_for_campaign=True)

    assert [variant["template"] for variant in preview["variants"]] == ["A", "B", "C", "D"]
    assert preview["selected_template"] == "A"
    assert preview["use_for_campaign"] is True

    banned_phrases = ["Я являюсь", "имею богатый опыт", "позвольте представиться"]
    for variant in preview["variants"]:
        body = variant["body"]
        paragraphs = [part for part in body.split("\n\n") if part.strip()]
        assert 2 <= len(paragraphs) <= 4
        assert "FastAPI" in body
        assert "PostgreSQL" in body
        assert all(phrase not in body for phrase in banned_phrases)
        assert len(body.split()) <= 130


def test_cover_letter_preview_can_be_selected_for_campaign_and_saved(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(_job())
    app.config["profiles"]["default"].update(_profile())

    preview = app.cover_letter_preview(job_id, template="B", use_for_campaign=True)

    assert preview["status"] == "ready"
    assert preview["selected_template"] == "B"
    assert preview["campaign_letter"]["template"] == "B"
    assert app.latest_letter(job_id).body == preview["campaign_letter"]["body"]
    assert app.replay_for_job(job_id)["events"][-1]["event_type"] == "cover_letter_generated"


def test_cover_letter_preview_web_api_returns_template_variants(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(_job())

    with _server(tmp_path) as base:
        preview = _post_json(
            base,
            f"/api/jobs/{job_id}/letter-preview",
            {"template": "D", "use_for_campaign": True},
        )

    assert preview["status"] == "ready"
    assert preview["selected_template"] == "D"
    assert len(preview["variants"]) == 4
    assert preview["campaign_letter"]["template"] == "D"


def test_human_cover_letter_falls_back_and_redacts_profile_summary_secrets():
    profile = {
        "must_have_skills": ["Go"],
        "nice_to_have_skills": [],
        "summary": "Invented Kubernetes certification. access_token=letter-secret",
    }

    preview = human_cover_letter_variants(_job(), profile, selected_template="Z")
    body = preview["campaign_letter"]["body"]

    assert preview["selected_template"] == "A"
    assert "letter-secret" not in body
    assert "access_token" not in body
    assert "Invented Kubernetes certification" not in body
    assert "backend" in body.lower() or "Go" in body


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


def _post_json(base: str, path: str, payload: dict):
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))
