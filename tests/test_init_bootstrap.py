from __future__ import annotations

import json
import tomllib
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from work_hunter import services
from work_hunter.cli import main as cli_main
from work_hunter.ops_import.preview import build_ops_import_preview
from work_hunter.product_lock import build_product_lock_preview, redact_product_lock_text
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


PRODUCT_DOC_REQUIREMENTS = {
    "WORK_HUNTER_VISION.md": [
        "private Windows-only local-first command center",
        "real auto-apply",
        "onboarding",
        "interview prep",
    ],
    "REQUIREMENTS.md": [
        "Windows 10/11",
        "127.0.0.1",
        "Codex/OpenCode auth cache files",
        "confirmed facts",
    ],
    "SOURCE_PRIORITY.md": [
        "HH",
        "GeekJob",
        "Habr",
        "Getmatch",
        "hirehi",
        "careerspace",
        "jabka.work",
    ],
    "SAFETY_CONTRACT.md": [
        "Non-certified source adapters cannot auto-apply",
        "Unknown forms",
        "campaign policy",
        "kill switch",
    ],
}


def test_pyproject_declares_requests_runtime_dependency():
    text = open("pyproject.toml", encoding="utf-8").read()

    assert '"requests' in text


def test_pyproject_declares_browser_and_dev_extras():
    pyproject = tomllib.loads(open("pyproject.toml", encoding="utf-8").read())
    extras = pyproject["project"]["optional-dependencies"]

    assert any(dependency.startswith("playwright") for dependency in extras["browser"])
    assert any(dependency.startswith("pytest") for dependency in extras["dev"])


def test_product_lock_docs_are_checked_in_and_generator_preserves_contract(tmp_path):
    app = WorkHunter(root=tmp_path)
    generated = app.init_report(refresh_docs=True)
    repo_product_dir = Path(__file__).resolve().parents[1] / "docs" / "product"

    assert sorted(Path(path).name for path in generated["docs"]) == sorted(PRODUCT_DOC_REQUIREMENTS)
    for file_name, required_fragments in PRODUCT_DOC_REQUIREMENTS.items():
        repo_doc = repo_product_dir / file_name
        generated_doc = tmp_path / "docs" / "product" / file_name

        assert repo_doc.exists(), f"{file_name} must be checked in for Phase 0 product lock"
        repo_text = repo_doc.read_text(encoding="utf-8")
        generated_text = generated_doc.read_text(encoding="utf-8")
        for fragment in required_fragments:
            assert fragment in repo_text
            assert fragment in generated_text


def test_readiness_uses_chromium_probe_for_browser_status(tmp_path, monkeypatch):
    monkeypatch.setattr(
        services,
        "_playwright_chromium_status",
        lambda: {"status": "ok", "executable": "C:/Browsers/chromium.exe"},
        raising=False,
    )

    report = WorkHunter(root=tmp_path).init_report(check=True)["readiness"]

    assert report["browser"]["chromium"] == {"status": "ok", "executable": "C:/Browsers/chromium.exe"}


