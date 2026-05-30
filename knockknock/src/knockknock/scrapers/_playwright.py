"""Thin Playwright wrapper for scrapers that need a JS-rendered DOM.

Wellfound and YC WaaS don't publish a stable public JSON API; their job
listings are rendered server-side and hydrated with React. We centralize
Chromium lifecycle in this module for two reasons:

1. **Tests inject a fake renderer.** Pytest doesn't need Chromium; the
   :class:`PlaywrightFetcher` dataclass takes an ``_renderer`` callable so
   unit tests can return a hand-crafted HTML fixture instead of standing
   up a browser. Live integration is exercised via the manual smoke run
   in Task 10.11.

2. **Shared auth/cookies plumbing.** Both Wellfound and YC WaaS render
   from the same Chromium context (same UA + viewport). If we later need
   to persist cookies (e.g. a logged-in Wellfound session) we extend this
   helper once.

The real ``playwright.sync_api`` import lives inside
:func:`_render_with_playwright` so importing this module never pays the
Playwright cost — important because the bare CLI imports
``knockknock.scrapers`` eagerly via :mod:`knockknock.scrapers.registry`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

Renderer = Callable[..., str]

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


def _render_with_playwright(url: str, *, wait_selector: str | None = None) -> str:
    """Real Chromium renderer. Import is lazy so unit tests skip the cost.

    Spins up a headless Chromium, navigates to ``url``, optionally waits
    for ``wait_selector`` to appear (post-hydration), and returns
    ``page.content()``. Timeouts: 30s for navigation, 20s for the wait
    selector.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 1024},
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            if wait_selector:
                page.wait_for_selector(wait_selector, timeout=20_000)
            html: str = page.content()
            return html
        finally:
            browser.close()


@dataclass(slots=True)
class PlaywrightFetcher:
    """Renders a URL to fully-hydrated HTML.

    Inject ``_renderer`` in tests to avoid Playwright; production code
    relies on the default :func:`_render_with_playwright`. Stored on a
    private attribute (``_renderer``) so callers don't accidentally treat
    it as part of the public surface.
    """

    _renderer: Renderer = field(default=_render_with_playwright)

    def render(self, url: str, *, wait_selector: str | None = None) -> str:
        """Render ``url`` to HTML, optionally waiting for ``wait_selector``."""
        return self._renderer(url, wait_selector=wait_selector)
