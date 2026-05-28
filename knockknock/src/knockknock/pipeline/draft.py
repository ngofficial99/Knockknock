"""Draft stage: TAILORED → AWAITING_APPROVAL via Gemini Pro + validator + Gmail.

This is the Phase 8 integration point. Per row:

1. Pre-check the Pro draft quota (``GeminiLimiter.check(PRO_2_5, DRAFT_EMAIL)``)
   *before* consuming a call; on :class:`RateLimitedError` the stage halts
   cleanly so the next run picks up where this one left off.
2. Resolve recipient: founder email (To, with careers Cc) when available,
   else careers-only on To. The phonebook row is guaranteed by the enrich
   stage so we treat its absence as a join miss (the row is silently
   skipped by the query, the same way TailorStage skips status mismatches).
3. Call Gemini Pro with the draft prompt, parse + validate the JSON response.
   On :class:`ValidationError` we get exactly one regeneration attempt with
   the rejection reason fed back; a second failure marks the job ERROR.
4. Load the tailored resume PDF for the chosen variant. ``FileNotFoundError``
   (operator config bug: the variant key references a PDF that's missing
   on disk) marks the job ERROR with ``last_error="resume_pdf_missing:..."``.
5. Create the Gmail draft via :class:`GmailClient`. On
   :class:`ExternalServiceError` we persist the ``EmailDraft`` row with
   ``state=FAILED`` so the operator has full context (subject + body), set
   ``last_error``, bump ``retry_count``, and leave the job at TAILORED so
   the next run can retry the whole flow.
6. On success persist ``EmailDraft(state=DRAFT_CREATED)``, advance the job
   to ``AWAITING_APPROVAL``, and emit a ``JobApplicationEvent``.

Drift from the original Phase 8 spec, intentional:

- ``DraftStage.run`` takes a :class:`StageContext` (the existing
  :class:`Stage` protocol shape used by ``ScoreStage`` and
  ``TailorStage``), *not* ``session`` + ``run_id`` as separate kwargs.
- ``EmailDraft`` carries a single ``body: str`` + JSONB recipient lists
  (``to_recipients`` / ``cc_recipients``), not the spec's
  ``to_email``/``cc_email``/``body_text``/``body_html``. The signature
  is attached server-side by Gmail; the prompt forbids a sign-off block
  and the validator rejects any email address in the body.
- ``EmailDraft`` has no ``phonebook_id`` / ``resume_variant_key`` /
  ``failure_reason`` columns; the spec's references to those did not
  survive the Phase 1 schema.
- ``GeminiCallLog`` uses ``job_id`` / ``prompt_tokens`` /
  ``completion_tokens`` / ``error_code`` (not the spec's
  ``job_application_id`` / ``tokens_input`` / ``tokens_output`` /
  ``error_class``).
- ``JobApplicationEvent`` uses ``job_id`` / ``payload`` / ``note`` (not
  ``job_application_id`` / ``pipeline_run_id`` / ``stage`` / ``detail``).
- ``DraftState`` is :class:`EmailDraftState` (actual enum name).
- ``prefs.candidate.name`` (not ``full_name``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import structlog
from sqlalchemy import asc, desc
from sqlmodel import Session, select

from knockknock.clients.gemini import GeminiResponse
from knockknock.clients.gmail import GmailDraftSpec
from knockknock.config.preferences import JobPreferences
from knockknock.db.enums import (
    EmailDraftState,
    GeminiModel,
    GeminiPurpose,
    JobStatus,
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
from knockknock.pipeline.stage import StageContext, StageResult

log = structlog.get_logger(__name__)


class _GeminiLike(Protocol):
    """Minimal Gemini surface the stage depends on."""

    def generate(
        self,
        *,
        model: GeminiModel,
        system_instruction: str,
        user_prompt: str,
        response_mime_type: str,
    ) -> GeminiResponse: ...


class _LimiterLike(Protocol):
    """Minimal limiter surface the stage depends on."""

    def check(self, model: GeminiModel, purpose: GeminiPurpose) -> None: ...

    def compute_rpm_wait_seconds(self, model: GeminiModel) -> float | None: ...


class _GmailLike(Protocol):
    """Minimal Gmail surface the stage depends on."""

    def create_draft(self, spec: GmailDraftSpec) -> str: ...


PdfLoader = Callable[[str], bytes]


def _default_pdf_loader(variant_key: str) -> bytes:
    """Read the tailored resume PDF for ``variant_key`` from disk.

    Path is ``resumes/<variant_key>.pdf``. Raises ``FileNotFoundError`` if
    missing; the stage catches that and marks the job ERROR so the next
    run picks it up after the operator fixes the manifest/PDF set.
    """
    path = Path("resumes") / f"{variant_key}.pdf"
    return path.read_bytes()


@dataclass
class DraftStage:
    """Generate, validate, and persist a personalised cold email per job.

    Field design:

    - ``gemini`` / ``limiter`` / ``gmail`` are typed as ``Protocol`` so
      unit tests can substitute stubs without pulling SDKs.
    - ``load_resume_pdf`` is injectable for tests; the production default
      reads from ``resumes/<variant_key>.pdf``.
    - ``sleep_fn`` is injectable so RPM-smoothing waits don't slow tests.
    """

    session: Session
    prefs: JobPreferences
    gemini: _GeminiLike
    limiter: _LimiterLike
    gmail: _GmailLike
    load_resume_pdf: PdfLoader = field(default=_default_pdf_loader)
    sleep_fn: Callable[[float], None] = field(default=time.sleep)
    name: str = field(default="draft", init=False)
    # Draft stage always uses Pro; Flash is reserved for SCORE.
    _model: GeminiModel = field(default=GeminiModel.PRO_2_5, init=False)

    def run(self, ctx: StageContext) -> StageResult:
        rows = self.session.exec(
            select(JobApplication, Company, PhonebookEntry)
            .join(Company, JobApplication.company_id == Company.id)  # type: ignore[arg-type]
            .join(PhonebookEntry, PhonebookEntry.company_id == Company.id)  # type: ignore[arg-type]
            .where(JobApplication.status == JobStatus.TAILORED)
            .order_by(
                desc(JobApplication.score),  # type: ignore[arg-type]
                asc(JobApplication.discovered_at),  # type: ignore[arg-type]
            )
        ).all()

        processed = 0
        advanced = 0
        errors = 0
        halted_reason: str | None = None

        for job, company, phonebook in rows:
            # Pre-call: enforce Pro draft quota BEFORE consuming a call.
            try:
                self.limiter.check(self._model, GeminiPurpose.DRAFT_EMAIL)
            except RateLimitedError as exc:
                log.warning(
                    "draft.halted",
                    reason=str(exc),
                    processed=processed,
                    run_id=ctx.run_id,
                )
                halted_reason = "rpd_exhausted"
                break

            # Soft RPM smoothing -- defence-in-depth on Pro's 2 RPM ceiling.
            wait = self.limiter.compute_rpm_wait_seconds(self._model)
            if wait and wait > 0:
                self.sleep_fn(min(wait + 0.1, 65.0))

            processed += 1
            to_recipients, cc_recipients, recipient_name = _resolve_recipients(phonebook)
            recipient_first_name = _first_name(recipient_name)

            # 1. Initial draft + validate. On ValidationError, one regen.
            try:
                draft_output = self._generate_and_validate(
                    purpose=GeminiPurpose.DRAFT_EMAIL,
                    prompt=build_draft_prompt(
                        self.prefs,
                        recipient_name=recipient_name,
                        recipient_email=to_recipients[0],
                        company_name=company.name,
                        role_title=job.title,
                        role_description=job.description or "",
                        apply_url=job.apply_url,
                    ),
                    job_id=job.id,
                    recipient_first_name=recipient_first_name,
                    apply_url=job.apply_url,
                    company_name=company.name,
                )
            except ValidationError as first_err:
                # 2. One regeneration with feedback.
                try:
                    draft_output = self._generate_and_validate(
                        purpose=GeminiPurpose.REGENERATE,
                        prompt=build_regenerate_prompt(
                            self.prefs,
                            recipient_name=recipient_name,
                            recipient_email=to_recipients[0],
                            company_name=company.name,
                            role_title=job.title,
                            role_description=job.description or "",
                            apply_url=job.apply_url,
                            previous_subject="(see reason)",
                            previous_body="(see reason)",
                            reason=str(first_err),
                        ),
                        job_id=job.id,
                        recipient_first_name=recipient_first_name,
                        apply_url=job.apply_url,
                        company_name=company.name,
                    )
                except (ValidationError, ExternalServiceError, ValueError) as second_err:
                    _mark_job_error(
                        self.session,
                        job=job,
                        message=f"validator failed twice: {second_err}",
                        run_id=ctx.run_id,
                    )
                    errors += 1
                    continue
            except (ExternalServiceError, ValueError) as exc:
                _mark_job_error(
                    self.session,
                    job=job,
                    message=f"gemini draft call failed: {exc}",
                    run_id=ctx.run_id,
                )
                errors += 1
                continue

            # 3. Load resume PDF. Missing key = tailor was skipped (data bug),
            # missing file = operator config bug; both go to ERROR.
            if not job.resume_variant_key:
                _mark_job_error(
                    self.session,
                    job=job,
                    message="resume_variant_key missing (skipped tailor?)",
                    run_id=ctx.run_id,
                )
                errors += 1
                continue
            try:
                pdf_bytes = self.load_resume_pdf(job.resume_variant_key)
            except FileNotFoundError as exc:
                _mark_job_error(
                    self.session,
                    job=job,
                    message=f"resume_pdf_missing: {exc}",
                    run_id=ctx.run_id,
                )
                errors += 1
                continue

            # 4. Create Gmail draft.
            gmail_spec = GmailDraftSpec(
                to_emails=to_recipients,
                cc_emails=cc_recipients,
                subject=draft_output.subject,
                body=draft_output.body,
                attachments=[
                    (
                        f"{job.resume_variant_key}.pdf",
                        pdf_bytes,
                        "application/pdf",
                    )
                ],
            )

            try:
                gmail_draft_id = self.gmail.create_draft(gmail_spec)
            except ExternalServiceError as exc:
                # Persist FAILED draft for visibility; leave job at TAILORED
                # for the next-run retry. Subject/body live on the row so
                # we don't burn another Pro call on the regenerated copy
                # if the failure was transient.
                self.session.add(
                    EmailDraft(
                        job_id=job.id,
                        gmail_draft_id=None,
                        subject=draft_output.subject,
                        body=draft_output.body,
                        to_recipients=to_recipients,
                        cc_recipients=cc_recipients,
                        state=EmailDraftState.FAILED,
                    )
                )
                job.last_error = f"gmail draft create failed: {exc}"
                job.retry_count = (job.retry_count or 0) + 1
                self.session.add(job)
                self.session.flush()
                errors += 1
                log.warning(
                    "draft.gmail_failed",
                    job_id=job.id,
                    error=str(exc),
                    run_id=ctx.run_id,
                )
                continue

            # 5. Persist DRAFT_CREATED row + advance job.
            self.session.add(
                EmailDraft(
                    job_id=job.id,
                    gmail_draft_id=gmail_draft_id,
                    subject=draft_output.subject,
                    body=draft_output.body,
                    to_recipients=to_recipients,
                    cc_recipients=cc_recipients,
                    state=EmailDraftState.DRAFT_CREATED,
                )
            )
            prev_status = job.status
            job.status = JobStatus.AWAITING_APPROVAL
            job.last_error = None
            self.session.add(job)
            self.session.add(
                JobApplicationEvent(
                    job_id=job.id,
                    from_status=prev_status,
                    to_status=JobStatus.AWAITING_APPROVAL,
                    note=f"draft created prompt={PROMPT_VERSION} variant={job.resume_variant_key}",
                    payload={
                        "run_id": ctx.run_id,
                        "variant_key": job.resume_variant_key,
                        "gmail_draft_id": gmail_draft_id,
                        "prompt_version": PROMPT_VERSION,
                    },
                )
            )
            self.session.flush()
            advanced += 1

        log.info(
            "draft.summary",
            processed=processed,
            advanced=advanced,
            errors=errors,
            halted=halted_reason is not None,
            run_id=ctx.run_id,
        )
        return StageResult(
            stage=self.name,
            processed=processed,
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
        job_id: int | None,
        recipient_first_name: str,
        apply_url: str,
        company_name: str,
    ) -> DraftOutput:
        """Call Pro, parse + validate, and write a ``GeminiCallLog`` row.

        Logs success=True only when validate passes. ValidationError /
        ValueError / ExternalServiceError all produce a success=False
        row and re-raise so the caller decides whether to regenerate or
        mark the job ERROR.
        """
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
                candidate_name=self.prefs.candidate.name,
                candidate_email=self.prefs.candidate.email,
                apply_url=apply_url,
                company_name=company_name,
                recipient_first_name=recipient_first_name,
            )
        except Exception as exc:
            # Catch-all on the call boundary: ValidationError, ValueError
            # (parse failures), ExternalServiceError (SDK failures), or
            # anything else the SDK might raise.  All are surfaced to the
            # caller after we record the failed attempt.
            latency_ms = int((time.monotonic() - started) * 1000)
            self.session.add(
                GeminiCallLog(
                    job_id=job_id,
                    model=self._model,
                    purpose=purpose,
                    prompt_tokens=response.tokens_input if response else 0,
                    completion_tokens=response.tokens_output if response else 0,
                    latency_ms=latency_ms,
                    success=False,
                    error_code=type(exc).__name__,
                )
            )
            self.session.flush()
            raise

        latency_ms = int((time.monotonic() - started) * 1000)
        self.session.add(
            GeminiCallLog(
                job_id=job_id,
                model=self._model,
                purpose=purpose,
                prompt_tokens=response.tokens_input if response else 0,
                completion_tokens=response.tokens_output if response else 0,
                latency_ms=latency_ms,
                success=True,
            )
        )
        self.session.flush()
        return draft


def _resolve_recipients(
    phonebook: PhonebookEntry,
) -> tuple[list[str], list[str] | None, str]:
    """Phase 6 contract: founder hit → To=[founder], Cc=[careers]; no founder
    → To=[careers], Cc=None. Returns ``(to_recipients, cc_recipients,
    recipient_name)``; ``recipient_name`` is empty when no founder is
    known so the prompt/validator both fall back to the neutral path.

    ``careers_email`` is guaranteed non-null by the phonebook lookup but
    we defensively coerce empties out for safety; this function is the
    only place that recipient assembly happens, so it owns the contract.
    """
    founder_email = (phonebook.founder_email or "").strip()
    careers_email = (phonebook.careers_email or "").strip()
    founder_name = (phonebook.founder_name or "").strip()

    if founder_email:
        # Cc the careers list when it's distinct from the founder address.
        cc: list[str] | None = (
            [careers_email] if careers_email and careers_email != founder_email else None
        )
        return [founder_email], cc, founder_name

    # No founder identified -- careers-only on To.
    if careers_email:
        return [careers_email], None, ""

    # Defensive: should never happen (enrich guarantees careers_email),
    # but raise rather than silently emailing nobody.
    raise ValueError(f"phonebook row {phonebook.id} has neither founder_email nor careers_email")


def _first_name(full_name: str) -> str:
    """Return the first whitespace-delimited token of ``full_name`` or
    ``""``. Used to feed ``validate_draft(recipient_first_name=...)``;
    empty string disables the greeting-personalisation check.
    """
    parts = (full_name or "").strip().split()
    return parts[0] if parts else ""


def _mark_job_error(
    session: Session,
    *,
    job: JobApplication,
    message: str,
    run_id: int,
) -> None:
    """Move a job to ERROR with ``last_error`` + ``retry_count`` bump and
    an audit event. Centralised so the FOUR error branches all produce
    identical row shapes.
    """
    prev_status = job.status
    job.last_error = message
    job.retry_count = (job.retry_count or 0) + 1
    job.status = JobStatus.ERROR
    session.add(job)
    session.add(
        JobApplicationEvent(
            job_id=job.id,
            from_status=prev_status,
            to_status=JobStatus.ERROR,
            note=message[:500],
            payload={"run_id": run_id},
        )
    )
    session.flush()
