"""Unit tests for the thin :class:`GeminiClient` wrapper.

The real ``google-genai`` SDK is heavy and requires network; tests stub
``Client.models.generate_content`` via a Protocol-compatible fake so we
exercise our adapter (model-id mapping, text/token extraction, empty
response handling, error wrapping) without any external dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from knockknock.clients.gemini import (
    MODEL_IDS,
    GeminiClient,
    GeminiResponse,
)
from knockknock.db.enums import GeminiModel
from knockknock.exceptions import ExternalServiceError


@dataclass
class _StubUsage:
    prompt_token_count: int
    candidates_token_count: int


@dataclass
class _StubResponse:
    text: str
    usage_metadata: _StubUsage


class _StubModelsApi:
    """Stub for the `genai.Client.models` resource."""

    def __init__(
        self,
        response: _StubResponse | None = None,
        raise_on_call: Exception | None = None,
    ) -> None:
        self._response = response
        self._raise = raise_on_call
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, *, model: str, contents: list[str], config: Any) -> _StubResponse:
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self._raise is not None:
            raise self._raise
        assert self._response is not None
        return self._response


class _StubGenAI:
    def __init__(
        self,
        response: _StubResponse | None = None,
        raise_on_call: Exception | None = None,
    ) -> None:
        self.models = _StubModelsApi(response=response, raise_on_call=raise_on_call)


def test_generate_returns_text_and_usage() -> None:
    response = _StubResponse(
        text=' {"score": 8} ',
        usage_metadata=_StubUsage(prompt_token_count=120, candidates_token_count=15),
    )
    stub = _StubGenAI(response)
    client = GeminiClient(stub, default_temperature=0.2)
    result = client.generate(
        model=GeminiModel.FLASH_2_5,
        system_instruction="be terse",
        user_prompt="score this job",
        response_mime_type="application/json",
    )
    assert isinstance(result, GeminiResponse)
    assert result.text == '{"score": 8}'  # stripped
    assert result.tokens_input == 120
    assert result.tokens_output == 15
    assert stub.models.calls[0]["model"] == MODEL_IDS[GeminiModel.FLASH_2_5]
    assert stub.models.calls[0]["contents"] == ["score this job"]


def test_generate_raises_on_empty_text() -> None:
    response = _StubResponse(
        text="",
        usage_metadata=_StubUsage(prompt_token_count=10, candidates_token_count=0),
    )
    stub = _StubGenAI(response)
    client = GeminiClient(stub)
    with pytest.raises(ValueError, match="empty"):
        client.generate(
            model=GeminiModel.FLASH_2_5,
            system_instruction="x",
            user_prompt="y",
            response_mime_type="application/json",
        )


def test_generate_uses_pro_model_id_when_requested() -> None:
    response = _StubResponse(
        text="{}",
        usage_metadata=_StubUsage(prompt_token_count=1, candidates_token_count=1),
    )
    stub = _StubGenAI(response)
    client = GeminiClient(stub)
    client.generate(
        model=GeminiModel.PRO_2_5,
        system_instruction="x",
        user_prompt="y",
        response_mime_type="application/json",
    )
    assert stub.models.calls[0]["model"] == MODEL_IDS[GeminiModel.PRO_2_5]


def test_generate_wraps_sdk_errors_as_external_service_error() -> None:
    stub = _StubGenAI(raise_on_call=RuntimeError("network kaboom"))
    client = GeminiClient(stub)
    with pytest.raises(ExternalServiceError, match="Gemini"):
        client.generate(
            model=GeminiModel.FLASH_2_5,
            system_instruction="x",
            user_prompt="y",
            response_mime_type="application/json",
        )


def test_generate_handles_missing_usage_metadata() -> None:
    """Some SDK responses omit `usage_metadata`; treat token counts as zero."""

    @dataclass
    class _NoUsage:
        text: str
        usage_metadata: None = None

    stub = _StubGenAI(_NoUsage(text="ok"))  # type: ignore[arg-type]
    client = GeminiClient(stub)
    result = client.generate(
        model=GeminiModel.FLASH_2_5,
        system_instruction="x",
        user_prompt="y",
        response_mime_type="application/json",
    )
    assert result.tokens_input == 0
    assert result.tokens_output == 0
