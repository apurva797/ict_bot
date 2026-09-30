"""Server-side Gemini strategy interpretation using schema-constrained JSON."""

import json
import os


class GeminiServiceError(ValueError):
    """Safe, user-displayable AI configuration or response error."""


STRATEGY_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "side": {"type": "string", "enum": ["BUY", "SELL"]},
        "entry": {"type": "array", "items": {"type": "object", "properties": {
            "indicator": {"type": "string", "enum": ["price", "percentage_change", "RSI", "SMA", "EMA", "MACD", "ATR"]},
            "period": {"type": "integer"},
            "operator": {"type": "string", "enum": [">", "<", ">=", "<=", "==", "crosses_above", "crosses_below"]},
            "value": {"type": "number"},
            "compare_to": {"type": "object", "properties": {
                "indicator": {"type": "string", "enum": ["SMA", "EMA", "MACD"]},
                "period": {"type": "integer"},
            }, "required": ["indicator", "period"]},
        }, "required": ["indicator", "operator"]}},
        "exit": {"type": "array", "items": {"type": "object", "properties": {
            "indicator": {"type": "string", "enum": ["price", "percentage_change", "RSI", "SMA", "EMA", "MACD", "ATR"]},
            "period": {"type": "integer"},
            "operator": {"type": "string", "enum": [">", "<", ">=", "<=", "==", "crosses_above", "crosses_below"]},
            "value": {"type": "number"},
            "compare_to": {"type": "object", "properties": {
                "indicator": {"type": "string", "enum": ["SMA", "EMA", "MACD"]},
                "period": {"type": "integer"},
            }, "required": ["indicator", "period"]},
        }, "required": ["indicator", "operator"]}},
    },
    "required": ["side", "entry", "exit"],
}


def generate_strategy_json(text: str) -> str:
    """Ask Gemini for schema-constrained DSL JSON; the caller must validate it."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise GeminiServiceError("GEMINI_API_KEY is not configured on the server.")
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
            contents=(
                "Convert the user's request into only the provided restricted strategy schema. "
                "Do not invent price-action, news, session, or risk rules that the schema cannot express. "
                "All entry and exit conditions in each list are combined with AND. If a material detail "
                "is ambiguous or unsupported, return empty condition arrays so validation rejects it. "
                "Use the default demo risk settings; do not return code. User request: " + text
            ),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=STRATEGY_RESPONSE_SCHEMA,
                temperature=0,
            ),
        )
        output = response.text
        if not output:
            raise GeminiServiceError("Gemini returned an empty strategy specification.")
        # Reject malformed JSON here; the DSL validator applies semantic checks downstream.
        json.loads(output)
        return output
    except GeminiServiceError:
        raise
    except ImportError as exc:
        raise GeminiServiceError("Gemini support is unavailable. Install the pinned google-genai dependency.") from exc
    except Exception as exc:
        raise GeminiServiceError("Gemini could not interpret this strategy. Check the server-side key and try again.") from exc
