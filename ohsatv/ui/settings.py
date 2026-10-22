"""Settings: everything that is set once rather than operated.

As in OpenHamShack NB, every engine setting is built from its ohsatv/state.py
entry - label, range, choices and the help sentence printed under it - so a
limit is never written twice. Saving applies all engine changes in one
`set(authorise=True, ...)`, all or nothing. This dialog is the only caller
that passes authorise: the transmit limits (`auth` keys) can be changed here
and nowhere else. A `restart` key makes the engine restart itself; that
never keys. Nothing here can key the transmitter.

The sample-rate pickers show, for each rate, what that rate would carry
(ohsatv.modem.am_plan / fm_plan with the parameters on the mode pages), so a
rate is chosen knowing what it gives up - not discovered afterwards.
"""
from __future__ import annotations

import threading

from PyQt6.QtCore import QObject, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetricsF, QPainter, QPen
from PyQt6.QtWidgets import (QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea,
                             QStackedWidget, QVBoxLayout, QWidget)

from .. import host, modem, txguard
from .. import state as state_mod
from ..i18n.fmt import number
from . import controller as controller_ui
from . import icons, palettes, theme
from .controls import Anim, Button, Check, FocusVisible, Segmented, Slider, Toggle

SP = state_mod.SPEC
MHZ = 1e6

LABELS = {
    "callsign": "Callsign", "locator": "Locator", "identText": "Second ident line", "identPosition": "Ident position",
    "device": "Radio", "plutoUri": "Pluto address", "plutoRx8": "8-bit receive (Tezuka)", "plutoTx8": "8-bit transmit (Tezuka)", "playFile": "Recording to play", "playLoop": "Play in a loop",
    "recSeconds": "Record IQ: time limit", "recMaxMB": "Record IQ: size limit", "recDir": "Record IQ: folder", "amRate": "AM-VSB sample rate", "fmRate": "FM-ATV sample rate", "a405Rate": "405-line sample rate",
    "webcam": "Webcam device",
    "rxGainMode": "Receiver gain control", "rxGain": "Receiver gain (manual)",
    "txGainCeiling": "Transmit gain ceiling", "txTimeoutMin": "Transmit time-out", "watchdogSec": "Window watchdog",
    "amVideoBw": "Video bandwidth", "amVsbLower": "Vestigial sideband", "amSoundOffset": "Sound carrier",
    "amSoundDb": "Sound carrier level", "fmDeviation": "Deviation (peak to peak)", "fmVideoBw": "Video bandwidth",
    "fmPreemph": "Pre-emphasis (CCIR F.405)", "fmSub": "Sound subcarrier", "fmSubIndex": "Subcarrier index",
    "fmInvert": "Invert video", "simCnrDb": "Carrier-to-noise ratio", "simStations": "Other stations",
    "simTxErrHz": "Our transmit frequency error", "audioBackend": "Sound", "audioOut": "Speaker",
    "audioIn": "Microphone", "toneHz": "Test tone",
    "autoProfile": "Choose the best mode for each radio",
    "formatAuto": "Recognise the picture format on receive",
    "ttxLines": "Teletext lines",
    "txDevice": "Transmitter", "hackrfSerial": "HackRF serial number", "hackrfAmp": "HackRF 14 dB amplifier",
    "simNicam": "NICAM stereo from the stations",
    "radioLockSsh": "Mark the radio as in use on the radio itself", "radioSshUser": "Radio login",
    "radioSshPassword": "Radio password",
}
CHOICE_LABELS = {
    "device": {"sim": "Simulator", "pluto": "Pluto family", "file": "Recording (IQ file)"},
    "txDevice": {"same": "The receiving radio", "hackrf": "HackRF One (transmit only)"},
    "rxGainMode": {"manual": "Manual", "slow_attack": "Slow AGC", "fast_attack": "Fast AGC"},
    "identPosition": {"bottom-left": "Bottom left", "bottom-right": "Bottom right", "top-left": "Top left",
                      "top-right": "Top right"},
    "amSoundOffset": {0.0: "None", 5_500_000.0: "5.5 MHz", 5_999_600.0: "5.9996 MHz (UK)", 6_500_000.0: "6.5 MHz"},
    "fmSub": {0.0: "None", 5_500_000.0: "5.5 MHz", 6_000_000.0: "6.0 MHz (UK)", 6_500_000.0: "6.5 MHz"},
    "a405Rate": {0: "As AM-VSB", 5_000_000: "5", 6_250_000: "6.25", 8_000_000: "8", 10_000_000: "10",
                 12_500_000: "12.5", 16_000_000: "16", 20_000_000: "20 MS/s"},
    "audioBackend": {"portaudio": "Sound card", "null": "None"},
}
STEPS = {"rxGain": 1, "txGainCeiling": 0.25, "txTimeoutMin": 0.5, "watchdogSec": 0.5, "amVideoBw": 0.05 * MHZ,
         "amVsbLower": 0.05 * MHZ, "amSoundDb": 0.5, "fmDeviation": 0.25 * MHZ, "fmVideoBw": 0.1 * MHZ,
         "fmSubIndex": 0.01, "simCnrDb": 1, "simTxErrHz": 100, "toneHz": 50}
MHZ_KEYS = {"amVideoBw", "amVsbLower", "fmDeviation", "fmVideoBw"}


def _fmt(key):
    unit = SP[key]["unit"]
    if key in MHZ_KEYS:
        return lambda v: f"{v / MHZ:.2f} MHz"
    if key == "simTxErrHz":
        return lambda v: f"{v / 1000:+.1f} kHz"
    if key == "txTimeoutMin":
        return lambda v: f"{int(v)}:{int(round((v % 1) * 60)):02d} min"
    step = STEPS.get(key, 1)
    places = 0 if step >= 1 else (2 if step < 0.1 else 1)
    return lambda v: f"{v:.{places}f}{(' ' + unit) if unit else ''}"


def rate_name(fs: float) -> str:
    return f"{fs / MHZ:g} MS/s"


