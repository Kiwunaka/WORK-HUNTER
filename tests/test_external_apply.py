from __future__ import annotations

from pathlib import Path

import pytest

from work_hunter.external_apply import (
    ExternalApplyDispatcher,
    ExternalApplyRequest,
    _already_applied,
    _application_scope,
    _application_success,
    _apply_with_session,
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
    ) is None  # An unscoped fuzzy answer is not authorization for a legal assertion.
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


def test_resolve_form_answer_does_not_turn_ai_text_into_candidate_facts(tmp_path: Path) -> None:
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

    assert answer is None
    assert calls == []


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
    assert _application_scope(page, "indeed") is None


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


@pytest.mark.parametrize("path", ["error", "step-2", "thanks", "login"])
def test_external_redirect_is_not_a_receipt(path):
    page = FakePage(set(), url=f"https://example.test/{path}")
    assert not _application_success(page, "habr", "https://example.test/apply")


def test_receipt_on_a_different_linkedin_vacancy_is_not_confirmation():
    page = FakePage({"a.jobs-s-apply__application-link"}, url="https://www.linkedin.com/jobs/view/other")
    assert not _application_success(page, "linkedin", "https://www.linkedin.com/jobs/view/123")


@pytest.mark.parametrize("selector", ['button:has-text("Done")', 'text="Thanks for applying"'])
def test_generic_text_is_not_a_source_receipt(selector):
    assert not _application_success(FakePage({selector}), "linkedin", "https://example.test/apply")


@pytest.mark.parametrize("code,payload", [
    (200, {"success": False, "errors": ["invalid"]}),
    (202, {"status": "processing"}),
    (200, {"success": True}),
    (201, {"application_id": "unverified-field"}),
    (500, {"error": "response failed after commit"}),
    (0, None),
])
def test_session_transport_is_not_a_delivery_verifier(tmp_path, monkeypatch, code, payload):
    monkeypatch.setattr("work_hunter.external_apply.call_external_session", lambda *a, **kw: {
        "status": "ok" if 200 <= code < 300 else "http_error",
        "response": {"status": code, "json_preview": payload},
    })
    result = _apply_with_session(request(tmp_path))
    assert result.status == "submission_unknown"
    assert not result.applied
    assert "reconciliation_required" in result.blockers


def test_session_timeout_keeps_uncertainty(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise TimeoutError("response lost after POST")

    monkeypatch.setattr("work_hunter.external_apply.call_external_session", timeout)
    result = _apply_with_session(request(tmp_path))
    assert result.status == "submission_unknown"
    assert not result.applied
