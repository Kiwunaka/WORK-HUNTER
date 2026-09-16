from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import requests

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


def test_direct_backend_rejects_saved_secret_mask_before_request(monkeypatch):
    def unexpected_post(*args, **kwargs):
        pytest.fail("a masked credential must not be sent to the provider")

    monkeypatch.setattr("requests.post", unexpected_post)
    with pytest.raises(ValueError, match="AI config incomplete"):
        chat_completion(
            [{"role": "user", "content": "hello"}],
            {"backend": "direct", "api_key": "***",
             "base_url": "https://llm.example/v1/chat/completions", "model": "provider/model"},
        )


def test_model_scopes_and_openrouter_provider_allowlist(tmp_path, monkeypatch):
    from work_hunter.services import WorkHunter

    calls = []
    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {"choices": [{"message": {"content": "ok"}}]})
    monkeypatch.setattr("requests.post", post)
    app = WorkHunter(tmp_path)
    app.config["ai"].update(backend="direct", api_key="fixture-key",
        base_url="https://openrouter.ai/api/v1/chat/completions", model="openai/gpt-5.6-luna",
        ranking={"model": "z-ai/glm-5.3-flash"},
        reasoning={"effort": "low"},
        model_providers={"z-ai/glm-5.3-flash": ["novita", "z-ai", "streamlake", "modal"]})
    try:
        chat_completion([], app.ai_config("ranking"))
        chat_completion([], app.ai_config("interview"))
        assert calls[0]["model"] == "z-ai/glm-5.3-flash"
        assert calls[0]["provider"] == {"only": ["novita", "z-ai", "streamlake", "modal"]}
        assert calls[0]["reasoning"] == {"effort": "low"}
        assert calls[1]["model"] == "openai/gpt-5.6-luna" and "provider" not in calls[1]
        app.config["ai"]["model_providers"]["z-ai/glm-5.3-flash"] = []
        with pytest.raises(ValueError, match="allowlist"):
            chat_completion([], app.ai_config("ranking"))
        assert len(calls) == 2
    finally:
        app.storage.close()


@pytest.mark.parametrize("status,body,uses_reserve", [
    (200, "", False),
    (404, "No endpoints found matching your price limits", True),
    (400, "meta/muse is not a valid model ID", True),
    (503, "No available provider", True),
    (400, "Invalid reasoning", False),
    (401, "Invalid key", False),
    (402, "Insufficient credits", False),
    (403, "Forbidden", False),
])
def test_model_price_cap_and_single_reserve(monkeypatch, status, body, uses_reserve):
    calls, recorded = [], []
    config = {
        "api_key": "fixture-key", "base_url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "meta/muse", "reasoning": {"effort": "high"},
        "model_fallbacks": {"meta/muse": "deepseek/flash"},
        "model_max_prices": {"meta/muse": {"prompt": 0.1, "completion": 0.2}},
        "model_providers": {"meta/muse": ["meta"], "deepseek/flash": ["deepseek"]},
        "_usage_recorder": recorded.append,
    }

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        response = requests.Response()
        response.status_code = status if len(calls) == 1 else 200
        response._content = (body if response.status_code != 200 else json.dumps({
            "model": kwargs["json"]["model"],
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"cost": 0.001},
        })).encode()
        return response

    monkeypatch.setattr("requests.post", post)
    if status == 200 or uses_reserve:
        assert chat_completion([{"role": "user", "content": "hello"}], config) == "ok"
    else:
        with pytest.raises(requests.HTTPError):
            chat_completion([{"role": "user", "content": "hello"}], config)
    assert calls[0]["provider"] == {
        "only": ["meta"], "max_price": {"prompt": 0.1, "completion": 0.2},
    }
    assert len(calls) == (2 if uses_reserve else 1)
    if uses_reserve:
        assert calls[1]["model"] == "deepseek/flash"
        assert calls[1]["provider"] == {"only": ["deepseek"]}
        assert calls[1]["reasoning"] == {"effort": "high"}
        assert recorded[0]["model"] == "deepseek/flash"
        assert recorded[0]["cost_usd"] == 0.001
    assert config["model"] == "meta/muse"


def test_failed_reserve_is_not_retried_and_images_are_not_sent_to_text_reserve(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs["json"]["model"])
        response = requests.Response()
        response.status_code = 404
        response._content = b"No endpoints"
        return response

    monkeypatch.setattr("requests.post", post)
    config = {"api_key": "key", "base_url": "https://openrouter.ai/api/v1/chat/completions",
              "model": "primary", "model_fallbacks": {"primary": "reserve", "reserve": "primary"}}
    with pytest.raises(requests.HTTPError):
        chat_completion([{"role": "user", "content": "hello"}], config)
    assert calls == ["primary", "reserve"]
    calls.clear()
    with pytest.raises(requests.HTTPError):
        chat_completion([{"role": "user", "content": [{"type": "image_url"}]}], config)
    assert calls == ["primary"]


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


def test_opencode_server_failure_does_not_silently_repeat_in_cli(monkeypatch):
    def fake_get(*args, **kwargs):
        raise OSError("server down")

    def fake_run(command, *, capture_output, timeout, check):
        pytest.fail("A failed server request must not trigger a second runtime")

    monkeypatch.setattr("requests.get", fake_get)
    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(OSError, match="server down"):
        chat_completion([{"role": "user", "content": "hello"}],
                        {"backend": "opencode", "opencode_transport": "server"})


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
