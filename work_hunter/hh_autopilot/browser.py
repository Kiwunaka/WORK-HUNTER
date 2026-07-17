from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Iterator, Mapping
from urllib.parse import urlsplit

from .challenges import sanitize_hh_url
from .types import DeliveryCertainty, DispatchOutcome


@dataclass(frozen=True)
class BrowserField:
    name: str
    kind: str = "text"
    required: bool = False
    options: tuple[str, ...] = ()
    prompt: str = ""
    option_labels: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class BrowserFlow:
    kind: str
    url: str
    fields: tuple[BrowserField, ...] = ()
    submit_selector: str = "#response-form button[type=submit]"
    runtime_url: str = ""

    @classmethod
    def captcha(cls, url: str, runtime_url: str = "") -> "BrowserFlow":
        return cls("captcha", url, runtime_url=runtime_url)

    @classmethod
    def assessment(cls, url: str, runtime_url: str = "") -> "BrowserFlow":
        return cls("assessment", url, runtime_url=runtime_url)

    @classmethod
    def auth(cls, url: str, runtime_url: str = "") -> "BrowserFlow":
        return cls("auth", url, runtime_url=runtime_url)

    @classmethod
    def form(
        cls,
        url: str,
        fields: tuple[BrowserField, ...],
        submit_selector: str = "#response-form button[type=submit]",
        runtime_url: str = "",
    ) -> "BrowserFlow":
        return cls("form", url, fields, submit_selector, runtime_url)


@dataclass
class ManualHandoff:
    context: Any
    page: Any
    url: str

    def close(self) -> None:
        self.context.close()


