"""Voice enrollment for BEASTT.

Two modes:
  * Owner enrollment  (`python main.py --enroll`)
        Records the owner speaking and builds their voiceprint.
  * Cohort enrollment (`python main.py --enroll-other`)
        Records someone *else* (a friend/family member) as a "not me" sample.
        This is what makes the voice lock reliable: BEASTT then decides by
        comparing -- does this sound more like the owner, or more like a known
        other person? -- instead of guessing at an absolute threshold.

After each run a quick calibration report shows the separation achieved.
"""

from __future__ import annotations

from .config import Config

SAMPLE_RATE = 16000
_CLIP_SECONDS = 5.0

_OWNER_PHRASES = [
    "Hey BEASTT, it's me. I'm the one you should listen to.",
    "The quick brown fox jumps over the lazy dog every single morning.",
    "I love building cool things with technology and solving hard problems.",
    "Today is a great day to talk with my assistant about anything at all.",
    "Remember my voice carefully so you always know that it is really me.",
    "One two three four five, six seven eight nine ten, this is my normal voice.",
]

_OTHER_PHRASES = [
    "Hello BEASTT, this is somebody else speaking to you right now.",
    "The quick brown fox jumps over the lazy dog every single morning.",
    "I am not the owner of this assistant, so please do not listen to me.",
    "One two three four five, six seven eight nine ten, this is a different voice.",
]


def _record(sd, np, seconds: float):
    audio = sd.rec(
        int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32"
    )
    sd.wait()
    return audio[:, 0].astype(np.float32)


def _collect(sd, np, phrases) -> list:
    clips = []
    for i, phrase in enumerate(phrases, 1):
        input(f"\n[{i}/{len(phrases)}] Press Enter, then say:\n    \"{phrase}\"\n> ")
        print(f"[enroll] Recording {_CLIP_SECONDS:.0f}s... speak now!")
        clip = _record(sd, np, _CLIP_SECONDS)
        rms = float(np.sqrt(np.mean(np.square(clip))))
        if rms < 0.005:
            print("[enroll] That was very quiet -- let's redo this one.")
            input("        Press Enter and speak a bit louder/closer.\n> ")
            clip = _record(sd, np, _CLIP_SECONDS)
        clips.append(clip)
        print("[enroll] Got it!")
    return clips


def _report(verifier, clips, label: str) -> None:
    """Print how the just-recorded clips score, so separation is visible."""
    try:
        print(f"\n[enroll] Calibration check ({label}):")
        for i, clip in enumerate(clips, 1):
            own, imp = verifier.score(clip)
            if imp >= 0.0:
                print(f"         clip {i}: owner {own:.2f} | others {imp:.2f}")
            else:
                print(f"         clip {i}: owner {own:.2f}")
    except Exception:
        pass


def run_enrollment(config: Config, as_imposter: bool = False) -> None:
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as exc:
        print(f"[enroll] Microphone libraries missing ({exc}).")
        print("        Run: pip install sounddevice")
        return

    from .voice.speaker import SpeakerVerifier

    verifier = SpeakerVerifier(
        config.voiceprint_path,
        threshold=config.speaker_threshold,
        margin=config.speaker_margin,
    )
    if not verifier.available:
        print("[enroll] Needs Resemblyzer:")
        print("        pip install librosa && pip install --no-deps resemblyzer")
        return

    if as_imposter:
        if not verifier.enrolled:
            print("[enroll] Enroll yourself first:  python main.py --enroll")
            return
        print("\n=== BEASTT: teaching another person's voice ===")
        print("Hand the mic to the OTHER person (your friend).")
        print("I'll learn their voice as 'not the owner' so I can tell you apart.\n")
        phrases = _OTHER_PHRASES
    else:
        print("\n=== BEASTT voice enrollment (owner) ===")
        print("I'll record a few phrases to learn YOUR voice.")
        print("Speak naturally, at your normal distance from the mic.\n")
        phrases = _OWNER_PHRASES

    clips = _collect(sd, np, phrases)

    print("\n[enroll] Building voiceprint...")
    verifier.enroll(clips, as_imposter=as_imposter)
    print(f"[enroll] Saved to {verifier.voiceprint_path}")

    _report(verifier, clips, "other person" if as_imposter else "you")

    if as_imposter:
        print("\n[enroll] Done! Now run:  python main.py --my-voice")
        print("        I'll accept a voice only if it matches you clearly more")
        print("        than it matches any other person I've learned.")
    else:
        if not verifier.has_cohort:
            print("\n[enroll] Owner voiceprint ready.")
            print("        STRONGLY RECOMMENDED next step -- teach me the voices")
            print("        that should be rejected:  python main.py --enroll-other")
        else:
            print("\n[enroll] Done! Run:  python main.py --my-voice")
