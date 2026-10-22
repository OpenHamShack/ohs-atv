"""Open received material: a .t42 capture's pages, with the cells that
arrived damaged marked, for choosing which go into the service.

The page is drawn by the same decoder as everything else; a damaged cell
gets a red frame with a diagonal across it, so a damaged space - which
looks like any other space - is still seen. Pages come in ticked if they
have no damage and their number is free; a number already in the service
is imported as the next free number in its magazine, and the status line
says which.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from .. import decoder, receive, render
from ..page import COLS, ROWS
from . import theme
from .canvas import to_qimage
from .controls import Button, hbox
from .panels import thumbnail


class DamageView(QWidget):
    """A received subpage at TV proportions, damaged cells marked."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sub = None
        self.page_number = 0x100
        self.setMinimumSize(520, 440)

    def show_subpage(self, number: int, rx_sub):
        self.page_number = number
        self.sub = rx_sub
        self.update()

    def page_rect(self) -> QRectF:
        pw, ph = render.PAGE_W * render.TV_ASPECT, render.PAGE_H
        s = min(self.width() / pw, self.height() / ph)
        return QRectF((self.width() - pw * s) / 2, (self.height() - ph * s) / 2, pw * s, ph * s)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), theme.c("scope"))
        if self.sub is None:
            return
        rows = [bytearray(r) for r in self.sub.sub.rows]
        lead = f" P{self.page_number:03X}   ".encode()[:8]
        rows[0][:8] = lead
        img = to_qimage(render.render(decoder.decode(rows)))
        pr = self.page_rect()
        p.drawImage(pr, img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cw, ch = pr.width() / COLS, pr.height() / ROWS
        pen = QPen(QColor(255, 60, 80), 1.6)
        for r, c in self.sub.damaged:
            cell = QRectF(pr.x() + c * cw, pr.y() + r * ch, cw, ch).adjusted(1, 1, -1, -1)
            p.setPen(pen)
            p.setBrush(QColor(255, 60, 80, 70))
            p.drawRect(cell)
            p.drawLine(QPointF(cell.left(), cell.bottom()), QPointF(cell.right(), cell.top()))


class ReceiveDialog(QDialog):
    def __init__(self, path: str, taken: set[int], parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Received: {name}").format(name=Path(path).name))
        self.capture = receive.load(path)
        self.taken = taken
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        head = QLabel(self.capture.summary())
        head.setWordWrap(True)
        head.setObjectName("Muted")
        left.addWidget(head)
        self.list = QListWidget()
        self.list.setIconSize(QSize(72, 63))
        for rx in self.capture.pages:
            pg = rx.to_page()
            subs = len(rx.subpages)
            text = (f"P{rx.number:03X}  ·  " + self.tr("%n subpage(s)", "", subs) + "  ·  "
                    + self.tr("{received} received").format(received=rx.receptions) + "\n"
                    + self.tr("%n damaged cell(s)", "", rx.damaged)
                    + ("  ·  " + self.tr("number taken") if rx.number in taken else ""))
            it = QListWidgetItem(QIcon(thumbnail(pg, width=72)), text)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if rx.damaged == 0 and rx.number not in taken
                             else Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, rx)
            self.list.addItem(it)
        self.list.currentItemChanged.connect(lambda it, _o: self._show(it))
        left.addWidget(self.list, 1)
        holder = QWidget()
        holder.setLayout(left)
        holder.setFixedWidth(340)
        lay.addWidget(holder)
        right = QVBoxLayout()
        self.view = DamageView()
        right.addWidget(self.view, 1)
        self.sub_label = QLabel("")
        self.prev = Button("‹", small=True)
        self.next = Button("›", small=True)
        self.prev.clicked.connect(lambda: self._step(-1))
        self.next.clicked.connect(lambda: self._step(1))
        note = QLabel(self.tr("Damaged cells (no copy arrived with good parity) are framed in red; a damaged control "
                              "code is shown as a space. Marks stay in the editor until the cell is typed over."))
        note.setObjectName("Faint")
        note.setWordWrap(True)
        right.addLayout(hbox(self.prev, self.sub_label, self.next, None))
        right.addWidget(note)
        self.ok = Button(self.tr("Import ticked pages"), "check", kind="primary")
        cancel = Button(self.tr("Cancel"))
        self.ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        right.addLayout(hbox(None, cancel, self.ok))
        lay.addLayout(right, 1)
        self.sub_index = 0
        if self.list.count():
            self.list.setCurrentRow(0)
        self.resize(1200, 760)

    def _show(self, it):
        if it is None:
            return
        self.sub_index = 0
        self._draw()

    def _step(self, d):
        it = self.list.currentItem()
        if it is None:
            return
        rx = it.data(Qt.ItemDataRole.UserRole)
        self.sub_index = (self.sub_index + d) % len(rx.subpages)
        self._draw()

    def _draw(self):
        rx = self.list.currentItem().data(Qt.ItemDataRole.UserRole)
        s = rx.subpages[self.sub_index]
        self.view.show_subpage(rx.number, s)
        self.sub_label.setText(self.tr("subpage {index}/{total}  ·  SC {code}  ·  {received} received  ·  "
                                       "{damaged} damaged").format(index=self.sub_index + 1, total=len(rx.subpages),
                                                                   code=f"{s.subcode:04X}", received=s.receptions,
                                                                   damaged=len(s.damaged)))

    def chosen(self):
        """The ticked pages as Pages, renumbered where their number is
        taken. Returns (pages, [(old, new)])."""
        out, moved = [], []
        taken = set(self.taken)
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() != Qt.CheckState.Checked:
                continue
            pg = it.data(Qt.ItemDataRole.UserRole).to_page()
            if pg.number in taken:
                n = pg.number
                while (n in taken or (n & 0xFF) > 0x99) and n < (pg.number | 0xFF):
                    n += 1
                if n in taken or (n & 0xFF) == 0xFF:
                    continue
                moved.append((pg.number, n))
                pg.number = n
            taken.add(pg.number)
            out.append(pg)
        return out, moved
