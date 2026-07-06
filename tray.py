"""
tray.py — PySide6 system tray icon (idle / recording / processing) + Settings dialog.

Icons are drawn programmatically with QPainter so no image assets need to be
bundled into the EXE. The processing state cycles through rotated arc frames
on a QTimer for a "spinner" effect.

The hotkey capture button grabs the keyboard while "listening" and translates
the Qt key event into the same normalised names hotkey.py uses, so the two
sides always agree on what e.g. "Ctrl+Alt+Space" means.
"""

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMessageBox, QPushButton, QTabWidget,
    QTextBrowser, QWidget,
    QSystemTrayIcon, QVBoxLayout,
)

import settings as cfg_mod

# ------------------------------------------------------------ icon drawing --

def _mic_icon(color: QColor) -> QIcon:
    """Draw a simple microphone glyph."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, 5)
    p.setPen(pen)
    p.setBrush(color)
    p.drawRoundedRect(24, 8, 16, 28, 8, 8)          # capsule body
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(16, 18, 32, 30, 180 * 16, 180 * 16)   # cradle arc
    p.drawLine(32, 48, 32, 56)                       # stem
    p.drawLine(22, 56, 42, 56)                       # base
    p.end()
    return QIcon(pm)


def _spinner_frame(angle: int) -> QIcon:
    """One frame of the processing spinner (rotating arc)."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor("#4a90d9"), 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawArc(12, 12, 40, 40, angle * 16, 120 * 16)
    p.end()
    return QIcon(pm)


ICON_IDLE = None       # built lazily — QPixmap needs a QApplication first
ICON_RECORDING = None
SPINNER_FRAMES: list[QIcon] = []


def _find_ico() -> str | None:
    """Locate echoscribe.ico: next to the script, or inside PyInstaller bundle."""
    import sys, os
    for base in (getattr(sys, "_MEIPASS", ""), os.path.dirname(os.path.abspath(__file__))):
        p = os.path.join(base, "echoscribe.ico")
        if os.path.exists(p):
            return p
    return None


def _build_icons():
    global ICON_IDLE, ICON_RECORDING, SPINNER_FRAMES
    if ICON_IDLE is None:
        ico_path = _find_ico()
        if ico_path:
            ICON_IDLE = QIcon(ico_path)
        else:
            ICON_IDLE = _mic_icon(QColor("#9a9a9a"))
        ICON_RECORDING = _mic_icon(QColor("#e03c3c"))
        SPINNER_FRAMES = [_spinner_frame(a) for a in range(0, 360, 30)]


# ------------------------------------------------------------- Qt ↔ pynput --

_QT_KEY_NAMES = {
    Qt.Key.Key_Space: "space", Qt.Key.Key_Tab: "tab", Qt.Key.Key_Return: "enter",
    Qt.Key.Key_Enter: "enter", Qt.Key.Key_Escape: "esc", Qt.Key.Key_Backspace: "backspace",
    Qt.Key.Key_Delete: "delete", Qt.Key.Key_Insert: "insert", Qt.Key.Key_Home: "home",
    Qt.Key.Key_End: "end", Qt.Key.Key_PageUp: "page_up", Qt.Key.Key_PageDown: "page_down",
    Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down", Qt.Key.Key_Left: "left",
    Qt.Key.Key_Right: "right", Qt.Key.Key_CapsLock: "caps_lock",
    Qt.Key.Key_Pause: "pause", Qt.Key.Key_Print: "print_screen",
}
for _i in range(1, 25):  # F1..F24
    _QT_KEY_NAMES[Qt.Key(Qt.Key.Key_F1.value + _i - 1)] = f"f{_i}"

_MODIFIER_QT_KEYS = {
    Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift,
    Qt.Key.Key_Meta, Qt.Key.Key_AltGr,
}


