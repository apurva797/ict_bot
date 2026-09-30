"""Restricted strategy DSL and natural-language-to-JSON parser."""

import json
import math
import os
import re
import time
from collections import deque

import pandas as pd
import requests


INDICATORS = {"price", "percentage_change", "RSI", "SMA", "EMA", "MACD", "ATR"}
OPERATORS = {">", "<", ">=", "<=", "==", "crosses_above", "crosses_below"}
MAX_TEXT = 500
_LLM_CALLS = deque()


class StrategyError(ValueError):
    pass


def parse_strategy_json(payload):
    try:
        parsed = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise StrategyError("The AI returned invalid JSON. Try a simpler condition.") from exc
    return validate_strategy(parsed)


EXAMPLES = [
    "Buy when RSI(14) < 30 and exit when RSI(14) > 70.",
    "Buy when 20 EMA crosses above 50 EMA and exit when 20 EMA crosses below 50 EMA.",
    "Buy after a 2% price dip and exit after a 3% gain.",
]


def _condition(indicator, operator, value=None, period=None, compare_to=None):
    item = {"indicator": indicator, "operator": operator}
    if period is not None:
        item["period"] = period
    if compare_to is not None:
        item["compare_to"] = compare_to
    else:
        item["value"] = value
    return item


def validate_strategy(obj):
    if not isinstance(obj, dict) or set(obj) - {"side", "entry", "exit", "risk_fraction", "rr", "leverage"}:
        raise StrategyError("Strategy must be a JSON object with only supported fields.")
    side = obj.get("side", "BUY")
    if side not in {"BUY", "SELL"}:
        raise StrategyError("Unsupported action. Choose BUY or SELL.")
    normalized = {"side": side, "entry": [], "exit": []}
    for key in ("entry", "exit"):
        conditions = obj.get(key)
        if not isinstance(conditions, list) or not conditions or len(conditions) > 8:
            raise StrategyError(f"{key.title()} must contain 1 to 8 conditions.")
        for raw in conditions:
            allowed = {"indicator", "period", "operator", "value", "compare_to"}
            if not isinstance(raw, dict) or not set(raw) <= allowed:
                raise StrategyError("Condition has fields outside the supported strategy rules.")
            indicator, operator = raw.get("indicator"), raw.get("operator")
            if indicator not in INDICATORS:
                raise StrategyError(f"Unsupported indicator: {indicator}.")
            if operator not in OPERATORS:
                raise StrategyError(f"Unsupported operator: {operator}.")
            period = raw.get("period")
            if indicator in {"RSI", "SMA", "EMA", "MACD", "ATR", "percentage_change"}:
                if not isinstance(period, int) or isinstance(period, bool) or not 1 <= period <= 200:
                    raise StrategyError("Indicator period must be an integer from 1 to 200.")
            elif period is not None:
                raise StrategyError("The price indicator does not accept a period.")
            item = {"indicator": indicator, "operator": operator}
            if period is not None:
                item["period"] = period
            compare = raw.get("compare_to")
            if compare is not None:
                if operator not in {"crosses_above", "crosses_below"} or not isinstance(compare, dict) or set(compare) != {"indicator", "period"}:
                    raise StrategyError("Indicator comparisons require a supported crossover pair.")
                if compare["indicator"] not in {"SMA", "EMA", "MACD"} or not isinstance(compare["period"], int) or not 1 <= compare["period"] <= 200:
                    raise StrategyError("Unsupported comparison indicator or period.")
                item["compare_to"] = compare
            else:
                value = raw.get("value")
                if not isinstance(value, (float, int)) or isinstance(value, bool) or not -1_000_000 <= value <= 1_000_000:
                    raise StrategyError("Each condition needs a finite numeric value.")
                item["value"] = float(value)
            normalized[key].append(item)
    for field, default, maximum in (("risk_fraction", 0.01, 0.01), ("rr", 2.0, None), ("leverage", 1.0, 1.0)):
        value = obj.get(field, default)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise StrategyError(f"Invalid {field} value.")
        if field == "risk_fraction" and (value <= 0 or value > maximum):
            raise StrategyError("Strategy rejected because requested risk exceeds the 1% demo safety limit.")
        if field == "rr" and value < 2:
            raise StrategyError("Strategy rejected because requested risk/reward is below 2.0.")
        if field == "leverage" and (value <= 0 or value > maximum):
            raise StrategyError("Strategy rejected because requested leverage exceeds the 1x demo safety limit.")
    return normalized


