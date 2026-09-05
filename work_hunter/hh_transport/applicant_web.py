"""Cookie-authenticated HH applicant profile operations.

The touch and job-search-status request contracts are compatible with the
public https://github.com/s3rgeym/hh-ai-responder implementation.
"""

from __future__ import annotations

import html
import json
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlparse

import requests

from .cookiejar import is_hh_domain


class HHApplicantWebClient:
    def __init__(
        self,
        *,
        cookies: Iterable[Mapping[str, Any]],
        xsrf_token: str,
        user_agent: str,
        base_url: str = "https://hh.ru",
        timeout: float = 30.0,
        session: Any | None = None,
    ) -> None:
        self.base_url = _validated_hh_url(base_url)
        self.xsrf_token = str(xsrf_token or "").strip()
        if not self.xsrf_token:
            raise ValueError("HH applicant web XSRF token is required")
        self.timeout = float(timeout)
        if self.timeout <= 0 or self.timeout > 300:
            raise ValueError("HH applicant web timeout must be in 0..300 seconds")
        self.http = session or requests.Session()
        if user_agent:
            self.http.headers.update({"User-Agent": str(user_agent)})
        cookie_count = 0
        for cookie in cookies:
            name = str(cookie.get("name") or "").strip()
            domain = str(cookie.get("domain") or ".hh.ru").strip()
            if not name or not is_hh_domain(domain):
                continue
            self.http.cookies.set(
                name,
                str(cookie.get("value") or ""),
                domain=domain,
                path=str(cookie.get("path") or "/"),
            )
            cookie_count += 1
        if cookie_count == 0:
            raise ValueError("HH applicant web cookies are required")

    def load_profile_data(self) -> dict[str, Any]:
        response = self.http.request(
            "GET",
            f"{self.base_url}/applicant/my_resumes",
            headers={"Accept": "text/html,application/xhtml+xml"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        text = str(response.text or "")
        marker = '{"redirectConfig":'
        index = text.find(marker)
        if index < 0 and ("{&#34;redirectConfig&#34;:" in text or "{&quot;redirectConfig&quot;:" in text):
            text = html.unescape(text)
            index = text.find(marker)
        if index < 0:
            raise RuntimeError("HH applicant resumes page has no embedded profile data")
        try:
            payload, _end = json.JSONDecoder().raw_decode(text[index:])
        except json.JSONDecodeError as exc:
            raise RuntimeError("HH applicant resumes page contains invalid profile data") from exc
        if not isinstance(payload, dict):
            raise TypeError("HH applicant profile data is not an object")
        return payload

    def touch_resume(self, resume_hash: str) -> dict[str, Any]:
        value = str(resume_hash or "").strip()
        if not value or len(value) > 256:
            raise ValueError("HH resume hash is required")
        response = self.http.request(
            "POST",
            f"{self.base_url}/applicant/resumes/touch",
            files={
                "resume": (None, value),
                "undirectable": (None, "true"),
            },
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "X-Xsrftoken": self.xsrf_token,
                "X-Hhtmfrom": "negotiation_list",
                "X-Hhtmsource": "resume_list",
                "Referer": f"{self.base_url}/applicant/my_resumes",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return _optional_json(response, fallback={"status_code": response.status_code})

    def set_looking_for_offers(self, profile_data: Mapping[str, Any]) -> dict[str, Any]:
        config = _mapping(profile_data.get("config"))
        hosts = _mapping(config.get("externalMicroFrontendHosts"))
        profile_front = _validated_hh_url(str(hosts.get("resume-profile-front") or ""))
        response = self.http.request(
            "POST",
            f"{profile_front}/profile/shards/user_statuses/job_search_status",
            params={"status": "looking_for_offers"},
            headers={
                "Accept": "application/json",
                "X-hhtmSource": "resume_list",
                "X-hhtmFrom": "",
                "X-hhtmSourceLabel": "",
                "X-hhtmFromLabel": "",
                "X-Requested-With": "XMLHttpRequest",
                "X-Xsrftoken": self.xsrf_token,
                "Referer": f"{self.base_url}/applicant/my_resumes",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return _optional_json(response, fallback={"status_code": response.status_code})


def applicant_profile_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    account = _mapping(payload.get("account"))
    notifications = _list(payload.get("userNotifications"))
    first_notification = _mapping(notifications[0]) if notifications else {}
    applicant_id = str(first_notification.get("userId") or "")
    resumes: list[dict[str, Any]] = []
    for raw in _list(payload.get("applicantResumes")):
        resume = _mapping(raw)
        attributes = _mapping(resume.get("_attributes"))
        if not applicant_id:
            applicant_id = str(attributes.get("user") or "")
        titles = _list(resume.get("title"))
        title = str(_mapping(titles[0]).get("string") or "") if titles else ""
        resumes.append(
            {
                "id": str(attributes.get("id") or ""),
                "hash": str(attributes.get("hash") or ""),
                "title": title,
            }
        )
    config = _mapping(payload.get("config"))
    hosts = _mapping(config.get("externalMicroFrontendHosts"))
    return {
        "latest_resume_hash": str(payload.get("latestResumeHash") or ""),
        "applicant_id": applicant_id,
        "account": {
            "first_name": str(account.get("firstName") or ""),
            "middle_name": str(account.get("middleName") or ""),
            "last_name": str(account.get("lastName") or ""),
            "email": str(account.get("email") or ""),
        },
        "resumes": resumes,
        "chatik_url": str(hosts.get("chatik") or ""),
        "resume_profile_front_url": str(hosts.get("resume-profile-front") or ""),
    }


def _validated_hh_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip().rstrip("/"))
    hostname = str(parsed.hostname or "")
    if (
        parsed.scheme != "https"
        or not is_hh_domain(hostname)
        or parsed.username
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("HH web URL must be an HTTPS hh.ru host")
    path = parsed.path.rstrip("/")
    return f"https://{hostname}{path}"


def _optional_json(response: Any, *, fallback: dict[str, Any]) -> dict[str, Any]:
    if not getattr(response, "content", b""):
        return fallback
    try:
        payload = response.json()
    except ValueError:
        return fallback
    return payload if isinstance(payload, dict) else fallback


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


__all__ = ["HHApplicantWebClient", "applicant_profile_summary"]
