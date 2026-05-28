"""Resume manifest loader: YAML → typed value objects.

The manifest is the contract between the human-curated PDF library
(``resumes/*.pdf``) and the deterministic selector. Two design rules
guard against the worst failure modes:

1. **Loud at load time.** Duplicate keys, missing ``pdf_path``, or an
   empty ``variants`` list are :class:`ValueError`. We refuse to start
   the pipeline with a broken manifest because a silently-missing
   variant key would mis-route every job downstream.

2. **Tags are lowercase tokens.** All tag comparisons happen in
   lowercase canonical space (see :meth:`canonicalise_token`). We
   normalise at load time so the hot path doesn't have to.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class ResumeVariant:
    """One row in the manifest. PDF lives at ``resumes/<pdf_path>``."""

    key: str
    pdf_path: str  # relative to resumes/
    tags: frozenset[str]
    priority: int


@dataclass(frozen=True, slots=True)
class ResumeManifest:
    """In-memory representation of ``resumes/manifest.yaml``."""

    variants: tuple[ResumeVariant, ...]
    tag_dictionary: dict[str, str]  # synonym (lowercase) → canonical (lowercase)

    def canonicalise_token(self, token: str) -> str:
        """Apply synonym → canonical mapping; pass through unknown tokens.

        ``"K8s"`` → ``"kubernetes"`` if the dictionary defines it,
        otherwise lowercased input is returned unchanged.
        """
        normalised = token.strip().lower()
        return self.tag_dictionary.get(normalised, normalised)

    def variant_by_key(self, key: str) -> ResumeVariant | None:
        """Linear scan; manifest is tiny (< 50 entries) so this is fine."""
        for variant in self.variants:
            if variant.key == key:
                return variant
        return None


def load_manifest(path: Path) -> ResumeManifest:
    """Parse a manifest YAML; raise :class:`ValueError` on structural problems.

    Validation rules:
    - top-level must be a mapping
    - ``variants`` must be a non-empty list of mappings
    - every variant must have a non-empty ``key`` and ``pdf_path``
    - duplicate ``key`` is rejected (silent override → mis-routed jobs)
    - ``priority`` defaults to 0 when omitted; non-int values raise
    """
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"Manifest {path} must be a mapping at the top level.")

    raw_variants = raw.get("variants") or []
    if not isinstance(raw_variants, list) or not raw_variants:
        raise ValueError(f"Manifest {path} must declare at least one variant.")

    raw_dict = raw.get("tag_dictionary") or {}
    if not isinstance(raw_dict, dict):
        raise ValueError(f"Manifest {path}: `tag_dictionary` must be a mapping.")
    tag_dictionary: dict[str, str] = {
        str(syn).strip().lower(): str(canon).strip().lower() for syn, canon in raw_dict.items()
    }

    seen: set[str] = set()
    variants: list[ResumeVariant] = []
    for idx, entry in enumerate(raw_variants):
        if not isinstance(entry, dict):
            raise ValueError(f"Manifest {path}: variant #{idx} is not a mapping.")
        key = str(entry.get("key") or "").strip()
        if not key:
            raise ValueError(f"Manifest {path}: variant #{idx} missing 'key'.")
        if key in seen:
            raise ValueError(f"Manifest {path}: duplicate variant key {key!r}.")
        seen.add(key)
        pdf_path = str(entry.get("pdf_path") or "").strip()
        if not pdf_path:
            raise ValueError(f"Manifest {path}: variant {key!r} missing 'pdf_path'.")
        raw_tags = entry.get("tags") or []
        if not isinstance(raw_tags, list):
            raise ValueError(f"Manifest {path}: variant {key!r} 'tags' must be a list.")
        tags = frozenset(str(t).strip().lower() for t in raw_tags if str(t).strip())
        priority_raw = entry.get("priority", 0)
        try:
            priority = int(priority_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Manifest {path}: variant {key!r} priority not an int: {priority_raw!r}"
            ) from exc
        variants.append(ResumeVariant(key=key, pdf_path=pdf_path, tags=tags, priority=priority))

    return ResumeManifest(variants=tuple(variants), tag_dictionary=tag_dictionary)
