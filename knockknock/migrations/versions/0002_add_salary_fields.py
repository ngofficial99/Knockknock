"""add salary fields to job_applications

Revision ID: 0002_add_salary_fields
Revises: 0001_initial_schema
Create Date: 2026-05-28 06:30:00.000000

Adds best-effort salary capture columns to ``job_applications``. All
columns are nullable because most sources (HN especially) don't expose
structured comp data — these are populated opportunistically by
``scrapers/_salary.py::parse_salary_from_text`` and used as a soft
scoring signal downstream, not as a hard pre-filter gate.

Columns:
* ``salary_min`` / ``salary_max``  — integer values in the *minor* unit
  expressed in the source currency (e.g. dollars, rupees). Not cents.
* ``salary_currency``  — ISO-ish 3-letter code: ``USD``/``INR``/``EUR``/``GBP``
* ``salary_period``    — ``annual`` / ``monthly`` / ``hourly``
* ``salary_raw``       — the substring the extractor matched, kept verbatim
  for auditing so the audit harness can spot bad regex matches.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_add_salary_fields"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_applications", sa.Column("salary_min", sa.BigInteger(), nullable=True))
    op.add_column("job_applications", sa.Column("salary_max", sa.BigInteger(), nullable=True))
    op.add_column("job_applications", sa.Column("salary_currency", sa.Text(), nullable=True))
    op.add_column("job_applications", sa.Column("salary_period", sa.Text(), nullable=True))
    op.add_column("job_applications", sa.Column("salary_raw", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_applications", "salary_raw")
    op.drop_column("job_applications", "salary_period")
    op.drop_column("job_applications", "salary_currency")
    op.drop_column("job_applications", "salary_max")
    op.drop_column("job_applications", "salary_min")
