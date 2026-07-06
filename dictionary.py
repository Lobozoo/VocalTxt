"""
dictionary.py — custom vocabulary support.

Three jobs:
  * capture_selection(): grab whatever text the user has selected in any app
    (simulated Ctrl+C, read clipboard, restore original clipboard).
  * hotwords(): build the biasing string fed to faster-whisper so the decoder
    is nudged toward dictionary words during transcription.
  * correct(): conservative fuzzy post-correction — repairs near-miss
    transcriptions ("kill gala lock" -> "Kilgallioch") using difflib.
    Every correction is returned to the caller for logging, so bad
    corrections are visible and the threshold can be tuned.
"""

import difflib
import logging
import time

import pyperclip
from pynput.keyboard import Controller, Key

_kb = Controller()

# Similarity threshold for a correction. Deliberately conservative: too low
# and legitimate words get "corrected" into jargon; too high and variants
# slip through. Tune with the log lines if needed.
MIN_RATIO = 0.78
_PUNCT = ".,!?;:"


# ----------------------------------------------------------- capture --------

def capture_selection(wait_released=None) -> str:
    """Copy the current selection in the focused app and return it.
    Waits for the user's hotkey fingers to lift first (same reason as
    inject.py: Ctrl+C sent while extra modifiers are held misfires)."""
    if wait_released is not None:
        deadline = time.time() + 2.0
        while time.time() < deadline and not wait_released():
            time.sleep(0.02)

    try:
        original = pyperclip.paste()
    except Exception:
        original = None

    try:
        pyperclip.copy("")           # clear, so an app that ignores Ctrl+C
    except Exception:                # yields "" rather than stale clipboard
        pass

    with _kb.pressed(Key.ctrl):
        _kb.press("c")
        _kb.release("c")
    time.sleep(0.15)                 # give the app a beat to service the copy

    try:
        text = pyperclip.paste() or ""
    except Exception:
        text = ""

    if original is not None:
        try:
            pyperclip.copy(original)
        except Exception:
            pass
    return text.strip()


def valid_entry(text: str) -> bool:
    """A dictionary entry is a word or short phrase, not a paragraph."""
    return bool(text) and len(text) <= 60 and "\n" not in text \
        and len(text.split()) <= 4


# ---------------------------------------------------------- hotwords --------

def hotwords(words: list[str]) -> str | None:
    """faster-whisper accepts a free-text hotwords string that biases the
    decoder. Effectiveness fades as the list grows very large."""
    return ", ".join(words) if words else None


# --------------------------------------------------------- correction -------

def _strip(tok: str) -> str:
    return tok.lower().strip(_PUNCT)


def _trailing_punct(tok: str) -> str:
    i = len(tok)
    while i > 0 and tok[i - 1] in _PUNCT:
        i -= 1
    return tok[i:]


def correct(text: str, words: list[str]) -> tuple[str, list[tuple[str, str]]]:
    """Repair near-miss transcriptions of dictionary words.
    Returns (corrected_text, [(original, replacement), ...])."""
    if not text or not words:
        return text, []

    tokens = text.split()
    corrections: list[tuple[str, str]] = []

    for word in words:
        target = word.lower()
        n = len(target.split())

        if n == 1:
            for i, tok in enumerate(tokens):
                bare = _strip(tok)
                if bare == target:
                    # Exact word, wrong casing — restore canonical casing.
                    if tok.rstrip(_PUNCT) != word:
                        corrections.append((tok, word))
                        tokens[i] = word + _trailing_punct(tok)
                    continue
                # Short tokens are too easy to false-match ("kilo" vs "kil…").
                if len(bare) < 4:
                    continue
                if difflib.SequenceMatcher(None, bare, target).ratio() >= MIN_RATIO:
                    corrections.append((tok, word))
                    tokens[i] = word + _trailing_punct(tok)
        else:
            # Multi-word entries. Three cases, checked in order:
            #   1. exact match (fix casing only),
            #   2. split words — "Crookhill T10" heard as "crook hill t10":
            #      an (n+1)-token window whose DE-SPACED text has the same
            #      length as the de-spaced target. The length rule is what
            #      stops a window that merely contains the target plus a
            #      stray word ("at crookhill t10") from matching.
            #   3. same-length fuzzy window (misspellings).
            tgt_nospace = target.replace(" ", "")
            i = 0
            while i <= len(tokens) - n:
                gram_toks = tokens[i:i + n]
                gram = " ".join(_strip(t) for t in gram_toks)

                if gram == target:                              # case 1
                    original = " ".join(gram_toks)
                    if " ".join(t.rstrip(_PUNCT) for t in gram_toks) != word:
                        corrections.append((original, word))
                        tokens[i:i + n] = [word + _trailing_punct(gram_toks[-1])]
                    i += 1
                    continue

                if i + n + 1 <= len(tokens):                    # case 2
                    g2_toks = tokens[i:i + n + 1]
                    g2 = "".join(_strip(t) for t in g2_toks)
                    # A genuine split starts with a fragment of the target's
                    # first word — this stops a stray leading word ("at")
                    # that merely length-balances the window from matching.
                    if tgt_nospace.startswith(_strip(g2_toks[0])) and \
                            abs(len(g2) - len(tgt_nospace)) <= 1 and \
                            difflib.SequenceMatcher(None, g2, tgt_nospace).ratio() >= MIN_RATIO:
                        corrections.append((" ".join(g2_toks), word))
                        tokens[i:i + n + 1] = [word + _trailing_punct(g2_toks[-1])]
                        i += 1
                        continue

                if difflib.SequenceMatcher(None, gram, target).ratio() >= MIN_RATIO:
                    corrections.append((" ".join(gram_toks), word))   # case 3
                    tokens[i:i + n] = [word + _trailing_punct(gram_toks[-1])]
                i += 1

    for orig, repl in corrections:
        logging.info("Dictionary correction: %r -> %r", orig, repl)
    return " ".join(tokens), corrections
