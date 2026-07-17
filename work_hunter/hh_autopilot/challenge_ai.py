from __future__ import annotations

import base64
import copy
import html
import json
import re
import time
from typing import Any, Callable, Mapping, Sequence

from work_hunter.ai_backends import chat_completion


class ChallengeAIError(RuntimeError):
    pass


Completion = Callable[[list[dict[str, Any]], dict[str, Any]], str]


class HHChallengeAI:
    """OpenAI-compatible answers for HH tests, forms and image CAPTCHA."""

    def __init__(
        self,
        config_provider: Callable[[], Mapping[str, Any]],
        *,
        completion: Completion = chat_completion,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not callable(config_provider):
            raise TypeError("config_provider must be callable")
        if not callable(completion):
            raise TypeError("completion must be callable")
        self._config_provider = config_provider
        self._completion = completion
        self._sleeper = sleeper

    def solve_captcha(self, image_data: bytes) -> str:
        if not isinstance(image_data, bytes) or not image_data:
            raise ChallengeAIError("CAPTCHA image is empty")
        config = self._config("captcha")
        encoded = base64.b64encode(image_data).decode("ascii")
        raw = self._complete(
            config,
            [
                {
                    "role": "system",
                    "content": str(
                        config.get("system_prompt")
                        or "Распознай текст на изображении и верни только его."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{encoded}"},
                        },
                        {
                            "type": "text",
                            "text": str(
                                config.get("prompt") or "Распознай текст CAPTCHA."
                            ),
                        },
                    ],
                },
            ],
        )
        token = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]", "", raw)
        if not token:
            raise ChallengeAIError("Vision model returned no CAPTCHA text")
        return token[:100]

    def answer_test_task(
        self,
        question: str,
        solutions: Sequence[Mapping[str, Any]],
    ) -> str:
        config = self._config("tests")
        prompt_question = _plain_text(question)
        if solutions:
            normalized = _solutions(solutions)
            options = "\n".join(
                f"{solution_id}: {label}" for solution_id, label in normalized
            )
            prompt = _prompt(
                config,
                "selection_prompt",
                "Вопрос: {question}\nВарианты:\n{options}\nВерни только ID.",
                question=prompt_question,
                options=options,
                facts="",
            )
            raw = self._text_complete(config, prompt)
            return _selected_id(raw, tuple(value[0] for value in normalized))
        if "://" in prompt_question:
            answer = str(config.get("external_link_answer") or "").strip()
            if answer:
                return answer
        prompt = _prompt(
            config,
            "text_prompt",
            "Дай краткий профессиональный ответ: {question}",
            question=prompt_question,
            options="",
            facts="",
        )
        return self._text_complete(config, prompt)

    def answer_form_field(
        self,
        field: Any,
        *,
        candidate: Mapping[str, Any],
        resume: Mapping[str, Any],
    ) -> Any:
        config = self._config("forms")
        name = str(getattr(field, "name", "") or "").strip()
        question = str(getattr(field, "prompt", "") or name).strip()
        kind = str(getattr(field, "kind", "text") or "text").casefold()
        option_labels = tuple(getattr(field, "option_labels", ()) or ())
        raw_options = tuple(
            str(value) for value in (getattr(field, "options", ()) or ())
        )
        if option_labels:
            normalized_options = tuple(
                (str(value), _plain_text(str(label)) or str(value))
                for value, label in option_labels
                if str(value)
            )
        else:
            normalized_options = tuple((value, value) for value in raw_options if value)
        facts = _facts(candidate, resume)
        if kind in {"radio", "select"} or normalized_options:
            if not normalized_options:
                raise ChallengeAIError(f"field {name} has no selectable options")
            options = "\n".join(
                f"{value}: {label}" for value, label in normalized_options
            )
            prompt = _prompt(
                config,
                "selection_prompt",
                "Поле: {question}\nВарианты:\n{options}\n"
                "Данные кандидата:\n{facts}\nВерни только ID варианта.",
                question=question,
                options=options,
                facts=facts,
            )
            return _selected_id(
                self._text_complete(config, prompt),
                tuple(value for value, _label in normalized_options),
            )
        prompt = _prompt(
            config,
            "text_prompt",
            "Поле: {question}\nДанные кандидата:\n{facts}\n"
            "Верни только ответ для поля.",
            question=question,
            options="",
            facts=facts,
        )
        answer = self._text_complete(config, prompt)
        if kind in {"checkbox", "boolean"}:
            lowered = answer.casefold()
            if lowered in {"да", "yes", "true", "1"}:
                return True
            if lowered in {"нет", "no", "false", "0"}:
                return False
            raise ChallengeAIError(f"field {name} requires a boolean answer")
        return answer

    def _text_complete(self, config: dict[str, Any], prompt: str) -> str:
        raw = self._complete(
            config,
            [
                {
                    "role": "system",
                    "content": str(config.get("system_prompt") or ""),
                },
                {"role": "user", "content": prompt},
            ],
        )
        answer = _clean_answer(raw)
        maximum = int(config.get("max_answer_characters", 2_000))
        if maximum < 1:
            raise ChallengeAIError("max_answer_characters must be positive")
        if not answer:
            raise ChallengeAIError("AI returned an empty answer")
        return answer[:maximum]

    def _complete(
        self,
        config: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> str:
        retries = int(config.get("max_retries", 0))
        if not 0 <= retries <= 10:
            raise ChallengeAIError("AI max_retries must be in 0..10")
        delay = float(config.get("retry_base_seconds", 0) or 0)
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                result = self._completion(messages, copy.deepcopy(config))
                if not isinstance(result, str) or not result.strip():
                    raise ChallengeAIError("AI returned empty content")
                return result.strip()
            except Exception as exc:
                last_error = exc
                if attempt < retries and delay > 0:
                    self._sleeper(delay * (attempt + 1))
        raise ChallengeAIError(
            f"AI challenge request failed: {last_error}"
        ) from last_error

    def _config(self, purpose: str) -> dict[str, Any]:
        root = self._config_provider()
        if not isinstance(root, Mapping):
            raise ChallengeAIError("AI config must be a mapping")
        return scoped_ai_config(root, purpose)


def scoped_ai_config(root: Mapping[str, Any], purpose: str) -> dict[str, Any]:
    nested_names = {"tests", "forms", "captcha"}
    result = {
        str(key): copy.deepcopy(value)
        for key, value in root.items()
        if key not in nested_names
    }
    override = root.get(purpose) or {}
    if not isinstance(override, Mapping):
        raise ChallengeAIError(f"ai.{purpose} must be a mapping")
    inherited_when_empty = {
        "api_key",
        "base_url",
        "model",
        "provider",
        "opencode_model",
    }
    for key, value in override.items():
        if key in inherited_when_empty and value in {None, ""}:
            continue
        result[str(key)] = copy.deepcopy(value)
    return result


def _solutions(
    values: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for value in values:
        raw_id = value.get("id")
        solution_id = "" if raw_id is None else str(raw_id).strip()
        if not solution_id:
            continue
        result.append((solution_id, _plain_text(str(value.get("text") or solution_id))))
    if not result:
        raise ChallengeAIError("test task has no valid solution IDs")
    return tuple(result)


def _selected_id(raw: str, allowed: Sequence[str]) -> str:
    cleaned = _clean_answer(raw).strip("`'\" ")
    for value in allowed:
        if cleaned == value:
            return value
    for value in allowed:
        if re.search(rf"(?<![\w.-]){re.escape(value)}(?![\w.-])", cleaned):
            return value
    raise ChallengeAIError("AI did not return one of the supplied solution IDs")


def _prompt(
    config: Mapping[str, Any],
    key: str,
    default: str,
    **values: str,
) -> str:
    template = str(config.get(key) or default)
    try:
        return template.format(**values)
    except (KeyError, ValueError) as exc:
        raise ChallengeAIError(f"invalid ai prompt template: {key}") from exc


def _plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _clean_answer(value: str) -> str:
    cleaned = value.strip()
    fenced = re.fullmatch(
        r"```(?:text)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL | re.I
    )
    if fenced is not None:
        cleaned = fenced.group(1).strip()
    return cleaned.strip("\ufeff")


def _facts(candidate: Mapping[str, Any], resume: Mapping[str, Any]) -> str:
    rendered = json.dumps(
        {"candidate": dict(candidate), "resume": dict(resume)},
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    return rendered[:12_000]


__all__ = [
    "ChallengeAIError",
    "HHChallengeAI",
    "scoped_ai_config",
]
