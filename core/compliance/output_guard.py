"""Guardrails for model and user-facing analytical text."""

from __future__ import annotations

import re

_DIRECTIVE = re.compile(
    r"\b(?:buy|sell|hold|short|long)\s+(?:shares?\s+of\s+)?[A-Z][A-Z0-9&.-]{1,15}\b",
    re.IGNORECASE,
)
_GUARANTEE = re.compile(
    r"\b(?:guaranteed?|sure[- ]?shot|will definitely| निश्चित रूप से)\b",
    re.IGNORECASE,
)


def guard_text(text: str) -> tuple[str, bool]:
    """Neutralize direct recommendations and guarantees.

    Returns ``(safe_text, changed)``. The transformation is deliberately
    conservative: it flags the sentence instead of trying to rewrite financial
    meaning.
    """
    if not isinstance(text, str):
        raise TypeError("Compliance output must be text.")
    changed = bool(_DIRECTIVE.search(text) or _GUARANTEE.search(text))
    if not changed:
        return text, False
    safe = _DIRECTIVE.sub("[direct recommendation removed]", text)
    safe = _GUARANTEE.sub("[guarantee removed]", safe)
    return safe, True
