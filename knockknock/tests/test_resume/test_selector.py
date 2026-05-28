"""Unit tests for :func:`select_variant`.

The selector is the heart of the tailor stage: a deterministic
tag-overlap scorer with priority as a sub-integer tie-breaker. The
contract is small but invariant:

- Higher overlap always wins over higher priority.
- Same overlap → higher priority wins.
- Zero overlap anywhere → fall back to highest-priority variant
  (the "always pick something" rule documented in resumes/README.md).
- Reproducible across calls: ties resolve by manifest order.
"""

from __future__ import annotations

from knockknock.resume.manifest import ResumeManifest, ResumeVariant
from knockknock.resume.selector import select_variant


def _manifest(*variants: ResumeVariant) -> ResumeManifest:
    return ResumeManifest(variants=tuple(variants), tag_dictionary={})


def test_select_returns_highest_overlap() -> None:
    """Three-tag overlap beats two-tag overlap even at equal priority."""
    a = ResumeVariant("a", "a.pdf", frozenset({"python", "kafka"}), priority=10)
    b = ResumeVariant("b", "b.pdf", frozenset({"python", "kafka", "kubernetes"}), priority=10)
    manifest = _manifest(a, b)
    chosen = select_variant(manifest, frozenset({"python", "kafka", "kubernetes"}))
    assert chosen is not None
    assert chosen.key == "b"


def test_select_breaks_ties_by_priority() -> None:
    a = ResumeVariant("a", "a.pdf", frozenset({"python"}), priority=10)
    b = ResumeVariant("b", "b.pdf", frozenset({"python"}), priority=99)
    manifest = _manifest(a, b)
    chosen = select_variant(manifest, frozenset({"python"}))
    assert chosen is not None
    assert chosen.key == "b"


def test_select_overlap_beats_priority() -> None:
    """A two-tag overlap must beat a higher-priority one-tag overlap."""
    high_prio_low_overlap = ResumeVariant("hp", "hp.pdf", frozenset({"python"}), priority=999)
    low_prio_high_overlap = ResumeVariant(
        "lo", "lo.pdf", frozenset({"python", "kafka"}), priority=1
    )
    manifest = _manifest(high_prio_low_overlap, low_prio_high_overlap)
    chosen = select_variant(manifest, frozenset({"python", "kafka"}))
    assert chosen is not None
    assert chosen.key == "lo"


def test_select_falls_back_to_highest_priority_when_no_overlap() -> None:
    """Out-of-scope job → return the most-general variant rather than None."""
    a = ResumeVariant("a", "a.pdf", frozenset({"x"}), priority=10)
    b = ResumeVariant("b", "b.pdf", frozenset({"y"}), priority=50)
    manifest = _manifest(a, b)
    chosen = select_variant(manifest, frozenset({"unrelated"}))
    assert chosen is not None
    assert chosen.key == "b"


def test_select_returns_none_when_no_variants() -> None:
    """An empty manifest is a config error -- the stage records it as such."""
    manifest = ResumeManifest(variants=(), tag_dictionary={})
    assert select_variant(manifest, frozenset({"python"})) is None


def test_select_is_deterministic_across_calls() -> None:
    """Same inputs → same output, every time. Critical for reruns."""
    a = ResumeVariant("a", "a.pdf", frozenset({"python"}), priority=10)
    b = ResumeVariant("b", "b.pdf", frozenset({"python"}), priority=10)
    manifest = _manifest(a, b)
    chosen1 = select_variant(manifest, frozenset({"python"}))
    chosen2 = select_variant(manifest, frozenset({"python"}))
    assert chosen1 is not None and chosen2 is not None
    assert chosen1.key == chosen2.key
