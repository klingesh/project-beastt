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
import sys
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
        "--ui",
        action="store_true",
        help="Open the chat interface in your browser (multiple conversations).",
    )
    p.add_argument(
        # No default here: it comes from BEASTT_UI_PORT via the config, so a
        # hardcoded 8765 would silently override the setting.
        "--port", type=int, default=None, help="Port for the chat interface."
    )
    p.add_argument(
        "--watch-log",
        action="store_true",
        help="Open a live view of what the background service is doing.",
    )
    p.add_argument(
        "--no-browser",
        action="store_true",
        help="Start the interface but don't open a browser window.",
    )
    p.add_argument(
        "--wake",
        action="store_true",
        help="Standby mode: idle until you call 'BEASTT', then choose voice or text.",
    )
    p.add_argument(
        "--on-wake",
        choices=["ask", "voice", "text", "ui"],
        default=None,
        help="What to do after waking: ask, voice, text, or ui (open the chat "
             "in a browser). Default: ask.",
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
    if args.port:
        config.ui_port = args.port
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
def _chat_session(assistant: Assistant, config: Config, tts, stt,
                  greeted: bool = False) -> None:
    """Run one conversation until the user says goodbye.

    `stt` is None for a typed session. Returns when the chat ends; the caller
    decides whether to exit or go back to standby.

    `greeted` says the user has already been welcomed -- standby now acknowledges
    the wake word by name, and greeting twice in three seconds sounds like a
    stutter rather than warmth.
    """
    assistant.voice_mode = stt is not None and getattr(stt, "available", False)

    if not greeted:
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
def _can_prompt() -> bool:
    """Is there a keyboard to ask a question at?

    The autostart service runs under `pythonw.exe`, which has no console at all:
    `sys.stdin` is None and `input()` raises RuntimeError("lost sys.stdin"). That
    took the whole service down every single time the wake word was heard, so it
    crash-looped on the one thing it exists to do.

    A piped stdin is still usable -- `input()` works and raises EOFError at the
    end, which callers already handle -- so this deliberately does not require a
    terminal. It only asks whether stdin exists at all.
    """
    if os.environ.get("BEASTT_HEADLESS") == "1":
        return False
    return getattr(sys, "stdin", None) is not None


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

    if not _can_prompt():
        # Running as the background service: there is no keyboard to fall back
        # to. Voice is the right default rather than an arbitrary one -- we got
        # here because the wake word was heard, so speech demonstrably works.
        print("[wake] No console to type at; carrying on by voice.")
        spoken = "I didn't catch that, so let's talk by voice."
        if tts:
            tts.say(spoken)
        return "voice"

    try:
        typed = input("Type 'v' for voice or 't' for text [v]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return "voice"
    except (RuntimeError, OSError, ValueError) as exc:
        # stdin can also disappear mid-run (a closed console, a redirect that
        # went away). Asking a question must never be what ends the session.
        print(f"[wake] Couldn't read the keyboard ({exc}); carrying on by voice.")
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


def _warn_if_already_listening() -> list:
    """Warn when a background service is already on the microphone.

    Two listeners on one microphone is worse than either alone: each grabs the
    input stream in turn, so a wake word lands in whichever happened to be
    recording and the other hears the silence. The symptom is intermittent
    deafness -- which is indistinguishable from the wake word simply not working,
    and is the state someone is in the moment they open a terminal to find out
    why the service seems deaf.

    A warning rather than a refusal: running a second copy on purpose, to watch
    it, is a legitimate thing to want. It just needs saying out loud.
    """
    try:
        from .status import _running

        pids = _running()
    except Exception:
        return []
    if not pids:
        return []

    print("\n[wake] WARNING: a background service is already listening "
          f"(pid {', '.join(pids)}).")
    print("       Two listeners share one microphone badly -- each takes it in "
          "turn, so")
    print("       whichever is not recording misses you. Waking will be "
          "unreliable until")
    print("       one of them stops.")
    print("       To watch the one already running instead:  "
          "python main.py --watch-log")
    print("       To stop it:  taskkill /F /IM pythonw.exe\n")
    return pids


def _toast_wake(config: Config, greeting: str) -> None:
    """A desktop notification as well as the spoken greeting.

    Belt and braces, and cheap. If the speakers are muted, or the TTS voice
    failed to load, or the user is wearing headphones plugged into something
    else, the spoken acknowledgement is invisible -- and this whole feature
    exists because an invisible acknowledgement is indistinguishable from not
    being heard.
    """
    try:
        from .botwatch import safe_for_toast
        from .notify import toast

        toast(str(getattr(config, "name", "BEASTT")), safe_for_toast(greeting))
    except Exception:
        pass


def _run_standby(config: Config, verbose: bool) -> None:
    """Idle listening for the wake word; start a chat when called."""
    from .notify import chime_sleep, chime_wake
    from .personality import wake_greeting
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

    #: One log window per standby run. There is no way to tell whether a window
    #: the user closed is still open, so opening one on every wake would bury the
    #: screen in consoles after a busy afternoon.
    console_opened = False

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

        # Say their name back, immediately, before deciding anything.
        #
        # This is the whole answer to "I say Jarvis and it stays silent". A beep
        # confirms that *something* happened; being greeted by name confirms that
        # the name was recognised and that it was recognised as them -- which is
        # the question actually being asked. Previously the first words spoken
        # were "would you like to talk by voice, or by text?", which is a fine
        # question and a poor acknowledgement.
        greeting = wake_greeting(config.user_name)
        print(f"{config.name}: {greeting}")
        if tts:
            tts.say(greeting)
        _toast_wake(config, greeting)

        if getattr(config, "wake_console", False) and not console_opened:
            from . import logview

            if logview.open_window(config):
                console_opened = True
                print("[wake] Opened a window showing what I'm doing.")

        # Decide how to converse.
        mode = config.on_wake
        if mode not in ("voice", "text", "ui"):
            mode = _ask_mode(config, tts, wake_stt)
        print(f"[wake] Starting {mode} chat.")

        # Straight to the browser. The interface is where most of the work
        # happens anyway, and a spoken greeting plus a window appearing is
        # unambiguous feedback in a way that a beep never was.
        if mode == "ui":
            from . import uilaunch

            ok, spoken = uilaunch.ensure(getattr(config, "ui_port", 8765))
            if ok and remainder:
                spoken += " Ask me your question in there."
            print(f"{config.name}: {spoken}")
            if tts:
                tts.say(spoken)
            chime_sleep()
            print(f"[wake] Back on standby. Call \"{config.name}\" anytime.\n")
            continue

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
            # Already greeted by name a moment ago, so don't do it again.
            _chat_session(assistant, config, session_tts, session_stt,
                          greeted=True)

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
    if args.watch_log:
        # Before the banner: this window is a log viewer, and a banner above the
        # log just pushes the interesting part off the top.
        from . import logview

        return logview.run(config)

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
            # Bake in whichever mode is configured, not just "voice". A launcher
            # that silently drops the setting is how "ask" came back after
            # someone had chosen otherwise.
            extra = (f"--on-wake {config.on_wake}"
                     if config.on_wake in ("voice", "text", "ui") else "")
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

    # Web chat interface: manages one conversation per thread itself.
    if args.ui:
        from .webui import serve

        serve(config, port=config.ui_port, open_browser=not args.no_browser)
        return

    # Standby mode manages its own assistants per conversation.
    if args.wake:
        # Only in the foreground. The service reaches _run_standby directly, and
        # would otherwise warn about itself on every restart.
        _warn_if_already_listening()
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
