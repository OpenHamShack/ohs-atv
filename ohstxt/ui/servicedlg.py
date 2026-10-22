"""Dialogs about the service as a whole: the checker's findings, and the
station settings (callsign, locator, live data sources) kept in the
folder's station.json.

The checker's dialog is not modal: it stays open beside the editor, and a
click on a finding takes the editor to the page, subpage and cell, so a
list of twenty problems can be worked through without reopening it. Run
before Save all, it becomes a question - save anyway, or go and fix - and
only errors ask it; warnings are said and saved past.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (QDialog, QDoubleSpinBox, QGridLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QSpinBox, QStyle, QStyledItemDelegate, QVBoxLayout)

from .. import check
from ..station import Station
from . import theme
from .controls import Button, hbox

SEVERITY_COLOUR = {check.ERROR: "err", check.WARNING: "warn", check.INFO: "text3"}


class _FindingDelegate(QStyledItemDelegate):
    """A finding as a row: a severity dot, where, and what."""

    def paint(self, p: QPainter, opt, index):
        f = index.data(Qt.ItemDataRole.UserRole)
        if f is None:
            return
        r = QRectF(opt.rect)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        if selected:
            p.fillRect(r, theme.c("accent", 0.18))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = theme.c(SEVERITY_COLOUR.get(f.severity, "text3"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(QRectF(r.x() + 8, r.center().y() - 5, 10, 10))
        p.setFont(theme.font(12, theme.QFont.Weight.DemiBold, mono=True))
        p.setPen(theme.c("text"))
        p.drawText(QRectF(r.x() + 26, r.y(), 128, r.height()), Qt.AlignmentFlag.AlignVCenter, f.where())
        p.setFont(theme.font(12.5))
        p.setPen(theme.c("text2"))
        p.drawText(QRectF(r.x() + 158, r.y() + 2, r.width() - 164, r.height() - 4),
                   Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap, f.message)

    def sizeHint(self, opt, index):
        from PyQt6.QtCore import QSize
        return QSize(600, 40)


class CheckDialog(QDialog):
    """The service checker's findings; a click goes to the cell."""
    jump = pyqtSignal(object)            # a check.Finding
    recheck = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Check service"))
        self.setModal(False)
        lay = QVBoxLayout(self)
        self.summary = QLabel("")
        self.summary.setFont(theme.font(14, theme.QFont.Weight.DemiBold))
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.list = QListWidget()
        self.list.setItemDelegate(_FindingDelegate(self.list))
        self.list.setUniformItemSizes(True)
        self.list.itemClicked.connect(lambda it: self.jump.emit(it.data(Qt.ItemDataRole.UserRole)))
        self.list.currentItemChanged.connect(
            lambda it, _old: self.jump.emit(it.data(Qt.ItemDataRole.UserRole)) if it else None)
        lay.addWidget(self.list, 1)
        self.note = QLabel(self.tr("Click a finding to go to it. Errors break the service for a viewer; warnings "
                                   "probably are not what was meant."))
        self.note.setObjectName("Faint")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.again = Button(self.tr("Check again"), "check")
        self.again.clicked.connect(self.recheck)
        self.save_anyway = Button(self.tr("Save anyway"), "save", kind="primary")
        self.save_anyway.clicked.connect(self.accept)
        self.close_btn = Button(self.tr("Close"))
        self.close_btn.clicked.connect(self.reject)
        lay.addLayout(hbox(self.again, None, self.close_btn, self.save_anyway))
        self.resize(780, 560)
        self.set_saving(False)

    def set_saving(self, saving: bool):
        """Before Save all: the dialog asks, rather than just tells."""
        self.save_anyway.setVisible(saving)
        self.close_btn.set_text(self.tr("Cancel") if saving else self.tr("Close"))

    def show_report(self, rep: check.Report):
        self.report = rep
        self.summary.setText(rep.summary())
        self.list.blockSignals(True)
        self.list.clear()
        for f in rep.findings:
            it = QListWidgetItem(f"{f.severity} {f.where()} {f.message}")
            it.setData(Qt.ItemDataRole.UserRole, f)
            it.setToolTip(f.message + (f"\n{f.filename}" if f.filename else ""))
            self.list.addItem(it)
        self.list.blockSignals(False)


