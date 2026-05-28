"""Unit tests for :mod:`knockknock.email_gen.validator`.

The validator is the *safety net* for prompt-injection from job
descriptions and Pro hallucinations. It runs after every Pro call;
on failure, Phase 8 gets one regeneration attempt with the error fed
back; a second failure halts the row.

Project-specific contract drift vs. the original Phase 8 spec:

- ``EmailDraft.body`` is a single field (no separate ``body_html``);
  Pro therefore emits ``{"subject", "body"}``.
- Gmail attaches the signature server-side, so the body must NOT
  contain the candidate's email address. Any email in the body is a
  rejection-worthy hallucination.
- The candidate name is no longer required in the body (the signature
  carries the sign-off). The recipient-greeting personalisation check
  still matters and is enforced when ``recipient_first_name`` is set.
"""

from __future__ import annotations

from typing import Any

import pytest

from knockknock.email_gen.validator import (
    DraftOutput,
    ValidationError,
    parse_draft_response,
    validate_draft,
)


def _draft(**overrides: Any) -> DraftOutput:
    base: dict[str, Any] = {
        "subject": "Re: Backend role at Acme",
        # 200+ chars, no email in body, no sign-off (Gmail handles it).
        "body": (
            "Hi Aarav,\n\n"
            "I came across the Senior Backend Engineer role at Acme and your "
            "post about scaling the ingestion pipeline. The work mirrors what "
            "I do at Zeotap on reactive Vert.x services -- happy to share more "
            "if useful.\n\n"
            "Resume attached; would love a quick chat."
        ),
    }
    base.update(overrides)
    return DraftOutput(**base)


def _context(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "candidate_name": "Nishant Gupta",
        "candidate_email": "ngofficial99@gmail.com",
        "apply_url": "https://acme.io/jobs/1",
        "company_name": "Acme",
        "recipient_first_name": "Aarav",
    }
    base.update(overrides)
    return base


def test_validate_accepts_good_draft() -> None:
    validate_draft(_draft(), **_context())


def test_validate_rejects_placeholder_brackets_in_body() -> None:
    bad = _draft(
        body=(
            "Hi [Recipient], I'd love to chat about your team's work on distributed systems. " * 4
        )
    )
    with pytest.raises(ValidationError, match="placeholder"):
        validate_draft(bad, **_context())


def test_validate_rejects_placeholder_brackets_in_subject() -> None:
    bad = _draft(subject="Re: [Role] at [Company]")
    with pytest.raises(ValidationError, match="placeholder"):
        validate_draft(bad, **_context())


def test_validate_rejects_ai_disclosure_patterns() -> None:
    bad = _draft(
        body=("Hi Aarav,\n\nAs an AI language model, I'd love to talk about the role. " * 4)
    )
    with pytest.raises(ValidationError, match="AI"):
        validate_draft(bad, **_context())


def test_validate_rejects_unknown_url() -> None:
    bad = _draft(
        body=(
            "Hi Aarav,\n\nSee my portfolio at https://evil.com/x for relevant "
            "work on reactive backends. " * 3
        )
    )
    with pytest.raises(ValidationError, match="URL"):
        validate_draft(bad, **_context())


def test_validate_accepts_apply_url_in_body() -> None:
    """The configured apply_url is the one URL we allow in the body --
    Pro is told it may reference it once."""
    good = _draft(
        body=(
            "Hi Aarav,\n\nI saw https://acme.io/jobs/1 and the work on "
            "distributed ingestion lined up well with what I do at Zeotap "
            "on reactive Vert.x services -- happy to share more if useful.\n\n"
            "Resume attached; would love a quick chat about the team."
        )
    )
    validate_draft(good, **_context())


def test_validate_rejects_any_email_in_body() -> None:
    """Gmail attaches the signature; ANY email in the body is a
    hallucination or a prompt-injection attempt."""
    bad = _draft(
        body=(
            "Hi Aarav,\n\nReply to me at attacker@evil.com about the role -- "
            "happy to chat about your distributed-systems work. " * 2
        )
    )
    with pytest.raises(ValidationError, match="email"):
        validate_draft(bad, **_context())


def test_validate_rejects_candidate_email_in_body() -> None:
    """Even the *candidate's own* email in the body is wrong -- it would
    duplicate the Gmail-managed signature."""
    bad = _draft(
        body=(
            "Hi Aarav,\n\nReach me at ngofficial99@gmail.com about the role "
            "and the distributed work at Acme on reactive backends. "
            "I'd love to share more context on Vert.x and the platform "
            "team's work on multi-tenant ingestion pipelines."
        )
    )
    with pytest.raises(ValidationError, match="email"):
        validate_draft(bad, **_context())


def test_validate_rejects_empty_subject() -> None:
    bad = _draft(subject="   ")
    with pytest.raises(ValidationError, match="subject"):
        validate_draft(bad, **_context())


def test_validate_rejects_body_too_short() -> None:
    bad = _draft(body="Hi Aarav.\n\nResume attached.")
    with pytest.raises(ValidationError, match="short"):
        validate_draft(bad, **_context())


def test_validate_rejects_body_too_long() -> None:
    long_body = (
        "Hi Aarav.\n\n"
        + ("This is a sentence about distributed systems. " * 200)
        + "Resume attached."
    )
    bad = _draft(body=long_body)
    with pytest.raises(ValidationError, match="long"):
        validate_draft(bad, **_context())


def test_validate_rejects_missing_recipient_greeting() -> None:
    """When ``recipient_first_name`` is non-empty, Pro must address
    them by name in the body. Phase 6 contract: founder hit → use
    their name; no founder → first-name is empty and check is skipped."""
    bad = _draft(
        body=(
            "Hi team,\n\nI came across the role at Acme and wanted to share "
            "context on distributed systems work I've shipped at Zeotap on "
            "reactive Vert.x services and multi-tenant ingestion pipelines. "
            "Resume attached; would love a quick chat about the platform."
        )
    )
    with pytest.raises(ValidationError, match="recipient"):
        validate_draft(bad, **_context())


def test_validate_skips_greeting_check_when_recipient_name_blank() -> None:
    """No founder identified → ``recipient_first_name=""`` → no name to
    enforce; a neutral ``Hi Acme team`` body is acceptable."""
    good = _draft(
        body=(
            "Hi Acme team,\n\nI came across the role and wanted to share "
            "context on distributed systems work I've shipped at Zeotap on "
            "reactive Vert.x services and multi-tenant ingestion pipelines. "
            "Resume attached; would love a quick chat about the platform."
        )
    )
    validate_draft(good, **_context(recipient_first_name=""))


def test_parse_draft_response_strips_code_fence() -> None:
    raw = '```json\n{"subject":"S","body":"' + ("B" * 250) + '"}\n```'
    parsed = parse_draft_response(raw)
    assert parsed.subject == "S"
    assert parsed.body.startswith("B")


def test_parse_draft_response_strips_unlabelled_code_fence() -> None:
    raw = '```\n{"subject":"S","body":"' + ("B" * 250) + '"}\n```'
    parsed = parse_draft_response(raw)
    assert parsed.subject == "S"


def test_parse_draft_response_rejects_missing_keys() -> None:
    with pytest.raises(ValueError, match="missing"):
        parse_draft_response('{"subject":"x"}')


def test_parse_draft_response_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="object"):
        parse_draft_response("[1, 2, 3]")


def test_parse_draft_response_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="JSON"):
        parse_draft_response("not even json {")
