"""Unit tests for :mod:`knockknock.clients.gmail`.

The Gmail client is a thin wrapper over ``googleapiclient.discovery``'s
``users().drafts().create/send`` chain. We test against a stub
``Resource`` that mimics the real builder chain so the tests run
offline and don't need any OAuth setup.

Phase 8 contract drift from the spec, intentional:

- ``GmailDraftSpec`` carries ``to_emails: list[str]`` and
  ``cc_emails: list[str] | None`` (matches the JSONB lists on
  ``EmailDraft``). Phase 6 emits these lists directly.
- Single ``body: str`` -- no separate HTML payload. The MIME message
  is ``text/plain`` only. Gmail attaches the server-side signature to
  the rendered message.
- We still support PDF attachments because the tailored resume rides
  along with the draft.
"""

from __future__ import annotations

import base64
import email
from dataclasses import dataclass, field
from typing import Any

import pytest

from knockknock.clients.gmail import GmailClient, GmailDraftSpec
from knockknock.exceptions import ExternalServiceError


@dataclass
class _StubReq:
    response: dict[str, Any]
    fail: bool = False

    def execute(self) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("gmail boom")
        return self.response


@dataclass
class _StubGmailResource:
    """Mimics ``service.users().drafts().create/send(...).execute()``."""

    create_calls: list[dict[str, Any]] = field(default_factory=list)
    send_calls: list[dict[str, Any]] = field(default_factory=list)
    create_response: dict[str, Any] = field(
        default_factory=lambda: {"id": "draft_123", "message": {"id": "msg_999"}}
    )
    send_response: dict[str, Any] = field(default_factory=lambda: {"id": "msg_999"})
    fail_create: bool = False
    fail_send: bool = False

    def users(self) -> _StubGmailResource:
        return self

    def drafts(self) -> _StubGmailResource:
        return self

    def create(self, *, userId: str, body: dict[str, Any]) -> _StubReq:  # noqa: N803 - Google API name
        self.create_calls.append({"userId": userId, "body": body})
        return _StubReq(self.create_response, fail=self.fail_create)

    def send(self, *, userId: str, body: dict[str, Any]) -> _StubReq:  # noqa: N803
        self.send_calls.append({"userId": userId, "body": body})
        return _StubReq(self.send_response, fail=self.fail_send)


def _decode_raw(create_body: dict[str, Any]) -> email.message.Message:
    """Decode the base64url-encoded MIME blob the client sent to Gmail.

    Gmail expects ``data['message']['raw']`` to be url-safe base64.
    """
    raw_b64 = create_body["message"]["raw"]
    raw_bytes = base64.urlsafe_b64decode(raw_b64.encode("ascii"))
    return email.message_from_bytes(raw_bytes)


def _draft_spec(**overrides: Any) -> GmailDraftSpec:
    base: dict[str, Any] = {
        "to_emails": ["aarav@acme.io"],
        "subject": "Backend role at Acme",
        "body": ("Hi Aarav,\n\nI'd love to talk about the role at Acme.\n\nResume attached."),
        "attachments": [("resume.pdf", b"%PDF-1.4 stub", "application/pdf")],
    }
    base.update(overrides)
    return GmailDraftSpec(**base)


def test_create_draft_returns_gmail_draft_id() -> None:
    stub = _StubGmailResource()
    client = GmailClient(service=stub, sender_email="me@example.com")

    draft_id = client.create_draft(_draft_spec())

    assert draft_id == "draft_123"
    assert len(stub.create_calls) == 1
    call = stub.create_calls[0]
    assert call["userId"] == "me"
    raw_b64 = call["body"]["message"]["raw"]
    assert isinstance(raw_b64, str) and raw_b64
    # urlsafe_b64encode produces only [-A-Za-z0-9_=] -- no '+' or '/'.
    assert "+" not in raw_b64 and "/" not in raw_b64


def test_create_draft_sets_to_from_subject_headers() -> None:
    """Headers must round-trip through MIME parsing."""
    stub = _StubGmailResource()
    client = GmailClient(service=stub, sender_email="me@example.com")
    client.create_draft(_draft_spec())

    msg = _decode_raw(stub.create_calls[0]["body"])
    assert msg["From"] == "me@example.com"
    assert msg["To"] == "aarav@acme.io"
    assert msg["Subject"] == "Backend role at Acme"
    # Cc absent when no cc_emails supplied.
    assert msg["Cc"] is None