def _local_parse(text):
    s = text.lower().strip()
    if "rsi" in s:
        period = int(re.search(r"rsi\s*\(?\s*(\d+)", s).group(1)) if re.search(r"rsi\s*\(?\s*(\d+)", s) else 14
        entry = re.search(r"(?:below|under|<)\s*(\d+(?:\.\d+)?)", s)
        exit_ = re.search(r"(?:above|over|>)\s*(\d+(?:\.\d+)?)", s)
        if entry and exit_:
            return {"side": "BUY", "entry": [_condition("RSI", "<", float(entry.group(1)), period)], "exit": [_condition("RSI", ">", float(exit_.group(1)), period)]}
    if "ema" in s and "cross" in s:
        periods = [int(v) for v in re.findall(r"(\d+)\s*ema", s)]
        if len(periods) >= 2:
            fast, slow = periods[:2]
            entry = [_condition("EMA", "crosses_above", period=fast, compare_to={"indicator": "EMA", "period": slow})]
            rsi_filter = re.search(r"\brsi(?:\s*\(\s*(\d+)\s*\))?[\s\S]{0,35}?(?:above|over|>)\s*(\d+(?:\.\d+)?)", s)
            if rsi_filter:
                period = int(rsi_filter.group(1) or 14)
                entry.append(_condition("RSI", ">", float(rsi_filter.group(2)), period))
            return {"side": "BUY", "entry": entry, "exit": [_condition("EMA", "crosses_below", period=fast, compare_to={"indicator": "EMA", "period": slow})]}
    dip = re.search(r"(\d+(?:\.\d+)?)\s*%.*dip", s)
    gain = re.search(r"(\d+(?:\.\d+)?)\s*%.*gain", s)
    if dip and gain:
        return {"side": "BUY", "entry": [_condition("percentage_change", "<", -float(dip.group(1)), 1)], "exit": [_condition("percentage_change", ">=", float(gain.group(1)), 1)]}
    raise StrategyError("Strategy could not be understood. Try a simpler condition or one of the examples.")


def parse_strategy(text):
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise StrategyError("Enter a strategy under 500 characters.")
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key:
        now = time.monotonic()
        while _LLM_CALLS and now - _LLM_CALLS[0] > 60:
            _LLM_CALLS.popleft()
        if len(_LLM_CALLS) >= 5:
            raise StrategyError("AI request limit reached. Wait one minute and try again.")
        _LLM_CALLS.append(now)
        try:
            from gemini_service import generate_strategy_json
            return parse_strategy_json(generate_strategy_json(text))
        except StrategyError:
            raise
        except Exception as exc:
            try:
                return validate_strategy(_local_parse(text))
            except StrategyError:
                raise StrategyError("Gemini is unavailable or the request is ambiguous. Refine the rules or configure GEMINI_API_KEY.") from exc
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return validate_strategy(_local_parse(text))
    now = time.monotonic()
    while _LLM_CALLS and now - _LLM_CALLS[0] > 60:
        _LLM_CALLS.popleft()
    if len(_LLM_CALLS) >= 5:
        raise StrategyError("AI request limit reached. Wait one minute and try again.")
    _LLM_CALLS.append(now)
    # The model can return JSON only. All output is validated locally before use.
    schema = {"type": "object", "properties": {"side": {"type": "string", "enum": ["BUY", "SELL"]}, "entry": {"type": "array", "items": {"type": "object"}}, "exit": {"type": "array", "items": {"type": "object"}}}, "required": ["side", "entry", "exit"], "additionalProperties": False}
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions", timeout=12,
            headers={"Authorization": f"Bearer {key}"},
            json={"model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"), "temperature": 0,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": "Convert the request to a restricted trading condition JSON object only. Never return code or risk settings. Indicators: price, percentage_change, RSI, SMA, EMA, MACD, ATR. Operators: >, <, >=, <=, ==, crosses_above, crosses_below. Each entry/exit condition has indicator, period where required, operator, and numeric value; crossovers may use compare_to {indicator,period}."}, {"role": "user", "content": text}], "max_tokens": 500},
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return parse_strategy_json(content)
    except StrategyError:
        raise
    except Exception as exc:
        # Local parsing supports the three public examples without requiring a key.
        try:
            return validate_strategy(_local_parse(text))
        except StrategyError:
            raise StrategyError("The strategy service is unavailable. Try a simpler condition.") from exc


