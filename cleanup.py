"""
cleanup.py — rule-based transcript post-processing.

Runs instantly in-process; no network, no external dependencies.

What it does:
  - strips pure filler words: um, uh, erm (word-boundary matched)
  - strips phrase fillers only when comma-delimited or at sentence start,
    so "I like it" and "what kind of sling" are left untouched
  - collapses double spaces
  - removes spaces before punctuation
  - capitalises the first word and the first word after . ! ?
"""

import re

# Pure fillers — safe to remove anywhere.
_PURE_FILLERS = re.compile(r"\b(?:um+|uh+|erm+)\b[,.]?\s*", re.IGNORECASE)

# Phrase fillers — only removed when set off by commas or at sentence start,
# which is how Whisper almost always transcribes genuine spoken fillers.
_PHRASE_FILLERS = re.compile(
    r"(?:,\s*(?:like|you know|sort of|kind of)\s*,)"
    r"|(?:(?<=[.!?]\s)(?:Like|You know|Sort of|Kind of),\s*)"
    r"|(?:^(?:Like|You know|Sort of|Kind of),\s*)",
    re.IGNORECASE,
)


def clean(text: str) -> str:
    if not text:
        return ""
    text = _PURE_FILLERS.sub("", text)
    text = _PHRASE_FILLERS.sub(lambda m: "," if m.group(0).startswith(",") else "", text)
    text = re.sub(r"\s{2,}", " ", text)           # double spaces
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)  # space before punctuation
    text = text.strip()

    # Capitalise after sentence-ending punctuation and at the very start.
    text = re.sub(r"([.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text
