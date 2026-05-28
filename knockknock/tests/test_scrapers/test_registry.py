from __future__ import annotations

from pathlib import Path

from knockknock.config.preferences import load_preferences
from knockknock.db.enums import JobSource
from knockknock.scrapers.hn import HNScraper
from knockknock.scrapers.registry import build_scrapers


def test_build_scrapers_returns_only_enabled(tmp_path: Path) -> None:
    prefs_text = """
candidate: {name: x, current_role: x, years_experience: 1, location: x}
target:
  locations: [x]
  titles_allow: [x]
  titles_deny: []
  seniority_allow: [x]
  company_size_allow: [SEED]
skills: {must_have_any: [x], nice_to_have: []}
scoring:
  min_score_to_draft: 70
  weight_skill_match: 40
  weight_seniority_match: 25
  weight_location_match: 20
  weight_company_stage: 15
limits: {daily_drafts_cap: 1, hourly_discover_cap: 1, gemini_pro_rpd_ceiling: 1}
sources:
  hn: {enabled: true, months_lookback: 1}
  wellfound: {enabled: false, query: ""}
  yc_waas: {enabled: false, query: ""}
  greenhouse: {enabled: false, boards: []}
  lever: {enabled: false, boards: []}
  ashby: {enabled: false, boards: []}
"""
    f = tmp_path / "p.yaml"
    f.write_text(prefs_text)
    prefs = load_preferences(f)
    scrapers = build_scrapers(prefs)
    assert len(scrapers) == 1
    assert scrapers[0].source == JobSource.HN
    assert isinstance(scrapers[0], HNScraper)
