from __future__ import annotations

from sqlmodel import SQLModel

from knockknock.db import models


def test_all_tables_registered() -> None:
    expected = {
        "companies",
        "companies_blacklist",
        "phonebook",
        "job_applications",
        "job_application_events",
        "email_drafts",
        "gemini_call_logs",
        "telegram_messages",
        "pipeline_runs",
        "scraper_states",
    }
    actual = set(SQLModel.metadata.tables.keys())
    assert expected.issubset(actual), f"missing tables: {expected - actual}"


def test_job_application_has_company_fk() -> None:
    table = SQLModel.metadata.tables["job_applications"]
    fks = {fk.column.table.name for fk in table.foreign_keys}
    assert "companies" in fks


def test_email_draft_has_job_fk() -> None:
    table = SQLModel.metadata.tables["email_drafts"]
    fks = {fk.column.table.name for fk in table.foreign_keys}
    assert "job_applications" in fks


def test_company_models_imported() -> None:
    assert models.Company.__tablename__ == "companies"
    assert models.JobApplication.__tablename__ == "job_applications"


def test_email_draft_state_default_is_generated() -> None:
    """Errata E.2: EmailDraftState.GENERATED is the initial state."""
    table = SQLModel.metadata.tables["email_drafts"]
    state_col = table.c.state
    # server_default is set to the GENERATED enum value
    assert state_col.server_default is not None
    assert "GENERATED" in str(state_col.server_default.arg)


def test_active_draft_partial_index_uses_generated_predicate() -> None:
    """Active-draft partial index must reference GENERATED (not PENDING)."""
    table = SQLModel.metadata.tables["email_drafts"]
    idx = next(i for i in table.indexes if i.name == "ix_email_drafts_active_per_job")
    where = idx.dialect_options.get("postgresql", {}).get("where")
    assert where is not None
    assert "GENERATED" in str(where) or "DRAFT_CREATED" in str(where)
