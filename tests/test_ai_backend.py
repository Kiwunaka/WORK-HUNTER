from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

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
