"""The panels around the editor: the service (every page in the folder),
the page's settings, its subpages, its Fastext links and labels, and the
tools with the control-code palette.

They are thin: each reads from and writes to the main window's current
`Document` through its methods, so undo covers what is done here as it
covers the keyboard, and each emits `edited` for the window to redraw.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QSpinBox, QWidget)

from .. import decoder, render
from ..editor import PEN_KEEP, fastext_labels
from ..page import (LINK_NAMES, NO_LINK, PS_ERASE, PS_INHIBIT, PS_INTERRUPTED, PS_NEWSFLASH, PS_SUBTITLE,
                    PS_SUPPRESS_HEADER, PS_TRANSMIT, PS_UPDATE, parse_page_number)
from ..i18n.fmt import number
from . import theme
from .canvas import TOOLS, to_qimage
from .controls import Button, Card, Check, Segmented, hbox
from .theme import S1, S2

COLOUR_HEX = ["#000000", "#ff0000", "#00ff00", "#ffff00", "#0000ff", "#ff00ff", "#00ffff", "#ffffff"]


def thumbnail(page, sub=None, width: int = 84) -> QPixmap:
    sub = sub or page.subpages[0]
    from .preview import display_rows
    d = decoder.decode(display_rows(page, sub, 0), black_fg=False)
    img = to_qimage(render.render(d))
    h = int(width * 500 / (480 * render.TV_ASPECT))
    return QPixmap.fromImage(img.scaled(width, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))


class ServiceList(Card):
    """Every page in the service folder, as a carousel of thumbnails: page
    number, description, how many subpages and how long they cycle."""
    picked = pyqtSignal(int)          # index into service.pages

    def __init__(self, parent=None):
        super().__init__("Service", parent)
        self.list = QListWidget()
        self.list.setIconSize(QSize(84, 73))
        self.list.setSpacing(2)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.currentRowChanged.connect(lambda i: self.picked.emit(i) if i >= 0 else None)
        self.folder = QLabel("")
        self.folder.setObjectName("Faint")
        self.folder.setWordWrap(True)
        self.lay.addWidget(self.list, 1)
        self.lay.addWidget(self.folder)
        self.new_btn = Button(self.tr("New"), "plus", small=True)
        self.open_btn = Button(self.tr("Open"), "folder", small=True)
        self.tmpl_btn = Button(self.tr("Template"), "page", small=True)
        self.tmpl_btn.setToolTip(self.tr("New page from a template (Ctrl+Shift+N)"))
        self.new_btn.setToolTip(self.tr("New blank page (Ctrl+N)"))
        self.open_btn.setToolTip(self.tr("Open a service folder (Ctrl+O)"))
        self.lay.addLayout(hbox(self.new_btn, self.tmpl_btn, self.open_btn, None))

    def populate(self, service, current: int, dirty: set[int]):
        self.list.blockSignals(True)
        self.list.clear()
        for i, pg in enumerate(service.pages):
            n = len(pg.subpages)
            star = " •" if id(pg) in dirty else ""
            sub = f"{n} subpages · {', '.join(sorted({str(s.cycle) for s in pg.subpages}))} s" if n > 1 else \
                "single page"
            it = QListWidgetItem(QIcon(thumbnail(pg)), f"P{pg.label()}{star}\n{pg.description or '—'}\n{sub}")
            it.setToolTip(pg.filename or self.tr("{name} (not saved yet)").format(name=f"P{pg.label()}.tti"))
            self.list.addItem(it)
        self.list.setCurrentRow(current)
        self.list.blockSignals(False)
        dup = service.duplicates()
        note = str(service.folder)
        if dup:
            note += "\n" + "; ".join(f"P{k:03X} is in {', '.join(v)}" for k, v in dup.items())
        if service.problems:
            note += "\nNot read: " + "; ".join(service.problems)
        self.folder.setText(note)

    def refresh_current(self, page, dirty: bool):
        it = self.list.currentItem()
        if it is None:
            return
        n = len(page.subpages)
        sub = f"{n} subpages" if n > 1 else "single page"
        it.setIcon(QIcon(thumbnail(page, page.subpages[0])))
        it.setText(f"P{page.label()}{' •' if dirty else ''}\n{page.description or '—'}\n{sub}")


class PagePanel(Card):
    """Page number, description and the page-status bits that matter to a
    Level 1 page, and whether the page is live."""
    edited = pyqtSignal()
    liveToggled = pyqtSignal(bool)
    FLAGS = [(PS_TRANSMIT, "Transmit", "PS bit 15: the inserter sends the page"),
             (PS_NEWSFLASH, "Newsflash (C5)", "only boxed text shows, over the picture"),
             (PS_SUBTITLE, "Subtitle (C6)", "only boxed text shows, over the picture"),
             (PS_SUPPRESS_HEADER, "Suppress header (C7)", ""),
             (PS_ERASE, "Erase page (C4)", "the set clears the page before showing this one"),
             (PS_UPDATE, "Update (C8)", ""), (PS_INTERRUPTED, "Interrupted sequence (C9)", ""),
             (PS_INHIBIT, "Inhibit display (C10)", "")]

    def __init__(self, parent=None):
        super().__init__("Page", parent)
        self.doc = None
        self.number = QLineEdit()
        self.number.setMaxLength(3)
        self.number.setFixedWidth(64)
        self.number.setFont(theme.font(14, theme.QFont.Weight.DemiBold, mono=True))
        self.number.editingFinished.connect(self._number)
        self.desc = QLineEdit()
        self.desc.setPlaceholderText(self.tr("description (DE)"))
        self.desc.editingFinished.connect(self._desc)
        self.lay.addLayout(hbox(QLabel("P"), self.number, self.desc))
        grid = QGridLayout()
        grid.setHorizontalSpacing(S2)
        grid.setVerticalSpacing(S1)
        self.checks = {}
        for i, (bit, label, tip) in enumerate(self.FLAGS):
            c = Check(label)
            c.setToolTip(tip)
            c.toggled.connect(lambda on, b=bit: self._flag(b, on))
            self.checks[bit] = c
            grid.addWidget(c, i // 2, i % 2)
        self.lay.addLayout(grid)
        self.live = Check(self.tr("Live page: {fields} filled by the runner"))
        self.live.setToolTip(self.tr("The page's {tokens} are filled from data (station, solar, DX cluster, beacons) "
                                     "by python3 -m ohstxt.live; the template is saved beside the page as .live.json"))
        self.live.toggled.connect(lambda on: self.liveToggled.emit(on))
        self.lay.addWidget(self.live)
        self.note = QLabel("")
        self.note.setObjectName("Faint")
        self.note.setWordWrap(True)
        self.lay.addWidget(self.note)

    def set_document(self, doc):
        self.doc = doc
        self.load()

    def load(self):
        d = self.doc
        if d is None:
            return
        self.live.blockSignals(True)
        self.live.setChecked(d.page.live is not None)
        self.live.blockSignals(False)
        self.number.setText(d.page.label())
        self.desc.setText(d.page.description)
        for bit, c in self.checks.items():
            c.blockSignals(True)
            c.setChecked(bool(d.sub.status & bit))
            c.blockSignals(False)
        self.note.setText(self.tr("Flags are per subpage; these are this subpage's. National option: English."))

    def _number(self):
        n = parse_page_number(self.number.text())
        if n is None:
            self.number.setText(self.doc.page.label())
            return
        if n != self.doc.page.number:
            self.doc.checkpoint()
            self.doc.page.number = n
            self.edited.emit()

    def _desc(self):
        if self.desc.text() != self.doc.page.description:
            self.doc.checkpoint()
            self.doc.page.description = self.desc.text()
            self.edited.emit()

    def _flag(self, bit, on):
        s = self.doc.sub
        new = (s.status | bit) if on else (s.status & ~bit)
        if new != s.status:
            self.doc.checkpoint()
            s.status = new
            self.edited.emit()


class SubpagePanel(Card):
    """The subpages of the page, each with its own cycle time."""
    edited = pyqtSignal()
    chosen = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Subpages", parent)
        self.doc = None
        self.list = QListWidget()
        self.list.setIconSize(QSize(56, 49))
        self.list.setFixedHeight(150)
        self.list.currentRowChanged.connect(self._pick)
        self.lay.addWidget(self.list)
        self.add = Button(self.tr("Add"), "plus", small=True)
        self.dup = Button(self.tr("Duplicate"), "page", small=True)
        self.rm = Button(self.tr("Delete"), "trash", small=True)
        self.up = Button("↑", small=True)
        self.down = Button("↓", small=True)
        self.lay.addLayout(hbox(self.add, self.dup, self.rm, None, self.up, self.down))
        self.cycle = QSpinBox()
        self.cycle.setRange(1, 255)
        self.cycle.setFixedWidth(70)
        self.cycle.valueChanged.connect(self._cycle)
        self.kind = Segmented([("T", "seconds"), ("C", "cycles")], "T", compact=True)
        self.kind.picked.connect(lambda v: self._cycle())
        lab = QLabel(self.tr("Cycle"))
        self.lay.addLayout(hbox(lab, self.cycle, self.kind))
        self.add.clicked.connect(lambda: self._do(lambda: self.doc.add_subpage(False)))
        self.dup.clicked.connect(lambda: self._do(lambda: self.doc.add_subpage(True)))
        self.rm.clicked.connect(lambda: self._do(self.doc.delete_subpage))
        self.up.clicked.connect(lambda: self._do(lambda: self.doc.move_subpage(-1)))
        self.down.clicked.connect(lambda: self._do(lambda: self.doc.move_subpage(1)))

    def set_document(self, doc):
        self.doc = doc
        self.load()

    def _do(self, fn):
        fn()
        self.load()
        self.edited.emit()
        self.chosen.emit()

    def load(self):
        d = self.doc
        if d is None:
            return
        self.list.blockSignals(True)
        self.list.clear()
        for i, s in enumerate(d.page.subpages, 1):
            self.list.addItem(QListWidgetItem(QIcon(thumbnail(d.page, s, 56)), self._row_text(i, s.subcode, s.cycle,
                                                                                               s.timed)))
        self.list.setCurrentRow(d.sub_index)
        self.list.blockSignals(False)
        self.cycle.blockSignals(True)
        self.cycle.setValue(d.sub.cycle)
        self.cycle.blockSignals(False)
        self.kind.set_value("T" if d.sub.timed else "C", animate=False)
        self.rm.setEnabled(len(d.page.subpages) > 1)

    def _pick(self, i):
        if i >= 0 and self.doc is not None and i != self.doc.sub_index:
            self.doc.sub_index = i
            self.load()
            self.chosen.emit()

    def _row_text(self, index: int, subcode: int, cycle: float, timed: bool) -> str:
        """A subpage's line in the list: its number, sub-code, and how long
        it stays up - seconds, or magazine cycles (a count, so a plural)."""
        if timed:
            return self.tr("{index}  ·  SC {code}  ·  {seconds} s").format(index=index, code=f"{subcode:04X}",
                                                                           seconds=number(cycle))
        return self.tr("{index}  ·  SC {code}  ·  %n cycle(s)", "", int(cycle)).format(index=index,
                                                                                    code=f"{subcode:04X}")

    def _cycle(self):
        d = self.doc
        if d is None:
            return
        v, timed = self.cycle.value(), self.kind.value == "T"
        if (v, timed) != (d.sub.cycle, d.sub.timed):
            d.set_cycle(v, timed)
            it = self.list.item(d.sub_index)
            if it:
                it.setText(self._row_text(d.sub_index + 1, d.sub.subcode, v, timed))
            self.edited.emit()


class Swatch(QWidget):
    """A small colour dot used as a label."""

    def __init__(self, colour: str, parent=None):
        super().__init__(parent)
        self.colour = colour
        self.setFixedSize(14, 14)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(theme.c("line2"), 1))
        p.setBrush(QColor(self.colour))
        p.drawEllipse(QRectF(1, 1, 12, 12))


class FastextPanel(Card):
    """The FL line - red, green, yellow, cyan, the fifth link and the index -
    and the labels in row 24 that the coloured keys sit under."""
    edited = pyqtSignal()
    LINK_COLOURS = ["#ff4d5e", "#3ddc84", "#ffd166", "#4fd1e8", "#8592a8", "#e8edf4"]

    def __init__(self, parent=None):
        super().__init__("Fastext", parent)
        self.doc = None
        grid = QGridLayout()
        grid.setHorizontalSpacing(S2)
        grid.setVerticalSpacing(S1)
        self.links = []
        for i, name in enumerate(LINK_NAMES):
            e = QLineEdit()
            e.setMaxLength(3)
            e.setFixedWidth(58)
            e.setPlaceholderText("—")
            e.setFont(theme.font(13, mono=True))
            e.editingFinished.connect(lambda i=i: self._link(i))
            self.links.append(e)
            cell = QHBoxLayout()
            cell.setSpacing(S1)
            cell.addWidget(Swatch(self.LINK_COLOURS[i]))
            lab = QLabel(name)
            lab.setObjectName("Muted")
            cell.addWidget(lab)
            cell.addStretch(1)
            cell.addWidget(e)
            grid.addLayout(cell, i // 2, i % 2)
        self.lay.addLayout(grid)
        cap = QLabel(self.tr("Row 24 labels"))
        cap.setObjectName("Muted")
        self.lay.addWidget(cap)
        self.labels = []
        row = QHBoxLayout()
        row.setSpacing(S1)
        for i in range(4):
            e = QLineEdit()
            e.setMaxLength(9)
            e.setFont(theme.font(12, mono=True))
            colour = COLOUR_HEX[(1, 2, 3, 6)[i]]
            e.setStyleSheet(f"QLineEdit {{ color: {colour}; background: #000; }}")
            e.editingFinished.connect(self._labels)
            self.labels.append(e)
            row.addWidget(e)
        self.lay.addLayout(row)
        self.note = QLabel("")
        self.note.setObjectName("Faint")
        self.note.setWordWrap(True)
        self.lay.addWidget(self.note)

    def set_document(self, doc):
        self.doc = doc
        self.load()

    def load(self):
        d = self.doc
        if d is None:
            return
        for i, e in enumerate(self.links):
            lk = d.sub.links[i]
            e.setText("" if lk == NO_LINK else f"{lk:03X}")
        labels = fastext_labels(d.sub.rows[24])
        blank = all(c == 0x20 for c in d.sub.rows[24])
        for i, e in enumerate(self.labels):
            e.setText(labels[i][1] if labels else "")
        if labels or blank:
            self.note.setText(self.tr("Four labels, a quarter of row 24 each, in the colours of the keys."))
        else:
            self.note.setText(self.tr("Row 24 has a layout of its own: typing a label here replaces it with four "
                                      "labels."))

    def _link(self, i):
        t = self.links[i].text().strip()
        n = parse_page_number(t) if t else None
        if t and n is None:
            self.links[i].setText("" if self.doc.sub.links[i] == NO_LINK else f"{self.doc.sub.links[i]:03X}")
            return
        if (n or NO_LINK) != self.doc.sub.links[i]:
            self.doc.set_link(i, n)
            self.edited.emit()

    def _labels(self):
        texts = [e.text() for e in self.labels]
        cur = fastext_labels(self.doc.sub.rows[24])
        if cur and [t for _, t in cur] == [t.rstrip() for t in texts]:
            return
        if not cur and not any(texts):
            return
        self.doc.set_fastext_labels([(c, t) for c, t in zip((1, 2, 3, 6), texts)])
        self.edited.emit()
        self.load()


class CodeButton(QWidget):
    """One control code in the palette: its mnemonic, coloured as the code view draws it."""
    clicked = pyqtSignal(int)

    def __init__(self, code: int, parent=None):
        super().__init__(parent)
        self.code = code
        self.setFixedSize(38, 26)
        self.setToolTip(f"0x{code:02X} {decoder.CONTROL_NAMES[code]}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hover = False

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.code)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self.code
        colour = QColor(COLOUR_HEX[c & 7]) if (c <= 7 or 0x10 <= c <= 0x17) else theme.c("accent")
        if (c & 7) == 0 and (c <= 7 or 0x10 <= c <= 0x17):
            colour = QColor(140, 140, 140)
        p.setPen(QPen(colour if self.hover else theme.c("line2"), 1))
        p.setBrush(QColor(0, 0, 0))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
        p.setPen(colour)
        p.setFont(theme.font(11.5, theme.QFont.Weight.Bold, mono=True))
        p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, decoder.CONTROL_SHORT[c])


class PenSwatch(QWidget):
    picked = pyqtSignal(int)

    def __init__(self, colour: int, parent=None):
        super().__init__(parent)
        self.colour = colour
        self.selected = False
        self.setFixedSize(26, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(self.tr("keep the cell's colour") if colour == PEN_KEEP else decoder.COLOUR_NAMES[colour])

    def mouseReleaseEvent(self, e):
        self.picked.emit(self.colour)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(3, 3, 20, 20)
        if self.colour == PEN_KEEP:
            p.setPen(QPen(theme.c("text2"), 1.4, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 5, 5)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(COLOUR_HEX[self.colour]))
            p.drawRoundedRect(r, 5, 5)
        if self.selected:
            p.setPen(QPen(theme.c("accent"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(1, 1, 24, 24), 7, 7)


class ToolPanel(Card):
    """The drawing tool, the pen colour, and every control code as a button."""
    toolPicked = pyqtSignal(str)
    penPicked = pyqtSignal(int)
    codePicked = pyqtSignal(int)
    filledChanged = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__("Tools", parent)
        self.tools = Segmented(TOOLS, "text", compact=True)
        self.tools.picked.connect(self.toolPicked)
        self.lay.addWidget(self.tools)
        row = QHBoxLayout()
        row.setSpacing(2)
        lab = QLabel(self.tr("Pen"))
        lab.setObjectName("Muted")
        row.addWidget(lab)
        self.swatches = []
        for col in [PEN_KEEP, 7, 1, 2, 3, 4, 5, 6]:
            s = PenSwatch(col)
            s.picked.connect(self._pen)
            self.swatches.append(s)
            row.addWidget(s)
        row.addStretch(1)
        self.filled = Check(self.tr("Filled"))
        self.filled.toggled.connect(self.filledChanged)
        row.addWidget(self.filled)
        self.lay.addLayout(row)
        grid = QGridLayout()
        grid.setSpacing(3)
        order = list(range(0x00, 0x08)) + list(range(0x10, 0x18)) + [0x08, 0x09, 0x0D, 0x0C, 0x1D, 0x1C, 0x18, 0x1E,
                                                                     0x1F, 0x19, 0x1A, 0x0B, 0x0A, 0x1B, 0x0E, 0x0F]
        for i, code in enumerate(order):
            b = CodeButton(code)
            b.clicked.connect(self.codePicked)
            grid.addWidget(b, i // 8, i % 8)
        self.lay.addLayout(grid)
        self._pen(PEN_KEEP)

    def set_tool(self, t: str):
        self.tools.set_value(t)

    def _pen(self, colour):
        for s in self.swatches:
            s.selected = s.colour == colour
            s.update()
        self.penPicked.emit(colour)


class CellInfo(QWidget):
    """One line under the editor: where the cursor is, the code in the cell,
    and the attributes the decoder has in force there."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(24)
        self.text = ""
        self.warn = ""

    def show_cell(self, doc, decoded):
        r, c = doc.row, doc.col
        code = doc.sub.rows[r][c]
        a = decoded.attrs_at(r, c)
        what = (f"0x{code:02X} {decoder.CONTROL_NAMES[code]}" if code < 0x20 else
                f"0x{code:02X} '{chr(code) if code != 0x7F else '■'}'" + (" mosaic" if decoded.gset[r, c] else ""))
        mode = "graphics" if a["graphics"] else "alpha"
        extra = [x for x, on in (("separated", a["separated"]), ("flash", a["flash"]), ("conceal", a["conceal"]),
                                 ("double height", a["dh"] == 1), ("lower half", a["dh"] == 2),
                                 ("boxed", a["box"])) if on]
        self.text = (f"row {r:2d}  col {c:2d}   {what}   {mode} {decoder.COLOUR_NAMES[a['fg']]} on "
                     f"{decoder.COLOUR_NAMES[a['bg']]}" + ("   " + ", ".join(extra) if extra else ""))
        self.update()

    def set_warning(self, text: str):
        self.warn = text
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setFont(theme.font(12, mono=True))
        p.setPen(theme.c("text2"))
        p.drawText(QRectF(4, 0, self.width(), self.height()), Qt.AlignmentFlag.AlignVCenter, self.text)
        if self.warn:
            p.setFont(theme.font(12, theme.QFont.Weight.DemiBold))
            p.setPen(theme.c("warn"))
            p.drawText(QRectF(0, 0, self.width() - 6, self.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self.warn)
