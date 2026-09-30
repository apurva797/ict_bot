"""Browser voice input adapter for the existing natural-language interpreter."""

from __future__ import annotations

import re
from typing import Protocol

import streamlit as st

_HTML = '<div class="voice-root"><button type="button" class="voice-start">🎙️ Speak strategy</button><span class="voice-status" aria-live="polite"></span></div>'
_CSS = """
.voice-root { display:flex; align-items:center; gap:10px; min-height:42px; }
.voice-start { border:1px solid #b7c6d8; border-radius:8px; background:#f4f7fb; color:#142033; padding:9px 14px; font:600 14px sans-serif; cursor:pointer; }
.voice-start:disabled { opacity:.65; cursor:wait; }
.voice-status { color:#495057; font:13px sans-serif; }
"""
_JS = r"""
export default function(component) {
  const { data, parentElement, setStateValue } = component;
  let state = parentElement.__strategyVoice;
  if (!state) state = parentElement.__strategyVoice = { lastReply: null, recognition: null };
  const button = parentElement.querySelector(".voice-start");
  const status = parentElement.querySelector(".voice-status");
  if (button) button.onclick = () => {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      const message = "Voice input is unavailable in this browser. Type your strategy instead.";
      if (status) status.textContent = message;
      setStateValue("status", message);
      return;
    }
    try {
      const recognition = new Recognition();
      state.recognition = recognition;
      recognition.lang = data.language || "en-IN";
      recognition.interimResults = false;
      recognition.continuous = false;
      recognition.onstart = () => { button.disabled = true; if (status) status.textContent = "Listening…"; };
      recognition.onresult = (event) => {
        const transcript = Array.from(event.results || []).map((result) => result[0].transcript).join(" ").trim();
        if (transcript) setStateValue("transcript", transcript);
        setStateValue("status", transcript ? "Transcript ready — review it below." : "No speech was detected.");
      };
      recognition.onerror = (event) => setStateValue("status", `Voice input: ${event.error || "recognition failed"}. You can type instead.`);
      recognition.onend = () => { button.disabled = false; };
      recognition.start();
    } catch (_) {
      button.disabled = false;
      setStateValue("status", "Could not start voice input. Check microphone permission or type instead.");
    }
  };
  if (data.speak_text && state.lastReply !== data.speak_text && window.speechSynthesis) {
    state.lastReply = data.speak_text;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(data.speak_text);
    utterance.lang = data.reply_language || data.language || "en-IN";
    window.speechSynthesis.speak(utterance);
  }
};
"""

_COMPONENTS_BY_RUNTIME: dict[int, object] = {}


class VoiceTranscriptProvider(Protocol):
    """Provider boundary between speech recognition and the strategy parser."""

    def normalize_transcript(self, transcript: str) -> str: ...


class BrowserSpeechRecognitionProvider:
    """Adapter for transcript text returned by Web Speech API in the browser."""

    def normalize_transcript(self, transcript: str) -> str:
        if not isinstance(transcript, str):
            return ""
        return re.sub(r"\s+", " ", transcript).strip()[:500]


_VOICE_PROVIDER: VoiceTranscriptProvider = BrowserSpeechRecognitionProvider()


def apply_voice_transcript(transcript: str) -> str:
    """Normalize browser speech text through the swappable provider boundary."""
    return _VOICE_PROVIDER.normalize_transcript(transcript)


def render_voice_strategy_controls(language: str = "en-IN", speak_text: str = "",
                                   reply_language: str | None = None) -> tuple[str, str]:
    """Render optional browser speech input and one-shot spoken responses."""
    from streamlit.components.v2.get_bidi_component_manager import get_bidi_component_manager

    manager_id = id(get_bidi_component_manager())
    component = _COMPONENTS_BY_RUNTIME.get(manager_id)
    if component is None:
        component = st.components.v2.component(
            "strategy_voice_input", html=_HTML, css=_CSS, js=_JS,
        )
        _COMPONENTS_BY_RUNTIME[manager_id] = component
    result = component(
        key="strategy_voice_input_instance",
        data={"language": language, "speak_text": speak_text,
              "reply_language": reply_language or language},
        default={"transcript": "", "status": ""},
        on_transcript_change=lambda: None,
        on_status_change=lambda: None,
        width="stretch", height=48,
    )
    return apply_voice_transcript(getattr(result, "transcript", "")), str(getattr(result, "status", ""))
