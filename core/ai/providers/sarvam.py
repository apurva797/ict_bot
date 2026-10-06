"""Official Sarvam Chat Completions adapter.

The SDK is imported lazily so local tests and fallback mode do not require an
API key or a network connection.
"""

from __future__ import annotations

import os
from io import BytesIO

from core.ai.provider import (AIProvider, AIProviderError, AIProviderNetworkError,
                              ProviderResponse)


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

    def transcribe(self, audio: bytes, *, filename: str = "voice.webm",
                   language_code: str = "unknown") -> str:
        """Send recorded audio to Sarvam STT; the key never leaves this process."""
        if not self._api_key:
            raise AIProviderError("Configure SARVAM_API_KEY to enable AI voice/research.")
        if not audio:
            raise AIProviderError("No audio was recorded.")
        try:
            from sarvamai import SarvamAI
            client = SarvamAI(api_subscription_key=self._api_key)
            response = client.speech_to_text.transcribe(
                file=(filename, BytesIO(audio), "audio/webm"),
                model=os.getenv("SARVAM_STT_MODEL", "saaras:v4"),
                mode="codemix",
                language_code=language_code if language_code != "auto" else "unknown",
            )
            transcript = str(getattr(response, "transcript", "") or "").strip()
        except ImportError as exc:
            raise AIProviderError("Sarvam SDK is not installed.") from exc
        except Exception as exc:
            raise AIProviderNetworkError("Sarvam speech-to-text request failed.") from exc
        if not transcript:
            raise AIProviderError("Sarvam speech-to-text returned no transcript.")
        return transcript

    def health_check(self) -> tuple[bool, str]:
        """Make one small authenticated request; callers are responsible for TTL caching."""
        if not self._api_key:
            return False, "Configure SARVAM_API_KEY to enable AI voice/research."
        try:
            from sarvamai import SarvamAI
            client = SarvamAI(api_subscription_key=self._api_key)
            response = client.chat.completions(
                model=self._model,
                messages=[{"role": "user", "content": "Reply with OK."}],
                temperature=0,
                max_tokens=3,
            )
            if not getattr(response, "choices", None):
                return False, "Sarvam returned an empty health response."
        except ImportError:
            return False, "Sarvam SDK is not installed."
        except Exception:
            return False, "Sarvam is unavailable."
        return True, "Sarvam is available."
