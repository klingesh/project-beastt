"""BEASTT's brain -- pluggable LLM backends."""

from .base import Brain, Message
from .ollama_brain import OllamaBrain
from .fallback_brain import FallbackBrain
from .factory import build_brain

__all__ = ["Brain", "Message", "OllamaBrain", "FallbackBrain", "build_brain"]
