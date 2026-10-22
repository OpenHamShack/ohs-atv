"""Dialogs: image import (with the converted page shown before anything is
changed), edit.tf / zxnet links in and out, and a new page's number.

Image import previews through the real decoder: the converted rows are
put into a copy of the subpage and rendered exactly as the editor will
render them, so what is shown is what Commit gives, colour-code cells and
all - not the dithered target, which no set could display.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QGuiApplication, QPainter
from PyQt6.QtWidgets import (QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                             QSpinBox, QVBoxLayout, QWidget)

from .. import decoder, edittf, imageconv, render
from ..page import parse_page_number
from ..i18n.fmt import number
from . import theme
from .canvas import editor_rows, to_qimage
from .controls import Button, Check, Segmented, Slider, hbox
from .theme import S2, S3


class _Picture(QWidget):
    """A page image, drawn at TV proportions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.img = None
        self.setMinimumSize(420, 360)

    def set_image(self, img):
        self.img = img
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), theme.c("scope"))
        if self.img is None:
            return
        pw, ph = render.PAGE_W * render.TV_ASPECT, render.PAGE_H
        s = min(self.width() / pw, self.height() / ph)
        r = QRectF((self.width() - pw * s) / 2, (self.height() - ph * s) / 2, pw * s, ph * s)
        p.drawImage(r, self.img)


class ImageDialog(QDialog):
    """Convert a picture into mosaics and show the page it makes."""

    def __init__(self, doc, path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Import image"))
        self.doc = doc
        self.image = imageconv.load_image(path)
        self.result = None
        self.top, self.left = 1, 0
        lay = QHBoxLayout(self)
        lay.setSpacing(S3)
        self.view = _Picture()
        self.target = _Picture()
        self.target.setMinimumSize(200, 170)
        left = QVBoxLayout()
        left.addWidget(self.view, 1)
        cap = QLabel(self.tr("The page as a set will show it. Below: what the conversion aimed at."))
        cap.setObjectName("Faint")
        left.addWidget(cap)
        left.addWidget(self.target)
        lay.addLayout(left, 1)
        side = QVBoxLayout()
        side.setSpacing(S2)
        name = QLabel(Path(path).name)
        name.setFont(theme.font(14, theme.QFont.Weight.DemiBold))
        side.addWidget(name)
        g = QGridLayout()
        self.rows = QSpinBox()
        self.rows.setRange(1, 23)
        self.rows.setValue(23)
        self.top_s = QSpinBox()
        self.top_s.setRange(1, 23)
        self.top_s.setValue(1)
        self.cols = QSpinBox()
        self.cols.setRange(2, 40)
        self.cols.setValue(40)
        self.left_s = QSpinBox()
        self.left_s.setRange(0, 38)
        for i, (lab, w) in enumerate((("Top row", self.top_s), ("Rows", self.rows), ("Left column", self.left_s),
                                      ("Columns", self.cols))):
            g.addWidget(QLabel(lab), i, 0)
            g.addWidget(w, i, 1)
        side.addLayout(g)
        side.addWidget(QLabel(self.tr("Dither")))
        self.dither = Segmented([("none", self.tr("None")), ("floyd", self.tr("Floyd–Steinberg")),
                                 ("bayer", self.tr("Ordered"))], "none", compact=True)
        side.addWidget(self.dither)
        side.addWidget(QLabel(self.tr("Fit")))
        self.fit = Segmented([("fit", self.tr("Fit")), ("fill", self.tr("Fill")), ("stretch", self.tr("Stretch"))], "fit", compact=True)
        side.addWidget(self.fit)
        side.addWidget(QLabel(self.tr("Colours")))
        self.palette = Segmented([("all", self.tr("All 8")), ("mono", self.tr("White")), ("warm", "R Y W"), ("cool", "B C W")], "all",
                                 compact=True)
        side.addWidget(self.palette)
        self.bgs = Check(self.tr("Background changes (new/black background codes)"), True)
        self.sep = Check(self.tr("Separated graphics"), False)
        side.addWidget(self.bgs)
        side.addWidget(self.sep)
        self.bright = Slider(self.tr("Brightness"), -0.5, 0.5, 0.0, 0.05)
        self.contrast = Slider(self.tr("Contrast"), 0.5, 2.0, 1.0, 0.05)
        side.addWidget(self.bright)
        side.addWidget(self.contrast)
        self.stats = QLabel("")
        self.stats.setObjectName("Muted")
        self.stats.setWordWrap(True)
        side.addWidget(self.stats)
        side.addStretch(1)
        self.ok = Button(self.tr("Commit to page"), "check", kind="primary")
        cancel = Button(self.tr("Cancel"))
        self.ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        side.addLayout(hbox(None, cancel, self.ok))
        holder = QWidget()
        holder.setLayout(side)
        holder.setFixedWidth(360)
        lay.addWidget(holder)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.convert)
        for w in (self.rows, self.top_s, self.cols, self.left_s):
            w.valueChanged.connect(self._later)
        for w in (self.dither, self.fit, self.palette):
            w.picked.connect(self._later)
        for w in (self.bgs, self.sep):
            w.toggled.connect(self._later)
        for w in (self.bright, self.contrast):
            w.changed.connect(self._later)
        self.resize(1100, 720)
        self.convert()

    def _later(self, *_):
        self._timer.start(120)

    def options(self) -> imageconv.Options:
        pal = {"all": (0, 1, 2, 3, 4, 5, 6, 7), "mono": (7,), "warm": (1, 3, 7), "cool": (4, 6, 7)}[self.palette.value]
        top = self.top_s.value()
        rows = min(self.rows.value(), 24 - top)
        left = self.left_s.value()
        cols = min(self.cols.value(), 40 - left)
        self.top, self.left = top, left
        return imageconv.Options(width=cols, height=rows, dither=self.dither.value, fit=self.fit.value,
                                 colours=pal, backgrounds=self.bgs.isChecked(), separated=self.sep.isChecked(),
                                 brightness=float(self.bright.v), contrast=float(self.contrast.v))

    def convert(self):
        opts = self.options()
        self.result = imageconv.convert(self.image, opts)
        rows = editor_rows(self.doc)
        for i, data in enumerate(self.result.rows):
            rows[self.top + i][self.left:self.left + len(data)] = data
        d = decoder.decode(rows, black_fg=self.doc.black_fg)
        self.view.set_image(to_qimage(render.render(d)))
        tgt = self.result.target
        self.target.set_image(to_qimage(render.stretch(tgt, 1.0)))
        self.stats.setText(self.tr("{width} × {height} cells, %n spent on colour codes; mean colour error {error} %.",
                                   "", self.result.codes_spent).format(width=opts.width, height=opts.height,
                                                                       error=number(100 * self.result.error, 1)))

    def commit(self):
        if self.result is not None:
            self.doc.replace_rows(self.result.rows, top=self.top, left=self.left)


