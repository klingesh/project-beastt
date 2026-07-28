"""Speaker verification -- lets BEASTT recognise and respond to only YOUR voice.

Uses Resemblyzer's VoiceEncoder to turn a snippet of speech into a compact
"voiceprint" embedding. You enroll once (recording a few phrases); after that,
every incoming utterance is embedded and compared (cosine similarity) to your
enrolled voiceprint. If it doesn't match closely enough, BEASTT ignores it.

This is a consensual, on-device personalisation feature for the owner's own
voice -- like a phone's voice-match unlock. If Resemblyzer isn't installed,
this degrades gracefully and BEASTT simply responds to any voice.
"""

from __future__ import annotations

import os
import sys
import types
from typing import List, Tuple


def _ensure_webrtcvad() -> None:
    """Make `import webrtcvad` succeed even when it isn't installed.

    Resemblyzer imports webrtcvad only to trim silence in `preprocess_wav`,
    which BEASTT never calls (we feed already-captured audio straight to the
    encoder and do our own voice-activity detection). webrtcvad is a C
    extension with no prebuilt wheel for newer Pythons on Windows, so rather
    than force users to install a C++ compiler, we register a tiny no-op stand-in.
    """
    try:
        import webrtcvad  # noqa: F401  (real one if present)
        return
    except Exception:
        pass
    stub = types.ModuleType("webrtcvad")

    class _Vad:  # minimal API surface Resemblyzer expects
        def __init__(self, mode: int = 0):
            self.mode = mode

        def is_speech(self, *args, **kwargs) -> bool:
            return True

    stub.Vad = _Vad
    sys.modules["webrtcvad"] = stub


class SpeakerVerifier:
    def __init__(self, voiceprint_path: str, threshold: float = 0.82):
        self.voiceprint_path = voiceprint_path
        self.threshold = threshold
        self.available = False
        self.reference = None
        self._np = None
        self._encoder = None
        try:
            import numpy as np

            _ensure_webrtcvad()  # slot in the stand-in before importing resemblyzer
            from resemblyzer import VoiceEncoder

            self._np = np
            self._encoder = VoiceEncoder(verbose=False)
            self.available = True
        except Exception as exc:
            print(
                f"[voice] Speaker recognition unavailable ({exc}). "
                "Run: pip install resemblyzer"
            )

        if self.available:
            self._load_voiceprint()

    # --- persistence ------------------------------------------------------
    def _load_voiceprint(self) -> None:
        if os.path.exists(self.voiceprint_path):
            try:
                self.reference = self._np.load(self.voiceprint_path)
            except Exception as exc:
                print(f"[voice] Couldn't load voiceprint ({exc}).")

    @property
    def enrolled(self) -> bool:
        return self.reference is not None

    # --- embeddings -------------------------------------------------------
    def _preprocess(self, wav):
        """Trim silence and volume-normalise for a clean, comparable voiceprint.

        Two problems otherwise hurt accuracy:
          * Enrollment clips are fixed-length and include silence/pauses, which
            dilute the voiceprint versus tightly-trimmed live speech.
          * Different mic distances/levels shift the embedding.

        So we drop low-energy (silent) frames, then scale to a consistent
        loudness -- applied identically at enrollment and verification.
        """
        np = self._np
        wav = wav.astype(np.float32)

        # 1. Energy-based silence trimming (30 ms frames).
        frame = int(0.03 * 16000)
        if len(wav) >= frame * 2:
            n = len(wav) // frame
            frames = wav[: n * frame].reshape(n, frame)
            energies = np.sqrt(np.mean(np.square(frames), axis=1))
            peak = float(energies.max()) + 1e-8
            voiced = energies > (0.15 * peak)  # keep frames near speech level
            if int(voiced.sum()) >= 10:  # at least ~0.3s of speech
                wav = frames[voiced].reshape(-1)

        # 2. Volume-normalise.
        rms = float(np.sqrt(np.mean(np.square(wav)))) + 1e-8
        wav = wav * (0.05 / rms)
        return np.clip(wav, -1.0, 1.0)

    def embed(self, audio_16k_mono_float32):
        """Return a normalised voice embedding for a 16 kHz mono float32 clip."""
        wav = self._preprocess(audio_16k_mono_float32)
        return self._encoder.embed_utterance(wav)

    def verify(self, audio_16k_mono_float32) -> Tuple[bool, float]:
        """Return (is_owner, similarity_score) for an utterance."""
        if not self.enrolled:
            return True, 1.0  # nobody enrolled -> accept everyone
        emb = self.embed(audio_16k_mono_float32)
        # Resemblyzer embeddings are L2-normalised, so cosine similarity is the
        # dot product.
        score = float(self._np.inner(emb, self.reference))
        return score >= self.threshold, score

    def enroll(self, clips: List) -> None:
        """Build and save a voiceprint from several recorded clips."""
        if not clips:
            raise ValueError("No audio clips provided for enrollment.")
        embeds = [self.embed(c) for c in clips]
        centroid = self._np.mean(embeds, axis=0)
        centroid = centroid / self._np.linalg.norm(centroid)
        os.makedirs(os.path.dirname(self.voiceprint_path) or ".", exist_ok=True)
        self._np.save(self.voiceprint_path, centroid)
        self.reference = centroid
