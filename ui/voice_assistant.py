"""Safe voice copilot for navigation and read-only market context.

The browser owns microphone permission and speech playback. The server only
receives normalized transcript text, maps it to a small validated intent set,
and delegates navigation to the existing router. No arbitrary code, SQL, or
execution action is reachable from this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import streamlit as st

from ui import components as ui
from ui import marketdata, navigation

OPEN_KEY = "voice_assistant_open"
LAST_TRANSCRIPT_KEY = "voice_assistant_last_transcript"
ATTENTION_KEY = "voice_trade_attention_seen"
LAST_ROUTE_KEY = "voice_last_route"

_COMPONENTS_BY_RUNTIME: dict[int, object] = {}

_HTML = """
<div class="voice-avatar-root">
  <div class="voice-avatar" aria-hidden="true"><span>✦</span></div>
  <div class="voice-avatar-state" aria-live="polite"></div>
  <button class="voice-listen" type="button">Start listening</button>
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
  if (!state) state = parentElement.__voiceAvatar = { recognition: null, lastReply: null };
  const button = parentElement.querySelector(".voice-listen");
  const avatar = parentElement.querySelector(".voice-avatar");
  const status = parentElement.querySelector(".voice-avatar-state");
  const setStatus = (text) => { if (status) status.textContent = text; setStateValue("status", text); };
  if (button && !button.__bound) {
    button.__bound = true;
    button.onclick = () => {
      const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!Recognition) { setStatus("Voice input is unavailable in this browser. You can type below."); return; }
      try {
        const recognition = new Recognition();
        state.recognition = recognition;
        recognition.lang = data.language || "en-IN";
        recognition.interimResults = false;
        recognition.continuous = false;
        recognition.onstart = () => {
          button.disabled = true; button.textContent = "Listening…";
          if (avatar) avatar.classList.add("listening"); setStatus("Listening…");
        };
        recognition.onresult = (event) => {
          const transcript = Array.from(event.results || []).map((result) => result[0].transcript).join(" ").trim();
          if (transcript) setStateValue("transcript", transcript);
          setStatus(transcript ? "Transcript received." : "No speech was detected.");
        };
        recognition.onerror = (event) => setStatus(`Microphone: ${event.error || "recognition failed"}.`);
        recognition.onend = () => {
          button.disabled = false; button.textContent = "Start listening";
          if (avatar) avatar.classList.remove("listening");
        };
        recognition.start();
      } catch (_) { setStatus("Could not start the microphone. Check browser permission."); }
    };
  }
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
    if any(word in lowered for word in ("portfolio", "positions", "p&l", "pnl")):
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
    return VoiceIntent(action, symbol, timeframe, _language(normalized))


def _reply(intent: VoiceIntent, current_route: str) -> str:
    """Build a response from actual screen state, never invented market facts."""
    symbol = intent.symbol or st.session_state.get("shell_symbol", "BTC/USDT")
    language = intent.language == "hinglish"
    if intent.action == "unknown":
        return ("Main sirf safe market, research, portfolio aur chart actions handle "
                "kar sakta hoon. Asset aur request batao." if language else
                "I can help with safe market, research, portfolio, and chart actions. "
                "Please name an asset and request.")
    if intent.action == "market":
        return (f"{symbol} selected hai. Analysis, risk ya backtest ke baare mein "
                "pooch sakte ho." if language else
                f"{symbol} is selected. You can ask for analysis, risk, or a backtest.")
    if intent.action == "timeframe":
        return (f"Chart {intent.timeframe} timeframe par set kar diya." if language else
                f"The chart timeframe is set to {intent.timeframe}.")
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


def _component(language: str, speak_text: str):
    from streamlit.components.v2.get_bidi_component_manager import get_bidi_component_manager
    manager_id = id(get_bidi_component_manager())
    component = _COMPONENTS_BY_RUNTIME.get(manager_id)
    if component is None:
        component = st.components.v2.component(
            "voice_research_avatar", html=_HTML, css=_CSS, js=_JS)
        _COMPONENTS_BY_RUNTIME[manager_id] = component
    return component(
        key="voice_research_avatar_instance",
        data={"language": "hi-IN" if language == "hi" else "en-IN",
              "speak_text": speak_text, "reply_language": "hi-IN" if language == "hi" else "en-IN"},
        default={"transcript": "", "status": ""},
        on_transcript_change=lambda: None, on_status_change=lambda: None,
        width="stretch", height=72,
    )


@st.dialog("Voice research assistant", width="small")
def _dialog(current_route: str) -> None:
    """Compact, user-initiated voice interaction surface."""
    st.caption("English, Hindi, or Hinglish · read-only assistant")
    response = _component("en", st.session_state.get("voice_assistant_reply", ""))
    transcript = " ".join(str(getattr(response, "transcript", "") or "").split())[:500]
    if transcript and transcript != st.session_state.get(LAST_TRANSCRIPT_KEY):
        st.session_state[LAST_TRANSCRIPT_KEY] = transcript
        intent = parse_intent(transcript)
        _apply(intent)
        st.session_state["voice_assistant_reply"] = _reply(intent, current_route)
        st.session_state[OPEN_KEY] = True
        st.rerun()
    typed = st.text_input("Or type your request", key="voice_assistant_text",
                          placeholder="Bhai BTC ka analysis kar…")
    if st.button("Ask assistant", key="voice_assistant_ask", type="primary",
                 width="stretch") and typed.strip():
        intent = parse_intent(typed)
        _apply(intent)
        st.session_state["voice_assistant_reply"] = _reply(intent, current_route)
        st.session_state[OPEN_KEY] = True
        st.rerun()
    reply = st.session_state.get("voice_assistant_reply")
    if reply:
        ui.html_block(ui.card(ui.esc(reply), variant="flat"))
    st.caption("Paper mode only. No orders, code, or database actions are available.")


def render_avatar(current_route: str) -> None:
    """Render the top-navigation avatar and one-time Trade attention cue."""
    previous = st.session_state.get(LAST_ROUTE_KEY)
    entering_trade = current_route == navigation.TRADE and previous != navigation.TRADE
    should_attention = entering_trade and not st.session_state.get(ATTENTION_KEY, False)
    st.session_state[LAST_ROUTE_KEY] = current_route
    if should_attention:
        st.session_state[ATTENTION_KEY] = True
        ui.html_block(
            '<div class="voice-trade-attention" style="animation:voice-attention 6s '
            'ease-out forwards"><strong>Trade setup samajhna hai?</strong> Mujhse poochho.</div>'
        )
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
            "Haan, batao kya analyse karna hai?" if current_route == navigation.TRADE
            else "Haan, batao platform mein kya dhoondhna hai?")
        _dialog(current_route)
