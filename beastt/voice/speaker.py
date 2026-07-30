"""Speaker verification -- lets BEASTT respond to only YOUR voice.

Turns speech into a compact "voiceprint" embedding, then decides whether an
incoming utterance came from the enrolled owner.

Why this design: a single absolute similarity threshold is unreliable, because
"how similar is this to the owner?" varies with mic distance, mood, and phrase.
Two similar-sounding people easily land on the same side of a fixed cut-off.

So we use **cohort (relative) scoring**, the approach real speaker-verification
systems use:

  * Owner enrollment stores *every* clip's embedding (not just an average), and
    an utterance is scored by its BEST match against them.
  * You can also enroll "other" people (friends/family) as an imposter cohort.
  * An utterance is accepted only if it looks clearly more like the owner than
    like anyone in the cohort -- i.e. (owner_score - best_imposter_score) must
    exceed a margin. This comparison cancels out most session/mic variation.

Falls back to a plain absolute threshold when no imposters are enrolled.
This is a consensual, on-device personalisation feature for the owner's own
voice, like a phone's voice-match unlock.
"""

from __future__ import annotations

import os
import sys
import types
from typing import List, Tuple

SAMPLE_RATE = 16000


def _ensure_webrtcvad() -> None:
    """Make `import webrtcvad` succeed even when it isn't installed.

    Resemblyzer imports webrtcvad only to trim silence in `preprocess_wav`,
    which BEASTT never calls (we do our own voice-activity detection).
    webrtcvad is a C extension with no prebuilt wheel for newer Pythons on
    Windows, so rather than require a C++ compiler we register a no-op stand-in.
    """
    try:
        import webrtcvad  # noqa: F401
        return
    except Exception:
        pass
    stub = types.ModuleType("webrtcvad")

    class _Vad:
        def __init__(self, mode: int = 0):
            self.mode = mode

        def is_speech(self, *args, **kwargs) -> bool:
            return True

    stub.Vad = _Vad
    sys.modules["webrtcvad"] = stub


