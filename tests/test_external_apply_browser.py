from __future__ import annotations

from dataclasses import replace

import pytest
from playwright.sync_api import sync_playwright

from work_hunter.external_apply import ExternalApplyRequest, _complete_browser_form
from work_hunter.models import Job


@pytest.fixture
def form_page(browser_artifacts):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(service_workers="block")
        context.route("**/*", lambda route: route.abort())
        try:
            with browser_artifacts(context):
                yield context.new_page()
        finally:
            context.close()
            browser.close()


def application(tmp_path, source="linkedin"):
    return ExternalApplyRequest(
        root=tmp_path,
        job=Job(source=source, source_id="fixture", url="https://example.test/job", title="Fixture"),
        letter="Fixture", profile={"email": "candidate@example.test", "phone": "123456789"},
        about={}, ai_config={}, source_config={}, global_config={"answer_with_ai": False},
    )


@pytest.mark.parametrize("source,expected", [("linkedin", "applied"), ("habr", "submission_unknown")])
def test_three_step_wizard_submits_once_after_review(form_page, tmp_path, source, expected):
    form_page.set_content('''
      <form>
        <label>Email<input type="email" required></label>
        <button type="submit">Next</button>
      </form>
      <script>
      window.stage = 1; window.deliveries = 0; window.answers = [];
      const form = document.querySelector('form');
      form.onsubmit = (event) => {
        event.preventDefault();
        window.answers.push(form.querySelector('input')?.value || 'reviewed');
        if (window.stage === 1) {
          form.innerHTML = '<label>Phone<input type="tel" required></label><button type="submit">Review</button>';
        } else if (window.stage === 2) {
          form.innerHTML = '<p>Review application</p><button type="submit">Submit application</button>';
        } else {
          window.deliveries++;
          form.innerHTML = '<div data-test-modal-id="easy-apply-done">Application submitted</div>';
        }
        window.stage++;
      };
      </script>
    ''')
    result = _complete_browser_form(form_page, application(tmp_path, source), {}, [])
    assert result.status == expected
    assert result.applied is (expected == "applied")
    assert form_page.evaluate("window.deliveries") == 1
    assert form_page.evaluate("window.answers") == ["candidate@example.test", "123456789", "reviewed"]
    assert result.steps == ["form_step_1", "form_step_2", "final_submit_started", "submitted"]


def test_generic_submit_attribute_does_not_authorize_final_action(form_page, tmp_path):
    form_page.set_content('''
      <form onsubmit="event.preventDefault(); window.clicked=true">
        <button type="submit">Proceed</button>
      </form>
    ''')
    result = _complete_browser_form(form_page, application(tmp_path), {}, [])
    assert result.status == "blocked"
    assert not form_page.evaluate("Boolean(window.clicked)")


def test_unrelated_form_is_not_filled(form_page, tmp_path):
    form_page.set_content('''
      <form><input aria-label="Email" required></form>
      <form><input aria-label="Phone" required><button>Submit application</button></form>
    ''')
    result = _complete_browser_form(form_page, application(tmp_path), {}, [])
    assert result.blockers == ["application_scope_unknown"]
    assert form_page.locator("input").evaluate_all("els => els.every(el => el.value === '')")


def test_final_click_timeout_remains_unknown(form_page, tmp_path, monkeypatch):
    form_page.set_content('<form><button>Submit application</button></form>')
    calls = []

    class AcceptedButTimedOut:
        def click(self):
            calls.append("accepted")
            raise TimeoutError("response lost")

    monkeypatch.setattr("work_hunter.external_apply._find_submit_action", lambda *a: AcceptedButTimedOut())
    result = _complete_browser_form(form_page, replace(application(tmp_path), letter="test"), {}, [])
    assert result.status == "submission_unknown"
    assert not result.applied
    assert calls == ["accepted"]


def test_required_checkboxes_are_not_automatically_confirmed(form_page, tmp_path):
    form_page.set_content('''<form>
      <label>Data processing<input type="checkbox" required></label>
      <label>Work authorization<input type="checkbox" required></label>
      <label>Information is true<input type="checkbox" required checked></label>
      <button>Submit application</button></form>''')
    result = _complete_browser_form(form_page, application(tmp_path), {}, [])
    assert result.status == "needs_answers"
    assert len(result.blockers) == 3
    assert form_page.locator('input:checked').count() == 1
