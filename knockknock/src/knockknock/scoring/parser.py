"""Strict JSON parser for the Gemini scoring response.

The score stage hands raw model output to :func:`parse_score` and expects
either a typed :class:`JobScore` or a :class:`ValueError`. Failure modes
we deliberately tolerate (because real Gemini JSON-mode output exhibits
them) are:

- Surrounding whitespace
- Markdown code fences (``` ```json ... ``` ``` even though we asked for
  ``response_mime_type="application/json"``)
- Missing optional keys (``matched_*``, ``*_match``) -- defaulted

Failure modes we reject (because we'd persist garbage otherwise):

- Non-JSON bodies
- JSON that isn't an object at the top level
- Missing ``score`` key
- ``score`` that can't be coerced to ``int``

The integer score is clamped to ``[1, 10]`` rather than raising: a model
that returns ``11`` or ``0`` is technically out-of-rubric but its intent
is unambiguous, and clamping keeps the downstream threshold comparison
simple. The decision to never reject the row over a clamping was made
explicitly -- changing it would also need a migration on the
``job_applications.score`` column's check constraint (currently absent).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# Tolerant code-fence stripper. We accept either ``` ``` or ``` ```json
# fences and require them to wrap the whole body (we don't try to extract
# JSON embedded inside chat-style prose).
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


@dataclass(frozen=True, slots=True)
class JobScore:
    """Typed view of a Gemini score response.

    ``score`` is clamped to 1..10 inclusive. ``rationale`` is the
    one-sentence explanation persisted to ``job_applications.score_rationale``.
    The ``matched_*`` tuples and ``*_match`` flags are advisory metadata
    the score stage may persist in events or logs but are not part of the
    job application row itself.
    """

    score: int  # 1..10 (clamped)
    rationale: str
    matched_must_have: tuple[str, ...]
    matched_nice_to_have: tuple[str, ...]
    seniority_match: bool
    company_stage_match: bool


def parse_score(raw: str) -> JobScore:
    """Convert a JSON-mode Gemini response into a :class:`JobScore`.

    Raises:
        ValueError: body is not JSON, not an object, missing ``score``,
            or has a non-integer ``score``.
    """
    stripped = raw.strip()
    fence_match = _FENCE_RE.match(stripped)
    if fence_match:
        stripped = fence_match.group(1).strip()

    try:
        data: Any = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse Gemini score response as JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Score payload must be a JSON object.")
    if "score" not in data:
        raise ValueError("Score payload missing required 'score' field.")

    score_raw = data["score"]
    if isinstance(score_raw, bool):  # bool is a subclass of int; reject explicitly.
        raise ValueError(f"score field must be an integer, got bool: {score_raw!r}")
    try:
        score_int = int(score_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"score field is not an integer: {score_raw!r}") from exc

    # Clamp to the rubric range. Out-of-range is a soft signal of model
    # drift; we log it via the upstream caller, not here.
    score_int = max(1, min(10, score_int))

    return JobScore(
        score=score_int,
        rationale=str(data.get("rationale", "")).strip(),
        matched_must_have=_str_tuple(data.get("matched_must_have")),
        matched_nice_to_have=_str_tuple(data.get("matched_nice_to_have")),
        seniority_match=bool(data.get("seniority_match", False)),
        company_stage_match=bool(data.get("company_stage_match", False)),
    )


def _str_tuple(value: Any) -> tuple[str, ...]:
    """Coerce a JSON list-or-missing into a tuple of strings."""
    if value is None:
        return ()
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)
