"""HN 'Ask HN: Who is hiring?' scraper using Algolia HN Search API."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from selectolax.parser import HTMLParser
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.exceptions import ExternalServiceError
from knockknock.scrapers.base import ScrapedJob

ALGOLIA = "https://hn.algolia.com/api/v1"
HEADER_SEPARATOR = re.compile(r"\s*\|\s*")
DOMAIN_HINT = re.compile(r"\(([^()\s]+\.[a-z]{2,})\)", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


@dataclass
class HNScraper:
    source: JobSource = JobSource.HN
    months_lookback: int = 1
    _client: httpx.Client | None = None

    def fetch(self) -> Iterator[ScrapedJob]:
        client = self._client or httpx.Client(
            timeout=20.0, headers={"User-Agent": "knockknock/0.1"}
        )
        try:
            thread_ids = self._find_threads(client)
            for thread_id in thread_ids[: self.months_lookback]:
                yield from self._fetch_thread(client, thread_id)
        finally:
            if self._client is None:
                client.close()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=8),
        reraise=True,
    )
    def _find_threads(self, client: httpx.Client) -> list[str]:
        params: dict[str, str | int] = {
            "query": "Ask HN Who is hiring",
            "tags": "story,author_whoishiring",
            "hitsPerPage": 10,
        }
        resp = client.get(f"{ALGOLIA}/search", params=params)
        if resp.status_code >= 500:
            raise httpx.HTTPStatusError("server", request=resp.request, response=resp)
        if resp.status_code >= 400:
            raise ExternalServiceError(f"HN search failed: {resp.status_code}")
        hits = resp.json().get("hits", [])
        return [h["objectID"] for h in hits if "Who is hiring" in (h.get("title") or "")]

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=8),
        reraise=True,
    )
    def _fetch_thread(self, client: httpx.Client, thread_id: str) -> Iterator[ScrapedJob]:
        resp = client.get(f"{ALGOLIA}/items/{thread_id}")
        if resp.status_code >= 400:
            raise ExternalServiceError(f"HN thread {thread_id} failed: {resp.status_code}")
        for comment in resp.json().get("children", []) or []:
            parsed = self._parse_comment(comment)
            if parsed is not None:
                yield parsed

    def _parse_comment(self, comment: dict[str, Any]) -> ScrapedJob | None:
        raw_html = comment.get("text") or ""
        if not raw_html:
            return None
        tree = HTMLParser(raw_html)
        full_text = tree.text(separator="\n") or ""
        header = full_text.splitlines()[0] if full_text else ""
        parts = [p.strip() for p in HEADER_SEPARATOR.split(header) if p.strip()]
        # A job header conventionally has at least: Company | Role | Location
        if len(parts) < 3:
            return None

        company_part = parts[0]
        domain_match = DOMAIN_HINT.search(company_part)
        if not domain_match:
            return None
        company_name = DOMAIN_HINT.sub("", company_part).strip()
        company_domain = domain_match.group(1)

        title = parts[1]
        location = parts[2]

        body_text = (tree.text(separator=" ") or "").strip()
        url_match = URL_RE.search(raw_html)
        if not url_match:
            return None
        apply_url = url_match.group(0)
        posted_at = datetime.fromtimestamp(comment.get("created_at_i", 0), tz=UTC)

        return ScrapedJob(
            source=JobSource.HN,
            source_job_id=str(comment["id"]),
            company_name=company_name or company_domain,
            company_domain=company_domain,
            company_size_bucket=CompanySizeBucket.UNKNOWN,
            title=title,
            location=location,
            apply_url=apply_url,
            description=body_text,
            posted_at=posted_at,
        )
