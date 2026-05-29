"""phase 9 schema: APPROVED job_status + telegram_messages.email_draft_id

Revision ID: 0004_phase9_schema
Revises: 0003_add_score_rationale
Create Date: 2026-05-29 10:00:00.000000

Two schema changes for Phase 9 (Telegram bot + DB-mediated approval):

1. Extend the existing ``job_status`` Postgres enum with the value
   ``APPROVED``. Sits between ``AWAITING_APPROVAL`` (Telegram card sent,
   waiting for a tap) and ``SENT`` (Gmail accepted the send). The
   Telegram webhook handler is the only writer that performs the
   ``AWAITING_APPROVAL -> APPROVED`` transition; the pipeline's
   ``SendStage`` performs the ``APPROVED -> SENT`` transition.

2. Add a nullable ``email_draft_id`` FK column on ``telegram_messages``
   pointing at ``email_drafts.id`` (ON DELETE SET NULL), plus an
   ``ix_tg_email_draft`` index. The NotifyPoller does a LEFT JOIN from
   ``email_drafts`` to ``telegram_messages`` to find drafts that have
   not yet had a preview sent; without this column we would only be
   able to join on ``job_id``, which breaks for regenerated drafts
   (same job, new draft row).

Notes on the enum change:

``ALTER TYPE ... ADD VALUE`` is permitted inside a transaction on
Postgres >= 12 (which is our target). The new value is not usable
inside the same transaction that adds it, but this migration does NOT
reference the new ``APPROVED`` value in any subsequent statement -- the
column add is unrelated -- so running both inside Alembic's default
transaction is safe.

``IF NOT EXISTS`` makes the enum extension idempotent: if a partial
re-run lands here, the second pass is a no-op.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0004_phase9_schema"
down_revision = "0003_add_score_rationale"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ----- 1. Extend job_status enum with APPROVED ---------------------
    # IF NOT EXISTS makes this re-runnable -- useful if a failed migration
    # rolls back the column add but leaves the enum value in place.
    op.execute("ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'APPROVED'")

    # ----- 2. telegram_messages.email_draft_id + index ----------------
    op.add_column(
        "telegram_messages",
        sa.Column(
            "email_draft_id",
            sa.BigInteger(),
            sa.ForeignKey("email_drafts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_tg_email_draft",
        "telegram_messages",
        ["email_draft_id"],
    )


def downgrade() -> None:
    # Postgres has no built-in support for removing a value from an
    # enum type. The conventional workaround is to recreate the type
    # without the offending value, which is invasive (requires altering
    # every column that uses the type). We instead leave the APPROVED
    # value in place on downgrade -- it's harmless if no rows reference
    # it, and re-upgrading will be a no-op thanks to IF NOT EXISTS.
    op.drop_index("ix_tg_email_draft", table_name="telegram_messages")
    op.drop_column("telegram_messages", "email_draft_id")
