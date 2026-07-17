from __future__ import annotations

import json
import re
import subprocess
from typing import Any

import requests


def chat_completion(
    messages: list[dict[str, Any]],
    ai_config: dict[str, Any],
) -> str:
    backend = str(ai_config.get("backend") or "direct").lower()
    if backend == "opencode":
        return _opencode_completion(messages, ai_config)
    return _direct_completion(messages, ai_config)


def _direct_completion(messages: list[dict[str, Any]], ai_config: dict[str, Any]) -> str:
    api_key = ai_config.get("api_key", "")
    base_url = ai_config.get("base_url", "")
    model = ai_config.get("model", "")

    if not api_key or not base_url or not model:
        raise ValueError("AI config incomplete: api_key, base_url, or model missing")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if "openrouter.ai" in base_url:
        headers["HTTP-Referer"] = "https://github.com/work-hunter"
        headers["X-Title"] = "Work Hunter"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": ai_config.get("temperature", 0.7),
        "max_tokens": ai_config.get("max_tokens", 1500),
    }

    response = requests.post(
        base_url,
        headers=headers,
        json=payload,
        timeout=float(ai_config.get("timeout", 60)),
    )
    response.raise_for_status()
    data = response.json()
    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    if not content:
        raise ValueError("AI returned empty content")
    return str(content).strip()


def _opencode_completion(messages: list[dict[str, Any]], ai_config: dict[str, Any]) -> str:
    transport = str(ai_config.get("opencode_transport") or "cli").lower()
    if transport == "server":
        try:
            return _opencode_server_completion(messages, ai_config)
        except Exception:
            return _opencode_cli_completion(messages, ai_config)
    return _opencode_cli_completion(messages, ai_config)


def _opencode_cli_completion(messages: list[dict[str, Any]], ai_config: dict[str, Any]) -> str:
    command = [str(ai_config.get("opencode_command") or "opencode"), "run", "--format", "json"]
    agent = str(ai_config.get("opencode_agent") or "work-hunter-ai")
    if agent:
        command.extend(["--agent", agent])
    model = str(ai_config.get("opencode_model") or "").strip()
    if model:
        command.extend(["--model", model])
    opencode_dir = str(ai_config.get("opencode_dir") or "").strip()
    if opencode_dir:
        command.extend(["--dir", opencode_dir])
    command.append(_messages_to_prompt(messages))

    completed = subprocess.run(
        command,
        capture_output=True,
        timeout=int(ai_config.get("opencode_timeout", 300)),
        check=False,
    )
    stdout = _decode_process_value(completed.stdout)
    stderr = _decode_process_value(completed.stderr)
    if completed.returncode != 0:
        raise ValueError(f"opencode failed with exit code {completed.returncode}: {stderr.strip()}")
    return _extract_opencode_text(stdout)


def _opencode_server_completion(messages: list[dict[str, Any]], ai_config: dict[str, Any]) -> str:
    base_url = str(ai_config.get("opencode_server_url") or "http://127.0.0.1:4096").rstrip("/")
    auth = _server_auth(ai_config)
    health = requests.get(f"{base_url}/global/health", timeout=5, auth=auth)
    health.raise_for_status()
    health_data = health.json()
    if not health_data.get("healthy", False):
        raise ValueError("opencode server is not healthy")

    session_response = requests.post(
        f"{base_url}/session",
        json={"title": "Work Hunter AI"},
        timeout=10,
        auth=auth,
    )
    session_response.raise_for_status()
    session = session_response.json()
    session_id = (
        session.get("id")
        or session.get("sessionID")
        or session.get("session_id")
        or session.get("info", {}).get("id")
    )
    if not session_id:
        raise ValueError("opencode server did not return a session id")

    payload: dict[str, Any] = {
        "agent": str(ai_config.get("opencode_agent") or "work-hunter-ai"),
        "parts": [{"type": "text", "text": _messages_to_prompt(messages)}],
    }
    model = str(ai_config.get("opencode_model") or "").strip()
    if model:
        payload["model"] = model

    message_response = requests.post(
        f"{base_url}/session/{session_id}/message",
        json=payload,
        timeout=int(ai_config.get("opencode_timeout", 300)),
        auth=auth,
    )
    message_response.raise_for_status()
    data = message_response.json()
    text = _extract_text_from_json(data)
    if not text:
        raise ValueError("opencode returned empty content")
    return text


def _server_auth(ai_config: dict[str, Any]):
    password = str(ai_config.get("opencode_server_password") or "").strip()
    if not password:
        return None
    username = str(ai_config.get("opencode_server_username") or "opencode")
    return (username, password)


def _messages_to_prompt(messages: list[dict[str, Any]]) -> str:
    parts = [
        "You are the Work Hunter AI backend.",
        "Return only the requested answer. If the prompt requests JSON, return valid JSON without markdown fences.",
    ]
    for message in messages:
        role = str(message.get("role") or "user").upper()
        content = str(message.get("content") or "")
        parts.append(f"{role}:\n{content}")
    return "\n\n".join(parts).strip()


def _extract_opencode_text(stdout: str) -> str:
    clean_stdout = _strip_ansi(stdout).strip()
    if not clean_stdout:
        raise ValueError("opencode returned empty content")

    collected: list[str] = []
    saw_json = False
    for line in clean_stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        saw_json = True
        text = _extract_text_from_json(event)
        if text:
            collected.append(text)

    if collected:
        return "\n".join(collected).strip()
    if saw_json:
        raise ValueError("opencode returned empty content")
    return clean_stdout


def _extract_text_from_json(value: Any) -> str:
    texts: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            item_type = item.get("type")
            if item_type in {"text", "message.text", "message.part"}:
                text_value = item.get("text")
                if isinstance(text_value, str):
                    texts.append(text_value)
            part = item.get("part")
            if isinstance(part, dict):
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
                else:
                    walk(part)
            for key in ("parts", "content", "message"):
                child = item.get(key)
                if isinstance(child, (dict, list)):
                    walk(child)
                elif key == "content" and isinstance(child, str) and item_type in {"assistant", "text"}:
                    texts.append(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return "\n".join(text for text in texts if text).strip()


def _decode_process_value(value: bytes | str) -> str:
    if isinstance(value, str):
        return value
    for encoding in ("utf-8", "cp1251"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", errors="replace")


def _strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", value)
