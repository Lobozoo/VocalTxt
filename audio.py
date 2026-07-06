r"""
audio.py — microphone capture (sounddevice) + Silero VAD speech gating.

The VAD runs Silero's ONNX model directly via onnxruntime (which is already a
faster-whisper dependency) instead of the silero-vad pip package — that
package drags in torch (~2.5 GB installed, hundreds of MB of RAM) to run a
2 MB model. The model file silero_vad.onnx ships alongside the app; if
missing, it is downloaded once to %APPDATA%\EchoScribe.

Pipeline:
  * sounddevice InputStream @ 16 kHz / mono / float32, blocksize 512
    (512 samples is exactly the frame size Silero expects at 16 kHz).
  * The audio callback runs on PortAudio's thread and must never block, so it
    only publishes an RMS level and pushes chunks into a thread-safe queue.
  * A VAD worker thread drains the queue, scores each frame with Silero, and
    assembles a speech buffer:
        - a small pre-roll ring buffer so the first syllable isn't clipped,
        - a hangover window so brief pauses between words aren't cut out.
  * stop() returns the assembled speech as a single float32 numpy array,
    or None if no speech was detected at all.
"""

import logging
import os
import queue
import sys
import threading
from collections import deque

import numpy as np
import sounddevice as sd

from settings import app_data_dir

SAMPLE_RATE = 16000
BLOCK = 512                      # samples per frame — required by Silero @16k
SPEECH_THRESHOLD = 0.5           # Silero probability above which a frame is speech
PRE_ROLL_FRAMES = 10             # ~0.32 s kept before speech onset
HANGOVER_FRAMES = 25             # ~0.8 s kept after speech seems to stop

_SILERO_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/"
    "master/src/silero_vad/data/silero_vad.onnx"
)


def _vad_model_path() -> str:
    """Find silero_vad.onnx: next to the app (or inside the PyInstaller
    bundle), else download once to %APPDATA%\\EchoScribe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    bundled = os.path.join(base, "silero_vad.onnx")
    if os.path.exists(bundled):
        return bundled
    cached = os.path.join(app_data_dir(), "silero_vad.onnx")
    if not os.path.exists(cached):
        import urllib.request  # truststore (injected in main) handles the proxy
        logging.info("Downloading Silero VAD model…")
        urllib.request.urlretrieve(_SILERO_URL, cached)
    return cached


class SileroVAD:
    """Minimal onnxruntime wrapper for Silero VAD v5.

    The v5 model is stateful (RNN state carried between calls) and requires
    each 512-sample chunk to be fed with the previous chunk's last 64 samples
    prepended — 576 samples total. Omitting that context silently wrecks
    accuracy (speech scores ~0.2 instead of ~0.95)."""

    CONTEXT = 64

    def __init__(self, path: str):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        # Single-threaded is plenty: inference is ~0.13 ms per 32 ms frame,
        # and it keeps onnxruntime from spinning up a worker pool at idle.
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            path, sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset_states()

    def reset_states(self):
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(self.CONTEXT, dtype=np.float32)

    def __call__(self, chunk: np.ndarray) -> float:
        """chunk: float32 (512,). Returns speech probability 0..1."""
        x = np.concatenate([self._context, chunk]).reshape(1, -1)
        out, self._state = self._session.run(
            None, {"input": x, "state": self._state, "sr": self._sr}
        )
        self._context = chunk[-self.CONTEXT:]
        return float(out[0][0])


class Recorder:
    def __init__(self):
        self._vad_model: SileroVAD | None = None
        self._queue: queue.Queue = queue.Queue()
        self._stream: sd.InputStream | None = None
        self._worker: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._speech: list[np.ndarray] = []
        self._had_speech = False
        self._lock = threading.Lock()
        # Live mic loudness (0.0–1.0-ish RMS), read by the overlay's animation
        # timer on the GUI thread. A plain float assignment is atomic in
        # Python, so no locking is needed for this.
        self.level: float = 0.0

    # ------------------------------------------------------------- VAD ----

    def _ensure_vad(self):
        """Lazy-load the VAD once; ~2 MB model, loads in well under a second.
        Also called from the startup preload thread so the first hotkey press
        does no heavy lifting."""
        if self._vad_model is None:
            self._vad_model = SileroVAD(_vad_model_path())
            logging.info("Silero VAD loaded (onnxruntime)")

    # -------------------------------------------------------- recording ---

    def start(self):
        with self._lock:
            if self._stream is not None:
                return  # already recording
            self._ensure_vad()
            self._vad_model.reset_states()   # stateful RNN: fresh per recording
            self._queue = queue.Queue()
            self._speech = []
            self._had_speech = False
            self._stop_evt.clear()

            def callback(indata, frames, time_info, status):
                # PortAudio callback thread: copy the buffer (it is reused by
                # the driver) and hand off immediately. Never block here.
                if status:
                    logging.warning("Audio stream status: %s", status)
                mono = indata[:, 0].copy()
                self.level = float(np.sqrt(np.mean(mono * mono)))
                self._queue.put(mono)

            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=BLOCK,
                callback=callback,
            )
            self._stream.start()

            self._worker = threading.Thread(
                target=self._vad_loop, name="vad-worker", daemon=True
            )
            self._worker.start()
            logging.info("Recording started")

    def _vad_loop(self):
        pre_roll: deque[np.ndarray] = deque(maxlen=PRE_ROLL_FRAMES)
        hangover = 0

        while not self._stop_evt.is_set() or not self._queue.empty():
            try:
                chunk = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                prob = self._vad_model(chunk)
            except Exception:
                logging.exception("VAD inference failed — keeping chunk")
                prob = 1.0

            if prob >= SPEECH_THRESHOLD:
                # Speech onset: flush the pre-roll so we don't clip the start.
                if pre_roll:
                    self._speech.extend(pre_roll)
                    pre_roll.clear()
                self._speech.append(chunk)
                self._had_speech = True
                hangover = HANGOVER_FRAMES
            elif hangover > 0:
                # Short pause inside speech — keep it for natural cadence.
                self._speech.append(chunk)
                hangover -= 1
            else:
                pre_roll.append(chunk)

    def stop(self) -> np.ndarray | None:
        """Stop capture, drain VAD, return speech audio (or None if silence)."""
        with self._lock:
            self.level = 0.0
            if self._stream is None:
                return None
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                logging.exception("Error closing input stream")
            self._stream = None

            self._stop_evt.set()
            if self._worker is not None:
                self._worker.join(timeout=5)
                self._worker = None

            if not self._had_speech or not self._speech:
                logging.info("No speech detected — dropping recording")
                return None

            audio = np.concatenate(self._speech).astype(np.float32)
            logging.info("Recording stopped: %.2f s of speech", len(audio) / SAMPLE_RATE)
            return audio
