"""
history.py — optional dictation transcript history.

OFF by default (config "keep_history"): when enabled, every successful
dictation is appended to %APPDATA%\\EchoScribe\\history.txt with a timestamp.
Privacy note: this is a plaintext record of everything dictated — the
feature exists behind a deliberate opt-in for exactly that reason.

The file is size-capped: when it grows past ~512 KB the oldest half is
trimmed (at a line boundary), so it can never grow unboundedly.
"""

import datetime
import logging
import os
import threading

from settings import app_data_dir

_LOCK = threading.Lock()
MAX_BYTES = 512_000        # trim threshold
KEEP_BYTES = 256_000       # size kept after a trim (most recent entries)


def path() -> str:
    return os.path.join(app_data_dir(), "history.txt")


def append(text: str):
    """Append one dictation. Called from the pipeline thread."""
    if not text:
        return
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with _LOCK:
        try:
            with open(path(), "a", encoding="utf-8") as f:
                f.write(f"[{stamp}] {text}\n")
            _trim_if_needed()
        except Exception:
            logging.exception("Failed to write dictation history")


def _trim_if_needed():
    try:
        if os.path.getsize(path()) <= MAX_BYTES:
            return
        with open(path(), "rb") as f:
            data = f.read()
        tail = data[-KEEP_BYTES:]
        # Cut at the first newline so we don't keep half an entry.
        nl = tail.find(b"\n")
        if nl != -1:
            tail = tail[nl + 1:]
        tmp = path() + ".tmp"
        with open(tmp, "wb") as f:
            f.write(tail)
        os.replace(tmp, path())
        logging.info("Dictation history trimmed to most recent entries")
    except Exception:
        logging.exception("History trim failed")
