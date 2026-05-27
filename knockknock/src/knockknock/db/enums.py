"""Postgres enum values mirrored as Python StrEnums.

Names and members are the authoritative source-of-truth: the Alembic
migration's enum tuples and the SQLModel column types both reference these.
Errata E.2 in docs/superpowers/plans/2026-05-28-knockknock/00-index.md
overrides earlier drafts -- the values here are final.
"""

from __future__ import annotations

from enum import StrEnum


class JobSource(StrEnum):
    HN = "HN"
    WELLFOUND = "WELLFOUND"
    YC_WAAS = "YC_WAAS"
    GREENHOUSE = "GREENHOUSE"
    LEVER = "LEVER"
    ASHBY = "ASHBY"


class CompanySizeBucket(StrEnum):
    SEED = "SEED"
    SERIES_A = "SERIES_A"
    SERIES_B = "SERIES_B"
    SERIES_C = "SERIES_C"
    LATE_STAGE = "LATE_STAGE"
    UNKNOWN = "UNKNOWN"


class PhonebookSource(StrEnum):
    APOLLO = "APOLLO"
    HUNTER = "HUNTER"
    PATTERN_GUESS = "PATTERN_GUESS"
    MANUAL = "MANUAL"
    SEED = "SEED"
    CACHE = "CACHE"


class JobStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    PRE_FILTERED = "PRE_FILTERED"
    PRE_FILTER_REJECTED = "PRE_FILTER_REJECTED"
    SCORED = "SCORED"
    SCORE_REJECTED = "SCORE_REJECTED"
    ENRICHED = "ENRICHED"
    ENRICH_FAILED = "ENRICH_FAILED"
    TAILORED = "TAILORED"
    DRAFTED = "DRAFTED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    SENT = "SENT"
    USER_REJECTED = "USER_REJECTED"
    ERROR = "ERROR"


class RejectionReason(StrEnum):
    BLACKLISTED = "BLACKLISTED"
    LOCATION_MISMATCH = "LOCATION_MISMATCH"
    ROLE_MISMATCH = "ROLE_MISMATCH"
    SENIORITY_MISMATCH = "SENIORITY_MISMATCH"
    SCORE_LOW = "SCORE_LOW"
    NO_EMAIL_FOUND = "NO_EMAIL_FOUND"
    USER_REJECTED = "USER_REJECTED"
    MAX_RETRIES = "MAX_RETRIES"
    OTHER = "OTHER"


class PipelineStage(StrEnum):
    DISCOVER = "DISCOVER"
    PRE_FILTER = "PRE_FILTER"
    SCORE = "SCORE"
    ENRICH = "ENRICH"
    TAILOR = "TAILOR"
    DRAFT = "DRAFT"
    NOTIFY = "NOTIFY"


class PipelineRunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class EmailDraftState(StrEnum):
    GENERATED = "GENERATED"
    DRAFT_CREATED = "DRAFT_CREATED"
    SENT = "SENT"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"


class GeminiModel(StrEnum):
    FLASH_2_5 = "gemini-2.5-flash"
    PRO_2_5 = "gemini-2.5-pro"


class GeminiPurpose(StrEnum):
    SCORE = "SCORE"
    DRAFT_EMAIL = "DRAFT_EMAIL"
    REGENERATE = "REGENERATE"


class TelegramDirection(StrEnum):
    OUTGOING = "OUTGOING"
    INCOMING = "INCOMING"


class TelegramKind(StrEnum):
    DRAFT_PREVIEW = "DRAFT_PREVIEW"
    APPROVAL = "APPROVAL"
    REJECTION = "REJECTION"
    COMMAND = "COMMAND"
    DIGEST = "DIGEST"
    ALERT = "ALERT"
