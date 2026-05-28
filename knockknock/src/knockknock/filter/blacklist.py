"""Blacklist YAML → DB sync and in-memory matcher.

The blacklist is a list of SQL-LIKE patterns (``%`` = any-run, ``_`` = any-char)
matched case-insensitively against either a company's name or its domain. This
module keeps the YAML file and the ``companies_blacklist`` table in lockstep
(``sync_blacklist_from_yaml``) and provides a small, eagerly-loaded matcher
(``BlacklistMatcher``) that pre-filter rule evaluation can call without hitting
the DB once per job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlmodel import Session, select

from knockknock.db.models import CompanyBlacklist
from knockknock.exceptions import ConfigError


def _load_yaml(path: Path) -> list[tuple[str, str | None]]:
    if not path.exists():
        raise FileNotFoundError(f"blacklist file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    entries = raw.get("entries") or []
    if not isinstance(entries, list):
        raise ConfigError("blacklist 'entries' must be a list")
    parsed: list[tuple[str, str | None]] = []
    for item in entries:
        if not isinstance(item, dict) or "pattern" not in item:
            raise ConfigError(f"invalid blacklist entry: {item!r}")
        pat = str(item["pattern"]).strip().lower()
        if not pat:
            raise ConfigError("empty pattern in blacklist")
        reason = item.get("reason")
        parsed.append((pat, None if reason is None else str(reason)))
    return parsed


def sync_blacklist_from_yaml(session: Session, path: Path) -> tuple[int, int]:
    """Reconcile DB rows to YAML. Returns ``(inserted, removed)``.

    Inserts rows present in YAML but missing in the DB, deletes rows present in
    the DB but missing from YAML. Reasons are not updated on re-sync (the
    pattern is the identity); change the pattern if the reason needs to evolve.
    """
    desired = _load_yaml(path)
    desired_patterns = {p for p, _ in desired}

    existing_rows = session.exec(select(CompanyBlacklist)).all()
    existing_patterns = {r.pattern for r in existing_rows}

    to_insert = desired_patterns - existing_patterns
    to_remove = existing_patterns - desired_patterns

    by_pattern = dict(desired)
    for pattern in to_insert:
        session.add(CompanyBlacklist(pattern=pattern, reason=by_pattern.get(pattern)))

    for row in existing_rows:
        if row.pattern in to_remove:
            session.delete(row)

    session.flush()
    return len(to_insert), len(to_remove)


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert a SQL-LIKE pattern into an anchored case-insensitive regex.

    ``%`` -> ``.*``, ``_`` -> ``.`` ; everything else is treated as literal.

    Implementation note: ``%`` and ``_`` are NOT regex metacharacters, so
    ``re.escape`` is a no-op on them. We substitute via NUL sentinels before
    escaping so user-supplied literal ``.`` or ``*`` chars stay literal.
    """
    sub = pattern.lower().replace("%", "\x00P\x00").replace("_", "\x00U\x00")
    escaped = re.escape(sub)
    escaped = escaped.replace(re.escape("\x00P\x00"), ".*").replace(re.escape("\x00U\x00"), ".")
    return re.compile(f"^{escaped}$")


@dataclass(frozen=True, slots=True)
class BlacklistMatcher:
    """Eagerly-loaded snapshot of the blacklist for in-memory matching."""

    patterns: tuple[tuple[str, re.Pattern[str]], ...]

    @classmethod
    def load(cls, session: Session) -> BlacklistMatcher:
        rows = session.exec(select(CompanyBlacklist)).all()
        compiled = tuple((r.pattern, _pattern_to_regex(r.pattern)) for r in rows)
        return cls(patterns=compiled)

    def match(self, company_name: str, company_domain: str) -> str | None:
        """Return the matching pattern (canonical form) or ``None``."""
        haystacks = (company_name.lower(), company_domain.lower())
        for raw_pattern, regex in self.patterns:
            for hay in haystacks:
                if regex.match(hay):
                    return raw_pattern
        return None
