from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .ai_backends import chat_completion
from .external_sessions import call_external_session
from .models import Job


BROWSER_LOGIN_URLS = {
    "linkedin": "https://www.linkedin.com/login",
    "indeed": "https://secure.indeed.com/auth",
    "getmatch": "https://getmatch.ru/auth/signin",
    "rvc": "https://app.rvc.global/auth/sign-in",
    "habr": "https://career.habr.com/login",
    "geekjob": "https://geekjob.ru/login",
    "hirehi": "https://hirehi.ru/login",
    "careerspace": "https://careerspace.app/login",
    "another_it": "https://another-it.ru/login",
    "jabka": "https://jabka.work/",
    "relocate_me": "https://relocate.me/",
}


@dataclass(frozen=True)
class ExternalApplyRequest:
    """Normalized application request for a non-HH source."""

    root: Path
    job: Job
    letter: str
    profile: dict[str, Any]
    about: dict[str, Any]
    ai_config: dict[str, Any]
    source_config: dict[str, Any]
    global_config: dict[str, Any]
    resume_id: str | None = None
    bundle: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExternalApplyResult:
    """Result returned by an external application adapter."""

    status: str
    mode: str
    message: str = ""
    applied: bool = False
    blockers: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    screenshot: str = ""
    raw_result: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExternalApplyDispatcher:
    """Select and execute a configured HTTP-session or browser adapter."""

    def plan(self, request: ExternalApplyRequest) -> dict[str, Any]:
        settings = adapter_settings(request)
        if not bool(request.global_config.get("enabled", True)):
            return {
                "status": "blocked",
                "mode": str(settings.get("transport") or "browser"),
                "message": "External apply is disabled in configuration.",
                "risk_flags": ["external_apply_disabled"],
            }
        transport = str(settings.get("transport") or "browser").casefold()
        if transport == "session":
            missing = [
                key
                for key in ("session_name", "url")
                if not str(settings.get(key) or "").strip()
            ]
            return {
                "status": "blocked" if missing else "ready",
                "mode": "session",
                "message": (
                    f"Missing session adapter fields: {', '.join(missing)}"
                    if missing
                    else "Authenticated session apply is configured."
                ),
                "risk_flags": ["external_mutation", "authenticated_session"],
                "adapter": _safe_adapter_view(settings),
            }
        if transport not in {"browser", "auto"}:
            return {
                "status": "blocked",
                "mode": transport,
                "message": f"Unsupported external apply transport: {transport}",
                "risk_flags": ["adapter_not_configured"],
            }
        return {
            "status": "ready",
            "mode": "browser",
            "message": "Persistent-browser application is ready.",
            "risk_flags": [
                "external_mutation",
                "browser_session",
                "interactive_login_may_be_required",
            ],
            "adapter": _safe_adapter_view(settings),
            "browser_profile": str(browser_profile_dir(request)),
        }

    def apply(self, request: ExternalApplyRequest) -> ExternalApplyResult:
        plan = self.plan(request)
        if plan.get("status") != "ready":
            return ExternalApplyResult(
                status="blocked",
                mode=str(plan.get("mode") or "external"),
                message=str(plan.get("message") or "External apply is not ready."),
                blockers=list(plan.get("risk_flags") or []),
            )
        if plan["mode"] == "session":
            return _apply_with_session(request)
        return BrowserApplyAdapter().apply(request)