class Field(QWidget):
    """One setting: label, control, the help sentence from SPEC."""

    def __init__(self, key: str, value, parent=None, label: str | None = None, help_: str | None = None):
        super().__init__(parent)
        self.key = key
        spec = SP[key]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 8)
        lay.setSpacing(5)
        t = spec["type"]
        lab = label or LABELS.get(key, key)
        if t == "bool":
            self.w = Toggle(lab, bool(value))
            lay.addWidget(self.w)
            self.get = lambda: self.w.on
        elif t == "enum":
            cap = QLabel(lab)
            cap.setObjectName("Muted")
            lay.addWidget(cap)
            names = CHOICE_LABELS.get(key, {})
            self.w = Segmented([(str(c), names.get(c, str(c))) for c in spec["choices"]], str(value))
            lay.addWidget(self.w)
            choices = {str(c): c for c in spec["choices"]}
            self.get = lambda: choices[self.w.value]
        elif t in ("float", "int") and spec["min"] is not None:
            self.w = Slider(lab, spec["min"], spec["max"], float(value), STEPS.get(key, 1), _fmt(key),
                            default=spec["default"])
            lay.addWidget(self.w)
            self.get = (lambda: int(round(self.w.v))) if t == "int" else (lambda: float(self.w.v))
        else:
            cap = QLabel(lab)
            cap.setObjectName("Muted")
            lay.addWidget(cap)
            self.w = QLineEdit("" if value is None else str(value))
            if key in ("callsign", "locator"):
                self.w.setFont(theme.font(16, theme.QFont.Weight.DemiBold, mono=True))
                self.get = lambda: self.w.text().strip().upper() if key == "callsign" else self.w.text().strip()
            else:
                self.get = lambda: self.w.text().strip()
            lay.addWidget(self.w)
        h = help_ if help_ is not None else spec.get("help", "")
        if h:
            hl = QLabel(h)
            hl.setWordWrap(True)
            hl.setObjectName("Faint")
            hl.setFont(theme.font(12))
            lay.addWidget(hl)


class RateList(FocusVisible, QWidget):
    """Every sample rate, each row saying what it would carry at the current
    mode settings, and whether this radio's link is reported to sustain it."""

    picked = pyqtSignal(object)
    ROW = 44

    def __init__(self, system: str, value, plan_fn, link_msps=None, parent=None):
        super().__init__(parent)
        self.system, self.value, self.plan_fn, self.link = system, value, plan_fn, link_msps
        self.rates = list(SP["amRate"]["choices"])
        self.hover = -1
        self.k = Anim(self, float(self.rates.index(value)) if value in self.rates else 0.0)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(self.ROW * len(self.rates) + 2)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        return QSize(560, self.height())

    def _row(self, i) -> QRectF:
        return QRectF(1, 1 + i * self.ROW, self.width() - 2, self.ROW)

    def mouseMoveEvent(self, e):
        i = int((e.position().y() - 1) // self.ROW)
        if i != self.hover:
            self.hover = i
            self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        i = int((e.position().y() - 1) // self.ROW)
        if 0 <= i < len(self.rates):
            self.select(self.rates[i])

    def keyPressEvent(self, e):
        i = self.rates.index(self.value) if self.value in self.rates else 0
        if e.key() == Qt.Key.Key_Down:
            self.select(self.rates[min(len(self.rates) - 1, i + 1)])
        elif e.key() == Qt.Key.Key_Up:
            self.select(self.rates[max(0, i - 1)])
        else:
            super().keyPressEvent(e)

    def select(self, v):
        self.value = v
        self.k.to(float(self.rates.index(v)))
        self.picked.emit(v)

    def summary(self, fs) -> tuple[str, bool]:
        pl = self.plan_fn(fs)
        bits = [f"video {pl['video_bw'] / MHZ:.1f} MHz", "colour" if pl["colour"] else "mono"]
        if self.system == "am":
            bits.append("sound" if pl.get("sound") is not None else "no sound")
        else:
            bits.append(f"{pl['sub'] / MHZ:g} MHz sound" if pl.get("sub") else "no sound")
        bits.append(f"{pl['width'] / MHZ:.1f} MHz wide")
        return " · ".join(bits), bool(pl["fits"])

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(theme.c("line"), 1))
        p.setBrush(theme.c("bg2"))
        p.drawRoundedRect(r, 8, 8)
        sel = self._row(0).translated(0, self.k.value * self.ROW).adjusted(3, 3, -3, -3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("accent", 0.16))
        p.drawRoundedRect(sel, 6, 6)
        p.setBrush(theme.c("accent"))
        p.drawRoundedRect(QRectF(sel.x(), sel.y() + 8, 3, sel.height() - 16), 1.5, 1.5)
        for i, fs in enumerate(self.rates):
            row = self._row(i)
            if i == self.hover and fs != self.value:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.c("bg3"))
                p.drawRoundedRect(row.adjusted(3, 3, -3, -3), 6, 6)
            on = fs == self.value
            p.setFont(theme.font(14, theme.QFont.Weight.DemiBold, mono=True))
            p.setPen(theme.c("text") if on else theme.c("text2"))
            p.drawText(QRectF(row.x() + 18, row.y(), 110, row.height()), Qt.AlignmentFlag.AlignVCenter, rate_name(fs))
            text, fits = self.summary(fs)
            over = self.link is not None and fs / MHZ > float(self.link)
            tags = [("FITS", "ok") if fits else ("TOO NARROW", "err")]
            if over:
                tags.insert(0, (f"OVER {float(self.link):g} MS/s LINK", "warn"))
            p.setFont(theme.font(9.5, theme.QFont.Weight.Bold, spacing=0.6))
            fmt = QFontMetricsF(p.font())
            x = row.right() - 12
            for t, col in tags:
                w = fmt.horizontalAdvance(t) + 12
                tr = QRectF(x - w, row.center().y() - 9, w, 18)
                p.setPen(QPen(theme.c(col, 0.7), 1))
                p.setBrush(theme.c(col, 0.10))
                p.drawRoundedRect(tr, 4, 4)
                p.setPen(theme.c(col))
                p.drawText(tr, Qt.AlignmentFlag.AlignCenter, t)
                x -= w + 6
            p.setFont(theme.font(12.5))
            p.setPen(theme.c("text2") if fits else theme.c("text3"))
            fm = QFontMetricsF(p.font())
            room = x - (row.x() + 128) - 6
            p.drawText(QRectF(row.x() + 128, row.y(), room, row.height()), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(text, Qt.TextElideMode.ElideRight, room))
        if self.focus_visible():
            p.setPen(QPen(theme.c("accent"), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 8, 8)


class Nav(FocusVisible, QWidget):
    """NB's settings navigation, unchanged."""

    picked = pyqtSignal(int)

    def __init__(self, items: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.items = items
        self.cur = 0
        self.k = Anim(self, 0.0)
        self.hover = -1
        self.setFixedWidth(210)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _row(self, i):
        return QRectF(10, 64 + i * 40, self.width() - 20, 36)

    def mouseMoveEvent(self, e):
        h = next((i for i in range(len(self.items)) if self._row(i).contains(e.position())), -1)
        if h != self.hover:
            self.hover = h
            self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        for i in range(len(self.items)):
            if self._row(i).contains(e.position()):
                self.select(i)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Down:
            self.select(min(len(self.items) - 1, self.cur + 1))
        elif e.key() == Qt.Key.Key_Up:
            self.select(max(0, self.cur - 1))
        else:
            super().keyPressEvent(e)

    def select(self, i):
        self.cur = i
        self.k.to(i)
        self.picked.emit(i)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), theme.c("bg1"))
        p.setPen(QPen(theme.c("line"), 1))
        p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        p.setFont(theme.font(19, theme.QFont.Weight.Bold))
        p.setPen(theme.c("text"))
        p.drawText(QRectF(22, 18, 180, 30), Qt.AlignmentFlag.AlignVCenter, self.tr("Settings"))
        sel = self._row(0).translated(0, self.k.value * 40)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("accent", 0.16))
        p.drawRoundedRect(sel, 8, 8)
        p.setBrush(theme.c("accent"))
        p.drawRoundedRect(QRectF(sel.x(), sel.y() + 8, 3, sel.height() - 16), 1.5, 1.5)
        for i, (icon, name) in enumerate(self.items):
            r = self._row(i)
            if i == self.hover and i != self.cur:
                p.setBrush(theme.c("bg3"))
                p.drawRoundedRect(r, 8, 8)
            col = theme.c("accent") if i == self.cur else theme.c("text2")
            icons.paint(p, icon, QRectF(r.x() + 14, r.center().y() - 9, 18, 18), col, 1.6)
            p.setFont(theme.font(13.5, theme.QFont.Weight.DemiBold if i == self.cur else theme.QFont.Weight.Normal))
            p.setPen(theme.c("text") if i == self.cur else theme.c("text2"))
            p.drawText(QRectF(r.x() + 44, r.y(), r.width() - 48, r.height()), Qt.AlignmentFlag.AlignVCenter, name)
            p.setPen(Qt.PenStyle.NoPen)


