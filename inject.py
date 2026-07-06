"""
inject.py — put text into whatever app currently has focus.

Strategy: clipboard + simulated Ctrl+V. Typing character-by-character with
pynput is unreliable for unicode and painfully slow for long dictations;
paste is instant and works in virtually every Windows app.

The user's original clipboard content is restored ~500 ms after the paste so
dictating doesn't clobber whatever they had copied.
"""

import logging
import threading
import time

import pyperclip
from pynput.keyboard import Controller, Key

_kb = Controller()


def inject(text: str, wait_released=None):
    """wait_released: optional callable returning True once the user's hotkey
    keys are all physically up. Pasting while modifiers are still held turns
    our Ctrl+V into Ctrl+Alt+V (or worse) in the target app, so we wait."""
    if not text:
        return

    if wait_released is not None:
        deadline = time.time() + 2.0
        while time.time() < deadline and not wait_released():
            time.sleep(0.02)

    # Save whatever is on the clipboard now (text only — pyperclip can't
    # round-trip images/files, so those are unavoidably lost).
    try:
        original = pyperclip.paste()
    except Exception:
        original = None

    try:
        pyperclip.copy(text)
        time.sleep(0.05)          # give the clipboard a beat to settle

        with _kb.pressed(Key.ctrl):
            _kb.press("v")
            _kb.release("v")

        logging.info("Injected %d chars", len(text))
    except Exception:
        logging.exception("Text injection failed")

    # Restore the original clipboard after the paste has definitely landed.
    if original is not None:
        def _restore():
            time.sleep(0.5)
            try:
                pyperclip.copy(original)
            except Exception:
                pass

        threading.Thread(target=_restore, name="clip-restore", daemon=True).start()
