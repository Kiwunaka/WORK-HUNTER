from __future__ import annotations

import hashlib
from contextlib import contextmanager

import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        item.browser_test_failed = report.failed


@pytest.fixture
def browser_artifacts(request):
    """Capture only local fixture contexts, and retain artifacts only on failure."""
    @contextmanager
    def record(context):
        context.tracing.start(screenshots=True, snapshots=True, sources=False)
        try:
            yield
        finally:
            if getattr(request.node, "browser_test_failed", False):
                key = hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]
                directory = request.config.rootpath / "test-results" / key
                directory.mkdir(parents=True, exist_ok=True)
                for index, page in enumerate(context.pages):
                    if not page.is_closed():
                        page.screenshot(path=str(directory / f"page-{index}.png"))
                context.tracing.stop(path=str(directory / "trace.zip"))
            else:
                context.tracing.stop()
    return record
