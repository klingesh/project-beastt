"""Command-line interface for BEASTT -- runs the text or voice chat loop."""

from __future__ import annotations

import argparse

from .assistant import Assistant
from .config import Config

_BANNER = r"""
  ____  _____ _    ____ _____ _____
 | __ )| ____| |  / ___|_   _|_   _|
 |  _ \|  _| | | | |     | |   | |
 | |_) | |___| |_| |___  | |   | |
 |____/|_____|_____\____| |_|   |_|
   your personal AI friend
"""

_EXIT_WORDS = {"bye", "goodbye", "exit", "quit", "see you", "see ya"}


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="beastt", description="BEASTT -- your JARVIS-style AI companion."
    )
    p.add_argument(
        "--voice",
        action="store_true",
        help="Enable voice mode (BEASTT listens on the mic and speaks replies).",
    )
    p.add_argument(
        "--model", default=None, help="Override the Ollama model (e.g. qwen3, phi4)."
    )
    p.add_argument(
        "--name", default=None, help="What BEASTT should call you this session."
    )
    p.add_argument("--quiet", action="store_true", help="Hide startup diagnostics.")
    return p.parse_args(argv)


def _build_config(args: argparse.Namespace) -> Config:
    config = Config.load()
    if args.model:
        config.model = args.model
    if args.name:
        config.user_name = args.name
    if args.voice:
        config.voice_enabled = True
    return config


def _is_exit(text: str) -> bool:
    return text.strip().lower() in _EXIT_WORDS


def run(argv=None) -> None:
    args = _parse_args(argv)
    config = _build_config(args)
    verbose = not args.quiet

    if verbose:
        print(_BANNER)

    assistant = Assistant(config=config, verbose=verbose)

    # Set up voice if requested.
    tts = stt = None
    if config.voice_enabled:
        from .voice import SpeechToText, TextToSpeech

        tts = TextToSpeech(rate=config.tts_rate)
        stt = SpeechToText(model=config.stt_model)
        if not stt.available:
            print("[voice] Microphone input unavailable -- falling back to typed input.")

    # Welcome the user.
    greeting = assistant.welcome()
    print(f"\n{config.name}: {greeting}\n")
    if tts:
        tts.say(greeting)

    # Main loop.
    while True:
        try:
            if stt and stt.available:
                user_text = stt.listen()
                if user_text:
                    print(f"You: {user_text}")
                if not user_text:
                    continue
            else:
                user_text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{config.name}: Talk soon! I'll be right here.")
            break

        if not user_text:
            continue

        if _is_exit(user_text):
            farewell = f"Take care, {config.user_name}! I'll be here whenever you need me."
            print(f"{config.name}: {farewell}")
            if tts:
                tts.say(farewell)
            break

        reply = assistant.respond(user_text)
        print(f"{config.name}: {reply}\n")
        if tts:
            tts.say(reply)
