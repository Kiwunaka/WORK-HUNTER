"""Require explicit missing-fact reporting for employer replies."""
from __future__ import annotations

from typing import Any

from .candidate_fit import fact_values
from .llm.structured import StructuredOutputSchema, extract_json_object, validate_output

REPLY_INSTRUCTION = (
    "\nОтвет верни только JSON: {\"answer\": \"текст ответа\", "
    "\"missing_facts\": [\"что нужно уточнить у кандидата\"], "
    "\"evidence\": [\"точные цитаты из фактов кандидата\"]}. "
    "Если вопрос требует неизвестных фактов, согласия или решения о времени встречи, "
    "answer оставь пустым и заполни missing_facts. Не угадывай и не соглашайся за кандидата. "
    "Для утверждений об опыте нужны точные цитаты evidence. Предпочтения поиска не являются опытом. "
    "Для приветствия без фактических утверждений evidence может быть пустым."
)


class MissingCandidateFacts(ValueError):
    pass


def parse_candidate_reply(raw: str, facts: dict[str, Any]) -> str:
    parsed = extract_json_object(raw)
    validate_output(parsed, StructuredOutputSchema("candidate_reply_v1", {
        "type": "object", "required": ["answer", "missing_facts", "evidence"], "properties": {
            "answer": {"type": "string"},
            "missing_facts": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "array", "items": {"type": "string"}},
        },
    }))
    if parsed["missing_facts"]:
        raise MissingCandidateFacts("; ".join(parsed["missing_facts"]))
    values = fact_values(facts)
    if any(not quote.strip() or not any(quote in value for value in values) for quote in parsed["evidence"]):
        raise MissingCandidateFacts("Ответ содержит утверждения вне базы фактов")
    if not parsed["answer"].strip():
        raise MissingCandidateFacts("Нужен ответ кандидата")
    return parsed["answer"].strip()
