"""One-time voice enrollment for BEASTT.

Records a few short clips of the owner speaking, builds a voiceprint, and saves
it so BEASTT can thereafter recognise and respond to only that voice.

Run with:  python main.py --enroll
"""

from __future__ import annotations

from .config import Config

SAMPLE_RATE = 16000
_PHRASES = [
    "Hey BEASTT, it's me.",
    "The quick brown fox jumps over the lazy dog.",
    "I love building cool things with technology.",
    "Today is a great day to talk with my assistant.",
    "Remember my voice so you know it's really me.",
]
_CLIP_SECONDS = 4.0


def run_enrollment(config: Config) -> None:
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as exc:
        print(f"[enroll] Microphone libraries missing ({exc}).")
        print("        Run: pip install sounddevice")
        return

    from .voice.speaker import SpeakerVerifier

    verifier = SpeakerVerifier(config.voiceprint_path, threshold=config.speaker_threshold)
    if not verifier.available:
        print("[enroll] Speaker recognition needs Resemblyzer. Run: pip install resemblyzer")
        return

    print("\n=== BEASTT voice enrollment ===")
    print("I'll record a few short phrases so I can learn your voice.")
    print("Speak naturally, in a quiet room. Press Enter before each phrase.\n")

    clips = []
    for i, phrase in enumerate(_PHRASES, 1):
        input(f"[{i}/{len(_PHRASES)}] Press Enter, then say:  \"{phrase}\"")
        print(f"[enroll] Recording for {_CLIP_SECONDS:.0f}s... speak now!")
        audio = sd.rec(
            int(_CLIP_SECONDS * SAMPLE_RATE),
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
        )
        sd.wait()
        clip = audio[:, 0].astype(np.float32)
        rms = float(np.sqrt(np.mean(np.square(clip))))
        if rms < 0.005:
            print("[enroll] That was very quiet -- let's try that one again.")
            # Re-do this phrase.
            audio = sd.rec(
                int(_CLIP_SECONDS * SAMPLE_RATE),
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
            )
            sd.wait()
            clip = audio[:, 0].astype(np.float32)
        clips.append(clip)
        print("[enroll] Got it!\n")

    print("[enroll] Building your voiceprint...")
    verifier.enroll(clips)
    print(f"[enroll] Done! Voiceprint saved to {config.voiceprint_path}")
    print("        BEASTT will now respond to only your voice when you run:")
    print("        python main.py --voice --my-voice\n")
