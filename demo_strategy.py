"""Restricted strategy DSL and natural-language-to-JSON parser."""

import json
import math
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TypedDict

import pandas as pd
import requests


INDICATORS = {"price", "percentage_change", "RSI", "SMA", "EMA", "MACD", "ATR",
              "VWAP", "previous_high", "previous_low"}
OPERATORS = {">", "<", ">=", "<=", "==", "crosses_above", "crosses_below"}
MAX_TEXT = 500
_LLM_CALLS = deque()


class StrategyError(ValueError):
    pass


class StrategySpecification(TypedDict):
    """Validated data-only contract consumed by the deterministic engines."""

    side: str
    entry: list[dict]
    exit: list[dict]
    risk_fraction: float
    rr: float
    leverage: float


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


def validate_strategy(obj) -> StrategySpecification:
    if not isinstance(obj, dict) or set(obj) - {"side", "entry", "exit", "risk_fraction", "rr", "leverage"}:
        raise StrategyError("Strategy must be a JSON object with only supported fields.")
    side = obj.get("side", "BUY")
    if side not in {"BUY", "SELL"}:
        raise StrategyError("Unsupported action. Choose BUY or SELL.")
    normalized = {"side": side, "entry": [], "exit": []}
    for key in ("entry", "exit"):
        conditions = obj.get(key)
        if (not isinstance(conditions, list) or len(conditions) > 8
                or (key == "entry" and not conditions)):
            raise StrategyError(f"{key.title()} must contain {('1 to 8' if key == 'entry' else '0 to 8')} conditions.")
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
                if not isinstance(compare, dict) or compare.get("indicator") not in INDICATORS:
                    raise StrategyError("Indicator comparison must use a supported indicator.")
                comparison_indicator = compare["indicator"]
                needs_period = comparison_indicator in {"RSI", "SMA", "EMA", "MACD", "ATR", "percentage_change"}
                expected_fields = {"indicator", "period"} if needs_period else {"indicator"}
                if set(compare) != expected_fields:
                    raise StrategyError("Indicator comparison is missing a valid period or has unsupported fields.")
                if needs_period and (not isinstance(compare["period"], int)
                                     or isinstance(compare["period"], bool)
                                     or not 1 <= compare["period"] <= 200):
                    raise StrategyError("Unsupported comparison indicator or period.")
                item["compare_to"] = compare
            else:
                value = raw.get("value")
                if (not isinstance(value, (float, int)) or isinstance(value, bool)
                    or not math.isfinite(value)
                    or not -1_000_000 <= value <= 1_000_000):
                    raise StrategyError("Each condition needs a finite numeric value.")
                item["value"] = float(value)
            normalized[key].append(item)
    for field, default, maximum in (("risk_fraction", 0.01, 0.01), ("rr", 2.0, None), ("leverage", 1.0, 1.0)):
        value = obj.get(field, default)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise StrategyError(f"Invalid {field} value.")
        if field == "risk_fraction" and (value <= 0 or value > maximum):
            raise StrategyError("Strategy rejected because requested risk exceeds the 1% demo safety limit.")
        if field == "rr" and value < 1.5:
            raise StrategyError("Strategy rejected because requested risk/reward is below 1.5R.")
        if field == "leverage" and (value <= 0 or value > maximum):
            raise StrategyError("Strategy rejected because requested leverage exceeds the 1x demo safety limit.")
        normalized[field] = float(value)
    return normalized


def _words_to_numbers(text):
    replacements = {
        r"\bone\s+point\s+five\b": "1.5", r"\bone\s+point\s+five\s+r\b": "1.5R",
        r"\bone\s+and\s+a\s+half\b": "1.5", r"\bdedh\b": "1.5", r"\bsawa\s+ek\b": "1.25",
        r"\bone\b": "1", r"\btwo\b": "2", r"\bthree\b": "3", r"\bfour\b": "4",
        r"\bfive\b": "5", r"\bten\b": "10", r"\btwenty\b": "20", r"\bfifty\b": "50",
        r"\bek\b": "1", r"\bdo\b": "2", r"\bteen\b": "3", r"\bchaar\b": "4",
        r"\bpaanch\b": "5", r"\btees\b": "30", r"\bsaat\b": "7", r"\bpachaas\b": "50",
    }
    normalized = text.lower()
    for pattern, value in replacements.items():
        normalized = re.sub(pattern, value, normalized)
    return normalized


