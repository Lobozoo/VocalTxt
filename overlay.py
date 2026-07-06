"""
overlay.py — floating on-screen indicator (WisprFlow / Siri style).

A frameless, translucent pill at the bottom-centre of the primary screen:

  * recording   : live waveform bars driven by the actual mic RMS level
                  published by audio.Recorder, plus "Listening…"
  * processing  : a travelling sine wave sweep, plus "Transcribing…"
  * idle        : fades out and hides.

Two properties are non-negotiable for a dictation overlay:
  * it must NEVER take keyboard focus — otherwise the paste would land in
    the overlay instead of the app the user was dictating into
    (WindowDoesNotAcceptFocus + WA_ShowWithoutActivating), and
  * it must be click-through so a stray mouse click can't hit it
    (WA_TransparentForMouseEvents).
"""

import math
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

WIDTH, HEIGHT = 300, 86
BAR_AREA_H = 52             # top zone for the waveform; label sits below it
MARGIN_BOTTOM = 90          # px above the bottom screen edge (clears taskbar)
BARS = 21
FPS_MS = 33                 # ~30 fps animation

BG = QColor(24, 24, 32, 235)
BAR_RECORDING = QColor(255, 92, 92)      # red — matches the tray icon
BAR_PROCESSING = QColor(86, 156, 255)    # blue sweep while transcribing
TEXT = QColor(220, 220, 228)


class Overlay(QWidget):
    def __init__(self, recorder):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool                       # no taskbar entry
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.recorder = recorder
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(WIDTH, HEIGHT)

        self._state = "idle"
        self._smooth = 0.0           # smoothed mic level
        self._bar_heights = [0.0] * BARS
        self._t0 = time.monotonic()

        self._timer = QTimer(self)
        self._timer.setInterval(FPS_MS)
        self._timer.timeout.connect(self._tick)

        self._fade = QTimer(self)
        self._fade.setInterval(FPS_MS)
        self._fade.timeout.connect(self._fade_step)

    # ------------------------------------------------------------ state ----

    def apply_state(self, state: str):
        """Connected to the same Qt signal the tray uses — GUI thread only."""
        self._state = state
        if state in ("recording", "processing"):
            self._fade.stop()
            self.setWindowOpacity(1.0)
            self._position()
            if not self.isVisible():
                self.show()      # WA_ShowWithoutActivating: no focus steal
            self._timer.start()
        else:
            self._timer.stop()
            self._fade.start()   # fade out, then hide

    def _position(self):
        """Bottom-centre of whichever screen currently has the cursor —
        follows the user across multi-monitor setups."""
        screen = QApplication.screenAt(self.cursor().pos()) \
            or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.move(
            geo.x() + (geo.width() - WIDTH) // 2,
            geo.y() + geo.height() - HEIGHT - MARGIN_BOTTOM,
        )

    def _fade_step(self):
        op = self.windowOpacity() - 0.12
        if op <= 0:
            self._fade.stop()
            self.hide()
        else:
            self.setWindowOpacity(op)

    # -------------------------------------------------------- animation ----

    def _tick(self):
        t = time.monotonic() - self._t0
        if self._state == "recording":
            # Exponential smoothing so the bars breathe instead of flickering.
            raw = min(1.0, self.recorder.level * 12.0)   # RMS→display gain
            self._smooth += (raw - self._smooth) * 0.35
            for i in range(BARS):
                # Centre-weighted envelope × per-bar phase jitter, scaled by
                # live loudness — quiet room = low ripple, speech = big bars.
                centre = 1.0 - abs(i - BARS // 2) / (BARS / 2)
                wobble = 0.6 + 0.4 * math.sin(t * 9 + i * 1.7)
                target = 0.08 + self._smooth * centre * wobble
                self._bar_heights[i] += (target - self._bar_heights[i]) * 0.5
        else:  # processing: level-independent travelling wave
            for i in range(BARS):
                self._bar_heights[i] = 0.25 + 0.20 * math.sin(t * 6 - i * 0.55)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Pill background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(BG)
        p.drawRoundedRect(0, 0, WIDTH, HEIGHT, 28, 28)

        # Waveform bars — full width across the top zone
        color = BAR_RECORDING if self._state == "recording" else BAR_PROCESSING
        pen = QPen(color, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        span = WIDTH - 56
        gap = span / (BARS - 1)
        cy = 12 + BAR_AREA_H // 2
        max_half = BAR_AREA_H // 2 - 6
        for i, h in enumerate(self._bar_heights):
            x = 28 + i * gap
            half = max(2.0, h * max_half * 2)
            half = min(half, max_half)
            p.drawLine(int(x), int(cy - half), int(x), int(cy + half))

        # Label — centred beneath the waveform, clear of the bars
        p.setPen(TEXT)
        p.setFont(QFont("Segoe UI", 9))
        label = "Listening…" if self._state == "recording" else "Transcribing…"
        p.drawText(
            0, HEIGHT - 26, WIDTH, 20,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            label,
        )
        p.end()
