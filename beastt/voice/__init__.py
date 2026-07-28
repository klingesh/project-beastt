"""BEASTT's voice -- optional speech output (TTS) and speech input (STT).

These modules import their heavy dependencies lazily so the core text app runs
fine even when the voice extras aren't installed.
"""

from .tts import TextToSpeech
from .stt import SpeechToText
from .speaker import SpeakerVerifier

__all__ = ["TextToSpeech", "SpeechToText", "SpeakerVerifier"]
