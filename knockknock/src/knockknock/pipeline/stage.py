"""Stage protocol and shared context types.

Every pipeline stage takes a :class:`StageContext` and returns a
:class:`StageResult` with simple per-stage counters. Stages are intentionally
plain protocol objects so they're easy to mock in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StageContext:
    """Per-run context shared across stages."""

    run_id: int


@dataclass(frozen=True, slots=True)
class StageResult:
    """Counters a stage reports back to the runner."""

    stage: str
    processed: int
    advanced: int
    rejected: int
    errors: int


class Stage(Protocol):
    """A unit of work in the pipeline."""

    name: str

    def run(self, ctx: StageContext) -> StageResult: ...
