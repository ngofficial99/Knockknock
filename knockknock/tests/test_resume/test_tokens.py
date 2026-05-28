"""Unit tests for the deterministic token extractor.

The extractor turns a job's title + description into the canonical-tag
set used by the selector. Two correctness properties matter:

- Multi-word synonyms must apply BEFORE single-token canonicalisation
  so ``"distributed systems"`` resolves to ``"distributed"`` instead of
  the noisy individual tokens.
- The output is a ``frozenset`` of lowercase tokens; the selector
  performs a set-intersection so dedup is automatic.
"""

from __future__ import annotations

from pathlib import Path

from knockknock.resume.manifest import load_manifest
from knockknock.resume.tokens import extract_job_tokens


def _manifest():
    return load_manifest(Path("resumes/manifest.yaml"))


def test_extract_tokens_lowercases_and_dedupes() -> None:
    """Same tag from different surface forms collapses into one entry."""
    manifest = _manifest()
    tokens = extract_job_tokens(
        manifest,
        title="Senior Backend Engineer",
        description="Python, Python, Kafka, AWS.",
    )
    assert "backend" in tokens
    assert "python" in tokens
    assert "kafka" in tokens
    assert "cloud" in tokens  # aws → cloud per seed dictionary


def test_extract_tokens_applies_multi_word_synonyms() -> None:
    """``distributed systems`` → ``distributed``, not ``distributed`` AND ``systems``."""
    manifest = _manifest()
    tokens = extract_job_tokens(
        manifest,
        title="Engineer",
        description="Experience with distributed systems and machine learning.",
    )
    assert "distributed" in tokens
    assert "llm" in tokens  # "machine learning" → llm


def test_extract_tokens_drops_short_words_and_stopwords() -> None:
    """``a``, ``the``, ``and`` etc. would otherwise pollute the overlap calc."""
    manifest = _manifest()
    tokens = extract_job_tokens(
        manifest,
        title="A B C",
        description="The and we you with for at on in is are be to of by an or so",
    )
    assert tokens == frozenset()


def test_extract_tokens_handles_empty_inputs() -> None:
    manifest = _manifest()
    assert extract_job_tokens(manifest, title="", description="") == frozenset()


def test_extract_tokens_handles_none_inputs() -> None:
    """Title / description can be NULL in the DB; treat as empty."""
    manifest = _manifest()
    assert extract_job_tokens(manifest, title=None, description=None) == frozenset()
