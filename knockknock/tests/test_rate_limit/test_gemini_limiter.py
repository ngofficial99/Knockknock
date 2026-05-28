"""Unit tests for :class:`GeminiLimiter`.

These exercise quota math against a real Postgres session (transactional
rollback per test). Time is frozen so we can deterministically construct
"calls today" rows relative to a known UTC instant.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from freezegun import freeze_time
from sqlmodel import Session

from knockknock.db.enums import GeminiModel, GeminiPurpose
from knockknock.db.models import GeminiCallLog
from knockknock.exceptions import RpdExhaustedError, SafetyCeilingError
from knockknock.rate_limit.gemini_limiter import (
    GeminiLimiter,
    LimiterConfig,
    ModelQuota,
)


def _log(
    session: Session,
    *,
    model: GeminiModel,
    when: datetime,
    success: bool = True,
    purpose: GeminiPurpose = GeminiPurpose.SCORE,
) -> None:
    session.add(
        GeminiCallLog(
            model=model,
            purpose=purpose,
            prompt_tokens=10,
            completion_tokens=20,
            latency_ms=100,
            success=success,
            called_at=when,
        )
    )
    session.flush()


def _config() -> LimiterConfig:
    return LimiterConfig(
        safety_ceiling_rpd=500,
        quotas={
            GeminiModel.FLASH_2_5: ModelQuota(rpm=15, tpm=1_000_000, rpd=1500),
            GeminiModel.PRO_2_5: ModelQuota(rpm=2, tpm=32_000, rpd=50),
        },
        pro_draft_soft_cap=40,
    )


@freeze_time("2026-05-28T12:00:00Z")
def test_check_passes_when_no_usage(db_session: Session) -> None:
    limiter = GeminiLimiter(db_session, _config())
    # Should not raise.
    limiter.check(GeminiModel.FLASH_2_5, GeminiPurpose.SCORE)


@freeze_time("2026-05-28T12:00:00Z")
def test_check_raises_rpd_when_model_rpd_exceeded(db_session: Session) -> None:
    now = datetime.now(UTC)
    # 50 successful Pro calls today exhausts pro RPD.
    for i in range(50):
        _log(
            db_session,
            model=GeminiModel.PRO_2_5,
            when=now - timedelta(minutes=i),
            purpose=GeminiPurpose.DRAFT_EMAIL,
        )
    limiter = GeminiLimiter(db_session, _config())
    with pytest.raises(RpdExhaustedError):
        limiter.check(GeminiModel.PRO_2_5, GeminiPurpose.DRAFT_EMAIL)


@freeze_time("2026-05-28T12:00:00Z")
def test_check_raises_pro_soft_cap_for_drafts(db_session: Session) -> None:
    now = datetime.now(UTC)
    for i in range(40):
        _log(
            db_session,
            model=GeminiModel.PRO_2_5,
            when=now - timedelta(minutes=i),
            purpose=GeminiPurpose.DRAFT_EMAIL,
        )
    limiter = GeminiLimiter(db_session, _config())
    with pytest.raises(RpdExhaustedError):
        limiter.check(GeminiModel.PRO_2_5, GeminiPurpose.DRAFT_EMAIL)
    # Regenerate purpose should still be permitted (uses the 10-call buffer).
    limiter.check(GeminiModel.PRO_2_5, GeminiPurpose.REGENERATE)


@freeze_time("2026-05-28T12:00:00Z")
def test_check_raises_safety_ceiling(db_session: Session) -> None:
    now = datetime.now(UTC)
    # 500 calls (any mix) today hits the safety ceiling.
    for i in range(500):
        _log(db_session, model=GeminiModel.FLASH_2_5, when=now - timedelta(seconds=i))
    limiter = GeminiLimiter(db_session, _config())
    with pytest.raises(SafetyCeilingError):
        limiter.check(GeminiModel.FLASH_2_5, GeminiPurpose.SCORE)


@freeze_time("2026-05-28T12:00:00Z")
def test_check_only_counts_successful_calls_against_rpd(db_session: Session) -> None:
    now = datetime.now(UTC)
    # 60 *failed* Pro calls should NOT exhaust the 50 RPD ceiling.
    for i in range(60):
        _log(
            db_session,
            model=GeminiModel.PRO_2_5,
            when=now - timedelta(minutes=i),
            success=False,
        )
    limiter = GeminiLimiter(db_session, _config())
    limiter.check(GeminiModel.PRO_2_5, GeminiPurpose.DRAFT_EMAIL)


@freeze_time("2026-05-28T12:00:00Z")
def test_check_ignores_yesterdays_calls(db_session: Session) -> None:
    """RPD is a UTC-day budget; calls before today's midnight don't count."""
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    # 500 calls yesterday should NOT trip today's safety ceiling.
    for i in range(500):
        _log(
            db_session,
            model=GeminiModel.FLASH_2_5,
            when=yesterday - timedelta(seconds=i),
        )
    limiter = GeminiLimiter(db_session, _config())
    # No raise.
    limiter.check(GeminiModel.FLASH_2_5, GeminiPurpose.SCORE)


@freeze_time("2026-05-28T12:00:00Z")
def test_compute_rpm_wait_returns_seconds_when_window_full(db_session: Session) -> None:
    now = datetime.now(UTC)
    # 15 Flash calls in the last 60s → next call must wait.
    for i in range(15):
        _log(db_session, model=GeminiModel.FLASH_2_5, when=now - timedelta(seconds=i * 2))
    limiter = GeminiLimiter(db_session, _config())
    wait = limiter.compute_rpm_wait_seconds(GeminiModel.FLASH_2_5)
    assert wait is not None
    assert 0 < wait <= 60


@freeze_time("2026-05-28T12:00:00Z")
def test_compute_rpm_wait_returns_none_when_window_clear(db_session: Session) -> None:
    limiter = GeminiLimiter(db_session, _config())
    assert limiter.compute_rpm_wait_seconds(GeminiModel.FLASH_2_5) is None
