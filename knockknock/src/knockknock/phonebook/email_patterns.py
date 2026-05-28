"""Deterministic email-pattern helpers. No I/O.

These two utilities are the *only* always-available pieces of the
phonebook chain: Apollo and Hunter can both fail (network, quota,
unknown company) but as long as we have a domain string we can write a
``careers@<domain>`` row so the enrich stage never leaves a job without
*something* to send to.

``guess_founder_email`` is best-effort. We err on the side of returning
``None`` -- a wrong founder address is worse than no founder address,
because the downstream draft stage will happily address an email to
``john.doe@`` of a company the founder doesn't work at.
"""

from __future__ import annotations

import re
import unicodedata

# Honorifics (prefix) and suffixes commonly attached to display names.
# Anything matching is dropped *before* the first/last split.
_NAME_PREFIX_RE = re.compile(r"^(dr|mr|mrs|ms|prof|sir|madam)\.?$", re.IGNORECASE)
_NAME_SUFFIX_RE = re.compile(r"^(jr|sr|ii|iii|iv|phd|md|mba|esq)\.?$", re.IGNORECASE)
# Middle-name initials like "K." -- single letter, optional trailing dot.
_INITIAL_RE = re.compile(r"^[a-z]\.?$", re.IGNORECASE)
_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def _strip_diacritics(value: str) -> str:
    """Drop diacritics (é → e). Keep base ASCII chars only.

    We pass the value through NFKD so combining marks become independent
    code points, then filter out anything in the Mn (mark, nonspacing)
    category.
    """
    normalised = unicodedata.normalize("NFKD", value)
    return "".join(c for c in normalised if not unicodedata.combining(c))


def _normalise_domain(raw: str) -> str:
    """Lowercase, strip scheme/path, strip leading ``www.``.

    The phonebook is keyed on this normalised form so we don't double-
    store ``www.acme.io`` and ``acme.io`` as different companies.
    """
    cleaned = raw.strip().lower()
    cleaned = _SCHEME_RE.sub("", cleaned)
    # Drop path: ``acme.io/jobs/123`` → ``acme.io``.
    cleaned = cleaned.split("/", 1)[0]
    if cleaned.startswith("www."):
        cleaned = cleaned[4:]
    return cleaned


def careers_fallback_email(domain: str) -> str:
    """Return ``careers@<normalised-domain>``. Raise on empty domain.

    This is the *last-resort* address used by the enrich stage when
    Apollo + Hunter + founder-guess have all returned nothing. We always
    have something to write into ``phonebook.careers_email`` so jobs
    with a known domain can never get stuck at ENRICH_FAILED for lack of
    a recipient.
    """
    norm = _normalise_domain(domain)
    if not norm:
        raise ValueError("careers_fallback_email requires a non-empty domain")
    return f"careers@{norm}"


def guess_founder_email(name: str, domain: str) -> str | None:
    """Heuristic ``first.last@domain`` guess. Returns ``None`` if unsafe.

    Refuses to guess when:
    - ``name`` is blank or collapses to a single token after stripping
      honorifics/suffixes/initials (so single-token founders like
      "Madonna" don't get false-positive guesses).
    - ``domain`` is blank.

    Diacritics are folded (``Renée`` → ``renee``) so the output is
    SMTP-safe ASCII.
    """
    if not domain.strip() or not name.strip():
        return None
    norm_domain = _normalise_domain(domain)
    if not norm_domain:
        return None

    # Tokenise on whitespace, drop honorifics/suffixes/initials.
    ascii_name = _strip_diacritics(name)
    tokens: list[str] = []
    for raw_tok in ascii_name.split():
        # Strip surrounding punctuation but keep the token's letters.
        tok = re.sub(r"[^A-Za-z]", "", raw_tok)
        if not tok:
            continue
        if _NAME_PREFIX_RE.fullmatch(tok) or _NAME_SUFFIX_RE.fullmatch(tok):
            continue
        if _INITIAL_RE.fullmatch(tok):
            continue
        tokens.append(tok.lower())

    if len(tokens) < 2:
        return None
    # Use first and last token; ignore any middle names.
    first, last = tokens[0], tokens[-1]
    return f"{first}.{last}@{norm_domain}"
