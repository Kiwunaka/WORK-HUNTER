from __future__ import annotations

from work_hunter.hh_agent.forms import detect_manual_form_url, draft_form_review
from work_hunter.hh_transport.challenges import ChallengeKind, HHChallengeHandler
from work_hunter.services import WorkHunter


def _manual_form() -> dict:
    return {
        "response_url": "https://hh.ru/applicant/vacancy_response?vacancyId=100",
        "fields": [
            {"name": "full_name", "label": "Full name", "required": True},
            {"name": "email", "label": "Email", "required": True},
            {"name": "message", "label": "Cover letter", "required": True},
            {"name": "availability", "label": "Earliest start date", "required": True},
        ],
    }


def test_detect_manual_form_url_and_draft_known_answers():
    review = draft_form_review(
        _manual_form(),
        persona={
            "facts": {
                "name": "Alex Candidate",
                "email": "alex@example.test",
                "summary": "Python backend developer.",
            },
            "body": "Backend engineer, Python/FastAPI.",
        },
        resume={"id": "res-1", "title": "Backend Python"},
        vacancy={"id": "100", "name": "Python Developer", "employer": {"name": "Acme"}},
    )

    assert detect_manual_form_url(_manual_form()) == "https://hh.ru/applicant/vacancy_response?vacancyId=100"
    assert review.form_url == "https://hh.ru/applicant/vacancy_response?vacancyId=100"
    assert review.answers["full_name"] == "Alex Candidate"
    assert review.answers["email"] == "alex@example.test"
    assert "Python Developer" in review.answers["message"]
    assert [field["name"] for field in review.unknown_fields] == ["availability"]


def test_workhunter_form_review_persists_journal_and_escalates_unknown_fields(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.config["profiles"]["default"]["name"] = "Alex Candidate"
    app.config["profiles"]["default"]["email"] = "alex@example.test"
    app.config["about"]["summary"] = "Python backend developer."

    result = app.review_hh_manual_form(
        _manual_form(),
        vacancy={"id": "100", "name": "Python Developer", "employer": {"name": "Acme"}},
        resume={"id": "res-1", "title": "Backend Python"},
    )

    pending = app.storage.list_hh_pending_messages(status="pending")
    reviews = app.storage.list_hh_form_reviews()

    assert result["status"] == "needs_approval"
    assert result["pending_message_id"] == pending[0].id
    assert pending[0].action_type == "form_submit"
    assert pending[0].reason == "unknown_form_fields"
    assert pending[0].payload["unknown_fields"][0]["name"] == "availability"
    assert reviews[0]["vacancy_id"] == "100"
    assert reviews[0]["resume_id"] == "res-1"
    assert reviews[0]["status"] == "needs_approval"


def test_form_mode_off_blocks_and_still_journals_review(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["form_mode"] = "off"

    result = app.review_hh_manual_form(_manual_form(), vacancy={"id": "100"}, resume={"id": "res-1"})

    assert result["status"] == "blocked"
    assert result["reason"] == "form_mode_off"
    assert app.storage.list_hh_pending_messages() == []
    assert app.storage.list_hh_form_reviews()[0]["status"] == "blocked"


def test_form_submit_requires_explicit_confirmation_even_in_agent_mode(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["form_mode"] = "agent"
    form = {
        "form_url": "https://hh.ru/applicant/vacancy_response?vacancyId=100",
        "fields": [{"name": "message", "label": "Cover letter", "required": True}],
    }

    result = app.review_hh_manual_form(
        form,
        vacancy={"id": "100", "name": "Python Developer"},
        resume={"id": "res-1"},
        submit=True,
        confirm=False,
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "explicit_confirmation_required"
    assert result["requires_confirmation"] is True
    assert "submitted" not in result


def test_challenge_handler_detects_form_urls_and_marks_modes():
    manual = HHChallengeHandler(challenge_mode="manual").classify_apply_result(
        {"status": "redirect", "response_url": "https://hh.ru/applicant/vacancy_response?vacancyId=100"}
    )
    off = HHChallengeHandler(challenge_mode="off").classify_apply_result(
        {"status": "error", "error": "captcha_required"}
    )
    ai = HHChallengeHandler(challenge_mode="ai").classify_apply_result(
        {"status": "error", "error": "test_required"}
    )

    assert manual.kind == ChallengeKind.MANUAL_FORM_REQUIRED
    assert manual.url == "https://hh.ru/applicant/vacancy_response?vacancyId=100"
    assert manual.metadata["challenge_mode"] == "manual"
    assert off.metadata["challenge_mode"] == "off"
    assert ai.metadata["challenge_mode"] == "ai"
