"""Single-process Gemini quota enforcer backed by ``gemini_call_logs``.

This is a *pre-call* gatekeeper: every code path that issues a Gemini
request should call :meth:`GeminiLimiter.check` first. The limiter peeks
at the DB-side log of past calls (rather than holding an in-memory token
bucket) so that crashing/restarting the pipeline mid-run picks up the
correct day-of usage. Single-process safety is sufficient -- the
hourly pipeline runs as one Cloud Run Job at a time.

Three independent budgets are enforced, cheapest-to-check first:

1. **Safety ceiling (cross-model RPD)** -- operator-defined "do not exceed
   N total Gemini calls in a UTC day" guard. Source-of-truth:
   ``LimiterConfig.safety_ceiling_rpd``. Raises
   :class:`SafetyCeilingError`.
2. **Per-model RPD** -- per Gemini model daily request budget from
   :class:`ModelQuota`. Raises :class:`RpdExhaustedError`.
3. **Pro draft soft cap** -- carve-out so a flood of ``DRAFT_EMAIL``
   calls cannot starve the daily Pro budget needed for
   ``REGENERATE``. Raises :class:`RpdExhaustedError`.

Only ``success=True`` log rows are counted: failed calls do not consume
budget. The day boundary is **UTC midnight** to match Google's quota
reset semantics.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import asc, func
from sqlmodel import Session, select

from knockknock.db.enums import GeminiModel, GeminiPurpose
from knockknock.db.models import GeminiCallLog
from knockknock.exceptions import RpdExhaustedError, SafetyCeilingError

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ModelQuota:
    """Per-model Gemini quota.

    ``rpm`` and ``tpm`` are advisory limits the limiter uses to *smooth*
    burst traffic with :meth:`GeminiLimiter.compute_rpm_wait_seconds`;
    ``rpd`` is a hard daily cap that raises on breach.
    """

    rpm: int
    tpm: int
    rpd: int


@dataclass(frozen=True, slots=True)
class LimiterConfig:
    """Aggregate Gemini budget config; one instance per pipeline run.

    ``pro_draft_soft_cap`` is the maximum number of ``PRO_2_5`` calls with
    ``purpose=DRAFT_EMAIL`` allowed in a day; remaining headroom under the
    Pro RPD is reserved for ``REGENERATE`` calls that the human approver
    triggers from Telegram.
    """

    safety_ceiling_rpd: int
    quotas: dict[GeminiModel, ModelQuota]
    pro_draft_soft_cap: int = 40


@dataclass(slots=True)
class GeminiLimiter:
    """Pre-call gatekeeper backed by ``gemini_call_logs``.

    Construct one per pipeline run; pass the same session as the score /
    draft stages so reads see the same in-transaction state. Safe for
    single-process use; multi-process callers need an external mutex
    (out of scope).
    """

    session: Session
    config: LimiterConfig
    _now_fn: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    def check(self, model: GeminiModel, purpose: GeminiPurpose) -> None:
        """Raise if making a call right now would breach any budget.

        The checks are ordered safety-ceiling first because it's the
        operator-facing "stop everything" guard and we want the most
        explanatory error to win when multiple limits are exceeded.

        Raises:
            SafetyCeilingError: cross-model daily call ceiling hit.
            RpdExhaustedError: per-model RPD or Pro-draft soft-cap hit.
        """
        now = self._now_fn()
        day_start = _day_start(now)

        total_today = self._count(day_start, model=None, success_only=True)
        if total_today >= self.config.safety_ceiling_rpd:
            log.error(
                "gemini.safety_ceiling_hit",
                total_today=total_today,
                ceiling=self.config.safety_ceiling_rpd,
            )
            raise SafetyCeilingError(
                f"Safety ceiling {self.config.safety_ceiling_rpd} hit ({total_today} calls today)."
            )

        quota = self.config.quotas[model]
        model_today = self._count(day_start, model=model, success_only=True)
        if model_today >= quota.rpd:
            raise RpdExhaustedError(
                f"{model.value} RPD {quota.rpd} exhausted ({model_today} calls today)."
            )

        # Pro draft soft cap protects regenerate headroom.
        if model is GeminiModel.PRO_2_5 and purpose is GeminiPurpose.DRAFT_EMAIL:
            draft_today = self._count_purpose(
                day_start, model=GeminiModel.PRO_2_5, purpose=GeminiPurpose.DRAFT_EMAIL
            )
            if draft_today >= self.config.pro_draft_soft_cap:
                raise RpdExhaustedError(
                    f"Pro draft soft cap {self.config.pro_draft_soft_cap} hit "
                    f"({draft_today} drafts today)."
                )

    def compute_rpm_wait_seconds(self, model: GeminiModel) -> float | None:
        """Return seconds to sleep so the next call respects RPM, or ``None``.

        Reads the trailing 60s of successful calls for the given model. If
        the count is below ``rpm``, returns ``None`` (no wait). Otherwise
        returns ``oldest_in_window + 60s - now`` -- the moment the oldest
        call falls out of the trailing-60s window and frees up a slot.
        """
        now = self._now_fn()
        window_start = now - timedelta(seconds=60)
        quota = self.config.quotas[model]
        stmt = (
            select(GeminiCallLog.called_at)
            .where(GeminiCallLog.model == model)
            .where(GeminiCallLog.called_at >= window_start)
            .where(GeminiCallLog.success == True)  # noqa: E712 -- SQL boolean comparison
            .order_by(asc(GeminiCallLog.called_at))  # type: ignore[arg-type]
        )
        rows = list(self.session.exec(stmt))
        if len(rows) < quota.rpm:
            return None
        oldest = rows[0]
        # Defensive: timestamps may be naive on some test paths.
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=UTC)
        free_at = oldest + timedelta(seconds=60)
        delta = (free_at - now).total_seconds()
        return max(delta, 0.0)

    def _count(
        self,
        since: datetime,
        *,
        model: GeminiModel | None,
        success_only: bool,
    ) -> int:
        stmt = (
            select(func.count()).select_from(GeminiCallLog).where(GeminiCallLog.called_at >= since)
        )
        if model is not None:
            stmt = stmt.where(GeminiCallLog.model == model)
        if success_only:
            stmt = stmt.where(GeminiCallLog.success == True)  # noqa: E712
        return int(self.session.exec(stmt).one())

    def _count_purpose(
        self,
        since: datetime,
        *,
        model: GeminiModel,
        purpose: GeminiPurpose,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(GeminiCallLog)
            .where(GeminiCallLog.called_at >= since)
            .where(GeminiCallLog.model == model)
            .where(GeminiCallLog.purpose == purpose)
            .where(GeminiCallLog.success == True)  # noqa: E712
        )
        return int(self.session.exec(stmt).one())


def _day_start(now: datetime) -> datetime:
    """UTC midnight floor. All Gemini quotas reset on UTC day boundaries."""
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
