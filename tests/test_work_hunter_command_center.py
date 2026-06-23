from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.cli import main as cli_main
from work_hunter.config import default_config
from work_hunter.models import Job, Resume
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


def test_default_config_exposes_ai_runtime_registry_routes():
    cfg = default_config()

    assert cfg["ai"]["default_route"] == "smart"
    assert cfg["ai"]["routes"]["smart"]["adapter"] == "codex_cli"
    assert cfg["ai"]["routes"]["patcher"]["adapter"] == "codex_cli"
    assert cfg["ai"]["routes"]["codex_sdk"]["adapter"] == "codex_sdk"
    assert cfg["ai"]["routes"]["codex_sdk"]["experimental"] is True
    assert cfg["ai"]["routes"]["codex_sdk"]["enabled"] is False
    assert cfg["ai"]["routes"]["opencode_go"]["adapter"] == "opencode_cli"
    assert cfg["ai"]["routes"]["openrouter"]["adapter"] == "openrouter"
    assert cfg["ai"]["routes"]["fireworks"]["adapter"] == "fireworks"


def test_ai_status_and_test_never_read_external_auth_cache(tmp_path, monkeypatch):
    attempted_reads: list[str] = []

    def forbidden_read_text(self, *args, **kwargs):
        path = str(self)
        if ".codex" in path or "opencode" in path:
            attempted_reads.append(path)
            raise AssertionError(f"auth cache read attempted: {path}")
        return original_read_text(self, *args, **kwargs)

    original_read_text = type(tmp_path).read_text
    monkeypatch.setattr(type(tmp_path), "read_text", forbidden_read_text)

    app = WorkHunter(root=tmp_path)
    status = app.ai_status()
    dry_run = app.ai_test(route="smart", prompt="hello", dry_run=True)

    assert attempted_reads == []
    assert status["routes"]["smart"]["auth"] == "external_runtime"
    assert status["routes"]["openrouter"]["ready"] is False
    assert dry_run["status"] == "dry_run"
    assert dry_run["request"]["adapter"] == "codex_cli"
    assert "auth.json" not in json.dumps(dry_run, ensure_ascii=False)