class HHBrowserApplicationAdapter:
    """Playwright-compatible HH form and application-CAPTCHA adapter."""

    CAPTCHA_IMAGE_SELECTOR = 'img[data-qa="account-captcha-picture"]'
    CAPTCHA_INPUT_SELECTOR = 'input[data-qa="account-captcha-input"]'
    SUBMIT_SELECTORS = (
        'button[data-qa="vacancy-response-submit-popup"]',
        'button[data-qa="vacancy-response-submit"]',
        'form button[type="submit"]',
        'button[type="submit"]',
    )

    def __init__(
        self,
        context_factory: Any,
        *,
        session: Any | None = None,
        navigation_timeout_ms: int = 30_000,
    ) -> None:
        if not callable(context_factory):
            raise TypeError("context_factory must be callable")
        self.context_factory = context_factory
        self.session = session
        self.navigation_timeout_ms = int(navigation_timeout_ms)
        if self.navigation_timeout_ms < 1:
            raise ValueError("navigation_timeout_ms must be positive")

    def _context(self) -> Any:
        return self.context_factory()

    @contextmanager
    def _managed_context(self) -> Iterator[Any]:
        candidate = self._context()
        if hasattr(candidate, "__enter__"):
            with candidate as context:
                self._restore_session(context)
                yield context
            return
        context = candidate
        try:
            self._restore_session(context)
            yield context
        finally:
            close = getattr(context, "close", None)
            if callable(close):
                close()

    def inspect(self, url: str) -> BrowserFlow:
        with self._managed_context() as context:
            page = context.new_page()
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.navigation_timeout_ms,
            )
            html = str(page.content())
            runtime_url = str(getattr(page, "url", url))
            safe_url = sanitize_hh_url(runtime_url)
            if _is_captcha(html):
                return BrowserFlow.captcha(safe_url, runtime_url)
            if _is_auth(html, safe_url):
                return BrowserFlow.auth(safe_url, runtime_url)
            fields = extract_supported_fields(html)
            if _is_assessment(html, fields):
                return BrowserFlow.assessment(safe_url, runtime_url)
            return BrowserFlow.form(safe_url, fields, runtime_url=runtime_url)

    def submit_grounded_form(
        self,
        flow: BrowserFlow,
        answers: Mapping[str, Any],
        *,
        captcha_solver: Any | None = None,
        captcha_attempts: int = 1,
    ) -> DispatchOutcome:
        if flow.kind != "form":
            raise ValueError("only a form flow can be submitted")
        clicked = False
        try:
            with self._managed_context() as context:
                page = context.new_page()
                page.goto(
                    flow.runtime_url or flow.url,
                    wait_until="domcontentloaded",
                    timeout=self.navigation_timeout_ms,
                )
                if _is_captcha(str(page.content())):
                    if captcha_solver is None or not self._solve_captcha_on_page(
                        page,
                        captcha_solver,
                        attempts=captcha_attempts,
                    ):
                        return self._captcha_outcome(page, flow.runtime_url or flow.url)
                for browser_field in flow.fields:
                    if browser_field.name not in answers:
                        if browser_field.required:
                            raise ValueError(
                                f"required grounded answer is absent: {browser_field.name}"
                            )
                        continue
                    self._fill(page, browser_field, answers[browser_field.name])
                clicked = True
                self._click_submit(page, flow.submit_selector)
                html = str(page.content())
                location = sanitize_hh_url(str(getattr(page, "url", flow.url)))
                if _is_captcha(html):
                    if captcha_solver is None or not self._solve_captcha_on_page(
                        page,
                        captcha_solver,
                        attempts=captcha_attempts,
                    ):
                        return self._captcha_outcome(page, flow.runtime_url or flow.url)
                    html = str(page.content())
                    location = sanitize_hh_url(
                        str(getattr(page, "url", flow.runtime_url or flow.url))
                    )
                if _is_assessment(html, ()):
                    return DispatchOutcome(
                        "manual_assessment",
                        DeliveryCertainty.DEFINITE_RESPONSE,
                        location=location,
                    )
                if _is_auth(html, location):
                    return DispatchOutcome(
                        "auth_expired",
                        DeliveryCertainty.DEFINITE_RESPONSE,
                        location=location,
                    )
                if extract_supported_fields(html) and not _is_application_success(html):
                    return DispatchOutcome(
                        "form_required",
                        DeliveryCertainty.DEFINITE_RESPONSE,
                        location=location,
                    )
                self.capture_session(context=context, page=page)
                return DispatchOutcome(
                    "applied",
                    DeliveryCertainty.DEFINITE_RESPONSE,
                    location=location,
                )
        except Exception:
            return DispatchOutcome(
                "post_dispatch_network_error" if clicked else "pre_dispatch_network_error",
                (
                    DeliveryCertainty.POSSIBLY_SENT
                    if clicked
                    else DeliveryCertainty.DEFINITELY_NOT_SENT
                ),
            )

    def solve_captcha(
        self,
        url: str,
        solver: Any,
        *,
        attempts: int = 3,
    ) -> DispatchOutcome:
        if not callable(getattr(solver, "solve_captcha", None)):
            raise TypeError("solver must expose solve_captcha")
        if type(attempts) is not int or not 1 <= attempts <= 10:
            raise ValueError("attempts must be in 1..10")
        try:
            with self._managed_context() as context:
                page = context.new_page()
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.navigation_timeout_ms,
                )
                if not self._solve_captcha_on_page(page, solver, attempts=attempts):
                    return self._captcha_outcome(page, url)
                self.capture_session(context=context, page=page)
                return DispatchOutcome(
                    "applied",
                    DeliveryCertainty.DEFINITE_RESPONSE,
                    location=sanitize_hh_url(str(getattr(page, "url", url))),
                )
        except Exception:
            return DispatchOutcome(
                "manual_captcha",
                DeliveryCertainty.DEFINITE_RESPONSE,
                location=sanitize_hh_url(url),
            )

    def _solve_captcha_on_page(
        self,
        page: Any,
        solver: Any,
        *,
        attempts: int,
    ) -> bool:
        for _attempt in range(attempts):
            if not _is_captcha(str(page.content())):
                return True
            image = page.locator(self.CAPTCHA_IMAGE_SELECTOR).screenshot()
            answer = str(solver.solve_captcha(image) or "").strip()
            if not answer:
                continue
            input_locator = page.locator(self.CAPTCHA_INPUT_SELECTOR)
            input_locator.fill(answer)
            input_locator.press("Enter")
            wait = getattr(page, "wait_for_load_state", None)
            if callable(wait):
                try:
                    wait("networkidle", timeout=self.navigation_timeout_ms)
                except Exception:
                    # HH pages may keep background requests open after the CAPTCHA
                    # has already disappeared; the DOM check below is authoritative.
                    pass
            if not _is_captcha(str(page.content())):
                return True
        return not _is_captcha(str(page.content()))

    @staticmethod
    def _captcha_outcome(page: Any, fallback_url: str) -> DispatchOutcome:
        return DispatchOutcome(
            "manual_captcha",
            DeliveryCertainty.DEFINITE_RESPONSE,
            location=sanitize_hh_url(str(getattr(page, "url", fallback_url))),
        )

    def _click_submit(self, page: Any, preferred: str) -> None:
        last_error: Exception | None = None
        selectors = tuple(dict.fromkeys((preferred, *self.SUBMIT_SELECTORS)))
        for selector in selectors:
            locator = page.locator(selector)
            count = getattr(locator, "count", None)
            if callable(count) and count() == 0:
                continue
            try:
                locator.click(timeout=self.navigation_timeout_ms)
                return
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("HH response form has no submit button")

    def open_manual_handoff(self, url: str) -> ManualHandoff:
        candidate = self._context()
        context = candidate.__enter__() if hasattr(candidate, "__enter__") else candidate
        self._restore_session(context)
        page = context.new_page()
        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=self.navigation_timeout_ms,
        )
        return ManualHandoff(
            context=context,
            page=page,
            url=sanitize_hh_url(str(getattr(page, "url", url))),
        )

    def capture_session(
        self,
        handoff: ManualHandoff | None = None,
        *,
        context: Any | None = None,
        page: Any | None = None,
    ) -> None:
        if self.session is None:
            return
        if handoff is not None:
            context, page = handoff.context, handoff.page
        if context is None:
            raise ValueError("browser context is required")
        html = "" if page is None else str(page.content())
        self.session.update_from_playwright_context(context.cookies(), html)

    def _restore_session(self, context: Any) -> None:
        if self.session is None:
            return
        if not self.session.cookies:
            self.session.load()
        if self.session.cookies:
            context.add_cookies(self.session.cookies)

    @staticmethod
    def _fill(page: Any, field: BrowserField, value: Any) -> None:
        name = _css_string(field.name)
        if field.kind in {"checkbox", "boolean"}:
            locator = page.locator(f'[name="{name}"]')
            (locator.check if bool(value) else locator.uncheck)()
        elif field.kind == "radio":
            option = _css_string(str(value))
            page.locator(f'[name="{name}"][value="{option}"]').check()
        elif field.kind == "select":
            page.locator(f'[name="{name}"]').select_option(str(value))
        else:
            page.locator(f'[name="{name}"]').fill(str(value))


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: list[BrowserField] = []
        self._select: dict[str, Any] | None = None
        self._option: dict[str, str] | None = None
        self._labels: dict[str, str] = {}
        self._label_for: str | None = None
        self._label_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        tag = tag.casefold()
        if tag == "label":
            self._label_for = values.get("for", "").strip()
            self._label_parts = []
            return
        if tag == "input":
            kind = values.get("type", "text").casefold()
            if kind in {"hidden", "submit", "button", "image", "reset"}:
                return
            name = values.get("name", "").strip()
            if name:
                if self._label_for == "":
                    self._label_for = name
                option = values.get("value", "").strip()
                self.fields.append(
                    BrowserField(
                        name,
                        kind,
                        "required" in values,
                        (option,) if kind == "radio" and option else (),
                        values.get("aria-label", "").strip()
                        or values.get("placeholder", "").strip()
                        or values.get("title", "").strip(),
                    )
                )
        elif tag == "textarea":
            name = values.get("name", "").strip()
            if name:
                if self._label_for == "":
                    self._label_for = name
                self.fields.append(
                    BrowserField(
                        name,
                        "textarea",
                        "required" in values,
                        prompt=(
                            values.get("aria-label", "").strip()
                            or values.get("placeholder", "").strip()
                            or values.get("title", "").strip()
                        ),
                    )
                )
        elif tag == "select":
            name = values.get("name", "").strip()
            if name:
                if self._label_for == "":
                    self._label_for = name
                self._select = {
                    "name": name,
                    "required": "required" in values,
                    "options": [],
                    "prompt": (
                        values.get("aria-label", "").strip()
                        or values.get("title", "").strip()
                    ),
                }
        elif tag == "option" and self._select is not None:
            self._option = {"value": values.get("value", ""), "label": ""}

    def handle_data(self, data: str) -> None:
        if self._label_for is not None:
            self._label_parts.append(data)
        if self._option is not None:
            self._option["label"] += data

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "label" and self._label_for is not None:
            label = " ".join("".join(self._label_parts).split())
            if self._label_for and label:
                self._labels[self._label_for] = label
            self._label_for = None
            self._label_parts = []
        elif tag == "option" and self._select is not None and self._option is not None:
            value = self._option["value"]
            label = " ".join(self._option["label"].split()) or value
            self._select["options"].append((value, label))
            self._option = None
        elif tag == "select" and self._select is not None:
            options = tuple(self._select["options"])
            self.fields.append(
                BrowserField(
                    self._select["name"],
                    "select",
                    self._select["required"],
                    tuple(value for value, _label in options),
                    self._select["prompt"],
                    options,
                )
            )
            self._select = None

    def result(self) -> tuple[BrowserField, ...]:
        merged: list[BrowserField] = []
        index_by_name: dict[tuple[str, str], int] = {}
        for field in self.fields:
            prompt = field.prompt or self._labels.get(field.name, "")
            prepared = BrowserField(
                field.name,
                field.kind,
                field.required,
                field.options,
                prompt,
                field.option_labels,
            )
            key = (prepared.name, prepared.kind)
            if prepared.kind == "radio" and key in index_by_name:
                index = index_by_name[key]
                previous = merged[index]
                options = tuple(dict.fromkeys((*previous.options, *prepared.options)))
                merged[index] = BrowserField(
                    previous.name,
                    previous.kind,
                    previous.required or prepared.required,
                    options,
                    previous.prompt or prepared.prompt,
                    previous.option_labels,
                )
                continue
            index_by_name[key] = len(merged)
            merged.append(prepared)
        return tuple(merged)


