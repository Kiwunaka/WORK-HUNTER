from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.cli import main as cli_main
from work_hunter.hh_agent.resume_templates import (
    build_hh_batch_preset_matrix,
    draft_hh_resume_payload_from_template,
)
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


RESUME_TEMPLATE = """# {title}
first_name: {first_name}
last_name: {last_name}
area: {area}
professional_roles: {professional_roles}
email: {email}
phone: {phone}

## Summary
{summary}

## Skills
{skills}

## Experience
{experience}
"""


def _configure_resume_profile(app: WorkHunter) -> None:
    app.config["profiles"]["default"].update(
        {
            "name": "Alex Candidate",
            "title": "Python Backend Engineer",
            "email": "alex@example.test",
            "phone": "+79990000000",
            "area": "1",
            "professional_roles": ["96", "165"],
            "must_have_skills": ["Python", "FastAPI"],
            "nice_to_have_skills": ["PostgreSQL"],
            "desired_roles": ["backend", "python"],
        }
    )
    app.config["about"].update(
        {
            "summary": "Builds reliable backend automation.",
            "all_skills": ["Python", "FastAPI", "PostgreSQL"],
            "experience": [
                {
                    "company": "Acme",
                    "position": "Backend Engineer",
                    "period": "2022-2024",
                    "description": "Built integrations and internal APIs.",
                }
            ],
        }
    )
    app.save_config(app.config)


def test_resume_template_drafts_hh_payload_from_profile_and_about(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_resume_profile(app)

    draft = draft_hh_resume_payload_from_template(
        RESUME_TEMPLATE,
        profile=app.config["profiles"]["default"],
        about=app.config["about"],
    )

    assert "Acme" in draft["markdown"]
    assert draft["status"] == "draft"
    assert draft["validation"]["valid"] is True
    assert draft["payload"]["title"] == "Python Backend Engineer"
    assert draft["payload"]["first_name"] == "Alex"
    assert draft["payload"]["last_name"] == "Candidate"
    assert draft["payload"]["area"] == {"id": "1"}
    assert draft["payload"]["professional_roles"] == [{"id": "96"}, {"id": "165"}]
    assert draft["payload"]["contact"][0]["type"]["id"] == "email"
    assert "FastAPI" in draft["payload"]["skills"]


def test_resume_template_preview_is_dry_run_without_hh_token(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_resume_profile(app)
    app.config["sources"]["hh"]["access_token"] = ""
    app.save_config(app.config)

    preview = app.preview_hh_resume_template(RESUME_TEMPLATE)

    assert preview["status"] == "dry_run"
    assert preview["validation"]["valid"] is True
    assert preview["payload"]["title"] == "Python Backend Engineer"
    assert "HH access token" not in json.dumps(preview)


def test_batch_preset_matrix_expands_combinations_and_strips_local_machine_details():
    matrix = build_hh_batch_preset_matrix(
        {
            "resumes": ["resume-a", "resume-b"],
            "search_presets": ["backend"],
            "letters": ["warm", "short"],
            "limits": [10, 20],
            "defaults": {
                "min_score": 80,
                "access_token": "secret",
                "shell_script": "C:\\Users\\me\\run.bat",
                "local_path": "C:\\Users\\me\\payload.json",
                "command": "powershell ./apply.ps1",
            },
        }
    )

    assert len(matrix["items"]) == 8
    first = matrix["items"][0]
    assert first["name"] == "resume-a/backend/warm/10"
    assert first["params"] == {
        "min_score": 80,
        "resume_id": "resume-a",
        "search_preset": "backend",
        "letter_template": "warm",
        "limit": 10,
    }
    assert matrix["dropped_defaults"] == ["access_token", "command", "local_path", "shell_script"]


def test_resume_template_preview_cli_outputs_payload(tmp_path, capsys):
    app = WorkHunter(root=tmp_path)
    _configure_resume_profile(app)
    template_path = tmp_path / "resume-template.md"
    template_path.write_text(RESUME_TEMPLATE, encoding="utf-8")

    cli_main(["--root", str(tmp_path), "hh-resume-template-preview", "--template-file", str(template_path)])

    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "dry_run"
    assert preview["validation"]["valid"] is True
    assert preview["payload"]["first_name"] == "Alex"


def test_resume_template_and_batch_matrix_web_api(tmp_path):
    app = WorkHunter(root=tmp_path)
    _configure_resume_profile(app)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        preview_request = urllib.request.Request(
            f"{base}/api/hh/resume-template/preview",
            data=json.dumps({"template": RESUME_TEMPLATE}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(preview_request, timeout=5) as response:
            preview = json.loads(response.read().decode("utf-8"))

        matrix_request = urllib.request.Request(
            f"{base}/api/hh/batch-matrix",
            data=json.dumps(
                {
                    "matrix": {
                        "resumes": ["resume-a"],
                        "search_presets": ["backend"],
                        "letters": ["warm"],
                        "limits": [5],
                        "defaults": {"secret": "x", "min_score": 70},
                    }
                }
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(matrix_request, timeout=5) as response:
            matrix = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert preview["status"] == "dry_run"
    assert preview["payload"]["title"] == "Python Backend Engineer"
    assert matrix["items"][0]["params"] == {
        "min_score": 70,
        "resume_id": "resume-a",
        "search_preset": "backend",
        "letter_template": "warm",
        "limit": 5,
    }