def _infer_side(text):
    s = _words_to_numbers(text)
    entry_text = re.split(r"\b(?:exit|close|take\s*profit|target)\b|निकाल", s, maxsplit=1)[0]
    if re.search(r"\b(short|sell|bech(?:o|na)?|bearish|downtrend)\b|शॉर्ट|बेच|मंदी", entry_text):
        return "SELL"
    return "BUY"


def _local_parse(text):
    """Interpret common indicator strategies in English, Hindi and Hinglish."""
    s = _words_to_numbers(text)
    side = _infer_side(s)
    entry = []
    exit_ = []

    # EMA/SMA fast/slow crossovers, including Hinglish "20 50 ko cross kare".
    cross_match = re.search(r"\b(ema|sma)\b", s)
    cross_periods = []
    if cross_match and re.search(r"cross|crossover|crossunder|crosses|क्रॉस", s):
        name = cross_match.group(1).upper()
        explicit_periods = re.findall(r"(?:ema|sma)\s*(\d+)|(\d+)\s*(?:ema|sma)", s)
        cross_periods = [int(left or right) for left, right in explicit_periods]
        cross_periods = cross_periods[:2]
        suffix = s[cross_match.end():]
        if len(cross_periods) < 2:
            cross_periods = [int(n) for n in re.findall(r"\d+", suffix)[:2]]
        if len(cross_periods) >= 2:
            fast, slow = cross_periods[:2]
            entry_text = re.split(r"\b(?:exit|close|take\s*profit|target)\b|निकाल", s, maxsplit=1)[0]
            explicit_entry_down = bool(re.search(r"cross(?:es)?\s*(?:below|down|under)|crossunder|neeche|down|नीचे", entry_text))
            explicit_entry_up = bool(re.search(r"cross(?:es)?\s*(?:above|up|over)|crossover|upar|ऊपर", entry_text))
            bearish = explicit_entry_down or (side == "SELL" and not explicit_entry_up)
            op = "crosses_below" if bearish else "crosses_above"
            entry.append(_condition(name, op, period=fast, compare_to={"indicator": name, "period": slow}))
            if re.search(r"exit|close|reverse|ulta|nikal", s):
                exit_text = re.split(r"\b(?:exit|close|take\s*profit|target)\b|निकाल", s, maxsplit=1)[-1]
                explicit_exit_down = bool(re.search(r"cross(?:es)?\s*(?:below|down|under)|crossunder|neeche|down|नीचे", exit_text))
                explicit_exit_up = bool(re.search(r"cross(?:es)?\s*(?:above|up|over)|crossover|upar|ऊपर", exit_text))
                exit_op = ("crosses_below" if explicit_exit_down else "crosses_above" if explicit_exit_up
                           else "crosses_above" if bearish else "crosses_below")
                exit_.append(_condition(name, exit_op,
                                         period=fast, compare_to={"indicator": name, "period": slow}))

    # Price above/below an indicator, for example "price 200 EMA ke upar".
    price_vs_average = re.search(
        r"(?:price|close|daam|bhaav)\s*(\d+)?\s*(ema|sma)\s*(?:ke\s*)?(upar|above|over|neeche|below|under|ऊपर|नीचे)", s
    ) or re.search(r"(?:price|close|daam|bhaav)\s*(?:is\s*)?(above|over|below|under)\s*(\d+)\s*(ema|sma)", s)
    if price_vs_average:
        if price_vs_average.group(2) in {"ema", "sma"}:
            period, name, direction = price_vs_average.groups()
        else:
            direction, period, name = price_vs_average.groups()
        operator = ">" if direction in {"upar", "above", "over", "ऊपर"} else "<"
        entry.append(_condition("price", operator, compare_to={"indicator": name.upper(), "period": int(period or 20)}))

    # RSI threshold and simple supported indicator levels.
    threshold_pattern = re.compile(
        r"\b(rsi|macd)\s*(?:\(\s*(\d+)\s*\))?\s*(?:(\d+(?:\.\d+)?)\s*(?:ke\s*)?(neeche|upar|below|under|above|over|के\s+नीचे|के\s+ऊपर|नीचे|ऊपर)|(?:(?:is|goes|go|moves?|ho)\s*)?(below|under|above|over|ke\s+neeche|ke\s+upar|के\s+नीचे|के\s+ऊपर|<|>)\s*(\d+(?:\.\d+)?))"
    )
    for match in threshold_pattern.finditer(s):
        name, period, before_value, before_direction, after_direction, after_value = match.groups()
        value = float(before_value or after_value)
        direction = before_direction or after_direction
        operator = "<" if direction in {"neeche", "below", "under", "ke neeche", "के नीचे", "नीचे", "<"} else ">"
        condition = _condition(name.upper(), operator, value, int(period or (14 if name == "rsi" else 26)) if name == "rsi" else int(period or 26))
        segment_before = s[max(0, match.start() - 45):match.start()]
        (exit_ if re.search(r"exit|close|target|nikal|then sell|and sell|phir sell", segment_before) else entry).append(condition)

    # Explicit previous-candle high/low breakouts.
    if re.search(r"(previous|prev|pichhla|pichla|pichle)\s+(high|highs|swing high).{0,24}(break|breakout|cross|toot|upar|ऊपर)", s):
        entry.append(_condition("price", "crosses_above", compare_to={"indicator": "previous_high"}))
    if re.search(r"(previous|prev|pichhla|pichla|pichle)\s+(low|lows|swing low).{0,24}(break|breakdown|cross|toot|neeche|नीचे)", s):
        entry.append(_condition("price", "crosses_below", compare_to={"indicator": "previous_low"}))

    # VWAP relation is directly measurable and deterministic in the DSL.
    if re.search(r"(?:price|close|daam|bhaav)\s*(?:is\s*)?(?:above|over|upar).*vwap", s) or re.search(r"vwap.*(?:below|under|neeche)", s):
        entry.append(_condition("price", ">", compare_to={"indicator": "VWAP"}))
    elif re.search(r"(?:price|close|daam|bhaav)\s*(?:is\s*)?(?:below|under|neeche).*vwap", s) or re.search(r"vwap.*(?:above|over|upar)", s):
        entry.append(_condition("price", "<", compare_to={"indicator": "VWAP"}))

    macd_cross = re.search(r"\bmacd\b.{0,25}?cross(?:es|ed)?\s*(above|over|up|below|under|down)\s*(?:the\s*)?(?:zero|0|line)", s)
    if macd_cross:
        operator = "crosses_above" if macd_cross.group(1) in {"above", "over", "up"} else "crosses_below"
        condition = _condition("MACD", operator, 0, 26)
        before = s[max(0, macd_cross.start() - 45):macd_cross.start()]
        (exit_ if re.search(r"exit|close|target", before) else entry).append(condition)

    dip = re.search(r"(\d+(?:\.\d+)?)\s*%.*(?:dip|drop|gir|fall)", s)
    if dip:
        entry.append(_condition("percentage_change", "<", -float(dip.group(1)), 1))
    gain = re.search(r"(?:exit|gain|profit|badhe|upar).{0,25}(\d+(?:\.\d+)?)\s*%", s)
    if gain:
        exit_.append(_condition("percentage_change", ">=", float(gain.group(1)), 1))

    if not entry:
        raise StrategyError("No measurable entry condition was found.")
    if re.search(r"exit|close|nikal", s) and not exit_:
        raise StrategyError("An exit was requested, but its measurable condition was not found.")

    draft = {"side": side, "entry": entry, "exit": exit_}
    draft.update(_extract_preferences(text))
    return draft