def extract_supported_fields(html: str) -> tuple[BrowserField, ...]:
    parser = _FormParser()
    parser.feed(str(html or ""))
    parser.close()
    return parser.result()


def _is_captcha(html: str) -> bool:
    lowered = html.casefold()
    return any(
        marker in lowered
        for marker in (
            "account-captcha-picture",
            "account-captcha-input",
            'data-qa="captcha',
            "data-qa='captcha",
            "не робот",
        )
    )


def _is_assessment(html: str, fields: Any) -> bool:
    lowered = html.casefold()
    return any(
        marker in lowered
        for marker in (
            "data-qa=\"assessment",
            "data-qa='assessment",
            "vacancy-test",
            "решите задачу",
            "пройти тест",
        )
    ) or any(field.kind in {"file", "knowledge"} for field in fields)


def _is_auth(html: str, url: str) -> bool:
    path = urlsplit(url).path.casefold()
    lowered = html.casefold()
    return "/account/login" in path or any(
        marker in lowered
        for marker in (
            'data-qa="login-input-username"',
            "data-qa='login-input-username'",
            'data-qa="login-form"',
            "data-qa='login-form'",
        )
    )


def _is_application_success(html: str) -> bool:
    lowered = html.casefold()
    return any(
        marker in lowered
        for marker in (
            "application-success",
            "vacancy-response-success",
            "negotiation-created",
            "отклик отправлен",
        )
    )


def _css_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


__all__ = [
    "BrowserField",
    "BrowserFlow",
    "HHBrowserApplicationAdapter",
    "ManualHandoff",
    "extract_supported_fields",
]
