from __future__ import annotations

from pathlib import Path

from work_hunter.external_apply import (
    ExternalApplyDispatcher,
    ExternalApplyRequest,
    _already_applied,
    _application_scope,
    _application_success,
    resolve_form_answer,
)
from work_hunter.models import Job


def request(tmp_path: Path, **overrides) -> ExternalApplyRequest:
    values = {
        "root": tmp_path,
        "job": Job(
            source="linkedin",
            source_id="123",
            url="https://www.linkedin.com/jobs/view/123",
            title="Python Engineer",
            company="Acme",
        ),
        "letter": "Hello Acme",
        "profile": {
            "name": "Ivan Petrov",
            "email": "ivan@example.com",
            "phone": "+79990000000",
            "city": "Moscow",
            "salary_min": 250000,
            "resume_path": "",
        },
        "about": {"summary": "Python developer"},
        "ai_config": {},
        "source_config": {
            "apply_adapter": {
                "transport": "browser",
                "kind": "linkedin_easy_apply",
                "answers": {"work authorization": "Yes"},
            }
        },
        "global_config": {"enabled": True, "transport": "browser"},
        "resume_id": None,
    }
    values.update(overrides)
    return ExternalApplyRequest(**values)


def test_browser_plan_is_ready_and_uses_private_profile(tmp_path: Path) -> None:
    plan = ExternalApplyDispatcher().plan(request(tmp_path))

    assert plan["status"] == "ready"
    assert plan["mode"] == "browser"
    assert plan["browser_profile"].endswith(
        str(Path(".work-hunter") / "browser" / "linkedin")
    )


def test_resolve_form_answer_prefers_explicit_and_profile_facts(tmp_path: Path) -> None:
    apply_request = request(tmp_path)

    assert resolve_form_answer(
        "Are you legally authorized to work?",
        field_type="radio",
        options=["Yes", "No"],
        request=apply_request,
    ) == "Yes"
    assert resolve_form_answer(
        "Email address",
        field_type="email",
        options=None,
        request=apply_request,
    ) == "ivan@example.com"
    assert resolve_form_answer(
        "Cover letter",
        field_type="textarea",
        options=None,
        request=apply_request,
    ) == "Hello Acme"


def test_resolve_form_answer_uses_ai_only_for_unknown_fact(tmp_path: Path) -> None:
    apply_request = request(
        tmp_path,
        ai_config={
            "backend": "direct",
            "api_key": "x",
            "base_url": "https://ai.example/v1/chat/completions",
            "model": "model",
        },
    )
    calls = []

    def completion(messages, config):
        calls.append((messages, config))
        return "3"

    answer = resolve_form_answer(
        "Years of Python experience",
        field_type="number",
        options=None,
        request=apply_request,
        completion=completion,
    )

    assert answer == "3"
    assert len(calls) == 1


def test_session_plan_requires_endpoint_fields(tmp_path: Path) -> None:
    apply_request = request(
        tmp_path,
        source_config={
            "apply_adapter": {
                "transport": "session",
                "session_name": "linkedin",
            }
        },
    )

    plan = ExternalApplyDispatcher().plan(apply_request)

    assert plan["status"] == "blocked"
    assert "url" in plan["message"]


class FakeCandidate:
    def __init__(self, visible: bool = True) -> None:
        self.visible = visible

    def is_visible(self) -> bool:
        return self.visible

    def is_enabled(self) -> bool:
        return True


class FakeLocator:
    def __init__(self, candidates: list[FakeCandidate]) -> None:
        self.candidates = candidates

    def count(self) -> int:
        return len(self.candidates)

    def nth(self, index: int) -> FakeCandidate:
        return self.candidates[index]


class FakePage:
    def __init__(self, visible_selectors: set[str], *, url: str = "https://example.test/job") -> None:
        self.visible_selectors = visible_selectors
        self.url = url

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(
            [FakeCandidate()] if selector in self.visible_selectors else []
        )


def test_linkedin_form_discovery_is_scoped_to_easy_apply_modal() -> None:
    page = FakePage({".jobs-easy-apply-modal"})

    assert _application_scope(page, "linkedin") is not page
    assert _application_scope(page, "indeed") is page


def test_linkedin_already_applied_and_success_markers_are_detected() -> None:
    page = FakePage({"a.jobs-s-apply__application-link"})

    assert _already_applied(page, "linkedin") is True
    assert _application_success(page, "linkedin", page.url) is True


def test_linkedin_url_change_alone_is_not_submission_confirmation() -> None:
    page = FakePage(set(), url="https://www.linkedin.com/jobs/view/456")

    assert _application_success(
        page,
        "linkedin",
        "https://www.linkedin.com/jobs/view/123",
    ) is False
