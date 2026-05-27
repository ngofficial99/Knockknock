← [Index](00-index.md) · [Prev: phase-07-resume-tailor.md](phase-07-resume-tailor.md) · [Next: phase-09-telegram.md](phase-09-telegram.md)

## Phase 8: Email Generation, Validator, Gmail Client, Draft Stage

**Outcome:** Every TAILORED job generates a personalized cold-email via Gemini 2.5 Pro, validates the output (no placeholders, no "As an AI" patterns, only the approved apply URL, only the user's signature email), creates a Gmail draft with the chosen resume PDF attached, and persists an `email_drafts` row in `GENERATED → DRAFT_CREATED` state. Job advances to AWAITING_APPROVAL. The Pro draft soft cap (40/day) from Phase 5's limiter governs how many jobs reach this state per run.

### Task 8.1: Email generation prompt + validator

**Files:**
- Create: `src/knockknock/email_gen/__init__.py`
- Create: `src/knockknock/email_gen/prompts.py`
- Create: `src/knockknock/email_gen/validator.py`
- Create: `tests/test_email_gen/__init__.py`
- Create: `tests/test_email_gen/test_prompts.py`
- Create: `tests/test_email_gen/test_validator.py`

The validator is the **safety net** for prompt-injection from job descriptions and for hallucinated content. It runs after every Pro call; if it fails, we get one regeneration attempt with the error fed back as a hint (per spec: "1 rewrite"); a second failure flips the job to ERROR and surfaces it in the digest.

- [ ] **Step 1: Write failing prompt tests**

Create `tests/test_email_gen/__init__.py` as empty file.

Create `tests/test_email_gen/test_prompts.py`:

```python
from __future__ import annotations

from pathlib import Path

from knockknock.config.preferences import load_preferences
from knockknock.email_gen.prompts import (
    SYSTEM_INSTRUCTION,
    build_draft_prompt,
    build_regenerate_prompt,
)


def _prefs():
    return load_preferences(Path("config/job_preferences.yaml"))


def test_system_instruction_forbids_placeholders() -> None:
    assert "[" not in SYSTEM_INSTRUCTION or "DO NOT" in SYSTEM_INSTRUCTION
    assert "placeholder" in SYSTEM_INSTRUCTION.lower()


def test_build_draft_prompt_includes_recipient_and_role() -> None:
    prefs = _prefs()
    prompt = build_draft_prompt(
        prefs,
        recipient_name="Aarav Singh",
        recipient_email="aarav@acme.io",
        company_name="Acme",
        role_title="Senior Backend Engineer",
        role_description="Build distributed Python services on AWS.",
        apply_url="https://acme.io/jobs/1",
    )
    assert "Aarav Singh" in prompt
    assert "Acme" in prompt
    assert "Senior Backend Engineer" in prompt
    assert "https://acme.io/jobs/1" in prompt
    # Candidate signature info must be present.
    assert prefs.candidate.full_name in prompt
    assert prefs.candidate.email in prompt


def test_build_draft_prompt_truncates_long_description() -> None:
    prefs = _prefs()
    big = "X" * 50_000
    prompt = build_draft_prompt(
        prefs,
        recipient_name="A",
        recipient_email="a@a.com",
        company_name="Acme",
        role_title="r",
        role_description=big,
        apply_url="https://a.com/j",
    )
    assert len(prompt) < 25_000


def test_build_regenerate_prompt_includes_previous_draft_and_reason() -> None:
    prefs = _prefs()
    prompt = build_regenerate_prompt(
        prefs,
        recipient_name="A",
        recipient_email="a@a.com",
        company_name="Acme",
        role_title="r",
        role_description="d",
        apply_url="https://a.com/j",
        previous_subject="prev sub",
        previous_body="prev body",
        reason="Body contained [Your Name] placeholder.",
    )
    assert "prev sub" in prompt
    assert "[Your Name]" in prompt
    assert "placeholder" in prompt.lower() or "reason" in prompt.lower()
```

- [ ] **Step 2: Write failing validator tests**

Create `tests/test_email_gen/test_validator.py`:

```python
from __future__ import annotations

import pytest

from knockknock.email_gen.validator import (
    DraftOutput,
    ValidationError,
    parse_draft_response,
    validate_draft,
)


def _draft(**overrides) -> DraftOutput:
    base = {
        "subject": "Re: Backend role at Acme",
        "body_text": "Hi Aarav,\n\nI'd love to talk about the role.\n\nBest,\nNishant\nnishant@example.com",
        "body_html": "<p>Hi Aarav,</p><p>I'd love to talk about the role.</p><p>Best,<br>Nishant<br>nishant@example.com</p>",
    }
    base.update(overrides)
    return DraftOutput(**base)


def _context(**overrides):
    base = {
        "candidate_name": "Nishant",
        "candidate_email": "nishant@example.com",
        "apply_url": "https://acme.io/jobs/1",
        "company_name": "Acme",
        "recipient_first_name": "Aarav",
    }
    base.update(overrides)
    return base


def test_validate_accepts_good_draft() -> None:
    validate_draft(_draft(), **_context())


def test_validate_rejects_placeholder_brackets() -> None:
    bad = _draft(body_text="Hi [Recipient], I'd love to chat.\nBest, Nishant\nnishant@example.com")
    with pytest.raises(ValidationError, match="placeholder"):
        validate_draft(bad, **_context())


def test_validate_rejects_ai_disclosure_patterns() -> None:
    bad = _draft(body_text="As an AI language model, I'd love to talk.\nBest, Nishant\nnishant@example.com")
    with pytest.raises(ValidationError, match="AI"):
        validate_draft(bad, **_context())


def test_validate_rejects_unknown_url() -> None:
    bad = _draft(body_text="Hi, see https://evil.com/x\nBest, Nishant\nnishant@example.com")
    with pytest.raises(ValidationError, match="URL"):
        validate_draft(bad, **_context())


def test_validate_rejects_unknown_email_addresses() -> None:
    bad = _draft(body_text="Hi, reply to attacker@evil.com\nBest, Nishant\nnishant@example.com")
    with pytest.raises(ValidationError, match="email"):
        validate_draft(bad, **_context())


def test_validate_rejects_empty_subject() -> None:
    bad = _draft(subject="   ")
    with pytest.raises(ValidationError, match="subject"):
        validate_draft(bad, **_context())


def test_validate_rejects_body_too_short() -> None:
    bad = _draft(body_text="Hi.\n\nNishant", body_html="<p>Hi.</p>")
    with pytest.raises(ValidationError, match="short"):
        validate_draft(bad, **_context())


def test_validate_rejects_body_too_long() -> None:
    long_body = "Hi Aarav.\n" + ("This is a sentence about distributed systems. " * 200) + "Best, Nishant\nnishant@example.com"
    bad = _draft(body_text=long_body)
    with pytest.raises(ValidationError, match="long"):
        validate_draft(bad, **_context())


def test_parse_draft_response_strips_code_fence() -> None:
    raw = '```json\n{"subject":"S","body_text":"B","body_html":"<p>B</p>"}\n```'
    parsed = parse_draft_response(raw)
    assert parsed.subject == "S"


def test_parse_draft_response_rejects_missing_keys() -> None:
    with pytest.raises(ValueError, match="missing"):
        parse_draft_response('{"subject":"x"}')
```

- [ ] **Step 3: Confirm failure**

```bash
uv run pytest tests/test_email_gen -v
```

Expected: FAIL.

- [ ] **Step 4: Implement the prompt builder**

Create `src/knockknock/email_gen/__init__.py` as empty file.

Create `src/knockknock/email_gen/prompts.py`:

```python
"""Email-drafting prompts for Gemini 2.5 Pro.

Two templates: initial draft and regeneration (with previous + failure reason
fed back so Pro can self-correct).
"""

from __future__ import annotations

from knockknock.config.preferences import JobPreferences

PROMPT_VERSION = "draft-v1"

SYSTEM_INSTRUCTION = (
    "You are drafting a short, plain, professional cold email for a single named "
    "candidate to send to a startup founder or hiring manager. The candidate is "
    "real; the recipient is real. Write as the candidate, in first person. "
    "Output strict JSON only. NEVER use placeholders like [Your Name], "
    "[Company], or square-bracketed tokens. NEVER mention you are an AI, "
    "language model, or assistant. Only reference URLs and email addresses "
    "explicitly provided in the prompt — do not invent any."
)

_DESCRIPTION_CHAR_LIMIT = 10_000

_DRAFT_TEMPLATE = """\
Candidate:
- Name: {candidate_name}
- Email (for signature): {candidate_email}
- Pitch: {candidate_pitch}
- Seniority: {seniority}

Recipient:
- Name: {recipient_name}
- Email: {recipient_email}

Opportunity:
- Company: {company_name}
- Role: {role_title}
- Apply URL: {apply_url}
- Role description (truncated):
\"\"\"
{role_description}
\"\"\"

Write a cold email with these constraints:
- Tone: warm, direct, confident, no fluff. Two short paragraphs + sign-off.
- 90-160 words in the body. Subject 4-10 words.
- Paragraph 1: address recipient by first name, name the role, one specific
  reason this candidate is a good fit grounded in the description.
- Paragraph 2: one concrete recent achievement (1-2 sentences) + a soft CTA
  (resume attached, happy to chat).
- Sign off with `Best,\\n{candidate_name}\\n{candidate_email}`.
- Reference {apply_url} once, naturally, if a link is needed at all.

Output strict JSON with EXACT keys:
{{
  "subject": "<subject line>",
  "body_text": "<plain-text body, with \\n line breaks>",
  "body_html": "<minimal HTML: <p> tags only, no inline styles>"
}}
"""

_REGEN_TEMPLATE = (
    _DRAFT_TEMPLATE
    + """\

Your previous attempt was REJECTED for this reason:
\"\"\"
{reason}
\"\"\"

Previous subject: {previous_subject}
Previous body:
\"\"\"
{previous_body}
\"\"\"

Write a corrected draft. Same JSON shape. Same constraints. Fix the specific issue stated above.
"""
)


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text or "(no description)"
    return text[:limit] + "\n…[truncated]"


def build_draft_prompt(
    prefs: JobPreferences,
    *,
    recipient_name: str,
    recipient_email: str,
    company_name: str,
    role_title: str,
    role_description: str,
    apply_url: str,
) -> str:
    return _DRAFT_TEMPLATE.format(
        candidate_name=prefs.candidate.full_name,
        candidate_email=prefs.candidate.email,
        candidate_pitch=prefs.candidate.pitch,
        seniority=", ".join(prefs.candidate.seniority_levels),
        recipient_name=recipient_name or "Hiring team",
        recipient_email=recipient_email,
        company_name=company_name,
        role_title=role_title,
        role_description=_truncate(role_description, _DESCRIPTION_CHAR_LIMIT),
        apply_url=apply_url,
    )


def build_regenerate_prompt(
    prefs: JobPreferences,
    *,
    recipient_name: str,
    recipient_email: str,
    company_name: str,
    role_title: str,
    role_description: str,
    apply_url: str,
    previous_subject: str,
    previous_body: str,
    reason: str,
) -> str:
    return _REGEN_TEMPLATE.format(
        candidate_name=prefs.candidate.full_name,
        candidate_email=prefs.candidate.email,
        candidate_pitch=prefs.candidate.pitch,
        seniority=", ".join(prefs.candidate.seniority_levels),
        recipient_name=recipient_name or "Hiring team",
        recipient_email=recipient_email,
        company_name=company_name,
        role_title=role_title,
        role_description=_truncate(role_description, _DESCRIPTION_CHAR_LIMIT),
        apply_url=apply_url,
        previous_subject=previous_subject,
        previous_body=previous_body,
        reason=reason,
    )
```

> **Preferences field note:** the template references `prefs.candidate.full_name`, `prefs.candidate.email`, `prefs.candidate.pitch`, `prefs.candidate.seniority_levels`. If Phase 2's `Candidate` model used different names, either rename here to match, or add the missing fields to the Candidate model (and update `config/job_preferences.yaml` to include them).

- [ ] **Step 5: Implement the validator**

Create `src/knockknock/email_gen/validator.py`:

```python
"""Strict validator for Pro draft output. Prompt-injection safety net."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
_BRACKETED_PLACEHOLDER_RE = re.compile(r"\[[A-Za-z][^\]\n]{1,40}\]")
_URL_RE = re.compile(r"https?://[^\s)\"'>]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_AI_DISCLOSURE_PATTERNS = (
    r"\bas an (?:ai|artificial intelligence)\b",
    r"\b(?:i am|i'm) (?:an? )?(?:ai|language model|assistant|chatbot)\b",
    r"\blarge language model\b",
)
_AI_DISCLOSURE_RE = re.compile("|".join(_AI_DISCLOSURE_PATTERNS), re.IGNORECASE)

_MIN_BODY_CHARS = 200
_MAX_BODY_CHARS = 2200
_MIN_SUBJECT_CHARS = 3
_MAX_SUBJECT_CHARS = 100


class ValidationError(ValueError):
    """Raised when the draft fails any safety/quality rule."""


@dataclass(frozen=True, slots=True)
class DraftOutput:
    subject: str
    body_text: str
    body_html: str


def parse_draft_response(raw: str) -> DraftOutput:
    """Parse Pro JSON response. Tolerate code fences."""
    stripped = (raw or "").strip()
    m = _FENCE_RE.match(stripped)
    if m:
        stripped = m.group(1).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Draft response not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Draft response must be a JSON object.")
    missing = [k for k in ("subject", "body_text", "body_html") if not data.get(k)]
    if missing:
        raise ValueError(f"Draft response missing keys: {missing}")
    return DraftOutput(
        subject=str(data["subject"]).strip(),
        body_text=str(data["body_text"]).strip(),
        body_html=str(data["body_html"]).strip(),
    )


def validate_draft(
    draft: DraftOutput,
    *,
    candidate_name: str,
    candidate_email: str,
    apply_url: str,
    company_name: str,  # noqa: ARG001 - kept for parity with future content checks
    recipient_first_name: str,  # noqa: ARG001 - reserved for personalisation check
) -> None:
    """Raise ValidationError on any quality / safety failure."""
    subject = draft.subject.strip()
    body = draft.body_text.strip()

    # Subject length.
    if not (_MIN_SUBJECT_CHARS <= len(subject) <= _MAX_SUBJECT_CHARS):
        raise ValidationError(
            f"subject length {len(subject)} not in [{_MIN_SUBJECT_CHARS},{_MAX_SUBJECT_CHARS}]"
        )

    # Body length.
    if len(body) < _MIN_BODY_CHARS:
        raise ValidationError(f"body too short ({len(body)} chars, need {_MIN_BODY_CHARS}+)")
    if len(body) > _MAX_BODY_CHARS:
        raise ValidationError(f"body too long ({len(body)} chars, max {_MAX_BODY_CHARS})")

    # Bracketed placeholders.
    if _BRACKETED_PLACEHOLDER_RE.search(subject) or _BRACKETED_PLACEHOLDER_RE.search(body):
        raise ValidationError("draft contains placeholder text inside [brackets]")

    # AI-disclosure patterns.
    if _AI_DISCLOSURE_RE.search(body) or _AI_DISCLOSURE_RE.search(subject):
        raise ValidationError("draft mentions AI/language-model self-disclosure")

    # URL whitelist (only the apply_url is allowed in the body).
    allowed_urls = {apply_url.strip().rstrip("/")}
    for url in _URL_RE.findall(body):
        if url.rstrip("/") not in allowed_urls:
            raise ValidationError(f"draft contains unapproved URL: {url}")

    # Email whitelist (only the candidate's signature email is allowed).
    allowed_emails = {candidate_email.lower()}
    found_emails = {e.lower() for e in _EMAIL_RE.findall(body)}
    extraneous = found_emails - allowed_emails
    if extraneous:
        raise ValidationError(f"draft contains unapproved email address(es): {sorted(extraneous)}")

    # Soft signature check — the candidate's name must appear once.
    if candidate_name.lower() not in body.lower():
        raise ValidationError("draft body missing candidate name signature")
```

- [ ] **Step 6: Run tests until green**

```bash
uv run pytest tests/test_email_gen -v
```

Expected: PASS for all prompt + validator tests.

- [ ] **Step 7: Type-check + lint + commit**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
git add src/knockknock/email_gen tests/test_email_gen
git commit -m "feat(email-gen): prompt templates + strict output validator"
```

### Task 8.2: Gmail OAuth + client wrapper

**Files:**
- Create: `src/knockknock/clients/gmail.py`
- Create: `tests/test_clients/test_gmail.py`

Gmail API is auth'd with a long-lived **OAuth refresh token** issued once by the user. We store `(client_id, client_secret, refresh_token)` in Secret Manager and exchange the refresh token for a short-lived access token at every CLI run. The wrapper exposes `create_draft(...) → gmail_draft_id` and `send_draft(gmail_draft_id) → gmail_message_id` — that's it. Reply tracking is explicitly out of scope (per spec).

- [ ] **Step 1: Write failing tests using a stubbed Resource object**

Create `tests/test_clients/test_gmail.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from knockknock.clients.gmail import GmailClient, GmailDraftSpec
from knockknock.exceptions import ExternalServiceError


@dataclass
class _StubGmailResource:
    """Mimics the google-api-python-client `users().drafts()` builder chain.

    The real shape is:
        service.users().drafts().create(userId="me", body=...).execute()
    """

    create_calls: list[dict] = field(default_factory=list)
    send_calls: list[dict] = field(default_factory=list)
    create_response: dict = field(default_factory=lambda: {"id": "draft_123", "message": {"id": "msg_999"}})
    send_response: dict = field(default_factory=lambda: {"id": "msg_999"})
    fail_create: bool = False
    fail_send: bool = False

    def users(self):  # type: ignore[no-untyped-def]
        return self

    def drafts(self):  # type: ignore[no-untyped-def]
        return self

    def create(self, *, userId: str, body: dict):  # noqa: N803 - api signature
        self.create_calls.append({"userId": userId, "body": body})
        return _Req(self.create_response, fail=self.fail_create)

    def send(self, *, userId: str, body: dict):  # noqa: N803
        self.send_calls.append({"userId": userId, "body": body})
        return _Req(self.send_response, fail=self.fail_send)


@dataclass
class _Req:
    response: dict
    fail: bool = False

    def execute(self):  # type: ignore[no-untyped-def]
        if self.fail:
            raise RuntimeError("gmail boom")
        return self.response


def test_create_draft_returns_gmail_draft_id() -> None:
    stub = _StubGmailResource()
    client = GmailClient(service=stub, sender_email="me@example.com")
    spec = GmailDraftSpec(
        to_email="aarav@acme.io",
        subject="Hi",
        body_text="Hello\nNishant\nme@example.com",
        body_html="<p>Hello</p><p>Nishant<br>me@example.com</p>",
        attachments=[("resume.pdf", b"%PDF-1.4 stub", "application/pdf")],
    )
    draft_id = client.create_draft(spec)
    assert draft_id == "draft_123"
    assert stub.create_calls[0]["userId"] == "me"
    raw = stub.create_calls[0]["body"]["message"]["raw"]
    assert isinstance(raw, str)
    assert len(raw) > 0  # base64 of MIME


def test_create_draft_wraps_exceptions() -> None:
    stub = _StubGmailResource(fail_create=True)
    client = GmailClient(service=stub, sender_email="me@example.com")
    spec = GmailDraftSpec(
        to_email="x@x.com",
        subject="s",
        body_text="body\nNishant\nme@example.com",
        body_html="<p>body</p>",
        attachments=[],
    )
    with pytest.raises(ExternalServiceError, match="draft"):
        client.create_draft(spec)


def test_send_draft_returns_message_id() -> None:
    stub = _StubGmailResource()
    client = GmailClient(service=stub, sender_email="me@example.com")
    message_id = client.send_draft("draft_123")
    assert message_id == "msg_999"
    assert stub.send_calls[0]["body"] == {"id": "draft_123"}


def test_send_draft_wraps_exceptions() -> None:
    stub = _StubGmailResource(fail_send=True)
    client = GmailClient(service=stub, sender_email="me@example.com")
    with pytest.raises(ExternalServiceError, match="send"):
        client.send_draft("draft_x")
```

- [ ] **Step 2: Confirm failure**

```bash
uv run pytest tests/test_clients/test_gmail.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement the client**

Create `src/knockknock/clients/gmail.py`:

```python
"""Gmail API wrapper: create + send drafts via OAuth refresh-token flow."""

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
    to_email: str
    subject: str
    body_text: str
    body_html: str
    attachments: list[tuple[str, bytes, str]]  # (filename, bytes, mime_type)
    cc_email: str | None = None
    reply_to: str | None = None


class _GmailService(Protocol):
    """Subset of the google-api-python-client `Resource` we depend on."""

    def users(self) -> Any: ...


@dataclass(slots=True)
class GmailClient:
    service: _GmailService
    sender_email: str

    def create_draft(self, spec: GmailDraftSpec) -> str:
        """Create a Gmail draft, return its draft_id."""
        raw = _build_mime_raw(spec, sender_email=self.sender_email)
        body = {"message": {"raw": raw}}
        try:
            response = (
                self.service.users()
                .drafts()
                .create(userId="me", body=body)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001 - googleapiclient raises diverse types.
            raise ExternalServiceError(f"Gmail draft create failed: {exc}") from exc
        draft_id = response.get("id")
        if not draft_id:
            raise ExternalServiceError(f"Gmail draft response missing 'id': {response}")
        return str(draft_id)

    def send_draft(self, draft_id: str) -> str:
        """Send a previously-created draft, return the message_id."""
        try:
            response = (
                self.service.users()
                .drafts()
                .send(userId="me", body={"id": draft_id})
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            raise ExternalServiceError(f"Gmail draft send failed: {exc}") from exc
        message_id = response.get("id")
        if not message_id:
            raise ExternalServiceError(f"Gmail send response missing 'id': {response}")
        return str(message_id)


def _build_mime_raw(spec: GmailDraftSpec, *, sender_email: str) -> str:
    """Build an RFC 2822 MIME message and base64url-encode for Gmail."""
    container = MIMEMultipart("mixed")
    container["To"] = spec.to_email
    container["From"] = sender_email
    container["Subject"] = spec.subject
    if spec.cc_email:
        container["Cc"] = spec.cc_email
    if spec.reply_to:
        container["Reply-To"] = spec.reply_to

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(spec.body_text, "plain", "utf-8"))
    alt.attach(MIMEText(spec.body_html, "html", "utf-8"))
    container.attach(alt)

    for filename, payload, mime_type in spec.attachments:
        maintype, subtype = (mime_type.split("/", 1) + ["octet-stream"])[:2]
        if maintype == "application":
            part = MIMEApplication(payload, _subtype=subtype, name=filename)
        else:
            # Fall back: treat as octet-stream so we don't crash on odd MIME types.
            part = MIMEApplication(payload, _subtype="octet-stream", name=filename)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        container.attach(part)

    return base64.urlsafe_b64encode(container.as_bytes()).decode("ascii")


def build_gmail_service(
    *, client_id: str, client_secret: str, refresh_token: str
) -> _GmailService:
    """Construct an authorised Gmail Resource. Imported lazily for offline tests."""
    from google.auth.transport.requests import Request  # type: ignore[import-not-found]
    from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]
    from googleapiclient.discovery import build  # type: ignore[import-not-found]

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=[
            "https://www.googleapis.com/auth/gmail.compose",
            "https://www.googleapis.com/auth/gmail.send",
        ],
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)
```

- [ ] **Step 4: Run tests until green**

```bash
uv run pytest tests/test_clients/test_gmail.py -v
```

Expected: PASS for all 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/knockknock/clients/gmail.py tests/test_clients/test_gmail.py
git commit -m "feat(clients): gmail draft create/send wrapper"
```

### Task 8.3: Draft stage — orchestrate Pro call + validator + Gmail + persistence

**Files:**
- Create: `src/knockknock/pipeline/draft.py`
- Create: `tests/test_pipeline/test_draft_stage.py`

Stage flow per job (TAILORED → DRAFTED → AWAITING_APPROVAL):

1. Pre-check Pro draft quota via `GeminiLimiter.check(PRO_2_5, DRAFT_EMAIL)`. If exhausted, halt cleanly.
2. Look up the phonebook row (must exist — guaranteed by enrich stage). Use `founder_email` if present, else `careers_email`.
3. Load the resume PDF bytes from `resumes/<resume_variant_key>.pdf`. If missing, mark job ERROR with `last_error="resume_pdf_missing"`.
4. Build prompt → call Gemini Pro → parse → validate. On validation failure, build regenerate prompt → call Pro again → parse → validate. Second failure → mark job ERROR, count call against logs, continue.
5. Create Gmail draft. Log call regardless of success/failure to `gemini_call_logs`.
6. Persist `email_drafts` row (`state=DRAFT_CREATED` on success, `FAILED` on Gmail failure). Update job: `status=AWAITING_APPROVAL` on success; on Gmail failure leave at TAILORED with `last_error` set and `retry_count += 1`.
7. Telegram notification is deferred to Phase 9 (the draft row has all the data the bot needs).

- [ ] **Step 1: Write failing stage tests**

Create `tests/test_pipeline/test_draft_stage.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from sqlmodel import Session

from knockknock.clients.gemini import GeminiResponse
from knockknock.clients.gmail import GmailDraftSpec
from knockknock.config.preferences import load_preferences
from knockknock.db.enums import (
    DraftState,
    GeminiModel,
    GeminiPurpose,
    JobStatus,
    PhonebookSource,
    PipelineStage,
)
from knockknock.db.models import (
    Company,
    EmailDraft,
    JobApplication,
    JobApplicationEvent,
    PhonebookEntry,
)
from knockknock.exceptions import ExternalServiceError, RpdExhaustedError
from knockknock.pipeline.draft import DraftStage
from pathlib import Path

PREFS = load_preferences(Path("config/job_preferences.yaml"))


@dataclass
class _StubGemini:
    responses: list[str]
    calls: int = 0

    def generate(self, **_: object) -> GeminiResponse:  # type: ignore[no-untyped-def]
        text = self.responses[self.calls]
        self.calls += 1
        return GeminiResponse(text=text, tokens_input=100, tokens_output=200)


@dataclass
class _NoLimit:
    def check(self, *_a: object, **_kw: object) -> None: ...
    def compute_rpm_wait_seconds(self, *_a: object, **_kw: object) -> float | None:
        return None


@dataclass
class _StubGmail:
    sender_email: str = "me@example.com"
    draft_calls: list[GmailDraftSpec] = field(default_factory=list)
    fail: bool = False

    def create_draft(self, spec: GmailDraftSpec) -> str:
        if self.fail:
            raise ExternalServiceError("gmail broke")
        self.draft_calls.append(spec)
        return f"draft_{len(self.draft_calls)}"

    def send_draft(self, draft_id: str) -> str:  # not used here, kept for protocol parity
        return "unused"


def _load_pdf_stub(_key: str) -> bytes:
    return b"%PDF-1.4 stub"


def _make_company_with_phonebook(db_session: Session) -> Company:
    company = Company(name="Acme", domain="acme.io")
    db_session.add(company)
    db_session.flush()
    db_session.add(
        PhonebookEntry(
            company_id=company.id,
            founder_name="Aarav Singh",
            founder_email="aarav@acme.io",
            careers_email="careers@acme.io",
            source=PhonebookSource.APOLLO,
        )
    )
    db_session.flush()
    return company


def _make_tailored_job(
    db_session: Session, company: Company, *, score: int = 8, source_job_id: str = "j-1"
) -> JobApplication:
    job = JobApplication(
        company_id=company.id,
        title="Senior Backend Engineer",
        location="Bangalore",
        description="Build distributed Python services.",
        apply_url="https://acme.io/jobs/1",
        source="HN_WHO_IS_HIRING",
        source_job_id=source_job_id,
        status=JobStatus.TAILORED,
        score=score,
        resume_variant_key="backend-distributed",
        discovered_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.flush()
    return job


_GOOD_DRAFT_JSON = (
    '{"subject":"Backend role at Acme","body_text":"Hi Aarav,\\n\\n'
    "I'd love to talk about the backend role at Acme. I've spent the last three years "
    "building distributed Python services on Kubernetes with Postgres and Kafka, which "
    "lines up well with what you described. https://acme.io/jobs/1\\n\\n"
    "Most recently I cut p99 latency on a payment-routing service from 850ms to 90ms by "
    "introducing a request-coalescing layer. Resume attached — happy to chat any time.\\n\\n"
    'Best,\\nNishant\\nnishant@example.com",'
    '"body_html":"<p>Hi Aarav,</p><p>I would love to talk...</p>'
    "<p>Best,<br>Nishant<br>nishant@example.com</p>\"}"
)

_BAD_DRAFT_JSON_PLACEHOLDER = (
    '{"subject":"Hi","body_text":"Hi [Recipient],\\n\\n'
    "I'd love to talk about the role at [Company]. I've been building backend stuff "
    "and I'd be a good fit. https://acme.io/jobs/1\\n\\nResume attached.\\n\\n"
    'Best,\\nNishant\\nnishant@example.com",'
    '"body_html":"<p>Hi</p>"}'
)


def test_draft_stage_happy_path(db_session: Session) -> None:
    company = _make_company_with_phonebook(db_session)
    job = _make_tailored_job(db_session, company)
    gemini = _StubGemini(responses=[_GOOD_DRAFT_JSON])
    gmail = _StubGmail()
    stage = DraftStage(
        prefs=PREFS,
        gemini=gemini,
        limiter=_NoLimit(),
        gmail=gmail,
        load_resume_pdf=_load_pdf_stub,
    )
    result = stage.run(session=db_session, run_id=1)
    db_session.refresh(job)
    assert job.status == JobStatus.AWAITING_APPROVAL
    draft = db_session.query(EmailDraft).filter_by(job_application_id=job.id).one()
    assert draft.state == DraftState.DRAFT_CREATED
    assert draft.gmail_draft_id == "draft_1"
    assert draft.to_email == "aarav@acme.io"
    assert result.advanced == 1
    assert gemini.calls == 1


def test_draft_stage_regenerates_after_validator_failure(db_session: Session) -> None:
    company = _make_company_with_phonebook(db_session)
    job = _make_tailored_job(db_session, company)
    gemini = _StubGemini(responses=[_BAD_DRAFT_JSON_PLACEHOLDER, _GOOD_DRAFT_JSON])
    gmail = _StubGmail()
    stage = DraftStage(
        prefs=PREFS,
        gemini=gemini,
        limiter=_NoLimit(),
        gmail=gmail,
        load_resume_pdf=_load_pdf_stub,
    )
    stage.run(session=db_session, run_id=1)
    db_session.refresh(job)
    assert job.status == JobStatus.AWAITING_APPROVAL
    assert gemini.calls == 2  # initial + 1 regenerate


def test_draft_stage_marks_job_error_on_second_validator_failure(db_session: Session) -> None:
    company = _make_company_with_phonebook(db_session)
    job = _make_tailored_job(db_session, company)
    gemini = _StubGemini(
        responses=[_BAD_DRAFT_JSON_PLACEHOLDER, _BAD_DRAFT_JSON_PLACEHOLDER]
    )
    gmail = _StubGmail()
    stage = DraftStage(
        prefs=PREFS,
        gemini=gemini,
        limiter=_NoLimit(),
        gmail=gmail,
        load_resume_pdf=_load_pdf_stub,
    )
    result = stage.run(session=db_session, run_id=1)
    db_session.refresh(job)
    assert job.status == JobStatus.ERROR
    assert "validator" in (job.last_error or "").lower() or "placeholder" in (job.last_error or "").lower()
    assert result.errors == 1


def test_draft_stage_falls_back_to_careers_email_when_no_founder(db_session: Session) -> None:
    company = Company(name="Acme", domain="acme.io")
    db_session.add(company)
    db_session.flush()
    db_session.add(
        PhonebookEntry(
            company_id=company.id,
            founder_name=None,
            founder_email=None,
            careers_email="careers@acme.io",
            source=PhonebookSource.SEED,
        )
    )
    db_session.flush()
    job = _make_tailored_job(db_session, company)
    gemini = _StubGemini(responses=[_GOOD_DRAFT_JSON])
    gmail = _StubGmail()
    stage = DraftStage(
        prefs=PREFS,
        gemini=gemini,
        limiter=_NoLimit(),
        gmail=gmail,
        load_resume_pdf=_load_pdf_stub,
    )
    stage.run(session=db_session, run_id=1)
    draft = db_session.query(EmailDraft).filter_by(job_application_id=job.id).one()
    assert draft.to_email == "careers@acme.io"


def test_draft_stage_halts_on_rpd_exhausted(db_session: Session) -> None:
    company = _make_company_with_phonebook(db_session)
    _make_tailored_job(db_session, company)

    class _Halt:
        def check(self, *_a: object, **_kw: object) -> None:
            raise RpdExhaustedError("pro exhausted")

        def compute_rpm_wait_seconds(self, *_a: object, **_kw: object) -> float | None:
            return None

    gemini = _StubGemini(responses=[])
    gmail = _StubGmail()
    stage = DraftStage(
        prefs=PREFS,
        gemini=gemini,
        limiter=_Halt(),
        gmail=gmail,
        load_resume_pdf=_load_pdf_stub,
    )
    result = stage.run(session=db_session, run_id=1)
    assert gemini.calls == 0
    assert result.halted_reason == "rpd_exhausted"


def test_draft_stage_handles_gmail_failure(db_session: Session) -> None:
    company = _make_company_with_phonebook(db_session)
    job = _make_tailored_job(db_session, company)
    gemini = _StubGemini(responses=[_GOOD_DRAFT_JSON])
    gmail = _StubGmail(fail=True)
    stage = DraftStage(
        prefs=PREFS,
        gemini=gemini,
        limiter=_NoLimit(),
        gmail=gmail,
        load_resume_pdf=_load_pdf_stub,
    )
    result = stage.run(session=db_session, run_id=1)
    db_session.refresh(job)
    assert job.status == JobStatus.TAILORED  # left at TAILORED for retry
    draft = db_session.query(EmailDraft).filter_by(job_application_id=job.id).one()
    assert draft.state == DraftState.FAILED
    assert result.errors == 1
    assert "gmail" in (job.last_error or "").lower()


def test_draft_stage_processes_in_score_desc_order(db_session: Session) -> None:
    company = _make_company_with_phonebook(db_session)
    low = _make_tailored_job(db_session, company, score=6, source_job_id="lo")
    high = _make_tailored_job(db_session, company, score=9, source_job_id="hi")
    gemini = _StubGemini(responses=[_GOOD_DRAFT_JSON, _GOOD_DRAFT_JSON])
    gmail = _StubGmail()
    stage = DraftStage(
        prefs=PREFS,
        gemini=gemini,
        limiter=_NoLimit(),
        gmail=gmail,
        load_resume_pdf=_load_pdf_stub,
    )
    stage.run(session=db_session, run_id=1)
    events = (
        db_session.query(JobApplicationEvent)
        .filter(JobApplicationEvent.stage == PipelineStage.DRAFT)
        .order_by(JobApplicationEvent.id.asc())
        .all()
    )
    assert events[0].job_application_id == high.id
    assert events[1].job_application_id == low.id
```

- [ ] **Step 2: Confirm failure**

```bash
uv run pytest tests/test_pipeline/test_draft_stage.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement the stage**

Create `src/knockknock/pipeline/draft.py`:

```python
"""Draft stage: TAILORED → AWAITING_APPROVAL via Pro + validator + Gmail."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import structlog
from sqlmodel import Session, select

from knockknock.clients.gemini import GeminiResponse
from knockknock.clients.gmail import GmailDraftSpec
from knockknock.config.preferences import JobPreferences
from knockknock.db.enums import (
    DraftState,
    GeminiModel,
    GeminiPurpose,
    JobStatus,
    PipelineStage,
)
from knockknock.db.models import (
    Company,
    EmailDraft,
    GeminiCallLog,
    JobApplication,
    JobApplicationEvent,
    PhonebookEntry,
)
from knockknock.email_gen.prompts import (
    PROMPT_VERSION,
    SYSTEM_INSTRUCTION,
    build_draft_prompt,
    build_regenerate_prompt,
)
from knockknock.email_gen.validator import (
    DraftOutput,
    ValidationError,
    parse_draft_response,
    validate_draft,
)
from knockknock.exceptions import ExternalServiceError, RateLimitedError
from knockknock.pipeline.stage import StageResult

log = structlog.get_logger(__name__)


class _GeminiLike(Protocol):
    def generate(
        self,
        *,
        model: GeminiModel,
        system_instruction: str,
        user_prompt: str,
        response_mime_type: str,
    ) -> GeminiResponse: ...


class _LimiterLike(Protocol):
    def check(self, model: GeminiModel, purpose: GeminiPurpose) -> None: ...
    def compute_rpm_wait_seconds(self, model: GeminiModel) -> float | None: ...


class _GmailLike(Protocol):
    def create_draft(self, spec: GmailDraftSpec) -> str: ...
    def send_draft(self, draft_id: str) -> str: ...


PdfLoader = Callable[[str], bytes]


def _default_pdf_loader(variant_key: str) -> bytes:
    path = Path("resumes") / f"{variant_key}.pdf"
    return path.read_bytes()


@dataclass(slots=True)
class DraftStage:
    """Generate, validate, and persist a personalized cold-email per job."""

    prefs: JobPreferences
    gemini: _GeminiLike
    limiter: _LimiterLike
    gmail: _GmailLike
    load_resume_pdf: PdfLoader = _default_pdf_loader
    sleep_fn: Callable[[float], None] = time.sleep
    name: str = "draft"

    _model: GeminiModel = GeminiModel.PRO_2_5

    def run(self, *, session: Session, run_id: int) -> StageResult:  # noqa: PLR0915
        stmt = (
            select(JobApplication, Company, PhonebookEntry)
            .join(Company, Company.id == JobApplication.company_id)
            .join(PhonebookEntry, PhonebookEntry.company_id == Company.id)
            .where(JobApplication.status == JobStatus.TAILORED)
            .order_by(
                JobApplication.score.desc(),
                JobApplication.discovered_at.asc(),
            )
        )

        advanced = 0
        errors = 0
        halted_reason: str | None = None

        for job, company, phonebook in session.exec(stmt).all():
            # Pre-call: enforce Pro draft quota BEFORE consuming a call.
            try:
                self.limiter.check(self._model, GeminiPurpose.DRAFT_EMAIL)
            except RateLimitedError as exc:
                log.warning("draft.halted", reason=str(exc))
                halted_reason = "rpd_exhausted"
                break

            wait = self.limiter.compute_rpm_wait_seconds(self._model)
            if wait and wait > 0:
                self.sleep_fn(min(wait + 0.1, 65.0))

            recipient_email = phonebook.founder_email or phonebook.careers_email
            recipient_name = phonebook.founder_name or "Hiring team"

            # 1. Initial draft.
            try:
                draft_output = self._generate_and_validate(
                    purpose=GeminiPurpose.DRAFT_EMAIL,
                    prompt=build_draft_prompt(
                        self.prefs,
                        recipient_name=recipient_name,
                        recipient_email=recipient_email,
                        company_name=company.name,
                        role_title=job.title,
                        role_description=job.description or "",
                        apply_url=job.apply_url,
                    ),
                    job_id=job.id,
                    session=session,
                    recipient_first_name=_first_name(recipient_name),
                    apply_url=job.apply_url,
                    company_name=company.name,
                )
            except ValidationError as first_err:
                # 2. One regeneration attempt with feedback.
                try:
                    draft_output = self._generate_and_validate(
                        purpose=GeminiPurpose.REGENERATE,
                        prompt=build_regenerate_prompt(
                            self.prefs,
                            recipient_name=recipient_name,
                            recipient_email=recipient_email,
                            company_name=company.name,
                            role_title=job.title,
                            role_description=job.description or "",
                            apply_url=job.apply_url,
                            previous_subject="(none — first attempt invalid)",
                            previous_body=str(first_err),
                            reason=str(first_err),
                        ),
                        job_id=job.id,
                        session=session,
                        recipient_first_name=_first_name(recipient_name),
                        apply_url=job.apply_url,
                        company_name=company.name,
                    )
                except (ValidationError, ExternalServiceError, ValueError) as second_err:
                    _mark_job_error(
                        session,
                        job=job,
                        run_id=run_id,
                        message=f"validator failed twice: {second_err}",
                    )
                    errors += 1
                    continue
            except (ExternalServiceError, ValueError) as exc:
                _mark_job_error(
                    session,
                    job=job,
                    run_id=run_id,
                    message=f"gemini draft call failed: {exc}",
                )
                errors += 1
                continue

            # 3. Load resume PDF.
            if not job.resume_variant_key:
                _mark_job_error(
                    session,
                    job=job,
                    run_id=run_id,
                    message="resume_variant_key missing (skipped tailor?)",
                )
                errors += 1
                continue
            try:
                pdf_bytes = self.load_resume_pdf(job.resume_variant_key)
            except FileNotFoundError as exc:
                _mark_job_error(
                    session,
                    job=job,
                    run_id=run_id,
                    message=f"resume_pdf_missing: {exc}",
                )
                errors += 1
                continue

            # 4. Create Gmail draft.
            gmail_spec = GmailDraftSpec(
                to_email=recipient_email,
                cc_email=phonebook.careers_email
                if phonebook.founder_email and phonebook.careers_email != phonebook.founder_email
                else None,
                subject=draft_output.subject,
                body_text=draft_output.body_text,
                body_html=draft_output.body_html,
                attachments=[(f"{job.resume_variant_key}.pdf", pdf_bytes, "application/pdf")],
            )

            try:
                gmail_draft_id = self.gmail.create_draft(gmail_spec)
            except ExternalServiceError as exc:
                # Leave job at TAILORED; persist FAILED draft so we can see history.
                draft = EmailDraft(
                    job_application_id=job.id,
                    phonebook_id=phonebook.id,
                    to_email=recipient_email,
                    cc_email=gmail_spec.cc_email,
                    subject=draft_output.subject,
                    body_text=draft_output.body_text,
                    body_html=draft_output.body_html,
                    resume_variant_key=job.resume_variant_key,
                    state=DraftState.FAILED,
                    failure_reason=str(exc),
                )
                session.add(draft)
                job.last_error = f"gmail draft create failed: {exc}"
                job.retry_count = (job.retry_count or 0) + 1
                session.flush()
                errors += 1
                log.warning("draft.gmail_failed", job_id=job.id, error=str(exc))
                continue

            # 5. Persist draft + advance job.
            draft = EmailDraft(
                job_application_id=job.id,
                phonebook_id=phonebook.id,
                to_email=recipient_email,
                cc_email=gmail_spec.cc_email,
                subject=draft_output.subject,
                body_text=draft_output.body_text,
                body_html=draft_output.body_html,
                resume_variant_key=job.resume_variant_key,
                gmail_draft_id=gmail_draft_id,
                state=DraftState.DRAFT_CREATED,
            )
            session.add(draft)
            from_status = job.status
            job.status = JobStatus.AWAITING_APPROVAL
            job.last_error = None
            session.add(
                JobApplicationEvent(
                    job_application_id=job.id,
                    pipeline_run_id=run_id,
                    stage=PipelineStage.DRAFT,
                    from_status=from_status,
                    to_status=JobStatus.AWAITING_APPROVAL,
                    detail=f"prompt={PROMPT_VERSION} variant={job.resume_variant_key}",
                )
            )
            session.flush()
            advanced += 1

        log.info(
            "draft.summary",
            advanced=advanced,
            errors=errors,
            halted=halted_reason is not None,
        )
        return StageResult(
            stage=PipelineStage.DRAFT,
            advanced=advanced,
            rejected=0,
            errors=errors,
            halted_reason=halted_reason,
        )

    def _generate_and_validate(
        self,
        *,
        purpose: GeminiPurpose,
        prompt: str,
        job_id: int,
        session: Session,
        recipient_first_name: str,
        apply_url: str,
        company_name: str,
    ) -> DraftOutput:
        started = time.monotonic()
        response: GeminiResponse | None = None
        try:
            response = self.gemini.generate(
                model=self._model,
                system_instruction=SYSTEM_INSTRUCTION,
                user_prompt=prompt,
                response_mime_type="application/json",
            )
            draft = parse_draft_response(response.text)
            validate_draft(
                draft,
                candidate_name=self.prefs.candidate.full_name,
                candidate_email=self.prefs.candidate.email,
                apply_url=apply_url,
                company_name=company_name,
                recipient_first_name=recipient_first_name,
            )
        except (ValidationError, ValueError) as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            session.add(
                GeminiCallLog(
                    job_application_id=job_id,
                    model=self._model,
                    purpose=purpose,
                    tokens_input=response.tokens_input if response else 0,
                    tokens_output=response.tokens_output if response else 0,
                    latency_ms=latency_ms,
                    success=False,
                    error_class=type(exc).__name__,
                )
            )
            session.flush()
            raise
        except Exception as exc:  # noqa: BLE001 - track all failures.
            latency_ms = int((time.monotonic() - started) * 1000)
            session.add(
                GeminiCallLog(
                    job_application_id=job_id,
                    model=self._model,
                    purpose=purpose,
                    tokens_input=0,
                    tokens_output=0,
                    latency_ms=latency_ms,
                    success=False,
                    error_class=type(exc).__name__,
                )
            )
            session.flush()
            raise

        latency_ms = int((time.monotonic() - started) * 1000)
        session.add(
            GeminiCallLog(
                job_application_id=job_id,
                model=self._model,
                purpose=purpose,
                tokens_input=response.tokens_input if response else 0,
                tokens_output=response.tokens_output if response else 0,
                latency_ms=latency_ms,
                success=True,
            )
        )
        session.flush()
        return draft


def _first_name(full_name: str) -> str:
    parts = (full_name or "").strip().split()
    return parts[0] if parts else ""


def _mark_job_error(
    session: Session,
    *,
    job: JobApplication,
    run_id: int,
    message: str,
) -> None:
    job.last_error = message
    job.retry_count = (job.retry_count or 0) + 1
    job.status = JobStatus.ERROR
    session.add(
        JobApplicationEvent(
            job_application_id=job.id,
            pipeline_run_id=run_id,
            stage=PipelineStage.DRAFT,
            from_status=JobStatus.TAILORED,
            to_status=JobStatus.ERROR,
            detail=message[:500],
        )
    )
    session.flush()
```

> **Schema note on `EmailDraft.cc_email`:** the test stub sets `cc_email` to `careers@acme.io` when the founder email is different (so the careers inbox is in the loop). If your Phase 1 schema didn't include `cc_email` as nullable, fix the migration. The spec at table `email_drafts` includes `cc_email TEXT` — confirm before running the stage tests.

> **Schema note on `EmailDraft.failure_reason`:** referenced in the Gmail-fail branch. Confirm it exists in Phase 1's `EmailDraft` model.

- [ ] **Step 4: Run tests until green**

```bash
uv run pytest tests/test_pipeline/test_draft_stage.py -v
```

Expected: PASS for all 7 tests.

- [ ] **Step 5: Run full suite + lint + commit**

```bash
uv run pytest -v
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
git add src/knockknock/pipeline/draft.py tests/test_pipeline/test_draft_stage.py
git commit -m "feat(pipeline): draft stage with gemini pro + validator + gmail"
```

### Task 8.4: Wire draft stage into CLI

**Files:**
- Modify: `src/knockknock/__main__.py`

- [ ] **Step 1: Build Gmail service from secrets and append `DraftStage`**

Edit `src/knockknock/__main__.py`. After the `TailorStage` is appended, add:

```python
from knockknock.clients.gmail import GmailClient, build_gmail_service
from knockknock.pipeline.draft import DraftStage


# ...inside the pipeline command, after TailorStage is appended...
gmail_service = build_gmail_service(
    client_id=secrets.get("gmail-oauth-client-id"),
    client_secret=secrets.get("gmail-oauth-client-secret"),
    refresh_token=secrets.get("gmail-oauth-refresh-token"),
)
gmail_client = GmailClient(service=gmail_service, sender_email=prefs.candidate.email)
stages.append(
    DraftStage(
        prefs=prefs,
        gemini=gemini_client,
        limiter=limiter,
        gmail=gmail_client,
    )
)
```

> **Limiter reuse note:** the same `GeminiLimiter` instance built for `ScoreStage` is reused here. Both stages run within the same `session_scope`, so the limiter's queries see all calls made in the same run. If you split sessions per-stage in a refactor, ensure each new limiter sees the parent DB.

- [ ] **Step 2: Smoke-run CLI**

```bash
KNOCKKNOCK_SECRET_GEMINI_API_KEY=fake \
KNOCKKNOCK_SECRET_APOLLO_API_KEY=fake \
KNOCKKNOCK_SECRET_HUNTER_API_KEY=fake \
KNOCKKNOCK_SECRET_GMAIL_OAUTH_CLIENT_ID=fake \
KNOCKKNOCK_SECRET_GMAIL_OAUTH_CLIENT_SECRET=fake \
KNOCKKNOCK_SECRET_GMAIL_OAUTH_REFRESH_TOKEN=fake \
uv run knockknock pipeline run --once
```

Expected: this will fail at `build_gmail_service` because the refresh token is fake. That's OK for smoke — it confirms wiring is correct. For real usage:

> **Gmail OAuth onboarding (manual one-time setup):** documented in Phase 12 (cloud deployment); for now, the user generates a refresh token using Google's OAuth playground with the scopes `gmail.compose` and `gmail.send`, then stores `gmail-oauth-{client-id,client-secret,refresh-token}` in `.env` locally or Secret Manager in cloud.

- [ ] **Step 3: Commit**

```bash
git add src/knockknock/__main__.py
git commit -m "feat(cli): wire draft stage with gemini pro + gmail"
```

---

← [Index](00-index.md) · [Prev: phase-07-resume-tailor.md](phase-07-resume-tailor.md) · [Next: phase-09-telegram.md](phase-09-telegram.md)
