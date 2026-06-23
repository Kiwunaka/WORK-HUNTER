from __future__ import annotations

import json

from work_hunter.agents.orchestrator import agent_orchestrator_policy
from work_hunter.agents.audit import build_agent_audit_record
from work_hunter.agents.merge_guard import guard_patch_wave_scope
from work_hunter.agents.task_splitter import route_task_to_wave
from work_hunter.agents.wave_planner import plan_agent_waves
from work_hunter.cli import main as cli_main
from work_hunter.services import WorkHunter


def test_agent_orchestrator_policy_captures_required_waves():
    policy = agent_orchestrator_policy()
    waves = {wave["name"]: wave for wave in policy["waves"]}

    smart = waves["smart-wave"]
    assert smart["max_agents"] == 5
    assert smart["model"] == "gpt-5.5"
    assert smart["reasoning"] == "high"
    assert smart["allowed_tasks"] == [
        "architecture",
        "source adapter design",
        "browser flow design",
        "campaign policy",
        "resume/onboarding logic",
        "UI/UX planning",
        "safety review",
        "test design",
    ]

    patch = waves["patch-wave"]
    assert patch["max_agents"] == 2
    assert patch["model"] == "gpt-5.3-codex-spark"
    assert patch["reasoning"] == "high"
    assert patch["allowed_tasks"] == [
        "mechanical refactor",
        "rename fields",
        "update docs",
        "add repetitive tests",
        "migrate config keys",
    ]
    assert patch["forbidden_paths"] == [
        "work_hunter/browser/session_store.py",
        "work_hunter/ai/redaction.py",
        "work_hunter/campaigns/policy.py",
        "work_hunter/sources/*/apply*.py",
        "work_hunter/storage.py migrations",
    ]
    assert patch["forbidden_topics"] == [
        "secrets",
        "auth",
        "OAuth",
        "cookies",
        "real apply",
        "payment",
        "browser session",
    ]

    assert policy["repo_skills"] == [
        "work-hunter-ops",
        "work-hiring",
        "candidate-memory",
        "resume-ats",
        "source-adapter",
        "browser-session-lab",
        "campaign-policy",
        "replay-timeline",
        "ui-smoke",
        "secret-redaction",
    ]


