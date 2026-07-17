from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class WebActionRequest:
    method: str
    url: str
    headers: dict[str, str]
    data: dict[str, Any]


class HHWebActions:
    def __init__(self, *, base_url: str = "https://hh.ru", user_agent: str = "", xsrf_token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.xsrf_token = xsrf_token

    def response_popup_request(
        self,
        *,
        vacancy_id: str,
        resume_id: str,
        message: str = "",
    ) -> WebActionRequest:
        return WebActionRequest(
            method="POST",
            url=f"{self.base_url}/applicant/vacancy_response/popup",
            headers=self.headers(),
            data={
                "vacancy_id": vacancy_id,
                "resume_id": resume_id,
                "message": message,
            },
        )

    def negotiation_cleanup_request(self, negotiation_id: str, *, message: str = "") -> WebActionRequest:
        return WebActionRequest(
            method="DELETE",
            url=f"{self.base_url}/applicant/negotiations/active/{negotiation_id}",
            headers=self.headers(),
            data={"with_decline_message": message},
        )

    def hide_negotiation_chat_request(self, negotiation_id: str) -> WebActionRequest:
        topic = str(negotiation_id or "").strip()
        if not topic or "\0" in topic:
            raise ValueError("negotiation_id is required")
        headers = self.headers()
        headers.update(
            {
                "X-Hhtmfrom": "main",
                "X-Hhtmsource": "negotiation_list",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": (
                    f"{self.base_url}/applicant/negotiations"
                    "?hhtmFrom=main&hhtmFromLabel=header"
                ),
            }
        )
        return WebActionRequest(
            method="POST",
            url=f"{self.base_url}/applicant/negotiations/trash",
            headers=headers,
            data={
                "topic": topic,
                "query": "?hhtmFrom=main&hhtmFromLabel=header",
                "substate": "HIDE",
            },
        )

    def headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Referer": self.base_url,
        }
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        if self.xsrf_token:
            headers["X-Xsrftoken"] = self.xsrf_token
            headers["_xsrf"] = self.xsrf_token
        return headers
