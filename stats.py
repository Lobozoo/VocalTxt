"""
stats.py — lightweight usage statistics.

Stored in %APPDATA%\\VoiceFlow\\stats.json, separate from config so settings
stay clean. Thread-safe (record() is called from the pipeline worker thread,
summary() from the GUI thread). Keeps all-time totals plus a rolling 30 days
of per-day counts.
"""

import datetime
import json
import logging
import os
import threading

from settings import app_data_dir

_LOCK = threading.Lock()

_DEFAULT = {
    "since": None,            # ISO date of first recorded dictation
    "dictations": 0,
    "words": 0,
    "chars": 0,
    "speech_seconds": 0.0,    # total time actually spent speaking
    "corrections": 0,         # dictionary substitutions applied
    "days": {},               # "YYYY-MM-DD" -> {"dictations": n, "words": n}
}


def _path() -> str:
    return os.path.join(app_data_dir(), "stats.json")


def _load() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            return {**_DEFAULT, **json.load(f)}
    except Exception:
        return dict(_DEFAULT)


def record(words: int, chars: int, speech_seconds: float, corrections: int):
    """Called once per successful dictation, from the pipeline thread."""
    with _LOCK:
        d = _load()
        today = datetime.date.today().isoformat()
        if not d["since"]:
            d["since"] = today
        d["dictations"] += 1
        d["words"] += words
        d["chars"] += chars
        d["speech_seconds"] += speech_seconds
        d["corrections"] += corrections
        day = d["days"].setdefault(today, {"dictations": 0, "words": 0})
        day["dictations"] += 1
        day["words"] += words
        # Rolling window: keep the most recent 30 day-entries only.
        if len(d["days"]) > 30:
            for k in sorted(d["days"])[:-30]:
                d["days"].pop(k, None)
        try:
            tmp = _path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2)
            os.replace(tmp, _path())
        except Exception:
            logging.exception("Failed to save stats")


def summary() -> dict:
    """Everything the statistics tab needs, pre-computed."""
    with _LOCK:
        d = _load()
    today = d["days"].get(
        datetime.date.today().isoformat(), {"dictations": 0, "words": 0}
    )
    minutes = d["speech_seconds"] / 60
    words = d["words"]
    # "Time saved" heuristic: the same words typed at 40 wpm vs time spoken.
    typing_min = words / 40 if words else 0.0
    saved_min = max(0.0, typing_min - minutes)
    return {
        "since": d["since"],
        "dictations": d["dictations"],
        "words": words,
        "chars": d["chars"],
        "speech_minutes": minutes,
        "avg_words": words / d["dictations"] if d["dictations"] else 0.0,
        "speaking_wpm": words / minutes if minutes > 0.05 else 0.0,
        "corrections": d["corrections"],
        "saved_minutes": saved_min,
        "today_dictations": today["dictations"],
        "today_words": today["words"],
    }
