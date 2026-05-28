"""Deterministic hard-rule pre-filter.

Pure-Python rule evaluation: no DB writes, no I/O. The caller (``PreFilterStage``)
takes a ``RuleVerdict`` and mutates ``JobApplication.status`` / ``rejection_reason``.

Rule order (first hit wins):

1.  blacklist (company name OR domain matches a SQL-LIKE pattern)
2.  data-quality: title looks like a location header (audit finding)
3.  data-quality: company_domain is a generic redirector (audit finding)
4.  title deny list
5.  title allow list
6.  location must match at least one configured location
7.  description must contain at least one ``skills.must_have_any`` token
8.  company.size_bucket must be in ``target.company_size_allow``
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from knockknock.config.preferences import JobPreferences
from knockknock.db.enums import RejectionReason
from knockknock.db.models import Company, JobApplication

# Tokens that, if found anywhere in a job title, suggest the "title" is really
# just a location/work-mode header (HN comment patterns like
# "AcmeCo | Bengaluru | ONSITE" with no role segment). Lowercased.
_TITLE_LOCATION_HINTS: frozenset[str] = frozenset(
    {
        "onsite",
        "remote",
        "hybrid",
        "bengaluru",
        "bangalore",
        "mumbai",
        "delhi",
        "hyderabad",
        "pune",
        "chennai",
        "kolkata",
        " usa",
        " india",
        " uk",
        " eu ",
    }
)

# Domains that are generic URL shorteners or profile-aggregator hosts; if
# this is the company's domain we don't have a real employer record.
_GENERIC_REDIRECTOR_DOMAINS: frozenset[str] = frozenset(
    {"linkedin.com", "bit.ly", "lnkd.in", "tinyurl.com", "goo.gl"}
)


@dataclass(frozen=True, slots=True)
class RuleVerdict:
    passed: bool
    reason: RejectionReason | None = None
    detail: str | None = None


BlacklistFn = Callable[[str, str], str | None]


@dataclass(frozen=True, slots=True)
class RuleEngine:
    """Applies hard rules. No DB writes; caller mutates state from the verdict."""

    prefs: JobPreferences
    blacklist_pattern: BlacklistFn

    def evaluate(self, job: JobApplication, company: Company) -> RuleVerdict:
        bl_hit = self.blacklist_pattern(company.name, company.domain)
        if bl_hit is not None:
            return RuleVerdict(False, RejectionReason.BLACKLISTED, f"matched pattern: {bl_hit}")

        title_lower = job.title.lower()
        if any(hint in title_lower for hint in _TITLE_LOCATION_HINTS):
            return RuleVerdict(
                False,
                RejectionReason.OTHER,
                f"title-looks-like-location: {job.title!r}",
            )

        if company.domain.lower() in _GENERIC_REDIRECTOR_DOMAINS:
            return RuleVerdict(
                False,
                RejectionReason.OTHER,
                f"generic-redirector domain: {company.domain}",
            )

        for deny in self.prefs.target.titles_deny:
            if deny.lower() in title_lower:
                return RuleVerdict(False, RejectionReason.ROLE_MISMATCH, f"deny title: {deny}")

        if not any(allow.lower() in title_lower for allow in self.prefs.target.titles_allow):
            return RuleVerdict(False, RejectionReason.ROLE_MISMATCH, "title not in allow list")

        location_lower = job.location.lower()
        if not any(loc.lower() in location_lower for loc in self.prefs.target.locations):
            return RuleVerdict(False, RejectionReason.LOCATION_MISMATCH, job.location)

        description_lower = job.description.lower()
        title_plus_desc = f"{title_lower}\n{description_lower}"
        if not any(skill.lower() in title_plus_desc for skill in self.prefs.skills.must_have_any):
            return RuleVerdict(False, RejectionReason.ROLE_MISMATCH, "no required skill found")

        if company.size_bucket not in self.prefs.target.company_size_allow:
            return RuleVerdict(False, RejectionReason.OTHER, f"size_bucket={company.size_bucket}")

        return RuleVerdict(True)
