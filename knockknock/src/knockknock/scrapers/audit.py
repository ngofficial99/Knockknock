"""Field-level data-quality audit harness for scrapers.

Use this to answer "is my scraper producing good leads?" without standing
up the whole pipeline. The audit runs a scraper N times, persists each
run's raw output as JSONL for forensic review, and emits a structured
report (Markdown + JSON) summarising per-field quality.

It is *intentionally* schema-aware — it knows what each ``ScrapedJob``
field should look like, so it can flag e.g. a ``title`` field that looks
suspiciously like a location ("Berlin, Germany"), or an ``apply_url``
that doesn't resolve.

Cross-run regression detection: if a field's null rate drifts by more
than ``NULL_RATE_DRIFT_THRESHOLD`` between consecutive runs, that field
is flagged WARN. This is the early-warning signal for "the scraper used
to extract X and now doesn't".

The audit is purely read-side: no DB writes, no Gemini calls, no email
side-effects. Safe to run repeatedly.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass
from dataclasses import field as dc_field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from knockknock.scrapers.base import ScrapedJob, Scraper

# Fields we inspect per-run. Listed explicitly (rather than reflectively)
# so we can attach per-field quality heuristics below.
INSPECTED_FIELDS = (
    "source_job_id",
    "company_name",
    "company_domain",
    "title",
    "location",
    "apply_url",
    "description",
    "posted_at",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "salary_raw",
)

# A field's null rate drifting by more than this between consecutive
# runs flags as a regression.
NULL_RATE_DRIFT_THRESHOLD = 0.10  # 10pp

# Heuristic: tokens that suggest a value is actually a *location*, used
# to flag job.title values like "Berlin, Germany" that the HN scraper
# currently emits when a header lacks a role segment.
_LOCATION_HINTS = (
    "remote",
    "onsite",
    "hybrid",
    "san francisco",
    "new york",
    "berlin",
    "london",
    "bangalore",
    "bengaluru",
    "tokyo",
    "singapore",
    "amsterdam",
    " ca",
    " ny",
    " uk",
    " usa",
    " india",
)


@dataclass(frozen=True, slots=True)
class FieldStats:
    """Per-field summary across a single run."""

    name: str
    null_count: int
    total_count: int
    distinct_count: int
    min_len: int | None  # for text fields
    max_len: int | None
    mean_len: float | None

    @property
    def null_rate(self) -> float:
        return self.null_count / self.total_count if self.total_count else 0.0


@dataclass(frozen=True, slots=True)
class AuditFinding:
    """A single quality concern surfaced by the audit."""

    severity: str  # "WARN" | "ERROR"
    field: str
    message: str
    examples: list[str] = dc_field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RunReport:
    """Summary of a single audit run."""

    run_index: int
    started_at: str
    finished_at: str
    source: str
    jobs_emitted: int
    fields: dict[str, FieldStats]
    findings: list[AuditFinding]


@dataclass(frozen=True, slots=True)
class AuditReport:
    """Aggregate report across N audit runs."""

    source: str
    runs: list[RunReport]
    cross_run_findings: list[AuditFinding]


def _looks_like_location(value: str) -> bool:
    """Heuristic: does this string look more like a location than a role?"""
    lower = value.lower()
    return any(h in lower for h in _LOCATION_HINTS)


def _is_url_well_formed(value: str) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _compute_field_stats(name: str, values: list[Any]) -> FieldStats:
    """Build per-field stats. None and empty-string both count as null."""
    null_count = sum(1 for v in values if v is None or (isinstance(v, str) and not v.strip()))
    non_null = [v for v in values if v is not None and not (isinstance(v, str) and not v.strip())]
    distinct = len({str(v) for v in non_null})
    lengths = [len(str(v)) for v in non_null]
    return FieldStats(
        name=name,
        null_count=null_count,
        total_count=len(values),
        distinct_count=distinct,
        min_len=min(lengths) if lengths else None,
        max_len=max(lengths) if lengths else None,
        mean_len=statistics.mean(lengths) if lengths else None,
    )


def _heuristic_findings(jobs: list[ScrapedJob]) -> list[AuditFinding]:
    """Surface specific data-quality problems we know how to spot."""
    out: list[AuditFinding] = []

    # 1. Titles that look like locations (the bug we just observed
    #    with HN headers that omit the role segment).
    title_loc_examples = [j.title for j in jobs if _looks_like_location(j.title)][:5]
    if title_loc_examples:
        out.append(
            AuditFinding(
                severity="WARN",
                field="title",
                message=(
                    f"{len(title_loc_examples)} job(s) have a title that looks "
                    "like a location — header parser may have grabbed the wrong segment."
                ),
                examples=title_loc_examples,
            )
        )

    # 2. Malformed apply URLs.
    bad_urls = [j.apply_url for j in jobs if not _is_url_well_formed(j.apply_url)][:5]
    if bad_urls:
        out.append(
            AuditFinding(
                severity="ERROR",
                field="apply_url",
                message=f"{len(bad_urls)} job(s) have a malformed apply_url.",
                examples=bad_urls,
            )
        )

    # 3. Duplicate source_job_ids inside a single run (idempotency
    #    bug detector — the discover-stage dedupe wouldn't run here).
    seen_ids: set[str] = set()
    dupes: list[str] = []
    for j in jobs:
        if j.source_job_id in seen_ids:
            dupes.append(j.source_job_id)
        seen_ids.add(j.source_job_id)
    if dupes:
        out.append(
            AuditFinding(
                severity="ERROR",
                field="source_job_id",
                message=f"{len(dupes)} duplicate source_job_id(s) within a single fetch.",
                examples=dupes[:5],
            )
        )

    # 4. company_domain == known generic redirector (e.g. linkedin.com,
    #    bit.ly) — these are usually wrong because they reflect the
    #    *application form* host, not the company host.
    generic_hosts = {"bit.ly", "lnkd.in", "tinyurl.com", "goo.gl", "linkedin.com"}
    bad_domains = [j.company_domain for j in jobs if j.company_domain in generic_hosts][:5]
    if bad_domains:
        out.append(
            AuditFinding(
                severity="WARN",
                field="company_domain",
                message=(
                    f"{len(bad_domains)} job(s) resolved to a generic redirector "
                    "host — company_domain may be wrong."
                ),
                examples=bad_domains,
            )
        )

    # 5. Posted-at recency. Anything posted >180 days ago in a "current"
    #    Who-is-hiring thread is a strong signal we're on a stale thread.
    if jobs:
        now = datetime.now(UTC)
        too_old = [j for j in jobs if j.posted_at and (now - j.posted_at).days > 180]
        if len(too_old) >= len(jobs) // 2 and too_old:
            out.append(
                AuditFinding(
                    severity="WARN",
                    field="posted_at",
                    message=(
                        f"{len(too_old)}/{len(jobs)} job(s) are older than 180 days — "
                        "scraper may be pulling stale threads."
                    ),
                    examples=[j.posted_at.isoformat() if j.posted_at else "" for j in too_old[:3]],
                )
            )

    return out


def _serialise_job(job: ScrapedJob) -> dict[str, Any]:
    """JSON-serialisable view of a ScrapedJob."""
    data = asdict(job)
    # Enums and datetimes need explicit coercion for the JSON layer.
    data["source"] = job.source.value
    data["company_size_bucket"] = job.company_size_bucket.value
    data["posted_at"] = job.posted_at.isoformat() if job.posted_at else None
    return data


def _audit_single_run(scraper: Scraper, run_index: int, output_dir: Path, source: str) -> RunReport:
    """Run the scraper once, dump JSONL, compute the run-level report."""
    started_at = datetime.now(UTC)
    jobs = list(scraper.fetch())
    finished_at = datetime.now(UTC)

    # Dump raw payloads to JSONL so we can forensically re-inspect.
    jsonl_path = (
        output_dir / f"{source}-run{run_index}-{started_at.strftime('%Y%m%dT%H%M%S')}.jsonl"
    )
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for job in jobs:
            fh.write(json.dumps(_serialise_job(job), ensure_ascii=False) + "\n")

    fields: dict[str, FieldStats] = {}
    for name in INSPECTED_FIELDS:
        values = [getattr(job, name, None) for job in jobs]
        fields[name] = _compute_field_stats(name, values)

    findings = _heuristic_findings(jobs)

    return RunReport(
        run_index=run_index,
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        source=source,
        jobs_emitted=len(jobs),
        fields=fields,
        findings=findings,
    )


def _cross_run_findings(runs: list[RunReport]) -> list[AuditFinding]:
    """Flag fields whose null-rate drifts >threshold between consecutive runs."""
    out: list[AuditFinding] = []
    if len(runs) < 2:
        return out
    for name in INSPECTED_FIELDS:
        rates = [r.fields[name].null_rate for r in runs]
        for i in range(1, len(rates)):
            drift = abs(rates[i] - rates[i - 1])
            if drift > NULL_RATE_DRIFT_THRESHOLD:
                out.append(
                    AuditFinding(
                        severity="WARN",
                        field=name,
                        message=(
                            f"null-rate drifted {drift:.0%} between run {i - 1} "
                            f"({rates[i - 1]:.0%}) and run {i} ({rates[i]:.0%})"
                        ),
                    )
                )
                break  # one finding per field is enough
    return out


def _render_markdown(report: AuditReport) -> str:
    """Human-readable report. Pasted into the audit-output directory."""
    lines: list[str] = [
        f"# Scraper Audit — `{report.source}`",
        "",
        f"Runs: **{len(report.runs)}**",
        "",
    ]

    for run in report.runs:
        lines += [
            f"## Run {run.run_index}  ({run.started_at} → {run.finished_at})",
            f"Jobs emitted: **{run.jobs_emitted}**",
            "",
            "| field | null rate | distinct | min len | mean len | max len |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for name in INSPECTED_FIELDS:
            s = run.fields[name]
            mean = f"{s.mean_len:.0f}" if s.mean_len is not None else "-"
            lines.append(
                f"| `{name}` | {s.null_rate:.0%} | {s.distinct_count} "
                f"| {s.min_len if s.min_len is not None else '-'} "
                f"| {mean} "
                f"| {s.max_len if s.max_len is not None else '-'} |"
            )
        lines.append("")
        if run.findings:
            lines.append("### Findings")
            for f_ in run.findings:
                lines.append(f"- **{f_.severity}** `{f_.field}` — {f_.message}")
                for ex in f_.examples:
                    lines.append(f"  - example: `{ex!r}`")
            lines.append("")
        else:
            lines.append("_No per-run findings._")
            lines.append("")

    if report.cross_run_findings:
        lines.append("## Cross-run regressions")
        for f_ in report.cross_run_findings:
            lines.append(f"- **{f_.severity}** `{f_.field}` — {f_.message}")
        lines.append("")
    else:
        lines.append("## Cross-run regressions")
        lines.append("_None._")
        lines.append("")

    return "\n".join(lines)


def _report_to_json(report: AuditReport) -> dict[str, Any]:
    """Machine-readable view of the report."""
    return {
        "source": report.source,
        "runs": [
            {
                "run_index": r.run_index,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "jobs_emitted": r.jobs_emitted,
                "fields": {
                    name: {
                        "null_count": fs.null_count,
                        "total_count": fs.total_count,
                        "null_rate": fs.null_rate,
                        "distinct_count": fs.distinct_count,
                        "min_len": fs.min_len,
                        "mean_len": fs.mean_len,
                        "max_len": fs.max_len,
                    }
                    for name, fs in r.fields.items()
                },
                "findings": [asdict(f_) for f_ in r.findings],
            }
            for r in report.runs
        ],
        "cross_run_findings": [asdict(f_) for f_ in report.cross_run_findings],
    }


def audit_scraper(scraper: Scraper, runs: int, output_dir: Path) -> AuditReport:
    """Run ``scraper`` ``runs`` times and emit reports under ``output_dir``.

    Writes ``<source>-runN-<ts>.jsonl`` per run plus
    ``<source>-<ts>-report.md`` and ``<source>-<ts>-report.json`` for
    the aggregate report. Returns the in-memory ``AuditReport`` so
    callers (tests, CLI) can inspect without re-parsing files.
    """
    if runs < 1:
        raise ValueError("runs must be >= 1")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Lowercase the source slug for filesystem-friendly artefact names
    # (enum values like "HN" become "hn-run0-...jsonl"). The JSON payload
    # itself keeps the canonical enum value via _serialise_job.
    source = scraper.source.value.lower()
    run_reports: list[RunReport] = []
    for i in range(runs):
        run_reports.append(_audit_single_run(scraper, i, output_dir, source))

    report = AuditReport(
        source=source,
        runs=run_reports,
        cross_run_findings=_cross_run_findings(run_reports),
    )

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    md_path = output_dir / f"{source}-{ts}-report.md"
    json_path = output_dir / f"{source}-{ts}-report.json"
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    json_path.write_text(json.dumps(_report_to_json(report), indent=2), encoding="utf-8")

    return report
