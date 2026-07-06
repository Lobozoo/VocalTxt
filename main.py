"""
main.py — EchoScribe entry point.

Wires together: config → transcriber → recorder → hotkey listener → tray.

Threading model
---------------
  * Qt event loop        : main thread (tray icon, dialogs).
  * pynput listener      : its own thread; hotkey callbacks fire there.
  * VAD worker           : background thread per recording (audio.py).
  * transcribe pipeline  : a fresh daemon thread per utterance, so a slow
                           transcription never blocks the Qt loop or the
                           hotkey listener.
State changes and notifications cross into the GUI thread via Qt signals.
"""

# Corporate proxies (Zscaler etc.) re-sign TLS traffic with their own root CA.
# truststore makes Python validate against the Windows certificate store —
# where IT has installed that CA — instead of certifi's bundle. Must run
# before anything imports httpx/requests.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass  # not installed — fine on non-corporate machines

import logging
import sys
import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QProgressDialog

import cleanup
import dictionary
import history
import inject
import settings
import stats
from audio import Recorder
from hotkey import HotkeyListener
from transcribe import Transcriber, model_is_cached
from tray import Tray


class _LoaderSignals(QObject):
    finished = Signal(bool, str)   # ok, error message


class AppController:
    """Owns all the pieces; passed around so the tray/dialog can reach them."""

    def __init__(self, qt_app: QApplication):
        self.qt_app = qt_app
        self.config = settings.Config()
        self.transcriber = Transcriber(
            self.config["model_size"], self.config["language"]
        )
        self.recorder = Recorder()
        self.hotkeys = HotkeyListener(
            self.config["hotkey"],
            on_activate=self._on_hotkey_down,
            on_deactivate=self._on_hotkey_up,
        )
        self.tray = Tray(self)
        # The overlay reuses the tray's state signal: one emit drives both the
        # tray icon and the on-screen waveform pill.
        from overlay import Overlay
        self.overlay = Overlay(self.recorder)
        self.tray.set_state.connect(self.overlay.apply_state)
        self.hotkeys.set_secondary(
            self.config["add_word_hotkey"], self._on_add_word_hotkey
        )
        self._busy = threading.Lock()   # one utterance processed at a time

    # ------------------------------------------------------- lifecycle ----

    def start(self):
        self.tray.show()
        self.hotkeys.start()
        self._preload_model()
        # Watchdog: if Windows silently kills the keyboard hook (slow callback,
        # sleep/lock cycle), detect it and bring the hotkey back to life.
        self._watchdog = QTimer()
        self._watchdog.setInterval(15_000)
        self._watchdog.timeout.connect(self._check_listener)
        self._watchdog.start()

    def _check_listener(self):
        try:
            if not self.hotkeys.is_running():
                logging.warning("Hotkey listener found dead — restarting")
                self.hotkeys.restart()
        except Exception:
            logging.exception("Listener watchdog failed")

    def quit(self):
        try:
            self.hotkeys.stop()
        except Exception:
            pass
        self.qt_app.quit()

    # ---------------------------------------------------- model preload ---

    def _preload_model(self):
        """Load Whisper at startup so the first dictation is fast. If the
        model isn't cached yet, show a download progress dialog."""
        size = self.config["model_size"]
        dlg = None
        if not model_is_cached(size):
            dlg = QProgressDialog(
                f"Downloading Whisper '{size}' model from HuggingFace…\n"
                "This only happens once.", None, 0, 0
            )
            dlg.setWindowTitle("EchoScribe — first run")
            dlg.setCancelButton(None)
            dlg.setMinimumDuration(0)
            dlg.show()

        sig = _LoaderSignals()

        def _close(ok: bool, err: str):
            if dlg is not None:
                dlg.close()
            if not ok:
                self.tray.notify.emit("EchoScribe", f"Model load failed — check logs")
                logging.error("Model preload failed: %s", err)

        sig.finished.connect(_close)

        def _load():
            try:
                self.transcriber.load()
                # Warm the VAD too, so the first hotkey press doesn't have to
                # load anything on the keyboard hook's critical path.
                self.recorder._ensure_vad()
                sig.finished.emit(True, "")
            except Exception as e:
                logging.exception("Model preload failed")
                sig.finished.emit(False, str(e))

        threading.Thread(target=_load, name="model-loader", daemon=True).start()

    # ------------------------------------------------- dictation pipeline -

    def _on_hotkey_down(self):
        """Runs on the pynput HOOK thread — must return in milliseconds.
        Windows silently removes keyboard hooks whose callbacks are slow
        (opening an audio stream takes 100-500 ms), which kills the hotkey
        with no error until restart. So: emit the state change and hand the
        real work to a thread immediately."""
        self.tray.set_state.emit("recording")
        self._start_thread = threading.Thread(
            target=self._start_recording, name="rec-start", daemon=True
        )
        self._start_thread.start()

    def _start_recording(self):
        try:
            self.recorder.start()
        except Exception:
            logging.exception("Failed to start recording")
            self.tray.set_state.emit("idle")
            self.tray.notify.emit("EchoScribe", "Could not open microphone — check logs")

    def _on_hotkey_up(self):
        """Also the hook thread; same rule — spawn and return."""
        threading.Thread(target=self._process, name="pipeline", daemon=True).start()

    def _on_add_word_hotkey(self):
        """Hook thread — spawn and return, same rule as the dictation hotkey."""
        threading.Thread(target=self._add_word, name="add-word", daemon=True).start()

    def _add_word(self):
        try:
            text = dictionary.capture_selection(
                wait_released=self.hotkeys.all_released
            )
            if not dictionary.valid_entry(text):
                self.tray.notify.emit(
                    "EchoScribe", "Select a single word or short phrase first"
                )
                return
            words = self.config["dictionary"]
            if text in words:
                self.tray.notify.emit("EchoScribe", f'"{text}" is already in the dictionary')
                return
            words.append(text)
            self.config.save()
            logging.info("Dictionary add: %r (%d entries)", text, len(words))
            self.tray.notify.emit("EchoScribe", f'Added "{text}" to dictionary')
        except Exception:
            logging.exception("Add-word failed")
            self.tray.notify.emit("EchoScribe", "Could not read selection — check logs")

    def _process(self):
        # Never crash the tray app: everything is wrapped.
        if not self._busy.acquire(blocking=False):
            # Previous utterance is still transcribing. Stop and DISCARD this
            # recording — otherwise the mic stream is left open forever and
            # the next dictation would include everything captured since.
            try:
                self.recorder.stop()
            except Exception:
                logging.exception("Failed to discard overlapping recording")
            logging.info("Dropped recording — previous one still processing")
            return
        try:
            self.tray.set_state.emit("processing")
            # Quick tap race: on a very short press, stop() could run before
            # the start thread has opened the stream, leaving it dangling.
            t = getattr(self, "_start_thread", None)
            if t is not None:
                t.join(timeout=3)
            audio = self.recorder.stop()
            if audio is None:
                return  # silence — do nothing, don't paste an empty string

            text = self.transcriber.transcribe(
                audio, hotwords=dictionary.hotwords(self.config["dictionary"])
            )
            if not text.strip():
                return

            text = cleanup.clean(text)
            text, corrections = dictionary.correct(text, self.config["dictionary"])
            if text:
                inject.inject(text, wait_released=self.hotkeys.all_released)
                stats.record(
                    words=len(text.split()),
                    chars=len(text),
                    speech_seconds=len(audio) / 16000,
                    corrections=len(corrections),
                )
                if self.config["keep_history"]:
                    history.append(text)
            if corrections:
                # Tell the user exactly what was substituted so bad matches
                # are caught immediately (full detail is also in log.txt).
                shown = "; ".join(f"{o} → {r}" for o, r in corrections[:4])
                if len(corrections) > 4:
                    shown += f" (+{len(corrections) - 4} more)"
                self.tray.notify.emit("Dictionary corrections", shown)
        except Exception:
            logging.exception("Transcription pipeline failed")
            self.tray.notify.emit("EchoScribe", "Transcription failed — check logs")
        finally:
            self.tray.set_state.emit("idle")
            self._busy.release()


def main():
    settings.setup_logging()
    logging.info("EchoScribe starting")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # closing dialogs must not exit the tray app

    ctrl = AppController(app)
    ctrl.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
