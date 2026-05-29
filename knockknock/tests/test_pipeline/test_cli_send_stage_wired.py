"""Pin that SendStage is part of the ``pipeline run`` stage list.

Why this test exists (Phase 9.8): the bot service flips
``job.status: AWAITING_APPROVAL -> APPROVED`` when the user taps
Approve, but APPROVED rows only become SENT if SendStage runs on the
next pipeline tick. If SendStage is missing from the CLI wiring,
approvals are silent no-ops -- the user taps the button, the bot
acknowledges, and the email is never actually sent.

We could exercise the full ``pipeline run`` here, but that pulls in
Gemini + Hunter + Apollo + Gmail OAuth. Instead we do a structural
check: import the CLI module, read its source, and assert ``SendStage``
appears in the stages-list literal. This is intentionally fragile to
*removal* (the whole point) and intentionally tolerant of *reordering*
(an operator may decide to put SendStage anywhere after DraftStage).
"""

from __future__ import annotations

import inspect

from knockknock import __main__ as cli_main


def test_pipeline_run_wires_send_stage() -> None:
    """``SendStage`` must appear in the ``pipeline run`` command's stages.

    Reads ``pipeline_run`` 's source text and asserts ``SendStage(`` is
    referenced as a stage constructor. The trailing ``(`` excludes the
    bare-import line at the top of the function so we don't pass by
    importing the symbol without actually wiring it in.
    """
    source = inspect.getsource(cli_main.pipeline_run)
    assert "SendStage(" in source, (
        "pipeline run must instantiate SendStage so APPROVED rows from the "
        "Telegram bot get sent on the next pipeline tick. Add "
        "SendStage(session=session, gmail=gmail_client) to the stages list."
    )


def test_pipeline_run_imports_send_stage() -> None:
    """The CLI module's source must import ``SendStage`` from
    ``knockknock.pipeline.send``. Cheap belt-and-suspenders alongside the
    presence check above -- catches the case where someone removes the
    import but leaves a typo'd constructor call.
    """
    source = inspect.getsource(cli_main.pipeline_run)
    assert "from knockknock.pipeline.send import SendStage" in source, (
        "pipeline run must import SendStage from knockknock.pipeline.send"
    )
