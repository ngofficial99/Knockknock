"""Score prompt template + builder for Gemini 2.5 Flash.

The template is intentionally short and rubric-driven so the model
output is highly structured and the per-call token cost stays low
(~600 input tokens + ~150 output tokens at the time of writing).

Key invariants:

- ``PROMPT_VERSION`` is the audit-trail key. Persisted in event payloads
  so historic scores can be re-evaluated against template changes.
- ``_DESCRIPTION_CHAR_LIMIT`` keeps prompts comfortably under the 1M TPM
  budget even when 50+ jobs are scored in a single pipeline run.
- All preference fields referenced here come from ``JobPreferences`` as
  shipped in Phase 2 (``prefs.target.locations``,
  ``prefs.target.seniority_allow``, ``prefs.target.company_size_allow``,
  ``prefs.skills.must_have_any``, ``prefs.skills.nice_to_have``). The
  template MUST NOT invent new prefs attributes -- if a new dimension is
  needed, add it to ``preferences.py`` first.
"""

from __future__ import annotations

from knockknock.config.preferences import JobPreferences

# Bump this whenever the template, rubric, or response schema changes in
# a way that would invalidate score comparisons across runs. Persisted
# in ``job_application_events.payload`` for auditability.
PROMPT_VERSION = "score-v1"

SYSTEM_INSTRUCTION = (
    "You are a senior engineering recruiter screening backend jobs for a single "
    "candidate. Be ruthlessly objective. Output strict JSON only -- no prose, "
    "no markdown fences."
)

# 12k chars ~= 3-4k tokens; plenty for HN posts and JD bodies while keeping
# headroom for the rubric, candidate context, and response.
_DESCRIPTION_CHAR_LIMIT = 12_000

_TEMPLATE = """\
Candidate profile:
- Current role: {current_role} ({years_experience}y experience)
- Based in: {candidate_location}
- Target seniority levels: {seniority}
- Target locations: {locations}
- Must-have skills (at least one required): {must_have}
- Nice-to-have skills (boosts score): {nice_to_have}
- Acceptable company stages: {company_stages}

Scoring rubric (integer 1..10):
- 9-10: strong match across must-have skills, seniority, location, stage.
- 7-8: solid backend role with most must-haves; seniority + location OK.
- 5-6: marginal fit -- weak must-have coverage OR mismatched seniority.
- 3-4: poor fit (wrong seniority, wrong stack, off-target role).
- 1-2: clearly off-target (frontend-only, manager track, wrong domain).

Job:
- Company: {company}
- Title: {title}
- Location: {location}
- Description (truncated):
\"\"\"
{description}
\"\"\"

Return JSON with EXACT keys (no additional fields, no markdown fences):
{{
  "score": <integer 1..10>,
  "rationale": "<one-sentence reason, max 200 chars>",
  "matched_must_have": [<skill,...>],
  "matched_nice_to_have": [<skill,...>],
  "seniority_match": <bool>,
  "company_stage_match": <bool>
}}
"""


def build_score_prompt(
    prefs: JobPreferences,
    *,
    company_name: str,
    role_title: str,
    location: str,
    description: str,
) -> str:
    """Render the score prompt for a single job."""
    desc = (description or "").strip()
    if len(desc) > _DESCRIPTION_CHAR_LIMIT:
        desc = desc[:_DESCRIPTION_CHAR_LIMIT] + "\n…[truncated]"
    elif not desc:
        desc = "(no description provided)"

    return _TEMPLATE.format(
        current_role=prefs.candidate.current_role,
        years_experience=prefs.candidate.years_experience,
        candidate_location=prefs.candidate.location,
        seniority=", ".join(prefs.target.seniority_allow),
        locations=", ".join(prefs.target.locations),
        must_have=", ".join(prefs.skills.must_have_any),
        nice_to_have=", ".join(prefs.skills.nice_to_have),
        company_stages=", ".join(s.value for s in prefs.target.company_size_allow),
        company=company_name,
        title=role_title,
        location=location or "unspecified",
        description=desc,
    )
