from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any, Callable

import requests

from .backends import CookieBackend, NetscapeCookieBackend
from .browser_session import HHBrowserSession


Transport = Callable[..., Any]


class HHWebSessionClient:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        cookie_backend: CookieBackend | None = None,
        transport: Transport | None = None,
        base_url: str = "https://hh.ru",
        chat_base_url: str = "https://chatik.hh.ru",
    ):
        self.config = config
        cookie_path = str(config.get("hh_cookie_file") or "").strip()
        if cookie_backend is None and cookie_path and Path(cookie_path).suffix.lower() == ".txt":
            cookie_backend = NetscapeCookieBackend(cookie_path)
        self.browser_session = HHBrowserSession(
            cookie_backend=cookie_backend,
            cookie_path=Path(cookie_path) if cookie_path else None,
        )
        self.transport = transport or requests.request
        self.base_url = str(config.get("web_base_url") or base_url).rstrip("/")
        self.chat_base_url = str(config.get("chat_base_url") or chat_base_url).rstrip("/")
        self.timeout = int(config.get("timeout", 30))
        self.user_agent = str(config.get("web_user_agent") or config.get("hh_user_agent") or "")
        self._loaded = False

    def has_session(self) -> bool:
        self._load()
        return bool(self.browser_session.cookies and self.browser_session.xsrf_token)

    def touch_resume(self, resume_hash: str) -> dict[str, Any]:
        resume_hash = str(resume_hash or "").strip()
        if not resume_hash:
            raise ValueError("resume hash is required")
        payload = self._request_json(
            "POST",
            "/applicant/resumes/touch",
            data={"resume": resume_hash, "undirectable": "true"},
            headers={
                "Accept": "application/json",
                "Referer": f"{self.base_url}/applicant/resumes",
            },
        )
        if payload.get("success") is True or payload.get("success") == "true" or not payload.get("error"):
            return {"status": "updated", "resume_hash": resume_hash, "raw_result": payload}
        return {"status": "error", "resume_hash": resume_hash, "raw_result": payload}

    def get_chats(self, page: int = 0) -> dict[str, Any]:
        query = {
            "filterUnread": "false",
            "filterHasTextMessage": "false",
            "do_not_track_session_events": "true",
        }
        if page > 0:
            query["page"] = str(page)
        endpoint = f"{self.chat_base_url}/chatik/api/chats?{urllib.parse.urlencode(query)}"
        return self._request_json(
            "GET",
            endpoint,
            headers={"Referer": f"{self.chat_base_url}/?platform=xhh&dest=iframe"},
        )

    def get_chat_data(self, chat_id: str | int, applicant_id: str) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {
                "chatId": str(chat_id),
                "applicantId": str(applicant_id),
                "do_not_track_session_events": "true",
            }
        )
        return self._request_json(
            "GET",
            f"{self.chat_base_url}/chatik/api/chat_data?{query}",
            headers={"Referer": f"{self.chat_base_url}/chat/{chat_id}"},
        )

    def send_chat_message(self, chat_id: str | int, text: str) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"{self.chat_base_url}/chatik/api/send",
            json={"chatId": str(chat_id), "text": text},
            headers={"Referer": f"{self.chat_base_url}/?platform=xhh&dest=iframe"},
        )

    def leave_chat(self, chat_id: str | int) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"{self.chat_base_url}/chatik/api/leave",
            json={"chatId": str(chat_id)},
            headers={"Referer": f"{self.chat_base_url}/chat/{chat_id}"},
        )

    def chats_awaiting_reply(
        self,
        *,
        resume_id: str = "",
        applicant_user_id: str = "",
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        pages = 1
        results: list[dict[str, Any]] = []
        for page in range(max(1, int(max_pages or 1))):
            if page >= pages:
                break
            payload = self.get_chats(page=page)
            chats = payload.get("chats") or {}
            items = list(chats.get("items") or [])
            pages = min(max_pages, int(chats.get("pages") or pages or 1))
            resources = payload.get("resources") or {}
            vacancies = resources.get("vacancies") or {}
            for item in items:
                target = _chat_reply_target(
                    item,
                    vacancies=vacancies,
                    resume_id=resume_id,
                    applicant_user_id=applicant_user_id,
                )
                if target:
                    results.append(target)
        return results

    def get_vacancy_tests(self, vacancy_id: str) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {
                "vacancyId": str(vacancy_id),
                "startedWithQuestion": "false",
                "hhtmFrom": "vacancy",
            }
        )
        response = self._request(
            "GET",
            f"/applicant/vacancy_response?{query}",
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        return extract_vacancy_tests(getattr(response, "content", b"") or getattr(response, "text", ""))

    def submit_vacancy_response(self, payload: dict[str, Any], *, referer_url: str = "") -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/applicant/vacancy_response/popup",
            data={key: str(value) for key, value in payload.items()},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": referer_url or f"{self.base_url}/applicant/vacancy_response",
            },
        )

    def _request_json(self, method: str, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        response = self._request(method, endpoint, **kwargs)
        try:
            payload = response.json()
        except ValueError:
            payload = json.loads(getattr(response, "text", "") or "{}")
        return payload if isinstance(payload, dict) else {}

    def _request(self, method: str, endpoint: str, **kwargs: Any):
        self._load()
        if not self.browser_session.xsrf_token:
            raise RuntimeError("HH web XSRF token is required")
        url = endpoint if endpoint.startswith("http") else f"{self.base_url}{endpoint}"
        headers = self._headers(kwargs.pop("headers", {}))
        response = self.transport(
            method.upper(),
            url,
            headers=headers,
            timeout=kwargs.pop("timeout", self.timeout),
            **kwargs,
        )
        status_code = int(getattr(response, "status_code", 200))
        if status_code >= 400:
            raise RuntimeError(f"HH web error {status_code}")
        return response

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-Xsrftoken": self.browser_session.xsrf_token,
            "Cookie": _cookie_header(self.browser_session.cookies),
        }
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        headers.update(extra or {})
        return {key: value for key, value in headers.items() if value}

    def _load(self) -> None:
        if self._loaded:
            return
        self.browser_session.load()
        self._loaded = True


