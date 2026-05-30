"""Map enabled sources from preferences to scraper instances.

Each :class:`~knockknock.config.preferences.JobPreferences` source sub-model
has an ``enabled`` flag plus the kwargs the scraper needs. This module is
the single seam where YAML config maps to instantiated scrapers, so the
CLI (Phase 11) only ever calls :func:`build_scrapers`.

Shared resources (``httpx.Client`` for ATS scrapers, ``PlaywrightFetcher``
for Wellfound + YC WaaS) are injectable so the CLI can manage their
lifetime — Playwright in particular is expensive to start. Both default
to fresh instances for backwards compatibility with callers that don't
supply them.
"""

from __future__ import annotations

import httpx

from knockknock.config.preferences import JobPreferences
from knockknock.scrapers._playwright import PlaywrightFetcher
from knockknock.scrapers.ashby import AshbyScraper
from knockknock.scrapers.ats_seed import load_ats_seed
from knockknock.scrapers.base import Scraper
from knockknock.scrapers.greenhouse import GreenhouseScraper
from knockknock.scrapers.hn import HNScraper
from knockknock.scrapers.lever import LeverScraper
from knockknock.scrapers.wellfound import WellfoundScraper
from knockknock.scrapers.yc_waas import YcWaasScraper


def build_scrapers(
    prefs: JobPreferences,
    *,
    http_client: httpx.Client | None = None,
    fetcher: PlaywrightFetcher | None = None,
) -> list[Scraper]:
    """Build the list of enabled scrapers from ``prefs``.

    ``http_client`` and ``fetcher`` default to fresh instances. Pass
    pre-built instances to share connection pools / Chromium contexts
    across calls (the Phase 11 CLI does this).
    """
    http = http_client if http_client is not None else httpx.Client(timeout=30)
    pw = fetcher if fetcher is not None else PlaywrightFetcher()

    out: list[Scraper] = []
    s = prefs.sources

    if s.hn.enabled:
        out.append(HNScraper(months_lookback=s.hn.months_lookback))

    if s.wellfound.enabled:
        out.append(
            WellfoundScraper(
                location=s.wellfound.location,
                role_types=s.wellfound.role_types,
                remote=s.wellfound.remote,
                fetcher=pw,
            )
        )

    if s.yc_waas.enabled:
        out.append(
            YcWaasScraper(
                location=s.yc_waas.location,
                role=s.yc_waas.role,
                fetcher=pw,
            )
        )

    if s.greenhouse.enabled and s.greenhouse.company_seed_list_path is not None:
        out.append(
            GreenhouseScraper(
                seeds=load_ats_seed(s.greenhouse.company_seed_list_path),
                http=http,
            )
        )

    if s.lever.enabled and s.lever.company_seed_list_path is not None:
        out.append(
            LeverScraper(
                seeds=load_ats_seed(s.lever.company_seed_list_path),
                http=http,
            )
        )

    if s.ashby.enabled and s.ashby.company_seed_list_path is not None:
        out.append(
            AshbyScraper(
                seeds=load_ats_seed(s.ashby.company_seed_list_path),
                http=http,
            )
        )

    return out
