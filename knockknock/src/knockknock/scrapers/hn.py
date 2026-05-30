"""HN 'Ask HN: Who is hiring?' scraper using Algolia HN Search API.

We use ``/search_by_date`` rather than ``/search`` so we always pick the
*most recent* Who-is-hiring threads (the popular endpoint returns the
all-time most-relevant matches, which include threads from 2016+ that no
longer contain live jobs).

Header parsing is intentionally lenient: most real comments do not put the
domain in parentheses next to the company name. Instead we derive the
company domain from the first URL in the comment body.

Segment classification (added 2026-05-28 to fix field-mapping bug):
Real HN headers use many shapes -- ``Company | Role | Location``, but also
``Company | Location | Mode``, ``Company | Mode | Location | Role``, etc.
The earlier parser blindly assigned ``title=parts[1]`` and
``location=parts[2]``, causing locations to leak into titles for ~18% of
live HN comments. The new classifier inspects each segment's CONTENT and
picks the first role-looking segment as title, first location-looking
segment as location.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from selectolax.parser import HTMLParser
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.exceptions import ExternalServiceError
from knockknock.scrapers._salary import parse_salary_from_text
from knockknock.scrapers.base import ScrapedJob

ALGOLIA = "https://hn.algolia.com/api/v1"
HEADER_SEPARATOR = re.compile(r"\s*\|\s*")
DOMAIN_HINT = re.compile(r"\(([^()\s]+\.[a-z]{2,})\)", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

# ---- Header-segment classification ------------------------------------------
#
# Role tokens: single whole words that strongly suggest a job-title segment.
# Whole-word match via ``\b`` so "Engineer" hits "Senior Engineer" but not
# "Engineering" (which is fine -- ``Engineering Manager`` headers should
# still classify as role, but the title itself contains the word "Manager"
# so downstream pre-filter catches it via the deny list).
_ROLE_TOKENS = (
    "engineer",
    "developer",
    "programmer",
    "designer",
    "manager",
    "scientist",
    "architect",
    "lead",
    "founder",
    "sde",
    "swe",
    "sre",
    "devops",
    "fullstack",
    "full-stack",
    "backend",
    "frontend",
    "front-end",
    "qa",
    "analyst",
    "researcher",
    "intern",
)
_ROLE_RE = re.compile(r"\b(" + "|".join(_ROLE_TOKENS) + r")\b", re.IGNORECASE)

# Location tokens: explicit mode markers + the bulk are caught by the
# substring fallback ("city, country" patterns, multi-word geo names).
_MODE_TOKENS = ("remote", "onsite", "on-site", "hybrid", "wfh", "anywhere")
_MODE_RE = re.compile(r"\b(" + "|".join(_MODE_TOKENS) + r")\b", re.IGNORECASE)

# A short geo-token list -- not exhaustive; intended to catch cases where
# the role-classifier returns false (e.g. "Berlin, Germany" with no role
# tokens). Comma-and-capitalised-word pattern is the structural backup.
_GEO_HINT_RE = re.compile(
    r"\b("
    r"bengaluru|bangalore|mumbai|delhi|hyderabad|pune|chennai|kolkata|"
    r"london|berlin|paris|amsterdam|dublin|barcelona|madrid|munich|zurich|"
    r"san francisco|sf|nyc|new york|boston|seattle|austin|chicago|toronto|"
    r"vancouver|montreal|singapore|tokyo|sydney|melbourne|tel aviv|"
    r"india|uk|usa|us|eu|emea|apac|americas|europe|worldwide|global"
    r")\b",
    re.IGNORECASE,
)

# Employment-type tokens we must NOT classify as location.
_EMPLOYMENT_TYPE_TOKENS = (
    "full-time",
    "full time",
    "part-time",
    "part time",
    "contract",
    "freelance",
    "internship",
)
_EMPLOYMENT_TYPE_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in _EMPLOYMENT_TYPE_TOKENS) + r")\b", re.IGNORECASE
)


def _looks_like_role(segment: str) -> bool:
    return bool(_ROLE_RE.search(segment))


def _looks_like_location(segment: str) -> bool:
    """Geo or mode tokens suggest this segment is the location."""
    if _MODE_RE.search(segment):
        return True
    if _GEO_HINT_RE.search(segment):
        return True
    # Structural: "X, Y" with a capitalised word on either side, e.g.
    # "Berlin, Germany", "St Paul, MN", "Toronto, ON".
    return bool(re.search(r"^[A-Z][\w\s.]+,\s*[A-Z][\w\s]+$", segment.strip()))


def _is_employment_type(segment: str) -> bool:
    return bool(_EMPLOYMENT_TYPE_RE.search(segment))


def _is_url(segment: str) -> bool:
    return segment.startswith(("http://", "https://"))


def _classify_header_segments(segments: list[str]) -> tuple[str, str]:
    """Return ``(title, location)`` chosen by content from ``segments[1:]``.

    Segment 0 is always treated as the company by the caller, so this
    function only inspects 1..N. Selection rules:

    - ``title``: first segment that matches a role token.
    - ``location``: first segment that matches a location/mode token AND
      is not an employment-type-only segment.
    - If no role segment exists, return ``title="(no role in header)"``
      so the pre-filter's title-looks-like-location rule still catches
      it but we recover the real location.
    - URL segments and employment-type segments are skipped for both.
    """
    title: str | None = None
    location: str | None = None
    for seg in segments[1:]:
        if _is_url(seg):
            continue
        # Role takes priority -- the same segment can contain both a role
        # and a location ("Backend Engineer, Bengaluru"); we want it as title.
        if title is None and _looks_like_role(seg):
            title = seg
            continue
        if location is None and _looks_like_location(seg) and not _is_employment_type(seg):
            location = seg
            continue
    if title is None:
        title = "(no role in header)"
    if location is None:
        location = ""
    return title, location


@dataclass
class HNScraper:
    # Class-level identifiers shared by all instances. Kept as ClassVar
    # equivalents (plain class attrs, not dataclass fields) so the
    # @dataclass machinery doesn't try to make them per-instance.
    source: JobSource = JobSource.HN
    months_lookback: int = 1
    _client: httpx.Client | None = None

    # Log/metric name -- Phase 10 added this to the Scraper Protocol so
    # we can write ``log.info("hn.thread_found", ...)`` without having
    # to ``self.source.value.lower()`` everywhere.
    name: str = "hn"

    def scrape(self) -> Iterator[ScrapedJob]:
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
        """Return Who-is-hiring thread IDs sorted newest-first.

        ``/search_by_date`` returns Algolia results sorted by ``created_at``
        descending, which is exactly what we want.
        """
        params: dict[str, str | int] = {
            "query": "Ask HN Who is hiring",
            "tags": "story,author_whoishiring",
            "hitsPerPage": 10,
        }
        resp = client.get(f"{ALGOLIA}/search_by_date", params=params)
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
        if resp.status_code >= 500:
            raise httpx.HTTPStatusError("server", request=resp.request, response=resp)
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
        # A job header conventionally has at least three pipe-separated
        # parts. Common shapes:
        #   "Company | Role | Location | ..."
        #   "Company | Location | ONSITE/REMOTE | ..."
        # We require >=3 to weed out chatter.
        if len(parts) < 3:
            return None

        # Find the first URL anywhere in the comment - we need a domain.
        # HN HTML-encodes slashes (`https:&#x2F;&#x2F;...`), so anchor-tag
        # extraction via selectolax is more reliable than regex on raw HTML.
        apply_url: str | None = None
        for anchor in tree.css("a[href]"):
            href = anchor.attributes.get("href")
            if href and href.startswith(("http://", "https://")):
                apply_url = href
                break
        if apply_url is None:
            # Fall back to regex on the decoded text body for plain-text URLs.
            url_match = URL_RE.search(full_text)
            if not url_match:
                return None
            apply_url = url_match.group(0)
        url_host = urlparse(apply_url).netloc.lower()
        if url_host.startswith("www."):
            url_host = url_host[4:]
        if not url_host:
            return None

        # Prefer an explicit (domain.tld) in the header; fall back to the URL host.
        company_part = parts[0]
        header_domain_match = DOMAIN_HINT.search(company_part)
        if header_domain_match:
            company_domain = header_domain_match.group(1).lower()
            company_name = DOMAIN_HINT.sub("", company_part).strip() or company_domain
        else:
            company_domain = url_host
            company_name = company_part

        title, location = _classify_header_segments(parts)

        body_text = (tree.text(separator=" ") or "").strip()
        posted_at = datetime.fromtimestamp(comment.get("created_at_i", 0), tz=UTC)

        # Best-effort salary extraction from the comment body. Falls back to
        # all-None when nothing parseable is found, which is the common case
        # for HN. The full text (header + body) is scanned because some posts
        # put comp inline in the header (e.g. "Acme | Eng | $150k-200k").
        salary = parse_salary_from_text(full_text)

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
            salary_min=salary.min_amount if salary else None,
            salary_max=salary.max_amount if salary else None,
            salary_currency=salary.currency if salary else None,
            salary_period=salary.period if salary else None,
            salary_raw=salary.raw if salary else None,
        )
