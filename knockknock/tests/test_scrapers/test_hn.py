from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from knockknock.db.enums import JobSource
from knockknock.scrapers.hn import HNScraper

FIX = Path(__file__).parent.parent / "fixtures"

# Algolia recency endpoint - HNScraper queries this so we always pick
# the most recent "Who is hiring" threads rather than the popular ones.
SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"


@respx.mock
def test_hn_scraper_finds_latest_thread_and_parses_jobs() -> None:
    thread_payload = json.loads((FIX / "hn_thread.json").read_text())
    comments_payload = json.loads((FIX / "hn_comments.json").read_text())

    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=thread_payload))
    respx.get("https://hn.algolia.com/api/v1/items/9000001").mock(
        return_value=httpx.Response(200, json=comments_payload)
    )

    scraper = HNScraper(months_lookback=1)
    jobs = list(scraper.fetch())

    sources = {j.source for j in jobs}
    assert sources == {JobSource.HN}
    titles = {j.title for j in jobs}
    assert "Backend Engineer" in titles
    # company_domain is derived from the apply URL host when not in header
    assert any("acme.test" in j.company_domain for j in jobs)


@respx.mock
def test_hn_scraper_emits_jobs_when_header_lacks_paren_domain() -> None:
    """Most real Who-is-hiring comments don't put `(domain.tld)` in the header.

    The scraper should still emit them as long as there is a parseable
    pipe-separated header and at least one URL we can use for the domain.
    """
    thread_payload = json.loads((FIX / "hn_thread.json").read_text())
    comments_payload = json.loads((FIX / "hn_comments.json").read_text())

    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=thread_payload))
    respx.get("https://hn.algolia.com/api/v1/items/9000001").mock(
        return_value=httpx.Response(200, json=comments_payload)
    )

    scraper = HNScraper(months_lookback=1)
    jobs = list(scraper.fetch())
    job_ids = {j.source_job_id for j in jobs}
    # 9000011 (BetaCo) has no (domain.tld) but has a beta.test URL -> emitted.
    assert "9000011" in job_ids
    # 9000012 has no pipes at all -> skipped.
    assert "9000012" not in job_ids


@respx.mock
def test_hn_scraper_handles_5xx_with_retry_then_fails() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(500))
    scraper = HNScraper(months_lookback=1)
    with pytest.raises(httpx.HTTPError):
        list(scraper.fetch())
