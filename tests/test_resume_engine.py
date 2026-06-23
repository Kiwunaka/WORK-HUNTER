from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.models import Job, Resume
from work_hunter.resume_engine import import_resume_file
from work_hunter.resumes.docx_export import export_docx
from work_hunter.resumes.pdf_export import export_pdf
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


def test_resume_import_json_builds_canonical_resume_and_persists_metadata(tmp_path):
    path = tmp_path / "resume.json"
    path.write_text(
        json.dumps(
            {
                "name": "Python Backend",
                "summary": "Backend developer",
                "skills": ["Python", "FastAPI", "PostgreSQL"],
                "experience": ["Built APIs"],
            }
        ),
        encoding="utf-8",
    )

    app = WorkHunter(root=tmp_path)
    result = app.import_resume(path)
    saved = app.storage.get_resume(result["id"])

    assert result["status"] == "imported"
    assert result["source_format"] == "json"
    assert result["canonical"]["skills"] == ["fastapi", "postgresql", "python"]
    assert result["canonical"]["summary"] == "Backend developer"
    assert saved is not None
    assert saved.canonical["skills"] == ["fastapi", "postgresql", "python"]
    assert saved.source_format == "json"


def test_resume_variant_includes_diff_against_canonical_base(tmp_path):
    app = WorkHunter(root=tmp_path)
    fact = app.answer_onboarding("skills", "FastAPI and PostgreSQL APIs.", source="test")
    app.confirm_candidate_fact(fact["facts"][0]["id"])
    job_id = app.storage.upsert_job(
        Job(source="geekjob", source_id="g1", url="u", title="FastAPI backend", description="FastAPI")
    )
    resume_id = app.storage.save_resume(
        Resume(
            name="Base",
            body="Python developer.",
            canonical={"summary": "Python developer.", "skills": ["python"], "experience": []},
            is_active=True,
        )
    )

    variant = app.build_resume_variant(job_id, resume_id)

    assert variant["status"] == "ready"
    assert variant["diff"]["added_lines"] == ["Relevant confirmed facts:", "- FastAPI and PostgreSQL APIs."]
    assert variant["diff"]["removed_lines"] == []
    assert "fastapi" in variant["canonical"]["skills"]
    assert "postgresql" in variant["canonical"]["skills"]


def test_resume_variant_reports_ats_keywords_risks_and_unsupported_claims(tmp_path):
    app = WorkHunter(root=tmp_path)
    fact = app.answer_onboarding("stack", "FastAPI and PostgreSQL APIs.", source="test")
    app.confirm_candidate_fact(fact["facts"][0]["id"])
    app.answer_onboarding("stack", "Kubernetes production ownership.", source="test")
    job_id = app.storage.upsert_job(
        Job(
            source="geekjob",
            source_id="g2",
            url="u",
            title="FastAPI backend",
            description="FastAPI PostgreSQL Kubernetes production platform",
        )
    )
    resume_id = app.storage.save_resume(
        Resume(
            name="Base",
            body="Python developer.",
            canonical={"summary": "Python developer.", "skills": ["python"], "experience": []},
            is_active=True,
        )
    )

    variant = app.build_resume_variant(job_id, resume_id)

    assert variant["status"] == "ready"
    assert "fastapi" in variant["ats_keywords"]
    assert "postgresql" in variant["ats_keywords"]
    assert "kubernetes" in variant["unsupported_claims"]
    assert "unsupported_claims_present" in variant["risk_flags"]
    assert variant["changes_summary"]


def test_resume_import_web_endpoint_and_variant_preview_expose_diff(tmp_path):
    app = WorkHunter(root=tmp_path)
    fact = app.answer_onboarding("experience", "FastAPI services.", source="test")
    app.confirm_candidate_fact(fact["facts"][0]["id"])
    job_id = app.storage.upsert_job(
        Job(source="habr", source_id="h1", url="u", title="FastAPI developer", description="FastAPI")
    )
    path = tmp_path / "resume.md"
    path.write_text(
        """# Python Backend

## Summary
Python developer.

## Skills
Python
""",
        encoding="utf-8",
    )

    with _server(tmp_path) as base:
        imported = _post_json(base, "/api/resumes/import", {"path": str(path), "activate": True})
        variant = _post_json(
            base,
            "/api/resume-variants/build",
            {"job_id": job_id, "resume_id": imported["id"]},
        )

    assert imported["status"] == "imported"
    assert imported["canonical"]["title"] == "Python Backend"
    assert variant["diff"]["added_lines"]
    assert variant["canonical"]["title"] == "Python Backend"


def test_resume_import_pdf_docx_returns_unsupported_when_optional_parser_missing(tmp_path, monkeypatch):
    path = tmp_path / "resume.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    app = WorkHunter(root=tmp_path)
    monkeypatch.setattr("work_hunter.resume_engine._optional_pdf_text", lambda source: None)

    result = app.import_resume(path)

    assert result["status"] == "unsupported"
    assert result["source_format"] == "pdf"
    assert "parser" in result["reason"]


def test_resume_docx_export_import_roundtrip_uses_real_docx_file(tmp_path):
    canonical = {
        "title": "Python Backend",
        "summary": "Backend developer building APIs.",
        "skills": ["Python", "FastAPI"],
        "experience": ["Built billing APIs."],
    }
    path = tmp_path / "resume.docx"

    exported = export_docx(canonical, path)
    imported = import_resume_file(path)

    assert exported["status"] == "exported"
    assert path.read_bytes().startswith(b"PK")
    assert imported["status"] == "imported"
    assert imported["source_format"] == "docx"
    assert imported["canonical"]["title"] == "Python Backend"
    assert imported["canonical"]["skills"] == ["fastapi", "python"]
    assert "Built billing APIs" in imported["body"]


def test_resume_pdf_export_import_roundtrip_uses_real_pdf_file(tmp_path):
    canonical = {
        "title": "Python Backend",
        "summary": "Backend developer building APIs.",
        "skills": ["Python", "FastAPI"],
        "experience": ["Built billing APIs."],
    }
    path = tmp_path / "resume.pdf"

    exported = export_pdf(canonical, path)
    imported = import_resume_file(path)

    assert exported["status"] == "exported"
    assert path.read_bytes().startswith(b"%PDF-1.4")
    assert imported["status"] == "imported"
    assert imported["source_format"] == "pdf"
    assert imported["canonical"]["title"] == "Python Backend"
    assert imported["canonical"]["skills"] == ["fastapi", "python"]
    assert "Built billing APIs" in imported["body"]


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
