r"""
transcribe.py — faster-whisper (CTranslate2) wrapper.

The model is loaded once and kept in memory; reloading per recording would add
seconds of latency. Changing model size in Settings just marks the model dirty
and it is reloaded lazily on the next transcription.

Models are cached in %APPDATA%\Talkloom\models\ — faster-whisper's built-in
HuggingFace download kicks in automatically on first use of a given size.
"""

import logging
import os
import threading

import numpy as np

from settings import models_dir


def _detect_device() -> tuple[str, str]:
    """Return (device, compute_type): CUDA if available, else CPU int8."""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    # int8 on CPU is the sweet spot for speed vs accuracy.
    return "cpu", "int8"


def model_is_cached(size: str) -> bool:
    """Rough check whether a model of this size already exists on disk,
    so the UI knows whether to show a download progress dialog."""
    root = models_dir()
    try:
        for entry in os.listdir(root):
            if size in entry.lower():
                # model dir must contain the CT2 weights file
                inner = os.path.join(root, entry)
                for dirpath, _dirs, files in os.walk(inner):
                    if "model.bin" in files:
                        return True
    except Exception:
        pass
    return False


class Transcriber:
    def __init__(self, model_size: str = "base", language: str | None = "en"):
        self.model_size = model_size
        # Pinning the language skips Whisper's per-utterance auto-detection,
        # which is unreliable on short clips (it guesses from ~1 s of audio).
        # None = auto-detect.
        self.language = language
        self._model = None
        self._lock = threading.Lock()   # model load + inference are serialised

    def set_model_size(self, size: str):
        with self._lock:
            if size != self.model_size:
                self.model_size = size
                self._model = None      # reload lazily on next use
                logging.info("Model size changed to %s (will reload)", size)

    def load(self):
        """Load (and download if needed) the model. Safe to call repeatedly."""
        with self._lock:
            self._load_locked()

    def _load_locked(self):
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        device, compute_type = _detect_device()
        logging.info(
            "Loading Whisper '%s' on %s (%s)…", self.model_size, device, compute_type
        )
        self._model = WhisperModel(
            self.model_size,
            device=device,
            compute_type=compute_type,
            download_root=models_dir(),
        )
        logging.info("Whisper model ready")

    def transcribe(self, audio: np.ndarray, hotwords: str | None = None) -> str:
        """audio: float32 mono @16 kHz. Returns the raw transcript text.
        hotwords: optional biasing string (custom dictionary words)."""
        with self._lock:
            self._load_locked()
            segments, _info = self._model.transcribe(
                audio,
                language=self.language, # pinned (config) or None = auto-detect
                hotwords=hotwords,      # nudge decoding toward dictionary words
                vad_filter=True,        # second-stage VAD trims residual silence
                beam_size=5,
            )
            # segments is a generator — consuming it runs the actual decode.
            return " ".join(seg.text.strip() for seg in segments).strip()
