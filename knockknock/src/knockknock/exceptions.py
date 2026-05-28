"""Top-level exception hierarchy."""

from __future__ import annotations


class KnockknockError(Exception):
    """Base for all Knockknock-raised errors."""


class ConfigError(KnockknockError):
    """Raised when configuration is missing or malformed."""


class ExternalServiceError(KnockknockError):
    """Raised when an external API call ultimately fails after retries."""


class RateLimitedError(ExternalServiceError):
    """Raised when an upstream provider returns a hard rate-limit response."""


class RpdExhaustedError(RateLimitedError):
    """Raised when the daily request budget for a Gemini model is exhausted.

    Distinct from a transient 429 because it represents a budget the
    pipeline must respect for the remainder of the UTC day (model RPD or
    Pro-draft soft cap).
    """


class SafetyCeilingError(RateLimitedError):
    """Raised when the cross-model daily safety ceiling has been hit.

    Independent of any single model's RPD -- this is the operator-defined
    "do not exceed N Gemini calls in a UTC day, period" guard rail set by
    ``job_preferences.limits.gemini_pro_rpd_ceiling``.
    """


class PipelineError(KnockknockError):
    """Raised when the pipeline cannot progress a job for a known reason."""
