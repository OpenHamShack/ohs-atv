"""The subtitle editor: a mode of the main window (View > Subtitle editor,
Ctrl+U) that swaps the page editor for a list of cues, the cue being edited
and page 888 as a set shows it.

The preview is the ordinary Preview widget on a page made from the cue by
ohstxt.subtitles - the same decoder and renderer as every page - so what is
seen is boxed double-height text over the picture, not a drawing of it.
Play runs the clock through the cues at their own times. Undo keeps whole
track snapshots, as the page editor keeps pages.
"""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QFileDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import subtitles as st
from ..editor import Document
from . import theme
from .controls import Button, Card, hbox
from .panels import PenSwatch
from .preview import Preview

UNDO_DEPTH = 200


def parse_stamp(text: str) -> float | None:
    """'00:01:02,500', '1:02.5', '62.5' -> seconds."""
    t = text.strip().replace(",", ".")
    try:
        parts = [float(p) for p in t.split(":")]
    except ValueError:
        return None
    if not parts or len(parts) > 3 or any(p < 0 for p in parts):
        return None
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s


def stamp(t: float) -> str:
    return st._stamp(t)


class SubtitleDoc:
    """The track being edited, which cue is current, and undo."""

    def __init__(self, track: st.Track | None = None):
        self.track = track or st.Track()
        self.index = 0
        self.undo_stack: list[tuple[st.Track, int]] = []
        self.redo_stack: list[tuple[st.Track, int]] = []
        self.dirty = False
        self.path: str = ""

    @property
    def cue(self) -> st.Cue | None:
        if not self.track.cues:
            return None
        self.index = max(0, min(self.index, len(self.track.cues) - 1))
        return self.track.cues[self.index]

    def checkpoint(self):
        self.undo_stack.append((self.track.copy(), self.index))
        del self.undo_stack[:-UNDO_DEPTH]
        self.redo_stack.clear()
        self.dirty = True

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append((self.track.copy(), self.index))
        self.track, self.index = self.undo_stack.pop()
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        self.undo_stack.append((self.track.copy(), self.index))
        self.track, self.index = self.redo_stack.pop()
        return True

    def add(self):
        """A new cue after the current one (or at the end), two seconds long."""
        self.checkpoint()
        cur = self.cue
        start = cur.end + 0.2 if cur else 0.0
        self.track.cues.insert(self.index + 1 if cur else 0, st.Cue(start, start + 2.0, [st.Line("")]))
        self.index = self.index + 1 if cur else 0

    def delete(self):
        if self.cue is None:
            return
        self.checkpoint()
        del self.track.cues[self.index]

    def set_times(self, start: float, end: float):
        c = self.cue
        if c is None or (c.start, c.end) == (start, end):
            return
        self.checkpoint()
        c.start, c.end = start, end
        moved = c
        self.track.cues.sort(key=lambda x: x.start)
        self.index = self.track.cues.index(moved)

    def set_line(self, k: int, text: str | None = None, colour: int | None = None):
        """Line k (0 or 1) of the current cue; an empty second line is removed."""
        c = self.cue
        if c is None:
            return
        old = (c.lines[k].text, c.lines[k].colour) if k < len(c.lines) else ("", 7)
        new = (old[0] if text is None else text, old[1] if colour is None else colour)
        if new == old:
            return
        self.checkpoint()                          # before anything changes, or undo keeps the change
        while len(c.lines) <= k:
            c.lines.append(st.Line(""))
        c.lines[k].text, c.lines[k].colour = new
        while len(c.lines) > 1 and not c.lines[-1].text:
            c.lines.pop()