class BrowserApplyAdapter:
    """Fill common job forms through a persistent Playwright profile."""

    def apply(self, request: ExternalApplyRequest) -> ExternalApplyResult:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return ExternalApplyResult(
                status="blocked",
                mode="browser",
                message='Playwright is missing. Install with: pip install -e ".[browser]"',
                blockers=["playwright_missing"],
            )

        settings = adapter_settings(request)
        browser_name = str(settings.get("browser") or "chromium")
        profile_dir = browser_profile_dir(request)
        profile_dir.mkdir(parents=True, exist_ok=True)
        steps: list[str] = []
        with sync_playwright() as playwright:
            browser_type = getattr(playwright, browser_name, None)
            if browser_type is None:
                return ExternalApplyResult(
                    status="blocked",
                    mode="browser",
                    message=f"Unknown Playwright browser: {browser_name}",
                    blockers=["browser_not_supported"],
                )
            context = browser_type.launch_persistent_context(
                str(profile_dir),
                headless=bool(settings.get("headless", False)),
                slow_mo=max(0, int(settings.get("slow_mo_ms", 100) or 0)),
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                timeout_ms = max(
                    5_000,
                    int(settings.get("timeout_seconds", 45) or 45) * 1000,
                )
                page.set_default_timeout(timeout_ms)
                page.goto(request.job.url, wait_until="domcontentloaded")
                steps.append("opened_job")
                if _login_required(page):
                    if not _wait_for_interactive_login(page, settings):
                        screenshot = _capture_apply_screenshot(
                            page,
                            request,
                            "login-required",
                        )
                        return ExternalApplyResult(
                            status="needs_login",
                            mode="browser",
                            message=(
                                "Login is required. Sign in in the opened browser and run apply again; "
                                "the persistent profile will keep the session."
                            ),
                            blockers=["login_required"],
                            steps=steps,
                            screenshot=screenshot,
                        )
                    steps.append("login_completed")

                if str(page.url) == request.job.url and _already_applied(page, request.job.source):
                    return ExternalApplyResult(
                        status="already_applied",
                        mode="browser",
                        message="The vacancy is already marked as applied.",
                        applied=True,
                        steps=[*steps, "already_applied"],
                        raw_result={"final_url": page.url},
                    )

                start = _find_start_action(page, request.job.source, settings)
                if start is None:
                    screenshot = _capture_apply_screenshot(
                        page,
                        request,
                        "apply-button-missing",
                    )
                    return ExternalApplyResult(
                        status="blocked",
                        mode="browser",
                        message="Application button was not found on the vacancy page.",
                        blockers=["apply_button_missing"],
                        steps=steps,
                        screenshot=screenshot,
                    )
                start.click()
                page.wait_for_timeout(500)
                page = context.pages[-1]
                steps.append("opened_application")
                return _complete_browser_form(
                    page,
                    request,
                    settings,
                    steps,
                )
            except Exception as exc:
                page = context.pages[-1] if context.pages else None
                screenshot = (
                    _capture_apply_screenshot(page, request, "browser-error")
                    if page is not None
                    else ""
                )
                possibly_sent = "final_submit_started" in steps
                return ExternalApplyResult(
                    status="submission_unknown" if possibly_sent else "error",
                    mode="browser",
                    message=str(exc),
                    blockers=["reconciliation_required" if possibly_sent else "browser_apply_error"],
                    steps=steps,
                    screenshot=screenshot,
                )
            finally:
                context.close()


def open_browser_session(
    *,
    root: str | Path,
    source: str,
    global_config: dict[str, Any],
    source_config: dict[str, Any],
    url: str = "",
    wait_seconds: int = 300,
) -> dict[str, Any]:
    """Open a persistent source browser and keep it alive for interactive login."""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "status": "blocked",
            "source": source,
            "message": 'Playwright is missing. Install with: pip install -e ".[browser]"',
        }
    settings = copy.deepcopy(global_config)
    source_settings = source_config.get("apply_adapter") or {}
    if isinstance(source_settings, dict):
        settings.update(copy.deepcopy(source_settings))
    browser_name = str(settings.get("browser") or "chromium")
    configured_profile = str(settings.get("profile_dir") or "").strip()
    root_path = Path(root)
    if configured_profile:
        profile_dir = Path(configured_profile).expanduser()
        if not profile_dir.is_absolute():
            profile_dir = root_path / profile_dir
    else:
        profile_dir = root_path / ".work-hunter" / "browser" / source
    profile_dir.mkdir(parents=True, exist_ok=True)
    login_url = url.strip() or BROWSER_LOGIN_URLS.get(source, "")
    if not login_url:
        return {
            "status": "blocked",
            "source": source,
            "message": "No default login URL is known; pass --url explicitly.",
        }
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name, None)
        if browser_type is None:
            return {
                "status": "blocked",
                "source": source,
                "message": f"Unknown Playwright browser: {browser_name}",
            }
        context = browser_type.launch_persistent_context(
            str(profile_dir),
            headless=False,
            slow_mo=max(0, int(settings.get("slow_mo_ms", 100) or 0)),
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(login_url, wait_until="domcontentloaded")
            total_wait = max(1, min(1800, int(wait_seconds)))
            for elapsed in range(total_wait):
                if page.is_closed():
                    break
                if elapsed >= 2 and not _login_required(page):
                    return {
                        "status": "authenticated",
                        "source": source,
                        "url": page.url,
                        "cookie_count": len(context.cookies()),
                        "profile_dir": str(profile_dir),
                    }
                page.wait_for_timeout(1000)
            return {
                "status": "saved",
                "source": source,
                "url": "" if page.is_closed() else page.url,
                "cookie_count": len(context.cookies()),
                "profile_dir": str(profile_dir),
                "message": "Browser profile was saved; rerun login if the session is incomplete.",
            }
        finally:
            context.close()


def adapter_settings(request: ExternalApplyRequest) -> dict[str, Any]:
    settings = copy.deepcopy(request.global_config)
    source_settings = request.source_config.get("apply_adapter") or {}
    if isinstance(source_settings, dict):
        settings.update(copy.deepcopy(source_settings))
    return settings


def browser_profile_dir(request: ExternalApplyRequest) -> Path:
    settings = adapter_settings(request)
    configured = str(settings.get("profile_dir") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else request.root / path
    return request.root / ".work-hunter" / "browser" / request.job.source


@dataclass(frozen=True)
class FormAnswer:
    field_id: str
    category: str
    value: str | None
    provenance: str

    @property
    def status(self) -> str:
        return "answered" if self.value is not None else "needs_answer"


def resolve_form_answer(
    label: str,
    *,
    field_type: str,
    options: list[str] | None,
    request: ExternalApplyRequest,
    completion: Callable[[list[dict[str, Any]], dict[str, Any]], str] = chat_completion,
    field_id: str = "",
) -> str | None:
    """Compatibility wrapper: generated text is never evidence of a candidate fact."""
    del completion
    return resolve_typed_answer(label, field_type=field_type, options=options,
                                request=request, field_id=field_id).value


def resolve_typed_answer(
    label: str, *, field_type: str, options: list[str] | None,
    request: ExternalApplyRequest, field_id: str = "",
) -> FormAnswer:
    normalized = _normalize_label(label)
    identity = _normalize_label(field_id)
    category = "unknown"
    aliases = {
        "employer_name": {"company name", "employer name", "company", "название компании", "работодатель"},
        "first_name": {"first name", "firstname", "given name", "имя"},
        "last_name": {"last name", "lastname", "surname", "family name", "фамилия"},
        "name": {"full name", "fullname", "name", "фио", "полное имя", "имя и фамилия"},
        "email": {"email", "email address", "e mail", "e mail address", "электронная почта", "почта"},
        "phone": {"phone", "phone number", "mobile", "mobile number", "телефон", "номер телефона"},
        "city": {"city", "location", "город", "местоположение"},
        "linkedin_url": {"linkedin", "linkedin url"},
        "portfolio_url": {"portfolio", "github", "website", "сайт", "портфолио"},
        "cover_letter": {"cover letter", "сопроводительное письмо", "message", "сообщение"},
    }
    for key, names in aliases.items():
        if identity == _normalize_label(key) or identity in names:
            category = key
            break
    if category == "unknown":
        for key, names in aliases.items():
            if normalized in names:
                category = key
                break
    if field_type == "checkbox":
        category = "consent_or_attestation"
    elif field_type == "file":
        category = "resume_file"

    def answer(value: Any, provenance: str) -> FormAnswer:
        text = str(value) if value is not None and value != "" else None
        return FormAnswer(field_id or label, category,
                          _match_option(text, options) if text is not None else None, provenance)

    settings = adapter_settings(request)
    decisions = settings.get("answers") or {}
    if isinstance(decisions, dict):
        decision = decisions.get(field_id) if field_id else None
        if decision is None:
            decision = decisions.get(normalized)
        if isinstance(decision, dict):
            try:
                expiry = datetime.fromisoformat(str(decision.get("expires_at") or ""))
                current = expiry.tzinfo is not None and expiry > datetime.now(timezone.utc)
            except ValueError:
                current = False
            if (current and decision.get("source") == "user"
                    and decision.get("origin") == _origin(request.job.url)
                    and str(decision.get("job_id")) == request.job.source_id
                    and decision.get("label") == normalized):
                value = decision.get("value")
                if field_type == "checkbox":
                    value = "true" if value is True else "false" if value is False else None
                return answer(value, "user_decision")
        # Legacy exact answers remain usable for ordinary contact fields only.
        elif category in aliases and category != "cover_letter" and decision is not None:
            return answer(decision, "user_contact_answer")
    if category == "resume_file":
        return answer(request.profile.get("resume_path"), "selected_resume_artifact")
    if category == "cover_letter":
        return answer(request.letter, "approved_letter")
    if category in aliases:
        return answer(request.profile.get(category), f"profile.{category}")
    return answer(None, "unknown")


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def _apply_with_session(request: ExternalApplyRequest) -> ExternalApplyResult:
    settings = adapter_settings(request)
    context = _template_context(request)
    method = str(settings.get("method") or "POST").upper()
    url = _format_value(str(settings.get("url") or ""), context)
    raw_body = settings.get("body")
    if isinstance(raw_body, (dict, list)):
        body = json.dumps(
            _format_nested(raw_body, context),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    elif raw_body is None:
        body = None
    else:
        body = _format_value(str(raw_body), context)
    try:
        response = call_external_session(
            request.root,
            str(settings.get("session_name") or request.job.source),
            method,
            url,
            data=body,
            real=True,
            unsafe_lab=True,
            timeout=max(1, int(settings.get("timeout_seconds", 30) or 30)),
        )
    except Exception:  # noqa: BLE001 - any transport failure after dispatch leaves delivery unknown
        # Once dispatch starts, even a timeout may follow a committed remote POST.
        response = {"status": "transport_unknown"}
    # This transport has no verified source-specific receipt contract yet.
    # Neither a 2xx nor an arbitrary JSON field proves delivery (or non-delivery).
    blocked = response.get("status") == "blocked"
    return ExternalApplyResult(
        status="blocked" if blocked else "submission_unknown",
        mode="session",
        message=(
            str(response.get("reason") or "Session dispatch was blocked.")
            if blocked
            else "Delivery is unconfirmed. Check the application on the site before any retry."
        ),
        blockers=[] if blocked else ["reconciliation_required", "source_verifier_missing"],
        raw_result=response,
    )


def _complete_browser_form(
    page: Any,
    request: ExternalApplyRequest,
    settings: dict[str, Any],
    steps: list[str],
) -> ExternalApplyResult:
    max_steps = max(1, int(settings.get("max_steps", 12) or 12))
    for step in range(max_steps):
        scope = _application_scope(page, request.job.source)
        if scope is None:
            return ExternalApplyResult(
                status="blocked", mode="browser",
                message="A single application form or dialog could not be identified.",
                blockers=["application_scope_unknown"], steps=steps,
            )
        blockers = _fill_visible_fields(scope, request)
        if blockers:
            screenshot = _capture_apply_screenshot(
                page,
                request,
                f"questions-{step + 1}",
            )
            return ExternalApplyResult(
                status="needs_answers",
                mode="browser",
                message="Required application questions need answers.",
                blockers=blockers,
                steps=steps,
                screenshot=screenshot,
            )
        next_action = _find_next_action(scope, request.job.source, settings)
        if next_action is not None:
            next_action.click()
            page.wait_for_timeout(500)
            steps.append(f"form_step_{step + 1}")
            continue
        submit = _find_submit_action(scope, request.job.source, settings)
        if submit is not None:
            previous_url = str(page.url or "")
            # Record before click: click itself can time out after remote acceptance.
            steps.append("final_submit_started")
            try:
                submit.click()
                page.wait_for_timeout(1_250)
            except Exception:  # noqa: BLE001 - browser libraries may fail after a committed click
                return ExternalApplyResult(
                    status="submission_unknown", mode="browser",
                    message="The final submit may have reached the site. Verification is required.",
                    blockers=["reconciliation_required"], steps=steps,
                )
            steps.append("submitted")
            if not _application_success(page, request.job.source, previous_url):
                screenshot = _capture_apply_screenshot(
                    page,
                    request,
                    "submit-unconfirmed",
                )
                return ExternalApplyResult(
                    status="submission_unknown",
                    mode="browser",
                    message=(
                        "The submit control was clicked, but the site did not expose "
                        "a success marker. Check the browser screenshot before retrying."
                    ),
                    blockers=["submission_confirmation_missing", "reconciliation_required"],
                    steps=steps,
                    screenshot=screenshot,
                    raw_result={"final_url": page.url},
                )
            return ExternalApplyResult(
                status="applied",
                mode="browser",
                message="Application submitted in the browser.",
                applied=True,
                steps=steps,
                raw_result={"final_url": page.url},
                evidence={"kind": "source_receipt", "source": request.job.source, "url": page.url},
            )
        screenshot = _capture_apply_screenshot(page, request, f"stalled-{step + 1}")
        return ExternalApplyResult(
            status="blocked", mode="browser",
            message="Application form stalled before a submit/review action.",
            blockers=["form_navigation_missing"], steps=steps, screenshot=screenshot,
        )
    screenshot = _capture_apply_screenshot(page, request, "step-limit")
    return ExternalApplyResult(
        status="blocked",
        mode="browser",
        message="Application form exceeded the configured step limit.",
        blockers=["form_step_limit"],
        steps=steps,
        screenshot=screenshot,
    )


def _fill_visible_fields(page: Any, request: ExternalApplyRequest) -> list[str]:
    blockers: list[str] = []
    fields = page.locator(
        "input:not([type=hidden]):not([disabled]), "
        "textarea:not([disabled]), select:not([disabled])"
    )
    processed_radio_names: set[str] = set()
    for index in range(fields.count()):
        field = fields.nth(index)
        if not field.is_visible():
            continue
        meta = field.evaluate(
            """
            (el) => ({
              tag: el.tagName.toLowerCase(),
              type: (el.type || el.tagName).toLowerCase(),
              name: el.name || '',
              id: el.id || el.name || '',
              label: (el.labels && el.labels[0] && el.labels[0].innerText) ||
                     el.getAttribute('aria-label') || el.placeholder || el.name || el.id || '',
              required: !!el.required || el.getAttribute('aria-required') === 'true',
              value: el.value || '',
              checked: !!el.checked,
              options: el.tagName === 'SELECT'
                ? Array.from(el.options).map((o) => ({text: o.text, value: o.value, disabled: o.disabled}))
                : []
            })
            """
        )
        field_type = str(meta.get("type") or "text")
        label = str(meta.get("label") or meta.get("name") or f"field-{index}")
        field_id = str(meta.get("id") or meta.get("name") or "")
        required = bool(meta.get("required"))
        if field_type in {"submit", "button", "image", "reset"}:
            continue
        if field_type == "file":
            answer = resolve_form_answer(
                label,
                field_type="file",
                options=None,
                request=request,
            )
            if answer and Path(answer).expanduser().is_file():
                path = Path(answer).expanduser()
                data = path.read_bytes()
                expected = request.profile.get("resume_sha256")
                if expected and hashlib.sha256(data).hexdigest() != expected:
                    blockers.append("resume_artifact_changed")
                    continue
                accepted = str(field.get_attribute("accept") or "").lower()
                if accepted and path.suffix.lower() not in accepted and "*" not in accepted:
                    mime = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".txt": "text/plain", ".md": "text/markdown"}.get(path.suffix.lower(), "")
                    if not mime or mime not in accepted:
                        blockers.append(f"resume_format_not_accepted:{accepted}")
                        continue
                field.set_input_files({"name": path.name, "mimeType": "application/octet-stream", "buffer": data})
            elif required:
                blockers.append(f"resume_file:{label}")
            continue
        if field_type == "checkbox":
            decision = resolve_typed_answer(label, field_type=field_type, options=None,
                                            request=request, field_id=field_id)
            if decision.value == "true":
                field.check()
            elif decision.value == "false":
                field.uncheck()
                if required:
                    blockers.append(f"consent_declined:{label}")
            elif required:
                # A prechecked control is not evidence of the user's decision.
                blockers.append(f"needs_consent:{label}")
            continue
        if field_type == "radio":
            name = str(meta.get("name") or label)
            if name in processed_radio_names:
                continue
            processed_radio_names.add(name)
            radios = page.locator(f'input[type="radio"][name="{_css_string(name)}"]')
            options = []
            for radio_index in range(radios.count()):
                radio = radios.nth(radio_index)
                option = radio.evaluate(
                    "(el) => (el.labels && el.labels[0] && el.labels[0].innerText) || el.value || ''"
                )
                options.append(str(option))
            answer = resolve_form_answer(
                label,
                field_type="radio",
                options=options,
                request=request,
                field_id=field_id,
            )
            if answer is not None:
                for radio_index, option in enumerate(options):
                    if _normalize_label(option) == _normalize_label(answer):
                        radios.nth(radio_index).check()
                        break
            elif required and not any(radios.nth(item).is_checked() for item in range(radios.count())):
                blockers.append(f"required:{label}")
            continue
        if meta.get("value"):
            continue
        raw_options = list(meta.get("options") or [])
        options = [
            str(item.get("text") or item.get("value") or "").strip()
            for item in raw_options
            if not item.get("disabled")
            and str(item.get("value") or "").strip()
        ]
        answer = resolve_form_answer(
            label,
            field_type=field_type,
            options=options or None,
            request=request,
            field_id=field_id,
        )
        if answer is not None:
            if field_type == "select-one":
                selected = False
                for item in raw_options:
                    if _normalize_label(str(item.get("text") or "")) == _normalize_label(answer):
                        field.select_option(str(item.get("value") or ""))
                        selected = True
                        break
                if not selected and required:
                    blockers.append(f"required:{label}")
            else:
                field.fill(answer)
        elif required:
            blockers.append(f"required:{label}")
    return list(dict.fromkeys(blockers))


def _find_start_action(page: Any, source: str, settings: dict[str, Any]) -> Any | None:
    selectors = list(settings.get("start_selectors") or [])
    if source == "linkedin":
        selectors.extend(
            [
                'button[aria-label*="Easy Apply"]',
                'button:has-text("Easy Apply")',
                'a[href*="openSDUIApplyFlow=true"]',
                "button.jobs-apply-button",
            ]
        )
    selectors.extend(
        [
            'button:has-text("Откликнуться")',
            'a:has-text("Откликнуться")',
            'button:has-text("Apply")',
            'a:has-text("Apply")',
            '[data-qa*="apply"]',
        ]
    )
    return _first_visible(page, selectors)


def _find_next_action(page: Any, source: str, settings: dict[str, Any]) -> Any | None:
    del source
    selectors = list(settings.get("next_selectors") or [])
    selectors.extend(
        [
            'button[aria-label*="Continue"]',
            'button[aria-label*="Next"]',
            'button[aria-label*="Review"]',
            'button:has-text("Continue")',
            'button:has-text("Next")',
            'button:has-text("Review")',
            'button:has-text("Продолжить")',
            'button:has-text("Далее")',
            'button:has-text("Проверить")',
        ]
    )
    return _first_visible(page, selectors)


def _find_submit_action(page: Any, source: str, settings: dict[str, Any]) -> Any | None:
    del source
    selectors = list(settings.get("submit_selectors") or [])
    selectors.extend(
        [
            'button[aria-label*="Submit application"]',
            'button:has-text("Submit application")',
            'button:has-text("Отправить отклик")',
            'button:has-text("Отправить заявку")',
        ]
    )
    return _first_visible(page, selectors)


def _first_visible(page: Any, selectors: list[str]) -> Any | None:
    for selector in selectors:
        locator = page.locator(str(selector))
        for index in range(min(locator.count(), 5)):
            candidate = locator.nth(index)
            if candidate.is_visible() and candidate.is_enabled():
                return candidate
    return None


def _application_scope(page: Any, source: str) -> Any:
    """Keep provider-specific form discovery away from the surrounding page."""

    if source == "linkedin":
        for selector in (
            ".jobs-easy-apply-modal",
            '[data-test-modal-id="easy-apply-modal"]',
            '[role="dialog"]',
        ):
            locator = page.locator(selector)
            for index in range(min(locator.count(), 3)):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    return candidate
    for selector in ('[role="dialog"]', "form"):
        locator = page.locator(selector)
        visible = [locator.nth(index) for index in range(locator.count()) if locator.nth(index).is_visible()]
        if len(visible) == 1:
            return visible[0]
        if len(visible) > 1:
            return None
    return None


def _already_applied(page: Any, source: str) -> bool:
    if source != "linkedin":
        return False
    return _first_visible(
        page,
        [
            "a.jobs-s-apply__application-link",
        ],
    ) is not None


def _application_success(page: Any, source: str, previous_url: str) -> bool:
    # Only source-specific receipt UI is evidence. A generic Done button, page
    # text, redirect or a wizard's next step cannot confirm an application.
    if source != "linkedin" or str(page.url or "") != previous_url:
        return False
    return _first_visible(page, [
        '[data-test-modal-id="easy-apply-done"]',
        "a.jobs-s-apply__application-link",
    ]) is not None


def _login_required(page: Any) -> bool:
    url = str(page.url or "").casefold()
    if any(marker in url for marker in ("/login", "/auth", "/checkpoint")):
        return True
    return _first_visible(
        page,
        [
            'input[name="session_key"]',
            'input[type="password"]',
            '[data-test-id="sign-in-form"]',
        ],
    ) is not None


def _wait_for_interactive_login(page: Any, settings: dict[str, Any]) -> bool:
    if bool(settings.get("headless", False)):
        return False
    seconds = min(55, max(0, int(settings.get("login_wait_seconds", 45) or 0)))
    for _ in range(seconds):
        if not _login_required(page):
            return True
        page.wait_for_timeout(1000)
    return not _login_required(page)


def _capture_apply_screenshot(
    page: Any,
    request: ExternalApplyRequest,
    suffix: str,
) -> str:
    directory = request.root / ".work-hunter" / "artifacts" / "external-apply"
    directory.mkdir(parents=True, exist_ok=True)
    safe_suffix = re.sub(r"[^a-zA-Z0-9_.-]+", "-", suffix).strip("-")
    path = directory / f"{request.job.source}-{request.job.source_id}-{safe_suffix}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception:
        return ""
    return str(path)


def _explicit_answer(normalized_label: str, raw_answers: Any) -> str | None:
    if not isinstance(raw_answers, dict):
        return None
    for pattern, value in raw_answers.items():
        normalized_pattern = _normalize_label(str(pattern))
        fuzzy_match = (
            _label_words(normalized_pattern) <= _label_words(normalized_label)
            if normalized_pattern
            else False
        )
        if normalized_pattern and (
            normalized_pattern in normalized_label or fuzzy_match
        ):
            text = str(value).strip()
            return text or None
    return None


def _match_option(answer: str, options: list[str] | None) -> str | None:
    if not options:
        return answer
    normalized_answer = _normalize_label(answer)
    for option in options:
        normalized_option = _normalize_label(option)
        if normalized_answer == normalized_option:
            return option
    for option in options:
        normalized_option = _normalize_label(option)
        if normalized_answer in normalized_option or normalized_option in normalized_answer:
            return option
    return None


def _forms_ai_config(ai_config: dict[str, Any]) -> dict[str, Any]:
    scoped = copy.deepcopy(ai_config)
    forms = scoped.pop("forms", {})
    if isinstance(forms, dict):
        scoped.update(copy.deepcopy(forms))
    return scoped


def _ai_configured(ai_config: dict[str, Any]) -> bool:
    backend = str(ai_config.get("backend") or "direct").casefold()
    if backend == "opencode":
        return bool(ai_config.get("opencode_command") or ai_config.get("opencode_server_url"))
    return all(str(ai_config.get(key) or "").strip() for key in ("api_key", "base_url", "model"))


def _template_context(request: ExternalApplyRequest) -> dict[str, str]:
    profile = request.profile
    return {
        "job_id": str(request.job.id or ""),
        "source_id": request.job.source_id,
        "job_url": request.job.url,
        "title": request.job.title,
        "company": request.job.company,
        "letter": request.letter,
        "resume_id": str(request.resume_id or ""),
        "name": str(profile.get("name") or ""),
        "email": str(profile.get("email") or ""),
        "phone": str(profile.get("phone") or ""),
    }


class _SafeFormat(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _format_value(value: str, context: dict[str, str]) -> str:
    return value.format_map(_SafeFormat(context))


def _format_nested(value: Any, context: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _format_value(value, context)
    if isinstance(value, list):
        return [_format_nested(item, context) for item in value]
    if isinstance(value, dict):
        return {str(key): _format_nested(item, context) for key, item in value.items()}
    return value


def _safe_adapter_view(settings: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "transport",
        "kind",
        "browser",
        "headless",
        "timeout_seconds",
        "login_wait_seconds",
        "max_steps",
        "session_name",
        "method",
        "url",
    }
    return {key: copy.deepcopy(value) for key, value in settings.items() if key in allowed}


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\wа-яё]+", " ", value.casefold())).strip()


def _label_words(value: str) -> set[str]:
    return {
        word[:6] if len(word) > 6 else word
        for word in _normalize_label(value).split()
        if len(word) > 2
    }


def _css_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
