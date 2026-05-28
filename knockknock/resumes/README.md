# Resume variants

Place your hand-built PDFs in this folder and register them in `manifest.yaml`.

- `key` is a stable id; do not rename without updating any `email_drafts` that already point at it.
- `tags` should be lowercase canonical tokens. Use `tag_dictionary` to declare synonyms.
- `priority` breaks ties; higher beats lower. Use 100 for "most-targeted" and 50 for "generic".

PDFs are NEVER committed to git (`*.pdf` is in `.gitignore`); only `manifest.yaml` and
this README are tracked. Replace the seed entries with your real variants before going
live.

## Selector rules (see `src/knockknock/resume/selector.py`)

- Score = `|variant.tags ∩ job_tokens| + priority/1000`.
- Overlap dominates; priority is only a tie-breaker.
- If no variant has any overlap, the highest-priority variant is returned as a
  safe default. Authors MUST keep at least one broad/generic variant so the
  selector never has to return None for an in-scope role.
