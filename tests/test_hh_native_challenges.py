from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from work_hunter.hh_autopilot.browser import (
    BrowserField,
    BrowserFlow,
    HHBrowserApplicationAdapter,
)
from work_hunter.hh_autopilot.challenge_ai import HHChallengeAI
from work_hunter.hh_autopilot.native_transport import (
    HHNativeApplicationTransport,
    HHVacancyTestTransport,
)
from work_hunter.hh_autopilot.types import DeliveryCertainty, DispatchOutcome
from work_hunter.hh_transport.browser_session import HHBrowserSession


def _ai_config() -> dict:
    return {
        "backend": "direct",
        "api_key": "key",
        "base_url": "https://ai.example/v1/chat/completions",
        "model": "vision-model",
        "tests": {"max_retries": 0, "retry_base_seconds": 0},
        "captcha": {"max_retries": 0, "retry_base_seconds": 0},
    }


@pytest.mark.parametrize("roles,expected", [
    ([{"id": "40"}], "role_not_allowed"),
    ([], "read_parse_error"),
    ([{"id": "96"}], "applied"),
])
def test_native_transport_rechecks_roles_before_post(roles, expected):
    calls = []
    client = SimpleNamespace(
        get_vacancy=lambda _id: {"professional_roles": roles},
        apply_outcome=lambda *args, **kwargs: calls.append(args) or DispatchOutcome(
            "applied", DeliveryCertainty.DEFINITE_RESPONSE,
        ),
    )
    native = HHNativeApplicationTransport(
        client, None, SimpleNamespace(), None,
        settings_provider=lambda: SimpleNamespace(
            application={"captcha_mode": "manual_handoff"},
            filters={"allowed_role_families": ["96"]},
        ),
        candidate_provider=dict, resume_provider=lambda _: {},
    )
    outcome = native.apply_outcome("v-1", "r-1", "", timeout_seconds=30)
    assert outcome.code == expected
    assert len(calls) == (1 if expected == "applied" else 0)
    if not calls:
        assert outcome.certainty is DeliveryCertainty.DEFINITELY_NOT_SENT


def test_native_transport_accepts_curated_vacancy_with_different_hh_role():
    posted = []
    client = SimpleNamespace(
        get_vacancy=lambda _id: {"professional_roles": [{"id": "40"}]},
        apply_outcome=lambda *args, **kwargs: posted.append(args) or DispatchOutcome(
            "applied", DeliveryCertainty.DEFINITE_RESPONSE,
        ),
    )
    native = HHNativeApplicationTransport(
        client, None, SimpleNamespace(), None,
        settings_provider=lambda: SimpleNamespace(
            application={"captcha_mode": "manual_handoff"},
            filters={
                "allowed_role_families": ["96"],
                "allowed_vacancy_ids": ["v-1"],
            },
        ),
        candidate_provider=dict, resume_provider=lambda _: {},
    )
    assert native.apply_outcome("v-1", "r-1", "letter", timeout_seconds=30).code == "applied"
    assert len(posted) == 1
    assert native.apply_outcome("v-2", "r-1", "letter", timeout_seconds=30).code == "role_not_allowed"
    assert len(posted) == 1


def test_preflight_read_failure_is_retryable_and_never_posts():
    from work_hunter.hh_transport.errors import HHNetworkError

    def unavailable(_id):
        raise HHNetworkError("temporary read failure")

    native = HHNativeApplicationTransport(
        SimpleNamespace(get_vacancy=unavailable), None, None, None,
        settings_provider=lambda: SimpleNamespace(application={}),
        candidate_provider=dict, resume_provider=lambda _: {},
    )
    result = native.apply_outcome("v-1", "r-1", "", timeout_seconds=30)
    assert result.code == "pre_dispatch_network_error"
    assert result.certainty is DeliveryCertainty.DEFINITELY_NOT_SENT
    assert result.payload["stage"] == "preflight"


@pytest.mark.parametrize("browser_result", ["applied", "manual_captcha"])
def test_preflight_stateful_captcha_is_solved_before_single_post(browser_result):
    from work_hunter.hh_transport.errors import HHForbiddenError

    reads, posts, browser_urls = [], [], []

    def vacancy(_id):
        reads.append(_id)
        if len(reads) == 1:
            raise HHForbiddenError(
                "captcha required", status_code=403, code="captcha_required",
                payload={"challenge_metadata": {"captcha_url":
                    "https://hh.ru/account/captcha?state=opaque&token=secret"}},
            )
        return {"has_test": False}

    browser = SimpleNamespace(solve_captcha=lambda url, *_args, **_kwargs:
                              browser_urls.append(url) or DispatchOutcome(
                                  browser_result, DeliveryCertainty.DEFINITE_RESPONSE))
    client = SimpleNamespace(
        get_vacancy=vacancy,
        apply_outcome=lambda *args, **_kwargs: posts.append(args) or DispatchOutcome(
            "applied", DeliveryCertainty.DEFINITE_RESPONSE),
    )
    native = HHNativeApplicationTransport(
        client, browser, SimpleNamespace(base_url="https://hh.ru"), None,
        settings_provider=lambda: SimpleNamespace(application={
            "captcha_mode": "vision_then_manual", "challenge_attempts": 2,
        }, filters={}),
        candidate_provider=dict, resume_provider=lambda _: {},
    )

    assert native.apply_outcome("v-1", "r-1", "letter", timeout_seconds=30).code == "applied"
    assert len(reads) == 2
    assert len(posts) == 1
    assert browser_urls == [
        "https://hh.ru/account/captcha?state=opaque&backurl=https%3A%2F%2Fhh.ru%2F"
    ]


