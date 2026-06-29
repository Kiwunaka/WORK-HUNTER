from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from work_hunter.config import default_config
from work_hunter.letters import chat_completion, draft_cover_letter_ai
from work_hunter.models import Job


def test_direct_backend_uses_openai_compatible_request(monkeypatch):
    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "direct ok"}}]}

        return Response()

    monkeypatch.setattr("requests.post", fake_post)

    result = chat_completion(
        [{"role": "user", "content": "hello"}],
        {
            "backend": "direct",
            "api_key": "key",
            "base_url": "https://llm.example/v1/chat/completions",
            "model": "provider/model",
        },
    )

    assert result == "direct ok"
    assert calls[0]["json"]["model"] == "provider/model"
    assert calls[0]["headers"]["Authorization"] == "Bearer key"


def test_opencode_cli_backend_extracts_text_from_json_events(monkeypatch):
    calls = []

    def fake_run(command, *, capture_output, timeout, check):
        calls.append(command)
        stdout = "\n".join(
            [
                json.dumps({"type": "session.created", "id": "s1"}),
                json.dumps({"type": "message.part", "part": {"type": "text", "text": "opencode ok"}}),
            ]
        )
        return SimpleNamespace(returncode=0, stdout=stdout.encode("utf-8"), stderr=b"")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = chat_completion(
        [{"role": "user", "content": "write letter"}],
        {
            "backend": "opencode",
            "opencode_transport": "cli",
            "opencode_command": "opencode",
            "opencode_agent": "work-hunter-ai",
            "opencode_model": "openai/gpt-test",
        },
    )

    assert result == "opencode ok"
    assert calls
    command = calls[0]
    assert command[:3] == ["opencode", "run", "--format"]
    assert "--agent" in command
    assert "work-hunter-ai" in command
    assert "--model" in command
    assert "openai/gpt-test" in command


def test_opencode_server_transport_falls_back_to_cli_when_unavailable(monkeypatch):
    def fake_get(*args, **kwargs):
        raise OSError("server down")

    def fake_run(command, *, capture_output, timeout, check):
        return SimpleNamespace(returncode=0, stdout=b"plain cli answer", stderr=b"")

    monkeypatch.setattr("requests.get", fake_get)
    monkeypatch.setattr("subprocess.run", fake_run)

    result = chat_completion(
        [{"role": "user", "content": "hello"}],
        {
            "backend": "opencode",
            "opencode_transport": "server",
            "opencode_command": "opencode",
            "opencode_server_url": "http://127.0.0.1:4096",
            "opencode_agent": "work-hunter-ai",
        },
    )

    assert result == "plain cli answer"


def test_opencode_backend_reports_invalid_json_without_silently_succeeding(monkeypatch):
    def fake_run(command, *, capture_output, timeout, check):
        return SimpleNamespace(returncode=0, stdout=b'{"type":"message.part","part":{"type":"tool"}}', stderr=b"")

    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(ValueError, match="empty"):
        chat_completion(
            [{"role": "user", "content": "hello"}],
            {
                "backend": "opencode",
                "opencode_transport": "cli",
                "opencode_command": "opencode",
            },
        )


def test_codex_backend_uses_codex_cli_with_subscription(monkeypatch):
    calls = []

    def fake_run(command, *, capture_output, timeout, check):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=b"codex ok", stderr=b"")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = chat_completion(
        [{"role": "user", "content": "write letter"}],
        {
            "backend": "codex",
            "codex_command": "codex",
            "codex_model": "gpt-5.5",
            "codex_reasoning": "high",
        },
    )

    assert result == "codex ok"
    command = calls[0]
    assert command[:2] == ["codex", "exec"]
    assert "--model" in command and "gpt-5.5" in command
    assert "--reasoning" in command and "high" in command


