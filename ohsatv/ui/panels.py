"""The cards round the picture: lock badges, transmit, the plan, the band.

Every number and sentence here comes from the engine's state or from
ohsatv.bands / ohsatv.state.SPEC - nothing is restated - so the window cannot
disagree with the guard about why transmit is refused or what fits.

As in OpenHamShack NB, each state is said two ways at once - colour *and* words -
because a red/green distinction alone fails one operator in twelve.
"""
from __future__ import annotations

import math
import os
import time
from pathlib import Path

from PyQt6.QtCore import QDir, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QFileDialog, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .. import bands
from ..i18n.fmt import frequency, number
from . import icons, theme
from .controls import Anim, Button, Card, FocusVisible, Segmented, Slider, Toggle, mix
from .picture import fmt_clock, float_to_u8, rgb_qimage

SOURCES = [("testcard", "Test card"), ("bars", "Bars"), ("webcam", "Webcam"), ("file", "File…")]
SOUNDS = [("tone", "Tone"), ("mic", "Mic"), ("file", "File"), ("none", "None")]
# The picture formats (ohsatv/narrow.py), short names for the transmit panel:
# FM's narrow ones, and AM's 625 or 405 lines (ohsatv/sysa.py).
FORMATS = [("pal", "PAL"), ("lowpal", "Low-PAL"), ("mac", "Mini-MAC")]
FORMATS_AM = [("pal", "625 PAL"), ("405", "405 lines")]
CABLE_BANNER = "CABLE FEED — NOT FOR ANTENNAS"


def first_sentence(text: str) -> str:
    """Up to the first full stop that ends a sentence (not one inside a
    number such as 1.25 MHz or an abbreviation like 'Dec.')."""
    for i, ch in enumerate(text):
        if ch == "." and (i + 1 == len(text) or text[i + 1] == " ") and i > 0 and not text[i - 1].isdigit():
            return text[:i + 1]
    return text


def cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


class WrapLabel(QLabel):
    """A word-wrapping label in a theme colour, optionally with an icon."""

    def __init__(self, text: str = "", colour: str = "text2", px: float = 12.5, parent=None, weight=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.colour = colour
        self.setFont(theme.font(px, weight or theme.QFont.Weight.Normal))
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.set_colour(colour)

    def set_colour(self, colour: str):
        self.colour = colour
        self.setStyleSheet(f"color: {theme.t()[colour]}; background: transparent;")


class ElidedLabel(QLabel):
    """One line that shortens itself to fit, in the middle - a file name
    keeps its beginning and its extension - with the whole text on hover."""

    def __init__(self, colour: str = "text2", px: float = 11.5, parent=None):
        super().__init__("", parent)
        self.full = ""
        self.setFont(theme.font(px))
        self.setStyleSheet(f"color: {theme.t()[colour]}; background: transparent;")
        # Ignored, not Preferred: a long name must not widen the panel; the
        # panel's width decides how much of the name is shown.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def set_full(self, text: str, tip: str = ""):
        self.full = text
        self.setToolTip(tip or text)
        self._elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        fm = QFontMetricsF(self.font())
        super().setText(fm.elidedText(self.full, Qt.TextElideMode.ElideMiddle, max(0, self.width() - 2)))


def video_start_dir(remembered: str) -> str:
    """Where the File… dialog opens: the folder a file was last chosen from;
    the first time (or if that folder has gone), ~/Videos, which is where a
    desktop puts video; and if there is none, home."""
    for d in (remembered, str(Path.home() / "Videos")):
        if d and os.path.isdir(d):
            return d
    return str(Path.home())


def radio_name(st: dict) -> str:
    """What the receiving radio is, from its own report: the Pluto family
    driver serves a LibreSDR and a Pluto+ too, and the window said "Pluto"
    for Brian's LibreSDR."""
    if st.get("device") == "sim":
        return "Simulator"
    if st.get("device") == "file":
        return "Recording"
    m = ((st.get("deviceStatus") or {}).get("model") or "").lower()
    if "libresdr" in m:
        return "LibreSDR"
    if "pluto+" in m or "plutoplus" in m or "pluto plus" in m:
        return "Pluto+"
    if "nano" in m:
        return "Pluto nano"
    return "Pluto"


# ================================================================ lock badges
class LockBadges(QWidget):
    """SYNC, FIELD, COLOUR, SNR, JITTER and NICAM as six tiles: a caption,
    a value, and a dot whose colour agrees with the words. NICAM says what
    is being heard - STEREO or MONO from NICAM, FM when NICAM is there but
    too damaged and the analogue sound has taken over - with its estimated
    bit-error rate in the caption."""

    N = 6

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.compact = False
        self.setFixedHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        bridge.changed.connect(self._changed)

    def set_compact(self, on: bool):
        self.compact = on
        self.updateGeometry()
        self.update()

    def _tile_w(self):
        return 86 if self.compact else 104

    def sizeHint(self):
        return QSize(self.N * self._tile_w() + (self.N - 1) * 6, 56)

    def minimumSizeHint(self):
        return self.sizeHint()

    def _changed(self, ch):
        if any(k in ch for k in ("hLock", "vLock", "colourLock", "snr", "jitterNs", "rxLock", "running", "keyed",
                                 "duplex", "rxFormat", "nicamLock", "nicamBer", "nicamMode", "nicamActive")):
            self.update()

    def tiles(self) -> list[tuple[str, str, str]]:
        """(caption, value, colour token) - also what the tests read."""
        st = self.bridge.st
        if not st.get("running") or (st.get("keyed") and not st.get("duplex")):
            why = "—" if not st.get("running") else "PAUSED"
            return [(c, why, "text3") for c in ("SYNC", "FIELD", "COLOUR", "SNR", "JITTER", "NICAM")]
        lock = st.get("rxLock", "searching")
        h = float(st.get("hLock") or 0.0)
        sync = (f"{100 * h:.0f} %", "ok" if h >= 0.95 else "warn" if h > 0.2 else "text3")
        field = ("LOCKED", "ok") if st.get("vLock") else ("—", "text3")
        if st.get("colourLock"):
            col = ("MAC" if st.get("rxFormat") == "mac" else "PAL", "ok")
        else:
            col = ("MONO", "text2") if lock == "locked" else ("—", "text3")
        snr = st.get("snr")
        if snr is None or lock == "searching":
            s = ("—", "text3")
        else:
            s = (f"{float(snr):.1f} dB", "ok" if snr >= 35 else "warn" if snr >= 20 else "err")
        jit = st.get("jitterNs")
        if jit is None or lock == "searching":
            j = ("—", "text3")
        else:
            j = (f"{float(jit):.0f} ns", "ok" if jit < 30 else "warn" if jit < 120 else "err")
        return [("SYNC", *sync), ("FIELD", *field), ("COLOUR", *col), ("SNR", *s), ("JITTER", *j),
                (self.nicam_caption(), *self.nicam_value())]

    def nicam_value(self) -> tuple[str, str]:
        st = self.bridge.st
        if not st.get("nicamLock"):
            return "—", "text3"
        if st.get("nicamActive"):
            return {"stereo": "STEREO", "mono": "MONO", "dual": "DUAL"}.get(st.get("nicamMode"), "NICAM"), "ok"
        # Received but not heard: the analogue sound is cleaner (or NICAM
        # carries data).
        return "FM", "warn"

    def nicam_caption(self) -> str:
        st = self.bridge.st
        ber = st.get("nicamBer")
        if not st.get("nicamLock") or ber is None:
            return "NICAM"
        return f"NICAM {float(ber):.0E}".replace("E-0", "E-") if ber > 0 else "NICAM 0"


    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tw = self._tile_w()
        x = self.width() - (self.N * tw + (self.N - 1) * 6)
        for capt, val, colk in self.tiles():
            r = QRectF(x + 0.5, 0.5, tw - 1, self.height() - 1)
            col = theme.c(colk)
            p.setPen(QPen(mix(theme.c("line"), col, 0.35 if colk not in ("text3", "text2") else 0.0), 1))
            p.setBrush(mix(theme.c("bg1"), col, 0.07 if colk not in ("text3", "text2") else 0.0))
            p.drawRoundedRect(r, 8, 8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(r.x() + 14, r.y() + 17), 3.5, 3.5)
            p.setFont(theme.caps(9.5))
            p.setPen(theme.c("text3"))
            p.drawText(QRectF(r.x() + 24, r.y() + 8, tw - 28, 18), Qt.AlignmentFlag.AlignVCenter, capt)
            p.setFont(theme.font(15 if not self.compact else 13.5, theme.QFont.Weight.DemiBold, mono=True))
            p.setPen(col if colk not in ("text3",) else theme.c("text3"))
            p.drawText(QRectF(r.x() + 10, r.y() + 26, tw - 16, 24), Qt.AlignmentFlag.AlignVCenter, val)
            x += tw + 6


class DeviceChip(QWidget):
    """Which radio, how it is reached and what that link carries; under it
    decoded frames per second and engine CPU."""

    clicked = pyqtSignal()

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setFixedHeight(56)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        bridge.changed.connect(lambda ch: self.update() if any(k in ch for k in (
            "running", "link", "linkMsps", "fps", "cpu", "device", "deviceStatus", "error", "fs", "txKind",
            "txUnderruns")) else None)

    def sizeHint(self):
        return QSize(176, 56)

    def mouseReleaseEvent(self, e):
        self.clicked.emit()

    def text(self) -> tuple[str, str]:
        st = self.bridge.st
        dev = st.get("device", "sim")
        if dev == "sim":
            head = "SIM"
            sub = "simulator · full duplex"
        elif dev == "file":
            head = "FILE"
            sub = "playing a recording · receive only"
        else:
            link = {"usb": "usb", "usb-net": "usb-net", "ethernet": "ethernet"}.get(st.get("link"), st.get("link") or "")
            lm = st.get("linkMsps")
            head = radio_name(st).upper()
            sub = " · ".join(b for b in (link, f"{lm:g} MS/s link" if lm else "") if b)
        if st.get("txKind") == "hackrf":
            # Split operation: the chip says which radio transmits.
            sub = "TX HackRF · " + ("receive " + ("sim" if dev == "sim" else "Pluto"))
        return head, sub

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        st = self.bridge.st
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(theme.c("line"), 1))
        p.setBrush(theme.c("bg1"))
        p.drawRoundedRect(r, 8, 8)
        head, sub = self.text()
        running = bool(st.get("running"))
        dot = theme.c("ok") if running else theme.c("err") if st.get("error") else theme.c("text3")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot)
        p.drawEllipse(QPointF(r.x() + 14, r.y() + 17), 3.5, 3.5)
        p.setFont(theme.font(12.5, theme.QFont.Weight.Bold, spacing=1.0))
        p.setPen(theme.c("text"))
        fm = QFontMetricsF(p.font())
        p.drawText(QRectF(r.x() + 24, r.y() + 7, 80, 20), Qt.AlignmentFlag.AlignVCenter, head)
        p.setFont(theme.font(11))
        p.setPen(theme.c("text3"))
        x2 = r.x() + 24 + fm.horizontalAdvance(head) + 8
        p.drawText(QRectF(x2, r.y() + 7, r.right() - x2 - 6, 20), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetricsF(p.font()).elidedText(sub, Qt.TextElideMode.ElideRight, r.right() - x2 - 6))
        p.setFont(theme.font(11.5, theme.QFont.Weight.DemiBold, mono=True))
        p.setPen(theme.c("text2"))
        line = f"{float(st.get('fps') or 0):4.1f} fps · CPU {100 * float(st.get('cpu') or 0):3.0f} %"
        p.drawText(QRectF(r.x() + 12, r.y() + 28, r.width() - 18, 22), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetricsF(p.font()).elidedText(line, Qt.TextElideMode.ElideRight, r.width() - 18))