class LineEditor(QWidget):
    """One subtitle line: its text and the speaker's colour."""
    edited = pyqtSignal()

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lab = QLabel(label)
        lab.setObjectName("Muted")
        lab.setFixedWidth(46)
        lay.addWidget(lab)
        self.text = QLineEdit()
        self.text.setFont(theme.font(14, mono=True))
        self.text.setMaxLength(60)
        self.text.textEdited.connect(lambda _t: self.edited.emit())
        lay.addWidget(self.text, 1)
        self.swatches = []
        for col in (7, 3, 6, 2, 5, 1, 4):
            s = PenSwatch(col)
            s.picked.connect(self._pick)
            self.swatches.append(s)
            lay.addWidget(s)
        self.count = QLabel("")
        self.count.setFixedWidth(30)
        self.count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.count)
        self.colour = 7

    def _pick(self, col):
        self.set_colour(col)
        self.edited.emit()

    def set_colour(self, col):
        self.colour = col
        for s in self.swatches:
            s.selected = s.colour == col
            s.update()

    def set_line(self, ln: st.Line | None):
        self.text.blockSignals(True)
        self.text.setText(ln.text if ln else "")
        self.text.blockSignals(False)
        self.set_colour(ln.colour if ln else 7)
        self.show_count()

    def show_count(self):
        n = len(self.text.text())
        self.count.setText(str(n))
        self.count.setStyleSheet(f"color: {theme.t()['warn'] if n > st.MAX_CHARS else theme.t()['text3']};")


