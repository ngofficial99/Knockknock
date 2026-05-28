"""Value objects passed between lookup providers and the enrich stage.

Kept small and immutable so providers can return them across threads
(future) without worrying about mutation. The enrich stage persists a
``LookupOutcome`` into the ``phonebook`` table in one transaction, so
the type names map 1:1 onto columns:

- ``founder_name`` / ``founder_email`` → ``phonebook.founder_*``
- ``careers_email`` → ``phonebook.careers_email`` (always non-null)
- ``source`` → ``phonebook.source`` (the *best* signal seen; if any
  provider returned a real founder hit, ``source`` is that provider;
  otherwise ``PATTERN_GUESS`` or ``CACHE``).
"""

from __future__ import annotations

from dataclasses import dataclass

from knockknock.db.enums import PhonebookSource


@dataclass(frozen=True, slots=True)
class FounderHit:
    """Single founder candidate emitted by a lookup provider.

    ``email`` may be ``None`` when the provider returns a name but no
    contact (Apollo's free tier often does this). The chain may then
    feed the name into ``guess_founder_email`` to derive an address.
    """

    name: str
    email: str | None
    source: PhonebookSource


@dataclass(frozen=True, slots=True)
class LookupOutcome:
    """Aggregated result of a phonebook lookup chain for one company.

    Invariants:
    - ``careers_email`` is always set; the fallback ``careers@<domain>``
      guarantees it.
    - ``founder_name`` and ``founder_email`` may both be ``None`` when
      no provider returned a founder.
    - ``source`` reflects the *best* signal: provider that returned a
      real founder (APOLLO/HUNTER), or the deterministic source
      (PATTERN_GUESS/CACHE) otherwise.
    """

    founder_name: str | None
    founder_email: str | None
    careers_email: str
    source: PhonebookSource
