"""Deterministic variant selector — tag-overlap + priority tie-break.

Score formula::

    score = |variant.tags ∩ job_tokens| + priority / 1000

Overlap is an integer ≥ 0 and priority is bounded by manifest convention
(≤ ~100), so dividing priority by 1000 keeps it strictly sub-integer.
That means a 2-tag overlap with priority 1 (score ≈ 2.001) **always**
beats a 1-tag overlap with priority 999 (score ≈ 1.999) — overlap
dominates, priority only breaks ties.

If no variant has any overlap with the job tokens (out-of-scope role,
or a JD that didn't tokenise to anything meaningful), the selector
falls back to the **highest-priority** variant rather than returning
``None``. This is the "always pick something" rule documented in
``resumes/README.md``; manifest authors must keep at least one broad
variant.

Stability: when scores tie exactly, the lower manifest index wins. The
same manifest + same tokens always produce the same pick — required so
that pipeline reruns (and tests) are reproducible.
"""

from __future__ import annotations

from knockknock.resume.manifest import ResumeManifest, ResumeVariant

# Divisor must be > max(priority); 1000 is comfortably above the seed
# manifest's 0-100 range. If you raise priority into the hundreds of
# thousands, bump this in lockstep.
_PRIORITY_TIE_BREAK_DENOMINATOR = 1000.0


def select_variant(manifest: ResumeManifest, job_tokens: frozenset[str]) -> ResumeVariant | None:
    """Pick the best resume variant for a job, or ``None`` if manifest empty.

    See module docstring for the scoring contract. Returns ``None`` ONLY
    when ``manifest.variants`` is empty — manifest authors are expected
    to keep at least one broad/generic variant so in-scope jobs always
    receive an assignment.
    """
    if not manifest.variants:
        return None

    scored: list[tuple[float, int, ResumeVariant]] = []
    for idx, variant in enumerate(manifest.variants):
        overlap = len(variant.tags & job_tokens)
        score = overlap + (variant.priority / _PRIORITY_TIE_BREAK_DENOMINATOR)
        scored.append((score, idx, variant))

    # Sort: highest score first; lower manifest index breaks score ties so
    # the result is reproducible across runs.
    scored.sort(key=lambda triple: (-triple[0], triple[1]))
    best_score, _, best_variant = scored[0]

    # If no variant achieved at least 1 full overlap point, the "best" is
    # effectively the highest-priority safe default. Fall through to the
    # explicit highest-priority pick rather than relying on the sort
    # ordering (which would pick the lowest-index variant on score-tie).
    if int(best_score) == 0:
        # Re-sort: highest priority first; lower manifest index breaks ties.
        by_priority = sorted(
            enumerate(manifest.variants),
            key=lambda pair: (-pair[1].priority, pair[0]),
        )
        return by_priority[0][1]

    return best_variant