def generate_strategy_code(strategy):
    """Return a deterministic, version-tagged Python adapter for display/export.

    The app never executes generated source. Execution continues through the
    validated DSL and its fixed indicator evaluator.
    """
    normalized = validate_strategy(strategy)
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    encoded = json.dumps(payload)
    return (
        '"""Generated strategy adapter; DSL version 1.0. Not dynamically executed by the app."""\n'
        "import json\n"
        "from demo_strategy import evaluate_conditions, validate_strategy\n\n"
        f"SPEC = json.loads({encoded})\n\n"
        "def signal_series(frame):\n"
        "    spec = validate_strategy(SPEC)\n"
        "    entries = evaluate_conditions(frame, spec['entry']).fillna(False)\n"
        "    return entries.map(lambda active: spec['side'] if active else None)\n"
    )


def indicators(frame, spec):
    close = frame.close
    name, period = spec["indicator"], spec.get("period", 1)
    if name == "price":
        return close
    if name == "percentage_change":
        return close.pct_change(periods=period) * 100
    if name == "RSI":
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        loss = -delta.clip(upper=0).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        result = 100 - 100 / (1 + gain / loss.replace(0, float("nan")))
        no_losses = loss.eq(0) & gain.notna()
        flat = gain.eq(0) & loss.eq(0)
        return result.mask(no_losses, 100.0).mask(flat, 50.0)
    if name == "SMA":
        return close.rolling(period, min_periods=period).mean()
    if name == "EMA":
        return close.ewm(span=period, min_periods=period, adjust=False).mean()
    if name == "MACD":
        return close.ewm(span=12, adjust=False).mean() - close.ewm(span=period, adjust=False).mean()
    if name == "ATR":
        tr = pd.concat([frame.high - frame.low, (frame.high - close.shift()).abs(), (frame.low - close.shift()).abs()], axis=1).max(axis=1)
        return tr.rolling(period, min_periods=period).mean()
    raise StrategyError("Unsupported indicator.")


def condition_mask(frame, condition, entry_price=None):
    left = indicators(frame, condition)
    if condition["indicator"] == "percentage_change" and entry_price is not None:
        left = (frame.close / entry_price - 1) * 100
    op = condition["operator"]
    if "compare_to" in condition:
        right = indicators(frame, {**condition["compare_to"], "operator": op})
    else:
        right = condition["value"]
        if op in {"crosses_above", "crosses_below"}:
            right = pd.Series(float(right), index=frame.index)
    if op == ">": return left > right
    if op == "<": return left < right
    if op == ">=": return left >= right
    if op == "<=": return left <= right
    if op == "==": return left == right
    if op == "crosses_above": return (left.shift(1) <= right.shift(1)) & (left > right)
    if op == "crosses_below": return (left.shift(1) >= right.shift(1)) & (left < right)
    raise StrategyError("Unsupported operator.")


def evaluate_conditions(frame, conditions, entry_price=None):
    masks = [condition_mask(frame, item, entry_price=entry_price).fillna(False) for item in conditions]
    return pd.concat(masks, axis=1).all(axis=1)

