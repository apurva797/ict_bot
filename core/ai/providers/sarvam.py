"""Official Sarvam Chat Completions adapter.

The SDK is imported lazily so local tests and fallback mode do not require an
API key or a network connection.
"""

from __future__ import annotations

import os

from core.ai.provider import AIProvider, AIProviderError, ProviderResponse


class SarvamProvider(AIProvider):
    name = "sarvam"

    def __init__(self) -> None:
        self._api_key = os.getenv("SARVAM_API_KEY", "").strip()
        self._model = os.getenv("SARVAM_MODEL", "sarvam-105b").strip()

    def generate(self, *, system_prompt: str, user_prompt: str) -> ProviderResponse:
        if not self._api_key:
            raise AIProviderError("SARVAM_API_KEY is not configured.")
        try:
            from sarvamai import SarvamAI
            client = SarvamAI(api_subscription_key=self._api_key)
            response = client.chat.completions(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                max_tokens=3000,
            )
            content = response.choices[0].message.content
        except ImportError as exc:
            raise AIProviderError("Sarvam SDK is not installed.") from exc
        except Exception as exc:
            raise AIProviderError("Sarvam AI request failed.") from exc
        if not content:
            raise AIProviderError("Sarvam AI returned an empty response.")
        return ProviderResponse(content, self.name)
