"""Safe voice copilot for navigation and read-only market context.

The browser owns microphone permission and speech playback. The server only
receives normalized transcript text, maps it to a small validated intent set,
and delegates navigation to the existing router. No arbitrary code, SQL, or
execution action is reachable from this module.
"""

from __future__ import annotations

import re
import math
import base64
from dataclasses import dataclass
from typing import Any

import streamlit as st

from ui import components as ui
from ui import marketdata, navigation
from core.ai.provider import AIProviderError, AIProviderNetworkError
from core.ai.providers.sarvam import SarvamProvider

OPEN_KEY = "voice_assistant_open"
LAST_TRANSCRIPT_KEY = "voice_assistant_last_transcript"
ATTENTION_KEY = "voice_trade_attention_seen"
LAST_ROUTE_KEY = "voice_last_route"
COMPONENT_UPDATE_KEY = "voice_component_updated"
VOICE_STATUS_KEY = "voice_assistant_status"
LAST_AUDIO_KEY = "voice_assistant_last_audio"

VOICE_STATUSES = {
    "IDLE", "LISTENING", "PROCESSING", "TRANSCRIBING", "RESPONDING",
    "SUCCESS", "MIC_PERMISSION_DENIED", "STT_ERROR", "AI_ERROR", "NETWORK_ERROR",
}

_COMPONENTS_BY_RUNTIME: dict[int, object] = {}

_HTML = """
<div class="voice-avatar-root">
  <div class="voice-avatar" aria-hidden="true"><span>✦</span></div>
  <div class="voice-avatar-state" aria-live="polite"></div>
  <button class="voice-listen" type="button">Start recording</button>
</div>
"""
_CSS = """
.voice-avatar-root { display:flex; align-items:center; gap:12px; min-height:64px; }
.voice-avatar { width:48px; height:48px; border-radius:50%; display:grid;
  place-items:center; color:#f7fbff; background:linear-gradient(145deg,#3858e9,#7b61ff);
  box-shadow:0 0 0 1px rgba(130,150,255,.35),0 8px 26px rgba(44,65,180,.28);
  font:700 22px sans-serif; }
.voice-avatar.listening { animation:voice-pulse 1.4s ease-in-out infinite; }
.voice-listen { border:1px solid #b7c6d8; border-radius:9px; background:#f4f7fb;
  color:#142033; padding:9px 13px; font:600 13px sans-serif; cursor:pointer; }
.voice-listen:disabled { opacity:.65; cursor:wait; }
.voice-avatar-state { color:#667085; font:13px sans-serif; }
@keyframes voice-pulse { 50% { box-shadow:0 0 0 8px rgba(106,113,255,.14),0 8px 26px rgba(44,65,180,.28); } }
"""
_JS = r"""
export default function(component) {
  const { data, parentElement, setStateValue } = component;
  let state = parentElement.__voiceAvatar;
  if (!state) state = parentElement.__voiceAvatar = { recorder: null, chunks: [], lastReply: null };
  const button = parentElement.querySelector(".voice-listen");
  const avatar = parentElement.querySelector(".voice-avatar");
  const status = parentElement.querySelector(".voice-avatar-state");
  const setStatus = (text) => { if (status) status.textContent = text; setStateValue("status", text); };
  if (button && !button.__bound) {
    button.__bound = true;
    button.onclick = () => {
      if (!navigator.mediaDevices || !window.MediaRecorder) {
        setStatus("STT_ERROR"); return;
      }
      try {
        navigator.mediaDevices.getUserMedia({ audio: true }).then((stream) => {
          const recorder = new MediaRecorder(stream);
          state.recorder = recorder; state.chunks = [];
          recorder.onstart = () => {
            button.disabled = true; button.textContent = "Recording…";
            if (avatar) avatar.classList.add("listening"); setStatus("LISTENING");
          };
          recorder.ondataavailable = (event) => { if (event.data.size) state.chunks.push(event.data); };
          recorder.onerror = () => { stream.getTracks().forEach((track) => track.stop()); setStatus("STT_ERROR"); };
          recorder.onstop = () => {
            stream.getTracks().forEach((track) => track.stop());
            button.disabled = false; button.textContent = "Start recording";
            if (avatar) avatar.classList.remove("listening");
            setStatus("PROCESSING");
            const blob = new Blob(state.chunks, { type: recorder.mimeType || "audio/webm" });
            const reader = new FileReader();
            reader.onloadend = () => {
              const encoded = String(reader.result || "").split(",").pop();
              if (encoded) setStateValue("audio_base64", encoded);
            };
            reader.readAsDataURL(blob);
          };
          recorder.start();
          setTimeout(() => { if (recorder.state === "recording") recorder.stop(); }, 15000);
        }).catch((error) => {
          setStatus(error && error.name === "NotAllowedError"
            ? "MIC_PERMISSION_DENIED" : "NETWORK_ERROR");
        });
      } catch (_) { setStatus("STT_ERROR"); }
    };
  }
  if (data.status && status && status.textContent !== data.status) status.textContent = data.status;
  if (data.status !== "LISTENING" && avatar) avatar.classList.remove("listening");
  if (data.speak_text && state.lastReply !== data.speak_text && window.speechSynthesis) {
    state.lastReply = data.speak_text;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(data.speak_text);
    utterance.lang = data.reply_language || "en-IN";
    window.speechSynthesis.speak(utterance);
  }
}
"""


