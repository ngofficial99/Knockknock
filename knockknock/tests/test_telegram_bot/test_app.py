"""Tests for the FastAPI Telegram webhook app.

The app's job is narrow:

1. Reject any POST without (or with a wrong) ``X-Telegram-Bot-Api-Secret-Token``
   header. 401, no DB work.
2. For valid requests, build a ``HandlerContext`` from a per-request DB
   session + pre-built TelegramClient and call :func:`handle_callback`.
3. Always return 200 (even on :class:`UnauthorizedError` from the handler)
   so Telegram doesn't retry forever -- we trust the handler's authz layer
   and prefer to drop bad updates silently rather than echo a 4xx that
   leaks which axis (chat id / hmac / draft id) failed.
4. ``GET /healthz`` returns 200 always (uvicorn / process-supervisor probe).

Drift from the Phase 9 plan: the plan's app injects a Gmail client into
``HandlerContext`` because the spec's APPROVE path calls Gmail directly.
Our DB-mediated architecture (per the Phase 9 brainstorm) keeps the bot
service "dumb" -- it only flips DB rows. The pipeline's SendStage
(Task 9.5) owns Gmail. So no Gmail dependency here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from knockknock.db.enums import (
    CompanySizeBucket,
    EmailDraftState,
    JobSource,
    JobStatus,
)
from knockknock.db.models import (
    Company,
    EmailDraft,
    JobApplication,
)
from knockknock.telegram_bot.app import AppConfig, build_app
from knockknock.telegram_bot.codec import CallbackAction, encode_callback_data

SECRET = "callback-secret"
WEBHOOK_SECRET = "tg-header-secret"
ADMIN_CHAT_ID = 42


@dataclass
class _StubTg:
    """Minimal TelegramClient stub. The app calls ``answer_callback`` +
    ``edit_card_to_status`` via the handler; ``send_text`` is unused here.
    """

    edits: list[dict[str, Any]] = field(default_factory=list)
    answers: list[dict[str, Any]] = field(default_factory=list)

    async def edit_card_to_status(self, *, message_id: int, status_text: str) -> None:
        self.edits.append({"message_id": message_id, "status_text": status_text})

    async def answer_callback(self, *, query_id: str, text: str) -> None:
        self.answers.append({"query_id": query_id, "text": text})


@pytest.fixture
def seeded_draft(db_session: Session) -> EmailDraft:
    """One DRAFT_CREATED draft + AWAITING_APPROVAL job, ready to approve."""
    company = Company(
        name="AcmeApp",
        domain="acmeapp.test",
        size_bucket=CompanySizeBucket.SERIES_A,
    )
    db_session.add(company)
    db_session.flush()
    job = JobApplication(
        company_id=company.id,
        source=JobSource.HN,
        source_job_id="j-app-1",
        title="Backend Engineer",
        location="Bengaluru",
        apply_url="https://acmeapp.test/jobs/1",
        description="Build distributed things.",
        status=JobStatus.AWAITING_APPROVAL,
        score=8,
        resume_variant_key="backend-distributed",
    )
    db_session.add(job)
    db_session.flush()
    draft = EmailDraft(
        job_id=job.id,
        subject="Senior Backend Engineer -- Nishant Gupta",
        body="Hi team, would love to chat.",
        to_recipients=["careers@acmeapp.test"],
        gmail_draft_id="gd_1",
        state=EmailDraftState.DRAFT_CREATED,
    )
    db_session.add(draft)
    db_session.flush()
    return draft


def _app_with(db_session: Session, tg: _StubTg) -> TestClient:
    """Build a TestClient bound to the per-test transactional session.

    The handler calls ``session.commit()`` inside; with a connection-bound
    Session, that commits to the outer transaction (SAVEPOINT semantics),
    which the conftest fixture rolls back at teardown. Net: assertions
    inside the test see the handler's writes, but the DB state resets
    between tests.
    """

    @contextmanager
    def fake_session_scope() -> Iterator[Session]:
        yield db_session

    config = AppConfig(
        webhook_secret=WEBHOOK_SECRET,
        callback_secret=SECRET,
        admin_chat_id=ADMIN_CHAT_ID,
    )
    app = build_app(
        config=config,
        session_scope=fake_session_scope,
        telegram=tg,
    )
    return TestClient(app)


# ----------------- /healthz -------------------------------------------------


def test_healthz_returns_ok(db_session: Session) -> None:
    client = _app_with(db_session, _StubTg())
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ----------------- header verification --------------------------------------


def test_webhook_requires_header(db_session: Session) -> None:
    """No ``X-Telegram-Bot-Api-Secret-Token`` -> 401, no DB work."""
    client = _app_with(db_session, _StubTg())
    r = client.post("/telegram/webhook", json={"update_id": 1})
    assert r.status_code == 401


def test_webhook_rejects_wrong_header(db_session: Session) -> None:
    client = _app_with(db_session, _StubTg())
    r = client.post(
        "/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert r.status_code == 401


# ----------------- happy path: APPROVE --------------------------------------


def test_webhook_handles_approve(db_session: Session, seeded_draft: EmailDraft) -> None:
    """A valid APPROVE update flips ``job.status = APPROVED`` (no Gmail)."""
    tg = _StubTg()
    client = _app_with(db_session, tg)
    data = encode_callback_data(CallbackAction.APPROVE, draft_id=seeded_draft.id, secret=SECRET)
    update = {
        "update_id": 1,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": ADMIN_CHAT_ID, "is_bot": False, "first_name": "u"},
            "message": {"message_id": 99, "chat": {"id": ADMIN_CHAT_ID}},
            "data": data,
        },
    }
    r = client.post(
        "/telegram/webhook",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    # job moved to APPROVED, SendStage will do the Gmail send later.
    db_session.refresh(seeded_draft)
    job = db_session.exec(
        select(JobApplication).where(JobApplication.id == seeded_draft.job_id)
    ).one()
    assert job.status == JobStatus.APPROVED
    assert seeded_draft.state == EmailDraftState.DRAFT_CREATED  # unchanged
    # TG client got both calls
    assert len(tg.answers) == 1
    assert len(tg.edits) == 1


# ----------------- unauthorized at handler layer ----------------------------


def test_webhook_returns_200_on_unauthorized_callback(
    db_session: Session, seeded_draft: EmailDraft
) -> None:
    """When the handler raises :class:`UnauthorizedError` (wrong chat id,
    bad HMAC, missing draft, ...), the app must still return 200. We don't
    want Telegram to retry forever -- the request was malicious or stale,
    not transient.
    """
    tg = _StubTg()
    client = _app_with(db_session, tg)
    data = encode_callback_data(CallbackAction.APPROVE, draft_id=seeded_draft.id, secret=SECRET)
    update = {
        "update_id": 2,
        "callback_query": {
            "id": "cb-evil",
            "from": {"id": 999, "is_bot": False, "first_name": "evil"},
            "message": {"message_id": 99, "chat": {"id": 999}},  # wrong chat id
            "data": data,
        },
    }
    r = client.post(
        "/telegram/webhook",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
    )
    assert r.status_code == 200
    # No DB mutation
    db_session.refresh(seeded_draft)
    job = db_session.exec(
        select(JobApplication).where(JobApplication.id == seeded_draft.job_id)
    ).one()
    assert job.status == JobStatus.AWAITING_APPROVAL


def test_webhook_returns_200_on_non_callback_update(db_session: Session) -> None:
    """A plain text message (no ``callback_query``) is a no-op -- the
    handler returns None. The app still acks 200.
    """
    client = _app_with(db_session, _StubTg())
    update = {
        "update_id": 3,
        "message": {
            "message_id": 1,
            "chat": {"id": ADMIN_CHAT_ID},
            "text": "hi",
        },
    }
    r = client.post(
        "/telegram/webhook",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
    )
    assert r.status_code == 200
