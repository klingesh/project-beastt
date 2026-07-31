"""Command-line interface for BEASTT.

Three ways to run:
  * text chat        -- `python main.py --text`
  * voice chat       -- `python main.py --voice` / `--my-voice`
  * standby (wake)   -- `python main.py --wake`
                        BEASTT idles listening for its name, then asks whether
                        you want to talk by voice or by text, and returns to
                        standby when the conversation ends.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from .assistant import Assistant
from .config import Config

def _banner(name: str) -> str:
    """A simple name-agnostic banner, so renaming the assistant just works."""
    spaced = "  ".join(name.upper())
    line = "=" * (len(spaced) + 8)
    return f"\n{line}\n    {spaced}\n{line}\n   your personal AI friend\n"

# Single words that mean "goodbye" on their own.
_EXIT_WORDS = {"bye", "byebye", "goodbye", "exit", "quit", "stop", "goodnight"}
# Multi-word farewell phrases.
_EXIT_PHRASES = {"see you", "see ya", "see you later", "good night", "talk later"}


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="assistant", description="Your personal JARVIS-style AI companion."
    )
    p.add_argument(
        "--voice",
        action="store_true",
        help="Enable voice mode (BEASTT listens on the mic and speaks replies).",
    )
    p.add_argument(
        "--text",
        action="store_true",
        help="Force text-only chat, even if voice is enabled in .env.",
    )
    p.add_argument(
        "--wake",
        action="store_true",
        help="Standby mode: idle until you call 'BEASTT', then choose voice or text.",
    )
    p.add_argument(
        "--on-wake",
        choices=["ask", "voice", "text"],
        default=None,
        help="What to do after waking (default: ask).",
    )
    p.add_argument(
        "--model", default=None, help="Override the Ollama model (e.g. qwen3, phi4)."
    )
    p.add_argument(
        "--name", default=None, help="What BEASTT should call you this session."
    )
    p.add_argument("--quiet", action="store_true", help="Hide startup diagnostics.")
    p.add_argument(
        "--service",
        action="store_true",
        help="Run headless in the background (logs to a file, auto-restarts).",
    )
    p.add_argument(
        "--install-startup",
        action="store_true",
        help="Start BEASTT automatically when you log in, hidden in the background.",
    )
    p.add_argument(
        "--uninstall-startup",
        action="store_true",
        help="Stop BEASTT from starting automatically.",
    )
    p.add_argument(
        "--status",
        action="store_true",
        help="Diagnose the setup: is BEASTT running, enrolled, and configured?",
    )
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
        help="(Advanced) Respond only to your enrolled voice. Needs --enroll-other "
             "to be reliable; off by default.",
    )
    p.add_argument(
        "--no-voice-lock",
        action="store_true",
        help="Respond to any voice, overriding BEASTT_MY_VOICE_ONLY in .env.",
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
    # Explicit opt-out always wins, so a stale .env can't lock the user out.
    if args.no_voice_lock:
        config.speaker_only = False
    if args.on_wake:
        config.on_wake = args.on_wake
    # Standby mode always needs the mic.
    if args.wake:
        config.voice_enabled = True
    # --text always wins, so it can override BEASTT_VOICE=on in .env.
    if args.text and not args.wake:
        config.voice_enabled = False
        config.speaker_only = False
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


# --- voice setup -----------------------------------------------------------
def _build_verifier(config: Config, announce: bool = True):
    """Create the speaker verifier when the voice lock is requested."""
    if not config.speaker_only:
        return None
    from .voice import SpeakerVerifier

    verifier = SpeakerVerifier(
        config.voiceprint_path,
        threshold=config.speaker_threshold,
        margin=config.speaker_margin,
    )
    if not verifier.available:
        print("[voice] Speaker recognition off (Resemblyzer not installed).")
        return None
    if not verifier.enrolled:
        print(
            "[voice] No voiceprint found -- responding to all voices. "
            "Run `python main.py --enroll` first to lock it to your voice."
        )
    elif announce:
        if verifier.has_cohort:
            print("[voice] Voice lock ON (comparing against known other voices).")
        else:
            print(
                f"[voice] Voice lock ON using a fixed threshold "
                f"({verifier.threshold:.2f}) -- the less reliable mode."
            )
            print("        If it ignores you, run:  python main.py --enroll-other")
    return verifier


# --- the conversation ------------------------------------------------------
def _chat_session(assistant: Assistant, config: Config, tts, stt) -> None:
    """Run one conversation until the user says goodbye.

    `stt` is None for a typed session. Returns when the chat ends; the caller
    decides whether to exit or go back to standby.
    """
    assistant.voice_mode = stt is not None and getattr(stt, "available", False)

    greeting = assistant.welcome()
    print(f"\n{config.name}: {greeting}\n")
    if tts:
        tts.say(greeting)

    while True:
        try:
            if stt is not None and stt.available:
                user_text = stt.listen()
                if user_text:
                    print(f"You: {user_text}")
                if not user_text:
                    continue
            else:
                user_text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{config.name}: Talk soon! I'll be right here.")
            assistant.remember_session()
            return

        if not user_text:
            continue

        if _is_exit(user_text):
            farewell = f"Take care, {config.user_name}! I'll be here whenever you need me."
            print(f"{config.name}: {farewell}")
            if tts:
                tts.say(farewell)
            # Reflect on the chat and store anything worth remembering.
            assistant.remember_session()
            return

        reply = assistant.respond(user_text)
        print(f"{config.name}: {reply}\n")
        if tts:
            tts.say(reply)


# --- standby / wake word ---------------------------------------------------
def _ask_mode(config: Config, tts, wake_stt) -> str:
    """Ask the user whether they want voice or text. Returns 'voice' or 'text'."""
    from .wake import parse_mode_choice

    question = "Yes? Would you like to talk by voice, or by text?"
    print(f"{config.name}: {question}")
    if tts:
        tts.say(question)

    # Try a couple of spoken answers first (hands-free), then fall back to typing.
    for _ in range(2):
        spoken = wake_stt.listen(prompt="Say 'voice' or 'text'...", start_timeout=6.0)
        if spoken:
            print(f"You: {spoken}")
            choice = parse_mode_choice(spoken)
            if choice:
                # Always confirm out loud: in text mode BEASTT otherwise goes
                # silent while waiting at the keyboard, which looks like a hang.
                confirm = (
                    "Okay, I'm listening."
                    if choice == "voice"
                    else "Sure -- opening a chat window for you now."
                )
                print(f"{config.name}: {confirm}")
                if tts:
                    tts.say(confirm)
                return choice
            nudge = "Sorry, voice or text?"
            print(f"{config.name}: {nudge}")
            if tts:
                tts.say(nudge)

    try:
        typed = input("Type 'v' for voice or 't' for text [v]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return "voice"
    return parse_mode_choice(typed) or "voice"


def _open_text_window(config: Config) -> bool:
    """Open a visible console running a text chat.

    In background/service mode there is no window to type into, so choosing
    "text" would leave the user with nothing. Spawn a real console (using
    python.exe, not pythonw.exe) and let standby keep listening.
    """
    import subprocess
    import sys

    from .paths import project_root

    exe = Path(sys.executable)
    console_py = exe.with_name("python.exe")
    if not console_py.exists():
        console_py = exe

    cmd = [str(console_py), str(project_root() / "main.py"), "--text"]
    try:
        subprocess.Popen(
            cmd,
            cwd=str(project_root()),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        return True
    except Exception as exc:
        print(f"[wake] Couldn't open a chat window ({exc}).")
        return False


def _run_standby(config: Config, verbose: bool) -> None:
    """Idle listening for the wake word; start a chat when called."""
    from .notify import chime_sleep, chime_wake
    from .voice import SpeechToText, TextToSpeech
    from .wake import detect

    tts = TextToSpeech(rate=config.tts_rate)
    verifier = _build_verifier(config)

    # A small, fast model for standby listening; the full model is used in chat.
    wake_stt = SpeechToText(model=config.wake_model, speaker_verifier=verifier)
    if not wake_stt.available:
        print("[wake] Microphone unavailable, so standby mode can't run.")
        print("       Install voice support:  pip install sounddevice openai-whisper")
        return

    chat_stt = wake_stt
    if config.stt_model != config.wake_model:
        chat_stt = SpeechToText(model=config.stt_model, speaker_verifier=verifier)

    wake_words = config.wake_words()
    print(f"\n[wake] Standby. Call \"{config.name}\" whenever you need me.")
    print(f"       (Listening for: {', '.join(wake_words)})")
    print("       (Ctrl+C to shut down.)\n")

    while True:
        try:
            heard = wake_stt.listen(quiet=True, start_timeout=3600.0)
        except KeyboardInterrupt:
            print("\n[wake] Shutting down. Bye!")
            return

        if not heard:
            continue

        woken, remainder = detect(heard, wake_words)
        if not woken:
            # Log what was heard but not matched -- essential for diagnosing
            # "it never responds" when there's no console to watch.
            print(f"[wake] (heard, not my name: {heard!r})")
            continue

        print(f"[wake] Heard you: {heard!r}")
        chime_wake()  # audible "I'm listening", since there may be no window

        # Decide how to converse.
        mode = config.on_wake
        if mode not in ("voice", "text"):
            mode = _ask_mode(config, tts, wake_stt)
        print(f"[wake] Starting {mode} chat.")

        # Headless (background service): a text chat needs its own window.
        if mode == "text" and os.environ.get("BEASTT_HEADLESS") == "1":
            if _open_text_window(config):
                print("[wake] Opened a separate console for the text chat.")
            else:
                spoken = "I couldn't open a window, so let's talk by voice instead."
                print(f"{config.name}: {spoken}")
                if tts:
                    tts.say(spoken)
                mode = "voice"
            if mode == "text":
                chime_sleep()
                print(f"[wake] Back on standby. Call \"{config.name}\" anytime.\n")
                continue

        # A fresh Assistant each time gives a clean conversation but keeps
        # long-term memory, which lives on disk.
        assistant = Assistant(config=config, verbose=verbose)
        session_tts = tts if mode == "voice" else None
        session_stt = chat_stt if mode == "voice" else None

        # If they said "BEASTT, what's the weather?", answer that straight away.
        if remainder:
            print(f"You: {remainder}")
            reply = assistant.respond(remainder)
            print(f"{config.name}: {reply}\n")
            if session_tts:
                session_tts.say(reply)
            _continue_session(assistant, config, session_tts, session_stt)
        else:
            _chat_session(assistant, config, session_tts, session_stt)

        chime_sleep()
        print(f"\n[wake] Back on standby. Call \"{config.name}\" anytime.\n")


def _continue_session(assistant: Assistant, config: Config, tts, stt) -> None:
    """Continue an already-greeted conversation (used when waking with a question)."""
    while True:
        try:
            if stt is not None and stt.available:
                user_text = stt.listen()
                if user_text:
                    print(f"You: {user_text}")
                if not user_text:
                    continue
            else:
                user_text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            assistant.remember_session()
            return

        if not user_text:
            continue

        if _is_exit(user_text):
            farewell = f"Take care, {config.user_name}! I'll be here whenever you need me."
            print(f"{config.name}: {farewell}")
            if tts:
                tts.say(farewell)
            assistant.remember_session()
            return

        reply = assistant.respond(user_text)
        print(f"{config.name}: {reply}\n")
        if tts:
            tts.say(reply)


# --- entry point -----------------------------------------------------------
def run(argv=None) -> None:
    args = _parse_args(argv)
    config = _build_config(args)
    verbose = not args.quiet

    # Diagnostics, then exit.
    if args.status:
        from .status import report

        report()
        return

    # Autostart management, then exit.
    if args.install_startup or args.uninstall_startup:
        from . import autostart

        if args.uninstall_startup:
            autostart.uninstall()
        else:
            extra = "--on-wake voice" if config.on_wake == "voice" else ""
            # Only enable the voice lock in the installed command when it was
            # explicitly asked for; it's off by default because a mismatch
            # silently stops the assistant from responding at all.
            lock = "--my-voice" if (args.my_voice and config.speaker_only) else ""
            autostart.install(" ".join(x for x in ["--wake", lock, "--service", extra] if x))
        return

    # One-time voice enrollment, then exit.
    if args.enroll or args.enroll_other:
        from .enroll import run_enrollment

        run_enrollment(config, as_imposter=args.enroll_other)
        return

    # Background service: supervised standby with file logging.
    if args.service:
        from .service import run_service

        run_service(config)
        return

    if verbose:
        print(_banner(config.name))

    # Standby mode manages its own assistants per conversation.
    if args.wake:
        _run_standby(config, verbose)
        return

    assistant = Assistant(config=config, verbose=verbose)

    tts = stt = None
    if config.voice_enabled:
        from .voice import SpeechToText, TextToSpeech

        tts = TextToSpeech(rate=config.tts_rate)
        verifier = _build_verifier(config)
        stt = SpeechToText(model=config.stt_model, speaker_verifier=verifier)
        if not stt.available:
            print("[voice] Microphone input unavailable -- falling back to typed input.")
            stt = None

    _chat_session(assistant, config, tts, stt)
