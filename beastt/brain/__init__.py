"""BEASTT's brain -- pluggable LLM backends."""

from .base import Brain, Message
from .ollama_brain import OllamaBrain
from .openai_compat import OpenAICompatBrain, OpenAICompatError
from .fallback_brain import FallbackBrain
from .factory import build_brain

__all__ = [
    "Brain", "Message", "OllamaBrain", "OpenAICompatBrain",
    "OpenAICompatError", "FallbackBrain", "build_brain",
]
