"""Text-to-speech: BEASTT speaks its replies out loud.

Uses pyttsx3 (offline, cross-platform, zero config). If pyttsx3 isn't
installed, TTS silently no-ops so the app still runs.
"""

from __future__ import annotations


class TextToSpeech:
    def __init__(self, rate: int = 175, voice_hint: str = ""):
        self._engine = None
        self.available = False
        try:
            import pyttsx3

            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", rate)
            if voice_hint:
                self._select_voice(voice_hint)
            self.available = True
        except Exception as exc:
            print(f"[voice] Text-to-speech unavailable ({exc}). Run: pip install pyttsx3")

    def _select_voice(self, hint: str) -> None:
        try:
            for v in self._engine.getProperty("voices"):
                if hint.lower() in (v.name or "").lower():
                    self._engine.setProperty("voice", v.id)
                    return
        except Exception:
            pass

    def say(self, text: str) -> None:
        if not self.available or not text:
            return
        try:
            self._engine.say(text)
            self._engine.runAndWait()
        except Exception as exc:
            print(f"[voice] Couldn't speak: {exc}")
