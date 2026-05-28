"""add score_rationale to job_applications

Revision ID: 0003_add_score_rationale
Revises: 0002_add_salary_fields
Create Date: 2026-05-28 15:00:00.000000

Adds a single nullable ``score_rationale`` column to ``job_applications``.

The Phase-5 score stage receives a one-sentence rationale alongside the
1..10 score from Gemini Flash. Storing it on the job row (rather than only
in ``job_application_events.payload``) makes the daily digest + Telegram
preview cheap to render -- no JOIN to the events table.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_add_score_rationale"
down_revision = "0002_add_salary_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_applications", sa.Column("score_rationale", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_applications", "score_rationale")
