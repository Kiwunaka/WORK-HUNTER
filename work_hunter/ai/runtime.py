from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Any

import requests

from ..config import mask_secrets


HTTP_ADAPTERS = {"openrouter", "fireworks", "openai_compatible"}
EXTERNAL_AUTH_ADAPTERS = {"codex_cli", "codex_sdk", "opencode_cli", "opencode_server"}


class AIRuntimeRegistry:
    def __init__(self, ai_config: dict[str, Any]):
        self.ai_config = dict(ai_config or {})

    def routes(self) -> dict[str, dict[str, Any]]:
        return runtime_routes(self.ai_config)

    def status(self) -> dict[str, Any]:
        return ai_status(self.ai_config)

    def test(
        self,
        *,
        route: str | None = None,
        prompt: str = "ping",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return ai_test(
            self.ai_config,
            route=route,
            prompt=prompt,
            dry_run=dry_run,
        )

    def chat_completion(self, messages: list[dict[str, str]]) -> str:
        return chat_completion(messages, self.ai_config)


def chat_completion(
    messages: list[dict[str, str]],
    ai_config: dict[str, Any],
) -> str:
    backend = str(ai_config.get("backend") or "direct").lower()
    route_config = _completion_route_from_config(ai_config, backend=backend)
    if route_config is not None:
        return _route_completion(route_config, messages)
    if backend == "opencode":
        return _opencode_completion(messages, ai_config)
    if backend in {"codex", "codex_cli"}:
        return _codex_cli_completion(messages, _codex_route_from_config(ai_config))
    if backend in {"codex_server", "codex_sdk"}:
        return _codex_sdk_completion(messages, _codex_server_route_from_config(ai_config))
    return _direct_completion(messages, ai_config)


def _codex_route_from_config(ai_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "adapter": "codex_cli",
        "command": ai_config.get("codex_command") or "codex",
        "model": ai_config.get("codex_model") or "",
        "reasoning": ai_config.get("codex_reasoning") or "",
        "timeout": ai_config.get("codex_timeout", ai_config.get("opencode_timeout", 300)),
    }


def _codex_server_route_from_config(ai_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "adapter": "codex_sdk",
        "enabled": True,
        "command": ai_config.get("codex_command") or "codex",
        "base_url": ai_config.get("codex_server_url") or "",
        "model": ai_config.get("codex_model") or "",
        "timeout": ai_config.get("codex_timeout", ai_config.get("opencode_timeout", 300)),
    }


def ai_status(ai_config: dict[str, Any]) -> dict[str, Any]:
    routes = _runtime_routes(ai_config)
    default_route = str(ai_config.get("default_route") or "smart")
    return {
        "default_route": default_route,
        "routes": {
            name: _route_status(name, route)
            for name, route in routes.items()
        },
    }


def ai_test(
    ai_config: dict[str, Any],
    *,
    route: str | None = None,
    prompt: str = "ping",
    dry_run: bool = False,
) -> dict[str, Any]:
    route_name = route or str(ai_config.get("default_route") or "smart")
    routes = _runtime_routes(ai_config)
    if route_name not in routes:
        raise ValueError(f"Unknown AI route: {route_name}")
    route_config = dict(routes[route_name])
    request = build_ai_request(route_config, prompt=prompt)
    if dry_run:
        return {
            "status": "dry_run",
            "route": route_name,
            "request": request,
        }
    start = time.perf_counter()
    text = _route_completion(route_config, [{"role": "user", "content": prompt}])
    return {
        "status": "ok",
        "route": route_name,
        "adapter": route_config.get("adapter"),
        "model": route_config.get("model", ""),
        "latency_ms": int((time.perf_counter() - start) * 1000),
        "content": text,
    }


def build_ai_request(route_config: dict[str, Any], *, prompt: str) -> dict[str, Any]:
    adapter = str(route_config.get("adapter") or "").lower()
    if adapter == "codex_cli":
        command = [str(route_config.get("command") or "codex"), "exec"]
        model = str(route_config.get("model") or "").strip()
        reasoning = str(route_config.get("reasoning") or "").strip()
        if model:
            command.extend(["--model", model])
        if reasoning:
            command.extend(["--reasoning", reasoning])
        command.append(prompt)
        return {
            "adapter": adapter,
            "auth": "external_runtime",
            "command": command,
        }
    if adapter == "codex_sdk":
        base_url = str(route_config.get("base_url") or route_config.get("server_url") or "")
        return {
            "adapter": adapter,
            "auth": "external_runtime",
            "experimental": True,
            "enabled": bool(route_config.get("enabled", False)),
            "url": base_url,
            "model": str(route_config.get("model") or ""),
        }
    if adapter == "opencode_cli":
        command = [str(route_config.get("command") or "opencode"), "run", "--format", "json"]
        agent = str(route_config.get("agent") or "work-hunter-ai").strip()
        model = str(route_config.get("model") or "").strip()
        if agent:
            command.extend(["--agent", agent])
        if model:
            command.extend(["--model", model])
        command.append(prompt)
        return {
            "adapter": adapter,
            "auth": "external_runtime",
            "command": command,
        }
    if adapter == "opencode_server":
        base_url = str(route_config.get("base_url") or route_config.get("server_url") or "http://127.0.0.1:4096")
        return {
            "adapter": adapter,
            "auth": "external_runtime",
            "url": f"{base_url.rstrip('/')}/session",
        }
    if adapter in HTTP_ADAPTERS:
        base_url = _http_base_url(route_config, adapter)
        headers = _http_headers(route_config, base_url)
        payload = {
            "model": str(route_config.get("model") or ""),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": route_config.get("temperature", 0.7),
            "max_tokens": route_config.get("max_tokens", 1500),
        }
        return {
            "adapter": adapter,
            "url": base_url,
            "headers": mask_secrets(headers),
            "payload": payload,
        }
    raise ValueError(f"Unsupported AI adapter: {adapter}")


def route_ready(route_config: dict[str, Any]) -> bool:
    adapter = str(route_config.get("adapter") or "").lower()
    if adapter == "codex_sdk":
        return bool(route_config.get("enabled") and (route_config.get("base_url") or route_config.get("server_url")))
    if adapter in EXTERNAL_AUTH_ADAPTERS:
        return bool(
            route_config.get("command")
            or route_config.get("base_url")
            or route_config.get("server_url")
            or adapter == "opencode_server"
        )
    if adapter in HTTP_ADAPTERS:
        return bool(route_config.get("api_key") and _http_base_url(route_config, adapter) and route_config.get("model"))
    return False


def backend_requires_api_key(ai_config: dict[str, Any]) -> bool:
    backend = str(ai_config.get("backend") or "direct").lower()
    if backend == "direct" and _completion_route_from_config(ai_config, backend=backend) is not None:
        return False
    return backend not in {
        "opencode",
        "codex",
        "codex_server",
        "codex_cli",
        "codex_sdk",
        "opencode_cli",
        "opencode_server",
    }


def _completion_route_from_config(ai_config: dict[str, Any], *, backend: str) -> dict[str, Any] | None:
    if backend != "direct" or _direct_config_complete(ai_config):
        return None
    configured_routes = ai_config.get("routes")
    if not isinstance(configured_routes, dict) or not configured_routes:
        return None
    route_name = str(ai_config.get("default_route") or "smart")
    routes = _runtime_routes(ai_config)
    route_config = routes.get(route_name)
    if not isinstance(route_config, dict):
        return None
    adapter = str(route_config.get("adapter") or "").lower()
    if adapter in EXTERNAL_AUTH_ADAPTERS or route_ready(route_config):
        return dict(route_config)
    return None


def _direct_config_complete(ai_config: dict[str, Any]) -> bool:
    return bool(
        ai_config.get("api_key")
        and ai_config.get("base_url")
        and ai_config.get("model")
    )


def runtime_routes(ai_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return _runtime_routes(ai_config)


def _runtime_routes(ai_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    routes = ai_config.get("routes")
    if isinstance(routes, dict) and routes:
        return {str(name): dict(value) for name, value in routes.items() if isinstance(value, dict)}
    backend = str(ai_config.get("backend") or "direct").lower()
    if backend == "opencode":
        adapter = "opencode_server" if str(ai_config.get("opencode_transport") or "cli").lower() == "server" else "opencode_cli"
        return {
            "smart": {
                "adapter": adapter,
                "command": ai_config.get("opencode_command") or "opencode",
                "model": ai_config.get("opencode_model") or "",
                "agent": ai_config.get("opencode_agent") or "work-hunter-ai",
                "base_url": ai_config.get("opencode_server_url") or "http://127.0.0.1:4096",
                "auth": "external_runtime",
            }
        }
    if backend in {"codex", "codex_cli"}:
        route = _codex_route_from_config(ai_config)
        route["auth"] = "external_runtime"
        route["purpose"] = "Codex CLI (вход по подписке)"
        return {"smart": route}
    if backend in {"codex_server", "codex_sdk"}:
        route = _codex_server_route_from_config(ai_config)
        route["auth"] = "external_runtime"
        route["purpose"] = "Codex app-server (экспериментально)"
        return {"smart": route}
    base_url = str(ai_config.get("base_url") or "")
    adapter = "openrouter" if "openrouter.ai" in base_url else "openai_compatible"
    return {
        "smart": {
            "adapter": adapter,
            "base_url": base_url,
            "api_key": ai_config.get("api_key") or "",
            "model": ai_config.get("model") or "",
            "temperature": ai_config.get("temperature", 0.7),
            "max_tokens": ai_config.get("max_tokens", 1500),
        }
    }


def _route_status(name: str, route_config: dict[str, Any]) -> dict[str, Any]:
    adapter = str(route_config.get("adapter") or "").lower()
    status = {
        "route": name,
        "adapter": adapter,
        "model": str(route_config.get("model") or ""),
        "purpose": str(route_config.get("purpose") or ""),
        "auth": str(route_config.get("auth") or ("external_runtime" if adapter in EXTERNAL_AUTH_ADAPTERS else "api_key")),
        "ready": route_ready(route_config),
    }
    if adapter in HTTP_ADAPTERS:
        status["base_url"] = _http_base_url(route_config, adapter)
        status["has_api_key"] = bool(route_config.get("api_key"))
    return status


def _route_completion(route_config: dict[str, Any], messages: list[dict[str, str]]) -> str:
    adapter = str(route_config.get("adapter") or "").lower()
    if adapter == "codex_cli":
        return _codex_cli_completion(messages, route_config)
    if adapter == "codex_sdk":
        return _codex_sdk_completion(messages, route_config)
    if adapter == "opencode_cli":
        compat = {
            "opencode_command": route_config.get("command") or "opencode",
            "opencode_agent": route_config.get("agent") or "work-hunter-ai",
            "opencode_model": route_config.get("model") or "",
            "opencode_timeout": route_config.get("timeout", 300),
        }
        return _opencode_cli_completion(messages, compat)
    if adapter == "opencode_server":
        compat = {
            "opencode_server_url": route_config.get("base_url") or route_config.get("server_url") or "http://127.0.0.1:4096",
            "opencode_agent": route_config.get("agent") or "work-hunter-ai",
            "opencode_model": route_config.get("model") or "",
            "opencode_timeout": route_config.get("timeout", 300),
        }
        return _opencode_server_completion(messages, compat)
    if adapter in HTTP_ADAPTERS:
        return _http_chat_completion(messages, route_config, adapter=adapter)
    raise ValueError(f"Unsupported AI adapter: {adapter}")


def _codex_cli_completion(messages: list[dict[str, str]], route_config: dict[str, Any]) -> str:
    request = build_ai_request(route_config, prompt=_messages_to_prompt(messages))
    completed = subprocess.run(
        request["command"],
        capture_output=True,
        timeout=int(route_config.get("timeout", 300)),
        check=False,
    )
    stdout = _decode_process_value(completed.stdout).strip()
    stderr = _decode_process_value(completed.stderr).strip()
    if completed.returncode != 0:
        raise ValueError(f"codex failed with exit code {completed.returncode}: {_safe_process_error(stderr)}")
    if not stdout:
        raise ValueError("codex returned empty content")
    return stdout


def _codex_sdk_completion(messages: list[dict[str, str]], route_config: dict[str, Any]) -> str:
    """Drive a single turn through `codex app-server` over stdio (JSON-RPC / JSONL).

    The app-server protocol is documented at developers.openai.com/codex/app-server.
    Auth is reused from the local `codex login` session (ChatGPT subscription or API key).
    """
    command = [str(route_config.get("command") or "codex"), "app-server"]
    prompt = _messages_to_prompt(messages)
    model = str(route_config.get("model") or "").strip() or None
    timeout = int(route_config.get("timeout", 300))

    try:
        process = subprocess.Popen(  # noqa: S603 - explicit local Codex integration
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ValueError(f"codex app-server command not found: {command[0]}") from exc

    def send(message: dict[str, Any]) -> None:
        if process.stdin is None:
            raise ValueError("codex app-server stdin unavailable")
        process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        process.stdin.flush()

    def incoming():
        if process.stdout is None:
            return
        for raw in process.stdout:
            line = _decode_process_value(raw).strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

    try:
        return _codex_appserver_collect(prompt, send=send, incoming=incoming(), model=model)
    finally:
        try:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
        except Exception:  # pragma: no cover - best effort cleanup
            pass
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:  # pragma: no cover - best effort cleanup
            try:
                process.kill()
            except Exception:
                pass


def _codex_appserver_collect(prompt: str, *, send, incoming, model: str | None = None) -> str:
    """Pure JSON-RPC driver for one app-server turn. Returns the agent's text answer.

    `send` is a callable that accepts a message dict; `incoming` is an iterable of
    parsed message dicts. Kept transport-free so it can be unit-tested with a fake stream.
    """
    send({
        "method": "initialize",
        "id": 0,
        "params": {"clientInfo": {"name": "work_hunter", "title": "Work Hunter", "version": "0.1.0"}},
    })
    send({"method": "initialized", "params": {}})
    start_params: dict[str, Any] = {"approvalPolicy": "never", "sandbox": "readOnly"}
    if model:
        start_params["model"] = model
    send({"method": "thread/start", "id": 1, "params": start_params})

    deltas: list[str] = []
    completed_text = ""
    turn_started = False
    for message in incoming:
        if not isinstance(message, dict):
            continue
        if message.get("error"):
            error = message["error"]
            detail = error.get("message") if isinstance(error, dict) else error
            raise ValueError(f"codex app-server error: {_safe_process_error(str(detail))}")
        if not turn_started and message.get("id") == 1:
            thread = (message.get("result") or {}).get("thread") or {}
            thread_id = thread.get("id")
            if thread_id:
                turn_started = True
                send({
                    "method": "turn/start",
                    "id": 2,
                    "params": {"threadId": thread_id, "input": [{"type": "text", "text": prompt}]},
                })
                continue
        method = str(message.get("method") or "")
        params = message.get("params") or {}
        if method == "item/agentMessage/delta":
            delta = _codex_delta_text(params)
            if delta:
                deltas.append(delta)
        elif method in {"item/completed", "item/updated"}:
            text = _codex_item_text(params.get("item") or {})
            if text:
                completed_text = text
        elif method in {"turn/completed", "turn/failed", "turn/aborted"}:
            if method != "turn/completed":
                raise ValueError(f"codex app-server {method.split('/')[-1]}")
            break

    result = ("".join(deltas).strip() or completed_text.strip())
    if not result:
        raise ValueError("codex app-server returned empty content")
    return result


def _codex_delta_text(params: dict[str, Any]) -> str:
    delta = params.get("delta")
    if isinstance(delta, str):
        return delta
    if isinstance(delta, dict):
        return str(delta.get("text") or delta.get("content") or "")
    return str(params.get("text") or "")


def _codex_item_text(item: dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return ""
    item_type = str(item.get("type") or "").lower()
    if item_type and "agent" not in item_type and "message" not in item_type:
        return ""
    text = item.get("text")
    if isinstance(text, str) and text.strip():
        return text
    content = item.get("content")
    if isinstance(content, list):
        parts = [str(part.get("text") or "") for part in content if isinstance(part, dict)]
        joined = "".join(parts).strip()
        if joined:
            return joined
    return ""


def _http_chat_completion(
    messages: list[dict[str, str]],
    route_config: dict[str, Any],
    *,
    adapter: str,
) -> str:
    base_url = _http_base_url(route_config, adapter)
    if not route_config.get("api_key") or not route_config.get("model") or not base_url:
        raise ValueError("AI route incomplete: api_key, base_url, or model missing")
    payload = {
        "model": route_config.get("model"),
        "messages": messages,
        "temperature": route_config.get("temperature", 0.7),
        "max_tokens": route_config.get("max_tokens", 1500),
    }
    response = requests.post(
        base_url,
        headers=_http_headers(route_config, base_url),
        json=payload,
        timeout=int(route_config.get("timeout", 60)),
    )
    response.raise_for_status()
    data = response.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content:
        raise ValueError("AI returned empty content")
    return str(content).strip()


def _http_base_url(route_config: dict[str, Any], adapter: str) -> str:
    if route_config.get("base_url"):
        return str(route_config["base_url"])
    if adapter == "openrouter":
        return "https://openrouter.ai/api/v1/chat/completions"
    if adapter == "fireworks":
        return "https://api.fireworks.ai/inference/v1/chat/completions"
    return ""


def _http_headers(route_config: dict[str, Any], base_url: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {route_config.get('api_key', '')}",
        "Content-Type": "application/json",
    }
    if "openrouter.ai" in base_url:
        headers["HTTP-Referer"] = "https://github.com/work-hunter"
        headers["X-Title"] = "Work Hunter"
    return headers


def _safe_process_error(stderr: str) -> str:
    masked = mask_secrets({"stderr": stderr}).get("stderr", "")
    return str(masked).splitlines()[0][:300] if masked else ""


def _direct_completion(messages: list[dict[str, str]], ai_config: dict[str, Any]) -> str:
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

    response = requests.post(base_url, headers=headers, json=payload, timeout=60)
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


def _opencode_completion(messages: list[dict[str, str]], ai_config: dict[str, Any]) -> str:
    transport = str(ai_config.get("opencode_transport") or "cli").lower()
    if transport == "server":
        try:
            return _opencode_server_completion(messages, ai_config)
        except Exception:
            return _opencode_cli_completion(messages, ai_config)
    return _opencode_cli_completion(messages, ai_config)


def _opencode_cli_completion(messages: list[dict[str, str]], ai_config: dict[str, Any]) -> str:
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


def _opencode_server_completion(messages: list[dict[str, str]], ai_config: dict[str, Any]) -> str:
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


def _messages_to_prompt(messages: list[dict[str, str]]) -> str:
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