@dataclass
class StrategyInterpretation:
    specification: StrategySpecification | None
    draft: dict | None
    summary: str
    clarification: str | None = None
    suggestions: list[str] = field(default_factory=list)
    symbol: str | None = None
    timeframe: str | None = None
    validation_message: str | None = None


def _extract_context(text):
    s = _words_to_numbers(text)
    symbol = None
    for ticker, market in (("btc", "BTC/USDT"), ("bitcoin", "BTC/USDT"),
                           ("eth", "ETH/USDT"), ("ethereum", "ETH/USDT"),
                           ("sol", "SOL/USDT"), ("solana", "SOL/USDT")):
        if re.search(rf"\b{ticker}\b", s):
            symbol = market
            break
    match = re.search(r"\b(\d+)\s*(m|min|mins|minute|minutes|h|hr|hour|hours|मिनट|घंटे?)\b", s)
    timeframe = None
    if match:
        timeframe = f"{int(match.group(1))}{'h' if match.group(2).startswith(('h', 'hr', 'hour', 'घंट')) else 'm'}"
    return symbol, timeframe


def _extract_preferences(text):
    s = _words_to_numbers(text)
    preferences = {}
    risk = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent|per cent|pratishat|प्रतिशत)\s*(?:risk|riks|जोखिम|रिस्क)?", s)
    if risk and re.search(r"risk|riks|risk fraction|जोखिम|रिस्क", s[max(0, risk.start() - 18):risk.end() + 8]):
        preferences["risk_fraction"] = float(risk.group(1)) / 100
    rr = re.search(r"(\d+(?:\.\d+)?)\s*(?:r:r|rr\b|r\b|risk\s*[:/]\s*reward|risk reward)", s)
    if rr:
        preferences["rr"] = float(rr.group(1))
    else:
        rr = re.search(r"(?:r:r|rr\b|risk\s*[:/]\s*reward|risk reward)\s*(?:=|is|of)?\s*(\d+(?:\.\d+)?)\s*r?\b", s)
        if rr:
            preferences["rr"] = float(rr.group(1))
    leverage = re.search(r"(\d+(?:\.\d+)?)\s*x\s*(?:leverage|lev)", s)
    if leverage:
        preferences["leverage"] = float(leverage.group(1))
    return preferences