def _combo_from_qt(event) -> list[str] | None:
    """Translate a QKeyEvent into a pynput-name combo, or None if it's only
    modifiers so far (capture continues until a non-modifier key arrives)."""
    key = Qt.Key(event.key())
    if key in _MODIFIER_QT_KEYS:
        return None

    combo: list[str] = []
    mods = event.modifiers()
    if mods & Qt.KeyboardModifier.ControlModifier:
        combo.append("ctrl")
    if mods & Qt.KeyboardModifier.AltModifier:
        combo.append("alt")
    if mods & Qt.KeyboardModifier.ShiftModifier:
        combo.append("shift")
    if mods & Qt.KeyboardModifier.MetaModifier:
        combo.append("win")

    if key in _QT_KEY_NAMES:
        combo.append(_QT_KEY_NAMES[key])
    else:
        text = event.text()
        if text and text.isprintable() and text.strip():
            combo.append(text.lower())
        elif Qt.Key.Key_A.value <= key.value <= Qt.Key.Key_Z.value:
            combo.append(chr(key.value).lower())
        elif Qt.Key.Key_0.value <= key.value <= Qt.Key.Key_9.value:
            combo.append(chr(key.value))
        else:
            return None  # unmappable — keep listening
    return combo


def combo_label(combo: list[str]) -> str:
    order = {"ctrl": 0, "alt": 1, "shift": 2, "win": 3}
    parts = sorted(combo, key=lambda k: order.get(k, 9))
    return "+".join(p.capitalize() if len(p) > 1 else p.upper() for p in parts)


# ------------------------------------------------------ hotkey capture UI ---

_MOD_FROM_QTKEY = {
    Qt.Key.Key_Control: "ctrl", Qt.Key.Key_Alt: "alt", Qt.Key.Key_AltGr: "alt",
    Qt.Key.Key_Shift: "shift", Qt.Key.Key_Meta: "win",
}


def _mods_from_event(event, pressed_key=None) -> set[str]:
    """Modifiers currently down, including the modifier key being pressed
    right now (Qt doesn't always fold a modifier into its own event's
    modifiers() at press time)."""
    mods: set[str] = set()
    m = event.modifiers()
    if m & Qt.KeyboardModifier.ControlModifier:
        mods.add("ctrl")
    if m & Qt.KeyboardModifier.AltModifier:
        mods.add("alt")
    if m & Qt.KeyboardModifier.ShiftModifier:
        mods.add("shift")
    if m & Qt.KeyboardModifier.MetaModifier:
        mods.add("win")
    if pressed_key in _MOD_FROM_QTKEY:
        mods.add(_MOD_FROM_QTKEY[pressed_key])
    return mods


class HotkeyButton(QPushButton):
    """Shows the current binding; click → 'listening' mode.

    Two capture paths:
      * modifiers + a normal key  → finalised the moment the normal key
        is pressed (e.g. Ctrl+Alt+D);
      * modifiers only            → finalised when the user RELEASES a key
        while ≥2 modifiers are held (e.g. Ctrl+Alt+Shift). Requiring two
        prevents accidentally binding plain Shift.
    """

    combo_captured = Signal(list)

    def __init__(self, combo: list[str], parent=None):
        super().__init__(combo_label(combo), parent)
        self.combo = list(combo)
        self._listening = False
        self._pending_mods: set[str] = set()
        self.clicked.connect(self._begin_capture)

    def _begin_capture(self):
        self._listening = True
        self._pending_mods = set()
        self.setText("Press new hotkey…")
        self.grabKeyboard()

    def _finalise(self, combo: list[str]):
        self.combo = combo
        self._listening = False
        self._pending_mods = set()
        self.releaseKeyboard()
        self.setText(combo_label(combo))
        self.combo_captured.emit(combo)

    def keyPressEvent(self, event):
        if not self._listening:
            return super().keyPressEvent(event)
        key = Qt.Key(event.key())
        if key in _MODIFIER_QT_KEYS:
            # Accumulate held modifiers and show a live preview.
            self._pending_mods = _mods_from_event(event, key)
            self.setText(combo_label(sorted(self._pending_mods)) + "…")
            return
        combo = _combo_from_qt(event)
        if combo is not None:
            self._finalise(combo)

    def keyReleaseEvent(self, event):
        if not self._listening:
            return super().keyReleaseEvent(event)
        # Releasing while only modifiers were held → modifier-only binding.
        if len(self._pending_mods) >= 2:
            self._finalise(sorted(self._pending_mods,
                                  key=lambda k: {"ctrl": 0, "alt": 1,
                                                 "shift": 2, "win": 3}.get(k, 9)))
        elif self._pending_mods:
            # A single modifier isn't a valid binding — reset and keep listening.
            self._pending_mods = set()
            self.setText("Press new hotkey…")

    def focusOutEvent(self, event):
        if self._listening:  # cancelled by clicking elsewhere
            self._listening = False
            self._pending_mods = set()
            self.releaseKeyboard()
            self.setText(combo_label(self.combo))
        super().focusOutEvent(event)