@dataclass(frozen=True)
class VoiceIntent:
    """Validated safe action extracted from a transcript."""

    action: str
    symbol: str | None = None
    timeframe: str | None = None
    language: str = "en"
    request: str = ""


@dataclass(frozen=True)
class StrategyAwareVoiceContext:
    """Safe, display-only context supplied by the current platform surface."""

    strategy: str
    symbol: str
    timeframe: str
    screen: str
    backtest_configuration: dict[str, Any] | None = None
    visible_metrics: dict[str, Any] | None = None


def build_context(current_route: str) -> StrategyAwareVoiceContext:
    """Build context from session state without including credentials or secrets."""
    strategy = str(st.session_state.get("strategy_choice", "") or "")
    if strategy == "ARJUNA Strategy":
        strategy = "ARJUNA"
    elif strategy == "Quant Strategy":
        strategy = "Quant"
    elif strategy == "Multi-Strategy Engine":
        strategy = "Multi-Strategy"
    elif strategy == "AI Strategy":
        strategy = "Custom Strategy"
    result = st.session_state.get("research_multi_strategy_backtest") or {}
    metrics = dict(result.get("metrics") or {})
    strategy_id = ("ict" if strategy == "ARJUNA" else
                   "quant.trend" if strategy == "Quant" else "multi.strategy")
    try:
        from ui import state as paper_state
        account = paper_state.account_for(
            strategy_id, str(st.session_state.get("shell_symbol", "BTC/USDT")),
            str(st.session_state.get("shell_timeframe", "5m")))
    except (KeyError, TypeError, ValueError):
        account = None
    if account:
        position = account.get("position")
        metrics.update({
            "paper_position": position.get("side") if isinstance(position, dict) else "FLAT",
            "paper_entry": position.get("entry") if isinstance(position, dict) else None,
            "paper_mark": account.get("last_mark"),
            "paper_balance": account.get("balance"),
            "realized_pnl": sum(float(trade.get("net_pnl", 0) or 0)
                                for trade in account.get("trades", [])),
        })
    research = st.session_state.get("research_copilot_analysis")
    if isinstance(research, dict):
        metrics["research_summary"] = research.get("summary")
        metrics["research_risks"] = research.get("key_risks")
    return StrategyAwareVoiceContext(
        strategy=strategy or "Platform",
        symbol=str(st.session_state.get("shell_symbol", "BTC/USDT")),
        timeframe=str(st.session_state.get("shell_timeframe", "5m")),
        screen=current_route,
        backtest_configuration=result.get("backtest_config"),
        visible_metrics=metrics,
    )


def _language(text: str) -> str:
    """Classify only the response register; this is not a translation claim."""
    lowered = text.lower()
    hindi_markers = ("ka ", "ke ", "mein", "mujhe", "batao", "dikhao",
                     "kyu", "kya", "samjhao", "hai", " karo", "chahiye")
    if any(marker in lowered for marker in hindi_markers):
        return "hinglish"
    return "en"


def _symbol(text: str) -> str | None:
    match = re.search(r"\b(BTC|ETH|SOL)(?:\s*/?\s*(?:USDT|USD))?\b", text.upper())
    if match:
        return f"{match.group(1)}/USDT"
    names = {"BITCOIN": "BTC/USDT", "ETHEREUM": "ETH/USDT", "SOLANA": "SOL/USDT"}
    for name, value in names.items():
        if name in text.upper():
            return value
    return None


