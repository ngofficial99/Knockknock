"""Canonical-tag extractor for job postings. No I/O. Deterministic.

The tokeniser turns free-text title + description into the lowercase
canonical-tag set the selector consumes. Multi-word synonyms (e.g.
``"distributed systems"`` → ``"distributed"``) are applied **before**
the single-word scan so the noisy individual tokens never make it into
the result. Single tokens are then run through
:meth:`ResumeManifest.canonicalise_token` so simple aliases like
``k8s → kubernetes`` work without a phrase match.

Stopwords are hardcoded English low-signal words; they exist purely to
keep the overlap score honest -- ``"the"`` showing up in 100% of JDs
would otherwise be a free overlap point for every variant tagged with
it (and yes, manifest authors do sometimes accidentally tag that way).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from knockknock.resume.manifest import ResumeManifest

# Common low-signal English words; expanded conservatively. We keep this
# short so the tokeniser does not start filtering domain terms.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "you",
        "your",
        "our",
        "we",
        "are",
        "is",
        "be",
        "to",
        "of",
        "an",
        "or",
        "in",
        "on",
        "at",
        "by",
        "as",
        "so",
        "this",
        "that",
        "it",
        "from",
        "into",
        "have",
        "has",
        "will",
        "shall",
        "any",
        "all",
        "but",
        "not",
        "no",
        "yes",
    }
)
# Lowercased input only; tokens must start with a letter and be ≥ 2 chars.
# We allow ``+ # . -`` inside tokens so ``c++``, ``c#``, ``node.js``,
# ``co-pilot`` survive intact.
_WORD_RE = re.compile(r"[a-z][a-z0-9+#.\-]+")


def extract_job_tokens(
    manifest: ResumeManifest,
    *,
    title: str | None,
    description: str | None,
) -> frozenset[str]:
    """Build the canonical-token set for a job, ready for variant scoring.

    Applies multi-word synonyms greedily before the single-token scan so
    phrases like ``"distributed systems"`` resolve to ``"distributed"``
    without contributing the noisy ``systems`` token afterwards.
    """
    text = f"{title or ''} {description or ''}".lower()
    if not text.strip():
        return frozenset()

    # 1) Multi-word synonyms first; erase matched phrases so the
    #    single-word scan doesn't double-count them.
    multi_word = {syn: canon for syn, canon in manifest.tag_dictionary.items() if " " in syn}
    canonical_from_multi: set[str] = set()
    for synonym, canonical in multi_word.items():
        if synonym in text:
            canonical_from_multi.add(canonical)
            text = text.replace(synonym, " ")

    # 2) Single-token scan with canonicalisation + stoplist.
    tokens: set[str] = set(canonical_from_multi)
    for match in _WORD_RE.findall(text):
        # Trailing punctuation like ``aws.`` would survive the regex; strip
        # ``. - +`` from both ends so ``aws.`` ≡ ``aws`` for dictionary lookup
        # but ``c++`` and ``node.js`` keep their internal punctuation.
        word = match.lower().strip(".-+")
        if not word or word in _STOPWORDS:
            continue
        canonical = manifest.canonicalise_token(word)
        if canonical in _STOPWORDS or not canonical:
            continue
        tokens.add(canonical)
    return frozenset(tokens)


def stoplist() -> Iterable[str]:
    """Exposed for tests / interactive inspection of the hardcoded stoplist."""
    return _STOPWORDS
