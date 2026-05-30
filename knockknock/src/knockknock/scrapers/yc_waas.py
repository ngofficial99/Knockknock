"""YC Work-at-a-Startup scraper (public listings page only).

Job descriptions on ``workatastartup.com`` are gated behind YC login. This
scraper **does not** authenticate or attempt to read gated content; it
reads only the public listings page (titles + companies + locations +
batch labels) and synthesizes a short description from that public data
so the scoring stage has *something* to work with. Description quality
is acceptable in practice because YC-WaaS titles are usually
high-signal.

Selectors target the public listing structure:
  - ``.directory-list``   — top-level container
  - ``.company-card``     — one block per company
  - ``.company-name``     — anchor with ``/companies/<slug>``; text is
                            ``"<Company> (YC <batch>)"``
  - ``.company-batch``    — short batch label (``"S25"`` / ``"W26"``)
  - ``.job-row``          — one block per posting
  - ``.job-link``         — anchor with ``/companies/<slug>/jobs/<id-slug>``
  - ``.job-location``     — free-form location string

Defensive parsing: cards missing required fields are silently skipped
(layout drift is inevitable); a Playwright render failure is logged +
swallowed so the pipeline continues with other scrapers.

Location filter is **India-positive** but **excludes** US/EU "remote"
qualifiers — ``"Remote (US)"`` is dropped, ``"India · Remote"`` is kept.
The synthesized description is built from public data only.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Final
from urllib.parse import quote

import structlog
from selectolax.parser import HTMLParser, Node

from knockknock.db.enums import CompanySizeBucket, JobSource
from knockknock.scrapers._playwright import PlaywrightFetcher
from knockknock.scrapers.base import ScrapedJob

log = structlog.get_logger(__name__)

_BASE: Final = "https://www.workatastartup.com"
# India-positive: explicit Bangalore/Bengaluru/India anywhere in the string.
_INDIA_RE: Final = re.compile(r"(bangalore|bengaluru|india)", re.IGNORECASE)
# Non-India remote qualifiers; we drop the card when one of these appears
# (e.g. "Remote (US)", "Remote · USA", "Remote, UK"). "India · Remote" still
# matches _INDIA_RE so it is kept.
_NON_INDIA_REMOTE_RE: Final = re.compile(
    r"remote\s*[\(\[\-·,].{0,30}\b(us|usa|united\s*states|uk|"
    r"united\s*kingdom|eu|europe|emea|americas|latam|apac)\b",
    re.IGNORECASE,
)
# Pull the clean company name out of "Acme (YC S25)" / "Acme Inc. (YC W26)".
_COMPANY_NAME_RE: Final = re.compile(r"^(?P<name>.+?)\s*\(YC\s*[SWFX]\d{2}\)\s*$")


def _text_or_empty(node: Node | None) -> str:
    return node.text(strip=True) if node is not None else ""


def _attr_or_empty(node: Node | None, name: str) -> str:
    if node is None:
        return ""
    return node.attributes.get(name) or ""


def _location_matches(location: str) -> bool:
    """True iff the posting is India-relevant (Bangalore/Bengaluru/India)."""
    if not location:
        return False
    if _NON_INDIA_REMOTE_RE.search(location):
        return False
    return bool(_INDIA_RE.search(location))


class YcWaasScraper:
    """Yields one :class:`ScrapedJob` per India-relevant posting from one search."""

    source: JobSource = JobSource.YC_WAAS
    name: str = "yc_waas"

    def __init__(
        self,
        *,
        location: str,
        role: str,
        fetcher: PlaywrightFetcher,
    ) -> None:
        self._location = location
        self._role = role
        self._fetcher = fetcher

    def _build_url(self) -> str:
        return f"{_BASE}/companies?location={quote(self._location)}&role={quote(self._role)}"

    def scrape(self) -> Iterator[ScrapedJob]:
        url = self._build_url()
        try:
            html = self._fetcher.render(url, wait_selector=".company-card")
        except Exception as exc:
            log.warning("yc_waas.render_failed", url=url, error=str(exc))
            return
        tree = HTMLParser(html)
        for card in tree.css(".company-card"):
            yield from self._iter_company_jobs(card)

    def _iter_company_jobs(self, card: Node) -> Iterator[ScrapedJob]:
        name_link = card.css_first(".company-name")
        raw_name = _text_or_empty(name_link)
        if not raw_name:
            return
        match = _COMPANY_NAME_RE.match(raw_name)
        clean_name = match.group("name").strip() if match else raw_name
        company_href = _attr_or_empty(name_link, "href")
        company_slug = company_href.removeprefix("/companies/").strip("/")
        if not company_slug:
            return
        batch = _text_or_empty(card.css_first(".company-batch"))

        for row in card.css(".job-row"):
            location = _text_or_empty(row.css_first(".job-location"))
            if not _location_matches(location):
                continue
            link = row.css_first(".job-link")
            href = _attr_or_empty(link, "href")
            title = _text_or_empty(link)
            if not (href and title):
                continue
            source_job_id = (
                href.removeprefix(f"/companies/{company_slug}/jobs/")
                .removeprefix("/jobs/")
                .strip("/")
            )
            if not source_job_id:
                continue
            # Synthesized from public data only -- no login bypass.
            batch_suffix = f" \u2014 YC {batch}" if batch else ""
            description = f"{title} ({clean_name}{batch_suffix})"
            try:
                yield ScrapedJob(
                    source=JobSource.YC_WAAS,
                    source_job_id=f"{company_slug}:{source_job_id}",
                    company_name=clean_name,
                    company_domain=f"{company_slug}.com",
                    # YC startups default to SEED stage; Phase 6 phonebook
                    # enrichment may upgrade companies.size_bucket later.
                    company_size_bucket=CompanySizeBucket.SEED,
                    title=title,
                    location=location,
                    apply_url=f"{_BASE}{href}",
                    description=description,
                    posted_at=None,
                )
            except ValueError as exc:
                log.warning(
                    "yc_waas.invalid_job",
                    company=clean_name,
                    job_href=href,
                    error=str(exc),
                )
                continue
