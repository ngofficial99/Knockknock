"""SQLModel ORM definitions mirroring the Phase-1 schema.

Each table lives in its own module under ``knockknock.db.models``. This
package ``__init__`` re-exports every class so existing imports keep working::

    from knockknock.db.models import Company, JobApplication

Importing the package also registers every table on ``SQLModel.metadata`` —
Alembic's ``env.py`` relies on this side-effect.

Errata E.2 applies: ``EmailDraftState`` (not ``DraftState``); the active-draft
partial index uses ``GENERATED``, not ``PENDING``; server defaults updated
accordingly.
"""

from __future__ import annotations

from knockknock.db.models.company import Company
from knockknock.db.models.company_blacklist import CompanyBlacklist
from knockknock.db.models.email_draft import EmailDraft
from knockknock.db.models.gemini_call_log import GeminiCallLog
from knockknock.db.models.job_application import JobApplication
from knockknock.db.models.job_application_event import JobApplicationEvent
from knockknock.db.models.phonebook_entry import PhonebookEntry
from knockknock.db.models.pipeline_run import PipelineRun
from knockknock.db.models.scraper_state import ScraperState
from knockknock.db.models.telegram_message import TelegramMessage

__all__ = [
    "Company",
    "CompanyBlacklist",
    "EmailDraft",
    "GeminiCallLog",
    "JobApplication",
    "JobApplicationEvent",
    "PhonebookEntry",
    "PipelineRun",
    "ScraperState",
    "TelegramMessage",
]