class LinkDialog(QDialog):
    """edit.tf / zxnet hash-URL: paste one to import, or copy this page's."""

    def __init__(self, url: str = "", importing: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("edit.tf link"))
        lay = QVBoxLayout(self)
        lay.setSpacing(S2)
        msg = ("Paste an edit.tf or zxnet editor link. The page replaces this subpage (undo brings it back)."
               if importing else "This subpage as an edit.tf link (zxnet's editor opens the same link).")
        lab = QLabel(msg)
        lab.setWordWrap(True)
        lay.addWidget(lab)
        self.text = QPlainTextEdit(url)
        self.text.setFont(theme.font(11.5, mono=True))
        self.text.setReadOnly(not importing)
        self.text.setMinimumSize(640, 160)
        lay.addWidget(self.text)
        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {theme.t()['warn']};")
        lay.addWidget(self.error)
        self.importing = importing
        if importing:
            ok = Button(self.tr("Import"), "check", kind="primary")
            ok.clicked.connect(self._try)
            cancel = Button(self.tr("Cancel"))
            cancel.clicked.connect(self.reject)
            lay.addLayout(hbox(None, cancel, ok))
        else:
            self.zx = Button(self.tr("zxnet form"))
            copy = Button(self.tr("Copy"), kind="primary")
            close = Button(self.tr("Close"))
            copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.text.toPlainText()))
            close.clicked.connect(self.accept)
            self.zx.clicked.connect(self._zx)
            lay.addLayout(hbox(self.zx, None, close, copy))
        self.parsed = None

    def _zx(self):
        t = self.text.toPlainText()
        if t.startswith(edittf.EDIT_TF):
            self.text.setPlainText(edittf.ZXNET + t[len(edittf.EDIT_TF):])
        else:
            self.text.setPlainText(edittf.EDIT_TF + t[len(edittf.ZXNET):])

    def _try(self):
        try:
            self.parsed = edittf.parse(self.text.toPlainText())
        except edittf.HashError as e:
            self.error.setText(str(e))
            return
        self.accept()


class NumberDialog(QDialog):
    def __init__(self, suggestion: int, taken: set[int], parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("New page"))
        self.taken = taken
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(self.tr("Page number (100–8FE)")))
        self.edit = QLineEdit(f"{suggestion:03X}")
        self.edit.setMaxLength(3)
        self.edit.setFont(theme.font(18, theme.QFont.Weight.DemiBold, mono=True))
        lay.addWidget(self.edit)
        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {theme.t()['warn']};")
        lay.addWidget(self.error)
        ok = Button(self.tr("Create"), "plus", kind="primary")
        cancel = Button(self.tr("Cancel"))
        ok.clicked.connect(self._ok)
        cancel.clicked.connect(self.reject)
        lay.addLayout(hbox(None, cancel, ok))
        self.number = None

    def _ok(self):
        n = parse_page_number(self.edit.text())
        if n is None or (n & 0xFF) == 0xFF:
            self.error.setText(self.tr("Three hex digits, magazine 1-8; mFF is not a page."))
            return
        if n in self.taken:
            self.error.setText(self.tr("P{page} is already in this service.").format(page=f"{n:03X}"))
            return
        self.number = n
        self.accept()


def open_image(parent, start: str) -> str:
    # A function, not a method: the words are looked up under the dialog
    # that follows, whose title is the same "Import image".
    from PyQt6.QtCore import QCoreApplication
    tr = QCoreApplication.translate
    path, _ = QFileDialog.getOpenFileName(parent, tr("ImageDialog", "Import image"), start,
                                          tr("ImageDialog", "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.tif *.tiff)"))
    return path
