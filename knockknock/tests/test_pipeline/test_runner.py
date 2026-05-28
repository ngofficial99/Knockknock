from __future__ import annotations

from dataclasses import dataclass

from knockknock.pipeline.runner import PipelineRunner
from knockknock.pipeline.stage import Stage, StageContext, StageResult


@dataclass
class _FakeStage:
    name: str
    counter: list[str]

    def run(self, ctx: StageContext) -> StageResult:
        self.counter.append(self.name)
        return StageResult(stage=self.name, processed=1, advanced=1, rejected=0, errors=0)


def test_runner_executes_stages_in_order() -> None:
    calls: list[str] = []
    stages: list[Stage] = [
        _FakeStage("discover", calls),
        _FakeStage("pre_filter", calls),
    ]
    runner = PipelineRunner(stages=stages)
    summary = runner.run_once(StageContext(run_id=1))
    assert calls == ["discover", "pre_filter"]
    assert len(summary.results) == 2
    assert summary.results[0].processed == 1


def test_runner_continues_on_stage_error() -> None:
    @dataclass
    class _BoomStage:
        name: str = "boom"

        def run(self, ctx: StageContext) -> StageResult:
            raise RuntimeError("kaboom")

    calls: list[str] = []
    stages: list[Stage] = [_BoomStage(), _FakeStage("after", calls)]
    runner = PipelineRunner(stages=stages)
    summary = runner.run_once(StageContext(run_id=2))
    assert summary.results[0].errors == 1
    assert "after" in calls