class SubtitleView(QWidget):
    """The subtitle editor's whole body."""
    message = pyqtSignal(str, str)
    changed = pyqtSignal()

    def __init__(self, prefs, parent=None):
        super().__init__(parent)
        self.prefs = prefs
        self.doc = SubtitleDoc()
        self.playing = False
        self.play_t = 0.0
        self._play_t0 = 0.0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        self.cues_card = Card(self.tr("Cues"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([self.tr("Start"), self.tr("End"), self.tr("Text"), ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.currentCellChanged.connect(lambda r, _c, _pr, _pc: self._pick(r))
        self.cues_card.lay.addWidget(self.table, 1)
        self.b_import = Button(self.tr("Import SRT"), "folder", small=True)
        self.b_export = Button(self.tr("Export SRT"), "export", small=True)
        self.b_add = Button(self.tr("Add cue"), "plus", small=True)
        self.b_del = Button(self.tr("Delete"), "trash", small=True)
        self.cues_card.lay.addLayout(hbox(self.b_add, self.b_del, None, self.b_import, self.b_export))
        self.file_label = QLabel("")
        self.file_label.setObjectName("Faint")
        self.cues_card.lay.addWidget(self.file_label)
        lay.addWidget(self.cues_card, 1)

        right = QVBoxLayout()
        right.setSpacing(12)
        self.preview_card = Card(self.tr("Page 888"))
        self.preview = Preview()
        self.preview.setMinimumHeight(380)
        self.pdoc = Document(st.cue_page(None))
        self.preview.set_document(self.pdoc)
        self.b_play = Button(self.tr("Play"), "play", small=True, checkable=True)
        self.clock = QLabel("00:00:00,000")
        self.clock.setFont(theme.font(13, mono=True))
        self.preview_card.lay.addWidget(self.preview, 1)
        self.preview_card.lay.addLayout(hbox(self.b_play, self.clock, None))
        right.addWidget(self.preview_card, 1)

        self.cue_card = Card(self.tr("Cue"))
        g = QGridLayout()
        self.start = QLineEdit()
        self.end = QLineEdit()
        for w in (self.start, self.end):
            w.setFont(theme.font(14, mono=True))
            w.setFixedWidth(150)
            w.editingFinished.connect(self._times)
        g.addWidget(QLabel(self.tr("Start")), 0, 0)
        g.addWidget(self.start, 0, 1)
        g.addWidget(QLabel(self.tr("End")), 0, 2)
        g.addWidget(self.end, 0, 3)
        g.setColumnStretch(4, 1)
        self.cue_card.lay.addLayout(g)
        self.lines = [LineEditor(self.tr("Line 1")), LineEditor(self.tr("Line 2"))]
        for k, le in enumerate(self.lines):
            le.edited.connect(lambda k=k: self._line(k))
            self.cue_card.lay.addWidget(le)
        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet(f"color: {theme.t()['warn']};")
        self.cue_card.lay.addWidget(self.warn)
        note = QLabel(self.tr("Colours are the speakers'. A line holds 37 characters; a cue, two lines. Exported SRT "
                              "is the subset OpenHamShack ATV reads for page 888."))
        note.setObjectName("Faint")
        note.setWordWrap(True)
        self.cue_card.lay.addWidget(note)
        right.addWidget(self.cue_card)
        holder = QWidget()
        holder.setLayout(right)
        holder.setFixedWidth(660)
        lay.addWidget(holder)

        self.b_add.clicked.connect(lambda: self._do(self.doc.add))
        self.b_del.clicked.connect(lambda: self._do(self.doc.delete))
        self.b_import.clicked.connect(self.import_srt)
        self.b_export.clicked.connect(self.export_srt)
        self.b_play.clicked.connect(lambda: self.set_playing(not self.playing))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.load()

    # ------------------------------------------------------------ track
    def set_track(self, track: st.Track, path: str = ""):
        self.doc = SubtitleDoc(track)
        self.doc.path = path
        self.load()
        self.changed.emit()

    def _do(self, fn):
        fn()
        self.load()
        self.changed.emit()

    def load(self):
        """Everything from the document: the table, the cue, the preview."""
        d = self.doc
        self.table.blockSignals(True)
        self.table.setRowCount(len(d.track.cues))
        for i, c in enumerate(d.track.cues):
            w = st.warnings(d.track, i)
            text = "  /  ".join(ln.text for ln in c.lines)
            for col, v in enumerate((stamp(c.start), stamp(c.end), text, "⚠" if w else "")):
                it = QTableWidgetItem(v)
                if col == 2 and c.lines:
                    it.setForeground(theme.QColor(st.HEX[c.lines[0].colour]) if theme.t()["name"] == "dark"
                                     else theme.c("text"))
                if col == 3 and w:
                    it.setForeground(theme.c("warn"))
                    it.setToolTip("\n".join(w))
                self.table.setItem(i, col, it)
        if d.track.cues:
            self.table.setCurrentCell(d.index, 2)
        self.table.blockSignals(False)
        self.file_label.setText((d.path or self.tr("not saved to a file yet")) + (self.tr("  ·  changed") if d.dirty else ""))
        self.show_cue()

    def show_cue(self):
        c = self.doc.cue
        for w in (self.start, self.end):
            w.setEnabled(c is not None)
        self.start.setText(stamp(c.start) if c else "")
        self.end.setText(stamp(c.end) if c else "")
        for k, le in enumerate(self.lines):
            le.setEnabled(c is not None)
            le.set_line(c.lines[k] if c and k < len(c.lines) else None)
        w = st.warnings(self.doc.track, self.doc.index) if c else []
        self.warn.setText("\n".join(w))
        self.cue_card.set_title(self.tr("Cue {index} of {total}").format(index=self.doc.index + 1, total=len(self.doc.track.cues))
                                if c else self.tr("Cue"))
        self.show_on_set(c)

    def show_on_set(self, cue):
        self.pdoc.page.subpages[0].rows = st.cue_rows(cue)
        self.preview.follow()

    def _pick(self, r):
        if 0 <= r < len(self.doc.track.cues) and r != self.doc.index:
            self.doc.index = r
            self.show_cue()

    def _times(self):
        a, b = parse_stamp(self.start.text()), parse_stamp(self.end.text())
        c = self.doc.cue
        if c is None:
            return
        if a is None or b is None:
            self.message.emit("Times are hh:mm:ss,mmm (or seconds)", "warn")
            self.show_cue()
            return
        self.doc.set_times(a, b)
        self.load()
        self.changed.emit()

    def _line(self, k):
        le = self.lines[k]
        self.doc.set_line(k, le.text.text(), le.colour)
        le.show_count()
        # the table row and the preview follow typing without rebuilding the editor under the cursor
        c = self.doc.cue
        if c is not None:
            it = self.table.item(self.doc.index, 2)
            if it:
                it.setText("  /  ".join(ln.text for ln in c.lines))
            w = st.warnings(self.doc.track, self.doc.index)
            self.warn.setText("\n".join(w))
            mark = self.table.item(self.doc.index, 3)
            if mark:
                mark.setText("⚠" if w else "")
            self.show_on_set(c)
        self.changed.emit()

    def undo(self) -> bool:
        ok = self.doc.undo()
        if ok:
            self.load()
        return ok

    def redo(self) -> bool:
        ok = self.doc.redo()
        if ok:
            self.load()
        return ok

    # ------------------------------------------------------------ files
    def import_srt(self, path: str | None = None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, self.tr("Import subtitles"), self.prefs["subtitleDir"] or
                                                  str(Path.home()), self.tr("SubRip subtitles (*.srt);;All files (*)"))
            if not path:
                return
        try:
            raw = Path(path).read_bytes()
        except OSError as e:
            self.message.emit(f"Could not read {Path(path).name}: {e.strerror or e}", "warn")
            return
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")         # SRT from older tools is often Windows-1252
        track, problems = st.parse_srt(text)
        self.prefs["subtitleDir"] = str(Path(path).parent)
        self.set_track(track, str(path))
        n = sum(1 for i in range(len(track.cues)) if st.warnings(track, i))
        msg = f"{Path(path).name}: {len(track.cues)} cues" + (f", {n} with warnings" if n else "")
        if problems:
            msg += f"; skipped {len(problems)} block(s) without a timing"
        self.message.emit(msg, "warn" if n or problems else "info")

    def export_srt(self, path: str | None = None):
        if path is None:
            start = self.doc.path or str(Path(self.prefs["subtitleDir"] or Path.home()) / "subtitles.srt")
            path, _ = QFileDialog.getSaveFileName(self, self.tr("Export subtitles"), start, self.tr("SubRip subtitles (*.srt)"))
            if not path:
                return
        p = Path(path)
        tmp = p.with_name("." + p.name + ".tmp")
        try:
            tmp.write_text(st.dumps_srt(self.doc.track), encoding="utf-8", newline="\n")
            tmp.replace(p)
        except OSError as e:
            self.message.emit(f"Not written: {e.strerror or e}", "warn")
            return
        self.doc.path = str(p)
        self.doc.dirty = False
        self.prefs["subtitleDir"] = str(p.parent)
        self.load()
        self.changed.emit()
        self.message.emit(f"Wrote {p.name}: {len(self.doc.track.cues)} cues", "info")

    # ------------------------------------------------------------ playing
    def set_playing(self, on: bool):
        self.playing = on
        self.b_play.set_checked(on)
        if on:
            c = self.doc.cue
            self.play_t = c.start if c else 0.0
            self._play_t0 = time.monotonic() - self.play_t
            self._timer.start(50)
        else:
            self._timer.stop()
            self.show_cue()

    def show_time(self, t: float):
        """What the set shows at t: the cue then, or nothing."""
        self.play_t = t
        self.clock.setText(stamp(t))
        c = self.doc.track.at(t)
        self.show_on_set(c)
        if c is not None:
            i = self.doc.track.cues.index(c)
            if i != self.doc.index:
                self.table.blockSignals(True)
                self.table.setCurrentCell(i, 2)
                self.table.blockSignals(False)
                self.doc.index = i
        return c

    def _tick(self):
        t = time.monotonic() - self._play_t0
        self.show_time(t)
        last = max((c.end for c in self.doc.track.cues), default=0.0)
        if t > last + 1:
            self.set_playing(False)