def test_doctor_json_reports_windows_foundation_without_auth_cache_reads(tmp_path, capsys, monkeypatch):
    attempted_reads: list[str] = []
    original_read_text = type(tmp_path).read_text

    def guarded_read_text(self, *args, **kwargs):
        path = str(self)
        if ".codex" in path or "opencode" in path or "auth.json" in path:
            attempted_reads.append(path)
            raise AssertionError(f"auth/cache read attempted: {path}")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "read_text", guarded_read_text)

    cli_main(["--root", str(tmp_path), "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert attempted_reads == []
    assert payload["status"] == "ok"
    assert payload["windows_only"] is True
    assert payload["checks"]["python"]["status"] == "ok"
    assert payload["checks"]["sqlite"]["status"] == "ok"
    assert payload["checks"]["ui_bind"]["host"] == "127.0.0.1"


def test_init_force_writes_merged_config_and_readiness_dashboard(tmp_path, capsys):
    root = tmp_path / "project"
    config_dir = root / ".work-hunter"
    config_dir.mkdir(parents=True)
    (config_dir / "work_hunter_config.json").write_text(
        json.dumps(
            {
                "ai": {
                    "routes": {
                        "openrouter": {
                            "adapter": "openrouter",
                            "base_url": "https://openrouter.ai/api/v1/chat/completions",
                            "api_key": "sk-keep",
                            "model": "openrouter/test",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    cli_main(["--root", str(root), "init", "--check", "--json", "--force", "--with-ai", "--with-browser"])
    payload = json.loads(capsys.readouterr().out)
    saved = json.loads((config_dir / "work_hunter_config.json").read_text(encoding="utf-8"))

    assert payload["status"] == "ok"
    assert payload["force"] is True
    assert payload["readiness"]["environment"]["windows_only"] is True
    assert payload["readiness"]["environment"]["python"] == "ok"
    assert payload["readiness"]["environment"]["sqlite"] == "ok"
    assert payload["readiness"]["ai_runtime"]["routes"]["smart"]["model"] == "gpt-5.5"
    assert payload["readiness"]["ai_runtime"]["routes"]["openrouter"]["has_api_key"] is True
    assert payload["readiness"]["browser"]["profile_root"].endswith("browser-profiles")
    assert payload["readiness"]["candidate"]["status"] in {"complete", "incomplete"}
    assert "hh" in payload["readiness"]["sources"]
    assert payload["readiness"]["safety"]["redaction_scan"] == "available"
    assert payload["readiness"]["safety"]["kill_switch"] == "active"
    assert payload["readiness"]["implementation"]["source_campaign_apply"]["status"] == "ready_for_certified_sources"
    assert "external_apply_evidence_gate" in payload["readiness"]["implementation"]["source_campaign_apply"]["ready"]
    assert "source_certification_audit" in payload["readiness"]["implementation"]["source_campaign_apply"]["ready"]
    assert "source_certification_promotion" in payload["readiness"]["implementation"]["source_campaign_apply"]["ready"]
    assert "source_certification_matrix" in payload["readiness"]["implementation"]["source_campaign_apply"]["ready"]
    assert "source_external_apply_target_config" in payload["readiness"]["implementation"]["source_campaign_apply"]["ready"]
    assert "non_hh_campaign_runner_missing" not in payload["readiness"]["implementation"]["source_campaign_apply"]["gaps"]
    assert payload["readiness"]["implementation"]["source_campaign_apply"]["gaps"] == []
    assert "per_source_certification_required" in payload["readiness"]["implementation"]["source_campaign_apply"]["operational_requirements"]
    assert payload["readiness"]["implementation"]["browser_session_lab"]["status"] == "ready"
    assert "playwright_fill_screenshot_executor_not_wired" not in payload["readiness"]["implementation"]["browser_session_lab"]["gaps"]
    assert payload["readiness"]["implementation"]["ai_runtime"]["status"] == "ready"
    assert payload["readiness"]["implementation"]["ai_runtime"]["gaps"] == []
    assert payload["readiness"]["implementation"]["resume_exports"]["status"] == "ready"
    assert payload["readiness"]["implementation"]["resume_exports"]["gaps"] == []
    assert saved["ai"]["routes"]["openrouter"]["api_key"] == "sk-keep"
    assert saved["sources"]["hh"]["enabled"] is True


def test_product_lock_preview_reads_allowlisted_docs_and_redacts_secret_text(tmp_path):
    source = _build_product_lock_source(tmp_path / "VPN")
    secret = source / "secrets" / "tokens.md"
    secret.parent.mkdir(parents=True)
    secret.write_text("Authorization: Bearer should-not-read", encoding="utf-8")

    preview = build_product_lock_preview(source, tmp_path / "target")
    serialized = json.dumps(preview, ensure_ascii=False)

    assert preview["status"] == "ok"
    assert "flow-state.md" in preview["read_files"]
    assert "secrets/tokens.md" not in serialized
    assert "Bearer" not in serialized
    assert "TELEGRAM_INIT_DATA" not in serialized
    assert "***" in serialized


def test_ops_import_preview_classifies_maps_and_diffs_redacted_assets(tmp_path):
    source = _build_product_lock_source(tmp_path / "VPN")
    target = tmp_path / "target"
    target.mkdir()
    (target / "AGENTS.md").write_text("# Old Agent Notes\n", encoding="utf-8")

    preview = build_ops_import_preview(source, target)
    serialized = json.dumps(preview, ensure_ascii=False)

    assert preview["status"] == "ok"
    assert preview["pipeline"] == ["scan", "classify", "redact", "map", "diff"]
    assert {item["kind"] for item in preview["classified_files"]} >= {"flow_state", "wo_template", "runbook"}
    assert "docs/ops/WO_TEMPLATE.md" in preview["proposed_files"]
    assert "docs/ops/FLOW_STATE_TEMPLATE.md" in preview["proposed_files"]
    assert "docs/ops/SMART_WAVE.md" in preview["proposed_files"]
    assert "docs/ops/PATCH_WAVE.md" in preview["proposed_files"]
    assert "docs/ops/REVIEW_CHECKLIST.md" in preview["proposed_files"]
    assert "--- AGENTS.md" in preview["diffs"]["AGENTS.md"]
    assert "+# Work Hunter Local Agent Context" in preview["diffs"]["AGENTS.md"]
    assert "Bearer" not in serialized
    assert "TELEGRAM_INIT_DATA" not in serialized
    assert "secrets/tokens.md" not in serialized
    assert "***" in serialized


def test_init_import_wo_generates_redacted_assets(tmp_path, capsys):
    source = _build_product_lock_source(tmp_path / "VPN")
    root = tmp_path / "project"

    cli_main(["--root", str(root), "init", "--json", "--import-wo", str(source)])
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "ok"
    assert payload["import_wo"]["status"] == "imported"
    expected = [
        root / "AGENTS.md",
        root / ".agents" / "skills" / "work-hunter" / "SKILL.md",
        root / ".codex" / "agents" / "work-hunter.md",
        root / ".opencode" / "agents" / "work-hunter-ai.md",
        root / "docs" / "developer" / "orchestration" / "FLOW_STATE.md",
        root / "docs" / "developer" / "orchestration" / "WO.template.md",
    ]
    for path in expected:
        assert path.exists(), path
        assert "Bearer" not in path.read_text(encoding="utf-8")
        assert "TELEGRAM_INIT_DATA" not in path.read_text(encoding="utf-8")


def test_init_import_wo_dry_run_does_not_write_generated_assets(tmp_path, capsys):
    source = _build_product_lock_source(tmp_path / "VPN")
    root = tmp_path / "project"

    cli_main(["--root", str(root), "init", "--json", "--dry-run", "--import-wo", str(source)])
    payload = json.loads(capsys.readouterr().out)

    assert payload["import_wo"]["status"] == "preview"
    assert not (root / "AGENTS.md").exists()
    assert payload["import_wo"]["proposed_files"]


def test_redact_product_lock_text_masks_cli_env_headers_and_urls():
    text = "\n".join(
        [
            "Authorization: Bearer token-value",
            "TELEGRAM_INIT_DATA=query_id=abc&hash=secret",
            "opencode --api-key sk-secret",
            "https://example.test/callback?access_token=secret&ok=1",
        ]
    )

    redacted = redact_product_lock_text(text)

    assert "token-value" not in redacted
    assert "query_id=abc" not in redacted
    assert "sk-secret" not in redacted
    assert "access_token=secret" not in redacted
    assert "***" in redacted


def test_web_api_doctor_and_import_wo_preview_apply(tmp_path):
    source = _build_product_lock_source(tmp_path / "VPN")
    root = tmp_path / "project"
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(root))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        doctor = _get_json(base, "/api/doctor")
        preview = _post_json(base, "/api/init/import-wo/preview", {"source": str(source)})
        applied = _post_json(base, "/api/init/import-wo", {"source": str(source)})
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert doctor["status"] == "ok"
    assert preview["status"] == "preview"
    assert not (root / "AGENTS.md").exists() or applied["status"] == "imported"
    assert applied["status"] == "imported"
    assert (root / "AGENTS.md").exists()
    assert "Bearer" not in json.dumps(preview, ensure_ascii=False)
    assert "TELEGRAM_INIT_DATA" not in json.dumps(applied, ensure_ascii=False)


def _build_product_lock_source(root):
    flow = root / "docs" / "developer" / "orchestration"
    templates = flow / "templates"
    work_orders = root / "docs" / "developer" / "work-orders"
    templates.mkdir(parents=True)
    work_orders.mkdir(parents=True)
    (flow / "flow-state.md").write_text(
        "FLOW_STATE\nAuthorization: Bearer secret\nTELEGRAM_INIT_DATA=query_id=abc",
        encoding="utf-8",
    )
    (flow / "wo-authoring-guide.md").write_text("WO guide\n--api-key sk-secret", encoding="utf-8")
    (templates / "WO.template.md").write_text("# WO Template\nCookie: sid=secret", encoding="utf-8")
    (work_orders / "README.md").write_text("# Work orders\nSet-Cookie: sid=secret", encoding="utf-8")
    return root


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
