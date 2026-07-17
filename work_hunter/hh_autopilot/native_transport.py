from __future__ import annotations

import copy
from json import JSONDecoder
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urljoin, urlsplit

import requests

from work_hunter.hh_transport.browser_session import (
    HHBrowserSession,
    extract_xsrf_token,
)

from .browser import BrowserField, HHBrowserApplicationAdapter
from .challenge_ai import ChallengeAIError, HHChallengeAI
from .challenges import GroundedAnswerMapper, sanitize_hh_url
from .types import DeliveryCertainty, DispatchOutcome


class HHVacancyTestTransport:
    """The native HH vacancy-test form protocol used by the reference tool."""

    TESTS_MARKER = ',"vacancyTests":'

    def __init__(
        self,
        browser_session: HHBrowserSession,
        ai: HHChallengeAI,
        *,
        base_url: str = "https://hh.ru",
        user_agent: str = "",
        timeout_seconds: int = 30,
        http_factory: Callable[[], Any] = requests.Session,
    ) -> None:
        self.browser_session = browser_session
        self.ai = ai
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.timeout_seconds = int(timeout_seconds)
        self.http_factory = http_factory

    def apply(
        self,
        vacancy_id: str,
        resume_id: str,
        message: str,
    ) -> tuple[DispatchOutcome, str]:
        if not self.browser_session.cookies:
            self.browser_session.load()
        if not self.browser_session.cookies:
            return _outcome("manual_auth"), ""
        response_url = (
            f"{self.base_url}/applicant/vacancy_response"
            f"?vacancyId={vacancy_id}&startedWithQuestion=false&hhtmFrom=vacancy"
        )
        http = self._http_session()
        try:
            response = http.get(response_url, timeout=self.timeout_seconds)
        except requests.RequestException:
            return _outcome("pre_dispatch_network_error"), ""
        runtime_location = str(getattr(response, "url", response_url) or response_url)
        if _looks_like_auth(response, runtime_location):
            return _outcome("auth_expired", location=runtime_location), runtime_location
        if _looks_like_captcha(response, runtime_location):
            return _outcome(
                "manual_captcha", location=runtime_location
            ), runtime_location
        if not 200 <= int(response.status_code) < 300:
            return _http_outcome(
                response, runtime_location, post=False
            ), runtime_location
        html = str(getattr(response, "text", "") or "")
        try:
            tests = self._parse_tests(html)
            test_data = tests[str(vacancy_id)]
            payload = self._payload(
                vacancy_id,
                resume_id,
                message,
                test_data,
                html=html,
            )
        except ChallengeAIError:
            return _outcome("ai_unavailable"), ""
        except (KeyError, TypeError, ValueError):
            return _outcome("manual_assessment"), runtime_location
        try:
            response = http.post(
                f"{self.base_url}/applicant/vacancy_response/popup",
                data=payload,
                headers={
                    "Referer": response_url,
                    "X-Hhtmfrom": "vacancy",
                    "X-Hhtmsource": "vacancy_response",
                    "X-Requested-With": "XMLHttpRequest",
                    "X-Xsrftoken": str(payload["_xsrf"]),
                },
                timeout=self.timeout_seconds,
                allow_redirects=False,
            )
        except requests.RequestException:
            return (
                _outcome(
                    "post_dispatch_network_error",
                    certainty=DeliveryCertainty.POSSIBLY_SENT,
                ),
                "",
            )
        runtime_location = _response_location(response)
        self._persist_cookies(http)
        return _test_response_outcome(response, runtime_location), runtime_location

    def _parse_tests(self, html: str) -> Mapping[str, Any]:
        start = html.find(self.TESTS_MARKER)
        if start < 0:
            raise ValueError("vacancyTests not found")
        value, _end = JSONDecoder().raw_decode(
            html,
            start + len(self.TESTS_MARKER),
        )
        if not isinstance(value, Mapping):
            raise TypeError("vacancyTests must be an object")
        return value

    def _payload(
        self,
        vacancy_id: str,
        resume_id: str,
        message: str,
        test_data: Any,
        *,
        html: str,
    ) -> dict[str, Any]:
        if not isinstance(test_data, Mapping):
            raise TypeError("vacancy test data must be an object")
        token = self.browser_session.xsrf_token or extract_xsrf_token(
            html,
            self.browser_session.cookies,
        )
        if not token:
            raise ValueError("HH browser XSRF token is absent")
        payload: dict[str, Any] = {
            "_xsrf": token,
            "uidPk": test_data["uidPk"],
            "guid": test_data["guid"],
            "startTime": test_data["startTime"],
            "testRequired": test_data["required"],
            "vacancy_id": vacancy_id,
            "resume_hash": resume_id,
            "ignore_postponed": "true",
            "incomplete": "false",
            "mark_applicant_visible_in_vacancy_country": "false",
            "country_ids": "[]",
            "lux": "true",
            "withoutTest": "no",
            "letter": message,
        }
        tasks = test_data.get("tasks")
        if isinstance(tasks, (str, bytes)) or not isinstance(tasks, Sequence):
            raise TypeError("vacancy test tasks must be an array")
        for task in tasks:
            if not isinstance(task, Mapping):
                raise TypeError("vacancy test task must be an object")
            raw_task_id = task.get("id")
            task_id = "" if raw_task_id is None else str(raw_task_id).strip()
            if not task_id:
                raise ValueError("vacancy test task ID is absent")
            question = str(task.get("description") or "").strip()
            solutions = task.get("candidateSolutions") or ()
            if isinstance(solutions, (str, bytes)) or not isinstance(
                solutions, Sequence
            ):
                raise TypeError("candidateSolutions must be an array")
            answer = self.ai.answer_test_task(question, solutions)
            field_name = f"task_{task_id}"
            if solutions:
                payload[field_name] = answer
            else:
                payload[f"{field_name}_text"] = answer
        return payload

    def _http_session(self) -> Any:
        http = self.http_factory()
        headers = getattr(http, "headers", None)
        if self.user_agent and hasattr(headers, "update"):
            headers.update({"User-Agent": self.user_agent})
        jar = getattr(http, "cookies", None)
        for cookie in self.browser_session.cookies:
            if not callable(getattr(jar, "set", None)):
                break
            jar.set(
                str(cookie.get("name") or ""),
                str(cookie.get("value") or ""),
                domain=str(cookie.get("domain") or ".hh.ru"),
                path=str(cookie.get("path") or "/"),
            )
        return http

    def _persist_cookies(self, http: Any) -> None:
        jar = getattr(http, "cookies", None)
        if jar is None:
            return
        existing = {
            (
                str(value.get("name") or ""),
                str(value.get("domain") or ""),
                str(value.get("path") or "/"),
            ): dict(value)
            for value in self.browser_session.cookies
        }
        try:
            cookies = list(jar)
        except TypeError:
            return
        changed = False
        for cookie in cookies:
            domain = str(getattr(cookie, "domain", "") or "")
            if not (domain.lstrip(".") == "hh.ru" or domain.endswith(".hh.ru")):
                continue
            key = (
                str(getattr(cookie, "name", "") or ""),
                domain,
                str(getattr(cookie, "path", "/") or "/"),
            )
            value = existing.get(
                key, {"name": key[0], "domain": domain, "path": key[2]}
            )
            new_value = str(getattr(cookie, "value", "") or "")
            changed = changed or value.get("value") != new_value
            value["value"] = new_value
            existing[key] = value
        if changed:
            self.browser_session.cookies = list(existing.values())
            self.browser_session.xsrf_token = extract_xsrf_token(
                cookies=self.browser_session.cookies
            )
            self.browser_session.save()


