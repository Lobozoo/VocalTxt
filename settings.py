r"""
settings.py — configuration load/save, app data paths, Windows startup registry entry.

Config lives at  %APPDATA%\VoiceFlow\config.json
Models cache at  %APPDATA%\VoiceFlow\models\
Log file at      %APPDATA%\VoiceFlow\log.txt
"""

import json
import logging
import os
import sys

APP_NAME = "VoiceFlow"

# ---------------------------------------------------------------- paths -----

def app_data_dir() -> str:
    r"""%APPDATA%\VoiceFlow (falls back to ~/.voiceflow on non-Windows dev boxes)."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.voiceflow")
    path = os.path.join(base, APP_NAME) if os.environ.get("APPDATA") else base
    os.makedirs(path, exist_ok=True)
    return path


def models_dir() -> str:
    path = os.path.join(app_data_dir(), "models")
    os.makedirs(path, exist_ok=True)
    return path


def config_path() -> str:
    return os.path.join(app_data_dir(), "config.json")


def log_path() -> str:
    return os.path.join(app_data_dir(), "log.txt")


# --------------------------------------------------------------- logging ----

def setup_logging():
    from logging.handlers import RotatingFileHandler
    # Rotate at 1 MB, keep 2 old files — the log can never grow unboundedly.
    handler = RotatingFileHandler(
        log_path(), maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(threadName)s] %(message)s"
    ))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # Also echo to console when running in dev mode (not frozen).
    if not getattr(sys, "frozen", False):
        root.addHandler(logging.StreamHandler())


# ---------------------------------------------------------------- config ----

DEFAULTS = {
    # Hotkey is stored as a list of normalised pynput key names. Modifier-only
    # combos are deliberate: held modifiers don't type anything into apps.
    "hotkey": ["ctrl", "alt", "shift"],
    # Tap this with a word selected in any app to add it to the dictionary.
    "add_word_hotkey": ["ctrl", "alt", "a"],
    # Custom vocabulary: biases Whisper (hotwords) and drives fuzzy
    # post-correction of near-miss transcriptions.
    "dictionary": [],
    "model_size": "base",              # tiny / base / small / medium
    "language": "en",                  # ISO code or null = auto-detect
    "start_with_windows": False,
    # OFF by default: when on, every dictation transcript is appended to
    # history.txt in plaintext. Deliberate opt-in — see history.py.
    "keep_history": False,
}


class Config:
    """Thin wrapper around config.json with defaults and atomic-ish save."""

    def __init__(self):
        self.data = dict(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(config_path(), "r", encoding="utf-8") as f:
                stored = json.load(f)
            # Merge so new keys added in future versions get defaults.
            self.data = {**DEFAULTS, **stored}
        except FileNotFoundError:
            self.save()
        except Exception:
            logging.exception("Failed to read config.json — using defaults")

    def save(self):
        try:
            tmp = config_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, config_path())
        except Exception:
            logging.exception("Failed to save config.json")

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value


# -------------------------------------------------- Windows startup entry ---

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_startup(enabled: bool):
    """Add/remove HKCU Run entry. HKCU does not require admin rights."""
    if sys.platform != "win32":
        return
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
        if enabled:
            if getattr(sys, "frozen", False):
                # Packaged EXE — point straight at it.
                cmd = f'"{sys.executable}"'
            else:
                # Dev mode — launch the script with pythonw (no console window).
                pyw = sys.executable.replace("python.exe", "pythonw.exe")
                script = os.path.abspath(sys.argv[0])
                cmd = f'"{pyw}" "{script}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception:
        logging.exception("Failed to update startup registry entry")