def test_init_refresh_docs_generates_repo_local_agent_assets(tmp_path, capsys):
    root = tmp_path / "project"

    cli_main(["--root", str(root), "init", "--check", "--json", "--refresh-docs"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "ok"
    assert payload["agents"]["status"] == "ready"
    expected = [
        root / "AGENTS.md",
        root / ".agents" / "skills" / "work-hunter-ops" / "SKILL.md",
        root / ".agents" / "skills" / "secret-redaction" / "SKILL.md",
        root / ".codex" / "agents" / "smart-wave.md",
        root / ".codex" / "agents" / "patch-wave.md",
        root / ".opencode" / "agents" / "work-hunter-ai.md",
    ]
    for path in expected:
        assert path.exists(), path
        content = path.read_text(encoding="utf-8")
        assert "Bearer" not in content
        assert "TELEGRAM_INIT_DATA" not in content

    agents_md = (root / "AGENTS.md").read_text(encoding="utf-8")
    smart = (root / ".codex" / "agents" / "smart-wave.md").read_text(encoding="utf-8")
    patch = (root / ".codex" / "agents" / "patch-wave.md").read_text(encoding="utf-8")

    assert "Never read Codex/OpenCode auth cache files" in agents_md
    assert "max_agents: 5" in smart
    assert "model: gpt-5.5" in smart
    assert "reasoning: high" in smart
    assert "max_agents: 2" in patch
    assert "model: gpt-5.3-codex-spark" in patch
    assert "work_hunter/sources/*/apply*.py" in patch
    assert "browser session" in patch


def test_init_dry_run_reports_agent_assets_without_writing(tmp_path, capsys):
    root = tmp_path / "project"

    cli_main(["--root", str(root), "init", "--json", "--dry-run", "--refresh-docs"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["agents"]["status"] == "preview"
    assert ".agents/skills/campaign-policy/SKILL.md" in payload["agents"]["proposed_files"]
    assert ".codex/agents/patch-wave.md" in payload["agents"]["proposed_files"]
    assert not (root / "AGENTS.md").exists()


def test_wave_planner_routes_exact_task_types_and_chunks_by_limits():
    tasks = [
        {"id": f"smart-{index}", "task_type": "architecture"}
        for index in range(6)
    ] + [
        {"id": f"patch-{index}", "task_type": "mechanical refactor"}
        for index in range(3)
    ]

    plan = plan_agent_waves(tasks)

    assert [wave["name"] for wave in plan["waves"]] == [
        "smart-wave",
        "smart-wave",
        "patch-wave",
        "patch-wave",
    ]
    assert [len(wave["tasks"]) for wave in plan["waves"]] == [5, 1, 2, 1]
    assert plan["waves"][0]["model"] == "gpt-5.5"
    assert plan["waves"][2]["model"] == "gpt-5.3-codex-spark"

    assert route_task_to_wave(" source adapter design ") == "smart-wave"
    assert route_task_to_wave("rename fields") == "patch-wave"
    assert route_task_to_wave("real apply policy") is None
    assert route_task_to_wave("auth design") is None
    assert route_task_to_wave("cookie review") is None


def test_patch_wave_merge_guard_blocks_forbidden_paths_topics_and_traversal():
    blocked = guard_patch_wave_scope(
        paths=[
            "work_hunter\\browser\\session_store.py",
            "work_hunter/sources/hh/apply_executor.py",
            "../work_hunter/sources/hh/apply.py",
            "work_hunter/storage.py",
        ],
        prompt="mechanical refactor for browser session cleanup",
        content_by_path={"work_hunter/storage.py": "ALTER TABLE jobs ADD COLUMN migrated INTEGER"},
    )

    assert blocked["allowed"] is False
    assert "forbidden_path:work_hunter/browser/session_store.py" in blocked["reasons"]
    assert "forbidden_glob:work_hunter/sources/*/apply*.py" in blocked["reasons"]
    assert "path_traversal" in blocked["reasons"]
    assert "storage_migration" in blocked["reasons"]
    assert "forbidden_topic:browser session" in blocked["reasons"]

    allowed = guard_patch_wave_scope(
        paths=["docs/product/README.md"],
        prompt="update docs",
        content_by_path={"docs/product/README.md": "Plain docs update."},
    )
    assert allowed == {"allowed": True, "reasons": []}


def test_agent_audit_record_masks_sensitive_text():
    record = build_agent_audit_record(
        wave="smart-wave",
        task_type="safety review",
        model="gpt-5.5",
        reasoning="high",
        decision="blocked",
        prompt="Authorization: Bearer raw-token\nCookie: sid=secret\nclient_secret=abc",
        output="visit https://example.test/cb?access_token=secret&refresh_token=other",
        reasons=["forbidden_topic:auth"],
    )
    serialized = json.dumps(record, ensure_ascii=False)

    assert record["wave"] == "smart-wave"
    assert record["decision"] == "blocked"
    assert "raw-token" not in serialized
    assert "sid=secret" not in serialized
    assert "client_secret=abc" not in serialized
    assert "access_token=secret" not in serialized
    assert "***" in serialized


def test_agent_run_storage_redacts_input_and_output(tmp_path):
    app = WorkHunter(root=tmp_path)

    run_id = app.storage.start_agent_run(
        "smart-wave",
        {
            "task_type": "safety review",
            "prompt": "Authorization: Bearer agent-secret",
        },
    )
    app.storage.finish_agent_run(
        run_id,
        status="blocked",
        output={"result": "access_token=agent-secret"},
    )
    runs = app.storage.list_agent_runs()
    serialized = json.dumps(runs, ensure_ascii=False)

    assert runs[0]["id"] == run_id
    assert runs[0]["agent_name"] == "smart-wave"
    assert runs[0]["status"] == "blocked"
    assert runs[0]["input"]["prompt"] == "Authorization: Bearer ***"
    assert runs[0]["output"]["result"] == "access_token=***"
    assert "agent-secret" not in serialized
