"""Unit tests for the resume-manifest loader.

The manifest is the contract between the human-curated PDF library and
the deterministic selector. Structural errors must surface as
``ValueError`` at load time (loud, early) rather than as wrong-variant
picks at runtime.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knockknock.resume.manifest import ResumeManifest, load_manifest


def _write(tmp_path: Path, yaml_text: str) -> Path:
    p = tmp_path / "manifest.yaml"
    p.write_text(yaml_text)
    return p


def test_load_manifest_parses_seed() -> None:
    """The shipped seed file MUST parse so a fresh clone is functional."""
    manifest = load_manifest(Path("resumes/manifest.yaml"))
    assert isinstance(manifest, ResumeManifest)
    assert len(manifest.variants) >= 1
    keys = {v.key for v in manifest.variants}
    assert "backend-generic" in keys


def test_load_manifest_normalises_tags_to_lowercase(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
tag_dictionary: {}
variants:
  - key: A
    pdf_path: a.pdf
    priority: 10
    tags: [Backend, PYTHON]
        """,
    )
    manifest = load_manifest(path)
    assert manifest.variants[0].tags == frozenset({"backend", "python"})


def test_load_manifest_rejects_duplicate_keys(tmp_path: Path) -> None:
    """Duplicate keys would shadow each other and silently mis-route jobs."""
    path = _write(
        tmp_path,
        """
tag_dictionary: {}
variants:
  - { key: x, pdf_path: x.pdf, priority: 1, tags: [a] }
  - { key: x, pdf_path: y.pdf, priority: 2, tags: [b] }
        """,
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_manifest(path)


def test_load_manifest_rejects_empty_variants(tmp_path: Path) -> None:
    """An empty manifest can never produce a variant pick — fail loud."""
    path = _write(tmp_path, "tag_dictionary: {}\nvariants: []\n")
    with pytest.raises(ValueError, match="at least one"):
        load_manifest(path)


def test_canonicalise_tag_uses_dictionary(tmp_path: Path) -> None:
    """Synonyms apply at lookup; unknown tokens pass through unchanged."""
    path = _write(
        tmp_path,
        """
tag_dictionary:
  k8s: kubernetes
  golang: go
variants:
  - { key: g, pdf_path: g.pdf, priority: 1, tags: [backend] }
        """,
    )
    manifest = load_manifest(path)
    assert manifest.canonicalise_token("K8s") == "kubernetes"
    assert manifest.canonicalise_token("Golang") == "go"
    assert manifest.canonicalise_token("python") == "python"


def test_load_manifest_rejects_missing_pdf_path(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
tag_dictionary: {}
variants:
  - { key: x, priority: 1, tags: [a] }
        """,
    )
    with pytest.raises(ValueError, match="pdf_path"):
        load_manifest(path)


def test_variant_by_key_returns_match_or_none(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
tag_dictionary: {}
variants:
  - { key: foo, pdf_path: foo.pdf, priority: 1, tags: [a] }
        """,
    )
    manifest = load_manifest(path)
    found = manifest.variant_by_key("foo")
    assert found is not None and found.key == "foo"
    assert manifest.variant_by_key("missing") is None