def _summarize_spec(spec, symbol=None, timeframe=None):
    market = symbol or "Selected market"
    interval = timeframe or "selected timeframe"
    side = "Long" if spec["side"] == "BUY" else "Short"
    parts = []
    for condition in spec["entry"]:
        indicator = condition["indicator"]
        if indicator == "price" and condition.get("compare_to"):
            right = condition["compare_to"]["indicator"].replace("_", " ")
            right_period = condition["compare_to"].get("period")
            right = f"{right_period} {right}" if right_period else right.title()
            parts.append(f"Price {condition['operator']} {right}")
        elif "compare_to" in condition:
            other = condition["compare_to"]
            parts.append(f"{condition.get('period', '')} {indicator} crossover vs {other.get('period', '')} {other['indicator']}")
        else:
            parts.append(f"{indicator} {condition['operator']} {condition.get('value', '')}")
    return f"{market} {interval} — {' + '.join(parts)} → {side}"


def summarize_strategy(spec, symbol=None, timeframe=None):
    """Public human-readable summary for an already validated rule set."""
    return _summarize_spec(spec, symbol, timeframe)


def interpret_strategy(text):
    """Return a validated spec or an actionable clarification for natural text."""
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        return StrategyInterpretation(None, None, "", "Describe the setup in 500 characters or fewer.")
    symbol, timeframe = _extract_context(text)
    preferences = _extract_preferences(text)
    try:
        draft = _local_parse(text)
    except StrategyError as local_error:
        draft = None
        # Use the configured server-side model for patterns outside the local vocabulary.
        if os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip():
            try:
                draft = parse_strategy(text)
                if draft is not None:
                    draft.update(preferences)
            except StrategyError:
                pass
        if draft is None:
            lowered = text.lower()
            if re.search(r"liquidity|sweep|fvg|fair value gap|order block|\bmss\b|market structure", lowered):
                side = _infer_side(text)
                draft = {"side": side, "entry": [], "exit": [], **preferences} if preferences else None
                limitations = []
                if preferences.get("rr", 2.0) < 1.5:
                    limitations.append("The existing ARJUNA engine enforces a minimum 1.5R target.")
                if re.search(r"\b(sl|stop\s*loss|stoploss)\b", lowered):
                    limitations.append("The existing ARJUNA engine uses its fixed 1% price-risk stop, not a custom swing stop.")
                limitation = " ".join(limitations) or None
                return StrategyInterpretation(
                    None, draft, f"{symbol or 'Selected market'} {timeframe or 'selected timeframe'} — ARJUNA setup → {('Short' if side == 'SELL' else 'Long')}",
                    "I recognize this as an ARJUNA price-action setup. Which confirmation should the existing ARJUNA engine wait for?",
                    ["Bearish/bullish candle confirmation", "Market structure shift", "Use the complete existing ARJUNA strategy"],
                    symbol, timeframe, limitation,
                )
            if re.search(r"\brsi\b", lowered):
                return StrategyInterpretation(None, None, f"{symbol or 'Selected market'} {timeframe or 'selected timeframe'} — RSI setup",
                                              "What RSI level should trigger the entry, and should it be above or below that level?",
                                              ["Below 30", "Above 70", "Another level"], symbol, timeframe)
            if re.search(r"\b(ema|sma)\b", lowered) and re.search(r"cross|crossover|crossunder", lowered):
                return StrategyInterpretation(None, None, f"{symbol or 'Selected market'} {timeframe or 'selected timeframe'} — moving-average crossover",
                                              "Which fast and slow average periods should cross, and in which direction?",
                                              ["20 crosses above 50", "20 crosses below 50", "Another pair"], symbol, timeframe)
            if re.search(r"\bmacd\b", lowered):
                return StrategyInterpretation(None, None, f"{symbol or 'Selected market'} {timeframe or 'selected timeframe'} — MACD setup",
                                              "Should MACD cross the zero line, or should it cross its signal line?",
                                              ["Cross above zero", "Cross below zero", "MACD/signal crossover"], symbol, timeframe)
            if re.search(r"support|resistance|\btrend\b|breakout|breakdown", lowered):
                return StrategyInterpretation(None, None, f"{symbol or 'Selected market'} {timeframe or 'selected timeframe'} — price-action setup",
                                              "What exact price level or measurable trend rule should confirm this setup?",
                                              ["Previous candle high/low break", "Price above/below a moving average", "Another price level"], symbol, timeframe)
            if preferences:
                side = "SELL" if re.search(r"\b(short|sell|bech|bearish)\b|शॉर्ट|बेच|मंदी", lowered) else "BUY"
                draft = {"side": side, "entry": [], "exit": [], **preferences}
                return StrategyInterpretation(
                    None, draft, f"{symbol or 'Selected market'} {timeframe or 'selected timeframe'} — risk settings received",
                    "What measurable entry condition should trigger this trade?",
                    ["RSI threshold", "EMA crossover", "Previous high/low breakout"], symbol, timeframe,
                )
            if re.search(r"\b(short|sell|long|buy|becho|kharid)\b", lowered):
                return StrategyInterpretation(None, None, f"{symbol or 'Selected market'} {timeframe or 'selected timeframe'} — direction understood",
                                              "What measurable entry condition should trigger this trade?",
                                              ["RSI threshold", "EMA crossover", "Previous high/low breakout"], symbol, timeframe)
            return StrategyInterpretation(None, None, "", "Which indicator or price event should trigger the entry?",
                                          ["RSI threshold", "EMA crossover", "Previous high/low breakout"], symbol, timeframe)

    lowered = text.lower()
    if len(draft.get("entry", [])) > 1 and re.search(r"\bor\b|या|अथवा", lowered):
        return StrategyInterpretation(
            None, draft, f"{symbol or 'Selected market'} {timeframe or 'selected timeframe'} — combined setup",
            "Should either entry condition trigger a trade, or must both be true? The custom strategy engine currently combines conditions with AND.",
            ["Both conditions must be true", "Either condition can trigger"], symbol, timeframe,
        )
    if re.search(r"liquidity|sweep|fvg|fair value gap|order block|\bmss\b|market structure|bearish reversal|bullish reversal|displacement", lowered):
        limitations = []
        if draft.get("rr", 2.0) < 1.5:
            limitations.append("The existing ARJUNA engine enforces a minimum 1.5R target.")
        if re.search(r"\b(sl|stop\s*loss|stoploss)\b", lowered):
            limitations.append("The existing ARJUNA engine uses its fixed 1% price-risk stop, not a custom swing stop.")
        limitation = " ".join(limitations) or None
        return StrategyInterpretation(
            None, draft, f"{symbol or 'Selected market'} {timeframe or 'selected timeframe'} — ARJUNA setup",
            "I recognize an ARJUNA price-action setup. Should I use the existing ARJUNA engine's full confirmation rules?",
            ["Use the complete existing ARJUNA strategy", "Add a measurable indicator condition"], symbol, timeframe, limitation,
        )

    summary = _summarize_spec(draft, symbol, timeframe)
    if re.search(r"\b(sl|stop\s*loss|stoploss)\b", text.lower()):
        message = "A custom stop-loss price is not supported by the deterministic demo engine; it keeps its fixed 1% price-risk stop."
        return StrategyInterpretation(None, draft, summary, message,
                                      ["Use the fixed demo stop", "Describe an indicator exit instead"], symbol, timeframe,
                                      validation_message=message)
    try:
        specification = validate_strategy(draft)
        return StrategyInterpretation(specification, draft, summary, symbol=symbol, timeframe=timeframe)
    except StrategyError as exc:
        return StrategyInterpretation(None, draft, summary, str(exc), symbol=symbol,
                                      timeframe=timeframe, validation_message=str(exc))


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
                  "messages": [{"role": "system", "content": "Interpret short natural-language trading requests, including Hindi and Hinglish. Return only a restricted strategy condition JSON object. Never return code or risk settings. Indicators: price, percentage_change, RSI, SMA, EMA, MACD, ATR, VWAP, previous_high, previous_low. Operators: >, <, >=, <=, ==, crosses_above, crosses_below. Each entry/exit condition has indicator, period where required, operator, and either a number or compare_to indicator. Do not invent unsupported ICT price-action rules; leave entry empty if clarification is needed."}, {"role": "user", "content": text}], "max_tokens": 500},
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
        "def evaluate_strategy(frame):\n"
        "    spec = validate_strategy(SPEC)\n"
        "    entries = evaluate_conditions(frame, spec['entry']).fillna(False)\n"
        "    exits = evaluate_conditions(frame, spec['exit']).fillna(False)\n"
        "    return {'side': spec['side'], 'entry': entries, 'exit': exits}\n"
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
    if name == "previous_high":
        return frame.high.shift(1)
    if name == "previous_low":
        return frame.low.shift(1)
    if name == "VWAP":
        typical_price = (frame.high + frame.low + close) / 3
        volume = frame.volume.fillna(0)
        if isinstance(frame.index, pd.DatetimeIndex):
            session = frame.index.tz_convert("UTC").normalize() if frame.index.tz else frame.index.normalize()
            numerator = (typical_price * volume).groupby(session).cumsum()
            denominator = volume.groupby(session).cumsum().replace(0, float("nan"))
        else:
            numerator = (typical_price * volume).cumsum()
            denominator = volume.cumsum().replace(0, float("nan"))
        return numerator / denominator
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
    if not conditions:
        return pd.Series(False, index=frame.index, dtype=bool)
    masks = [condition_mask(frame, item, entry_price=entry_price).fillna(False) for item in conditions]
    return pd.concat(masks, axis=1).all(axis=1)

