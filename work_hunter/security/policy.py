from __future__ import annotations


def safety_policy() -> dict[str, set[str]]:
    return {
        "forbidden_outputs": {
            "access_token",
            "refresh_token",
            "Authorization",
            "Cookie",
            "Set-Cookie",
            "client_secret",
            "api_key",
            "OpenRouter key",
            "Fireworks key",
            "OpenCode key",
            "Codex auth",
            "XSRF token",
            "session id",
            "browser localStorage raw",
        }
    }
