"""Thin adapter around the ``google-genai`` SDK.

Responsibilities are deliberately narrow:

- Translate our :class:`GeminiModel` enum to the SDK's stringly-typed
  ``model=`` argument.
- Build a ``GenerateContentConfig`` (or a plain dict fallback when the SDK
  isn't importable -- e.g. in unit tests that stub the whole client).
- Extract ``text`` + token counts from the response and surface them as
  the typed :class:`GeminiResponse` dataclass.
- Re-raise any SDK-side exception as :class:`ExternalServiceError` so
  callers can catch a single hierarchy.

What this module deliberately does NOT do:

- No retries / rate-limiting (see :mod:`knockknock.rate_limit.gemini_limiter`).
- No prompt logging (security policy: prompts may contain candidate PII).
- No business validation of the response body -- that lives in
  ``scoring/parser.py`` and ``email_gen/validator.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from knockknock.db.enums import GeminiModel
from knockknock.exceptions import ExternalServiceError

# Map our enum to the SDK's model-id strings. Single source-of-truth so the
# enum stays free of vendor strings and tests can assert on this dict
# without monkeypatching the SDK.
MODEL_IDS: dict[GeminiModel, str] = {
    GeminiModel.FLASH_2_5: "gemini-2.5-flash",
    GeminiModel.PRO_2_5: "gemini-2.5-pro",
}


@dataclass(frozen=True, slots=True)
class GeminiResponse:
    """Typed view of a successful Gemini call.

    ``text`` has been stripped; an empty body raises :class:`ValueError`
    upstream so the caller never sees ``text=""``.
    """

    text: str
    tokens_input: int
    tokens_output: int


class _GenAIClient(Protocol):
    """Subset of ``google.genai.Client`` we depend on.

    Kept minimal so unit tests can stub it without importing the SDK.
    The actual SDK type is :class:`google.genai.Client`. ``models`` is a
    read-only property on the real client, hence the ``@property`` here.
    """

    @property
    def models(self) -> Any:  # ``.generate_content(model=..., contents=..., config=...)``
        ...


@dataclass(slots=True)
class GeminiClient:
    """Adapter over ``google-genai``'s ``Client.models.generate_content``."""

    genai: _GenAIClient
    default_temperature: float = 0.2

    def generate(
        self,
        *,
        model: GeminiModel,
        system_instruction: str,
        user_prompt: str,
        response_mime_type: str,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> GeminiResponse:
        """Issue one generate-content call. Caller handles retries/budget.

        Raises:
            ExternalServiceError: SDK / network failure.
            ValueError: SDK returned a 2xx but with an empty ``text``
                payload (most often a safety filter trimmed the output).
        """
        config = self._build_config(
            system_instruction=system_instruction,
            response_mime_type=response_mime_type,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

        try:
            response = self.genai.models.generate_content(
                model=MODEL_IDS[model],
                contents=[user_prompt],
                config=config,
            )
        except Exception as exc:  # SDK exception classes vary.
            raise ExternalServiceError(f"Gemini API call failed: {exc}") from exc

        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise ValueError("Gemini returned empty response text.")

        usage = getattr(response, "usage_metadata", None)
        return GeminiResponse(
            text=text,
            tokens_input=int(getattr(usage, "prompt_token_count", 0) or 0),
            tokens_output=int(getattr(usage, "candidates_token_count", 0) or 0),
        )

    def _build_config(
        self,
        *,
        system_instruction: str,
        response_mime_type: str,
        temperature: float | None,
        max_output_tokens: int | None,
    ) -> Any:
        """Build the SDK's ``GenerateContentConfig`` (dict fallback for tests)."""
        effective_temp = temperature if temperature is not None else self.default_temperature
        # Late import: tests stub the whole client and don't need the SDK.
        try:
            from google.genai import types
        except ImportError:  # pragma: no cover -- only triggered without SDK.
            return {
                "system_instruction": system_instruction,
                "temperature": effective_temp,
                "response_mime_type": response_mime_type,
                "max_output_tokens": max_output_tokens,
            }
        return types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=effective_temp,
            response_mime_type=response_mime_type,
            max_output_tokens=max_output_tokens,
        )


def build_gemini_client(api_key: str) -> GeminiClient:
    """Construct a real Gemini client backed by ``google-genai``.

    Imported lazily so unit tests that stub the client don't pay the
    SDK import cost (~250ms cold) or require a network-egress sandbox.
    """
    from google import genai

    return GeminiClient(genai=genai.Client(api_key=api_key))