def test_challenge_ai_uses_supplied_ids_and_vision_image() -> None:
    calls: list[tuple[list[dict], dict]] = []

    def completion(messages, config):
        calls.append((messages, config))
        if isinstance(messages[-1]["content"], list):
            return " whin  stickybeak\n"
        return "Ответ: 22"

    ai = HHChallengeAI(_ai_config, completion=completion)

    assert (
        ai.answer_test_task(
            "Выберите ответ",
            [{"id": 11, "text": "Нет"}, {"id": 22, "text": "Да"}],
        )
        == "22"
    )
    assert ai.solve_captcha(b"png-bytes") == "whin stickybeak"
    image_url = calls[1][0][-1]["content"][0]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")
    assert calls[1][1]["model"] == "vision-model"


@pytest.mark.parametrize("quote", ['"', "&#34;", "&quot;"])
def test_native_vacancy_test_builds_hh_task_payload(tmp_path, quote) -> None:
    browser_session = HHBrowserSession(cookie_path=tmp_path / "hh.json")
    browser_session.update_from_playwright_context(
        [
            {"name": "hhtoken", "value": "auth", "domain": ".hh.ru"},
            {"name": "_xsrf", "value": "xsrf", "domain": ".hh.ru"},
        ]
    )
    html = (
        '<script>{"page":1,"vacancyTests":{"123":'
        '{"uidPk":"u","guid":"g","startTime":10,"required":true,"tasks":['
        '{"id":1,"description":"2+2?","candidateSolutions":['
        '{"id":10,"text":"3"},{"id":20,"text":"4"}]},'
        '{"id":2,"description":"Почему вы?","candidateSolutions":[]}'
        "]}}}</script>"
    )
    http = _FakeHTTP(html.replace('"', quote))

    def completion(messages, _config):
        prompt = str(messages[-1]["content"])
        return "20" if "2+2" in prompt else "Подхожу по опыту"

    transport = HHVacancyTestTransport(
        browser_session,
        HHChallengeAI(_ai_config, completion=completion),
        http_factory=lambda: http,
    )

    outcome, _runtime_url = transport.apply("123", "resume-1", "Здравствуйте")

    assert outcome.code == "applied"
    assert http.posted["task_1"] == "20"
    assert http.posted["task_2_text"] == "Подхожу по опыту"
    assert http.posted["_xsrf"] == "xsrf"
    assert http.posted["letter"] == "Здравствуйте"


def test_application_captcha_is_solved_in_saved_browser_session_and_retried(
    tmp_path,
) -> None:
    state = {
        "html": '<img data-qa="account-captcha-picture">',
        "captcha_language_button": "English",
        "cookies": [{"name": "hhtoken", "value": "before", "domain": ".hh.ru"}],
    }
    session = HHBrowserSession(cookie_path=tmp_path / "hh.json")
    session.update_from_playwright_context(state["cookies"])
    browser = HHBrowserApplicationAdapter(
        lambda: _CaptchaContext(state),
        session=session,
    )
    client = _CaptchaClient()
    application = {
        "screening_mode": "ai",
        "form_mode": "ai",
        "captcha_mode": "vision_then_manual",
        "challenge_attempts": 2,
    }
    ai = SimpleNamespace(solve_captcha=lambda image: "A7B")
    native = HHNativeApplicationTransport(
        client,
        browser,
        SimpleNamespace(
            apply=lambda *_args: None,
            browser_session=session,
            base_url="https://hh.ru",
        ),
        ai,
        settings_provider=lambda: SimpleNamespace(application=application),
        candidate_provider=dict,
        resume_provider=lambda _resume_id: {},
    )

    outcome = native.apply_outcome(
        "123",
        "resume-1",
        "",
        timeout_seconds=30,
    )

    assert outcome.code == "applied"
    assert client.calls == 2
    assert state["captcha_answer"] == "A7B"
    assert state["wait_state"] == "hidden"
    assert state["image_language"] == "Русский"
    reloaded = HHBrowserSession(cookie_path=tmp_path / "hh.json")
    reloaded.load()
    assert reloaded.load_cookie("hhtoken") == "after"
    assert (
        client.session.http.cookies.get("hhtoken", domain=".hh.ru", path="/") == "after"
    )