# ================================================================ transmit
class TxThumb(QWidget):
    """The source as it will be sent (the txvideo feed), at 4:3."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.img = None
        self._buf = None
        self.compact = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(120)
        bridge.txvideo.connect(self.on_frame)
        bridge.changed.connect(lambda ch: self.update() if "keyed" in ch or "sourceStatus" in ch else None)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        # At 1366 x 768 the panel below needs the room (the format row and
        # its note pushed the warnings under PTT off the window): the
        # preview stops at 180 px and is drawn at 4:3 inside it.
        return min(int(w * 3 / 4), 180) if self.compact else int(w * 3 / 4)

    def set_compact(self, on: bool):
        self.compact = on
        self.setFixedHeight(self.heightForWidth(self.width()))
        self.update()

    def sizeHint(self):
        return QSize(320, 240)

    def resizeEvent(self, e):
        h = self.heightForWidth(self.width())
        if self.height() != h:
            self.setFixedHeight(h)
        super().resizeEvent(e)

    def on_frame(self, item):
        _t, rgb = item
        self.img, _ = rgb_qimage(float_to_u8(rgb))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        clip = QPainterPath()
        clip.addRoundedRect(r, 8, 8)
        p.save()
        p.setClipPath(clip)
        p.fillRect(r, QColor("#030406"))
        if self.img is not None:
            w = min(r.width(), r.height() * 4 / 3)
            p.drawImage(QRectF(r.center().x() - w / 2, r.y(), w, r.height()), self.img)
        else:
            p.setPen(QColor(255, 255, 255, 110))
            p.setFont(theme.font(12))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.tr("Waiting for the source…"))
        p.restore()
        keyed = bool(self.bridge.st.get("keyed"))
        p.setPen(QPen(theme.c("tx") if keyed else QColor(0, 0, 0, 120), 2 if keyed else 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 8, 8)
        # A tag saying this is the outgoing picture.
        f = theme.font(10.5, theme.QFont.Weight.Bold, spacing=1.0)
        fm = QFontMetricsF(f)
        txt = "ON AIR" if keyed else "OUTGOING"
        w = fm.horizontalAdvance(txt) + 16
        tag = QRectF(r.x() + 8, r.y() + 8, w, fm.height() + 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("tx", 0.9) if keyed else QColor(4, 6, 10, 170))
        p.drawRoundedRect(tag, tag.height() / 2, tag.height() / 2)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(tag, Qt.AlignmentFlag.AlignCenter, txt)


class IdentPreview(QWidget):
    """The callsign box as it will appear in vision. It cannot be turned off
    (licence condition 6(21)), so this shows it, rather than offering it."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setFixedHeight(70)
        bridge.changed.connect(lambda ch: self.update() if any(k in ch for k in (
            "callsign", "locator", "identText", "identPosition", "source")) else None)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        st = self.bridge.st
        call = (st.get("callsign") or "").strip().upper()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(theme.c("line"), 1))
        p.setBrush(theme.c("bg2"))
        p.drawRoundedRect(r, 8, 8)
        box = QRectF(r.x() + 10, r.y() + 10, 132 if self.width() > 340 else 112, r.height() - 20)
        p.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
        p.setBrush(QColor("#000000"))
        p.drawRect(box)
        if call:
            p.setFont(theme.font(17, theme.QFont.Weight.Black, mono=True))
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(box.x(), box.y() + 1, box.width(), box.height() * 0.62), Qt.AlignmentFlag.AlignCenter,
                       call)
            p.setFont(theme.font(10.5, theme.QFont.Weight.DemiBold, mono=True))
            p.setPen(QColor("#d6dde6"))
            line = " ".join(x for x in ((st.get("locator") or "").upper(), (st.get("identText") or "").upper()) if x)
            fm = QFontMetricsF(p.font())
            p.drawText(QRectF(box.x(), box.y() + box.height() * 0.58, box.width(), box.height() * 0.4),
                       Qt.AlignmentFlag.AlignCenter, fm.elidedText(line, Qt.TextElideMode.ElideRight, box.width() - 6))
        else:
            p.setFont(theme.font(11, theme.QFont.Weight.Bold))
            p.setPen(theme.c("warn"))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, self.tr("NO CALLSIGN"))
        x = box.right() + 12
        p.setFont(theme.caps(9.5))
        p.setPen(theme.c("text3"))
        p.drawText(QRectF(x, r.y() + 10, r.right() - x - 8, 16), Qt.AlignmentFlag.AlignVCenter,
                   self.tr("Ident · in vision"))
        p.setFont(theme.font(11.5))
        p.setPen(theme.c("text2"))
        if call:
            where = ("in the card" if st.get("source") in ("testcard", "bars")
                     else st.get("identPosition", "bottom-left").replace("-", " "))
            txt = f"Every frame, {where}. Always on."
        else:
            txt = "Set your callsign in Settings › Station."
        p.drawText(QRectF(x, r.y() + 28, r.right() - x - 8, r.height() - 30),
                   Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignTop, txt)


