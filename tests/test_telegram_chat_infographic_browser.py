from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright


ARTIFACT = Path(__file__).resolve().parents[1] / "telegram-chat-retrospective-2026-07-11-13.html"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        try:
            yield instance
        finally:
            instance.close()


def _open(
    browser: Browser,
    width: int,
    *,
    reduced_motion: str = "no-preference",
    java_script_enabled: bool = True,
) -> tuple[object, Page, list[str], list[str], list[str]]:
    context = browser.new_context(
        viewport={"width": width, "height": 900},
        reduced_motion=reduced_motion,
        java_script_enabled=java_script_enabled,
    )
    requests: list[str] = []
    page_errors: list[str] = []
    console_errors: list[str] = []
    context.on("request", lambda request: requests.append(request.url))
    page = context.new_page()
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )
    page.goto(ARTIFACT.as_uri(), wait_until="load")
    return context, page, requests, page_errors, console_errors


@pytest.mark.parametrize("width", [1440, 900, 390])
def test_offline_layout_has_no_external_requests_or_overflow(browser, width):
    context, page, requests, page_errors, console_errors = _open(browser, width)
    try:
        external = [
            url
            for url in requests
            if urlsplit(url).scheme.lower() not in {"", "file", "data", "about", "blob"}
        ]
        assert external == []
        assert page_errors == []
        assert console_errors == []
        assert page.title() == "Чат Котенков и Горь — 67 часов внутри AI-гонки"
        assert page.locator("main section").count() == 7
        assert page.locator("#overview h1").is_visible()
        assert page.locator("[data-message-id='497797']").first.is_visible()
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1
    finally:
        context.close()


def test_navigation_copy_control_and_reduced_motion(browser):
    context, page, _, page_errors, console_errors = _open(browser, 390, reduced_motion="reduce")
    try:
        page.locator("a[href='#gems']").first.click()
        assert page.url.endswith("#gems")
        assert page.locator("#gems").is_visible()
        assert page.locator("html").get_attribute("data-motion") == "reduced"
        assert page.locator("[data-reveal][data-visible='true']").count() >= 20

        copy_button = page.locator("[data-copy-link]").first
        assert copy_button.is_visible()
        box = copy_button.bounding_box()
        assert box and box["width"] >= 44 and box["height"] >= 44
        copy_button.click()
        status = page.locator("#copy-status")
        expect(status).not_to_have_text("", timeout=3000)
        assert status.get_attribute("aria-live") == "polite"
        assert page.locator("a[href='https://t.me/c/1778093200/497797']").count() >= 1
        assert page_errors == []
        assert console_errors == []
    finally:
        context.close()


def test_static_document_remains_complete_without_javascript(browser):
    context, page, requests, page_errors, console_errors = _open(
        browser, 390, java_script_enabled=False
    )
    try:
        assert page.locator("#overview h1").is_visible()
        assert page.locator("#gems .gem-card[data-message-id='500156']").is_visible()
        assert page.locator(".metric-value[data-value='2743']").inner_text() == "2\u202f743"
        assert page.locator("a[href='https://t.me/c/1778093200/500250']").count() >= 1
        assert not any(urlsplit(url).scheme in {"http", "https", "ws", "wss"} for url in requests)
        assert page_errors == []
        assert console_errors == []
    finally:
        context.close()


def test_mobile_nav_tracks_and_centers_the_current_section(browser):
    context, page, _, page_errors, console_errors = _open(browser, 390)
    try:
        page.locator("#method").scroll_into_view_if_needed()
        method_link = page.locator("[data-nav-target='method']")
        expect(method_link).to_have_attribute("aria-current", "", timeout=3000)
        nav_box = page.locator(".dossier-nav").bounding_box()
        link_box = method_link.bounding_box()
        assert nav_box and link_box
        assert link_box["x"] >= nav_box["x"] - 1
        assert link_box["x"] + link_box["width"] <= nav_box["x"] + nav_box["width"] + 1

        page.locator("a[href='#polls']").first.click()
        heading = page.locator("#polls h2").first
        expect(heading).to_be_in_viewport(timeout=3000)
        header_box = page.locator(".site-header").bounding_box()
        heading_box = heading.bounding_box()
        assert header_box and heading_box
        assert heading_box["y"] >= header_box["y"] + header_box["height"]
        assert page_errors == []
        assert console_errors == []
    finally:
        context.close()
