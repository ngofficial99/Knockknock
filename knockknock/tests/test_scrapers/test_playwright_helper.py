"""Tests for the Playwright fetch helper.

The helper exists so scrapers that need a JS-rendered DOM (Wellfound, YC
WaaS) can share one Chromium lifecycle. The real renderer is imported
lazily inside the helper module; tests inject a fake to avoid standing
up a browser.
"""

from __future__ import annotations

from knockknock.scrapers import _playwright


def test_render_url_uses_injected_renderer() -> None:
    """``render`` forwards ``url`` and ``wait_selector`` to the injected callable."""
    captured: dict[str, str] = {}

    def fake_render(url: str, *, wait_selector: str | None = None) -> str:
        captured["url"] = url
        captured["wait"] = wait_selector or ""
        return f"<html><body>{url}</body></html>"

    helper = _playwright.PlaywrightFetcher(_renderer=fake_render)
    html = helper.render("https://example.test/jobs", wait_selector="div.job-card")
    assert "https://example.test/jobs" in html
    assert captured == {"url": "https://example.test/jobs", "wait": "div.job-card"}


def test_render_url_defaults_wait_selector_to_none() -> None:
    """When called without ``wait_selector`` the helper forwards ``None``."""
    captured: dict[str, str | None] = {}

    def fake_render(url: str, *, wait_selector: str | None = None) -> str:
        captured["url"] = url
        captured["wait"] = wait_selector
        return "<html></html>"

    helper = _playwright.PlaywrightFetcher(_renderer=fake_render)
    helper.render("https://example.test/")
    assert captured == {"url": "https://example.test/", "wait": None}