class StationDialog(QDialog):
    """The station: who is transmitting, from where, and (for live pages)
    where the data comes from. Written to the service folder's station.json."""

    def __init__(self, st: Station, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Station settings"))
        self.st = st
        lay = QVBoxLayout(self)
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        self.callsign = QLineEdit(st.callsign)
        self.callsign.setFont(theme.font(15, theme.QFont.Weight.DemiBold, mono=True))
        self.name = QLineEdit(st.name)
        self.locator = QLineEdit(st.locator)
        self.locator.setPlaceholderText(self.tr("from latitude and longitude if empty"))
        self.qth = QLineEdit(st.qth)
        self.lat = QDoubleSpinBox()
        self.lon = QDoubleSpinBox()
        for w, lim, v in ((self.lat, 90, st.lat), (self.lon, 180, st.lon)):
            w.setRange(-lim, lim)
            w.setDecimals(4)
            w.setSpecialValueText(self.tr("not set"))
            w.setMinimum(-lim - 0.0001)
            w.setValue(v if v is not None else w.minimum())
        self.lines = QSpinBox()
        self.lines.setRange(1, 32)
        self.lines.setValue(st.vbi_lines)
        rows = [("Callsign", self.callsign), ("Operator name", self.name), ("Locator", self.locator),
                ("QTH", self.qth), ("Latitude", self.lat), ("Longitude", self.lon),
                ("Teletext lines per field", self.lines)]
        for i, (lab, w) in enumerate(rows):
            g.addWidget(QLabel(lab), i, 0)
            g.addWidget(w, i, 1)
        lay.addLayout(g)
        self.extra = QVBoxLayout()          # live data sources go here
        lay.addLayout(self.extra)
        note = QLabel(self.tr("Kept in station.json in the service folder: the checker, the templates and the "
                              "live-page runner read it from there. Pages carry the callsign in their header row or "
                              "on P100."))
        note.setObjectName("Faint")
        note.setWordWrap(True)
        lay.addWidget(note)
        ok = Button(self.tr("Save"), "check", kind="primary")
        cancel = Button(self.tr("Cancel"))
        ok.clicked.connect(self._ok)
        cancel.clicked.connect(self.reject)
        lay.addLayout(hbox(None, cancel, ok))
        self.resize(520, 420)

    def _ok(self):
        st = self.st
        st.callsign = self.callsign.text().strip().upper()
        st.name = self.name.text().strip()
        st.locator = self.locator.text().strip()
        st.qth = self.qth.text().strip()
        st.lat = None if self.lat.value() == self.lat.minimum() else self.lat.value()
        st.lon = None if self.lon.value() == self.lon.minimum() else self.lon.value()
        st.vbi_lines = self.lines.value()
        self.accept()


def free_near(n: int, taken: set[int]) -> int:
    """The template's own number if free, else the nearest free page in its
    magazine with decimal digits (P19A is a page, but not one an index or a
    viewer's keypad reaches)."""
    mag = n & 0xF00
    order = sorted((m for m in range(mag, mag + 0x100) if (m & 0xF0) <= 0x90 and (m & 0xF) <= 9),
                   key=lambda m: (abs(m - n), m))
    return next((m for m in order if m not in taken), n)


class TemplateDialog(QDialog):
    """New page from a template: the templates as pages (live ones filled
    with sample data), and the number to give the new page."""

    def __init__(self, st: Station, taken: set[int], parent=None):
        super().__init__(parent)
        from PyQt6.QtCore import QSize
        from PyQt6.QtGui import QIcon

        from .. import templates
        self.setWindowTitle(self.tr("New page from template"))
        self.st = st
        self.taken = taken
        self.page = None
        lay = QVBoxLayout(self)
        if not st.callsign:
            warn = QLabel(self.tr("No callsign in the station settings yet: the pages will say N0CALL until it is "
                                  "set."))
            warn.setStyleSheet(f"color: {theme.t()['warn']};")
            warn.setWordWrap(True)
            lay.addWidget(warn)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(240, 208))
        self.list.setGridSize(QSize(270, 268))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setWordWrap(True)
        for t in templates.TEMPLATES:
            pg = t.build(st, t.number)
            it = QListWidgetItem(QIcon(self._thumb(pg)), f"{t.title}\nP{t.number:03X} · {t.about}")
            it.setData(Qt.ItemDataRole.UserRole, t)
            it.setToolTip(t.about)
            self.list.addItem(it)
        self.list.currentItemChanged.connect(self._picked)
        self.list.itemDoubleClicked.connect(lambda _it: self._ok())
        lay.addWidget(self.list, 1)
        self.number = QLineEdit()
        self.number.setMaxLength(3)
        self.number.setFixedWidth(80)
        self.number.setFont(theme.font(16, theme.QFont.Weight.DemiBold, mono=True))
        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {theme.t()['warn']};")
        ok = Button(self.tr("Create"), "plus", kind="primary")
        cancel = Button(self.tr("Cancel"))
        ok.clicked.connect(self._ok)
        cancel.clicked.connect(self.reject)
        lay.addLayout(hbox(QLabel(self.tr("Page")), self.number, self.error, None, cancel, ok))
        self.list.setCurrentRow(0)
        self.resize(1180, 720)

    def _thumb(self, pg):
        from .. import live
        from .panels import thumbnail
        if pg.live is not None:
            b = live.Board(".", self.st)
            b.sample = True
            pg = b.render(pg, live.template_of(pg, pg.live))
        return thumbnail(pg, width=240)

    def _picked(self, it, _old=None):
        if it is None:
            return
        t = it.data(Qt.ItemDataRole.UserRole)
        self.number.setText(f"{free_near(t.number, self.taken):03X}")
        self.error.setText("")

    def _ok(self):
        from ..page import parse_page_number
        it = self.list.currentItem()
        n = parse_page_number(self.number.text())
        if it is None:
            return
        if n is None or (n & 0xFF) == 0xFF:
            self.error.setText(self.tr("Three hex digits, magazine 1-8"))
            return
        if n in self.taken:
            self.error.setText(self.tr("P{page} is already in the service").format(page=f"{n:03X}"))
            return
        t = it.data(Qt.ItemDataRole.UserRole)
        self.page = t.build(self.st, n)
        self.accept()
