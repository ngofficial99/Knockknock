"""Email-drafting prompts for Gemini 2.5 Pro.

Two templates: initial draft (``build_draft_prompt``) and regeneration
(``build_regenerate_prompt``). The regenerator feeds Pro the previous
output and the validator's rejection reason so the rewrite is targeted
rather than a blind resample -- per spec we only get *one* rewrite
before the row halts.

Contract drift from the original Phase 8 spec, intentional:

- ``EmailDraft.body`` is a single field, so Pro emits
  ``{"subject", "body"}`` -- no separate HTML payload.
- Gmail attaches the signature server-side, so the prompt explicitly
  forbids generating a sign-off block. Without this rule the email
  would be double-stamped.
- ``Candidate`` model uses ``name`` (not ``full_name``) and has no
  ``seniority_levels`` field; we draw seniority signal from the
  candidate's existing ``current_role`` + ``years_experience`` and
  the ``target.seniority_allow`` preference list.
"""

from __future__ import annotations

from knockknock.config.preferences import JobPreferences

PROMPT_VERSION = "draft-v1"

#: Hard rules sent as Gemini ``system_instruction``. The validator
#: enforces these *after* generation; this string is the first line of
#: defence and is deliberately blunt because Pro will obey explicit
#: prohibitions more reliably than implicit ones.
SYSTEM_INSTRUCTION = (
    "You are drafting a short, plain, professional cold email for a single "
    "named candidate to send to a startup founder or hiring manager. "
    "The candidate is real; the recipient is real. Write as the candidate, "
    "in first person. Output strict JSON only. "
    "NEVER use placeholder text like [Your Name], [Company], [Role], or any "
    "square-bracketed token. "
    "NEVER mention you are an AI, language model, assistant, or chatbot. "
    "Only reference URLs and email addresses explicitly provided in the "
    "prompt -- do not invent any. "
    "DO NOT include a sign-off, signature, name, contact details, or email "
    "address in the body. The candidate's Gmail account attaches a signature "
    "automatically; adding one in the body would double-stamp the email."
)

# Pro's TPM ceiling is 32k. A 25k-char final prompt keeps comfortable
# headroom for the system instruction + response tokens.
_DESCRIPTION_CHAR_LIMIT = 10_000

_DRAFT_TEMPLATE = """\
Candidate:
- Name: {candidate_name}
- Current role: {candidate_role}
- Years of experience: {candidate_years}
- Location: {candidate_location}
- Elevator pitch: {candidate_pitch}
- Seniority targets (from preferences): {seniority}

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
- Tone: warm, direct, confident, no fluff. Two short paragraphs.
- 90-160 words in the body. Subject 4-10 words.
- Paragraph 1: address the recipient using "{greeting_hint}". Name the
  role. Give ONE specific reason this candidate is a good fit, grounded
  in the role description.
- Paragraph 2: ONE concrete recent achievement (1-2 sentences) drawn
  from the candidate's current role/pitch + a soft CTA
  (resume attached, happy to chat).
- Reference {apply_url} at most once, naturally, only if a link is
  genuinely useful in-line.
- DO NOT include a sign-off, name, or contact line. Gmail appends
  the candidate's signature automatically.

Output strict JSON with EXACT keys:
{{
  "subject": "<subject line>",
  "body": "<plain-text body, with \\n line breaks; no sign-off>"
}}
"""

_REGEN_TEMPLATE = (
    _DRAFT_TEMPLATE
    + """

Your previous attempt was REJECTED for this reason:
\"\"\"
{reason}
\"\"\"

Previous subject: {previous_subject}
Previous body:
\"\"\"
{previous_body}
\"\"\"

Write a corrected draft. Same JSON shape. Same constraints. Fix the \
specific issue stated above.
"""
)


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if not text:
        return "(no description provided)"
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…[truncated]"


def _candidate_pitch(prefs: JobPreferences) -> str:
    """Pitch line for the prompt. Falls back to a synthesised one-liner
    when the optional ``pitch`` field is absent so the prompt still has
    a usable elevator-pitch surface."""
    pitch = prefs.candidate.pitch
    if pitch:
        return pitch
    return (
        f"{prefs.candidate.current_role} with "
        f"{prefs.candidate.years_experience} years of experience."
    )


def _greeting_hint(recipient_name: str, company_name: str) -> str:
    """Phase 6 emits an empty ``recipient_name`` when only
    ``careers_email`` is set (no founder identified). Steer Pro to a
    neutral greeting in that case rather than letting it fabricate
    a name."""
    if recipient_name.strip():
        return f"Hi {recipient_name.strip().split()[0]}"
    return f"Hi {company_name} team"


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
        candidate_name=prefs.candidate.name,
        candidate_role=prefs.candidate.current_role,
        candidate_years=prefs.candidate.years_experience,
        candidate_location=prefs.candidate.location,
        candidate_pitch=_candidate_pitch(prefs),
        seniority=", ".join(prefs.target.seniority_allow),
        recipient_name=recipient_name or "(no specific recipient identified)",
        recipient_email=recipient_email,
        company_name=company_name,
        role_title=role_title,
        role_description=_truncate(role_description, _DESCRIPTION_CHAR_LIMIT),
        apply_url=apply_url,
        greeting_hint=_greeting_hint(recipient_name, company_name),
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
        candidate_name=prefs.candidate.name,
        candidate_role=prefs.candidate.current_role,
        candidate_years=prefs.candidate.years_experience,
        candidate_location=prefs.candidate.location,
        candidate_pitch=_candidate_pitch(prefs),
        seniority=", ".join(prefs.target.seniority_allow),
        recipient_name=recipient_name or "(no specific recipient identified)",
        recipient_email=recipient_email,
        company_name=company_name,
        role_title=role_title,
        role_description=_truncate(role_description, _DESCRIPTION_CHAR_LIMIT),
        apply_url=apply_url,
        greeting_hint=_greeting_hint(recipient_name, company_name),
        previous_subject=previous_subject,
        previous_body=previous_body,
        reason=reason,
    )