def test_codex_backend_does_not_require_api_key():
    from work_hunter.ai import backend_requires_api_key

    assert backend_requires_api_key({"backend": "codex"}) is False
    assert backend_requires_api_key({"backend": "codex_server"}) is False
    assert backend_requires_api_key({"backend": "direct"}) is True
    assert backend_requires_api_key(default_config()["ai"]) is False


def test_default_route_drives_chat_completion_when_legacy_direct_is_empty(monkeypatch):
    calls = []

    def fake_run(command, *, capture_output, timeout, check):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=b"default route ok", stderr=b"")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = chat_completion(
        [{"role": "user", "content": "hello"}],
        default_config()["ai"],
    )

    assert result == "default route ok"
    assert calls[0][:2] == ["codex", "exec"]
    assert "--model" in calls[0]
    assert "gpt-5.5" in calls[0]


def test_codex_appserver_collect_drives_thread_and_collects_deltas():
    from work_hunter.ai.runtime import _codex_appserver_collect

    sent = []
    incoming = [
        {"id": 0, "result": {}},
        {"id": 1, "result": {"thread": {"id": "thr_1"}}},
        {"method": "item/agentMessage/delta", "params": {"delta": "Hello "}},
        {"method": "item/agentMessage/delta", "params": {"delta": "world"}},
        {"method": "turn/completed", "params": {}},
    ]

    result = _codex_appserver_collect(
        "summarize", send=lambda m: sent.append(m), incoming=iter(incoming), model="gpt-5.5"
    )

    assert result == "Hello world"
    assert [m.get("method") for m in sent] == ["initialize", "initialized", "thread/start", "turn/start"]
    assert sent[2]["params"]["model"] == "gpt-5.5"
    assert sent[-1]["params"]["threadId"] == "thr_1"
    assert sent[-1]["params"]["input"][0]["text"] == "summarize"


def test_codex_appserver_collect_falls_back_to_completed_item_and_raises_on_error():
    from work_hunter.ai.runtime import _codex_appserver_collect

    incoming = [
        {"id": 1, "result": {"thread": {"id": "t"}}},
        {"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "final answer"}}},
        {"method": "turn/completed", "params": {}},
    ]
    assert _codex_appserver_collect("hi", send=lambda m: None, incoming=iter(incoming)) == "final answer"

    failing = [{"id": 1, "error": {"message": "boom"}}]
    with pytest.raises(ValueError, match="codex app-server error"):
        _codex_appserver_collect("hi", send=lambda m: None, incoming=iter(failing))


def test_ai_cover_letter_uses_opencode_without_direct_api_key(monkeypatch):
    calls = []

    def fake_chat(messages, ai_config):
        calls.append({"messages": messages, "ai_config": ai_config})
        return "opencode letter"

    monkeypatch.setattr("work_hunter.letters.chat_completion", fake_chat)

    result = draft_cover_letter_ai(
        Job(source="hh", source_id="1", url="u", title="Python", company="Acme"),
        {"name": "Candidate"},
        {"summary": "Python dev", "all_skills": ["Python"], "experience": []},
        {"backend": "opencode", "opencode_command": "opencode"},
    )

    assert result == "opencode letter"
    assert calls[0]["ai_config"]["backend"] == "opencode"


def test_ai_runtime_registry_package_matches_legacy_backend():
    from work_hunter import ai_backends
    from work_hunter.ai import AIRuntimeRegistry, build_ai_request, runtime_routes

    cfg = default_config()["ai"]
    registry = AIRuntimeRegistry(cfg)

    assert registry.routes()["smart"]["adapter"] == "codex_cli"
    assert registry.status() == ai_backends.ai_status(cfg)
    assert runtime_routes(cfg)["openrouter"]["adapter"] == "openrouter"

    dry_run = registry.test(route="smart", prompt="ping", dry_run=True)
    assert dry_run["status"] == "dry_run"
    assert dry_run["request"] == build_ai_request(cfg["routes"]["smart"], prompt="ping")
    assert dry_run["request"]["auth"] == "external_runtime"