def test_redirect_form_keeps_grounded_values_and_ai_fills_the_rest() -> None:
    browser = _FormBrowser()
    application = {
        "screening_mode": "ai",
        "form_mode": "ai",
        "captcha_mode": "vision_then_manual",
        "challenge_attempts": 2,
    }
    ai = SimpleNamespace(
        answer_form_field=lambda _field, **_kwargs: "Да",
        solve_captcha=lambda _image: "A7B",
    )
    native = HHNativeApplicationTransport(
        _FormClient(),
        browser,
        SimpleNamespace(base_url="https://hh.ru"),
        ai,
        settings_provider=lambda: SimpleNamespace(application=application),
        candidate_provider=lambda: {"first_name": "Анна"},
        resume_provider=lambda _resume_id: {"title": "Python"},
    )

    outcome = native.apply_outcome("123", "resume-1", "", timeout_seconds=30)

    assert outcome.code == "applied"
    assert browser.answers == {"first_name": "Анна", "custom_question": "Да"}


class _Response:
    def __init__(self, status_code, *, text="", url="https://hh.ru/", data=None):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = {}
        self._data = data

    def json(self):
        return self._data


class _FakeHTTP:
    def __init__(self, html):
        self.html = html
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self.posted = {}

    def get(self, url, **_kwargs):
        return _Response(200, text=self.html, url=url)

    def post(self, url, *, data, **_kwargs):
        self.posted = dict(data)
        return _Response(200, url=url, data={"success": "true"})


class _CaptchaLocator:
    def __init__(self, state, selector):
        self.state = state
        self.selector = selector

    def screenshot(self):
        self.state["image_language"] = self.state.get("captcha_language_button")
        return b"captcha-png"

    def is_visible(self):
        return not self.state.get("captcha_solved", False)

    def count(self):
        return int(bool(self.state.get("captcha_language_button")))

    def inner_text(self):
        return self.state["captcha_language_button"]

    def click(self):
        assert self.selector == '[data-qa="captcha-language"]'
        self.state["captcha_language_button"] = "Русский"

    def fill(self, value):
        self.state["captcha_answer"] = value

    def press(self, key):
        assert key == "Enter"

    def wait_for(self, *, state, **_kwargs):
        self.state["wait_state"] = state
        assert state == "hidden"
        self.state["captcha_solved"] = True
        # HH can retain CAPTCHA translations/markers in scripts after redirect.
        self.state["html"] = '<main>solved</main><script>"account-captcha-input"</script>'
        self.state["cookies"] = [
            {"name": "hhtoken", "value": "after", "domain": ".hh.ru"}
        ]


class _CaptchaPage:
    def __init__(self, state):
        self.state = state
        self.url = "https://hh.ru/account/captcha?token=secret"

    def goto(self, url, **_kwargs):
        self.url = url

    def content(self):
        return self.state["html"]

    def locator(self, selector):
        return _CaptchaLocator(self.state, selector)

    def wait_for_load_state(self, state, **_kwargs):
        self.state["wait_state"] = state


class _CaptchaContext:
    def __init__(self, state):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def new_page(self):
        return _CaptchaPage(self.state)

    def add_cookies(self, cookies):
        self.state["cookies"] = cookies

    def cookies(self):
        return self.state["cookies"]


class _CaptchaClient:
    def __init__(self):
        self.calls = 0
        self.session = SimpleNamespace(
            http=SimpleNamespace(cookies=requests.cookies.RequestsCookieJar())
        )

    def get_vacancy(self, _vacancy_id):
        return {"has_test": False}

    def apply_outcome_with_runtime_location(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return (
                DispatchOutcome(
                    "manual_captcha",
                    DeliveryCertainty.DEFINITE_RESPONSE,
                    location="https://hh.ru/account/captcha?state=opaque",
                ),
                "https://hh.ru/account/captcha?state=opaque&token=secret",
            )
        return (
            DispatchOutcome("applied", DeliveryCertainty.DEFINITE_RESPONSE),
            "",
        )


class _FormClient:
    def get_vacancy(self, _vacancy_id):
        return {"has_test": False}

    def apply_outcome_with_runtime_location(self, *_args, **_kwargs):
        return (
            DispatchOutcome(
                "form_required",
                DeliveryCertainty.DEFINITE_RESPONSE,
                location="https://hh.ru/applicant/vacancy_response",
            ),
            "https://hh.ru/applicant/vacancy_response?token=secret",
        )


class _FormBrowser:
    def __init__(self):
        self.answers = None

    def inspect(self, url):
        return BrowserFlow.form(
            "https://hh.ru/applicant/vacancy_response",
            (
                BrowserField("first_name", required=True),
                BrowserField(
                    "custom_question",
                    required=True,
                    prompt="Готовы начать?",
                ),
            ),
            runtime_url=url,
        )

    def submit_grounded_form(self, _flow, answers, **_kwargs):
        self.answers = dict(answers)
        return DispatchOutcome("applied", DeliveryCertainty.DEFINITE_RESPONSE)