class PttButton(FocusVisible, QWidget):
    """The big PTT button (NB's, reshaped as a bar). Click toggles; the
    window maps Space held to press/release. Greyed with a padlock when the
    guard would refuse; red, breathing and counting the time-out down while
    on air."""

    pressed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.keyed = False
        self.blocked = ""
        self.tx_seconds = 0.0
        self.timeout = 600.0
        self.hover = Anim(self)
        self.on = Anim(self)
        self.glow_t = 0.0
        self.setFixedHeight(84)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_status(self, keyed: bool, blocked: str, tx_seconds: float, timeout: float, all_reasons: list[str]):
        if keyed != self.keyed:
            self.on.to(1.0 if keyed else 0.0)
        self.keyed, self.blocked, self.tx_seconds, self.timeout = keyed, blocked, tx_seconds, timeout
        if keyed:
            tip = "On air. Click, or release Space, to stop."
        elif all_reasons:
            tip = "Transmit is refused here:\n\n" + "\n".join("• " + cap(r) for r in all_reasons)
        else:
            tip = "Transmit: click to key and click again to stop, or hold Space."
        self.setToolTip(tip)
        self.update()

    def tick(self, dt):
        if self.keyed or self.on.value > 0.01:
            self.glow_t += dt
            self.update()

    def enterEvent(self, e):
        self.hover.to(1)

    def leaveEvent(self, e):
        self.hover.to(0)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.pressed.emit()

    def keyPressEvent(self, e):
        # Space is the window's (hold-to-talk); Return on a focused button toggles.
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.pressed.emit()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        k = self.on.value
        red = theme.c("tx")
        avail = not self.blocked
        if k > 0.01:
            pulse = 0.5 + 0.5 * math.sin(self.glow_t * 2 * math.pi / 1.6)
            for i in range(3):
                gl = QColor(theme.t()["txGlow"])
                gl.setAlphaF(0.2 * k * (0.6 + 0.4 * pulse) / (i + 1))
                p.setPen(QPen(gl, 3 + i * 3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(-i, -i, i, i), 14, 14)
        idle_bg = mix(theme.c("bg2"), red, 0.12 + 0.08 * self.hover.value) if avail else theme.c("bg2", 0.6)
        p.setPen(QPen(mix(theme.c("tx", 0.8), red, k) if avail else theme.c("line2"), 1.5))
        p.setBrush(mix(idle_bg, red, k))
        p.drawRoundedRect(r, 14, 14)
        fg = QColor("#ffffff") if k > 0.5 else (red if avail else theme.c("text3"))
        if self.keyed:
            p.setFont(theme.font(24, theme.QFont.Weight.Black, spacing=2))
            p.setPen(fg)
            p.drawText(QRectF(r.x(), r.y() + 8, r.width(), 36), Qt.AlignmentFlag.AlignCenter, self.tr("ON AIR"))
            left = max(0.0, self.timeout - self.tx_seconds)
            p.setFont(theme.font(12.5, theme.QFont.Weight.DemiBold, mono=True))
            p.setPen(QColor(255, 255, 255, 230))
            p.drawText(QRectF(r.x(), r.y() + 42, r.width(), 20), Qt.AlignmentFlag.AlignCenter,
                       self.tr("{elapsed}  ·  {left} left").format(elapsed=fmt_clock(self.tx_seconds),
                                                                   left=fmt_clock(left)))
            frac = min(1.0, self.tx_seconds / max(1.0, self.timeout))
            bar = QRectF(r.x() + 18, r.bottom() - 10, r.width() - 36, 3)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 60))
            p.drawRoundedRect(bar, 1.5, 1.5)
            p.setBrush(QColor(255, 255, 255, 230))
            p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * (1 - frac), 3), 1.5, 1.5)
        else:
            p.setFont(theme.font(26, theme.QFont.Weight.Black, spacing=3))
            fm = QFontMetricsF(p.font())
            label = "PTT"
            lw = fm.horizontalAdvance(label)
            top = QRectF(r.x(), r.y() + 8, r.width(), 40)
            if not avail:
                icons.paint(p, "lock", QRectF(r.center().x() - lw / 2 - 30, top.center().y() - 10, 20, 20), fg, 1.8)
            p.setPen(fg)
            p.drawText(top, Qt.AlignmentFlag.AlignCenter, label)
            p.setFont(theme.font(11, theme.QFont.Weight.DemiBold, spacing=1.0))
            p.setPen(theme.c("text2") if avail else theme.c("text3"))
            p.drawText(QRectF(r.x(), r.y() + 46, r.width(), 22), Qt.AlignmentFlag.AlignCenter,
                       self.tr("CLICK  ·  OR HOLD SPACE") if avail else self.tr("TRANSMIT LOCKED"))
        if self.focus_visible():
            p.setPen(QPen(theme.c("accent"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-2, -2, 2, 2), 15, 15)


class TransmitPanel(Card):
    """Source, sound, ident, power and PTT - the only place that keys."""

    ptt_clicked = pyqtSignal()
    edit_pages = pyqtSignal()           # the Teletext editor, on the folder being sent

    def __init__(self, bridge, prefs=None, parent=None):
        super().__init__("Transmit", parent, pad=14)
        self.bridge = bridge
        self.prefs = prefs
        self.lay.setSpacing(10)
        self.freq = QLabel("")
        self.freq.setFont(theme.font(12, theme.QFont.Weight.DemiBold, mono=True))
        self.set_header_widget(self.freq)
        # The cable feed (Settings › Transmit; DESIGN.md "Cable feed"): Band
        # I is no amateur allocation and the output must go by cable to a
        # television, never an antenna. Red and at the top of the panel for
        # as long as it is on, not only while keyed.
        self.cable = WrapLabel(CABLE_BANNER, "err", 13, weight=theme.QFont.Weight.Black)
        t = theme.t()
        red = QColor(t["tx"])
        self.cable.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cable.setStyleSheet(f"color: #ffffff; background: rgba({red.red()}, {red.green()}, {red.blue()}, 235);"
                                 f" border-radius: 8px; padding: 8px 10px; letter-spacing: 1px;")
        self.cable.setToolTip(self.tr("Settings › Transmit › Cable feed is on: 405 lines on B1-B5, or 625-line PAL "
                                      "on UHF E21-E48, at the radio's least output, by cable through attenuation "
                                      "into a receiver's aerial socket. Never connect an antenna. It is off again "
                                      "at every start."))
        self.cable.hide()
        self.lay.addWidget(self.cable)
        self.thumb = TxThumb(bridge)
        self.lay.addWidget(self.thumb)
        self.src_status = WrapLabel("", "warn", 11.5)
        self.src_status.hide()
        self.lay.addWidget(self.src_status)
        self.source = Segmented(SOURCES, bridge.st.get("source", "testcard"), compact=True)
        self.source.picked.connect(self._pick_source)
        self.lay.addLayout(self._row(self.tr("Picture"), self.source))
        # The file being sent, by name, under the choice that sent it. It was
        # said only on the status strip, as a path that the strip cut short
        # at 1366 x 768 - and the File… chip itself says nothing of which.
        self.file_name = ElidedLabel("text2", 11.5)
        self.file_row = QWidget()
        fv = QVBoxLayout(self.file_row)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.setSpacing(4)
        fr = self._row("", self.file_name)
        fv.addLayout(fr)
        # Pause and seek: the file is played as the transmission goes, and
        # subtitles from an SRT follow where it is (ohsatv/subtitles.py). A
        # pause holds the picture - the transmitter keeps sending it.
        from PyQt6.QtWidgets import QHBoxLayout
        self.file_pause = Button(self.tr("Pause"), small=True, checkable=True)
        self.file_pause.setFixedWidth(64)
        self.file_pause.clicked.connect(lambda: self._file_act(lambda: self.bridge.radio.file_pause(
            self.file_pause.checked)))
        self.file_pos = Slider(self.tr("Position"), 0.0, 1.0, 0.0, 1.0, fmt_clock)
        self.file_pos.committed.connect(lambda v: self._file_act(lambda: self.bridge.radio.file_seek(v)))
        fc = QHBoxLayout()
        fc.setSpacing(8)
        fc.addSpacing(66)
        fc.addWidget(self.file_pause)
        fc.addWidget(self.file_pos, 1)
        fv.addLayout(fc)
        self.file_row.hide()
        self.lay.addWidget(self.file_row)
        self.sound = Segmented(SOUNDS, bridge.st.get("audioSource", "tone"), compact=True)
        self.sound.picked.connect(lambda v: self.bridge.set(audioSource=v))
        self.lay.addLayout(self._row(self.tr("Sound"), self.sound))
        # The picture format (FM only). The narrow ones put colour and sound
        # into the ~5 MS/s a USB Pluto can transmit, at the price of every
        # standard receiver - so whenever one is chosen the panel says so,
        # in amber, under the choice.
        self.format = Segmented(FORMATS, bridge.st.get("videoFormat", "pal"), compact=True)
        self.format.picked.connect(lambda v: self.bridge.set(videoFormat=v))
        self.format.setToolTip(bridge.spec("videoFormat")["help"])
        self.format_row = QWidget()
        fr = self._row(self.tr("Format"), self.format)
        fr.setContentsMargins(0, 0, 0, 0)
        self.format_row.setLayout(fr)
        self.lay.addWidget(self.format_row)
        self.format_note = WrapLabel("", "warn", 11.5, weight=theme.QFont.Weight.DemiBold)
        self.format_note.hide()
        self.lay.addWidget(self.format_note)
        # NICAM stereo rides on standard PAL where the plan has room;
        # switched on where it has none, the panel says why in amber and
        # nothing is sent (the plan card says it too).
        # Laid out as the teletext row below it: the switch first.
        from PyQt6.QtWidgets import QHBoxLayout
        self.nicam = Toggle("", bool(bridge.st.get("nicam")))
        self.nicam.setFixedWidth(44)
        self.nicam.setToolTip(bridge.spec("nicam")["help"])
        self.nicam.toggled.connect(lambda on: self.bridge.set(nicam=on))
        box = QWidget()
        bl = QHBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        bl.addWidget(self.nicam)
        lab = QLabel(self.tr("NICAM-728 digital stereo"))
        lab.setStyleSheet(f"color: {theme.t()['text2']};")
        lab.setToolTip(bridge.spec("nicam")["help"])
        bl.addWidget(lab, 1)
        self.lay.addLayout(self._row(self.tr("Stereo"), box))
        self.extras_note = WrapLabel("", "warn", 11.5)
        self.extras_note.hide()
        self.lay.addWidget(self.extras_note)
        # Teletext: on / off, the service (a folder of .tti pages) and the
        # page receivers open on. When it is on but will not be sent - the
        # mode is too narrow, the folder is empty - the engine's sentence
        # goes under it in amber, as the format's does: a switch that is on
        # and silently sends nothing is the thing not to build.
        from PyQt6.QtWidgets import QHBoxLayout, QLineEdit
        st = bridge.st
        self.ttx_on = Toggle("", bool(st.get("ttxOn")))
        self.ttx_on.setFixedWidth(44)
        self.ttx_on.setToolTip(bridge.spec("ttxOn")["help"])
        self.ttx_on.toggled.connect(lambda v: self.bridge.set(ttxOn=v))
        self.ttx_dir = Button(self.tr("Choose pages…"), icon="folder", small=True)
        self.ttx_dir.setToolTip(bridge.spec("ttxDir")["help"])
        self.ttx_dir.clicked.connect(self._pick_ttx_dir)
        # The editor beside the folder it edits: the pages are made where
        # they are sent from, and a page saved while keyed reaches the air
        # through the folder watch, with no step between the two programs.
        self.ttx_edit = Button("", icon="text", small=True)
        self.ttx_edit.setFixedWidth(30)
        self.ttx_edit.setToolTip(self.tr("Edit these pages in the Teletext editor"))
        self.ttx_edit.clicked.connect(self.edit_pages)
        self.ttx_initial = QLineEdit(str(st.get("ttxInitial") or "100"))
        self.ttx_initial.setMaxLength(3)
        self.ttx_initial.setFixedWidth(46)
        self.ttx_initial.setFont(theme.font(12, theme.QFont.Weight.DemiBold, mono=True))
        self.ttx_initial.setToolTip(self.tr("Initial page: {help}").format(help=bridge.spec("ttxInitial")["help"]))
        self.ttx_initial.editingFinished.connect(self._set_initial)
        box = QWidget()
        bl = QHBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        bl.addWidget(self.ttx_on)
        bl.addWidget(self.ttx_dir, 1)
        bl.addWidget(self.ttx_edit)
        bl.addWidget(self.ttx_initial)
        self.lay.addLayout(self._row(self.tr("Text"), box))
        # Subtitles on 888: an SRT (timed to the file being played) and a
        # box for captions typed live, sent on Enter ('|' for a new line).
        self.srt_btn = Button(self.tr("Subtitles…"), icon="text", small=True)
        self.srt_btn.setToolTip(bridge.spec("ttxSrt")["help"])
        self.srt_btn.clicked.connect(self._pick_srt)
        self.srt_clear = Button("", icon="close", small=True)
        self.srt_clear.setFixedWidth(30)
        self.srt_clear.setToolTip(self.tr("No SRT file (typed captions only)"))
        self.srt_clear.clicked.connect(lambda: self.bridge.set(ttxSrt=""))
        self.caption = QLineEdit()
        self.caption.setPlaceholderText(self.tr("Caption - Enter sends, | breaks the line"))
        self.caption.setToolTip(self._caption_help())
        self.caption.returnPressed.connect(self._send_caption)
        self.subs_box = QWidget()
        sv = QVBoxLayout(self.subs_box)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(6)
        srow = QHBoxLayout()
        srow.setSpacing(8)
        srow.addWidget(self.srt_btn, 1)
        srow.addWidget(self.srt_clear)
        sv.addLayout(srow)
        sv.addWidget(self.caption)
        self.lay.addLayout(self._row("888", self.subs_box))
        self.subs_caption = self.lay.itemAt(self.lay.count() - 1)
        self.ttx_note = WrapLabel("", "warn", 11.5, weight=theme.QFont.Weight.DemiBold)
        self.ttx_note.hide()
        self.lay.addWidget(self.ttx_note)
        self.ident = IdentPreview(bridge)
        self.lay.addWidget(self.ident)
        sp = bridge.spec("txGain")
        self.power = Slider(self.tr("Power (TX gain)"), sp["min"], sp["max"], float(bridge.st.get("txGain", sp["default"])),
                            0.25, lambda v: f"{v:.2f} dB", default=sp["default"], colour="tx")
        self.power.cap = float(bridge.st.get("txGainCeiling", -10.0))
        self.power.committed.connect(lambda v: self.bridge.set(txGain=round(v, 2)))
        self.power.setToolTip(self.tr("{help} The red notch is the ceiling set in Settings › Transmit.").format(
            help=sp["help"]))
        self.lay.addWidget(self.power)
        # With the cable feed on there is no power control: the radio's
        # least output is used whatever the slider said.
        self.power_fixed = WrapLabel(self.tr("Power: the radio's least output (cable feed) - −89.75 dB on a Pluto, VGA 0 "
                                             "and no amplifier on a HackRF"), "text2", 11.5)
        self.power_fixed.hide()
        self.lay.addWidget(self.power_fixed)
        self.ptt = PttButton()
        self.ptt.pressed.connect(self.ptt_clicked)
        self.lay.addWidget(self.ptt)
        self.refusal = WrapLabel("", "text2", 12.5)
        self.lay.addWidget(self.refusal)
        # Shared radios (ohsatv/devices/shared.py): the one thing to do about
        # a radio another program has - open it again once it is closed
        # there - or has changed - accept it as it is. Hidden otherwise.
        self.radio_btn = Button("", small=True)
        self.radio_btn.clicked.connect(self._radio_action)
        self.radio_btn.hide()
        self.lay.addWidget(self.radio_btn, 0, Qt.AlignmentFlag.AlignLeft)
        # A lock that covers this computer only (the radio's shell did not
        # answer): said for as long as it is so, not only in a toast.
        self.lock_note = WrapLabel("", "warn", 11.5)
        self.lock_note.hide()
        self.lay.addWidget(self.lock_note)
        # The one warning that is a decision, not a neighbour: transmitting
        # analogue where the bandplan has none (70cm). A red banner, not one
        # more amber line (Brian: allowed, with a prominent warning).
        self.outside = WrapLabel("", "err", 12.5, weight=theme.QFont.Weight.Bold)
        t = theme.t()
        red = QColor(t["tx"])
        self.outside.setStyleSheet(f"color: {t['tx']}; background: rgba({red.red()}, {red.green()}, {red.blue()}, 28);"
                                   f" border: 1px solid {t['tx']}; border-radius: 8px; padding: 8px 10px;")
        self.outside.hide()
        self.lay.addWidget(self.outside)
        self.compact = False
        self.warnings = WrapLabel("", "warn", 12)
        self.lay.addWidget(self.warnings)
        self.lay.addStretch(1)
        bridge.changed.connect(self._changed)
        bridge.tick.connect(self.ptt.tick)
        self._changed(dict(bridge.st))

    def _radio_action(self):
        st = self.bridge.st
        if not st.get("running"):
            err = self.bridge.start()
            if err:
                self.bridge.say(err, "error")
        else:
            self.bridge.accept_radio()

    def set_compact(self, on: bool):
        self.compact = on
        self.thumb.set_compact(on)
        self.update_tx()
        self.update_format()
        self.update_ttx()

    def update_format(self):
        from .. import narrow
        st = self.bridge.st
        fm = st.get("system") == "fm"
        fmt = st.get("videoFormat", "pal")
        opts = FORMATS if fm else FORMATS_AM
        shown = fmt if any(v == fmt for v, _ in opts) else "pal"      # what this system actually sends
        self.format.set_options(opts, shown)
        self.format.set_value(shown)
        self.format_row.setVisible(True)
        if narrow.is_narrow(shown):
            # At 1366 x 768 the panel has one line to spare: the short form,
            # the whole sentence on hover.
            self.format_note.setText(self.tr("◆ OpenHamShack ATV only") if self.compact else
                                     f"◆ {narrow.LABELS[fmt]}: {narrow.NOT_STANDARD}.")
            self.format_note.setToolTip(f"{narrow.LABELS[fmt]}: {narrow.NOT_STANDARD}.")
            self.format_note.show()
        else:
            self.format_note.hide()

    def update_extras(self):
        st = self.bridge.st
        self.nicam.set_on(bool(st.get("nicam")))
        pl = st.get("plan") or {}
        notes = [w for w in (pl.get("nicamWhy") if st.get("nicam") else "",) if w]
        self.extras_note.setText("\n".join("▲ " + cap(w) for w in notes))
        self.extras_note.setVisible(bool(notes))

    def _row(self, caption: str, w: QWidget):
        from PyQt6.QtWidgets import QHBoxLayout
        lay = QHBoxLayout()
        lay.setSpacing(10)
        c = QLabel(caption.upper())
        c.setFont(theme.caps(9.5))
        c.setStyleSheet(f"color: {theme.t()['text3']};")
        c.setFixedWidth(56)
        lay.addWidget(c)
        lay.addWidget(w, 1)
        return lay

    def _file_act(self, fn):
        try:
            fn()
        except ValueError as e:
            self.bridge.say(str(e))

    def update_file(self):
        st = self.bridge.st
        n = st.get("fileLen")
        pos = st.get("filePos")
        if n:
            self.file_pos.set_range(0.0, float(n))
            self.file_pos.fmt = lambda v, n=n: f"{fmt_clock(v)} / {fmt_clock(n)}"
        if pos is not None and not self.file_pos.drag:
            self.file_pos.set_value(float(pos))
        self.file_pause.set_checked(bool(st.get("filePaused")))
        self.file_pause.set_text(self.tr("Play") if st.get("filePaused") else self.tr("Pause"))

    def _pick_srt(self):
        start = os.path.dirname(self.bridge.st.get("ttxSrt") or "") or os.path.dirname(
            self.bridge.st.get("videoFile") or "") or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Subtitles to send on page 888"), start,
                                              self.tr("Subtitles (*.srt);;All files (*)"))
        if path:
            self.bridge.set(ttxSrt=QDir.toNativeSeparators(path))

    def _caption_help(self) -> str:
        return self.tr("Typed live onto page 888 for as long as it takes to read; Enter on an empty box clears it")

    def _send_caption(self):
        text = self.caption.text()
        try:
            self.bridge.radio.caption(text)
        except ValueError as e:
            self.bridge.say(str(e))
            return
        self.caption.clear()

    def _pick_ttx_dir(self):
        start = self.bridge.st.get("ttxDir") or (self.prefs.get("ttxDirLast", "") if self.prefs is not None else "")
        start = start or os.path.expanduser("~")
        path = QDir.toNativeSeparators(
            QFileDialog.getExistingDirectory(self, self.tr("Teletext pages to send (a folder of .tti files)"), start))
        if not path:
            return
        if self.prefs is not None:
            self.prefs["ttxDirLast"] = path
            self.prefs.save()
        self.bridge.set(ttxDir=path, ttxOn=True)

    def _set_initial(self):
        v = self.ttx_initial.text().strip().upper()
        if v == self.bridge.st.get("ttxInitial"):
            return
        if not self.bridge.set(ttxInitial=v):
            self.ttx_initial.setText(str(self.bridge.st.get("ttxInitial") or "100"))

    def update_ttx(self):
        st = self.bridge.st
        self.ttx_on.set_on(bool(st.get("ttxOn")))
        d = st.get("ttxDir") or ""
        self.ttx_dir.set_text(os.path.basename(d.rstrip("/")) or d if d else self.tr("Choose pages…"))
        srt = st.get("ttxSrt") or ""
        self.srt_btn.set_text(os.path.basename(srt) if srt else self.tr("Subtitles…"))
        self.srt_clear.setVisible(bool(srt))
        on = bool(st.get("ttxOn"))
        lay = self.subs_caption.layout()
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is not None:
                w.setVisible(on)
        if not on:
            self.srt_clear.hide()
        said = st.get("ttxSub") or ""
        self.caption.setToolTip(self.tr("On air now: {caption}").format(caption=said) + "\n\n" + self._caption_help()
                                if said else self._caption_help())
        if not self.ttx_initial.hasFocus():
            self.ttx_initial.setText(str(st.get("ttxInitial") or "100"))
        why = st.get("ttxRefused") or ""
        what = st.get("ttxStatus") or ""
        if st.get("ttxOn") and why:
            self.ttx_note.set_colour("warn")
            self.ttx_note.setText("◆ " + (first_sentence(why) if self.compact else why))
            self.ttx_note.setToolTip(why)
            self.ttx_note.show()
        elif st.get("ttxOn") and what and not self.compact:
            # Sent: say what, quietly - and the plan's caveat, if any.
            self.ttx_note.set_colour("text3")
            self.ttx_note.setText(what)
            self.ttx_note.setToolTip(what)
            self.ttx_note.show()
        else:
            self.ttx_note.hide()

    def _pick_source(self, v: str):
        if v == "file":
            # The folder is a window preference, beside the theme and the
            # geometry, not an engine setting: the engine needs the file,
            # never where the operator likes to browse. The current file,
            # if it is in that folder, is selected in it.
            start = video_start_dir(self.prefs.get("videoDir", "") if self.prefs is not None else "")
            cur = self.bridge.st.get("videoFile") or ""
            if cur and os.path.dirname(cur) == start and os.path.isfile(cur):
                start = cur
            path, _ = QFileDialog.getOpenFileName(
                self, self.tr("Video file to transmit"), start,
                self.tr("Video (*.mp4 *.mkv *.avi *.mov *.webm *.ts *.mpg);;All files (*)"))
            if not path:
                self.source.set_value(self.bridge.st.get("source", "testcard"))
                return
            # Qt's dialogs answer C:/Users/... on Windows; the setting, the
            # tooltip and the saved folder take Windows' own spelling. (A
            # no-op on Linux.)
            path = QDir.toNativeSeparators(path)
            if self.prefs is not None:
                self.prefs["videoDir"] = os.path.dirname(path)
                self.prefs.save()
            self.bridge.set(source="file", videoFile=path)
            return
        self.bridge.set(source=v)

    def _changed(self, ch):
        st = self.bridge.st
        if "source" in ch:
            self.source.set_value(st["source"])
        if "audioSource" in ch:
            self.sound.set_value(st["audioSource"])
        if any(k in ch for k in ("videoFormat", "system")):
            self.update_format()
        if any(k in ch for k in ("nicam", "plan")):
            self.update_extras()
        if any(k in ch for k in ("ttxOn", "ttxDir", "ttxInitial", "ttxStatus", "ttxRefused", "ttxSrt", "ttxSub")):
            self.update_ttx()
        if any(k in ch for k in ("filePos", "fileLen", "filePaused")):
            self.update_file()
        if "cableFeed" in ch:
            on = bool(st.get("cableFeed"))
            self.cable.setVisible(on)
            self.power.setVisible(not on)
            self.power_fixed.setVisible(on)
        if "txGain" in ch and not self.power.drag:
            self.power.set_value(float(st["txGain"]))
        if "txGainCeiling" in ch:
            self.power.cap = float(st["txGainCeiling"])
            self.power.update()
        if any(k in ch for k in ("txFreq", "split", "keyed")):
            self.freq.setText(self.tr("TX {freq}").format(freq=frequency(int(round(float(st.get("txFreq") or 0))), "MHz", 3)))
            self.freq.setStyleSheet(f"color: {theme.t()['tx' if st.get('keyed') else 'text2']};")
            self.freq.adjustSize()
        if any(k in ch for k in ("source", "videoFile")):
            f = st.get("videoFile") or ""
            self.file_name.set_full(os.path.basename(f), f)
            self.file_row.setVisible(st.get("source") == "file" and bool(f))
        if any(k in ch for k in ("sourceStatus", "sourceError", "source")):
            # The engine says whether the source failed. This used to be
            # guessed from the status's wording, and the guess ("webcam ",
            # "file ") never matched the real "webcam: 12 frames", so a
            # working camera or file showed its frame count as an amber
            # warning that something was wrong.
            err = st.get("sourceError") or ""
            self.src_status.setText(err)
            self.src_status.setVisible(bool(err) and st.get("source") in ("webcam", "file"))
        if any(k in ch for k in ("txRefused", "txWarnings", "keyed", "txSeconds", "running", "txTimeoutMin",
                                 "error", "radioLock", "radioChanged")):
            self.update_tx()

    def update_tx(self):
        st = self.bridge.st
        reasons = list(st.get("txRefused") or [])
        if not st.get("running"):
            reasons = [st.get("error") or "The radio is not running"] + reasons
        blocked = reasons[0] if reasons else ""
        self.ptt.set_status(bool(st.get("keyed")), blocked, float(st.get("txSeconds") or 0),
                            60 * float(st.get("txTimeoutMin") or 10), reasons)
        lock = st.get("radioLock") or {}
        # Who has the radio is the whole point of the lock's sentence - its
        # first sentence names them, the second says this program touched
        # nothing - so it is shown whole, not cut at the first full stop.
        whole = bool(blocked) and blocked in (lock.get("refused"), lock.get("lost"))
        if blocked:
            more = f"  (+{len(reasons) - 1} more — hover)" if len(reasons) > 1 else ""
            self.refusal.setText(cap(blocked if whole else first_sentence(blocked)) + more)
            self.refusal.setToolTip("\n\n".join(cap(r) for r in reasons))
            self.refusal.set_colour("text")
            self.refusal.show()
        else:
            self.refusal.setText("")
            self.refusal.hide()
        if not st.get("running") and (lock.get("refused") or lock.get("lost")):
            self.radio_btn.text = "Open the radio again"
            self.radio_btn.show()
        elif st.get("running") and st.get("radioChanged") and not st.get("keyed"):
            self.radio_btn.text = "Accept the radio as it is now"
            self.radio_btn.show()
        else:
            self.radio_btn.hide()
        self.radio_btn.updateGeometry()
        self.radio_btn.update()
        only_here = [n for h in lock.get("held") or [] for n in h.get("notes") or [] if "covers this computer only" in n]
        self.lock_note.setText("\n".join("▲ " + n + "." for n in only_here))
        self.lock_note.setVisible(bool(only_here) and bool(st.get("running")))
        warns = list(st.get("txWarnings") or [])
        outside = [w for w in warns if w.startswith("OUTSIDE THE BANDPLAN")]
        if outside:
            self.outside.setText(self.tr("⚠ {why}. It is logged with every transmission.").format(why=outside[0]))
            self.outside.show()
        else:
            self.outside.hide()
        warns = [w for w in warns if w not in outside]
        if warns:
            # 1366 x 768: one line and a count, the rest on hover - three
            # wrapped sentences pushed the panel into a scroll.
            n = 1 if self.compact else 3
            shown = warns[:n]
            text = "\n".join("▲ " + cap(w) for w in shown)
            if len(warns) > n:
                text += f"\n+{len(warns) - n} more (hover)"
            self.warnings.setText(text)
            self.warnings.setToolTip("\n\n".join(cap(w) for w in warns))
            self.warnings.show()
        else:
            self.warnings.hide()


