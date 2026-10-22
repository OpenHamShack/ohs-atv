"""The main window: builds the layout, wires the bridge to the widgets, and
owns the behaviour that spans widgets - tuning, PTT, the shortcuts.

Layout (DESIGN.md "User interface"):

  top bar   mark | frequency readout | band / system | lock badges | device | tools
  body      picture (the hero)  | waveform monitor        | transmit
                                | vectorscope | the plan |  (source, ident,
            spectrum + waterfall + bandplan  | the band   |   power, PTT)
  footer    status strip

The picture and the scope column are placed by hand (HeroRow, ScopeColumn)
rather than by a stretch factor: the monitor must stay exactly 4:3 at every
window size, and the vectorscope square, and Qt's box layouts cannot say
either. Everything else is ordinary layouts.

Theme changes rebuild the window's content (each widget picks up colours
when built, as in OpenHamShack NB); the radio and the feeds keep running.
"""
from __future__ import annotations

from PyQt6.QtCore import QByteArray, QEvent, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFontMetricsF, QKeySequence, QPainter, QPen, QShortcut
from PyQt6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QMainWindow, QPlainTextEdit, QScrollArea, QStackedWidget, QTextEdit, QVBoxLayout,
                             QWidget)

from .. import bands
from . import appicon, theme
from .chrome import IconButton, ShortcutOverlay, StatusStrip
from .controls import Anim, Button, Card, Segmented
from .instruments import LineSelector, Vectorscope, WaveformMonitor
from .panels import BandCard, DeviceChip, LockBadges, PlanCard, ToastHost, TransmitPanel
from .picture import Monitor
from .readout import STEPS, Readout
from .spectrum import SpectrumView
from .teletext import TeletextView

TEXT_INPUTS = (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QComboBox)
SYSTEMS = [("am", "AM-VSB"), ("fm", "FM-ATV")]
GAP = 12


