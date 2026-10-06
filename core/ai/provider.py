"""Provider contract and safe provider errors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class AIProviderError(RuntimeError):
    """Expected provider/configuration failure safe to show to a user."""


@dataclass(frozen=True)
class ProviderResponse:
    raw_text: str
    provider: str


class AIProvider(ABC):
    name: str

    @abstractmethod
    def generate(self, *, system_prompt: str, user_prompt: str) -> ProviderResponse:
        raise NotImplementedError
