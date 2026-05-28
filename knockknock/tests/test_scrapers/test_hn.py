from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from knockknock.db.enums import JobSource
from knockknock.scrapers.hn import HNScraper

FIX = Path(__file__).parent.parent / "fixtures"


@respx.mock
def test_hn_scraper_finds_latest_thread_and_parses_jobs() -> None:
    thread_payload = json.loads((FIX / "hn_thread.json").read_text())
    comments_payload = json.loads((FIX / "hn_comments.json").read_text())

    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(200, json=thread_payload)
    )
    respx.get("https://hn.algolia.com/api/v1/items/9000001").mock(
        return_value=httpx.Response(200, json=comments_payload)
    )

    scraper = HNScraper(months_lookback=1)
    jobs = list(scraper.fetch())

    sources = {j.source for j in jobs}
    assert sources == {JobSource.HN}
    titles = {j.title for j in jobs}
    assert "Backend Engineer" in titles
    assert any("acme.test" in j.company_domain for j in jobs)


@respx.mock
def test_hn_scraper_skips_non_job_comments() -> None:
    thread_payload = json.loads((FIX / "hn_thread.json").read_text())
    comments_payload = json.loads((FIX / "hn_comments.json").read_text())

    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(200, json=thread_payload)
    )
    respx.get("https://hn.algolia.com/api/v1/items/9000001").mock(
        return_value=httpx.Response(200, json=comments_payload)
    )

    scraper = HNScraper(months_lookback=1)
    jobs = list(scraper.fetch())
    # Comment 9000012 is a non-job line and should be skipped
    job_ids = {j.source_job_id for j in jobs}
    assert "9000012" not in job_ids


@respx.mock
def test_hn_scraper_handles_5xx_with_retry_then_fails() -> None:
    respx.get("https://hn.algolia.com/api/v1/search").mock(return_value=httpx.Response(500))
    scraper = HNScraper(months_lookback=1)
    with pytest.raises(httpx.HTTPError):
        list(scraper.fetch())
