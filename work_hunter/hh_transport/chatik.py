"""Typed client for HH's applicant Chatik web transport.

The endpoint contract was independently integrated from the public
https://github.com/s3rgeym/hh-ai-responder project.  Authentication material is
always supplied by the local operator and is never logged by this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse
import uuid

import requests

from .cookiejar import is_hh_domain


CHATIK_BASE_URL = "https://chatik.hh.ru"


@dataclass(frozen=True, slots=True)
class ChatikCandidate:
    chat_id: str
    applicant_id: str
    last_message_id: str
    last_message_text: str
    last_message_at: str
    contact_name: str = ""
    vacancy_id: str = ""
    vacancy_name: str = ""
    vacancy_url: str = ""
    company_id: str = ""
    company_name: str = ""
    resume_id: str = ""
    resume_hash: str = ""
    resume_title: str = ""
    reply_options: tuple[str, ...] = field(default_factory=tuple)
    discarded: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reply_options"] = list(self.reply_options)
        return payload


class HHChatikClient:
    """Small requests-based client for the applicant chat web API."""

    def __init__(
        self,
        *,
        cookies: Iterable[Mapping[str, Any]],
        xsrf_token: str,
        user_agent: str,
        base_url: str = CHATIK_BASE_URL,
        timeout: float = 30.0,
        session: Any | None = None,
    ) -> None:
        self.base_url = _validated_chatik_base_url(base_url)
        self.xsrf_token = str(xsrf_token or "").strip()
        if not self.xsrf_token:
            raise ValueError("HH Chatik XSRF token is required")
        self.timeout = float(timeout)
        if self.timeout <= 0 or self.timeout > 300:
            raise ValueError("HH Chatik timeout must be in 0..300 seconds")
        self.http = session or requests.Session()
        if user_agent:
            self.http.headers.update({"User-Agent": str(user_agent)})
        cookie_count = 0
        for cookie in cookies:
            name = str(cookie.get("name") or "").strip()
            value = str(cookie.get("value") or "")
            domain = str(cookie.get("domain") or ".hh.ru").strip()
            if not name or not is_hh_domain(domain):
                continue
            self.http.cookies.set(
                name,
                value,
                domain=domain,
                path=str(cookie.get("path") or "/"),
            )
            cookie_count += 1
        if cookie_count == 0:
            raise ValueError("HH Chatik cookies are required")

    def list_chats(self, *, page: int = 0) -> dict[str, Any]:
        if page < 0:
            raise ValueError("Chatik page must be nonnegative")
        params: dict[str, Any] = {
            "filterUnread": "false",
            "filterHasTextMessage": "false",
            "do_not_track_session_events": "true",
        }
        if page:
            params["page"] = page
        return self._request_json("GET", "/chatik/api/chats", params=params)

    def get_chat_data(self, chat_id: str | int, applicant_id: str | int) -> dict[str, Any]:
        return self._request_json(
            "GET",
            "/chatik/api/chat_data",
            params={
                "chatId": _numeric_id(chat_id, "chat_id"),
                "applicantId": _numeric_id(applicant_id, "applicant_id"),
                "do_not_track_session_events": "true",
            },
            referer=f"{self.base_url}/chat/{_numeric_id(chat_id, 'chat_id')}",
        )

    def send_message(
        self,
        chat_id: str | int,
        text: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        message = str(text or "").strip()
        if not message:
            raise ValueError("HH Chatik message cannot be empty")
        key = str(idempotency_key or uuid.uuid4())
        try:
            parsed_key = uuid.UUID(key)
        except ValueError as exc:
            raise ValueError("HH Chatik idempotency key must be a UUID") from exc
        payload = {
            "chatId": _numeric_id(chat_id, "chat_id"),
            "text": message,
            "idempotencyKey": str(parsed_key),
        }
        result = self._request_json("POST", "/chatik/api/send", json_body=payload)
        if result.get("error"):
            raise RuntimeError(f"HH Chatik rejected the message: {result['error']}")
        return result

    def leave_chat(self, chat_id: str | int) -> dict[str, Any]:
        parsed_chat_id = _numeric_id(chat_id, "chat_id")
        return self._request_json(
            "POST",
            "/chatik/api/leave",
            json_body={"chatId": parsed_chat_id},
            referer=f"{self.base_url}/chat/{parsed_chat_id}",
            extra_headers={
                "X-hhtmFrom": "resume",
                "X-hhtmFromLabel": "resume",
                "X-hhtmSource": "app",
                "X-hhtmSourceLabel": "resume",
            },
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        referer: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-Xsrftoken": self.xsrf_token,
            "Referer": referer or f"{self.base_url}/?platform=xhh&dest=iframe",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        headers.update(dict(extra_headers or {}))
        response = self.http.request(
            method,
            f"{self.base_url}{path}",
            params=dict(params or {}),
            json=dict(json_body) if json_body is not None else None,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("HH Chatik returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("HH Chatik returned a non-object JSON response")
        return payload


def load_hh_cookie_file(path: str | Path) -> list[dict[str, Any]]:
    """Load either a browser JSON export or Netscape cookies.txt."""

    cookie_path = Path(path)
    text = cookie_path.read_text(encoding="utf-8", errors="replace")
    stripped = text.lstrip("\ufeff\r\n\t ")
    if stripped.startswith("[") or stripped.startswith("{"):
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            payload = payload.get("cookies")
        if not isinstance(payload, list):
            raise ValueError("HH cookie JSON must contain a cookie list")
        cookies = [dict(item) for item in payload if isinstance(item, dict)]
    else:
        cookies = _parse_netscape_cookies(text.splitlines())
    valid = [
        cookie
        for cookie in cookies
        if cookie.get("name") and is_hh_domain(str(cookie.get("domain") or ""))
    ]
    if not valid:
        raise ValueError("HH cookie file contains no hh.ru cookies")
    return valid


def extract_chatik_candidates(
    payload: Mapping[str, Any],
    *,
    awaiting_only: bool = True,
    max_age_hours: float | None = 72.0,
    now: datetime | None = None,
) -> list[ChatikCandidate]:
    """Normalize a Chatik page without depending on undocumented key order."""

    chats = _mapping(payload.get("chats"))
    resources = _mapping(payload.get("resources"))
    vacancies = _mapping(resources.get("vacancies"))
    resumes = _mapping(resources.get("resumes"))
    current_time = now or datetime.now(timezone.utc)
    result: list[ChatikCandidate] = []
    for raw_item in _list(chats.get("items")):
        item = _mapping(raw_item)
        last = _mapping(item.get("lastMessage"))
        if not last:
            continue
        applicant_id = _text_id(item.get("currentParticipantId"))
        last_participant = _text_id(last.get("participantId"))
        if awaiting_only and applicant_id and last_participant == applicant_id:
            continue
        last_at = str(last.get("creationTime") or "")
        if max_age_hours is not None and _older_than_hours(last_at, max_age_hours, current_time):
            continue

        item_resources = _mapping(item.get("resources"))
        vacancy_id = _first_text(item_resources.get("VACANCY") or item_resources.get("vacancy"))
        resume_id = _first_text(item_resources.get("RESUME") or item_resources.get("resume"))
        vacancy = _mapping(vacancies.get(vacancy_id))
        resume = _mapping(resumes.get(resume_id))
        company = _mapping(vacancy.get("company"))
        links = _mapping(vacancy.get("links"))
        display = _mapping(last.get("participantDisplay"))
        actions = _mapping(last.get("actions"))
        buttons = [
            str(_mapping(button).get("text") or "").strip()
            for button in _list(actions.get("text_buttons") or actions.get("textButtons"))
        ]
        transition = _mapping(last.get("workflowTransition"))
        result.append(
            ChatikCandidate(
                chat_id=_text_id(item.get("id")),
                applicant_id=applicant_id,
                last_message_id=_text_id(last.get("id")),
                last_message_text=str(last.get("text") or "").strip(),
                last_message_at=last_at,
                contact_name=str(display.get("name") or "").strip(),
                vacancy_id=_text_id(vacancy.get("vacancyId") or vacancy_id),
                vacancy_name=str(vacancy.get("name") or "").strip(),
                vacancy_url=str(links.get("desktop") or "").strip(),
                company_id=_text_id(company.get("id")),
                company_name=str(company.get("name") or "").strip(),
                resume_id=_text_id(resume.get("id") or resume_id),
                resume_hash=str(resume.get("hash") or "").strip(),
                resume_title=str(resume.get("title") or "").strip(),
                reply_options=tuple(option for option in buttons if option),
                discarded=str(transition.get("applicantState") or "").upper() == "DISCARD",
            )
        )
    return [candidate for candidate in result if candidate.chat_id]


def chatik_page_count(payload: Mapping[str, Any]) -> int:
    chats = _mapping(payload.get("chats"))
    try:
        return max(1, int(chats.get("pages") or 1))
    except (TypeError, ValueError):
        return 1


def chatik_message_history(payload: Mapping[str, Any], *, limit: int = 20) -> str:
    chat = _mapping(payload.get("chat"))
    messages = _mapping(chat.get("messages"))
    items = _list(messages.get("items"))[-max(1, int(limit)) :]
    lines: list[str] = []
    for raw in items:
        message = _mapping(raw)
        text = str(message.get("text") or "").strip()
        if not text:
            continue
        display = _mapping(message.get("participantDisplay"))
        name = str(display.get("name") or "Участник").strip()
        created = str(message.get("creationTime") or "").strip()
        lines.append(f"[{created}] {name}: {text}" if created else f"{name}: {text}")
    return "\n".join(lines)


def chatik_write_allowed(payload: Mapping[str, Any]) -> bool:
    states = _mapping(payload.get("chatStates"))
    write_state = _mapping(states.get("writeMessageState"))
    return write_state.get("allowed") is True


def chatik_message_count(payload: Mapping[str, Any]) -> int:
    chat = _mapping(payload.get("chat"))
    messages = _mapping(chat.get("messages"))
    return len(_list(messages.get("items")))


def _validated_chatik_base_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip().rstrip("/"))
    if parsed.scheme != "https" or parsed.hostname != "chatik.hh.ru" or parsed.username:
        raise ValueError("HH Chatik base URL must be https://chatik.hh.ru")
    if parsed.port not in {None, 443} or parsed.path not in {"", "/"}:
        raise ValueError("HH Chatik base URL must not contain a custom port or path")
    return "https://chatik.hh.ru"


def _parse_netscape_cookies(lines: Iterable[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for original in lines:
        line = original.strip()
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
            http_only = True
        elif not line or line.startswith("#"):
            continue
        else:
            http_only = False
        fields = line.split("\t")
        if len(fields) != 7:
            fields = line.split(None, 6)
        if len(fields) != 7:
            continue
        domain, _include_subdomains, path, secure, expires, name, value = fields
        if not is_hh_domain(domain):
            continue
        result.append(
            {
                "domain": domain,
                "path": path or "/",
                "secure": secure.upper() == "TRUE",
                "expires": int(expires) if expires.isdigit() else -1,
                "name": name,
                "value": value,
                "httpOnly": http_only,
            }
        )
    return result


def _numeric_id(value: str | int, name: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"HH Chatik {name} must be numeric") from exc
    if parsed <= 0:
        raise ValueError(f"HH Chatik {name} must be positive")
    return parsed


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text_id(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("id")
    return str(value or "").strip()


def _first_text(value: Any) -> str:
    values = _list(value)
    return _text_id(values[0]) if values else ""


def _older_than_hours(value: str, hours: float, now: datetime) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed.astimezone(timezone.utc)).total_seconds() > float(hours) * 3600


__all__ = [
    "CHATIK_BASE_URL",
    "ChatikCandidate",
    "HHChatikClient",
    "chatik_message_count",
    "chatik_message_history",
    "chatik_page_count",
    "chatik_write_allowed",
    "extract_chatik_candidates",
    "load_hh_cookie_file",
]
