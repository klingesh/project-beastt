"""Speech-to-text: BEASTT listens to you through the microphone.

Records from the mic with `sounddevice` (which bundles its own audio engine,
so there's no compiler/PyAudio hassle on Windows) and transcribes offline with
Whisper. Uses a simple energy-based voice-activity detection so it starts
capturing when you speak and stops after a short pause.

If the voice extras aren't installed, `listen()` returns None and the caller
should fall back to typed input.
"""

from __future__ import annotations

from typing import Optional

SAMPLE_RATE = 16000  # Whisper expects 16 kHz mono audio.


class SpeechToText:
    def __init__(self, model: str = "base"):
        self.model_name = model
        self.available = False
        self._sd = None
        self._np = None
        self._model = None
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
    def listen(self, prompt: str = "Listening...") -> Optional[str]:
        """Capture one spoken utterance and transcribe it.

        Returns the transcribed text, "" if nothing was heard, or None if STT
        isn't available at all.
        """
        if not self.available:
            return None
        try:
            print(f"[voice] {prompt}")
            audio = self._record_until_silence()
            if audio is None or len(audio) == 0:
                return ""
            result = self._model.transcribe(audio, fp16=False, language="en")
            return (result.get("text") or "").strip()
        except Exception as exc:
            print(f"[voice] Couldn't understand audio: {exc}")
            return ""

    # --- recording --------------------------------------------------------
    def _record_until_silence(
        self,
        max_seconds: float = 15.0,
        start_timeout: float = 8.0,
        silence_duration: float = 1.0,
    ):
        """Record from the mic until the speaker pauses.

        Waits up to `start_timeout` for speech to begin, then keeps recording
        until `silence_duration` seconds of quiet, or `max_seconds` total.
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
            started = False
            silent_run = 0.0
            waited = 0.0

            while True:
                block, _ = stream.read(blocksize)
                mono = block[:, 0]
                rms = float(np.sqrt(np.mean(np.square(mono))))

                if rms > threshold:
                    started = True
                    silent_run = 0.0
                    frames.append(mono.copy())
                elif started:
                    silent_run += block_dur
                    frames.append(mono.copy())
                    if silent_run >= silence_duration:
                        break
                else:
                    waited += block_dur
                    if waited >= start_timeout:
                        break  # nobody spoke

                if started and (len(frames) * block_dur) >= max_seconds:
                    break

        if not frames:
            return np.array([], dtype=np.float32)
        return np.concatenate(frames).astype(np.float32)

    def _calibrate(self, stream, blocksize, seconds: float = 0.4) -> float:
        """Sample the ambient noise floor to set a sensible speech threshold."""
        np = self._np
        block_dur = 0.1
        levels = []
        for _ in range(max(1, int(seconds / block_dur))):
            block, _ = stream.read(blocksize)
            mono = block[:, 0]
            levels.append(float(np.sqrt(np.mean(np.square(mono)))))
        ambient = sum(levels) / len(levels)
        # Speak-detection threshold: comfortably above ambient, with a floor.
        return max(0.012, ambient * 3.0)