class HHNativeApplicationTransport:
    """Resolve HH native challenges inside the executor's real transport path."""

    def __init__(
        self,
        client: Any,
        browser: HHBrowserApplicationAdapter,
        tests: HHVacancyTestTransport,
        ai: HHChallengeAI,
        *,
        settings_provider: Callable[[], Any],
        candidate_provider: Callable[[], Mapping[str, Any]],
        resume_provider: Callable[[str], Mapping[str, Any]],
        mapper: GroundedAnswerMapper | None = None,
    ) -> None:
        self.client = client
        self.browser = browser
        self.tests = tests
        self.ai = ai
        self.settings_provider = settings_provider
        self.candidate_provider = candidate_provider
        self.resume_provider = resume_provider
        self.mapper = mapper or GroundedAnswerMapper()

    def apply_outcome(
        self,
        vacancy_id: str,
        resume_id: str,
        message: str,
        *,
        timeout_seconds: int,
    ) -> DispatchOutcome:
        application = self._application_settings()
        try:
            vacancy = self.client.get_vacancy(vacancy_id)
        except Exception:
            vacancy = {}
        if isinstance(vacancy, Mapping) and vacancy.get("has_test") is True:
            return self._apply_test(vacancy_id, resume_id, message, application)
        return self._apply_direct(
            vacancy_id,
            resume_id,
            message,
            timeout_seconds=timeout_seconds,
            application=application,
        )

    def _apply_direct(
        self,
        vacancy_id: str,
        resume_id: str,
        message: str,
        *,
        timeout_seconds: int,
        application: Mapping[str, Any],
    ) -> DispatchOutcome:
        automatic_captcha_available = (
            application["captcha_mode"] == "vision_then_manual"
        )
        for challenge_number in range(2):
            outcome, runtime_location = self._client_apply(
                vacancy_id, resume_id, message, timeout_seconds=timeout_seconds
            )
            if outcome.code == "manual_captcha":
                if (
                    not automatic_captcha_available
                    or challenge_number >= 1
                    or not self._solve_captcha(
                        runtime_location or outcome.location, application
                    )
                ):
                    return outcome
                continue
            if outcome.code in {"form_required", "manual_assessment"}:
                handled = self._handle_browser_flow(
                    runtime_location or outcome.location,
                    vacancy_id=vacancy_id,
                    resume_id=resume_id,
                    message=message,
                    application=application,
                )
                if handled is None:
                    continue
                return handled
            return outcome
        return _outcome("manual_captcha")

    def _client_apply(
        self,
        vacancy_id: str,
        resume_id: str,
        message: str,
        *,
        timeout_seconds: int,
    ) -> tuple[DispatchOutcome, str]:
        self._sync_api_cookies()
        detailed = getattr(self.client, "apply_outcome_with_runtime_location", None)
        if callable(detailed):
            result = detailed(
                vacancy_id,
                resume_id,
                message,
                timeout_seconds=timeout_seconds,
            )
        else:
            result = (
                self.client.apply_outcome(
                    vacancy_id,
                    resume_id,
                    message,
                    timeout_seconds=timeout_seconds,
                ),
                "",
            )
        self._sync_browser_cookies()
        return result

    def _sync_api_cookies(self) -> None:
        browser_session = getattr(self.tests, "browser_session", None)
        if browser_session is None:
            return
        if not browser_session.cookies:
            browser_session.load()
        api_session = getattr(self.client, "session", None)
        http = getattr(api_session, "http", None)
        jar = getattr(http, "cookies", None)
        if not callable(getattr(jar, "set", None)):
            return
        for cookie in browser_session.cookies:
            name = str(cookie.get("name") or "").strip()
            if not name:
                continue
            jar.set(
                name,
                str(cookie.get("value") or ""),
                domain=str(cookie.get("domain") or ".hh.ru"),
                path=str(cookie.get("path") or "/"),
            )

    def _sync_browser_cookies(self) -> None:
        browser_session = getattr(self.tests, "browser_session", None)
        if browser_session is None:
            return
        api_session = getattr(self.client, "session", None)
        http = getattr(api_session, "http", None)
        jar = getattr(http, "cookies", None)
        try:
            api_cookies = list(jar)
        except TypeError:
            return
        existing = {
            (
                str(cookie.get("name") or ""),
                str(cookie.get("domain") or ""),
                str(cookie.get("path") or "/"),
            ): dict(cookie)
            for cookie in browser_session.cookies
        }
        changed = False
        for cookie in api_cookies:
            domain = str(getattr(cookie, "domain", "") or "")
            if not (domain.lstrip(".") == "hh.ru" or domain.endswith(".hh.ru")):
                continue
            key = (
                str(getattr(cookie, "name", "") or ""),
                domain,
                str(getattr(cookie, "path", "/") or "/"),
            )
            value = existing.get(
                key, {"name": key[0], "domain": domain, "path": key[2]}
            )
            new_value = str(getattr(cookie, "value", "") or "")
            changed = changed or value.get("value") != new_value
            value["value"] = new_value
            existing[key] = value
        if changed:
            browser_session.cookies = list(existing.values())
            browser_session.xsrf_token = extract_xsrf_token(
                cookies=browser_session.cookies
            )
            browser_session.save()

    def _apply_test(
        self,
        vacancy_id: str,
        resume_id: str,
        message: str,
        application: Mapping[str, Any],
    ) -> DispatchOutcome:
        mode = str(application["screening_mode"])
        if mode == "off":
            return _outcome("screening_disabled")
        if mode != "ai":
            return _outcome("manual_assessment")
        for challenge_number in range(2):
            outcome, runtime_location = self.tests.apply(vacancy_id, resume_id, message)
            if outcome.code != "manual_captcha":
                return outcome
            if (
                application["captcha_mode"] != "vision_then_manual"
                or challenge_number >= 1
                or not self._solve_captcha(
                    runtime_location or outcome.location, application
                )
            ):
                return outcome
        return _outcome("manual_captcha")

    def _handle_browser_flow(
        self,
        runtime_url: str,
        *,
        vacancy_id: str,
        resume_id: str,
        message: str,
        application: Mapping[str, Any],
    ) -> DispatchOutcome | None:
        if not runtime_url:
            return _outcome("manual_assessment")
        runtime_url = urljoin(
            f"{getattr(self.tests, 'base_url', 'https://hh.ru').rstrip('/')}/",
            runtime_url,
        )
        try:
            flow = self.browser.inspect(runtime_url)
        except Exception:
            return _outcome("form_required", location=runtime_url)
        if flow.kind == "captcha":
            if application[
                "captcha_mode"
            ] == "vision_then_manual" and self._solve_captcha(
                flow.runtime_url or runtime_url, application
            ):
                return None
            return _outcome("manual_captcha", location=flow.url)
        if flow.kind == "auth":
            return _outcome("auth_expired", location=flow.url)
        if flow.kind == "assessment":
            return self._apply_test(vacancy_id, resume_id, message, application)
        mode = str(application["form_mode"])
        if mode == "off":
            return _outcome("form_disabled", location=flow.url)
        try:
            candidate = copy.deepcopy(dict(self.candidate_provider()))
            resume = copy.deepcopy(dict(self.resume_provider(resume_id)))
        except Exception:
            return _outcome("missing_required_data", location=flow.url)
        mapping = self.mapper.map(
            flow.fields,
            candidate,
            resume,
            prepared={"cover_letter": message},
        )
        answers = dict(mapping.answers)
        if mode == "profile_grounded":
            if mapping.outcome != "mapped":
                return _outcome(mapping.outcome, location=flow.url)
        else:
            for field in _unique_fields(flow.fields):
                if field.name in answers:
                    continue
                try:
                    answers[field.name] = self.ai.answer_form_field(
                        field,
                        candidate=candidate,
                        resume=resume,
                    )
                except ChallengeAIError:
                    if field.required:
                        return _outcome("ai_unavailable", location=flow.url)
        if any(field.required and field.name not in answers for field in flow.fields):
            return _outcome("missing_required_data", location=flow.url)
        captcha_solver = (
            self.ai if application["captcha_mode"] == "vision_then_manual" else None
        )
        return self.browser.submit_grounded_form(
            flow,
            answers,
            captcha_solver=captcha_solver,
            captcha_attempts=int(application["challenge_attempts"]),
        )

    def _solve_captcha(
        self,
        runtime_url: str,
        application: Mapping[str, Any],
    ) -> bool:
        if not runtime_url:
            return False
        runtime_url = urljoin(
            f"{getattr(self.tests, 'base_url', 'https://hh.ru').rstrip('/')}/",
            runtime_url,
        )
        outcome = self.browser.solve_captcha(
            runtime_url,
            self.ai,
            attempts=int(application["challenge_attempts"]),
        )
        return outcome.code == "applied"

    def _application_settings(self) -> Mapping[str, Any]:
        settings = self.settings_provider()
        application = getattr(settings, "application", None)
        if not isinstance(application, Mapping):
            raise TypeError("settings provider did not return application settings")
        return copy.deepcopy(dict(application))