def extract_vacancy_tests(data: bytes | str) -> dict[str, Any]:
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data or "")
    marker = '"vacancyTests"'
    marker_index = text.find(marker)
    if marker_index < 0:
        return {}
    colon_index = text.find(":", marker_index + len(marker))
    if colon_index < 0:
        return {}
    object_start = text.find("{", colon_index)
    if object_start < 0:
        return {}
    raw = _balanced_json_object(text, object_start)
    if not raw:
        return {}
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def build_vacancy_test_response_payload(
    *,
    vacancy_id: str,
    resume_hash: str,
    xsrf_token: str,
    letter: str = "",
    test: dict[str, Any],
    answers: dict[int | str, dict[str, Any]],
) -> dict[str, str]:
    payload = {
        "_xsrf": xsrf_token,
        "uidPk": str(test.get("uidPk") or test.get("uid_pk") or ""),
        "guid": str(test.get("guid") or ""),
        "startTime": str(test.get("startTime") or test.get("start_time") or ""),
        "testRequired": str(test.get("required") or "true"),
        "vacancy_id": str(vacancy_id),
        "resume_hash": str(resume_hash),
        "ignore_postponed": "true",
        "incomplete": "false",
        "lux": "true",
        "withoutTest": "no",
        "letter": letter,
        "mark_applicant_visible_in_vacancy_country": "false",
        "country_ids": "[]",
    }
    for task in test.get("tasks") or []:
        task_id = str(task.get("id") or "")
        if not task_id:
            continue
        answer = answers.get(task_id) or answers.get(int(task_id)) if task_id.isdigit() else answers.get(task_id)
        if not isinstance(answer, dict):
            continue
        if answer.get("solution_id") is not None:
            payload[f"task_{task_id}"] = str(answer["solution_id"])
        else:
            payload[f"task_{task_id}_text"] = str(answer.get("text_answer") or answer.get("answer") or "")
    return payload


def _cookie_header(cookies: list[dict[str, Any]]) -> str:
    parts = []
    for cookie in cookies:
        domain = str(cookie.get("domain") or "").lower().lstrip(".")
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if not name or not (domain == "hh.ru" or domain.endswith(".hh.ru")):
            continue
        parts.append(f"{name}={value}")
    return "; ".join(parts)


def _balanced_json_object(text: str, start: int) -> str:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""


def _chat_reply_target(
    item: dict[str, Any],
    *,
    vacancies: dict[str, Any],
    resume_id: str,
    applicant_user_id: str,
) -> dict[str, Any] | None:
    resources = item.get("resources") or {}
    resume_refs = [str(value) for value in resources.get("resume") or []]
    if resume_id and resume_refs and str(resume_id) not in resume_refs:
        return None
    last = item.get("lastMessage") or item.get("last_message") or item.get("last") or {}
    text = str(last.get("text") or last.get("body") or "").strip()
    if not text:
        return None
    participant_id = str(last.get("participantId") or last.get("participant_id") or "")
    if applicant_user_id and participant_id == str(applicant_user_id):
        return None
    vacancy_refs = [str(value) for value in resources.get("vacancy") or []]
    vacancy = vacancies.get(vacancy_refs[0], {}) if vacancy_refs else {}
    actions = last.get("actions") or {}
    buttons = actions.get("textButtons") or actions.get("text_buttons") or []
    transition = last.get("workflowTransition") or last.get("workflow_transition") or {}
    return {
        "chat_id": str(item.get("id") or item.get("chatId") or ""),
        "vacancy_id": str(vacancy.get("vacancyId") or vacancy.get("id") or (vacancy_refs[0] if vacancy_refs else "")),
        "vacancy_name": str(vacancy.get("name") or ""),
        "vacancy_url": str((vacancy.get("links") or {}).get("desktop") or ""),
        "employer_name": str(((vacancy.get("company") or {}).get("name")) or ""),
        "reply_to_message": text,
        "reply_options": [str(button.get("text") or "") for button in buttons if isinstance(button, dict)],
        "is_discard": str(transition.get("applicantState") or "").upper() == "DISCARD",
    }
