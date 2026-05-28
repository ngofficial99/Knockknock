from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from knockknock.db.models import CompanyBlacklist
from knockknock.filter.blacklist import BlacklistMatcher, sync_blacklist_from_yaml


def _write_yaml(tmp_path: Path, body: str) -> Path:
    f = tmp_path / "bl.yaml"
    f.write_text(body)
    return f


def test_sync_inserts_new_entries(db_session: Session, tmp_path: Path) -> None:
    f = _write_yaml(
        tmp_path,
        """
entries:
  - {pattern: "acme", reason: "test"}
  - {pattern: "%spam%", reason: "spam"}
""",
    )
    inserted, removed = sync_blacklist_from_yaml(db_session, f)
    assert inserted == 2
    assert removed == 0
    rows = db_session.exec(select(CompanyBlacklist)).all()
    assert {r.pattern for r in rows} == {"acme", "%spam%"}


def test_sync_is_idempotent(db_session: Session, tmp_path: Path) -> None:
    f = _write_yaml(tmp_path, 'entries:\n  - {pattern: "acme", reason: "x"}\n')
    sync_blacklist_from_yaml(db_session, f)
    inserted, removed = sync_blacklist_from_yaml(db_session, f)
    assert inserted == 0
    assert removed == 0


def test_sync_removes_entries_dropped_from_yaml(db_session: Session, tmp_path: Path) -> None:
    f1 = _write_yaml(
        tmp_path,
        'entries:\n  - {pattern: "a", reason: "x"}\n  - {pattern: "b", reason: "y"}\n',
    )
    sync_blacklist_from_yaml(db_session, f1)
    f2 = _write_yaml(tmp_path, 'entries:\n  - {pattern: "a", reason: "x"}\n')
    inserted, removed = sync_blacklist_from_yaml(db_session, f2)
    assert inserted == 0
    assert removed == 1


def test_matcher_returns_pattern_on_hit(db_session: Session, tmp_path: Path) -> None:
    f = _write_yaml(
        tmp_path,
        (
            "entries:\n"
            '  - {pattern: "%consulting%", reason: "x"}\n'
            '  - {pattern: "zeotap", reason: "y"}\n'
        ),
    )
    sync_blacklist_from_yaml(db_session, f)
    matcher = BlacklistMatcher.load(db_session)
    assert matcher.match("Big Consulting Co", "bigconsulting.com") == "%consulting%"
    assert matcher.match("Zeotap", "zeotap.com") == "zeotap"
    assert matcher.match("Acme Labs", "acme.test") is None