class Mark(QWidget):
    """The app mark: the icon and OPENHAMSHACK ATV, with the station under it."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.compact = False
        self.setFixedHeight(56)
        bridge.changed.connect(lambda ch: self.update() if "callsign" in ch or "locator" in ch else None)

    # "OPENHAMSHACK ATV" is half as long again as the "OPENHAMSHACK ATV" this
    # was drawn for, so the size comes from the title itself, a point smaller
    # and tighter than before - at the old 15 px and 2.2 spacing it wanted
    # 270 px of a top bar that at 1366 wide has none to give.
    TITLE = "OPENHAMSHACK ATV"

    @staticmethod
    def title_font():
        return theme.font(13, theme.QFont.Weight.Black, spacing=1.2)

    def sizeHint(self):
        from PyQt6.QtCore import QSize
        from PyQt6.QtGui import QFontMetricsF
        return QSize(54 + int(QFontMetricsF(self.title_font()).horizontalAdvance(self.TITLE)) + 4, 56)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        appicon.paint_mark(p, QRectF(0, 6, 44, 44))
        p.setFont(self.title_font())
        p.setPen(theme.c("text"))
        p.drawText(QRectF(54, 8, self.width() - 54, 22), Qt.AlignmentFlag.AlignVCenter, self.TITLE)
        st = self.bridge.st
        call = (st.get("callsign") or "").upper()
        sub = " · ".join(x for x in (call, (st.get("locator") or "").upper()) if x) or "analogue amateur TV"
        p.setFont(theme.font(11.5, theme.QFont.Weight.DemiBold, mono=bool(call)))
        p.setPen(theme.c("text3"))
        p.drawText(QRectF(54, 30, self.width() - 54, 18), Qt.AlignmentFlag.AlignVCenter, sub)


class WhereLabel(QWidget):
    """One line in the spectrum card's header: where the receive carrier is
    in the bandplan - the segment, in its colour, and on a channel what the
    channel is for. (It was a block in the top bar; at 1920 wide the bar had
    170 px left for it and it was all ellipsis.)"""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setFixedHeight(20)
        bridge.changed.connect(lambda ch: self.update() if "rxFreq" in ch or "band" in ch else None)

    def sizeHint(self):
        from PyQt6.QtCore import QSize
        return QSize(760, 20)

    def text(self) -> tuple[str, str]:
        f = float(self.bridge.st.get("rxFreq") or 0)
        seg = bands.segment_at(f)
        b = bands.band_at(f)
        if b is None and self.bridge.st.get("band") in bands.CABLE_NAMES:
            b = bands.BY_NAME[self.bridge.st["band"]]
        ch = next((c for c in (b.channels if b else ()) if abs(c.freq - f) < 1.0), None)
        where = (seg.label if seg is not None else
                 f"{b.name}: cable feed only, never an antenna" if b in bands.CABLE_BANDS
                 else "outside the bandplan's segments")
        if ch is not None:
            where += f"  ·  {ch.freq / 1e6:g} MHz {ch.label}"
        return where, (seg.kind if seg is not None else "")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        where, kind = self.text()
        hint = "  ·  click a channel to tune"
        p.setFont(theme.font(12))
        fm = QFontMetricsF(p.font())
        w = self.width()
        hw = fm.horizontalAdvance(hint)
        room = w - 14 - hw
        if room < 120:
            hint, hw, room = "", 0.0, w - 14
        text = fm.elidedText(where, Qt.TextElideMode.ElideRight, room)
        tw = fm.horizontalAdvance(text)
        x = w - hw - tw
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c(theme.segment_colour_key(kind)) if kind else theme.c("text3"))
        p.drawEllipse(QRectF(x - 14, 6, 8, 8))
        p.setPen(theme.c("text2"))
        p.drawText(QRectF(x, 0, tw + 2, 20), Qt.AlignmentFlag.AlignVCenter, text)
        p.setPen(theme.c("text3"))
        p.drawText(QRectF(w - hw, 0, hw + 2, 20), Qt.AlignmentFlag.AlignVCenter, hint)


class OnAirFrame(QWidget):
    """A red frame round the whole window while keyed (NB's on-air glow).
    Transparent to the mouse."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.k = Anim(self, ms=220)
        self.hide()

    def set_on(self, on: bool):
        if on:
            self.show()
            self.raise_()
        self.k.to(1.0 if on else 0.0)
        if not on and self.k.value < 0.01:
            self.hide()

    def paintEvent(self, _e):
        k = self.k.value
        if k <= 0.001:
            return
        p = QPainter(self)
        base = QColor(theme.t()["txGlow"])
        w, h = self.width(), self.height()
        for i in range(8):
            c = QColor(base)
            c.setAlphaF(k * 0.5 * (1 - i / 8) ** 2)
            p.setPen(QPen(c, 2))
            p.drawRect(QRectF(1 + i * 2, 1 + i * 2, w - 2 - i * 4, h - 2 - i * 4))
        c = QColor(base)
        c.setAlphaF(k)
        p.setPen(QPen(c, 3))
        p.drawRect(QRectF(1.5, 1.5, w - 3, h - 3))


class HeroRow(QWidget):
    """The picture card at exactly 4:3 for the row's height (never more than
    62 % of the width), and the scope column filling the rest."""

    def __init__(self, picture: QWidget, scopes: QWidget, parent=None):
        super().__init__(parent)
        self.picture, self.scopes = picture, scopes
        picture.setParent(self)
        scopes.setParent(self)
        self.head = 40          # the picture card's title row
        self.pad = 12

    def resizeEvent(self, e):
        w, h = self.width(), self.height()
        mon_h = h - self.head - self.pad
        mon_w = (mon_h - 32) * 4 / 3 + 32
        card_w = int(min(w * 0.62, mon_w + 2 * self.pad))
        self.picture.setGeometry(0, 0, card_w, h)
        self.scopes.setGeometry(card_w + GAP, 0, max(10, w - card_w - GAP), h)
        super().resizeEvent(e)


class ScopeColumn(QWidget):
    """Waveform monitor on top; the vectorscope (square) and the plan below."""

    def __init__(self, wfm: QWidget, vec: QWidget, plan: QWidget, parent=None):
        super().__init__(parent)
        self.wfm, self.vec, self.plan = wfm, vec, plan
        for x in (wfm, vec, plan):
            x.setParent(self)

    def resizeEvent(self, e):
        w, h = self.width(), self.height()
        top = int((h - GAP) * 0.47)
        bottom = h - top - GAP
        self.wfm.setGeometry(0, 0, w, top)
        vec_w = int(min(bottom - 6, w * 0.5))
        self.vec.setGeometry(0, top + GAP, vec_w, bottom)
        self.plan.setGeometry(vec_w + GAP, top + GAP, max(10, w - vec_w - GAP), bottom)
        super().resizeEvent(e)


def scrolled(w: QWidget) -> QScrollArea:
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sc.setFrameShape(QFrame.Shape.NoFrame)
    sc.setWidget(w)
    return sc


class MainWindow(QMainWindow):
    def __init__(self, bridge, prefs, start_error: str = ""):
        super().__init__()
        self.bridge, self.prefs = bridge, prefs
        self.setWindowTitle(self.tr("OpenHamShack ATV"))
        self.setWindowIcon(appicon.app_icon())
        self.setMinimumSize(1280, 720)
        self.step = 100_000
        self.space_down = False
        self.built = False
        self.ttx_editor = None          # the Teletext editor's window, once opened
        self.build()
        bridge.changed.connect(self.on_state)
        bridge.toast.connect(self.on_toast)
        bridge.tick.connect(self.on_tick)
        QApplication.instance().installEventFilter(self)
        self._shortcuts()
        self.restore_state()
        if start_error:
            QTimer.singleShot(400, lambda: self.on_toast({"level": "error", "text": start_error}))
        self.on_state(dict(bridge.st))

    # ================================================================ building
    def build(self):
        old = self.centralWidget()
        old_spectrum = self.spectrum if self.built else None
        b = self.bridge
        root = QWidget()
        root.setObjectName("Root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- top bar
        top = QWidget()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(18, 14, 14, 10)
        tl.setSpacing(22)
        self.top_lay = tl
        self.mark = Mark(b)
        tl.addWidget(self.mark)
        self.readout = Readout(b)
        self.readout.set_step(self.step)
        self.readout.tune.connect(self.tune)
        self.readout.tune_tx.connect(lambda f: self.bridge.set(txFreq=f))
        self.readout.step_clicked.connect(lambda: self.cycle_step(1))
        self.readout.split_clicked.connect(lambda: self.bridge.set(split=not self.bridge.st.get("split")))
        tl.addWidget(self.readout)
        col = QVBoxLayout()
        col.setSpacing(8)
        self.band_sel = Segmented([(x.name, x.name) for x in bands.BANDS], b.st.get("band", "23cm"), compact=True)
        self.band_sel.picked.connect(self.pick_band)
        self.band_sel.setFixedWidth(220)
        self.sys_sel = Segmented(SYSTEMS, b.st.get("system", "fm"), compact=True)
        self.sys_sel.picked.connect(lambda v: self.bridge.set(system=v))
        self.sys_sel.setFixedWidth(220)
        col.addStretch(1)
        col.addWidget(self.band_sel)
        col.addWidget(self.sys_sel)
        col.addStretch(1)
        tl.addLayout(col)
        tl.addStretch(1)
        self.badges = LockBadges(b)
        tl.addWidget(self.badges)
        self.device = DeviceChip(b)
        self.device.clicked.connect(lambda: self.open_settings(1))
        tl.addWidget(self.device)
        tools = QHBoxLayout()
        tools.setSpacing(2)
        self.btn_help = IconButton("help", self.tr("Keyboard shortcuts (?)"))
        self.btn_help.clicked.connect(lambda: self.overlay.open())
        self.btn_theme = IconButton("theme", self.tr("Light / dark theme (Ctrl+T)"))
        self.btn_theme.clicked.connect(self.toggle_theme)
        self.btn_gear = IconButton("gear", self.tr("Settings (Ctrl+,)"))
        self.btn_gear.clicked.connect(self.open_settings)
        for x in (self.btn_help, self.btn_theme, self.btn_gear):
            tools.addWidget(x)
        tl.addLayout(tools)
        outer.addWidget(top)

        # ---- body
        body = QHBoxLayout()
        body.setContentsMargins(GAP, 2, GAP, GAP)
        body.setSpacing(GAP)
        main = QVBoxLayout()
        main.setSpacing(GAP)

        self.picture_card = Card(self.tr("Picture"), pad=12)
        self.view_sel = Segmented([("rx", self.tr("Received")), ("tx", self.tr("TX preview"))], b.st.get("view", "rx"),
                                  compact=True)
        self.view_sel.setFixedWidth(210)
        self.view_sel.picked.connect(lambda v: self.bridge.set(view=v))
        # Teletext takes the picture's place, as on a television: the Text
        # button (or T) swaps the monitor for the page viewer and back. It is
        # the window's choice, not the engine's - receive goes on the same.
        self.text_btn = Button(self.tr("Text"), icon="text", small=True, checkable=True)
        self.text_btn.setToolTip(self.tr("Teletext received with the picture (T)"))
        self.text_btn.clicked.connect(lambda: self.show_text(self.text_btn.checked))
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        # Subtitles from page 888 over the received picture (on by default).
        self.subs_btn = Button("888", small=True, checkable=True)
        self.subs_btn.setToolTip(self.tr("Subtitles (teletext page 888) over the received picture (S)"))
        self.subs_btn.clicked.connect(lambda: self.show_subtitles(self.subs_btn.checked))
        hl.addWidget(self.subs_btn)
        hl.addWidget(self.text_btn)
        hl.addWidget(self.view_sel)
        self.picture_card.set_header_widget(head)
        self.monitor = Monitor(b)
        self.subs_btn.set_checked(bool(self.prefs.get("subtitles", True)))
        self.monitor.set_subtitles(self.subs_btn.checked)
        self.ttx_view = TeletextView(b, self.prefs)
        self.picture_stack = QStackedWidget()
        self.picture_stack.addWidget(self.monitor)
        self.picture_stack.addWidget(self.ttx_view)
        self.picture_card.lay.setContentsMargins(6, 40, 6, 6)
        self.picture_card.lay.addWidget(self.picture_stack)
        if self.prefs.get("textView"):
            self.show_text(True)

        self.wfm_card = Card(self.tr("Waveform"), pad=10)
        self.line_sel = LineSelector(int(b.st.get("scopeLine", 100)))
        self.line_sel.picked.connect(lambda v: self.bridge.set(scopeLine=v))
        self.wfm_card.set_header_widget(self.line_sel)
        self.wfm = WaveformMonitor(b)
        self.wfm_card.lay.addWidget(self.wfm)
        self.vec_card = Card(self.tr("Vectorscope"), pad=8)
        self.vec = Vectorscope(b)
        self.vec_card.lay.addWidget(self.vec)
        self.plan_card = PlanCard(b)
        self.scopes = ScopeColumn(self.wfm_card, self.vec_card, self.plan_card)
        self.hero = HeroRow(self.picture_card, self.scopes)
        main.addWidget(self.hero, 63)

        lower = QHBoxLayout()
        lower.setSpacing(GAP)
        self.spec_card = Card(self.tr("Receive band"), pad=0)
        self.spec_card.lay.setContentsMargins(1, 34, 1, 1)
        self.where = WhereLabel(b)
        # Record IQ sits with the spectrum: it records what this panel shows,
        # the receiving radio's raw samples (ohsatv/iqrec.py).
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)
        hl.addWidget(self.where, 1)
        self.rec_btn = Button(self.tr("Record IQ"), icon="waves", small=True, checkable=True)
        self.rec_btn.setToolTip(self.tr("Record the receiving radio's raw IQ as SigMF, for playing back later "
                                        "(limits and folder: Settings › Radio)"))
        self.rec_btn.clicked.connect(self.toggle_record)
        hl.addWidget(self.rec_btn)
        self.spec_card.set_header_widget(head)
        self.spectrum = SpectrumView(b, self.prefs)
        if old_spectrum is not None:
            self.spectrum.adopt(old_spectrum)
        self.spectrum.tune.connect(self.tune)
        self.spectrum.wheel_step.connect(lambda d: self.step_freq(d))
        self.spec_card.lay.addWidget(self.spectrum)
        lower.addWidget(self.spec_card, 1)
        self.band_card = BandCard(b)
        self.band_card.tune.connect(self.tune)
        self.band_scroll = scrolled(self.band_card)
        lower.addWidget(self.band_scroll)
        main.addLayout(lower, 37)
        body.addLayout(main, 1)

        self.tx_panel = TransmitPanel(b, self.prefs)
        self.tx_panel.ptt_clicked.connect(self.ptt_toggle)
        self.tx_panel.edit_pages.connect(self.open_teletext_editor)
        self.tx_scroll = scrolled(self.tx_panel)
        body.addWidget(self.tx_scroll)
        outer.addLayout(body, 1)

        self.status = StatusStrip(b)
        outer.addWidget(self.status)
        self.setCentralWidget(root)
        if old is not None:
            old.deleteLater()
        self.toasts = ToastHost(root)
        self.overlay = ShortcutOverlay(root)
        self.onair = OnAirFrame(root)
        self.built = True
        self._responsive()
        self.on_state(dict(self.bridge.st))

    def _responsive(self):
        """Two sizes of the same layout: at 1366 x 768 the side columns
        narrow, the badges compact and the device chip moves to the status
        strip (which already says the same)."""
        w = self.width()
        compact = w < 1700
        self.badges.set_compact(compact)
        self.plan_card.set_compact(compact)
        self.band_card.set_compact(compact)
        self.tx_panel.set_compact(compact)
        self.top_lay.setSpacing(22 if not compact else 14)
        self.device.setVisible(not compact)
        self.btn_theme.setVisible(not compact)
        self.mark.compact = compact
        self.mark.updateGeometry()
        side = 400 if not compact else 330
        self.tx_scroll.setFixedWidth(side)
        self.band_scroll.setFixedWidth(380 if not compact else 300)
        self.band_sel.setFixedWidth(220 if not compact else 190)
        # The picture card's header: at 1366 the Text button is its icon alone.
        self.text_btn.set_text("" if compact else self.tr("Text"))
        self.text_btn.setFixedWidth(34 if compact else 74)
        self.subs_btn.setFixedWidth(44 if not compact else 40)
        self.view_sel.setFixedWidth(210 if not compact else 180)
        self.sys_sel.setFixedWidth(220 if not compact else 190)
        self.toasts.top = 100

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.built:
            self._responsive()
            self._picture_title()
            root = self.centralWidget()
            self.onair.setGeometry(root.rect())
            if self.overlay.isVisible():
                self.overlay.setGeometry(root.rect())
            self.toasts._place()

    # ================================================================ state
    def on_state(self, ch: dict):
        st = self.bridge.st
        if "cableFeed" in ch or "band" in ch:
            # Band I and UHF are the cable feed's, and shown only while it is on.
            opts = [(x.name, x.name) for x in bands.BANDS]
            if st.get("cableFeed"):
                opts += [(x.name, x.name) for x in bands.CABLE_BANDS]
            self.band_sel.set_options(opts, st.get("band"))
        if "band" in ch:
            self.band_sel.set_value(st["band"])
        if "band" in ch or "range" in ch or "bandOk" in ch:
            rng = st.get("range") or None
            why = {}
            for bd in bands.BANDS + bands.CABLE_BANDS:
                ok, sentence = bands.reachable(bd, tuple(rng) if rng else None)
                if not ok:
                    why[bd.name] = sentence
            if not st.get("bandOk", True) and st.get("bandWhy"):
                why[st["band"]] = st["bandWhy"]
            # The current band stays choosable even when greyed: it is where we are.
            self.band_sel.set_disabled({k: v for k, v in why.items() if k != st.get("band")})
            self.band_sel.setToolTip(why.get(st.get("band"), ""))
        if "system" in ch:
            self.sys_sel.set_value(st["system"])
        if "view" in ch:
            self.view_sel.set_value(st["view"])
        if "scopeLine" in ch:
            self.line_sel.set_value(int(st["scopeLine"]))
        if "keyed" in ch:
            self.onair.setGeometry(self.centralWidget().rect())
            self.onair.set_on(bool(st["keyed"]))
            if not st["keyed"]:
                self.space_down = False
        if any(k in ch for k in ("recording", "recWritten")):
            on = bool(st.get("recording"))
            self.rec_btn.set_checked(on)
            secs = f"{float(st.get('recWritten') or 0):.0f}"
            self.rec_btn.set_text(self.tr("Recording {seconds} s").format(seconds=secs) if on else self.tr("Record IQ"))
        if any(k in ch for k in ("fs", "system", "plan")):
            fs = float(st.get("fs") or 0)
            # A span, not a tuned frequency: it keeps Python's "." until M3
            # brings a number helper.
            self.spec_card.set_title(self.tr("Receive band · {span} MHz span").format(span=f"{fs / 1e6:g}") if fs
                                     else self.tr("Receive band"))
        if any(k in ch for k in ("rxFreq", "system", "rxLock", "keyed", "view", "ttxRx")):
            self._picture_title()

    def _picture_title(self):
        if self.picture_stack.currentWidget() is self.ttx_view:
            n = int((self.bridge.st.get("ttxRx") or {}).get("pages") or 0)
            # At 1366 the header's buttons leave room for the word alone.
            self.picture_card.set_title(self.tr("Teletext") if self.width() < 1700 else
                                        self.tr("Teletext · %n page(s) received", "", n))
            return
        mode = self.monitor.mode()
        self.picture_card.set_title(self.tr("Picture · transmit preview") if mode == "tx" else
                                    self.tr("Picture · received"))

    def show_subtitles(self, on: bool):
        self.subs_btn.set_checked(on)
        self.monitor.set_subtitles(on)
        self.prefs["subtitles"] = bool(on)

    def show_text(self, on: bool):
        self.text_btn.set_checked(on)
        self.picture_stack.setCurrentWidget(self.ttx_view if on else self.monitor)
        if on:
            self.ttx_view.refresh()
        self.prefs["textView"] = bool(on)
        self._picture_title()

    def on_toast(self, it: dict):
        self.toasts.show_toast(it.get("level", "info"), it.get("text", ""))

    def on_tick(self, dt):
        # The strip changes once a second (clock, fps, CPU); repainting it
        # at the pump's 30 Hz cost the receive threads GIL time for nothing.
        self._strip_t = getattr(self, "_strip_t", 0.0) + dt
        if self._strip_t >= 0.5:
            self._strip_t = 0.0
            self.status.update()
        if self.onair.isVisible():
            self.onair.update()
            if not self.bridge.st.get("keyed") and self.onair.k.value < 0.01:
                self.onair.hide()

    # ================================================================ actions
    def tune(self, f: float):
        """Tune receive to `f`, staying in the current band: a wheel turned
        past the band edge stops there rather than toasting every click. A
        frequency in another band (typed, or a click) moves to that band."""
        st = self.bridge.st
        b = bands.BY_NAME.get(st.get("band", "23cm"))
        other = bands.band_at(f)
        if other is not None and b is not None and other.name != b.name:
            self.bridge.set(band=other.name, rxFreq=float(f), txFreq=float(f))
            return
        if b is not None:
            f = max(b.lo, min(b.hi, f))
        self.bridge.set(rxFreq=float(f))

    def step_freq(self, n: int):
        self.tune(float(self.bridge.st.get("rxFreq") or 0) + n * self.step)

    def cycle_step(self, d: int):
        i = STEPS.index(self.step) if self.step in STEPS else 2
        self.step = STEPS[(i + d) % len(STEPS)]
        self.readout.set_step(self.step)
        self.spectrum.snap_step = max(250_000.0, float(self.step))

    def pick_band(self, name: str):
        if not self.bridge.set(band=name):
            self.band_sel.set_value(self.bridge.st.get("band"))

    def ptt_toggle(self):
        if self.bridge.keyed():
            self.bridge.ptt(False)
        else:
            self.bridge.ptt(True)

    def toggle_record(self):
        self.bridge.record_iq(not bool(self.bridge.radio.state().get("recording")))

    def snapshot(self):
        return self.monitor.snapshot()

    def toggle_theme(self):
        self.prefs["theme"] = "light" if self.prefs["theme"] == "dark" else "dark"
        self.prefs.save()
        self.apply_prefs(theme_changed=True)

    def apply_prefs(self, theme_changed: bool = False):
        if theme_changed:
            theme.set_theme(self.prefs["theme"])
            app = QApplication.instance()
            app.setStyleSheet(theme.stylesheet())
            self.build()
        else:
            self.spectrum.set_palette(self.prefs["palette"])

    def open_settings(self, page: int = 0):
        from .settings import SettingsDialog
        d = SettingsDialog(self.bridge, self.prefs, self, page=page if isinstance(page, int) else 0)
        d.exec()

    # ================================================================ keys
    def _shortcuts(self):
        for seq, fn in (("Ctrl+,", self.open_settings), ("Ctrl+T", self.toggle_theme),
                        ("F11", lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen()),
                        ("Ctrl+S", self.snapshot), ("Ctrl+Q", self.close)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(fn)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False
        if not self.isActiveWindow() or QApplication.activeModalWidget() is not None:
            return False
        focus = QApplication.focusWidget()
        typing = isinstance(focus, TEXT_INPUTS)
        k = ev.key()
        if k == Qt.Key.Key_Space and not typing:
            # Hold to talk: press keys, release unkeys. Auto-repeat is
            # ignored both ways so a held bar is one transmission.
            if ev.isAutoRepeat():
                return True
            if t == QEvent.Type.KeyPress:
                self.space_press()
            else:
                self.space_release()
            return True
        if t != QEvent.Type.KeyPress or typing:
            return False
        mods = ev.modifiers()
        if k == Qt.Key.Key_Escape:
            if self.overlay.isVisible():
                return False
            if self.bridge.keyed():
                self.bridge.ptt(False)
                return True
            return False
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right) and not (mods & Qt.KeyboardModifier.ControlModifier):
            n = 10 if mods & Qt.KeyboardModifier.ShiftModifier else 1
            self.step_freq(n if k == Qt.Key.Key_Right else -n)
            return True
        if k == Qt.Key.Key_Question:
            self.overlay.open()
            return True
        if k == Qt.Key.Key_T and not mods & Qt.KeyboardModifier.ControlModifier:
            self.show_text(not self.text_btn.checked)
            return True
        if k == Qt.Key.Key_S and not mods & Qt.KeyboardModifier.ControlModifier:
            self.show_subtitles(not self.subs_btn.checked)
            return True
        if (self.picture_stack.currentWidget() is self.ttx_view and not mods & Qt.KeyboardModifier.ControlModifier
                and self.ttx_view.key(k, ev.text())):
            return True
        if k == Qt.Key.Key_P and not mods & Qt.KeyboardModifier.ControlModifier:
            self.bridge.set(view="tx" if self.bridge.st.get("view") != "tx" else "rx")
            return True
        return False

    def space_press(self):
        if self.space_down:
            return
        self.space_down = True
        if not self.bridge.keyed():
            self.bridge.ptt(True)

    def space_release(self):
        if not self.space_down:
            return
        self.space_down = False
        self.bridge.ptt(False)

    # ================================================================ lifecycle
    def restore_state(self):
        g = self.prefs.get("geometry")
        if g:
            try:
                self.restoreGeometry(QByteArray.fromBase64(g.encode()))
            except (ValueError, TypeError):
                pass

    def open_teletext_editor(self):
        """The Teletext editor (ohstxt, OpenHamShack Teletext folded into
        ATV) as a window of this program, on the folder the transmit panel
        sends. With no folder chosen yet, the editor's own service folder
        (created with its sample pages) becomes the one sent - so what is
        edited is always what goes out, which is the point of having the
        editor here. The folder is handed over as a path and nothing else:
        the editor saves .tti files and the engine reads them (ohsatv/tti.py),
        the same contract the two programs had apart. Opening it again
        raises the window, moved to the current folder if that changed."""
        import os
        from pathlib import Path

        from PyQt6 import sip

        from ohstxt.ui import open_editor
        folder = (self.bridge.st.get("ttxDir") or "").strip()
        if not folder:
            from ohstxt.ui.mainwindow import default_service_dir
            folder = str(default_service_dir())
            self.bridge.set(ttxDir=folder)
        folder = Path(os.path.expanduser(folder))
        ed = self.ttx_editor
        if ed is not None and not sip.isdeleted(ed):
            if Path(ed.service.folder).resolve() != folder.resolve():
                ed.open_service(folder)
        else:
            ed = self.ttx_editor = open_editor(folder, self.prefs["theme"])
        ed.show()
        ed.raise_()
        ed.activateWindow()
        return ed

    def closeEvent(self, e):
        try:
            self.bridge.ptt(False)
        except Exception:  # noqa: BLE001 - closing must never fail
            pass
        # The editor goes with the transceiver, but its unsaved pages are
        # its to ask about: a "keep editing" there keeps both windows open
        # (unkeyed, above, either way).
        from PyQt6 import sip
        ed = self.ttx_editor
        if ed is not None and not sip.isdeleted(ed) and ed.isVisible() and not ed.close():
            e.ignore()
            return
        self.prefs["geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        self.prefs.save()
        super().closeEvent(e)

    def changeEvent(self, e):
        # Losing focus while Space is held must not leave the radio keyed.
        if e.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and self.space_down:
            self.space_release()
        super().changeEvent(e)

