"""Tests for the shared ATS seed-company YAML loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from knockknock.scrapers.ats_seed import AtsSeedCompany, load_ats_seed


def test_load_ats_seed_returns_companies(tmp_path: Path) -> None:
    p = tmp_path / "seed.yaml"
    p.write_text(
        """
companies:
  - slug: razorpay
    name: Razorpay
  - slug: cred
    name: CRED
"""
    )
    seeds = load_ats_seed(p)
    assert seeds == [
        AtsSeedCompany(slug="razorpay", name="Razorpay"),
        AtsSeedCompany(slug="cred", name="CRED"),
    ]


def test_load_ats_seed_rejects_empty(tmp_path: Path) -> None:
    p = tmp_path / "seed.yaml"
    p.write_text("companies: []\n")
    with pytest.raises(ValueError, match="at least one company"):
        load_ats_seed(p)


def test_load_ats_seed_rejects_duplicate_slug(tmp_path: Path) -> None:
    p = tmp_path / "seed.yaml"
    p.write_text(
        """
companies:
  - {slug: a, name: A}
  - {slug: a, name: A2}
"""
    )
    with pytest.raises(ValueError, match="duplicate slug"):
        load_ats_seed(p)


def test_load_ats_seed_rejects_missing_top_level_key(tmp_path: Path) -> None:
    """A YAML that is a list (not a mapping with ``companies``) must be rejected."""
    p = tmp_path / "seed.yaml"
    p.write_text("- slug: razorpay\n  name: Razorpay\n")
    with pytest.raises(ValueError, match="companies"):
        load_ats_seed(p)


def test_load_ats_seed_rejects_entry_missing_slug_or_name(tmp_path: Path) -> None:
    """Each entry needs both ``slug`` and ``name`` (non-empty)."""
    p = tmp_path / "seed.yaml"
    p.write_text(
        """
companies:
  - slug: razorpay
"""
    )
    with pytest.raises(ValueError, match="slug \\+ name"):
        load_ats_seed(p)
