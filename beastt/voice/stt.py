"""Speech-to-text: BEASTT listens to you through the microphone.

Records from the mic with `sounddevice` (bundles its own audio engine -- no
compiler/PyAudio hassle on Windows) and transcribes offline with Whisper.

To make BEASTT respond to *spoken words* and not to random noises (a sip of a
drink, a cough, background clatter), the listener applies several filters:

  1. Voice-activity detection that requires *sustained* sound to start
     recording -- a brief transient (slurp/click) won't trigger it.
  2. A minimum amount of voiced audio before we bother transcribing.
  3. Whisper's own `no_speech_prob` / confidence scores to drop noise.
  4. A small blocklist of Whisper's classic noise "hallucinations".

Optionally, a SpeakerVerifier can be supplied so BEASTT only reacts to the
enrolled owner's voice and ignores everyone else.
"""

from __future__ import annotations

from typing import Optional

SAMPLE_RATE = 16000  # Whisper expects 16 kHz mono audio.

# Things Whisper commonly "hears" in silence/noise. We only drop these when the
# model is also unsure it was speech (see _filter_transcription). "bye" etc. are
# intentionally NOT here, so real commands still work.
_HALLUCINATIONS = {
    "you",
    "thank you.",
    "thank you",
    "thanks for watching!",
    "thanks for watching.",
    "please subscribe",
    "subtitles by the amara.org community",
    ".",
    "...",
    "uh",
    "um",
    "mm",
}


class SpeechToText:
    def __init__(self, model: str = "base", speaker_verifier=None):
        self.model_name = model
        self.available = False
        self._sd = None
        self._np = None
        self._model = None
        self._verifier = speaker_verifier
        try:
            import numpy as np
            import sounddevice as sd
            import whisper

            self._np = np
            self._sd = sd
            print(f"[voice] Loading Whisper '{model}' model (first run downloads it)...")
            self._model = whisper.load_model(model)
            self.available = True
        except Exception as exc:
            print(
                f"[voice] Speech-to-text unavailable ({exc}). "
                "Run: pip install sounddevice openai-whisper"
            )

    # --- public API -------------------------------------------------------
    def listen(
        self,
        prompt: str = "Listening...",
        quiet: bool = False,
        start_timeout: float = 8.0,
    ) -> Optional[str]:
        """Capture one spoken utterance and transcribe it.

        Returns transcribed text, "" if nothing usable was heard (noise, or a
        voice that isn't the enrolled owner), or None if STT isn't available.
        `quiet` suppresses the per-utterance logging, which keeps standby mode
        from spamming the terminal while it waits for the wake word.
        """
        if not self.available:
            return None
        try:
            if not quiet:
                print(f"[voice] {prompt}")
            audio = self._record_until_silence(start_timeout=start_timeout)

            # Not enough actual voiced audio -- likely a transient noise.
            min_len = int(0.4 * SAMPLE_RATE)
            if audio is None or len(audio) < min_len:
                return ""

            # Only respond to the owner's voice, if enrolled.
            if self._verifier is not None and getattr(self._verifier, "enrolled", False):
                ok, _score = self._verifier.verify(audio)
                if not quiet:
                    print(f"[voice] match: {self._verifier.explain(audio)}")
                if not ok:
                    # Always report a rejection, even in quiet/standby mode:
                    # silently dropping speech makes the assistant look dead.
                    print(f"[voice] ignored -- not your voice ({self._verifier.explain(audio)})")
                    return ""

            result = self._model.transcribe(audio, fp16=False, language="en")
            return self._filter_transcription(result)
        except Exception as exc:
            print(f"[voice] Couldn't understand audio: {exc}")
            return ""

    # --- transcription filtering -----------------------------------------
    def _filter_transcription(self, result: dict) -> str:
        text = (result.get("text") or "").strip()
        segments = result.get("segments", []) or []

        if segments:
            avg_nsp = sum(s.get("no_speech_prob", 0.0) for s in segments) / len(segments)
            avg_lp = sum(s.get("avg_logprob", 0.0) for s in segments) / len(segments)
            # High "no speech" probability => it wasn't really words.
            if avg_nsp > 0.7:
                return ""
            # Moderately unsure AND a known filler phrase => drop it.
            if avg_nsp > 0.4 and text.lower().strip() in _HALLUCINATIONS:
                return ""
            # Extremely low confidence => garbage.
            if avg_lp < -1.1:
                return ""

        if not text:
            return ""
        return text

    # --- recording --------------------------------------------------------
    def _record_until_silence(
        self,
        max_seconds: float = 15.0,
        start_timeout: float = 8.0,
        silence_duration: float = 1.0,
        min_voiced_seconds: float = 0.3,
    ):
        """Record until the speaker pauses.

        Requires two consecutive loud blocks to *start* (so a brief slurp/click
        doesn't trigger it), keeps a short pre-roll so the first syllable isn't
        clipped, and discards the take if too little of it was actually voiced.
        """
        sd = self._sd
        np = self._np

        block_dur = 0.1
        blocksize = int(SAMPLE_RATE * block_dur)

        with sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=blocksize
        ) as stream:
            threshold = self._calibrate(stream, blocksize)

            frames = []
            pre_roll = []
            started = False
            voiced_streak = 0
            voiced_blocks = 0
            silent_run = 0.0
            waited = 0.0

            while True:
                block, _ = stream.read(blocksize)
                mono = block[:, 0].copy()
                rms = float(np.sqrt(np.mean(np.square(mono))))
                loud = rms > threshold

                if not started:
                    pre_roll.append(mono)
                    pre_roll = pre_roll[-3:]  # ~0.3s of pre-roll
                    if loud:
                        voiced_streak += 1
                        if voiced_streak >= 2:  # sustained -> real speech
                            started = True
                            frames.extend(pre_roll)
                            voiced_blocks += voiced_streak
                    else:
                        voiced_streak = 0
                    waited += block_dur
                    if waited >= start_timeout:
                        break  # nobody spoke
                else:
                    frames.append(mono)
                    if loud:
                        voiced_blocks += 1
                        silent_run = 0.0
                    else:
                        silent_run += block_dur
                        if silent_run >= silence_duration:
                            break
                    if len(frames) * block_dur >= max_seconds:
                        break

        if not frames or (voiced_blocks * block_dur) < min_voiced_seconds:
            return np.array([], dtype=np.float32)
        return np.concatenate(frames).astype(np.float32)

    def _calibrate(self, stream, blocksize, seconds: float = 0.4) -> float:
        """Sample ambient noise to set a sensible speech threshold."""
        np = self._np
        block_dur = 0.1
        levels = []
        for _ in range(max(1, int(seconds / block_dur))):
            block, _ = stream.read(blocksize)
            mono = block[:, 0]
            levels.append(float(np.sqrt(np.mean(np.square(mono)))))
        ambient = sum(levels) / len(levels)
        return max(0.015, ambient * 3.5)
