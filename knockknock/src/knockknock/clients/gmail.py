"""Gmail API wrapper: create + send drafts via OAuth refresh-token flow.

The client exposes a deliberately small surface:

- :meth:`GmailClient.create_draft` -- build an RFC 2822 MIME message,
  base64url-encode it, POST it to ``users().drafts().create``, return
  the Gmail draft id.
- :meth:`GmailClient.send_draft` -- POST the draft id to
  ``users().drafts().send``, return the resulting message id.

That is the whole feature surface that DraftStage (Task 8.3) and the
Telegram bot (Phase 9) need. Reply tracking is explicitly out of scope.

Design choices, project-specific:

- Single ``body: str`` payload, ``text/plain`` only. Gmail renders the
  signature and any HTML the user has configured server-side; emitting
  a Pro-generated HTML alternative would double-stamp the message.
- ``to_emails`` / ``cc_emails`` are *lists* matching the JSONB columns
  on :class:`~knockknock.db.models.EmailDraft`. RFC 2822 headers can
  carry multiple addresses comma-joined, which is what most Gmail
  clients render correctly. Phase 6's contract (founder hit → To, Cc
  the careers list; no founder → To the careers list) drops in
  unchanged.
- ``ExternalServiceError`` wraps everything that crosses the network
  boundary so callers (DraftStage, the digest job) have a single
  failure mode to inspect.
- :func:`build_gmail_service` is imported lazily so unit tests don't
  need ``google-api-python-client`` at import time and we keep cold
  start fast for ``--once`` runs.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Protocol

import structlog

from knockknock.exceptions import ExternalServiceError

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class GmailDraftSpec:
    """All the data needed to materialise a Gmail draft.

    Attributes are deliberately concrete (no pydantic) because the
    spec is constructed at one site (``DraftStage``) and consumed at
    one site (``GmailClient``); the validation we need is the
    ``to_emails`` non-empty check below, not full schema validation.
    """

    to_emails: list[str]
    subject: str
    body: str
    attachments: list[tuple[str, bytes, str]]  # (filename, bytes, mime_type)
    cc_emails: list[str] | None = None
    reply_to: str | None = None

    def __post_init__(self) -> None:
        if not self.to_emails:
            raise ValueError("GmailDraftSpec.to_emails must be non-empty")


class _GmailService(Protocol):
    """The subset of ``googleapiclient.discovery.Resource`` we depend on.

    Defining a Protocol means our tests can substitute a stub without
    pulling in ``google-api-python-client`` and means mypy still
    catches drift between the stub and the real shape.
    """

    def users(self) -> Any: ...


@dataclass(slots=True)
class GmailClient:
    service: _GmailService
    sender_email: str

    def create_draft(self, spec: GmailDraftSpec) -> str:
        """Create a Gmail draft from ``spec``; return its draft id.

        Raises :class:`ExternalServiceError` on any Gmail-side failure
        or if the response is missing the ``id`` field.
        """
        raw = _build_mime_raw(spec, sender_email=self.sender_email)
        body = {"message": {"raw": raw}}
        try:
            response = self.service.users().drafts().create(userId="me", body=body).execute()
        except Exception as exc:
            # googleapiclient raises a wide range of HttpError / refresh /
            # transport exceptions; wrapping at this boundary gives callers
            # a single failure mode to handle.
            raise ExternalServiceError(f"Gmail draft create failed: {exc}") from exc
        draft_id = response.get("id")
        if not draft_id:
            raise ExternalServiceError(f"Gmail draft create response missing 'id': {response!r}")
        log.debug(
            "gmail.draft_created",
            draft_id=draft_id,
            to_emails=spec.to_emails,
            cc_emails=spec.cc_emails,
        )
        return str(draft_id)

    def send_draft(self, draft_id: str) -> str:
        """Send a previously-created draft; return the Gmail message id."""
        try:
            response = (
                self.service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
            )
        except Exception as exc:
            raise ExternalServiceError(f"Gmail draft send failed: {exc}") from exc
        message_id = response.get("id")
        if not message_id:
            raise ExternalServiceError(f"Gmail send response missing 'id': {response!r}")
        log.info("gmail.draft_sent", draft_id=draft_id, message_id=message_id)
        return str(message_id)


def _build_mime_raw(spec: GmailDraftSpec, *, sender_email: str) -> str:
    """Build an RFC 2822 MIME message and base64url-encode for Gmail.

    Structure:

    - When there are attachments: ``multipart/mixed`` containing the
      ``text/plain`` body followed by the attachment parts.
    - When there are no attachments: a plain ``text/plain`` message.

    We never emit ``text/html`` -- Gmail handles HTML rendering and
    the server-side signature for the sender's account.
    """
    plain = MIMEText(spec.body, "plain", "utf-8")

    if not spec.attachments:
        container: MIMEMultipart | MIMEText = plain
    else:
        mixed = MIMEMultipart("mixed")
        mixed.attach(plain)
        for filename, payload, mime_type in spec.attachments:
            parts = [*mime_type.split("/", 1), "octet-stream"][:2]
            maintype, subtype = parts[0], parts[1]
            if maintype == "application":
                attachment = MIMEApplication(payload, _subtype=subtype, name=filename)
            else:
                # Fall back: treat as octet-stream rather than crashing on
                # an unexpected MIME type.
                attachment = MIMEApplication(payload, _subtype="octet-stream", name=filename)
            attachment.add_header("Content-Disposition", "attachment", filename=filename)
            mixed.attach(attachment)
        container = mixed

    container["To"] = ", ".join(spec.to_emails)
    container["From"] = sender_email
    container["Subject"] = spec.subject
    if spec.cc_emails:
        container["Cc"] = ", ".join(spec.cc_emails)
    if spec.reply_to:
        container["Reply-To"] = spec.reply_to

    return base64.urlsafe_b64encode(container.as_bytes()).decode("ascii")


def build_gmail_service(*, client_id: str, client_secret: str, refresh_token: str) -> _GmailService:
    """Construct an authorised Gmail Resource via the refresh-token flow.

    Imported lazily so unit tests for callers (and for this module's
    stubbed-service path) don't need ``google-api-python-client``,
    ``google-auth``, or ``google-auth-oauthlib`` to be importable at
    test-collection time.
    """
    # Imports are deliberately inside the function body. The google-*
    # packages ship without ``py.typed`` markers, so we cross the
    # untyped boundary here and re-emit our own narrow ``_GmailService``
    # type for the rest of the codebase.
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build  # type: ignore[import-untyped]

    creds = Credentials(  # type: ignore[no-untyped-call]
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",  # noqa: S106 - public OAuth endpoint
        client_id=client_id,
        client_secret=client_secret,
        scopes=[
            "https://www.googleapis.com/auth/gmail.compose",
            "https://www.googleapis.com/auth/gmail.send",
        ],
    )
    creds.refresh(Request())  # type: ignore[no-untyped-call]
    service: _GmailService = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return service
