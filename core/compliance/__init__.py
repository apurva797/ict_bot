"""Centralized safety and disclosure helpers."""

from .output_guard import guard_text
from .disclaimer import EDUCATIONAL_DISCLAIMER

__all__ = ["EDUCATIONAL_DISCLAIMER", "guard_text"]
