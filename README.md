# VoiceFlow

**Local, offline voice-to-text dictation for Windows.**

Hold a hotkey, speak, release — the transcribed text is cleaned up and pasted
into whatever app has focus. Works in any app: email, Teams, Word, browser,
terminal. No cloud APIs, no account, no audio ever leaves your machine.

Built by **Gary van Niekerk**.

---

## How it works

- **Speech recognition** — OpenAI's Whisper model via
  [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2
  backend), running fully on your CPU. Auto-detects CUDA if available.
- **Voice activity detection** — Silero VAD (ONNX, torch-free), bundled as
  `silero_vad.onnx`. Only the parts of the recording where you actually spoke
  are sent to Whisper.
- **No cloud** — after the one-time model download everything runs offline.
- **No installer** — runs from a single `.exe` or directly from source.

---

## Quick start (running from source)

> No admin rights required.

**1. Install Python 3.11–3.13** from [python.org](https://python.org)
- Untick *Install for all users* (per-user install, no admin)
- Tick *Add python.exe to PATH*

**2. Clone or download this repo**, then in a terminal:

```
cd VoiceFlow
py -3.13 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

~500 MB download (torch-free — VAD runs on onnxruntime).

**3. Run:**

```
python main.py
```

First launch downloads the Whisper `base` model (~145 MB) to
`%APPDATA%\VoiceFlow\models` — one time only. On corporate machines with
TLS-inspection proxies (Zscaler etc.), this is handled automatically via
`truststore`.

**4. Dictate** — click into any app, hold **Ctrl+Alt+Shift**, speak, release.

---

## Building the EXE

With the venv activated:

```
build.bat
```

Output: `dist\VoiceFlow.exe` (~300–400 MB). The `--onefile` build unpacks to
`%TEMP%` on first launch (10–30 s) — subsequent launches are instant.

> **Corporate AV quarantining the EXE?** Skip packaging entirely — create a
> shortcut to `venv\Scripts\pythonw.exe main.py`. Functionally identical,
> nothing for AV to flag.

---

## Features

### Dictation hotkey (hold to talk)
Default: **Ctrl+Alt+Shift**. Hold it, speak, release. The on-screen pill
shows *Listening…* (red bars respond to your voice) then *Transcribing…*
(blue wave). Nothing pastes if you said nothing.

### Custom dictionary
Teach VoiceFlow site names and technical terms it would otherwise mangle:

1. Dictate something — notice a word is wrong
2. Correct it in your document
3. **Select** the corrected word
4. Tap the **add-word hotkey** (default: **Ctrl+Alt+A**)
5. A notification confirms it has been added

From the next dictation, VoiceFlow biases Whisper toward that spelling and
auto-corrects near-misses (e.g. *"crook hill t10"* → *"Crookhill T10"*).
Every substitution triggers a tray notification so you can verify it.
Manage the word list in Settings → Dictionary.

### Settings (click the tray icon)
| Setting | Description |
|---|---|
| Dictation hotkey | Hold-to-talk combo. Click the button and press your new combo — takes effect immediately. |
| Add-word hotkey | Tap with a word selected to add it to the dictionary. |
| Whisper model size | tiny / base / small / medium — larger = more accurate, slower. New sizes download on first use. |
| Dictionary | View, add, and remove custom words. |
| Start with Windows | Writes a per-user registry entry (no admin needed). |
| Keep dictation history | Off by default. When on, every transcript is appended to `history.txt` in plaintext. |

### Statistics (Settings → Statistics tab)
Tracks dictations, words, characters, time spent speaking, speaking pace,
dictionary corrections applied, and estimated typing time saved. Resets via
the *Reset all data* button (also clears log and history).

---

## Data & privacy

| File | Location | Contents |
|---|---|---|
| `config.json` | `%APPDATA%\VoiceFlow\` | Settings and dictionary |
| `stats.json` | `%APPDATA%\VoiceFlow\` | Usage statistics |
| `log.txt` | `%APPDATA%\VoiceFlow\` | Events, errors, correction audit (rotates at 1 MB) |
| `history.txt` | `%APPDATA%\VoiceFlow\` | Dictation transcripts — opt-in only |
| Whisper models | `%APPDATA%\VoiceFlow\models\` | Downloaded once from HuggingFace |

No telemetry. No network traffic after the initial model download.

---

## Diagnostics

If the hotkey stops responding, run:

```
python keytest.py
```

This prints every key event as pynput sees it. Press your hotkey combo and
confirm the combo-detected line appears. Useful for diagnosing conflicts with
corporate endpoint software.

Errors and events are logged to `%APPDATA%\VoiceFlow\log.txt` (accessible
via Settings → Statistics → *Open log*).

---

## File reference

| File | Purpose |
|---|---|
| `main.py` | Entry point, app controller, dictation pipeline |
| `hotkey.py` | Global hold-to-talk + tap hotkey listener (pynput) |
| `audio.py` | Mic capture at 16 kHz + Silero VAD speech gating |
| `transcribe.py` | faster-whisper wrapper, CUDA autodetect, model cache |
| `cleanup.py` | Rule-based filler word removal and capitalisation |
| `inject.py` | Clipboard paste injection with key-release wait |
| `dictionary.py` | Selection capture, hotwords biasing, fuzzy correction |
| `overlay.py` | On-screen pill indicator (Listening / Transcribing) |
| `tray.py` | PyQt6 system tray icon and Settings/Stats/Home window |
| `settings.py` | Config load/save, paths, logging, startup registry |
| `stats.py` | Usage statistics tracking |
| `history.py` | Optional dictation history with size cap |
| `silero_vad.onnx` | Bundled Silero VAD model (MIT licence, 2 MB) |
| `keytest.py` | Hotkey diagnostic tool |
| `build.bat` | PyInstaller build script → `dist\VoiceFlow.exe` |

---

## Requirements

- Windows 10/11 (64-bit)
- Python 3.11–3.13
- Microphone
- ~500 MB disk for dependencies + ~200 MB for the Whisper base model