def _unique_fields(fields: Sequence[BrowserField]) -> tuple[BrowserField, ...]:
    result: list[BrowserField] = []
    seen: set[str] = set()
    for field in fields:
        if field.name in seen:
            continue
        seen.add(field.name)
        result.append(field)
    return tuple(result)


def _test_response_outcome(response: Any, runtime_location: str) -> DispatchOutcome:
    if _looks_like_auth(response, runtime_location):
        return _outcome(
            "auth_expired",
            certainty=DeliveryCertainty.DEFINITE_RESPONSE,
            location=runtime_location,
        )
    if _looks_like_captcha(response, runtime_location):
        return _outcome(
            "manual_captcha",
            certainty=DeliveryCertainty.DEFINITE_RESPONSE,
            location=runtime_location,
        )
    try:
        data = response.json()
    except ValueError:
        return _outcome(
            "post_dispatch_parse_error",
            certainty=DeliveryCertainty.POSSIBLY_SENT,
            status_code=int(response.status_code),
            location=runtime_location,
        )
    if not isinstance(data, Mapping):
        return _outcome(
            "post_dispatch_parse_error",
            certainty=DeliveryCertainty.POSSIBLY_SENT,
            status_code=int(response.status_code),
            location=runtime_location,
        )
    success = data.get("success")
    if success is True or str(success).casefold() == "true":
        return _outcome(
            "applied",
            certainty=DeliveryCertainty.DEFINITE_RESPONSE,
            status_code=int(response.status_code),
            location=runtime_location,
            payload={"transport": "hh_web_test"},
        )
    error = str(data.get("error") or "").strip().casefold()
    normalized = error.replace("-", "_")
    if normalized in {"negotiations_limit_exceeded", "vacancy_response_limit"}:
        code = "hh_daily_limit"
    elif normalized in {"already_applied", "negotiation_already_exists"}:
        code = "duplicate"
    elif "captcha" in normalized:
        code = "manual_captcha"
    elif int(response.status_code) == 429:
        code = "rate_limited"
    elif int(response.status_code) >= 500:
        code = "server_error"
    else:
        code = "invalid_request"
    return _outcome(
        code,
        certainty=DeliveryCertainty.DEFINITE_RESPONSE,
        status_code=int(response.status_code),
        location=runtime_location,
        payload={"transport": "hh_web_test", "error": error[:200]},
    )


