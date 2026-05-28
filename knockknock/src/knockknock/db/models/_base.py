"""Shared helpers for SQLModel table definitions.

Kept private (leading underscore) — outside callers should import the
concrete model classes from :mod:`knockknock.db.models`, not from here.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import ENUM as PgEnum  # noqa: N811


def pg_enum(enum_cls: type, name: str) -> PgEnum:
    """Build a Postgres ENUM column type.

    ``create_type=False`` tells SQLAlchemy not to emit ``CREATE TYPE`` on
    ``metadata.create_all`` — the Alembic migration is responsible for
    creating the enum types up front. ``values_callable`` ensures
    ``member.value`` (not ``member.name``) is used as the Postgres label.
    """
    return PgEnum(
        enum_cls,
        name=name,
        create_type=False,
        values_callable=lambda e: [m.value for m in e],
    )
