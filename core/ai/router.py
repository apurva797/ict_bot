"""Provider routing with explicit development fallback."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .provider import AIProvider, AIProviderError, ProviderResponse
from .providers.gemini import GeminiProvider
from .providers.mock import MockProvider
from .providers.sarvam import SarvamProvider


@dataclass(frozen=True)
class ProviderStatus:
    requested: str
    active: str
    development_mode: bool
    message: str = ""


class AIRouter:
    def __init__(self, provider: AIProvider | None = None) -> None:
        self.requested = os.getenv("AI_PROVIDER", "sarvam").strip().lower()
        self.provider = provider or self._build(self.requested)
        self.status = ProviderStatus(self.requested, self.provider.name, False)

    @staticmethod
    def _build(name: str) -> AIProvider:
        if name == "gemini":
            return GeminiProvider()
        if name == "mock":
            return MockProvider()
        return SarvamProvider()

    def generate(self, *, system_prompt: str, user_prompt: str) -> ProviderResponse:
        try:
            return self.provider.generate(system_prompt=system_prompt, user_prompt=user_prompt)
        except AIProviderError as exc:
            if self.provider.name == "mock":
                raise
            self.provider = MockProvider()
            self.status = ProviderStatus(
                self.requested, "mock", True,
                "AI provider unavailable; deterministic development output is shown.",
            )
            return self.provider.generate(system_prompt=system_prompt, user_prompt=user_prompt)
