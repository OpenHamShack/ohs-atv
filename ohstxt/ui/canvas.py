"""The page editor: the 40 x 25 grid, drawn by the decoder, with a cursor.

What is drawn is the decoder's output (ohstxt.decoder -> ohstxt.render), the
same pixels the preview and the PNG export show - the editor never draws
text itself. On top of that, and only on top: the cursor, an optional cell
grid, and the code view, which labels every control cell with a
two-letter mnemonic (AR alpha red, GB graphics blue, Dh double height...)
in the code's own colour, because on a real set those cells are blank and
a page cannot be edited without seeing where its codes are.

Pixels are 1.2 times as wide as they are high, as a 4:3 set draws them
(render.TV_ASPECT), so what is drawn here has the proportions of the air.
Flash is shown steady here (the preview animates it); conceal follows the
Reveal switch.

Keys, in the style of edit.tf (DESIGN.md has the full table):
  typing         type-over at the cursor, which then moves right
  Esc, then key  a control code: r g y b m c w k alpha colours, R G Y B M C
                 W K graphics colours, f flash  F steady, d double height
                 D normal height, n new background  N black background,
                 h hold  H release, s separated  S contiguous, o conceal,
                 [ start box  ] end box. Esc twice cancels.
  q w a s z x    with a drawing tool: toggle that sixel of the cursor cell
                 (q w the top pair, a s the middle, z x the bottom)
  Insert/Delete  insert or delete a cell (the row moves); with Ctrl, a row
  Backspace      clear the cell to the left and move there
  PgUp/PgDn      previous / next subpage
  Shift+arrows   select a block (Ctrl+C / X / V copy, cut and paste it;
                 Delete clears it)
Mouse: click places the cursor; with Text, drag selects a block (Shift+click
extends it); with Pen, drag draws (right button erases); Line and Rect drag
out the shape; Fill fills the run clicked.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from .. import charset, decoder, render
from ..editor import PEN_KEEP, Document, line_points
from ..page import COLS, ROWS
from . import theme

ESC_KEYS = {
    "k": 0x00, "r": 0x01, "g": 0x02, "y": 0x03, "b": 0x04, "m": 0x05, "c": 0x06, "w": 0x07,
    "K": 0x10, "R": 0x11, "G": 0x12, "Y": 0x13, "B": 0x14, "M": 0x15, "C": 0x16, "W": 0x17,
    "f": 0x08, "F": 0x09, "]": 0x0A, "[": 0x0B, "D": 0x0C, "d": 0x0D, "o": 0x18, "S": 0x19, "s": 0x1A,
    "N": 0x1C, "n": 0x1D, "h": 0x1E, "H": 0x1F,
}
SIXEL_KEYS = {"q": (0, 0), "w": (1, 0), "a": (0, 1), "s": (1, 1), "z": (0, 2), "x": (1, 2)}
TOOLS = [("text", "Text"), ("pen", "Pen"), ("line", "Line"), ("rect", "Rect"), ("fill", "Fill")]


def to_qimage(rgb) -> QImage:
    h, w = rgb.shape[:2]
    data = rgb.copy(order="C").tobytes()
    img = QImage(data, w, h, 3 * w, QImage.Format.Format_RGB888)
    return img.copy()        # own the pixels: `data` goes out of scope


def editor_rows(doc: Document) -> list:
    """Rows as the editor shows them: row 0's first eight cells as the set
    would fill them (the page number), the header template left unexpanded
    so its %% tokens can be edited."""
    rows = [bytearray(r) for r in doc.sub.rows]
    lead = f" P{doc.page.magazine}{doc.page.number & 0xFF:02X}   ".encode()[:8]
    rows[0][:8] = lead
    return rows


class PageCanvas(QWidget):
    changed = pyqtSignal()           # the page's bytes changed
    cursorMoved = pyqtSignal()
    message = pyqtSignal(str, str)   # text, level ("info" / "warn")
    subpageChanged = pyqtSignal()

    def __init__(self, doc: Document, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.tool = "text"
        self.pen = PEN_KEEP
        self.rect_filled = False
        self.show_codes = False
        self.grid = False
        self.reveal = False
        self.esc_pending = False
        self.drag_from: tuple[int, int] | None = None
        self.drag_to: tuple[int, int] | None = None
        self.drag_on = True
        self.hover: tuple[int, int] | None = None
        self._img: QImage | None = None
        self._dec = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(480, 420)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.blink = True
        self._blink = QTimer(self)
        self._blink.timeout.connect(self._tick)
        self._blink.start(530)

    # ------------------------------------------------------------ state
    def set_document(self, doc: Document):
        self.doc = doc
        self.refresh()

    def refresh(self):
        """Re-decode and redraw (after any change to the page)."""
        self._img = None
        self.update()

    def edited(self):
        self.refresh()
        self.changed.emit()

    def _tick(self):
        self.blink = not self.blink
        if self.hasFocus():
            self.update(self.cell_rect(self.doc.row, self.doc.col).toAlignedRect().adjusted(-3, -3, 3, 3))

    def decoded(self):
        if self._img is None:
            self._dec = decoder.decode(editor_rows(self.doc), black_fg=self.doc.black_fg)
            rgb = render.render(self._dec, flash_phase_on=True, reveal=self.reveal)
            self._img = to_qimage(rgb)
        return self._dec

    # ------------------------------------------------------------ geometry
    def page_rect(self) -> QRectF:
        pw, ph = render.PAGE_W * render.TV_ASPECT, render.PAGE_H
        m = 8
        s = min((self.width() - 2 * m) / pw, (self.height() - 2 * m) / ph)
        w, h = pw * s, ph * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def cell_rect(self, r: int, c: int) -> QRectF:
        pr = self.page_rect()
        cw, ch = pr.width() / COLS, pr.height() / ROWS
        return QRectF(pr.x() + c * cw, pr.y() + r * ch, cw, ch)

    def cell_at(self, pos: QPointF) -> tuple[int, int] | None:
        pr = self.page_rect()
        if not pr.contains(pos):
            return None
        c = int((pos.x() - pr.x()) / pr.width() * COLS)
        r = int((pos.y() - pr.y()) / pr.height() * ROWS)
        return min(ROWS - 1, r), min(COLS - 1, c)

    def sixel_at(self, pos: QPointF) -> tuple[int, int] | None:
        pr = self.page_rect()
        if not pr.contains(pos):
            return None
        fx = (pos.x() - pr.x()) / pr.width() * COLS * 2
        # Sixel rows are 6, 8 and 6 dots of a 20-dot cell, not thirds.
        fy = (pos.y() - pr.y()) / pr.height() * ROWS
        r = int(fy)
        dot = (fy - r) * 20
        sy = 3 * r + (0 if dot < 6 else 1 if dot < 14 else 2)
        return min(COLS * 2 - 1, int(fx)), min(ROWS * 3 - 1, sy)

    def sixel_rect(self, sx: int, sy: int) -> QRectF:
        cr = self.cell_rect(sy // 3, sx // 2)
        k = sy % 3
        y0, y1 = ((0, 6), (6, 14), (14, 20))[k]
        hw = cr.width() / 2
        return QRectF(cr.x() + (sx % 2) * hw, cr.y() + cr.height() * y0 / 20, hw, cr.height() * (y1 - y0) / 20)

    # ------------------------------------------------------------ painting
    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), theme.c("scope"))
        d = self.decoded()
        pr = self.page_rect()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(pr, self._img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.grid:
            p.setPen(QPen(QColor(255, 255, 255, 34), 1))
            for c in range(1, COLS):
                x = pr.x() + pr.width() * c / COLS
                p.drawLine(QPointF(x, pr.y()), QPointF(x, pr.bottom()))
            for r in range(1, ROWS):
                y = pr.y() + pr.height() * r / ROWS
                p.drawLine(QPointF(pr.x(), y), QPointF(pr.right(), y))
        if self.show_codes:
            self._paint_codes(p, d)
        if self.doc.page.live is not None:
            self._paint_tokens(p)
        if self.doc.sub.damaged:
            self._paint_damage(p)
        # the receiver-owned part of row 0
        p.fillRect(QRectF(pr.x(), pr.y(), pr.width() * 8 / COLS, pr.height() / ROWS), QColor(0, 0, 0, 90))
        if self.tool != "text":
            self._paint_tool(p)
        self._paint_selection(p)
        self._paint_cursor(p)
        if self.esc_pending:
            p.setFont(theme.font(12, QFont.Weight.Bold, mono=True))
            r = QRectF(pr.right() - 70, pr.y() - 0, 66, 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.c("accent"))
            p.drawRoundedRect(r, 5, 5)
            p.setPen(theme.c("accentText"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.tr("ESC …"))

    def _paint_codes(self, p: QPainter, d):
        rows = editor_rows(self.doc)
        size = max(7.0, self.cell_rect(0, 0).height() * 0.42)
        p.setFont(theme.font(size, QFont.Weight.Bold, mono=True))
        for r in range(ROWS):
            if d.hidden_row[r]:
                # the codes of a row hidden by double height above are not
                # in effect; show them faintly so they can still be found
                pass
            for c in range(COLS):
                code = rows[r][c]
                if code >= 0x20:
                    continue
                cr = self.cell_rect(r, c).adjusted(1, 1, -1, -1)
                col = QColor(*render.PALETTE[code & 7]) if (code <= 0x07 or 0x10 <= code <= 0x17) else \
                    theme.c("accent")
                if (code & 7) == 0 and (code <= 0x07 or 0x10 <= code <= 0x17):
                    col = QColor(140, 140, 140)
                bg = QColor(0, 0, 0, 170)
                p.setPen(QPen(col, 1))
                p.setBrush(bg)
                p.setOpacity(0.45 if d.hidden_row[r] else 1.0)
                p.drawRoundedRect(cr, 2, 2)
                p.setPen(col)
                p.drawText(cr, Qt.AlignmentFlag.AlignCenter, decoder.CONTROL_SHORT.get(code, "??"))
                p.setOpacity(1.0)

    def _paint_selection(self, p: QPainter):
        sel = self.doc.selection()
        if sel is None:
            return
        t, l, b, r = sel
        rect = self.cell_rect(t, l).united(self.cell_rect(b, r))
        fill = theme.c("accent")
        fill.setAlpha(60)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRect(rect)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(theme.c("accent"), 1.5, Qt.PenStyle.DashLine))
        p.drawRect(rect)

    def _paint_damage(self, p: QPainter):
        """Cells that arrived with errors (an imported capture): a red
        frame and a diagonal, so a damaged space is seen too."""
        pen = QPen(QColor(255, 60, 80), 1.4)
        for r, c in self.doc.sub.damaged:
            cr = self.cell_rect(r, c).adjusted(1, 1, -1, -1)
            p.setPen(pen)
            p.setBrush(QColor(255, 60, 80, 60))
            p.drawRect(cr)
            p.drawLine(cr.bottomLeft(), cr.topRight())

    def _paint_tokens(self, p: QPainter):
        """A live page's fields: on a set the braces would be ¼ and ¾, so
        each token is drawn over as its name in a tinted box the width of
        the field - where the runner will put the value."""
        from ..live.template import tokens_in
        rows = self.doc.sub.rows
        col = theme.c("beacon")
        fill = QColor(34, 18, 60)              # opaque: the ¼name¾ glyphs underneath must not show through
        size = max(7.0, self.cell_rect(0, 0).height() * 0.5)
        p.setFont(theme.font(size, QFont.Weight.Bold, mono=True))
        for t in tokens_in(rows):
            whole = self.cell_rect(t.row, t.col).united(self.cell_rect(t.row, t.col + t.width - 1))
            p.fillRect(whole, fill)
            r = whole.adjusted(1, 1, -1, -1)
            p.setPen(QPen(col, 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 3, 3)
            p.setPen(col)
            align = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if t.right else
                                                     Qt.AlignmentFlag.AlignLeft)
            p.drawText(r.adjusted(3, 0, -3, 0), align, t.name)

    def _paint_cursor(self, p: QPainter):
        cr = self.cell_rect(self.doc.row, self.doc.col)
        if self.hasFocus() and not self.blink and self.tool == "text":
            return
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0), 3))
        p.drawRect(cr.adjusted(-1, -1, 1, 1))
        p.setPen(QPen(theme.c("accent") if self.hasFocus() else QColor(200, 200, 200), 2))
        p.drawRect(cr.adjusted(-1, -1, 1, 1))

    def _paint_tool(self, p: QPainter):
        pts = []
        if self.drag_from and self.drag_to and self.tool in ("line", "rect"):
            (x0, y0), (x1, y1) = self.drag_from, self.drag_to
            if self.tool == "line":
                pts = list(line_points(x0, y0, x1, y1))
            else:
                xa, xb = sorted((x0, x1))
                ya, yb = sorted((y0, y1))
                pts = [(x, y) for y in range(ya, yb + 1) for x in range(xa, xb + 1)
                       if self.rect_filled or x in (xa, xb) or y in (ya, yb)]
        elif self.hover:
            pts = [self.hover]
        if not pts:
            return
        col = theme.c("accent")
        col.setAlpha(150 if self.drag_on else 90)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        for x, y in pts:
            p.drawRect(self.sixel_rect(x, y).adjusted(1, 1, -1, -1))

    # ------------------------------------------------------------ keyboard
    def keyPressEvent(self, e):
        doc = self.doc
        k = e.key()
        mods = e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        text = e.text()
        if self.esc_pending:
            self.esc_pending = False
            if k == Qt.Key.Key_Escape:
                self.update()
                return
            code = ESC_KEYS.get(text)
            if code is not None:
                doc.put(code)
                self.message.emit(f"{decoder.CONTROL_NAMES[code]} at row {doc.row}", "info")
                self._moved()
                self.edited()
            else:
                self.message.emit(f"Esc {text or '?'}: no control code on that key", "warn")
                self.update()
            return
        if k == Qt.Key.Key_Escape and not ctrl:
            self.esc_pending = True
            self.update()
            return
        moves = {Qt.Key.Key_Left: (0, -1), Qt.Key.Key_Right: (0, 1), Qt.Key.Key_Up: (-1, 0), Qt.Key.Key_Down: (1, 0)}
        if k in moves:
            if mods & Qt.KeyboardModifier.ShiftModifier:
                doc.extend_selection(*moves[k])
            else:
                doc.clear_selection()
                doc.move(*moves[k])
            self._moved()
            return
        if doc.selection() is not None and k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not ctrl:
            doc.clear_block()
            self.edited()
            return
        if k == Qt.Key.Key_Home:
            doc.goto(doc.row, 0)
            self._moved()
            return
        if k == Qt.Key.Key_End:
            row = doc.sub.rows[doc.row]
            last = max((i for i in range(COLS) if row[i] != 0x20), default=-1)
            doc.goto(doc.row, min(COLS - 1, last + 1))
            self._moved()
            return
        if k in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            n = len(doc.page.subpages)
            doc.sub_index = (doc.sub_index + (1 if k == Qt.Key.Key_PageDown else -1)) % n
            self.refresh()
            self.subpageChanged.emit()
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            doc.goto(doc.row + 1, 0)
            self._moved()
            return
        if k == Qt.Key.Key_Backspace:
            doc.backspace()
            self._moved()
            self.edited()
            return
        if k == Qt.Key.Key_Delete:
            doc.delete_row() if ctrl else doc.delete_cell()
            self.edited()
            return
        if k == Qt.Key.Key_Insert:
            doc.insert_row() if ctrl else doc.insert_cell()
            self.edited()
            return
        if ctrl or not text or not text.isprintable():
            super().keyPressEvent(e)
            return
        doc.clear_selection()
        if self.tool != "text" and text in SIXEL_KEYS:
            sx, sy = SIXEL_KEYS[text]
            gx, gy = 2 * doc.col + sx, 3 * doc.row + sy
            on = not doc.sixel_on(gx, gy)
            from ..editor import Stroke
            st = Stroke()
            doc.set_sixel(gx, gy, on, self.pen, st)
            self._report(st)
            self.edited()
            return
        doc.type_text(text)
        self._moved()
        self.edited()

    def inputMethodEvent(self, e):
        # Composed characters (£ on a keyboard that dead-keys it) arrive here.
        s = e.commitString()
        if s:
            self.doc.type_text(s)
            self._moved()
            self.edited()

    def _moved(self):
        self.blink = True
        self.update()
        self.cursorMoved.emit()

    def _report(self, st):
        msg = st.message()
        if msg:
            self.message.emit(msg, "warn")
        elif st.changed:
            self.message.emit("", "info")

    # ------------------------------------------------------------ mouse
    def mousePressEvent(self, e):
        self.setFocus()
        pos = e.position()
        cell = self.cell_at(pos)
        if cell is None:
            return
        if self.tool == "text" and e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            if self.doc.anchor is None:
                self.doc.anchor = (self.doc.row, self.doc.col)
            self.doc.goto(*cell)
            self._moved()
            return
        self.doc.clear_selection()
        self.doc.goto(*cell)
        self._press_cell = (self.doc.row, self.doc.col)
        self._moved()
        if self.tool == "text":
            return
        sx = self.sixel_at(pos)
        on = e.button() != Qt.MouseButton.RightButton
        self.drag_on = on
        if self.tool == "pen":
            self.doc.begin()
            self._stroke_acc = None
            from ..editor import Stroke
            self._stroke = Stroke()
            self.doc.set_sixel(*sx, on, self.pen, self._stroke)
            self._last = sx
            self.edited()
        elif self.tool in ("line", "rect"):
            self.drag_from = self.drag_to = sx
            self.update()
        elif self.tool == "fill":
            st = self.doc.fill(*sx, on=on, pen=self.pen)
            self._report(st)
            if not st.changed and not st.refused:
                self.message.emit("Nothing to fill there", "info")
            self.edited()

    def mouseMoveEvent(self, e):
        pos = e.position()
        sx = self.sixel_at(pos)
        if self.tool != "text":
            self.hover = sx
        buttons = e.buttons()
        if self.tool == "text" and buttons & Qt.MouseButton.LeftButton and getattr(self, "_press_cell", None):
            cell = self.cell_at(pos)
            if cell is not None and (cell != (self.doc.row, self.doc.col) or self.doc.anchor is not None):
                if self.doc.anchor is None:
                    self.doc.anchor = self._press_cell
                self.doc.goto(*cell)
                self._moved()
            return
        if sx is not None and self.tool == "pen" and buttons & (Qt.MouseButton.LeftButton |
                                                                 Qt.MouseButton.RightButton):
            last = getattr(self, "_last", sx) or sx
            for x, y in line_points(last[0], last[1], sx[0], sx[1]):
                self.doc.set_sixel(x, y, self.drag_on, self.pen, self._stroke)
            self._last = sx
            self.edited()
        elif sx is not None and self.drag_from is not None:
            self.drag_to = sx
        self.update()

    def mouseReleaseEvent(self, e):
        self._press_cell = None
        if self.tool == "pen" and hasattr(self, "_stroke"):
            self.doc.end()
            self._report(self._stroke)
            del self._stroke
        if self.drag_from is not None and self.drag_to is not None:
            (x0, y0), (x1, y1) = self.drag_from, self.drag_to
            if self.tool == "line":
                st = self.doc.line(x0, y0, x1, y1, on=self.drag_on, pen=self.pen)
            else:
                st = self.doc.rect(x0, y0, x1, y1, on=self.drag_on, filled=self.rect_filled, pen=self.pen)
            self._report(st)
            self.drag_from = self.drag_to = None
            self.edited()

    def leaveEvent(self, e):
        self.hover = None
        self.update()
