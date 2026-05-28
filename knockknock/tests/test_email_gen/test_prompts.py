"""Unit tests for :mod:`knockknock.email_gen.prompts`.

These tests pin the *contract* of the Pro prompt without coupling to its
wording too tightly:

- The system instruction must forbid AI self-disclosure, square-bracket
  placeholders, and -- crucially for this project -- any sign-off block
  (Gmail appends its own signature; a Pro-generated one would
  double-stamp the email).
- ``build_draft_prompt`` must include the deterministic context the
  validator later checks (recipient name, role, company, apply URL,
  candidate name + role + pitch).
- ``build_regenerate_prompt`` must feed the previous output and failure
  reason back to Pro so the rewrite is targeted, not a blind retry.
- Long job descriptions must be truncated so we stay well clear of
  Pro's 32k TPM ceiling.
"""

from __future__ import annotations

from pathlib import Path

from knockknock.config.preferences import load_preferences
from knockknock.email_gen.prompts import (
    SYSTEM_INSTRUCTION,
    build_draft_prompt,
    build_regenerate_prompt,
)


def _prefs():
    return load_preferences(Path("config/job_preferences.yaml"))


def test_system_instruction_forbids_placeholders_and_signoff() -> None:
    """The system rules are the safety floor; they must call out the
    three things the validator can catch only after the fact:
    bracketed placeholders, AI self-disclosure, and generated sign-offs.
    """
    lowered = SYSTEM_INSTRUCTION.lower()
    assert "placeholder" in lowered
    # AI disclosure prohibition.
    assert "ai" in lowered or "language model" in lowered
    # Sign-off prohibition -- this is the project-specific constraint
    # because Gmail attaches a signature automatically.
    assert "sign-off" in lowered or "signature" in lowered or "sign off" in lowered


def test_build_draft_prompt_includes_recipient_role_and_candidate() -> None:
    prefs = _prefs()
    prompt = build_draft_prompt(
        prefs,
        recipient_name="Aarav Singh",
        recipient_email="aarav@acme.io",
        company_name="Acme",
        role_title="Senior Backend Engineer",
        role_description="Build distributed Python services on AWS.",
        apply_url="https://acme.io/jobs/1",
    )
    # Recipient context.
    assert "Aarav Singh" in prompt
    assert "Acme" in prompt
    assert "Senior Backend Engineer" in prompt
    assert "https://acme.io/jobs/1" in prompt
    # Candidate context -- note ``name`` (not ``full_name``).
    assert prefs.candidate.name in prompt
    assert prefs.candidate.current_role in prompt
    # Pitch is optional; when set in config it should surface.
    if prefs.candidate.pitch is not None:
        assert prefs.candidate.pitch in prompt


def test_build_draft_prompt_falls_back_when_pitch_missing() -> None:
    """``pitch`` is optional in the YAML; the prompt must not crash and
    must still produce a usable candidate blurb."""
    prefs = _prefs()
    # Mutate via model_copy to drop pitch without touching the YAML.
    candidate_no_pitch = prefs.candidate.model_copy(update={"pitch": None})
    prefs_no_pitch = prefs.model_copy(update={"candidate": candidate_no_pitch})

    prompt = build_draft_prompt(
        prefs_no_pitch,
        recipient_name="A",
        recipient_email="a@a.com",
        company_name="Acme",
        role_title="Backend Engineer",
        role_description="d",
        apply_url="https://a.com/j",
    )
    # Fallback uses current_role + years_experience so the model still
    # has a one-line elevator pitch surface.
    assert prefs_no_pitch.candidate.current_role in prompt
    assert str(prefs_no_pitch.candidate.years_experience) in prompt


def test_build_draft_prompt_truncates_long_description() -> None:
    """Pro charges per token; a 50k-char JD would blow past the TPM
    ceiling. Truncation keeps the final prompt below 25k chars."""
    prefs = _prefs()
    big = "X" * 50_000
    prompt = build_draft_prompt(
        prefs,
        recipient_name="A",
        recipient_email="a@a.com",
        company_name="Acme",
        role_title="r",
        role_description=big,
        apply_url="https://a.com/j",
    )
    assert len(prompt) < 25_000


def test_build_draft_prompt_uses_neutral_greeting_when_recipient_name_blank() -> None:
    """Phase 6 emits empty ``recipient_name`` when only ``careers_email``
    is set (no founder identified). The prompt must steer Pro to a
    neutral greeting (``Hi {company} team``) rather than fabricating
    a name."""
    prefs = _prefs()
    prompt = build_draft_prompt(
        prefs,
        recipient_name="",
        recipient_email="careers@acme.io",
        company_name="Acme",
        role_title="Backend Engineer",
        role_description="d",
        apply_url="https://a.com/j",
    )
    # The prompt should give Pro the company name as the fallback hook.
    assert "Acme" in prompt
    # No fabricated "Hi <name>" hint.
    assert "Aarav" not in prompt


def test_build_regenerate_prompt_includes_previous_draft_and_reason() -> None:
    """The whole point of regeneration is targeted self-correction;
    the previous output and the rejection reason must both appear so
    Pro can fix the *specific* issue rather than randomly resampling."""
    prefs = _prefs()
    prompt = build_regenerate_prompt(
        prefs,
        recipient_name="A",
        recipient_email="a@a.com",
        company_name="Acme",
        role_title="r",
        role_description="d",
        apply_url="https://a.com/j",
        previous_subject="prev sub",
        previous_body="prev body containing [Your Name]",
        reason="Body contained [Your Name] placeholder.",
    )
    assert "prev sub" in prompt
    assert "prev body containing [Your Name]" in prompt
    assert "[Your Name]" in prompt
    assert "placeholder" in prompt.lower() or "reason" in prompt.lower()
