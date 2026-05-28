"""Strict validator for Pro draft output.

The validator is the safety net for prompt-injection from scraped job
descriptions and for Pro hallucinations. Phase 8 contract: on failure,
DraftStage gets one regeneration attempt with the error string fed back
as the rejection ``reason``; a second failure halts the row.

Drift vs the original Phase 8 spec, intentional:

- The :class:`~knockknock.db.models.EmailDraft` row has a single
  ``body: str`` column, so :class:`DraftOutput` carries only
  ``subject`` and ``body``.
- Gmail attaches a signature server-side, so the prompt forbids
  generating one. Consequently the validator also rejects ANY email
  address in the body -- including the candidate's own. The signature
  belongs to Gmail, not to Pro.
- The candidate's name is no longer required in the body (the
  signature carries the sign-off). The greeting-personalisation check
  still matters and runs only when ``recipient_first_name`` is set.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_FENCE_RE = re.compile(r"^```(?:[a-zA-Z]+)?\s*(.*?)\s*```$", re.DOTALL)
# Match e.g. ``[Your Name]``, ``[Company]``, ``[Role]`` but tolerate
# arbitrary URLs (which use brackets in IPv6 form, irrelevant here, and
# never in cold emails).
_BRACKETED_PLACEHOLDER_RE = re.compile(r"\[[A-Za-z][^\]\n]{1,40}\]")
_URL_RE = re.compile(r"https?://[^\s)\"'>]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_AI_DISCLOSURE_PATTERNS = (
    r"\bas an (?:ai|artificial intelligence)\b",
    r"\b(?:i am|i'm) (?:an? )?(?:ai|language model|assistant|chatbot)\b",
    r"\blarge language model\b",
)
_AI_DISCLOSURE_RE = re.compile("|".join(_AI_DISCLOSURE_PATTERNS), re.IGNORECASE)

# Sanity floor: anything shorter is a one-liner, not a cold email. The
# good-draft fixture in the tests is ~280 chars so 200 is a safe floor.
_MIN_BODY_CHARS = 200
# Anything longer is a wall of text; Pro is told 90-160 words which is
# typically <1500 chars but we allow some slack.
_MAX_BODY_CHARS = 2200
_MIN_SUBJECT_CHARS = 3
_MAX_SUBJECT_CHARS = 100


class ValidationError(ValueError):
    """Raised when the draft fails any safety/quality rule.

    Inherits from :class:`ValueError` so it cleanly composes with
    pydantic's validation errors and existing ``except ValueError``
    sites, while still being type-narrowable when needed.
    """


@dataclass(frozen=True, slots=True)
class DraftOutput:
    """Parsed Pro draft. Matches the single-field ``EmailDraft.body``
    column in the DB; HTML rendering (if ever needed) is downstream."""

    subject: str
    body: str


def parse_draft_response(raw: str) -> DraftOutput:
    """Parse Pro JSON response. Tolerates ``\u200b```json`` code fences
    because Pro occasionally wraps output in them despite the
    ``response_mime_type="application/json"`` hint.
    """
    stripped = (raw or "").strip()
    fence_match = _FENCE_RE.match(stripped)
    if fence_match:
        stripped = fence_match.group(1).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Draft response not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Draft response must be a JSON object.")
    missing = [k for k in ("subject", "body") if not data.get(k)]
    if missing:
        raise ValueError(f"Draft response missing keys: {missing}")
    return DraftOutput(
        subject=str(data["subject"]).strip(),
        body=str(data["body"]).strip(),
    )


def validate_draft(
    draft: DraftOutput,
    *,
    candidate_name: str,  # noqa: ARG001 - reserved; name now lives in Gmail signature
    candidate_email: str,  # noqa: ARG001 - reserved; email now lives in Gmail signature
    apply_url: str,
    company_name: str,  # noqa: ARG001 - reserved for future content checks
    recipient_first_name: str,
) -> None:
    """Raise :class:`ValidationError` on any quality/safety failure.

    The check order is roughly cheapest → most-context-dependent so the
    most informative error wins for short-circuit-on-first-failure.
    """
    subject = draft.subject.strip()
    body = draft.body.strip()

    # 1. Subject length.
    if not (_MIN_SUBJECT_CHARS <= len(subject) <= _MAX_SUBJECT_CHARS):
        raise ValidationError(
            f"subject length {len(subject)} not in [{_MIN_SUBJECT_CHARS},{_MAX_SUBJECT_CHARS}]"
        )

    # 2. Body length.
    if len(body) < _MIN_BODY_CHARS:
        raise ValidationError(f"body too short ({len(body)} chars, need {_MIN_BODY_CHARS}+)")
    if len(body) > _MAX_BODY_CHARS:
        raise ValidationError(f"body too long ({len(body)} chars, max {_MAX_BODY_CHARS})")

    # 3. Bracketed placeholders.
    if _BRACKETED_PLACEHOLDER_RE.search(subject) or _BRACKETED_PLACEHOLDER_RE.search(body):
        raise ValidationError("draft contains placeholder text inside [brackets]")

    # 4. AI-disclosure patterns.
    if _AI_DISCLOSURE_RE.search(body) or _AI_DISCLOSURE_RE.search(subject):
        raise ValidationError("draft mentions AI/language-model self-disclosure")

    # 5. URL whitelist -- only the apply_url is allowed.
    allowed_urls = {apply_url.strip().rstrip("/")}
    for url in _URL_RE.findall(body):
        if url.rstrip("/") not in allowed_urls:
            raise ValidationError(f"draft contains unapproved URL: {url}")

    # 6. Email whitelist -- NONE allowed in the body. Gmail's signature
    # carries the candidate's address; any email in the body is either
    # a prompt-injection from the scraped JD or a hallucination.
    found_emails = _EMAIL_RE.findall(body)
    if found_emails:
        raise ValidationError(
            f"draft contains email address(es) in body (Gmail signature handles this): "
            f"{sorted({e.lower() for e in found_emails})}"
        )

    # 7. Recipient greeting personalisation -- when Phase 6 found a
    # founder, their first name must appear in the body. When the only
    # contact is ``careers_email`` (no founder), ``recipient_first_name``
    # is empty and this check is skipped (Pro is steered to ``Hi {company}
    # team`` instead).
    if recipient_first_name and recipient_first_name.lower() not in body.lower():
        raise ValidationError(f"draft body missing recipient first name: {recipient_first_name!r}")
