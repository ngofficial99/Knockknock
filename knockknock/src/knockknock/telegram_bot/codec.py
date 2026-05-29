"""HMAC-signed ``callback_data`` codec for Telegram inline-button actions.

Telegram caps ``callback_data`` at 64 bytes. We pack the tuple
``(action, draft_id, hmac_tag)`` colon-delimited:

    "<action>:<draft_id>:<hmac_hex16>"

``hmac_hex16`` is the first 16 hex chars (64 bits) of
``HMAC-SHA256(secret, f"{action}:{draft_id}")``. 64 bits is plenty for
a single-user, single-bot threat model: an attacker who somehow reaches
our webhook would need ~2^32 attempts on average to forge a tag for a
specific (action, draft_id) pair, and we rate-limit + only honour
``X-Telegram-Bot-Api-Secret-Token`` + admin chat-id messages anyway --
the HMAC is defence-in-depth, not the primary boundary.

The signing secret comes from ``KNOCKKNOCK_SECRET_TELEGRAM_CALLBACK_SECRET``
and is rotated by regenerating the secret + redeploying. In-flight
inline keyboards become un-clickable after rotation, which is the
intended behaviour.
"""

from __future__ import annotations

import hmac
from enum import StrEnum
from hashlib import sha256

# 64 bits is enough for our threat model. Tradeoff: a longer tag eats
# into the 64-byte ``callback_data`` budget and we still want room for
# very large draft_ids.
_SIGNATURE_HEX_LEN = 16


class CallbackAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REGENERATE = "regenerate"


def _sign(action: CallbackAction, draft_id: int, secret: str) -> str:
    message = f"{action.value}:{draft_id}".encode()
    mac = hmac.new(secret.encode("utf-8"), message, sha256).hexdigest()
    return mac[:_SIGNATURE_HEX_LEN]


def encode_callback_data(action: CallbackAction, *, draft_id: int, secret: str) -> str:
    """Pack ``(action, draft_id, hmac)`` into a colon-delimited string <= 64 bytes."""
    signature = _sign(action, draft_id, secret)
    return f"{action.value}:{draft_id}:{signature}"


def decode_callback_data(raw: str, *, secret: str) -> tuple[CallbackAction, int]:
    """Verify HMAC and return ``(action, draft_id)``.

    Raises ``ValueError`` on any malformedness, unknown action, non-integer
    draft id, or signature mismatch -- the caller should treat all of these
    as "ignore this update" (don't leak detail back to Telegram).
    """
    if not raw or raw.count(":") != 2:
        raise ValueError(f"Malformed callback_data: {raw!r}")
    action_str, draft_id_str, signature = raw.split(":", 2)
    try:
        action = CallbackAction(action_str)
    except ValueError as exc:
        raise ValueError(f"Unknown callback action: {action_str!r}") from exc
    try:
        draft_id = int(draft_id_str)
    except ValueError as exc:
        raise ValueError(f"Non-integer draft id in callback_data: {draft_id_str!r}") from exc
    expected = _sign(action, draft_id, secret)
    # Constant-time comparison defeats timing-side-channel guessing of
    # the tag. The remaining brute-force is ~2^32 average attempts per
    # specific (action, draft_id), gated by Telegram's per-update rate
    # limits and our admin-chat-id check.
    if not hmac.compare_digest(expected, signature):
        raise ValueError("callback_data signature mismatch")
    return action, draft_id
