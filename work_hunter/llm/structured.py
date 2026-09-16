from __future__ import annotations

import copy
import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from ..ai_backends import chat_completion


class StructuredLLMError(RuntimeError):
    pass


class StructuredParseError(StructuredLLMError):
    pass


@dataclass(frozen=True)
class StructuredOutputSchema:
    name: str
    schema: dict[str, Any]
    strict: bool = True

    def to_prompt(self) -> str:
        strict_note = "Return only valid JSON." if self.strict else "Prefer valid JSON."
        return (
            f"You must respond with JSON for schema `{self.name}`.\n"
            f"{strict_note}\n"
            f"JSON schema:\n{json.dumps(self.schema, ensure_ascii=False, sort_keys=True)}"
        )


@dataclass
class StructuredReply:
    content: str
    parsed: dict[str, Any]
    model: str = ""
    attempts: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def send_structured_chat(
    messages: list[dict[str, str]],
    ai_config: dict[str, Any],
    schema: StructuredOutputSchema,
    *,
    completion: Callable[[list[dict[str, str]], dict[str, Any]], str] = chat_completion,
    max_retries: int | None = None,
) -> StructuredReply:
    attempts = min(3, max(1, int(max_retries if max_retries is not None else ai_config.get("max_retries", 1))))
    deadline = time.monotonic() + min(120, max(1, float(ai_config.get("structured_budget_seconds", 60))))
    last_error: Exception | None = None
    augmented_messages = _with_schema_instruction(messages, schema)
    for attempt in range(1, attempts + 1):
        try:
            content = completion(augmented_messages, ai_config)
            parsed = extract_json_object(content)
            validate_output(parsed, schema)
            return StructuredReply(
                content=content,
                parsed=parsed,
                model=str(ai_config.get("model") or ai_config.get("opencode_model") or ""),
                attempts=attempt,
                metadata={
                    "schema": schema.name,
                    "backend": str(ai_config.get("backend") or "direct"),
                },
            )
        except StructuredParseError as ex:
            last_error = ex
        except Exception as ex:
            last_error = ex
            if not _is_retryable_error(ex):
                break
        if attempt < attempts:
            delay = min(5, max(0, float(ai_config.get("retry_base_seconds", 0))))
            if time.monotonic() + delay >= deadline:
                break
            time.sleep(delay)
    raise StructuredLLMError(f"Structured chat failed after {attempt} attempt(s): {type(last_error).__name__}") from last_error


def validate_output(value: Any, schema: StructuredOutputSchema) -> None:
    specification = copy.deepcopy(schema.schema)

    def strict_objects(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                node.setdefault("additionalProperties", False)
            for child in node.values():
                strict_objects(child)
        elif isinstance(node, list):
            for child in node:
                strict_objects(child)

    if schema.strict:
        strict_objects(specification)
    Draft202012Validator.check_schema(specification)
    try:
        Draft202012Validator(specification).validate(value)
    except ValidationError as exc:
        # Do not echo candidate data or malicious model text into diagnostics.
        raise StructuredParseError(f"Response violates {schema.name}: {exc.validator}") from exc


def extract_json_object(content: str) -> dict[str, Any]:
    def invalid_constant(value: str) -> None:
        raise StructuredParseError("Non-finite JSON number")

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise StructuredParseError("Duplicate JSON key")
            result[key] = value
        return result

    for candidate in _json_candidates(content):
        try:
            parsed = json.loads(candidate, parse_constant=invalid_constant, object_pairs_hook=unique_pairs)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        raise StructuredParseError("LLM response must be an object")
    raise StructuredParseError("LLM response did not contain a JSON object")


def _with_schema_instruction(
    messages: list[dict[str, str]],
    schema: StructuredOutputSchema,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": schema.to_prompt()},
        *messages,
    ]


def _json_candidates(content: str) -> list[str]:
    stripped = content.strip()
    candidates = [stripped]
    for match in re.finditer(r"```(?:json)?\s*(?P<body>.*?)```", content, flags=re.DOTALL | re.IGNORECASE):
        candidates.insert(0, match.group("body").strip())
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        candidates.append(stripped[start : end + 1])
    return candidates


def _is_retryable_error(ex: Exception) -> bool:
    text = str(ex).lower()
    return "429" in text or "rate" in text or "timeout" in text or "temporarily" in text