# ================================================================ plan
def _nicam_dev() -> float:
    from .. import nicam
    return nicam.FM_RMS_DEV


def shown_plan(st: dict) -> dict:
    """The plan the card and its strip describe: while keyed, what we send
    (`plan`); otherwise what the receiver is decoding (`rxPlan`), which is
    another format when formatAuto found one on the air. Whether transmit
    is allowed is always the sent plan's (PlanCard.fits)."""
    if st.get("keyed"):
        return st.get("plan") or {}
    return st.get("rxPlan") or st.get("plan") or {}


class StatTile(QWidget):
    def __init__(self, caption: str, parent=None):
        super().__init__(parent)
        self.caption, self.value, self.colour = caption, "—", "text"
        self.setFixedHeight(52)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set(self, value: str, colour: str = "text"):
        self.value, self.colour = value, colour
        self.update()

    def sizeHint(self):
        return QSize(90, 52)

    def minimumSizeHint(self):
        return QSize(60, 52)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(theme.c("line"), 1))
        p.setBrush(theme.c("bg2"))
        p.drawRoundedRect(r, 7, 7)
        p.setFont(theme.caps(9))
        p.setPen(theme.c("text3"))
        p.drawText(QRectF(r.x() + 10, r.y() + 6, r.width() - 14, 16), Qt.AlignmentFlag.AlignVCenter, self.caption)
        p.setFont(theme.font(14 if self.width() >= 96 else 12, theme.QFont.Weight.DemiBold, mono=True))
        p.setPen(theme.c(self.colour))
        fm = QFontMetricsF(p.font())
        p.drawText(QRectF(r.x() + 10, r.y() + 23, r.width() - 14, 22), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.value, Qt.TextElideMode.ElideRight, r.width() - 14))


