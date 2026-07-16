from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Iterator, Mapping

from .challenges import sanitize_hh_url
from .types import DeliveryCertainty, DispatchOutcome


@dataclass(frozen=True)
class BrowserField:
    name: str
    kind: str = "text"
    required: bool = False
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class BrowserFlow:
    kind: str
    url: str
    fields: tuple[BrowserField, ...] = ()
    submit_selector: str = "#response-form button[type=submit]"

    @classmethod
    def captcha(cls, url: str) -> "BrowserFlow":
        return cls("captcha", url)

    @classmethod
    def assessment(cls, url: str) -> "BrowserFlow":
        return cls("assessment", url)

    @classmethod
    def auth(cls, url: str) -> "BrowserFlow":
        return cls("auth", url)

    @classmethod
    def form(
        cls,
        url: str,
        fields: tuple[BrowserField, ...],
        submit_selector: str = "#response-form button[type=submit]",
    ) -> "BrowserFlow":
        return cls("form", url, fields, submit_selector)


@dataclass
class ManualHandoff:
    context: Any
    page: Any
    url: str

    def close(self) -> None:
        self.context.close()


class HHBrowserApplicationAdapter:
    """Small Playwright-compatible adapter; it never interacts with CAPTCHA UI."""

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
            safe_url = sanitize_hh_url(str(getattr(page, "url", url)))
            if _is_captcha(html):
                return BrowserFlow.captcha(safe_url)
            if _is_auth(html, safe_url):
                return BrowserFlow.auth(safe_url)
            fields = extract_supported_fields(html)
            if _is_assessment(html, fields):
                return BrowserFlow.assessment(safe_url)
            return BrowserFlow.form(safe_url, fields)

    def submit_grounded_form(
        self,
        flow: BrowserFlow,
        answers: Mapping[str, Any],
    ) -> DispatchOutcome:
        if flow.kind != "form":
            raise ValueError("only a form flow can be submitted")
        clicked = False
        try:
            with self._managed_context() as context:
                page = context.new_page()
                page.goto(
                    flow.url,
                    wait_until="domcontentloaded",
                    timeout=self.navigation_timeout_ms,
                )
                if _is_captcha(str(page.content())):
                    return DispatchOutcome(
                        "manual_captcha",
                        DeliveryCertainty.DEFINITE_RESPONSE,
                        location=sanitize_hh_url(str(getattr(page, "url", flow.url))),
                    )
                for browser_field in flow.fields:
                    if browser_field.name not in answers:
                        if browser_field.required:
                            raise ValueError(
                                f"required grounded answer is absent: {browser_field.name}"
                            )
                        continue
                    self._fill(page, browser_field, answers[browser_field.name])
                clicked = True
                page.locator(flow.submit_selector).click(timeout=self.navigation_timeout_ms)
                html = str(page.content())
                location = sanitize_hh_url(str(getattr(page, "url", flow.url)))
                if _is_captcha(html):
                    return DispatchOutcome(
                        "manual_captcha",
                        DeliveryCertainty.DEFINITE_RESPONSE,
                        location=location,
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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        tag = tag.casefold()
        if tag == "input":
            kind = values.get("type", "text").casefold()
            if kind in {"hidden", "submit", "button", "image", "reset"}:
                return
            name = values.get("name", "").strip()
            if name:
                self.fields.append(
                    BrowserField(name, kind, "required" in values)
                )
        elif tag == "textarea":
            name = values.get("name", "").strip()
            if name:
                self.fields.append(
                    BrowserField(name, "textarea", "required" in values)
                )
        elif tag == "select":
            name = values.get("name", "").strip()
            if name:
                self._select = {
                    "name": name,
                    "required": "required" in values,
                    "options": [],
                }
        elif tag == "option" and self._select is not None:
            self._select["options"].append(values.get("value", ""))

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "select" and self._select is not None:
            self.fields.append(
                BrowserField(
                    self._select["name"],
                    "select",
                    self._select["required"],
                    tuple(self._select["options"]),
                )
            )
            self._select = None


def extract_supported_fields(html: str) -> tuple[BrowserField, ...]:
    parser = _FormParser()
    parser.feed(str(html or ""))
    return tuple(parser.fields)


def _is_captcha(html: str) -> bool:
    lowered = html.casefold()
    return "captcha" in lowered or "не робот" in lowered


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
    lowered = f"{html} {url}".casefold()
    return any(marker in lowered for marker in ("account/login", "login-form", "войти в аккаунт"))


def _css_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


__all__ = [
    "BrowserField",
    "BrowserFlow",
    "HHBrowserApplicationAdapter",
    "ManualHandoff",
    "extract_supported_fields",
]
