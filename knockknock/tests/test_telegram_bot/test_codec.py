from __future__ import annotations

import pytest

from knockknock.telegram_bot.codec import (
    CallbackAction,
    decode_callback_data,
    encode_callback_data,
)


def test_encode_decode_roundtrip() -> None:
    payload = encode_callback_data(CallbackAction.APPROVE, draft_id=42, secret="s3cret")
    action, draft_id = decode_callback_data(payload, secret="s3cret")
    assert action is CallbackAction.APPROVE
    assert draft_id == 42


def test_encode_fits_in_64_bytes() -> None:
    payload = encode_callback_data(
        CallbackAction.REGENERATE, draft_id=9_999_999_999, secret="s3cret"
    )
    assert len(payload.encode("utf-8")) <= 64


def test_decode_rejects_wrong_secret() -> None:
    payload = encode_callback_data(CallbackAction.REJECT, draft_id=1, secret="real")
    with pytest.raises(ValueError, match="signature"):
        decode_callback_data(payload, secret="fake")


def test_decode_rejects_tampered_payload() -> None:
    payload = encode_callback_data(CallbackAction.APPROVE, draft_id=1, secret="s")
    # Flip approve -> reject; signature is over the original action.
    tampered = payload.replace("approve:", "reject:", 1)
    with pytest.raises(ValueError, match="signature"):
        decode_callback_data(tampered, secret="s")


def test_decode_rejects_malformed_input() -> None:
    with pytest.raises(ValueError):
        decode_callback_data("garbage", secret="s")


def test_decode_rejects_unknown_action() -> None:
    with pytest.raises(ValueError, match="action"):
        decode_callback_data("delete:1:abcd1234abcd1234", secret="s")