def parse_intent(text: str) -> VoiceIntent:
    """Map natural wording to a bounded intent, refusing unknown actions."""
    normalized = " ".join(str(text or "").split())
    lowered = normalized.lower()
    symbol = _symbol(normalized)
    timeframe = next((value for value in ("5m", "15m", "1h")
                      if re.search(rf"\b{re.escape(value)}\b", lowered)), None)
    if any(word in lowered for word in (
            "buy", "sell", "order", "cancel order", "withdraw", "transfer",
            "place a trade", "execute trade", "live broker")):
        action = "execution_blocked"
    elif any(word in lowered for word in ("portfolio", "position", "positions", "p&l", "pnl")):
        action = "portfolio"
    elif any(word in lowered for word in ("research hub", "backtest", "research")):
        action = "research"
    elif any(word in lowered for word in ("quant lab", "quant strategy")):
        action = "quant"
    elif any(word in lowered for word in ("timeframe", "interval", "set chart",
                                          "chart to")) and timeframe:
        action = "timeframe"
    elif any(word in lowered for word in ("analyse", "analyze", "analysis",
                                           "volatil", "funding", "indicator",
                                           "risk", "move", "setup", "explain")):
        action = "explain"
    elif symbol:
        action = "market"
    else:
        action = "unknown"
    return VoiceIntent(action, symbol, timeframe, _language(normalized), normalized)


def _reply(intent: VoiceIntent, current_route: str,
           context: StrategyAwareVoiceContext | None = None) -> str:
    """Build a response from actual screen state, never invented market facts."""
    symbol = intent.symbol or st.session_state.get("shell_symbol", "BTC/USDT")
    language = intent.language == "hinglish"
    context = context or build_context(current_route)
    if intent.action == "unknown":
        return ("Main sirf safe market, research, portfolio aur chart actions handle "
                "kar sakta hoon. Asset aur request batao." if language else
                "I can help with safe market, research, portfolio, and chart actions. "
                "Please name an asset and request.")
    if intent.action == "execution_blocked":
        return ("Voice Research Assistant read-only hai. Main BUY, SELL, order, "
                "withdrawal ya broker action execute nahi kar sakta." if language else
                "The Voice Research Assistant is read-only and cannot place orders, "
                "cancel orders, withdraw, transfer funds, or perform broker actions.")
    if intent.action == "market":
        return (f"{symbol} selected hai. Analysis, risk ya backtest ke baare mein "
                "pooch sakte ho." if language else
                f"{symbol} is selected. You can ask for analysis, risk, or a backtest.")
    if intent.action == "explain" and intent.symbol is None and context.visible_metrics:
        metrics = context.visible_metrics
        drawdown = metrics.get("Max drawdown %")
        return (f"{context.strategy} ka measured max drawdown "
                f"{float(drawdown):.2f}% hai." if language else
                f"{context.strategy}'s measured maximum drawdown is "
                f"{float(drawdown):.2f}%.")
    if intent.action == "timeframe":
        return (f"Chart {intent.timeframe} timeframe par set kar diya." if language else
                f"The chart timeframe is set to {intent.timeframe}.")
    if intent.action == "portfolio" and any(
            word in intent.request.lower()
            for word in ("position", "p&l", "pnl", "paper")):
        metrics = context.visible_metrics or {}
        position = metrics.get("paper_position")
        if position is None:
            return ("Is information ka reliable data abhi available nahi hai."
                    if language else
                    "Reliable paper-position data is not available right now.")
        mark = metrics.get("paper_mark")
        balance = metrics.get("paper_balance")
        return (f"Paper position {position}. Mark {mark if mark is not None else 'available nahi'}; "
                f"balance {balance if balance is not None else 'available nahi'}."
                if language else
                f"Paper position: {position}. Mark: {mark if mark is not None else 'unavailable'}; "
                f"balance: {balance if balance is not None else 'unavailable'}.")
    if intent.action == "portfolio":
        return ("Portfolio khol raha hoon. Yeh paper/simulation records par based hai."
                if language else "Opening Portfolio. It contains paper/simulation records only.")
    if intent.action == "research":
        return ("Research Hub khol raha hoon. Backtest actual historical candles par chalega."
                if language else
                "Opening Research Hub. Backtests run on actual historical candles.")
    if intent.action == "quant":
        return ("Quant Lab Trade workspace mein available hai. Main order execute nahi "
                "karunga." if language else
                "The Quant workspace is available in Trade. I will not execute orders.")
    snapshot = st.session_state.get("chart_snapshot") or {}
    frame = snapshot.get("frame")
    if snapshot.get("symbol") != symbol or frame is None:
        return (f"{symbol} ka chart load nahi hai. Pehle chart load karo, phir main "
                "available data explain karunga." if language else
                f"The {symbol} chart is not loaded. Load it first, then I can explain "
                "the available data.")
    changes = marketdata.series_changes(frame)
    change = changes.get("change_pct")
    if change is None:
        return (f"{symbol} ka complete change data available nahi hai." if language else
                f"Complete change data is not available for {symbol}.")
    return (f"{symbol} mein recent available candles par change {change:+.2f}% hai. "
            "Yeh descriptive snapshot hai, forecast ya investment advice nahi."
            if language else
            f"{symbol} changed {change:+.2f}% over the recent available candles. "
            "This is a descriptive snapshot, not a forecast or investment advice.")