def _http_outcome(response: Any, location: str, *, post: bool) -> DispatchOutcome:
    status = int(response.status_code)
    if status == 401:
        code = "auth_expired"
    elif status == 403:
        code = "forbidden"
    elif status == 429:
        code = "rate_limited"
    elif status >= 500:
        code = "server_error"
    elif 300 <= status < 400:
        code = "form_required"
    else:
        code = "invalid_request"
    return _outcome(
        code,
        certainty=(
            DeliveryCertainty.POSSIBLY_SENT
            if post
            else DeliveryCertainty.DEFINITELY_NOT_SENT
        ),
        status_code=status,
        location=location,
    )


def _looks_like_auth(response: Any, location: str) -> bool:
    path = urlsplit(location).path.casefold()
    text = str(getattr(response, "text", "") or "").casefold()
    return "/account/login" in path or any(
        marker in text
        for marker in (
            'data-qa="login-input-username"',
            "data-qa='login-input-username'",
            'data-qa="login-form"',
            "data-qa='login-form'",
        )
    )


def _looks_like_captcha(response: Any, location: str) -> bool:
    path = urlsplit(location).path.casefold()
    text = str(getattr(response, "text", "") or "").casefold()
    return "captcha" in path or any(
        marker in text
        for marker in (
            "account-captcha-picture",
            "account-captcha-input",
            'data-qa="captcha',
            "data-qa='captcha",
            "не робот",
        )
    )


def _response_location(response: Any) -> str:
    raw = str(getattr(response, "headers", {}).get("Location", "") or "")
    current = str(getattr(response, "url", "") or "")
    return urljoin(current, raw) if raw else current


def _outcome(
    code: str,
    *,
    certainty: DeliveryCertainty = DeliveryCertainty.DEFINITELY_NOT_SENT,
    status_code: int | None = None,
    location: str = "",
    payload: dict[str, Any] | None = None,
) -> DispatchOutcome:
    return DispatchOutcome(
        code,
        certainty,
        status_code=status_code,
        location=sanitize_hh_url(location),
        payload=payload or {},
    )


__all__ = ["HHNativeApplicationTransport", "HHVacancyTestTransport"]
