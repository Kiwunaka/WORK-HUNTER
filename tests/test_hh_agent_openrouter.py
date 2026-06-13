from __future__ import annotations

import pytest

from work_hunter.llm.structured import (
    StructuredLLMError,
    StructuredOutputSchema,
    StructuredParseError,
    extract_json_object,
    send_structured_chat,
)


SCHEMA = StructuredOutputSchema(
    name="vacancy_analysis",
    schema={
        "type": "object",
        "properties": {
            "score": {"type": "integer"},
            "recommended_action": {"type": "string"},
        },
        "required": ["score", "recommended_action"],
    },
)


def test_extract_json_object_from_plain_and_fenced_content():
    assert extract_json_object('{"score": 90, "recommended_action": "apply"}')["score"] == 90
    assert extract_json_object('```json\n{"score": 42, "recommended_action": "skip"}\n```')["score"] == 42
    assert extract_json_object('text before {"score": 77, "recommended_action": "apply"} text after')["score"] == 77


def test_extract_json_object_rejects_invalid_content():
    with pytest.raises(StructuredParseError):
        extract_json_object("not json")


def test_send_structured_chat_adds_schema_instruction_and_metadata():
    calls = []

    def fake_completion(messages, ai_config):
        calls.append({"messages": messages, "ai_config": ai_config})
        return '{"score": 95, "recommended_action": "apply"}'

    reply = send_structured_chat(
        [{"role": "user", "content": "analyze"}],
        {"backend": "direct", "model": "test-model"},
        SCHEMA,
        completion=fake_completion,
    )

    assert reply.parsed["recommended_action"] == "apply"
    assert reply.model == "test-model"
    assert reply.metadata["schema"] == "vacancy_analysis"
    assert "JSON schema" in calls[0]["messages"][0]["content"]


def test_send_structured_chat_retries_parse_errors():
    attempts = []

    def fake_completion(messages, ai_config):
        attempts.append(1)
        if len(attempts) == 1:
            return "oops"
        return '{"score": 80, "recommended_action": "apply"}'

    reply = send_structured_chat(
        [{"role": "user", "content": "analyze"}],
        {"backend": "direct", "model": "test-model"},
        SCHEMA,
        completion=fake_completion,
        max_retries=2,
    )

    assert reply.attempts == 2
    assert len(attempts) == 2


def test_send_structured_chat_reports_final_failure():
    def fake_completion(messages, ai_config):
        return "oops"

    with pytest.raises(StructuredLLMError, match="failed after 2"):
        send_structured_chat(
            [{"role": "user", "content": "analyze"}],
            {"backend": "direct"},
            SCHEMA,
            completion=fake_completion,
            max_retries=2,
        )
