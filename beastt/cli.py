"""Command-line interface for BEASTT -- runs the text or voice chat loop."""

from __future__ import annotations

import argparse
import re

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

# Single words that mean "goodbye" on their own.
_EXIT_WORDS = {"bye", "byebye", "goodbye", "exit", "quit", "stop", "goodnight"}
# Multi-word farewell phrases.
_EXIT_PHRASES = {"see you", "see ya", "see you later", "good night", "talk later"}


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
    p.add_argument(
        "--enroll",
        action="store_true",
        help="Record your voiceprint so BEASTT can recognise only your voice.",
    )
    p.add_argument(
        "--enroll-other",
        action="store_true",
        help="Teach BEASTT another person's voice to reject (improves accuracy a lot).",
    )
    p.add_argument(
        "--my-voice",
        action="store_true",
        help="Respond to only your enrolled voice, ignoring other speakers.",
    )
    return p.parse_args(argv)


def _build_config(args: argparse.Namespace) -> Config:
    config = Config.load()
    if args.model:
        config.model = args.model
    if args.name:
        config.user_name = args.name
    if args.voice:
        config.voice_enabled = True
    if args.my_voice:
        config.speaker_only = True
        config.voice_enabled = True
    return config


def _is_exit(text: str) -> bool:
    """Detect a goodbye, tolerant of punctuation and speech-to-text quirks.

    Whisper adds punctuation/capitalization (e.g. "Bye."), so we normalise
    first, then match short farewell utterances only (to avoid quitting on a
    sentence that merely mentions "bye" mid-conversation).
    """
    norm = re.sub(r"[^a-z0-9\s]", "", text.lower()).strip()
    if not norm:
        return False
    words = norm.split()
    if len(words) > 6:  # too long to be a simple sign-off
        return False
    if any(w in _EXIT_WORDS for w in words):
        return True
    return any(phrase in norm for phrase in _EXIT_PHRASES)


def run(argv=None) -> None:
    args = _parse_args(argv)
    config = _build_config(args)
    verbose = not args.quiet

    # One-time voice enrollment, then exit.
    if args.enroll or args.enroll_other:
        from .enroll import run_enrollment

        run_enrollment(config, as_imposter=args.enroll_other)
        return

    if verbose:
        print(_BANNER)

    assistant = Assistant(config=config, verbose=verbose)

    # Set up voice if requested.
    tts = stt = None
    if config.voice_enabled:
        from .voice import SpeechToText, TextToSpeech

        tts = TextToSpeech(rate=config.tts_rate)

        # Optionally restrict listening to the owner's enrolled voice.
        verifier = None
        if config.speaker_only:
            from .voice import SpeakerVerifier

            verifier = SpeakerVerifier(
                config.voiceprint_path,
                threshold=config.speaker_threshold,
                margin=config.speaker_margin,
            )
            if not verifier.available:
                print("[voice] Speaker recognition off (Resemblyzer not installed).")
                verifier = None
            elif not verifier.enrolled:
                print(
                    "[voice] No voiceprint found -- responding to all voices. "
                    "Run `python main.py --enroll` first to lock it to your voice."
                )
            elif verifier.has_cohort:
                print("[voice] Voice lock ON (comparing against known other voices).")
            else:
                print("[voice] Voice lock ON -- but accuracy is much better if you")
                print("        also run: python main.py --enroll-other")

        stt = SpeechToText(model=config.stt_model, speaker_verifier=verifier)
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