def test_ai_route_payloads_cover_codex_openrouter_and_fireworks(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.config["ai"]["routes"]["openrouter"]["api_key"] = "sk-openrouter"
    app.config["ai"]["routes"]["openrouter"]["model"] = "openrouter/model"
    app.config["ai"]["routes"]["fireworks"]["api_key"] = "fw-key"
    app.config["ai"]["routes"]["fireworks"]["model"] = "accounts/fireworks/models/test"

    codex = app.ai_test(route="smart", prompt="inspect", dry_run=True)
    openrouter = app.ai_test(route="openrouter", prompt="inspect", dry_run=True)
    fireworks = app.ai_test(route="fireworks", prompt="inspect", dry_run=True)

    assert codex["request"]["command"][:2] == ["codex", "exec"]
    assert "--model" in codex["request"]["command"]
    assert openrouter["request"]["payload"]["model"] == "openrouter/model"
    assert openrouter["request"]["headers"]["Authorization"] == "***"
    assert "openrouter.ai/api/v1/chat/completions" in openrouter["request"]["url"]
    assert fireworks["request"]["payload"]["model"] == "accounts/fireworks/models/test"
    assert fireworks["request"]["headers"]["Authorization"] == "***"
    assert "api.fireworks.ai/inference/v1" in fireworks["request"]["url"]


def test_ai_runtime_exposes_codex_sdk_as_disabled_experimental_route(tmp_path):
    app = WorkHunter(root=tmp_path)

    status = app.ai_status()["routes"]["codex_sdk"]
    dry_run = app.ai_test(route="codex_sdk", prompt="inspect", dry_run=True)

    assert status["adapter"] == "codex_sdk"
    assert status["auth"] == "external_runtime"
    assert status["ready"] is False
    assert dry_run["request"]["adapter"] == "codex_sdk"
    assert dry_run["request"]["experimental"] is True
    assert dry_run["request"]["enabled"] is False


def test_init_check_json_refreshes_product_docs_and_preserves_masked_secrets(tmp_path, capsys):
    app = WorkHunter(root=tmp_path)
    app.init()
    app.config["ai"]["routes"]["openrouter"]["api_key"] = "real-key"
    app.save_config(app.config)

    masked = app.config.copy()
    masked["ai"] = dict(app.config["ai"])
    masked["ai"]["routes"] = json.loads(json.dumps(app.config["ai"]["routes"]))
    masked["ai"]["routes"]["openrouter"]["api_key"] = "***"
    app.save_config(masked)

    cli_main(["--root", str(tmp_path), "init", "--check", "--json", "--refresh-docs", "--with-ai", "--with-browser"])
    payload = json.loads(capsys.readouterr().out)
    reloaded = WorkHunter(root=tmp_path)

    assert payload["status"] == "ok"
    assert payload["windows_only"] is True
    assert payload["ai"]["default_route"] == "smart"
    assert (tmp_path / "docs" / "product" / "WORK_HUNTER_VISION.md").exists()
    assert reloaded.config["ai"]["routes"]["openrouter"]["api_key"] == "real-key"


def test_candidate_fact_confirmation_controls_resume_variant_claims(tmp_path):
    app = WorkHunter(root=tmp_path)
    questions = app.onboarding_questions()

    answer = app.answer_onboarding(
        questions[0]["id"],
        "I build FastAPI services and PostgreSQL APIs.",
        source="manual_test",
    )
    job_id = app.storage.upsert_job(
        Job(
            source="geekjob",
            source_id="g1",
            url="https://geekjob.ru/vacancy/g1",
            title="FastAPI backend",
            company="Acme",
            description="FastAPI PostgreSQL",
        )
    )
    resume_id = app.storage.save_resume(
        Resume(name="Base", body="Python developer.", profile_id="default", is_active=True)
    )

    blocked = app.build_resume_variant(job_id, resume_id)
    app.confirm_candidate_fact(answer["facts"][0]["id"])
    variant = app.build_resume_variant(job_id, resume_id)

    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "no_confirmed_candidate_facts"
    assert variant["status"] == "ready"
    assert "FastAPI" in variant["body"]
    assert all(claim["status"] == "confirmed" for claim in variant["claims"])


def test_application_pack_policy_blocks_unknown_forms_and_records_replay(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(
        Job(
            source="habr",
            source_id="h1",
            url="https://career.habr.com/vacancies/1",
            title="Python",
            company="Acme",
            description="Unknown form and captcha",
        )
    )
    pack = app.build_application_pack(
        job_id,
        resume_variant={"id": "rv1", "body": "Python developer"},
        cover_letter="Hi",
        short_message="Hi",
        source_payload={"form_signature": "unknown", "captcha": True},
        campaign_policy={"enabled": True, "real_apply": True, "min_score": 0},
    )
    replay = app.replay_for_job(job_id)

    assert pack["policy_status"] == "blocked_manual_review"
    assert "unknown_form" in pack["policy_reasons"]
    assert "captcha_or_challenge" in pack["policy_reasons"]
    assert replay["events"][0]["event_type"] == "application_pack_built"
    assert "Authorization" not in json.dumps(replay, ensure_ascii=False)


def test_web_api_exposes_init_ai_onboarding_and_replay(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.init()
    job_id = app.storage.upsert_job(
        Job(source="hh", source_id="1", url="https://hh.ru/vacancy/1", title="Python")
    )
    app.record_replay_event(
        job_id=job_id,
        source="hh",
        event_type="policy_decision",
        title="Policy blocked",
        data={"headers": {"Authorization": "Bearer secret"}},
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        init_status = _get_json(base, "/api/init/status")
        ai_status = _get_json(base, "/api/ai/status")
        questions = _get_json(base, "/api/onboarding/questions")
        replay = _get_json(base, f"/api/replay/jobs/{job_id}")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert init_status["status"] == "ok"
    assert ai_status["routes"]["smart"]["adapter"] == "codex_cli"
    assert "experience" in {question["id"] for question in questions}
    assert replay["events"][0]["data"]["headers"]["Authorization"] == "***"


def _get_json(base: str, path: str):
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))