def _apply(intent: VoiceIntent) -> None:
    """Apply only validated, non-consequential navigation/context changes."""
    if intent.symbol:
        st.session_state["shell_symbol"] = intent.symbol
    if intent.action == "timeframe" and intent.timeframe:
        st.session_state["shell_timeframe"] = intent.timeframe
    if intent.action in {"market", "timeframe", "explain"}:
        navigation.go(navigation.TRADE if intent.action == "timeframe" else navigation.MARKETS)
    elif intent.action == "portfolio":
        navigation.go(navigation.PORTFOLIO)
    elif intent.action == "research":
        navigation.go(navigation.RESEARCH)
    elif intent.action in {"quant"}:
        navigation.go(navigation.TRADE)
        st.session_state["strategy_choice"] = "Quant Strategy"


def _component(language: str, speak_text: str,
               context: StrategyAwareVoiceContext):
    from streamlit.components.v2.get_bidi_component_manager import get_bidi_component_manager
    manager_id = id(get_bidi_component_manager())
    component = _COMPONENTS_BY_RUNTIME.get(manager_id)
    if component is None:
        component = st.components.v2.component(
            "voice_research_avatar", html=_HTML, css=_CSS, js=_JS)
        _COMPONENTS_BY_RUNTIME[manager_id] = component
    metrics = {
        key: (None if isinstance(value, float) and not math.isfinite(value) else value)
        for key, value in (context.visible_metrics or {}).items()
        if isinstance(value, (int, float, str, type(None)))
    }
    def on_transcript_change() -> None:
        # Component state changes trigger this callback on the server rerun.
        # The dialog then reads the returned transcript and processes it once.
        st.session_state[COMPONENT_UPDATE_KEY] = True

    return component(
        key="voice_research_avatar_instance",
        data={"language": "hi-IN" if language == "hi" else "en-IN",
              "speak_text": speak_text,
              "reply_language": "hi-IN" if language == "hi" else "en-IN",
              "status": st.session_state.get(VOICE_STATUS_KEY, "IDLE"),
              "context": {
                  "strategy": context.strategy, "symbol": context.symbol,
                  "timeframe": context.timeframe, "screen": context.screen,
                  "backtest_configuration": context.backtest_configuration,
                  "visible_metrics": metrics,
              }},
        default={"transcript": "", "status": "IDLE", "audio_base64": ""},
        on_transcript_change=on_transcript_change,
        on_status_change=lambda: None,
        width="stretch", height=72,
    )


@st.cache_data(ttl=60, show_spinner=False)
def _sarvam_health() -> tuple[bool, str]:
    """Cache one authenticated provider check instead of probing every rerun."""
    return SarvamProvider().health_check()


def _process_audio(audio_base64: str, context: StrategyAwareVoiceContext) -> None:
    """Decode and transcribe browser audio entirely on the server."""
    st.session_state[VOICE_STATUS_KEY] = "TRANSCRIBING"
    try:
        audio = base64.b64decode(audio_base64, validate=True)
        if len(audio) > 10 * 1024 * 1024:
            raise AIProviderError("Recorded audio is too large.")
        transcript = SarvamProvider().transcribe(
            audio, filename="voice.webm", language_code="unknown")
    except AIProviderNetworkError as exc:
        st.session_state[VOICE_STATUS_KEY] = "NETWORK_ERROR"
        st.session_state["voice_assistant_reply"] = str(exc)
        return
    except AIProviderError as exc:
        st.session_state[VOICE_STATUS_KEY] = "STT_ERROR"
        st.session_state["voice_assistant_reply"] = str(exc)
        return
    except (ValueError, base64.binascii.Error):
        st.session_state[VOICE_STATUS_KEY] = "STT_ERROR"
        st.session_state["voice_assistant_reply"] = "Recorded audio could not be decoded."
        return
    except OSError:
        st.session_state[VOICE_STATUS_KEY] = "NETWORK_ERROR"
        st.session_state["voice_assistant_reply"] = "The voice network request failed."
        return
    st.session_state[VOICE_STATUS_KEY] = "RESPONDING"
    intent = parse_intent(transcript[:500])
    _apply(intent)
    st.session_state["voice_assistant_reply"] = _reply(intent, context.screen, context)
    st.session_state[LAST_TRANSCRIPT_KEY] = transcript[:500]
    st.session_state[VOICE_STATUS_KEY] = "SUCCESS"


