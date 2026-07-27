"""Speech-to-text: BEASTT listens to you through the microphone.

Uses the SpeechRecognition library with an offline Whisper backend. If the
voice extras aren't installed, `listen()` returns None and the caller should
fall back to typed input.
"""

from __future__ import annotations

from typing import Optional


class SpeechToText:
    def __init__(self, model: str = "base"):
        self.model = model
        self._recognizer = None
        self._mic = None
        self.available = False
        try:
            import speech_recognition as sr  # noqa: F401

            self._sr = sr
            self._recognizer = sr.Recognizer()
            self._mic = sr.Microphone()
            self.available = True
        except Exception as exc:
            print(
                f"[voice] Speech-to-text unavailable ({exc}). "
                "Run: pip install -r requirements-voice.txt"
            )

    def listen(self, prompt: str = "Listening...") -> Optional[str]:
        """Capture one utterance from the mic and transcribe it. None on failure."""
        if not self.available:
            return None
        try:
            with self._mic as source:
                print(f"[voice] {prompt}")
                self._recognizer.adjust_for_ambient_noise(source, duration=0.4)
                audio = self._recognizer.listen(source, timeout=8, phrase_time_limit=15)
            # Offline transcription via Whisper.
            text = self._recognizer.recognize_whisper(audio, model=self.model)
            return text.strip()
        except self._sr.WaitTimeoutError:
            return ""
        except Exception as exc:
            print(f"[voice] Couldn't understand audio: {exc}")
            return ""
