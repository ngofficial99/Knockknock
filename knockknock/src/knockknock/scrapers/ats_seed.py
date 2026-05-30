"""Loader for ATS (Greenhouse / Lever / Ashby) seed-company YAML files.

The seed YAML lists the companies whose public job boards we want each ATS
scraper to fetch. Format:

    companies:
      - slug: razorpay
        name: Razorpay
      - slug: cred
        name: CRED

``slug`` is the API path segment (Greenhouse: ``/v1/boards/<slug>/jobs``;
Lever: ``/v0/postings/<slug>?mode=json``; Ashby:
``/posting-api/job-board/<slug>``). ``name`` is the human-readable company
name we surface downstream when building :class:`ScrapedJob` rows.

The loader validates structure eagerly and rejects malformed/empty files so
that misconfiguration fails at pipeline-startup, not deep inside an HTTP
loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class AtsSeedCompany:
    """A single (slug, name) pair from a seed YAML."""

    slug: str
    name: str


def load_ats_seed(path: Path) -> list[AtsSeedCompany]:
    """Parse and validate a ``config/seed_companies_*.yaml`` file.

    Raises :class:`ValueError` on any structural problem (missing top-level
    key, empty list, missing slug/name on an entry, duplicate slug).
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "companies" not in raw:
        raise ValueError(f"{path}: missing top-level 'companies' key")
    companies_raw = raw["companies"]
    if not isinstance(companies_raw, list) or not companies_raw:
        raise ValueError(f"{path}: 'companies' must list at least one company")
    seen: set[str] = set()
    out: list[AtsSeedCompany] = []
    for entry in companies_raw:
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: company entries must be mappings, got {entry!r}")
        slug = str(entry.get("slug", "")).strip()
        name = str(entry.get("name", "")).strip()
        if not slug or not name:
            raise ValueError(f"{path}: each company needs slug + name, got {entry!r}")
        if slug in seen:
            raise ValueError(f"{path}: duplicate slug {slug!r}")
        seen.add(slug)
        out.append(AtsSeedCompany(slug=slug, name=name))
    return out