@st.dialog("Voice research assistant", width="small")
def _dialog(context: StrategyAwareVoiceContext) -> None:
    """Compact, user-initiated voice interaction surface."""
    st.caption(
        f"{context.strategy} · {context.symbol} · {context.timeframe} · "
        f"{context.screen} · English, Hindi, or Hinglish · read-only")
    health_ok, health_message = _sarvam_health()
    st.caption(f"SARVAM  {'🟢 AVAILABLE' if health_ok else '🔴 UNAVAILABLE'}")
    if not health_ok:
        st.caption(health_message)
    response = _component("en", st.session_state.get("voice_assistant_reply", ""), context)
    component_status = str(getattr(response, "status", "") or "")
    if component_status in VOICE_STATUSES:
        st.session_state[VOICE_STATUS_KEY] = component_status
    audio_base64 = str(getattr(response, "audio_base64", "") or "")
    if audio_base64 and audio_base64 != st.session_state.get(LAST_AUDIO_KEY):
        st.session_state[LAST_AUDIO_KEY] = audio_base64
        _process_audio(audio_base64, context)
        st.rerun()
    transcript = " ".join(str(getattr(response, "transcript", "") or "").split())[:500]
    if transcript and transcript != st.session_state.get(LAST_TRANSCRIPT_KEY):
        st.session_state[LAST_TRANSCRIPT_KEY] = transcript
        intent = parse_intent(transcript)
        _apply(intent)
        st.session_state["voice_assistant_reply"] = _reply(intent, context.screen, context)
        st.session_state[OPEN_KEY] = True
        st.rerun()
    typed = st.text_input("Or type your request", key="voice_assistant_text",
                          placeholder="Bhai BTC ka analysis kar…")
    if st.button("Ask assistant", key="voice_assistant_ask", type="primary",
                 width="stretch") and typed.strip():
        intent = parse_intent(typed)
        _apply(intent)
        st.session_state["voice_assistant_reply"] = _reply(intent, context.screen, context)
        st.session_state[OPEN_KEY] = True
        st.rerun()
    reply = st.session_state.get("voice_assistant_reply")
    if reply:
        ui.html_block(ui.card(ui.esc(reply), variant="flat"))
    st.caption("Paper mode only. No orders, code, or database actions are available.")


def render_avatar(current_route: str,
                  context: StrategyAwareVoiceContext | None = None) -> None:
    """Render the top-navigation avatar and one-time Trade attention cue."""
    context = context or build_context(current_route)
    previous = st.session_state.get(LAST_ROUTE_KEY)
    entering_trade = current_route == navigation.TRADE and previous != navigation.TRADE
    should_attention = entering_trade and not st.session_state.get(ATTENTION_KEY, False)
    st.session_state[LAST_ROUTE_KEY] = current_route
    if should_attention:
        st.session_state[ATTENTION_KEY] = True
        message = ("Multiple strategies compare karni hain? Mujhse poochho."
                   if context.strategy == "Multi-Strategy"
                   else f"{context.strategy} selected hai. Setup, risk ya backtest ke "
                        "baare mein pooch sakte ho.")
        ui.html_block(f'<div class="voice-trade-attention" style="animation:voice-attention 6s '
                      f'ease-out forwards"><strong>{ui.esc(message)}</strong></div>')
    st.markdown(
        "<style>.voice-trade-attention{position:fixed;right:1.5rem;top:4.7rem;z-index:5;"
        "padding:.45rem .7rem;border-radius:999px;background:var(--ui-surface);"
        "border:1px solid var(--ui-border);box-shadow:0 8px 20px rgba(20,30,70,.12);"
        "font-size:.78rem;pointer-events:none}@keyframes voice-attention{0%,75%{opacity:1}"
        "100%{opacity:0;transform:translateY(-4px)}}</style>",
        unsafe_allow_html=True,
    )
    st.button("✦ AI Voice", key="voice_avatar_launcher", on_click=lambda: st.session_state.update(
        {OPEN_KEY: True}), help="Ask the voice research assistant")
    if st.session_state.pop(OPEN_KEY, False):
        st.session_state["voice_assistant_reply"] = (
            (f"{context.strategy} selected hai. Haan, batao kya analyse karna hai?"
             if current_route == navigation.TRADE else
             "Haan, batao platform mein kya dhoondhna hai?"))
        _dialog(context)