class PlanStrip(QWidget):
    """The plan as a picture: the sample band the radio streams (fs wide,
    with the edges the filters cannot use shaded), the occupied bandwidth
    placed where the modulator puts it, and the carrier, sound and colour
    subcarrier. The tiles above say the same in numbers."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _e):
        from .. import modem, pal
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        st = self.bridge.st
        pl = shown_plan(st)
        fs = float(st.get("fs") or 0)
        if not pl or not fs:
            return
        r = QRectF(2, 16, self.width() - 4, 22)

        def x(off):          # Hz from the LO -> px
            return r.x() + (off / fs + 0.5) * r.width()
        p.setPen(QPen(theme.c("line2"), 1))
        p.setBrush(theme.c("bg2"))
        p.drawRoundedRect(r, 5, 5)
        edge = (1 - modem.USABLE) / 2 * r.width()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("line", 0.9))
        p.drawRect(QRectF(r.x() + 1, r.y() + 1, edge, r.height() - 2))
        p.drawRect(QRectF(r.right() - edge - 1, r.y() + 1, edge, r.height() - 2))
        am = st.get("system") == "am"
        car = float(pl.get("carrier", 0.0)) if am else 0.0
        lo, hi = (pl.get("occupied") or [0, 0])
        a, b = x(car + lo), x(car + hi)
        fits = bool(pl.get("fits", True))
        col = theme.c("rx") if fits else theme.c("err")
        a2, b2 = max(r.x(), a), min(r.right(), b)
        p.setBrush(QColor(col.red(), col.green(), col.blue(), 70))
        p.setPen(QPen(col, 1))
        p.drawRoundedRect(QRectF(a2, r.y() + 3, max(2.0, b2 - a2), r.height() - 6), 3, 3)
        marks = [(car, "C", theme.c("rx"))]
        if am:
            if pl.get("sound") is not None:
                marks.append((float(pl["sound"]), "S", theme.c("sound")))
            if pl.get("colour"):
                marks.append((car + pal.FSC, "PAL", theme.c("chroma")))
        elif pl.get("sub"):
            marks += [(car - float(pl["sub"]), "S", theme.c("sound")), (car + float(pl["sub"]), "S", theme.c("sound"))]
        p.setFont(theme.font(9.5, theme.QFont.Weight.Bold, spacing=0.4))
        for off, lab, c in marks:
            xx = x(off)
            p.setPen(QPen(c, 1.6))
            p.drawLine(QPointF(xx, r.y() - 3), QPointF(xx, r.bottom() + 3))
            p.setPen(c)
            p.drawText(QRectF(xx - 20, 0, 40, 14), Qt.AlignmentFlag.AlignCenter, lab)
        p.setFont(theme.font(10, mono=True))
        p.setPen(theme.c("text3"))
        half = fs / 2e6
        p.drawText(QRectF(r.x(), r.bottom() + 6, 80, 14), Qt.AlignmentFlag.AlignLeft, "-" + number(half))
        p.drawText(QRectF(r.right() - 80, r.bottom() + 6, 80, 14), Qt.AlignmentFlag.AlignRight,
                   self.tr("+{half} MHz").format(half=number(half)))
        p.drawText(QRectF(r.center().x() - 60, r.bottom() + 6, 120, 14), Qt.AlignmentFlag.AlignCenter,
                   self.tr("{rate} MS/s").format(rate=number(fs / 1e6)))


class PlanCard(Card):
    """'What fits at N MS/s': the plan's numbers and every sentence of what
    this rate gives up, then what the link and the device say."""

    def __init__(self, bridge, parent=None):
        super().__init__("What fits", parent, pad=14)
        self.bridge = bridge
        from PyQt6.QtWidgets import QGridLayout
        grid = QGridLayout()
        grid.setSpacing(6)
        self.t_occ, self.t_vid, self.t_col, self.t_snd = (StatTile(c) for c in ("Occupied", "Video", "Colour",
                                                                               "Sound"))
        for i, t in enumerate((self.t_occ, self.t_vid, self.t_col, self.t_snd)):
            grid.addWidget(t, 0, i)
        self.lay.addLayout(grid)
        self.strip = PlanStrip(bridge)
        self.lay.addWidget(self.strip)
        self.fits = WrapLabel("", "ok", 12.5, weight=theme.QFont.Weight.DemiBold)
        self.lay.addWidget(self.fits)
        self.why = WrapLabel("", "text2", 12)
        self.lay.addWidget(self.why)
        self.format = WrapLabel("", "warn", 12, weight=theme.QFont.Weight.DemiBold)
        self.format.hide()
        self.lay.addWidget(self.format)
        # NICAM, when sent: where it goes.
        self.extras = WrapLabel("", "ok", 12)
        self.extras.hide()
        self.lay.addWidget(self.extras)
        self.link = WrapLabel("", "warn", 12)
        self.lay.addWidget(self.link)
        self.short = WrapLabel("", "err", 12, weight=theme.QFont.Weight.DemiBold)
        self.lay.addWidget(self.short)
        # What the engine chose for this radio, and why (ohsatv/profiles.py).
        self.profile = WrapLabel("", "text3", 11.5)
        self.profile.hide()
        self.lay.addWidget(self.profile)
        self.lay.addStretch(1)
        bridge.changed.connect(self._changed)
        self._changed(dict(bridge.st))

    def set_compact(self, on: bool):
        # At 1366 x 768 the card is ~190 px tall: the tiles say what the
        # strip draws, so the strip goes rather than the sentences.
        self.strip.setVisible(not on)

    def _changed(self, ch):
        if not any(k in ch for k in ("plan", "rxPlan", "fs", "linkMsps", "deviceStatus", "system", "txUnderruns",
                                     "txKind", "profile", "keyed", "rxFormat", "videoFormat", "radioMoved",
                                     "cableFeed", "nicam")):
            return
        prof = self.bridge.st.get("profile") or ""
        self.profile.setText(self.tr("⟲ Chosen for this radio: {profile}").format(profile=prof) if prof else "")
        self.profile.setVisible(bool(prof))
        st = self.bridge.st
        pl = shown_plan(st)
        fs = float(st.get("fs") or 0)
        self.set_title(self.tr("What fits at {rate} MS/s").format(rate=number(fs / 1e6)) if fs else self.tr("What fits"))
        self.strip.update()
        am = st.get("system") == "am"
        fmt = pl.get("format", "pal")
        if pl:
            self.t_occ.set(f"{float(pl.get('width', 0)) / 1e6:.1f} MHz")
            self.t_vid.set(f"{float(pl.get('video_bw', 0)) / 1e6:.2f} MHz")
            col = {"mac": "MAC", "lowpal": "PAL 1.6"}.get(fmt, "PAL")
            if fmt == "405":
                # Monochrome is System A, not something this rate gave up.
                self.t_col.set("405 MONO", "text2")
            else:
                self.t_col.set(col if pl.get("colour") else "MONO", "ok" if pl.get("colour") else "warn")
            if am and fmt == "405":
                self.t_snd.caption = "Sound"
                # None below 6.25 MS/s (sysa.SOUND_MIN_FS); the bullets say why.
                if pl.get("sound") is None:
                    self.t_snd.set("NONE", "warn")
                else:
                    self.t_snd.set("−3.5 AM", "ok")
            elif am:
                snd = pl.get("sound")
                off = float(st.get("amSoundOffset") or 0)
                self.t_snd.caption = "Sound"
                self.t_snd.set(f"+{off / 1e6:.1f} MHz" if snd is not None else "NONE",
                               "ok" if snd is not None else "warn")
            elif fmt == "mac":
                self.t_snd.caption = "Sound"
                self.t_snd.set("DIGITAL", "ok")
            else:
                sub = pl.get("sub")
                self.t_snd.caption = "Sound sub"
                self.t_snd.set(f"{float(sub) / 1e6:.1f} MHz" if sub else "NONE", "ok" if sub else "warn")
        if (st.get("plan") or {}).get("fits", True):
            self.fits.setText(self.tr("✓ The signal fits this rate"))
            self.fits.set_colour("ok")
        else:
            self.fits.setText(self.tr("✕ The signal does not fit this rate: transmit is refused"))
            self.fits.set_colour("err")
        from .. import narrow
        why = [cap(w) + ("" if w.endswith(".") else ".") for w in pl.get("why") or [] if w != narrow.NOT_STANDARD]
        if not am and fmt == "pal" and not pl.get("colour") and fs and fs <= narrow.NARROW_MAX_FS:
            why.append("Colour and sound fit this rate as Mini-MAC or narrow colour PAL (Transmit › Format) - "
                       "OpenHamShack ATV only.")
        self.why.setText("\n".join("• " + w for w in why) if why else self.tr("Nothing is given up at this rate."))
        self.why.set_colour("text2" if why else "text3")
        # Non-standard, said once and in amber - not one more bullet - and
        # what the receiver is decoding when formatAuto found something else.
        notes = []
        if narrow.is_narrow(fmt):
            notes.append(f"◆ {narrow.LABELS[fmt]}: {narrow.NOT_STANDARD}.")
        rxf = st.get("rxFormat") or "pal"
        # What PTT sends on this system (videoFormat may name another
        # system's format: Mini-MAC chosen on FM means PAL on AM).
        txf = narrow.fmt_for(st.get("system", "fm"), {"videoFormat": st.get("videoFormat") or "pal"}, fs)
        if rxf != txf:
            # The card above describes the received format; what PTT would
            # send is still the operator's choice, so name it here.
            notes.append(f"◆ Receiving {narrow.LABELS.get(rxf, rxf)} - found on the air. "
                         f"Transmit stays {narrow.SHORT.get(txf, txf)}.")
        self.format.setText("\n".join(notes))
        self.format.setVisible(bool(notes))
        sent = st.get("plan") or {}
        lines = []
        if sent.get("nicam") is not None:
            lines.append("◆ NICAM-728 stereo 6.552 MHz above vision" +
                         (", 20 dB below it" if am else f", {_nicam_dev() / 1e3:.0f} kHz RMS deviation") + ".")
        self.extras.setText("\n".join(lines))
        self.extras.setVisible(bool(lines))
        lm = st.get("linkMsps")
        if lm and fs / 1e6 > float(lm):
            self.link.setText(self.tr("▲ This link is reported to sustain {rate} MS/s — expect dropped samples.").format(
                rate=number(float(lm))))
            self.link.show()
        else:
            self.link.hide()
        msgs = []
        if (st.get("deviceStatus") or {}).get("rxShort"):
            msgs.append("✕ Samples are being dropped: the link cannot keep up with this rate.")
        # Skipped after they arrived: the engine fell behind the radio and
        # the driver skipped whole frames to catch up. Found only from IQ
        # captures after the 8-bit colour test (docs/atv-research.md 13a): the
        # link was fine, so rxShort never said a word, while the picture lost
        # lock and the colour flickered. Shown for 10 s after each.
        ds = st.get("deviceStatus") or {}
        skips = int(ds.get("rxSkips") or 0)
        last = getattr(self, "_skips", (0, 0.0))
        if skips != last[0]:
            self._skips = last = (skips, time.monotonic() if skips > last[0] else 0.0)
        if skips and time.monotonic() - last[1] < 10.0:
            lost = float(ds.get("rxDropped") or 0) / fs if fs else 0.0
            msgs.append(f"✕ This computer is not keeping up with {fs / 1e6:g} MS/s: {lost:.1f} s of signal skipped "
                        f"({skips} times), whole pictures at a time. A lower rate, or fewer programs running, "
                        f"helps.")
        # The radio changed its own rate or LO (docs/atv-research.md 17a: the
        # nano, twice, while the window showed what had been set). Set back
        # and held, it is history after 30 s; not held, the radio is still
        # wrong, so it stays. The engine's toast says it once; this says it
        # for as long as it matters.
        for m in st.get("radioMoved") or []:
            if m.get("held") is False or time.time() - float(m.get("t") or 0) < 30.0:
                msgs.append(f"✕ {m.get('text', '')}.")
        late = int((st.get("deviceStatus") or {}).get("txLate") or 0)
        if st.get("keyed") and late and st.get("txKind") != "hackrf":
            # A Pluto whose link cannot carry the transmit rate: the DAC
            # starves and the far end loses the picture. Seen on the first
            # real transmission (a USB nano at 6.25 MS/s), and not shown then.
            msgs.append(f"✕ Transmit falling behind: {late} late buffers - the link cannot carry "
                        f"{fs / 1e6:g} MS/s out; the far end sees breaks in the picture.")
        if st.get("txKind") == "hackrf" and int(st.get("txUnderruns") or 0):
            # The HackRF sends zeros when the host falls behind: a break in
            # the picture at the far end. Never silent.
            msgs.append(f"✕ HackRF underruns: {int(st['txUnderruns'])} - the computer is not keeping up "
                        f"with {fs / 1e6:g} MS/s; the far end sees breaks in the picture.")
        if msgs:
            self.short.setText("\n".join(msgs))
            self.short.show()
        else:
            self.short.hide()


# ================================================================ band
class ChannelList(QWidget):
    """The band's channels as a grid of chips - frequency, and a dot that is
    green where a home station may transmit, grey where it is receive only -
    with the chosen (or hovered) channel's description under the grid.
    Click to tune. A list with a line each did not fit beside the spectrum."""

    tune = pyqtSignal(float)
    CHIP_H = 28
    COLS = 3

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.COLS, self.CHIP_H = 3, 28
        self.hover = -1
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bridge.changed.connect(lambda ch: self._refresh() if "band" in ch or "rxFreq" in ch else None)
        self._refresh()

    def channels(self):
        b = bands.BY_NAME.get(self.bridge.st.get("band", "23cm"))
        return b.channels if b else ()

    def _rows(self):
        return max(1, math.ceil(len(self.channels()) / self.COLS))

    def set_compact(self, on: bool):
        # 1366 x 768: four narrower, lower chips a row, so all 23cm's ten
        # channels fit without scrolling the card.
        self.COLS, self.CHIP_H = (4, 24) if on else (3, 28)
        self._refresh()

    def _refresh(self):
        self.setFixedHeight(self._rows() * (self.CHIP_H + 6) + 34)
        self.update()

    def _chip(self, i) -> QRectF:
        w = (self.width() - (self.COLS - 1) * 6) / self.COLS
        return QRectF((i % self.COLS) * (w + 6), (i // self.COLS) * (self.CHIP_H + 6), w, self.CHIP_H)

    def _at(self, pos) -> int:
        return next((i for i in range(len(self.channels())) if self._chip(i).contains(pos)), -1)

    def mouseMoveEvent(self, e):
        i = self._at(e.position())
        if i != self.hover:
            self.hover = i
            self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        i = self._at(e.position())
        if i >= 0:
            self.tune.emit(float(self.channels()[i].freq))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f_rx = float(self.bridge.st.get("rxFreq") or 0)
        chs = self.channels()
        cur = next((i for i, ch in enumerate(chs) if abs(ch.freq - f_rx) < 1.0), -1)
        for i, ch in enumerate(chs):
            r = self._chip(i).adjusted(0.5, 0.5, -0.5, -0.5)
            on = i == cur
            hot = i == self.hover
            p.setPen(QPen(theme.c("accent") if on else theme.c("line2") if hot else theme.c("line"), 1))
            p.setBrush(theme.c("accent", 0.16) if on else theme.c("bg3") if hot else theme.c("bg2"))
            p.drawRoundedRect(r, 6, 6)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.c("ok") if ch.tx else theme.c("text3"))
            narrow = self.COLS > 3
            dx = 9 if narrow else 12
            p.drawEllipse(QPointF(r.x() + dx, r.center().y()), 3.0 if narrow else 3.5, 3.0 if narrow else 3.5)
            # Four to a row at 1366 x 768: a smaller figure, so "1311.5" fits
            # whole instead of losing its ".5".
            p.setFont(theme.font(11 if narrow else 12.5, theme.QFont.Weight.DemiBold, mono=True))
            p.setPen(theme.c("accent") if on else theme.c("text"))
            p.drawText(r.adjusted(dx + 8, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter, f"{ch.freq / 1e6:g}")
        k = self.hover if self.hover >= 0 else cur
        y = self._rows() * (self.CHIP_H + 6)
        p.setFont(theme.font(12))
        if k >= 0:
            ch = chs[k]
            p.setPen(theme.c("text2"))
            txt = f"{ch.freq / 1e6:g} MHz — {ch.label}" + ("" if ch.tx else " · receive only")
        else:
            p.setPen(theme.c("text3"))
            txt = "Green: a home station may transmit there. Grey: receive only."
        fm = QFontMetricsF(p.font())
        p.drawText(QRectF(0, y, self.width(), 28), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(txt, Qt.TextElideMode.ElideRight, self.width()))


class BandCard(Card):
    """ohsatv.bands for the current band, verbatim: allocation status, power,
    what the radio can do there, the notes, the channels."""

    tune = pyqtSignal(float)

    def __init__(self, bridge, parent=None):
        super().__init__("Band", parent, pad=14)
        self.bridge = bridge
        self.lay.setSpacing(8)
        self.why = WrapLabel("", "err", 12.5, weight=theme.QFont.Weight.DemiBold)
        self.lay.addWidget(self.why)
        # Channels first: they are what the card is used for (click to tune),
        # and at 1366 x 768 they were below the fold under the facts.
        cap_ = QLabel(self.tr("CHANNELS"))
        cap_.setFont(theme.caps(9.5))
        cap_.setStyleSheet(f"color: {theme.t()['text3']};")
        self.lay.addWidget(cap_)
        self.channels = ChannelList(bridge)
        self.channels.tune.connect(self.tune)
        self.lay.addWidget(self.channels)
        self.facts = WrapLabel("", "text2", 12)
        self.facts.setTextFormat(Qt.TextFormat.RichText)
        self.lay.addWidget(self.facts)
        self.rf = WrapLabel("", "warn", 12)
        self.lay.addWidget(self.rf)
        self.notes = WrapLabel("", "text3", 11.5)
        self.lay.addWidget(self.notes)
        self.lay.addStretch(1)
        self.compact = False
        bridge.changed.connect(self._changed)
        self._changed(dict(bridge.st))

    def set_compact(self, on: bool):
        """At 1366 x 768 the notes move into the facts' tooltip and the
        channels go four a row, so the card fits its column unscrolled."""
        self.compact = on
        self.channels.set_compact(on)
        self.notes.setVisible(not on)
        self._changed({"band": None})

    def _changed(self, ch):
        if not any(k in ch for k in ("band", "bandOk", "bandWhy", "bandLicence")):
            return
        st = self.bridge.st
        b = bands.BY_NAME.get(st.get("band", "23cm"))
        if b is None:
            return
        # The allocation and power are the station's own licence's, from
        # the shared dataset (the engine's bandLicence), not the UK's.
        lic = st.get("bandLicence") or {}
        spans = lic.get("spans") or []
        blocks = ", ".join(f"{number(lo / 1e6)}–{number(hi / 1e6)}" for lo, hi in spans)
        cable = b in bands.CABLE_BANDS
        if b is bands.CABLE:
            self.set_title(self.tr("Band · Band I · B1–B5, cable feed only"))
        elif cable:
            self.set_title(self.tr("Band · UHF · E21–E48, cable feed only"))
        elif spans:
            self.set_title(self.tr("Band · {band} · {spans} MHz").format(
                band=b.name, spans=blocks))
        else:
            self.set_title(self.tr("Band · {band} · not in your licence").format(
                band=b.name))
        lim = lic.get("limit")
        if cable:
            power = b.power
        elif lim:
            power = self.tr("{watts} W {unit} at most (your licence)").format(
                watts=number(lim["watts"]), unit=lim["unit"])
        else:
            power = self.tr("None: your licence class has nothing here")
        if not st.get("bandOk", True):
            self.why.setText("✕ " + (st.get("bandWhy") or self.tr("This radio cannot tune this band.")))
            self.why.show()
        else:
            self.why.hide()
        t = theme.t()
        modes = " / ".join({"am": "AM-VSB", "fm": "FM-ATV"}[m] for m in b.modes)
        used = (self.tr(" — 405 lines, <b>by cable only</b>") if b is bands.CABLE else
                self.tr(" — 625-line PAL (System I) with teletext, <b>by cable only</b>") if cable else
                "" if b.analogue_tx else self.tr(" — <b>outside the bandplan</b> for analogue"))
        # The words in the catalog, the markup out of it: a translator
        # never has to keep a table's tags intact.
        table = "<table cellspacing=0 cellpadding=1>{rows}</table>"
        row = "<tr><td style='color:{colour};padding-right:10px'>{key}</td><td>{value}</td></tr>"
        facts = ((self.tr("Status"), b.status), (self.tr("Power"), power), (self.tr("Used"), modes + used))
        self.facts.setText(table.format(rows="".join(row.format(colour=t["text3"], key=k, value=v) for k, v in facts)))
        self.rf.setText(("▲ " if b.name == "6cm" or cable else "") + b.rf_note)
        self.rf.set_colour("err" if cable else "warn" if b.name == "6cm" else "text2")
        self.notes.setText("\n\n".join(b.notes))
        self.facts.setToolTip("\n\n".join(b.notes) if self.compact else "")


# ================================================================ toasts (NB's)
class Toast(QWidget):
    closed = pyqtSignal(object)

    def __init__(self, parent, level: str, text: str):
        super().__init__(parent)
        self.level, self.text = level, text
        self.k = Anim(self)
        self.born = time.monotonic()
        self.life = {"info": 5.0, "warn": 10.0, "error": 16.0}.get(level, 6.0)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hover = False

    def height_for(self, w: int) -> int:
        fm = QFontMetricsF(theme.font(14, theme.QFont.Weight.Medium))
        r = fm.boundingRect(QRectF(0, 0, w - 76, 1000), Qt.TextFlag.TextWordWrap, self.text)
        return int(max(52, r.height() + 30))

    def enterEvent(self, e):
        self.hover = True

    def leaveEvent(self, e):
        self.hover = False

    def mouseReleaseEvent(self, e):
        self.closed.emit(self)

    def expired(self) -> bool:
        # Hover is asked of the cursor, not remembered from enter/leave: a
        # toast that appears under a still pointer gets an enter and never a
        # leave, and would then stay for ever.
        from PyQt6.QtGui import QCursor
        self.hover = self.isVisible() and self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        return not self.hover and time.monotonic() - self.born > self.life

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self.k.value)
        key = {"info": "accent", "warn": "warn", "error": "err"}.get(self.level, "accent")
        col = theme.c(key)
        r = QRectF(self.rect()).adjusted(6, 4, -6, -4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 90))
        p.drawRoundedRect(r.translated(0, 3), 10, 10)
        p.setBrush(theme.c("bg2"))
        p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 140), 1))
        p.drawRoundedRect(r, 10, 10)
        clip = QPainterPath()
        clip.addRoundedRect(r, 10, 10)
        p.save()
        p.setClipPath(clip)
        p.fillRect(QRectF(r.x(), r.y(), 5, r.height()), col)
        left = max(0.0, 1 - (time.monotonic() - self.born) / self.life) if not self.hover else 1.0
        p.fillRect(QRectF(r.x(), r.bottom() - 2, r.width() * left, 2), QColor(col.red(), col.green(), col.blue(), 120))
        p.restore()
        icons.paint(p, "warn" if self.level != "info" else "info", QRectF(r.x() + 18, r.center().y() - 11, 22, 22),
                    col, 2)
        p.setFont(theme.font(14, theme.QFont.Weight.Medium))
        p.setPen(theme.c("text"))
        p.drawText(QRectF(r.x() + 52, r.y() + 8, r.width() - 64, r.height() - 16),
                   Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap, self.text)


class ToastHost(QWidget):
    """Stacks toasts top-centre. Non-modal: nothing waits on them; a click
    dismisses one; identical repeats refresh rather than pile up."""

    def __init__(self, parent):
        super().__init__(parent)
        from PyQt6.QtCore import QTimer
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.toasts: list[Toast] = []
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._expire)
        self.timer.start()
        self.width_px = 600
        self.top = 110

    def show_toast(self, level: str, text: str):
        for t in self.toasts:
            if t.text == text:
                t.born = time.monotonic()
                t.update()
                return
        t = Toast(self, level, text)
        t.closed.connect(self._remove)
        self.toasts.insert(0, t)
        for x in self.toasts[4:]:
            self._remove(x)
        t.show()
        t.k.to(1.0)
        self._place()

    def texts(self) -> list[str]:
        return [t.text for t in self.toasts]

    def _remove(self, t):
        if t in self.toasts:
            self.toasts.remove(t)
            t.hide()
            t.deleteLater()
            self._place()

    def _expire(self):
        for t in list(self.toasts):
            if t.expired():
                self._remove(t)
            else:
                t.update()

    def _place(self):
        pw = self.parentWidget().width() if self.parentWidget() else 800
        w = min(self.width_px, pw - 40)
        y = 0
        for t in self.toasts:
            h = t.height_for(w)
            t.setGeometry(0, y, w, h)
            y += h + 2
        self.setGeometry((pw - w) // 2, self.top, w, max(1, y))
        self.raise_()
