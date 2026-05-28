"""Map enabled sources from preferences to scraper instances."""

from __future__ import annotations

from knockknock.config.preferences import JobPreferences
from knockknock.scrapers.base import Scraper
from knockknock.scrapers.hn import HNScraper


def build_scrapers(prefs: JobPreferences) -> list[Scraper]:
    """Return scrapers in the order Phase 3 supports them.

    Sources other than HN are added in Phase 10.
    """
    scrapers: list[Scraper] = []
    if prefs.sources.hn.enabled:
        scrapers.append(HNScraper(months_lookback=prefs.sources.hn.months_lookback))
    return scrapers
