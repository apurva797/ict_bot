"""Optional Gemini adapter, retained for future provider selection."""

from __future__ import annotations

import os

from core.ai.provider import AIProvider, AIProviderError, ProviderResponse


class GeminiProvider(AIProvider):
    name = "gemini"

    def generate(self, *, system_prompt: str, user_prompt: str) -> ProviderResponse:
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            raise AIProviderError("GEMINI_API_KEY is not configured.")
        try:
            from google import genai
            client = genai.Client(api_key=key)
            response = client.models.generate_content(
                model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
                contents=f"{system_prompt}\n\n{user_prompt}",
            )
        except Exception as exc:
            raise AIProviderError("Gemini request failed.") from exc
        if not response.text:
            raise AIProviderError("Gemini returned an empty response.")
        return ProviderResponse(response.text, self.name)