# The driver for whatever Find finds. discover() returns only the Pluto
# family (it keeps AD936x contexts), and a Pluto, a Tezuka nano and a
# LibreSDR are all driven by "pluto" - the LibreSDR is no exception, it is an
# AD9363 behind the same libiio. If discover() ever learns another kind of
# radio, this becomes a lookup on what it reports.
FOUND_DRIVER = "pluto"


class _Finder(QObject):
    """ohsatv.devices.pluto.discover on a worker thread: it probes addresses
    with timeouts and can take several seconds; the dialog must not freeze."""

    done = pyqtSignal(list, list)

    def run(self):
        def work():
            from ..devices.pluto import discover
            notes: list[str] = []
            try:
                res = discover(notes=notes)
            except Exception as e:  # noqa: BLE001 - discover promises not to raise; belt and braces
                res, notes = [], [str(e)]
            self.done.emit(res, notes)
        threading.Thread(target=work, name="ohsatv-find-pluto", daemon=True).start()


class Swatch(QWidget):
    picked = pyqtSignal(str)

    def __init__(self, name: str, on: bool, parent=None):
        super().__init__(parent)
        self.name, self.on = name, on
        self.setFixedSize(124, 56)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        self.picked.emit(self.name)

    def paintEvent(self, _e):
        from PyQt6.QtGui import QLinearGradient
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        g = QLinearGradient(r.x(), 0, r.right(), 0)
        for pos, rgb in palettes.gradient_stops(self.name, 10):
            g.setColorAt(pos, QColor((rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        bar = r.height() * 0.52
        p.drawRoundedRect(QRectF(r.x(), r.y(), r.width(), bar), 6, 6)
        p.setFont(theme.font(12, theme.QFont.Weight.DemiBold))
        p.setPen(theme.c("text") if self.on else theme.c("text2"))
        p.drawText(QRectF(r.x(), r.y() + bar, r.width(), r.height() - bar), Qt.AlignmentFlag.AlignCenter, self.name)
        if self.on:
            p.setPen(QPen(theme.c("accent"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 7, 7)


class SettingsDialog(QDialog):
    PAGES = [("star", "Station"), ("antenna", "Radio"), ("lock", "Transmit"), ("tv", "AM-VSB"), ("waves", "FM-ATV"),
             ("link", "Controller"), ("chip", "Simulator"), ("webcam", "Sources"), ("speaker", "Audio"),
             ("theme", "Display")]

    def __init__(self, bridge, prefs, parent=None, page: int = 0):
        super().__init__(parent)
        self.bridge, self.prefs = bridge, prefs
        self.setWindowTitle(self.tr("Settings"))
        self.setModal(True)
        self.resize(1000, 740)
        self.st = dict(bridge.radio.state())
        self.fields: dict[str, Field] = {}
        self.extra: dict[str, object] = {}
        self.palette = prefs["palette"]
        self.finder = _Finder(self)
        self.finder.done.connect(self._found)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        names = self.page_names()
        self.nav = Nav([(icon, names[name]) for icon, name in self.PAGES])
        root.addWidget(self.nav)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {theme.t()['err']}; padding: 0 28px;")
        self.error.hide()
        right.addWidget(self.error)
        bar = QHBoxLayout()
        bar.setContentsMargins(28, 12, 28, 18)
        note = QLabel(self.tr("Changes to the radio, rates or sound restart the receiver. Nothing here can transmit."))
        note.setObjectName("Faint")
        note.setWordWrap(True)
        bar.addWidget(note, 1)
        cancel = Button(self.tr("Cancel"), kind="ghost")
        cancel.clicked.connect(self.reject)
        save = Button(self.tr("Save"), kind="primary")
        save.setMinimumWidth(110)
        save.clicked.connect(self.save)
        bar.addWidget(cancel)
        bar.addWidget(save)
        right.addLayout(bar)
        root.addLayout(right, 1)
        for i, (_icon, name) in enumerate(self.PAGES):
            self.stack.addWidget(self._page(name))
        self.nav.picked.connect(self.stack.setCurrentIndex)
        self.nav.picked.connect(self._page_shown)
        self.nav.select(page)
        self.nav.k.value = float(page)

    def page_names(self) -> dict[str, str]:
        """What the navigation shows for each of PAGES. PAGES keeps the
        English names, because _page, _page_shown and the tests find a page
        by them; only what is drawn is translated."""
        return {"Station": self.tr("Station"), "Radio": self.tr("Radio"), "Transmit": self.tr("Transmit"),
                "AM-VSB": self.tr("AM-VSB"), "FM-ATV": self.tr("FM-ATV"), "Controller": self.tr("Controller"),
                "Simulator": self.tr("Simulator"),
                "Sources": self.tr("Sources"), "Audio": self.tr("Audio"), "Display": self.tr("Display")}

    # ------------------------------------------------------------ pages
    def _scroll(self, title: str, sub: str):
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(28, 22, 28, 12)
        lay.setSpacing(4)
        h = QLabel(title)
        h.setFont(theme.font(20, theme.QFont.Weight.Bold))
        lay.addWidget(h)
        if sub:
            s = QLabel(sub)
            s.setObjectName("Muted")
            s.setWordWrap(True)
            lay.addWidget(s)
        lay.addSpacing(10)
        sc.setWidget(inner)
        return sc, lay

    def _add(self, lay, key, **kw):
        f = Field(key, self.st.get(key, SP[key]["default"]), **kw)
        self.fields[key] = f
        lay.addWidget(f)
        return f

    def _section(self, lay, text):
        lab = QLabel(text)
        lab.setFont(theme.caps(10.5))
        lab.setStyleSheet(f"color: {theme.t()['text3']}; padding-top: 14px;")
        lay.addWidget(lab)

    def _params(self) -> dict:
        """The mode parameters as they stand in this dialog (unsaved edits
        included), so the rate list answers for what would be saved."""
        out = {}
        for k in ("amVideoBw", "amVsbLower", "amSoundOffset", "fmDeviation", "fmVideoBw", "fmSub", "fmSubIndex"):
            f = self.fields.get(k)
            out[k] = f.get() if f is not None else self.st[k]
        return out

    def _cable_box(self) -> QWidget:
        """The cable feed (DESIGN.md "Cable feed"): its own red box, the
        confirmation first and the switch only once it is ticked. Both
        start unticked every time the program starts (they are never
        saved), so this box is where it is turned on, each session."""
        box = QFrame()
        box.setObjectName("Cable")
        t = theme.t()
        red = QColor(t["tx"])
        box.setStyleSheet(f"#Cable {{ background: rgba({red.red()}, {red.green()}, {red.blue()}, 22); "
                          f"border: 1px solid {t['tx']}; border-radius: 10px; }}")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(16, 14, 16, 14)
        bl.setSpacing(6)
        head = QLabel(self.tr("CABLE FEED — INTO A TELEVISION'S AERIAL SOCKET, NOT FOR ANTENNAS"))
        head.setFont(theme.caps(10.5))
        head.setStyleSheet(f"color: {t['tx']}; background: transparent; border: none;")
        bl.addWidget(head)
        ack = Check(self.tr("The output goes by cable, through attenuation, into a receiver's aerial "
                            "socket - never to an antenna."), bool(self.st.get("cableFeedAck")), bold=True)
        on = Check(self.tr("Cable feed on: 405 lines on the Band I channels B1-B5, or 625-line PAL on the UHF "
                           "channels E21-E48, at the radio's least output, and nothing else transmitted while it is "
                           "on."),
                   bool(self.st.get("cableFeed")))
        on.setEnabled(ack.isChecked())

        def acked(v):
            on.setEnabled(v)
            if not v:
                on.setChecked(False)
        ack.toggled.connect(acked)
        self.extra["cableFeedAck"] = ack
        self.extra["cableFeed"] = on
        bl.addWidget(ack)
        bl.addWidget(on)
        h = QLabel(self.tr("Neither Band I nor UHF is an amateur allocation. With this on the radio goes to Band I "
                           "(B1 45.00 MHz vision; B2-B5 on a Pluto with Tezuka firmware, all five on a HackRF) and AM"
                           " 405 lines, for a vintage 405-line set; choose UHF in the band list for 625-line PAL with"
                           " teletext into a 625-line set (E36 591.25 MHz first, the old VCR channel; E21-E48 except "
                           "E38). The power control is replaced by the radio's least output, and every transmission "
                           "is logged as a cable feed. Off again whenever the program starts."))
        h.setObjectName("Muted")
        h.setWordWrap(True)
        h.setStyleSheet("background: transparent; border: none;")
        bl.addWidget(h)
        return box

    def _licence_box(self, lay) -> None:
        """Where the station is and whose licence it transmits under - the
        station profile's location and licence, which decide what the
        transmit guard allows (ohsatv/txguard.py, the shared dataset). The
        classes and the yes/no questions are the licence in force's, so
        they are rebuilt as the callsign, country or licence change here,
        before anything is saved."""
        f = Field("stationCountry", self.st.get("stationCountry", ""), label=self.tr("Country (DXCC prefix)"),
                  help_=self.tr("G, GM, K, DL... Empty: worked out from your callsign."))
        self.fields["stationCountry"] = f
        lay.addWidget(f)
        cap = QLabel(self.tr("Licence"))
        cap.setObjectName("Muted")
        lay.addWidget(cap)
        self.licence_combo = QComboBox()
        self.licence_combo.addItem(self.tr("Automatic: the country's licence"), "auto")
        for rid, short in txguard.GUARD.regimes():
            self.licence_combo.addItem(short, rid)
        i = self.licence_combo.findData(self.st.get("licence") or "auto")
        self.licence_combo.setCurrentIndex(max(0, i))
        lay.addWidget(self.licence_combo)
        cap = QLabel(self.tr("Licence class"))
        cap.setObjectName("Muted")
        lay.addWidget(cap)
        self.class_combo = QComboBox()
        lay.addWidget(self.class_combo)
        self.cond_box = QVBoxLayout()
        self.cond_box.setContentsMargins(0, 4, 0, 4)
        lay.addLayout(self.cond_box)
        self.cond_toggles: dict[str, tuple[Toggle, bool]] = {}
        self.licence_state = QLabel("")
        self.licence_state.setWordWrap(True)
        lay.addWidget(self.licence_state)
        self._licence_refresh()
        self.licence_combo.currentIndexChanged.connect(lambda _i: self._licence_refresh())
        self.class_combo.currentIndexChanged.connect(lambda _i: self._licence_said())
        f.w.textChanged.connect(lambda _t: self._licence_refresh())
        call = self.fields.get("callsign")
        if call is not None:
            call.w.textChanged.connect(lambda _t: self._licence_refresh())

    def _licence_profile(self, with_class: bool = True) -> dict:
        """The station profile as this dialog stands, unsaved edits included."""
        st = dict(self.st)
        for k in ("callsign", "stationCountry"):
            if k in self.fields:
                st[k] = self.fields[k].get()
        st["licence"] = self.licence_combo.currentData() or "auto"
        st["licenceClass"] = (self.class_combo.currentData() or "") if with_class else ""
        st["stationConditions"] = self._conditions()
        return txguard.profile(st)

    def _conditions(self) -> dict:
        out = dict(self.st.get("stationConditions") or {})
        for k, (t, shown) in getattr(self, "cond_toggles", {}).items():
            if t.on != shown or k in out:
                out[k] = bool(t.on)
        return out

    def _licence_refresh(self) -> None:
        prof = self._licence_profile(with_class=False)
        keep = self.class_combo.currentData() if self.class_combo.count() else self.st.get("licenceClass", "")
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        self.class_combo.addItem(self.tr("Choose your class"), "")
        for cid, name in txguard.GUARD.classes(prof):
            self.class_combo.addItem(name, cid)
        self.class_combo.setCurrentIndex(max(0, self.class_combo.findData(keep or "")))
        self.class_combo.blockSignals(False)
        answers = self._conditions()
        while self.cond_box.count():
            w = self.cond_box.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.cond_toggles = {}
        for c in txguard.GUARD.conditions(dict(prof, conditions=answers)):
            t = Toggle(c["label"], c["value"])
            t.toggled.connect(lambda _v: self._licence_said())
            self.cond_box.addWidget(t)
            self.cond_toggles[c["key"]] = (t, c["value"])
        self._licence_said()

    def _licence_said(self) -> None:
        d = txguard.GUARD.in_force(self._licence_profile())
        t = theme.t()
        if d["canTransmit"]:
            text = self.tr("{licence}, {cls}: the transmit guard applies this licence's allocations and "
                           "conditions (band-plan data {version}).").format(
                licence=d["regimeShort"], cls=d["className"], version=d["bandplanDataVersion"])
            colour = t["text2"]
        else:
            text = d["why"]
            colour = t["err"]
        self.licence_state.setText(text)
        self.licence_state.setStyleSheet(f"color: {colour};")

    def _page(self, name: str) -> QWidget:
        if name == "Controller":
            sc, lay = self._scroll(self.tr("Controller"), self.tr(
                "An OpenHamShack controller on your network shares one station profile with every app paired to "
                "it. This app works the same without one: unpairing keeps everything on this computer."))
            # The store opens here, not with the radio, for an engine that
            # was not linked at start (an override run); a store that will
            # not open leaves the page saying so (link None).
            try:
                link = self.bridge.radio.station_link
            except Exception:  # noqa: BLE001  the page explains; nothing else depends on it
                link = None
            lay.addWidget(controller_ui.ControllerPage(link, self))
            lay.addStretch(1)
            return sc
        if name == "Station":
            sc, lay = self._scroll(self.tr("Station"),
                                   self.tr("Who you are. The ident is drawn into every transmitted picture and "
                                           "cannot be turned off (licence condition 6(21))."))
            row = QHBoxLayout()
            row.setSpacing(16)
            for k in ("callsign", "locator"):
                f = Field(k, self.st.get(k, ""))
                self.fields[k] = f
                row.addWidget(f)
            lay.addLayout(row)
            self._add(lay, "identText")
            self._add(lay, "identPosition")
            self._section(lay, self.tr("Licence"))
            self._licence_box(lay)
            lic = Check(self.tr("I hold an amateur licence, I will identify, and I accept that I am responsible "
                                "for what is transmitted."), bool(self.st.get("licenceAck")), bold=True)
            self.extra["licenceAck"] = lic
            lay.addWidget(lic)
            h = QLabel(SP["licenceAck"]["help"])
            h.setObjectName("Faint")
            h.setWordWrap(True)
            lay.addWidget(h)
            lay.addStretch(1)
            return sc
        if name == "Radio":
            sc, lay = self._scroll(self.tr("Radio"),
                                   self.tr("Which SDR, how it is reached, and the sample rate for each system. "
                                           "Changes here restart the radio; it comes back receiving, never "
                                           "transmitting."))
            self._add(lay, "device")
            f = self._add(lay, "plutoUri")
            find = Button(self.tr("Find"), icon="antenna", small=True)
            find.clicked.connect(self.find_pluto)
            self.find_btn = find
            self.found = QComboBox()
            self.found.hide()
            self.found.activated.connect(lambda _i: self._choose_found())
            r = QHBoxLayout()
            r.addWidget(find)
            r.addWidget(self.found, 1)
            r.addStretch(1)
            lay.addLayout(r)
            self.find_msg = QLabel("")
            self.find_msg.setObjectName("Faint")
            self.find_msg.setWordWrap(True)
            self.find_msg.hide()
            lay.addWidget(self.find_msg)
            self._add(lay, "plutoRx8")
            self._add(lay, "plutoTx8")
            # The radio lock: other OpenHamShack programs on this computer and
            # others see the radio as in use while this one has it open.
            self._section(lay, self.tr("Shared radios"))
            self._add(lay, "radioLockSsh")
            self._add(lay, "radioSshUser")
            self._add(lay, "radioSshPassword")
            self._section(lay, self.tr("Recordings"))
            self._add(lay, "playFile")
            self._add(lay, "playLoop")
            self._add(lay, "recSeconds")
            self._add(lay, "recMaxMB")
            self._add(lay, "recDir")
            rng = self.st.get("range") or []
            if rng:
                n = QLabel(self.tr("This radio tunes {low}–{high} MHz.").format(low=number(rng[0] / MHZ, 0),
                                                                               high=number(rng[1] / MHZ, 0)))
                n.setObjectName("Muted")
                lay.addWidget(n)
            # A HackRF beside the receiver: 8-bit IQ at up to 20 MS/s over
            # USB 2, so full-width FM-ATV from a USB device. Receive stays on
            # the radio above and carries on while keyed.
            self._section(lay, self.tr("Transmitter"))
            self._add(lay, "txDevice")
            self._add(lay, "hackrfSerial")
            self._add(lay, "hackrfAmp")
            hn = QLabel(self.tr("The HackRF tunes 1 MHz–6 GHz at 2–20 MS/s. Its output falls with frequency and is "
                                "low at 5.8 GHz: an external PA is needed there. Underruns (the computer not keeping "
                                "up) are shown on the main window while transmitting."))
            hn.setObjectName("Faint")
            hn.setWordWrap(True)
            lay.addWidget(hn)
            tw = self.st.get("txBandWhy") or ""
            if self.st.get("txKind") == "hackrf" and tw:
                lay.addWidget(QLabel(tw))
            self._add(lay, "autoProfile")
            link = self.st.get("linkMsps")
            for system, key, fn in (("am", "amRate", modem.am_plan), ("fm", "fmRate", modem.fm_plan)):
                self._section(lay, LABELS[key])
                h = QLabel(SP[key]["help"])
                h.setObjectName("Faint")
                h.setWordWrap(True)
                lay.addWidget(h)
                # Bound now, not looked up when called: a closure over the loop
                # variable would make both lists ask fm_plan.
                if system == "am":
                    def plan(fs, fn=fn):
                        p = self._params()
                        return fn(fs, p["amVideoBw"], p["amVsbLower"], p["amSoundOffset"])
                else:
                    def plan(fs, fn=fn):
                        p = self._params()
                        return fn(fs, p["fmDeviation"], p["fmVideoBw"], p["fmSub"], p["fmSubIndex"])
                rl = RateList(system, self.st.get(key), plan, link)
                self.extra[key] = rl
                lay.addWidget(rl)
                if system == "am":
                    self._add(lay, "a405Rate")
            self._section(lay, self.tr("Receiver"))
            self._add(lay, "rxGainMode")
            self._add(lay, "rxGain")
            lay.addStretch(1)
            return sc
        if name == "Transmit":
            sc, lay = self._scroll(self.tr("Transmit"), self.tr("Limits that only this dialog can change."))
            box = QFrame()
            box.setObjectName("Danger")
            t = theme.t()
            red = QColor(t["tx"])
            box.setStyleSheet(f"#Danger {{ background: rgba({red.red()}, {red.green()}, {red.blue()}, 22); "
                              f"border: 1px solid {t['tx']}; border-radius: 10px; }}")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(16, 14, 16, 14)
            bl.setSpacing(6)
            head = QLabel(self.tr("REAL-HARDWARE TRANSMIT"))
            head.setFont(theme.caps(10.5))
            head.setStyleSheet(f"color: {t['tx']}; background: transparent; border: none;")
            bl.addWidget(head)
            hw = Check(self.tr("Let a real radio transmit. I have checked the band, the power and the"
                               " antenna, and this is my licence."), bool(self.st.get("allowHardwareTx")), bold=True)
            self.extra["allowHardwareTx"] = hw
            bl.addWidget(hw)
            h = QLabel(self.tr("{help} The simulator never needs this: it sends nothing.").format(
                help=SP["allowHardwareTx"]["help"]))
            h.setObjectName("Muted")
            h.setWordWrap(True)
            h.setStyleSheet("background: transparent; border: none;")
            bl.addWidget(h)
            lay.addWidget(box)
            lay.addSpacing(10)
            lay.addWidget(self._cable_box())
            lay.addSpacing(10)
            self._add(lay, "txGainCeiling", help_=self.tr(
                "{help} The power slider on the main window cannot go past it (0 dB is a few mW from the SDR).").format(
                help=SP["txGainCeiling"]["help"]))
            self._section(lay, self.tr("Timers"))
            self._add(lay, "txTimeoutMin")
            self._add(lay, "watchdogSec")
            self._section(lay, self.tr("Teletext"))
            note = QLabel(self.tr("Teletext is switched on, and its folder of pages chosen, on the transmit panel. It"
                                  " rides on the picture's blanked lines at 6.9 Mbit/s and needs about 3.75 MHz of "
                                  "video: AM-VSB from 8 MS/s, FM-ATV at 12.5 MS/s and up with its video at 5 MHz. "
                                  "Narrower modes (a USB Pluto's 5 MS/s) and the narrow formats do not carry it, and "
                                  "say so."))
            note.setObjectName("Muted")
            note.setWordWrap(True)
            lay.addWidget(note)
            self._add(lay, "ttxLines")
            lay.addStretch(1)
            return sc
        if name == "AM-VSB":
            sc, lay = self._scroll(self.tr("AM-VSB"), self.tr("PAL, negative-modulation AM with a vestigial lower "
                                                              "sideband (UK System I-style). Both ends must agree."))
            for k in ("amVideoBw", "amVsbLower", "amSoundOffset", "amSoundDb"):
                self._add(lay, k)
            self._section(lay, self.tr("405 lines"))
            note = QLabel(self.tr("Transmit › Format › 405 lines sends the British System A of 1936-1985 instead: 405"
                                  " lines, positive modulation, 3 MHz of mono picture with its vestige above the "
                                  "carrier and AM sound 3.5 MHz below it - 4.3 MHz in all, which fits a USB Pluto's 5"
                                  " MS/s whole. Its numbers are the standard's (CCIR Report 624-2), so the settings "
                                  "above do not apply to it. A vintage 405-line set receives it; the receiver "
                                  "recognises 625 or 405 lines by itself (Settings › FM-ATV › Recognise the picture "
                                  "format)."))
            note.setObjectName("Muted")
            note.setWordWrap(True)
            lay.addWidget(note)
            lay.addStretch(1)
            return sc
        if name == "FM-ATV":
            sc, lay = self._scroll(self.tr("FM-ATV"),
                                   self.tr("PAL composite frequency-modulating the carrier (IARU R1 V.2). Both ends "
                                           "must agree."))
            for k in ("fmDeviation", "fmVideoBw", "fmPreemph", "fmSub", "fmSubIndex", "fmInvert"):
                self._add(lay, k)
            self._section(lay, self.tr("Picture format"))
            note = QLabel(self.tr("PAL is the standard. A USB Pluto transmits only ~5 MS/s, too narrow for PAL's "
                                  "colour and sound, so two narrow formats are offered under Transmit › Format: "
                                  "Mini-MAC (colour and digital sound; holds lock down to about 4 dB CNR) and narrow "
                                  "colour PAL (1.6 MHz colour, 2.2 MHz FM sound). Both are OpenHamShack ATV only: not"
                                  " compatible with standard ATV receivers. Mini-MAC ignores the pre-emphasis switch "
                                  "(it is always flat)."))
            note.setObjectName("Muted")
            note.setWordWrap(True)
            lay.addWidget(note)
            self._add(lay, "formatAuto")
            lay.addStretch(1)
            return sc
        if name == "Simulator":
            sc, lay = self._scroll(self.tr("Simulator"),
                                   self.tr("The simulated radio used when Radio is 'Simulator': no hardware, nothing "
                                           "transmitted, every mode can be tried."))
            for k in ("simCnrDb", "simStations", "simTxErrHz", "simNicam"):
                self._add(lay, k)
            lay.addStretch(1)
            return sc
        if name == "Sources":
            sc, lay = self._scroll(self.tr("Sources"),
                                   self.tr("The camera read for the Webcam picture source (see the transmit panel on "
                                           "the main window). Picked by device path, which a camera usually keeps on "
                                           "the same USB port."))
            cap = QLabel(LABELS["webcam"])
            cap.setObjectName("Muted")
            lay.addWidget(cap)
            refresh = Button(self.tr("Refresh"), icon="webcam", small=True)
            refresh.clicked.connect(self.list_webcams)
            self.webcam_refresh = refresh
            self.webcam_combo = QComboBox()
            cur = self.st.get("webcam", SP["webcam"]["default"])
            self.webcam_combo.addItem(cur, cur)
            r = QHBoxLayout()
            r.addWidget(refresh)
            r.addWidget(self.webcam_combo, 1)
            lay.addLayout(r)
            self.webcam_note = QLabel("")
            self.webcam_note.setObjectName("Faint")
            self.webcam_note.setWordWrap(True)
            self.webcam_note.hide()
            lay.addWidget(self.webcam_note)
            h = QLabel(SP["webcam"]["help"])
            h.setObjectName("Faint")
            h.setWordWrap(True)
            lay.addWidget(h)
            self.webcam_listed = False
            lay.addStretch(1)
            return sc
        if name == "Audio":
            sc, lay = self._scroll(self.tr("Audio"),
                                   self.tr("Sound devices are remembered by name, so a USB headset is found on any "
                                           "port."))
            self._add(lay, "audioBackend")
            # The device list is filled when the page is first shown: asking
            # PortAudio opens every ALSA and JACK backend, which takes a
            # moment and prints to the terminal, for a page most visits skip.
            self.audio_combos = {}
            self.audio_note = QLabel("")
            self.audio_note.setStyleSheet(f"color: {theme.t()['warn']};")
            self.audio_note.hide()
            for key in ("audioOut", "audioIn"):
                cap = QLabel(LABELS[key])
                cap.setObjectName("Muted")
                lay.addWidget(cap)
                cb = QComboBox()
                cur = self.st.get(key, "")
                cb.addItem(self.tr("System default"), "")
                if cur:
                    cb.addItem(cur, cur)
                    cb.setCurrentIndex(1)
                lay.addWidget(cb)
                self.audio_combos[key] = cb
                lay.addSpacing(8)
            lay.addWidget(self.audio_note)
            self.audio_listed = False
            self._add(lay, "toneHz")
            lay.addStretch(1)
            return sc
        sc, lay = self._scroll(self.tr("Display"), self.tr("How the window looks. These belong to this window, not "
                                                           "the radio."))
        cap = QLabel(self.tr("Theme"))
        cap.setObjectName("Muted")
        lay.addWidget(cap)
        th = Segmented([("dark", self.tr("Dark instrument")), ("light", self.tr("Light"))], self.prefs["theme"])
        self.extra["theme"] = th
        lay.addWidget(th)
        lay.addSpacing(10)
        cap = QLabel(self.tr("Waterfall palette"))
        cap.setObjectName("Muted")
        lay.addWidget(cap)
        row = QHBoxLayout()
        self.swatches = []
        for n in palettes.NAMES:
            s = Swatch(n, n == self.prefs["palette"])
            s.picked.connect(self._pick_palette)
            self.swatches.append(s)
            row.addWidget(s)
        row.addStretch(1)
        lay.addLayout(row)
        auto = Toggle(self.tr("Automatic waterfall floor and contrast"), self.prefs["wfAuto"])
        self.extra["wfAuto"] = auto
        lay.addSpacing(10)
        lay.addWidget(auto)
        lay.addStretch(1)
        return sc

    # ------------------------------------------------------------ actions
    def _page_shown(self, i: int):
        name = self.PAGES[i][1]
        if name == "Audio" and not self.audio_listed:
            self.audio_listed = True
            self.list_audio()
        elif name == "Sources" and not self.webcam_listed:
            self.webcam_listed = True
            self.list_webcams()

    def list_audio(self):
        try:
            from ..audio import list_devices
            devs = list_devices()
        except Exception:  # noqa: BLE001 - no sound system is a sentence, not a crash
            devs = []
        for key, inputs in (("audioOut", False), ("audioIn", True)):
            cb = self.audio_combos[key]
            cur = cb.currentData() or ""
            cb.clear()
            cb.addItem(self.tr("System default"), "")
            for d in devs:
                if (d["inputs"] if inputs else d["outputs"]) > 0:
                    cb.addItem(d["name"], d["name"])
            i = cb.findData(cur)
            if i < 0 and cur:
                cb.addItem(self.tr("{device} (not connected)").format(device=cur), cur)
                i = cb.count() - 1
            cb.setCurrentIndex(max(0, i))
        self.audio_note.setText("" if devs else self.tr("No sound devices found (is python3-pyaudio installed?)."))
        self.audio_note.setVisible(not devs)

    def list_webcams(self):
        try:
            from ..sources import list_webcams
            devs = list_webcams()
        except Exception:  # noqa: BLE001 - a broken v4l2 stack is a sentence, not a crash
            devs = []
        cb = self.webcam_combo
        cur = cb.currentData() or self.st.get("webcam", "")
        cb.clear()
        if host.windows():
            # "" is the default there: whichever camera ffmpeg lists first.
            cb.addItem(self.tr("First camera found"), "")
        for d in devs:
            # A DirectShow camera's path is usually its name; said once.
            label = d["name"] if d["path"] == d["name"] else f"{d['name']}  ({d['path']})"
            cb.addItem(label, d["path"])
        i = cb.findData(cur)
        if i < 0 and cur:
            cb.addItem(self.tr("{device} (not connected)").format(device=cur), cur)
            i = cb.count() - 1
        cb.setCurrentIndex(max(0, i))
        self.webcam_note.setText("" if devs else host.hint("webcam-none"))
        self.webcam_note.setVisible(not devs)

    def _pick_palette(self, name):
        self.palette = name
        for s in self.swatches:
            s.on = s.name == name
            s.update()

    def find_pluto(self):
        self.find_btn.setEnabled(False)
        self.find_btn.set_text(self.tr("Searching…"))
        self.find_msg.hide()
        self.finder.run()

    def _found(self, results: list, notes: list):
        self.find_btn.setEnabled(True)
        self.find_btn.set_text(self.tr("Find"))
        self.found.clear()
        for uri, desc in results:
            self.found.addItem(f"{uri}  —  {desc}", uri)
        if results:
            self.found.show()
            self._choose_found()
            self.find_msg.hide()
        else:
            self.found.hide()
            msg = ("No Pluto found. If one is connected by USB, ip:192.168.2.1 above usually works without any "
                   "USB permissions.")
            self.find_msg.setText((" ".join(notes) + " " + msg).strip())
            self.find_msg.show()

    def _choose_found(self):
        """A radio picked from Find's list becomes the radio: its address,
        and the driver that speaks to it. Filling in only the address left
        Radio on the simulator, so Find then Save changed nothing anyone
        could see - the address waited, unused, for a second change on the
        same page that nobody knew to make. Only the fields change here;
        Save still applies them through set(), which restarts the radio
        receiving and never keys."""
        uri = self.found.currentData()
        if not uri:
            return
        self.fields["plutoUri"].w.setText(uri)
        self.fields["device"].w.set_value(FOUND_DRIVER)

    def _say(self, text):
        self.error.setText(text)
        self.error.setVisible(bool(text))

    # ------------------------------------------------------------ save
    def collect(self) -> dict:
        changes = {}
        for k, f in self.fields.items():
            v = f.get()
            if v != self.st.get(k):
                changes[k] = v
        for k in ("licenceAck", "allowHardwareTx", "cableFeedAck", "cableFeed"):
            if k not in self.extra:
                continue
            v = bool(self.extra[k].isChecked())
            if v != bool(self.st.get(k)):
                changes[k] = v
        if hasattr(self, "licence_combo"):
            for k, v in (("licence", self.licence_combo.currentData() or "auto"),
                         ("licenceClass", self.class_combo.currentData() or ""),
                         ("stationConditions", self._conditions())):
                if v != (self.st.get(k) or ("auto" if k == "licence" else {} if k == "stationConditions" else "")):
                    changes[k] = v
        for k in ("amRate", "fmRate"):
            v = self.extra[k].value
            if v != self.st.get(k):
                changes[k] = v
        for k, cb in getattr(self, "audio_combos", {}).items():
            v = cb.currentData() or ""
            if v != self.st.get(k, ""):
                changes[k] = v
        webcam_combo = getattr(self, "webcam_combo", None)
        if webcam_combo is not None:
            v = webcam_combo.currentData() or ""
            if v != self.st.get("webcam", ""):
                changes["webcam"] = v
        return changes

    def save(self) -> bool:
        try:
            changes = self.collect()
            if changes:
                self.bridge.radio.set(authorise=True, **changes)
        except ValueError as e:
            self._say(str(e))
            return False
        p = self.prefs
        old_theme = p["theme"]
        p["theme"] = self.extra["theme"].value
        p["palette"] = self.palette
        p["wfAuto"] = self.extra["wfAuto"].on
        p.save()
        self.bridge.st.update(self.bridge.radio.state())
        self.bridge.changed.emit(dict(changes))
        self.accept()
        parent = self.parent()
        if parent is not None and hasattr(parent, "apply_prefs"):
            parent.apply_prefs(theme_changed=p["theme"] != old_theme)
        return True

    def sizeHint(self):
        return QSize(1000, 740)