class SpeakerVerifier:
    def __init__(
        self,
        voiceprint_path: str,
        threshold: float = 0.80,
        margin: float = 0.04,
    ):
        self.voiceprint_path = voiceprint_path
        self.threshold = threshold          # used when no imposters enrolled
        self.margin = margin                # required owner-vs-imposter lead
        self.available = False
        self.owner_embeds = None            # (n, d) array
        self.imposter_embeds = None         # (m, d) array or None
        self._np = None
        self._encoder = None
        try:
            import numpy as np

            _ensure_webrtcvad()
            from resemblyzer import VoiceEncoder

            self._np = np
            self._encoder = VoiceEncoder(verbose=False)
            self.available = True
        except Exception as exc:
            print(
                f"[voice] Speaker recognition unavailable ({exc}). "
                "Run: pip install librosa && pip install --no-deps resemblyzer"
            )

        if self.available:
            self._load()

    # --- persistence ------------------------------------------------------
    def _load(self) -> None:
        if not os.path.exists(self.voiceprint_path):
            return
        try:
            data = self._np.load(self.voiceprint_path, allow_pickle=False)
            if hasattr(data, "files"):  # .npz bundle
                self.owner_embeds = data["owner"] if "owner" in data.files else None
                if "imposters" in data.files:
                    imp = data["imposters"]
                    self.imposter_embeds = imp if len(imp) else None
            else:  # legacy single-vector .npy
                self.owner_embeds = data.reshape(1, -1)
        except Exception as exc:
            print(f"[voice] Couldn't load voiceprint ({exc}). Re-run --enroll.")

    def _save(self) -> None:
        np = self._np
        path = self.voiceprint_path
        if path.endswith(".npy"):
            path = path[:-4] + ".npz"
            self.voiceprint_path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        payload = {"owner": self.owner_embeds}
        if self.imposter_embeds is not None and len(self.imposter_embeds):
            payload["imposters"] = self.imposter_embeds
        np.savez(path, **payload)

    @property
    def enrolled(self) -> bool:
        return self.owner_embeds is not None and len(self.owner_embeds) > 0

    @property
    def has_cohort(self) -> bool:
        return self.imposter_embeds is not None and len(self.imposter_embeds) > 0

    # --- audio prep -------------------------------------------------------
    def _preprocess(self, wav):
        """Trim silence and volume-normalise so comparisons are voice-to-voice.

        Silence/padding dilutes an embedding, and mic level shifts it; both hurt
        accuracy. Applied identically at enrollment and verification.
        """
        np = self._np
        wav = np.asarray(wav, dtype=np.float32)

        frame = int(0.03 * SAMPLE_RATE)
        if len(wav) >= frame * 2:
            n = len(wav) // frame
            frames = wav[: n * frame].reshape(n, frame)
            energies = np.sqrt(np.mean(np.square(frames), axis=1))
            peak = float(energies.max()) + 1e-8
            voiced = energies > (0.15 * peak)
            if int(voiced.sum()) >= 10:
                wav = frames[voiced].reshape(-1)

        rms = float(np.sqrt(np.mean(np.square(wav)))) + 1e-8
        wav = wav * (0.05 / rms)
        return np.clip(wav, -1.0, 1.0)

    def embed(self, wav):
        """Return a unit-norm voice embedding for 16 kHz mono float32 audio."""
        np = self._np
        emb = self._encoder.embed_utterance(self._preprocess(wav))
        emb = np.asarray(emb, dtype=np.float32)
        return emb / (np.linalg.norm(emb) + 1e-8)

    def _chunk_embeds(self, wav, chunk_seconds: float = 2.0):
        """Embed a clip as several overlapping windows for richer enrollment."""
        np = self._np
        wav = np.asarray(wav, dtype=np.float32)
        size = int(chunk_seconds * SAMPLE_RATE)
        step = size // 2
        embeds = [self.embed(wav)]  # whole clip
        if len(wav) > size + step:
            for start in range(0, len(wav) - size + 1, step):
                embeds.append(self.embed(wav[start : start + size]))
        return embeds

    # --- scoring ----------------------------------------------------------
    def _best(self, emb, bank) -> float:
        if bank is None or len(bank) == 0:
            return -1.0
        return float(self._np.max(bank @ emb))

    def score(self, wav) -> Tuple[float, float]:
        """Return (owner_score, best_imposter_score) for an utterance."""
        emb = self.embed(wav)
        return self._best(emb, self.owner_embeds), self._best(emb, self.imposter_embeds)

    def verify(self, wav) -> Tuple[bool, float]:
        """Return (is_owner, owner_score).

        With a cohort enrolled, accept only if the owner match leads the best
        imposter match by `margin`. Otherwise fall back to the fixed threshold.
        """
        if not self.enrolled:
            return True, 1.0
        own, imp = self.score(wav)
        if imp >= 0.0:  # cohort available -> relative decision
            return (own - imp) >= self.margin and own >= 0.60, own
        return own >= self.threshold, own

    def explain(self, wav) -> str:
        own, imp = self.score(wav)
        if imp >= 0.0:
            return f"you {own:.2f} vs others {imp:.2f} (need lead {self.margin:.2f})"
        return f"you {own:.2f} (need >= {self.threshold:.2f})"

    # --- enrollment -------------------------------------------------------
    def enroll(self, clips: List, as_imposter: bool = False) -> None:
        """Add voice clips for the owner, or for the 'other people' cohort."""
        np = self._np
        if not clips:
            raise ValueError("No audio clips provided for enrollment.")
        embeds = []
        for clip in clips:
            embeds.extend(self._chunk_embeds(clip))
        new = np.stack(embeds).astype(np.float32)

        if as_imposter:
            self.imposter_embeds = (
                new if self.imposter_embeds is None
                else np.concatenate([self.imposter_embeds, new])
            )
        else:
            self.owner_embeds = new  # fresh owner enrollment replaces the old
        self._save()

    def clear_cohort(self) -> None:
        self.imposter_embeds = None
        self._save()