def test_create_draft_joins_multiple_to_and_cc_recipients() -> None:
    """Phase 6 recipient contract: founder hit → To=founder,
    Cc=careers list split on '", "'. Multiple addresses are
    comma-joined into a single RFC2822 header value."""
    stub = _StubGmailResource()
    client = GmailClient(service=stub, sender_email="me@example.com")
    client.create_draft(
        _draft_spec(
            to_emails=["aarav@acme.io"],
            cc_emails=["careers@acme.io", "hr@acme.io"],
        )
    )

    msg = _decode_raw(stub.create_calls[0]["body"])
    assert msg["To"] == "aarav@acme.io"
    assert msg["Cc"] == "careers@acme.io, hr@acme.io"


def test_create_draft_carries_plain_text_body_no_html() -> None:
    """We emit text/plain only -- Gmail handles HTML rendering and the
    signature. No multipart/alternative."""
    stub = _StubGmailResource()
    client = GmailClient(service=stub, sender_email="me@example.com")
    client.create_draft(_draft_spec())

    msg = _decode_raw(stub.create_calls[0]["body"])
    # mixed wraps the plain body + the PDF attachment.
    assert msg.get_content_type() == "multipart/mixed"
    body_parts = [p for p in msg.walk() if p.get_content_type() == "text/plain"]
    assert len(body_parts) == 1
    payload = body_parts[0].get_payload(decode=True)
    assert isinstance(payload, bytes)
    decoded = payload.decode("utf-8")
    assert "Hi Aarav," in decoded
    # No alternative HTML part.
    assert not any(p.get_content_type() == "text/html" for p in msg.walk())


def test_create_draft_attaches_resume_pdf() -> None:
    stub = _StubGmailResource()
    client = GmailClient(service=stub, sender_email="me@example.com")
    client.create_draft(_draft_spec())

    msg = _decode_raw(stub.create_calls[0]["body"])
    pdf_parts = [p for p in msg.walk() if p.get_filename() == "resume.pdf"]
    assert len(pdf_parts) == 1
    pdf = pdf_parts[0]
    assert pdf.get_content_type() == "application/pdf"
    assert pdf.get_payload(decode=True) == b"%PDF-1.4 stub"


def test_create_draft_works_with_no_attachments() -> None:
    """Phase 9 might create drafts without a resume (e.g. follow-ups)."""
    stub = _StubGmailResource()
    client = GmailClient(service=stub, sender_email="me@example.com")
    client.create_draft(_draft_spec(attachments=[]))
    msg = _decode_raw(stub.create_calls[0]["body"])
    assert msg.get_content_type() in {"text/plain", "multipart/mixed"}
    # No PDF parts.
    assert not [p for p in msg.walk() if p.get_filename()]


def test_create_draft_wraps_exceptions_as_external_service_error() -> None:
    stub = _StubGmailResource(fail_create=True)
    client = GmailClient(service=stub, sender_email="me@example.com")
    with pytest.raises(ExternalServiceError, match="draft"):
        client.create_draft(_draft_spec())


def test_create_draft_raises_when_response_missing_id() -> None:
    stub = _StubGmailResource(create_response={"message": {"id": "msg_999"}})
    client = GmailClient(service=stub, sender_email="me@example.com")
    with pytest.raises(ExternalServiceError, match="id"):
        client.create_draft(_draft_spec())


def test_send_draft_returns_message_id() -> None:
    stub = _StubGmailResource()
    client = GmailClient(service=stub, sender_email="me@example.com")
    message_id = client.send_draft("draft_123")
    assert message_id == "msg_999"
    assert stub.send_calls[0]["body"] == {"id": "draft_123"}
    assert stub.send_calls[0]["userId"] == "me"


def test_send_draft_wraps_exceptions_as_external_service_error() -> None:
    stub = _StubGmailResource(fail_send=True)
    client = GmailClient(service=stub, sender_email="me@example.com")
    with pytest.raises(ExternalServiceError, match="send"):
        client.send_draft("draft_x")


def test_send_draft_raises_when_response_missing_id() -> None:
    stub = _StubGmailResource(send_response={})
    client = GmailClient(service=stub, sender_email="me@example.com")
    with pytest.raises(ExternalServiceError, match="id"):
        client.send_draft("draft_x")


def test_gmail_draft_spec_rejects_empty_to_emails() -> None:
    """A draft with no recipients makes no sense -- catch it at
    construction time rather than letting Gmail reject the API call."""
    with pytest.raises(ValueError, match="to_emails"):
        GmailDraftSpec(
            to_emails=[],
            subject="x",
            body="x" * 250,
            attachments=[],
        )