# ---------------------------------------------------------- settings dialog -

MODEL_SIZES = ["tiny", "base", "small", "medium"]


class SettingsDialog(QDialog):
    """Edits are applied live via the `app` controller on Save."""

    def __init__(self, app_ctrl, parent=None):
        super().__init__(parent)
        self.app_ctrl = app_ctrl
        config = app_ctrl.config
        self.setWindowTitle("EchoScribe Settings")
        self.setMinimumWidth(380)

        # Match the on-screen overlay pill: same dark navy, light text, and
        # the pill's red (recording) + blue (processing) as accents.
        self.setStyleSheet("""
            QDialog { background-color: #181820; }
            QLabel { color: #dcdce4; }
            QCheckBox { color: #dcdce4; spacing: 8px; }
            QCheckBox::indicator {
                width: 16px; height: 16px;
                border: 1px solid #5a5a66; border-radius: 4px;
                background: #202028;
            }
            QCheckBox::indicator:hover { border-color: #8a8a96; }
            QCheckBox::indicator:checked {
                background-color: #569cff; border-color: #569cff;
            }
            QPushButton {
                background-color: #26262e; color: #f0f0f8;
                border: 1px solid #3a3a44; border-radius: 8px;
                padding: 6px 14px;
            }
            QPushButton:hover { background-color: #32323c; }
            QPushButton#saveBtn {
                background-color: #569cff; color: #10101a;
                font-weight: 600; border: none;
            }
            QPushButton#saveBtn:hover { background-color: #6faaff; }
            QPushButton#quitBtn { color: #ff8c8c; border-color: #5a3a3e; }
            QPushButton#quitBtn:hover { background-color: #3a2a2e; }
            QComboBox, QLineEdit, QListWidget {
                background-color: #202028; color: #f0f0f8;
                border: 1px solid #3a3a44; border-radius: 6px; padding: 4px;
            }
            QComboBox QAbstractItemView {
                background-color: #202028; color: #f0f0f8;
                selection-background-color: #569cff;
            }
            QListWidget::item:selected { background-color: #569cff; color: #10101a; }
            QTabWidget::pane { border: 1px solid #3a3a44; border-radius: 6px; }
            QTabBar::tab {
                background: #202028; color: #dcdce4; padding: 6px 16px;
                border-top-left-radius: 6px; border-top-right-radius: 6px;
            }
            QTabBar::tab:selected { background: #569cff; color: #10101a; }
            QScrollBar:vertical {
                background: #181820; width: 10px; margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #3a3a44; border-radius: 5px; min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: #4a4a56; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)

        form = QFormLayout()

        self.hotkey_btn = HotkeyButton(config["hotkey"])
        form.addRow("Hotkey (hold to talk):", self.hotkey_btn)

        self.addword_btn = HotkeyButton(config["add_word_hotkey"])
        form.addRow("Add-word hotkey (tap):", self.addword_btn)

        self.model_box = QComboBox()
        self.model_box.addItems(MODEL_SIZES)
        self.model_box.setCurrentText(config["model_size"])
        form.addRow("Whisper model size:", self.model_box)

        # ---- custom dictionary -------------------------------------------
        self.dict_list = QListWidget()
        self.dict_list.addItems(config["dictionary"])
        self.dict_list.setMaximumHeight(120)
        form.addRow("Dictionary:", self.dict_list)

        dict_row = QHBoxLayout()
        self.dict_input = QLineEdit()
        self.dict_input.setPlaceholderText("Add word or phrase…")
        add_btn = QPushButton("Add")
        rem_btn = QPushButton("Remove")
        add_btn.clicked.connect(self._dict_add)
        self.dict_input.returnPressed.connect(self._dict_add)
        rem_btn.clicked.connect(self._dict_remove)
        dict_row.addWidget(self.dict_input)
        dict_row.addWidget(add_btn)
        dict_row.addWidget(rem_btn)
        form.addRow("", dict_row)

        self.startup_chk = QCheckBox("Start EchoScribe with Windows")
        self.startup_chk.setChecked(config["start_with_windows"])
        form.addRow("", self.startup_chk)

        self.history_chk = QCheckBox("Keep dictation history (plaintext on this PC)")
        self.history_chk.setChecked(config["keep_history"])
        form.addRow("", self.history_chk)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("saveBtn")
        save_btn.clicked.connect(self._save)
        quit_btn = QPushButton("Quit EchoScribe")
        quit_btn.setObjectName("quitBtn")
        quit_btn.clicked.connect(self._quit_app)
        btn_row = QHBoxLayout()
        btn_row.addWidget(quit_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(save_btn)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        # ---- tab 1: home / info --------------------------------------------
        tabs.addTab(self._build_info_page(), "Home")

        # ---- tab 2: settings ----------------------------------------------
        settings_page = QWidget()
        page_layout = QVBoxLayout(settings_page)
        page_layout.addLayout(form)
        hint = QLabel("Changing model size downloads the new model on first use.")
        hint.setStyleSheet("color: #8a8a96; font-size: 11px;")
        page_layout.addWidget(hint)
        about = QLabel(
            "EchoScribe — local, offline dictation. Hold the hotkey, speak, "
            "release. Powered by faster-whisper + Silero VAD."
        )
        about.setWordWrap(True)
        about.setStyleSheet("color: #8a8a96; font-size: 11px;")
        page_layout.addWidget(about)
        tabs.addTab(settings_page, "Settings")

        # ---- tab 3: statistics --------------------------------------------
        tabs.addTab(self._build_stats_page(), "Statistics")

        layout.addWidget(tabs)
        layout.addLayout(btn_row)

        # While the dialog is open, pause the global listener so pressing the
        # capture combo doesn't also start a recording.
        app_ctrl.hotkeys.paused = True

    def _build_stats_page(self) -> QWidget:
        import stats as stats_mod
        s = stats_mod.summary()

        page = QWidget()
        form = QFormLayout(page)

        def row(label, value):
            v = QLabel(value)
            v.setStyleSheet("font-weight: 600;")
            form.addRow(label, v)

        if s["dictations"] == 0:
            form.addRow(QLabel("No dictations recorded yet — statistics "
                               "start counting from your next one."))
            self._add_file_buttons(form)
            return page

        hrs, mins = divmod(int(s["speech_minutes"]), 60)
        saved_h, saved_m = divmod(int(s["saved_minutes"]), 60)

        row("Today:", f'{s["today_dictations"]} dictations · {s["today_words"]:,} words')
        row("Dictations (all time):", f'{s["dictations"]:,}')
        row("Words dictated:", f'{s["words"]:,}')
        row("Characters typed for you:", f'{s["chars"]:,}')
        row("Time spent speaking:", f"{hrs} h {mins} min" if hrs else f"{mins} min")
        row("Average per dictation:", f'{s["avg_words"]:.0f} words')
        if s["speaking_wpm"]:
            row("Your speaking pace:", f'{s["speaking_wpm"]:.0f} words/min')
        row("Dictionary corrections:", f'{s["corrections"]:,}')
        row("Est. typing time saved:",
            f"{saved_h} h {saved_m} min" if saved_h else f"{saved_m} min")
        if s["since"]:
            since = QLabel(f'Counting since {s["since"]}')
            since.setStyleSheet("color: #8a8a96; font-size: 11px;")
            form.addRow(since)
        self._add_file_buttons(form)
        return page

    def _info_html(self) -> str:
        """Build the Home tab HTML with the CURRENT hotkey bindings."""
        config = self.app_ctrl.config
        talk = combo_label(config["hotkey"])
        addw = combo_label(config["add_word_hotkey"])

        H = 'style="color:#f0f0f8; font-size:14px; font-weight:600;"'
        P = 'style="color:#c8c8d2; font-size:12px;"'
        KEY = 'style="color:#569cff; font-weight:600;"'
        HL = 'style="color:#f0f0f8; font-weight:600;"'
        RED = 'style="color:#ff5c5c; font-weight:600;"'
        BLUE = 'style="color:#569cff; font-weight:600;"'
        MUTED = 'style="color:#8a8a96; font-size:11px;"'
        RULE = ('<div style="margin-top:14px;"></div>'
                '<hr style="background-color:#3a3a44;">')

        html = f"""
        <p {H}>&#127908;&nbsp; EchoScribe</p>
        <p {P}><span {HL}>EchoScribe is a voice-to-text dictation tool that
        works in any app.</span> Hold a hotkey, speak, release — your words
        are transcribed, cleaned up, and typed into whatever has focus:
        emails, chats, documents, forms.</p>
        <p {P}>Everything runs <span {HL}>locally on this PC</span>. Speech
        recognition uses OpenAI's Whisper model (via faster-whisper) with
        Silero voice-activity detection — no cloud services, no account, no
        audio or text ever leaves the machine. After the one-time model
        download it works fully offline. Settings, statistics, and the
        optional dictation history are stored in
        <span {HL}>%APPDATA%\\EchoScribe</span>.</p>
        {RULE}

        <p {H}>&#127911;&nbsp; How to dictate</p>
        <p {P}>Hold <span {KEY}>{talk}</span>, speak, release. The pill at
        the bottom of the screen shows <span {RED}>Listening&#8230;</span>
        (red bars move with your voice) then
        <span {BLUE}>Transcribing&#8230;</span> (blue wave). If nothing was
        said, nothing is pasted.</p>
        {RULE}

        <p {H}>&#128218;&nbsp; Teaching it words</p>
        <p {P}>When a site name or technical term comes out wrong: correct it
        in your document, <span {HL}>select the corrected word</span>, and tap
        <span {KEY}>{addw}</span>. EchoScribe then steers transcription toward
        that spelling and auto-repairs near-misses — every substitution is
        shown in a notification so you can check it.</p>
        {RULE}

        <p {H}>&#9881;&nbsp; Settings</p>
        <p {P}>Everything is adjustable in the <span {HL}>Settings</span>
        tab:</p>
        <p {P}>&#8226;&nbsp; <span {HL}>Hotkeys</span> — click a hotkey
        button, then press modifiers + a key (e.g.
        <span {KEY}>Ctrl+Alt+D</span>) or hold two or more modifiers and
        release (e.g. <span {KEY}>Ctrl+Alt+Shift</span>). Applies instantly.
        <br>&#8226;&nbsp; <span {HL}>Model size</span> — larger models are
        more accurate with names and accents, at the cost of a bigger
        one-time download and slightly slower transcription.
        <br>&#8226;&nbsp; <span {HL}>Dictionary</span> — view, add, or remove
        your custom words.
        <br>&#8226;&nbsp; <span {HL}>Start with Windows</span> and the
        optional <span {HL}>dictation history</span> (a plaintext record of
        what you dictate, off by default).</p>
        <p {P}>The <span {HL}>Statistics</span> tab tracks your usage and
        links to the log and history files.</p>
        {RULE}

        <p {H}>&#128100;&nbsp; Creator</p>
        <p {P}>Created by <span {HL}>Gary van Niekerk</span>.</p>
        <p {MUTED}>Built with Python — faster-whisper, Silero VAD, and
        PySide6.</p>
        """

        return html


    def _build_info_page(self) -> QWidget:
        """Home tab."""

        html = self._info_html()
        # QTextBrowser instead of QLabel-in-QScrollArea: a rich-text QLabel
        # re-measures its height as the scrollbar toggles, causing a reflow
        # feedback loop (the "jumpy" scrolling). QTextBrowser owns its
        # scrolling, scrolls per-pixel, and never thrashes.
        self._info_viewer = QTextBrowser()
        viewer = self._info_viewer
        viewer.setHtml(html)
        viewer.setOpenExternalLinks(False)
        viewer.setFrameShape(QTextBrowser.Shape.NoFrame)
        viewer.setStyleSheet(
            "QTextBrowser { background: transparent; border: none; padding: 4px; }"
        )
        viewer.verticalScrollBar().setSingleStep(12)   # gentle wheel steps

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.addWidget(viewer)
        return page

    def _add_file_buttons(self, form):
        """Log/history viewers and reset belong with the stats."""
        row = QHBoxLayout()
        log_btn = QPushButton("Open log")
        log_btn.clicked.connect(self._open_log)
        hist_btn = QPushButton("Open dictation history")
        hist_btn.clicked.connect(self._open_history)
        reset_btn = QPushButton("Reset all data")
        reset_btn.setStyleSheet(
            "color: #ff8c8c; border-color: #5a3a3e;"
        )
        reset_btn.clicked.connect(self._reset_all_data)
        row.addWidget(log_btn)
        row.addWidget(hist_btn)
        row.addStretch(1)
        row.addWidget(reset_btn)
        form.addRow("", row)

    def _reset_all_data(self):
        """Wipe stats, history, log, and dictionary — back to a clean slate
        for sharing or a fresh start. Config structure and hotkeys are kept."""
        reply = QMessageBox.question(
            self, "Reset all data",
            "This will clear your statistics, dictation history, log file, "
            "and dictionary.\n\nHotkeys and model choice are kept.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        import os
        data_dir = cfg_mod.app_data_dir()
        for name in ("stats.json", "history.txt", "log.txt", "log.txt.1", "log.txt.2"):
            try:
                os.remove(os.path.join(data_dir, name))
            except FileNotFoundError:
                pass
            except Exception:
                logging.exception("Failed to remove %s", name)
        # Clear dictionary from live config and disk
        self.app_ctrl.config["dictionary"] = []
        self.app_ctrl.config.save()
        self.dict_list.clear()
        QMessageBox.information(self, "EchoScribe", "All data has been reset.")

    def _quit_app(self):
        self.reject()
        self.app_ctrl.quit()

    def _open_log(self):
        """Open log.txt in the default text editor (Windows: Notepad)."""
        import os
        try:
            os.startfile(cfg_mod.log_path())      # Windows-native open
        except AttributeError:                    # non-Windows dev box
            import subprocess
            subprocess.Popen(["xdg-open", cfg_mod.log_path()])
        except Exception:
            logging.exception("Could not open log file")

    def _open_history(self):
        import history as history_mod
        import os
        if not os.path.exists(history_mod.path()):
            QMessageBox.information(
                self, "EchoScribe",
                "No history yet. Enable 'Keep dictation history' and dictate "
                "something first."
            )
            return
        try:
            os.startfile(history_mod.path())
        except AttributeError:
            import subprocess
            subprocess.Popen(["xdg-open", history_mod.path()])
        except Exception:
            logging.exception("Could not open history file")

    def _dict_add(self):
        text = self.dict_input.text().strip()
        if text and not self.dict_list.findItems(text, Qt.MatchFlag.MatchExactly):
            self.dict_list.addItem(text)
        self.dict_input.clear()

    def _dict_remove(self):
        for item in self.dict_list.selectedItems():
            self.dict_list.takeItem(self.dict_list.row(item))

    def _save(self):
        config = self.app_ctrl.config

        # The two hotkeys must differ, or every word-add would also start
        # a recording.
        if set(self.hotkey_btn.combo) == set(self.addword_btn.combo):
            QMessageBox.warning(
                self, "EchoScribe",
                "The dictation and add-word hotkeys must be different."
            )
            return

        config["hotkey"] = self.hotkey_btn.combo
        config["add_word_hotkey"] = self.addword_btn.combo
        config["model_size"] = self.model_box.currentText()
        config["start_with_windows"] = self.startup_chk.isChecked()
        config["keep_history"] = self.history_chk.isChecked()
        config["dictionary"] = [
            self.dict_list.item(i).text() for i in range(self.dict_list.count())
        ]
        config.save()

        # Apply live — no restart needed.
        # Refresh the Home tab so it shows the new hotkey labels immediately.
        if hasattr(self, "_info_viewer"):
            self._info_viewer.setHtml(self._info_html())
        self.app_ctrl.hotkeys.set_combo(config["hotkey"])
        self.app_ctrl.hotkeys.set_secondary(
            config["add_word_hotkey"], self.app_ctrl._on_add_word_hotkey
        )
        self.app_ctrl.transcriber.set_model_size(config["model_size"])
        cfg_mod.set_startup(config["start_with_windows"])
        self.accept()

    def closeEvent(self, event):
        self.app_ctrl.hotkeys.paused = False
        super().closeEvent(event)

    def done(self, r):
        self.app_ctrl.hotkeys.paused = False
        super().done(r)


# ----------------------------------------------------------------- tray -----

class Tray(QSystemTrayIcon):
    # Signals let worker threads change tray state safely (queued to GUI thread).
    set_state = Signal(str)          # "idle" | "recording" | "processing"
    notify = Signal(str, str)        # title, message

    def __init__(self, app_ctrl, parent=None):
        _build_icons()
        super().__init__(ICON_IDLE, parent)
        self.app_ctrl = app_ctrl
        self.setToolTip("EchoScribe — hold hotkey to dictate, click for settings")
        # Set app-wide window icon for dialogs, Alt-Tab, and taskbar.
        from PySide6.QtWidgets import QApplication
        ico_path = _find_ico()
        if ico_path:
            QApplication.instance().setWindowIcon(QIcon(ico_path))
        self._settings_dlg = None   # open-dialog tracker (singleton guard)

        # No context menu: the Settings window is the app's single front door
        # (it carries the app info and the Quit button). Any click opens it.
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(90)
        self._spin_timer.timeout.connect(self._spin_tick)
        self._spin_idx = 0

        # Left-click on the tray icon opens Settings directly (the right-click
        # menu stays for About/Quit). Trigger = plain left click.
        self.activated.connect(self._on_activated)

        self.set_state.connect(self._apply_state)
        self.notify.connect(self._show_notification)

    def _on_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.Context,
        ):
            self.open_settings()

    # -- state ------------------------------------------------------------

    def _apply_state(self, state: str):
        self._spin_timer.stop()
        if state == "recording":
            self.setIcon(ICON_RECORDING)
        elif state == "processing":
            self._spin_idx = 0
            self._spin_timer.start()
        else:
            self.setIcon(ICON_IDLE)

    def _spin_tick(self):
        self.setIcon(SPINNER_FRAMES[self._spin_idx % len(SPINNER_FRAMES)])
        self._spin_idx += 1

    def _show_notification(self, title: str, msg: str):
        self.showMessage(title, msg, QSystemTrayIcon.MessageIcon.Information, 4000)

    # -- menu actions -------------------------------------------------------

    def open_settings(self):
        # Singleton: re-clicking the tray icon while Settings is open focuses
        # the existing window instead of stacking duplicates (which would also
        # leave the hotkey listener paused by whichever closed last).
        if self._settings_dlg is not None:
            self._settings_dlg.raise_()
            self._settings_dlg.activateWindow()
            return
        try:
            dlg = SettingsDialog(self.app_ctrl)
            self._settings_dlg = dlg
            dlg.exec()
        except Exception:
            logging.exception("Settings dialog failed")
        finally:
            self._settings_dlg = None
