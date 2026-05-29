from __future__ import annotations

from knockknock.db.enums import (
    CompanySizeBucket,
    EmailDraftState,
    GeminiModel,
    GeminiPurpose,
    JobSource,
    JobStatus,
    PhonebookSource,
    PipelineRunStatus,
    PipelineStage,
    RejectionReason,
    TelegramDirection,
    TelegramKind,
)


def test_all_enums_have_string_values() -> None:
    enums = [
        JobSource,
        CompanySizeBucket,
        PhonebookSource,
        JobStatus,
        RejectionReason,
        PipelineStage,
        PipelineRunStatus,
        EmailDraftState,
        GeminiModel,
        GeminiPurpose,
        TelegramDirection,
        TelegramKind,
    ]
    for enum_cls in enums:
        for member in enum_cls:
            assert isinstance(member.value, str)
            # values are either fully-uppercase identifiers or fully-lowercase
            # (e.g. gemini model IDs like "gemini-2.5-flash").
            assert member.value == member.value.upper() or member.value.islower()


def test_job_status_has_terminal_states() -> None:
    assert JobStatus.SENT in JobStatus
    assert JobStatus.USER_REJECTED in JobStatus
    assert JobStatus.ERROR in JobStatus


def test_job_status_has_approved_member() -> None:
    """Phase 9 (DB-mediated approval flow): APPROVED is the post-tap,
    pre-Gmail-send job status. The user-tap on the Telegram inline button
    flips ``job_applications.status`` from ``AWAITING_APPROVAL`` to
    ``APPROVED``; the pipeline's ``SendStage`` then transitions
    ``APPROVED -> SENT`` after Gmail accepts the send. This supersedes
    Errata E.2's earlier "APPROVED is only a draft state" rule.
    """
    assert JobStatus.APPROVED.value == "APPROVED"
    # APPROVED sits between AWAITING_APPROVAL and SENT in the lifecycle.
    assert JobStatus.AWAITING_APPROVAL in JobStatus
    assert JobStatus.SENT in JobStatus


def test_email_draft_state_members() -> None:
    """Errata E.2: DraftState renamed to EmailDraftState with new members."""
    expected = {"GENERATED", "DRAFT_CREATED", "SENT", "FAILED", "SUPERSEDED"}
    assert set(EmailDraftState.__members__) == expected


def test_rejection_reason_uses_errata_names() -> None:
    """Errata E.2: SCORE_LOW (not SCORE_BELOW_THRESHOLD), NO_EMAIL_FOUND
    (not NO_CONTACT_FOUND), and MAX_RETRIES is present.
    """
    assert RejectionReason.SCORE_LOW.value == "SCORE_LOW"
    assert RejectionReason.NO_EMAIL_FOUND.value == "NO_EMAIL_FOUND"
    assert RejectionReason.MAX_RETRIES.value == "MAX_RETRIES"
    assert "SCORE_BELOW_THRESHOLD" not in RejectionReason.__members__
    assert "NO_CONTACT_FOUND" not in RejectionReason.__members__


def test_company_size_bucket_members() -> None:
    """Errata E.2: SERIES_C and LATE_STAGE (not SERIES_C_PLUS)."""
    expected = {"SEED", "SERIES_A", "SERIES_B", "SERIES_C", "LATE_STAGE", "UNKNOWN"}
    assert set(CompanySizeBucket.__members__) == expected


def test_phonebook_source_members() -> None:
    """Errata E.2: PhonebookSource includes SEED and CACHE."""
    expected = {"APOLLO", "HUNTER", "PATTERN_GUESS", "MANUAL", "SEED", "CACHE"}
    assert set(PhonebookSource.__members__) == expected


def test_gemini_purpose_members() -> None:
    """Errata E.2: SCORE, DRAFT_EMAIL, REGENERATE."""
    assert GeminiPurpose.SCORE.value == "SCORE"
    assert GeminiPurpose.DRAFT_EMAIL.value == "DRAFT_EMAIL"
    assert GeminiPurpose.REGENERATE.value == "REGENERATE"
    assert "DRAFT" not in GeminiPurpose.__members__


def test_telegram_direction_members() -> None:
    """Errata E.2: OUTGOING/INCOMING, not OUTBOUND/INBOUND."""
    expected = {"OUTGOING", "INCOMING"}
    assert set(TelegramDirection.__members__) == expected


def test_telegram_kind_members() -> None:
    """Errata E.2: DRAFT_PREVIEW, APPROVAL, REJECTION, COMMAND, DIGEST, ALERT."""
    expected = {"DRAFT_PREVIEW", "APPROVAL", "REJECTION", "COMMAND", "DIGEST", "ALERT"}
    assert set(TelegramKind.__members__) == expected


def test_gemini_model_values_are_lowercase_ids() -> None:
    assert GeminiModel.FLASH_2_5.value == "gemini-2.5-flash"
    assert GeminiModel.PRO_2_5.value == "gemini-2.5-pro"
