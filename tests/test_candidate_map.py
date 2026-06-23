from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.memory.claims import evidence_status_for_fact
from work_hunter.onboarding.questions import onboarding_questions
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


def test_onboarding_questions_cover_candidate_map_sections():
    questions = onboarding_questions()
    ids = [question["id"] for question in questions]

    assert ids == [
        "identity",
        "stack",
        "roles",
        "experience",
        "projects",
        "achievements",
        "forbidden_claims",
        "salary_format",
        "avoid",
        "writing_style",
        "resume_assets",
        "apply_permissions",
    ]
    assert len(questions) == 12
    assert all(question["category"] for question in questions)


def test_candidate_map_schema_and_claim_evidence_statuses(tmp_path):
    app = WorkHunter(root=tmp_path)
    skill = app.answer_onboarding("stack", "Python, FastAPI, PostgreSQL", source="chat")
    app.answer_onboarding("forbidden_claims", "Do not claim Kubernetes production ownership.", source="manual")
    app.confirm_candidate_fact(skill["facts"][0]["id"])

    candidate_map = app.candidate_map()
    claims = {claim["key"]: claim for claim in candidate_map["claims"]}

    assert set(candidate_map) >= {
        "identity",
        "target",
        "skills",
        "experience",
        "portfolio",
        "resume_assets",
        "writing_style",
        "claims",
        "completeness",
    }
    assert candidate_map["skills"]["hard"] == ["fastapi", "postgresql", "python"]
    assert candidate_map["writing_style"]["tone"] == "human, concise, confident"
    assert claims["stack"]["status"] == "verified_by_user"
    assert claims["forbidden_claims"]["status"] == "forbidden_to_claim"
    assert evidence_status_for_fact({"status": "unconfirmed", "source": "chat"}) == "needs_confirmation"
    assert evidence_status_for_fact({"status": "confirmed", "source": "resume_import"}) == "verified_by_user"


def test_candidate_readiness_uses_config_about_as_verified_baseline(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.config["about"] = {
        "all_skills": ["Python", "FastAPI", "PostgreSQL"],
        "experience": [
            {
                "role": "Backend Engineer",
                "project": "Hiring OS",
                "details": ["Built a local automation command center"],
                "tech": ["Python", "SQLite"],
            }
        ],
    }
    app.config["profiles"]["default"]["locations"] = ["remote"]
    app.config["profiles"]["default"]["remote_only"] = True

    completeness = app.candidate_completeness()
    candidate_map = app.candidate_map()
    claims = {claim["key"]: claim for claim in candidate_map["claims"]}

    assert completeness["status"] == "complete"
    assert completeness["missing"] == []
    assert completeness["confirmed_facts"] == 3
    assert app.candidate_facts() == []
    assert candidate_map["skills"]["hard"] == ["fastapi", "postgresql", "python"]
    assert candidate_map["experience"][0]["role"] == "Backend Engineer"
    assert candidate_map["target"]["remote"] is True
    assert claims["skills"]["status"] == "verified_by_user"
    assert claims["experience"]["source"] == "config_about"
    assert claims["constraints"]["source"] == "config_profile"


def test_candidate_map_web_endpoint(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.answer_onboarding("roles", "Backend developer, Python engineer", source="test")

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        payload = _get_json(base, "/api/candidate/map")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert payload["target"]["roles"] == ["backend developer", "python engineer"]
    assert payload["claims"][0]["status"] == "needs_confirmation"


def _get_json(base: str, path: str):
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))
