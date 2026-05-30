from __future__ import annotations

from pathlib import Path

from knockknock.config.preferences import load_preferences
from knockknock.db.enums import JobSource
from knockknock.scrapers.hn import HNScraper
from knockknock.scrapers.registry import build_scrapers


def test_build_scrapers_returns_only_enabled(tmp_path: Path) -> None:
    prefs_text = """
candidate: {name: x, email: "x@example.com", current_role: x, years_experience: 1, location: x}
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
  wellfound: {enabled: false}
  yc_waas: {enabled: false}
  greenhouse: {enabled: false}
  lever: {enabled: false}
  ashby: {enabled: false}
"""
    f = tmp_path / "p.yaml"
    f.write_text(prefs_text)
    prefs = load_preferences(f)
    scrapers = build_scrapers(prefs)
    assert len(scrapers) == 1
    assert scrapers[0].source == JobSource.HN
    assert isinstance(scrapers[0], HNScraper)


def test_build_scrapers_enables_full_set(tmp_path: Path) -> None:
    """All six sources enabled -> six scrapers, one per JobSource."""
    gh = tmp_path / "gh.yaml"
    gh.write_text("companies: [{slug: razorpay, name: Razorpay}]\n")
    lv = tmp_path / "lv.yaml"
    lv.write_text("companies: [{slug: groww, name: Groww}]\n")
    ay = tmp_path / "ay.yaml"
    ay.write_text("companies: [{slug: postman, name: Postman}]\n")

    prefs_text = f"""
candidate: {{name: x, email: "x@example.com", current_role: x, years_experience: 1, location: x}}
target:
  locations: [x]
  titles_allow: [x]
  titles_deny: []
  seniority_allow: [x]
  company_size_allow: [SEED]
skills: {{must_have_any: [x], nice_to_have: []}}
scoring:
  min_score_to_draft: 70
  weight_skill_match: 40
  weight_seniority_match: 25
  weight_location_match: 20
  weight_company_stage: 15
limits: {{daily_drafts_cap: 1, hourly_discover_cap: 1, gemini_pro_rpd_ceiling: 1}}
sources:
  hn: {{enabled: true, months_lookback: 1}}
  wellfound: {{enabled: true, location: Bangalore, role_types: [engineering], remote: true}}
  yc_waas: {{enabled: true, location: India, role: engineer}}
  greenhouse: {{enabled: true, company_seed_list_path: "{gh}"}}
  lever: {{enabled: true, company_seed_list_path: "{lv}"}}
  ashby: {{enabled: true, company_seed_list_path: "{ay}"}}
"""
    f = tmp_path / "p.yaml"
    f.write_text(prefs_text)
    prefs = load_preferences(f)
    scrapers = build_scrapers(prefs)

    sources = {s.source for s in scrapers}
    assert sources == {
        JobSource.HN,
        JobSource.WELLFOUND,
        JobSource.YC_WAAS,
        JobSource.GREENHOUSE,
        JobSource.LEVER,
        JobSource.ASHBY,
    }
    names = {s.name for s in scrapers}
    assert names == {"hn", "wellfound", "yc_waas", "greenhouse", "lever", "ashby"}
