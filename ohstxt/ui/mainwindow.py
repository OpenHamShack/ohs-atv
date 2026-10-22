"""The main window: builds the layout, holds the service and one Document
per open page, and owns what spans widgets - files, export, undo, the
shortcuts.

Layout (DESIGN.md "The window"):

  top bar   mark | page title | undo redo | codes grid reveal | import link export save | theme help
  body      service list  | editor (the hero) + cell line | preview (a 4:3 set, live)
            (thumbnails)  |                               | tools and the code palette
                          |                               | page, subpages, Fastext
  footer    status strip

Each page keeps its own Document, so its undo history survives moving to
another page and back. Panels are refreshed through one short timer after
an edit rather than on every keystroke: a burst of typing costs one
thumbnail, not one per letter.
"""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import QByteArray, QRectF, Qt, QTimer
from PyQt6.QtGui import QAction, QKeySequence, QPainter
from PyQt6.QtWidgets import (QFileDialog, QHBoxLayout, QMainWindow, QMessageBox, QScrollArea, QStackedWidget,
                             QVBoxLayout, QWidget)

from .. import __version__, check, clipboard, decoder, edittf, header, live, render, samples, station, t42
from ..editor import Document
from ..page import Page, Subpage
from ..service import Service
from . import appicon, theme
from .canvas import PageCanvas
from .chrome import IconButton, ShortcutOverlay, StatusStrip
from .controls import Card
from .dialogs import ImageDialog, LinkDialog, NumberDialog, open_image
from .panels import CellInfo, FastextPanel, PagePanel, ServiceList, SubpagePanel, ToolPanel
from .preview import Preview, display_rows
from .prefs import data_dir

GAP = 12


class Mark(QWidget):
    """The app mark: the icon, OPENHAMSHACK TELETEXT, and the page being edited."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sub = "teletext page editor"
        self.setFixedHeight(56)
        self.setMinimumWidth(320)

    def set_sub(self, text: str):
        self.sub = text
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        appicon.paint_mark(p, QRectF(0, 6, 44, 44))
        # A point smaller and tighter than the family's 15 px / 2.2: at that
        # size "OPENHAMSHACK TELETEXT" wants 340 px, past the 320 the mark
        # is given.
        p.setFont(theme.font(13, theme.QFont.Weight.Black, spacing=1.2))
        p.setPen(theme.c("text"))
        p.drawText(QRectF(54, 8, self.width() - 54, 22), Qt.AlignmentFlag.AlignVCenter, self.tr("OPENHAMSHACK TELETEXT"))
        p.setFont(theme.font(11.5, theme.QFont.Weight.DemiBold))
        p.setPen(theme.c("text3"))
        p.drawText(QRectF(54, 30, self.width() - 54, 18), Qt.AlignmentFlag.AlignVCenter, self.sub)


def default_service_dir() -> Path:
    return data_dir() / "service"


def ensure_service(folder: Path) -> Service:
    """Open a service folder; a folder that does not exist yet is created
    with the sample pages, so a first run opens on something to look at."""
    folder = Path(folder)
    if not folder.exists():
        folder.mkdir(parents=True, exist_ok=True)
        svc = Service(folder)
        for pg in samples.service():
            svc.save(pg)
    return Service.open(folder)


class MainWindow(QMainWindow):
    def __init__(self, prefs, service: Service, parent=None):
        super().__init__(parent)
        self.prefs = prefs
        self.service = service
        self.docs: dict[int, Document] = {}
        self.station = station.load(service.folder)
        self.board = self._make_board()
        self.live_problems = live.attach_service(service)
        self.check_dlg = None
        self.cur = -1
        self.ask_before_discard = True
        # True when another program's QApplication holds this window
        # (ohstxt.ui.open_editor): the theme then changes this window only.
        self.embedded = False
        self.setWindowTitle(self.tr("OpenHamShack Teletext"))
        self.setWindowIcon(appicon.app_icon())
        self._panels_timer = QTimer(self)
        self._panels_timer.setSingleShot(True)
        self._panels_timer.timeout.connect(self._refresh_panels)
        self._build()
        self.preview.board = self.board
        self._live_timer = QTimer(self)
        self._live_timer.timeout.connect(self._live_tick)
        self._live_timer.start(5000)
        self._menus()
        if not self.service.pages:
            self.service.pages.append(self._blank_page(0x100))
        self.select_page(0)
        geo = prefs["geometry"]
        if geo:
            self.restoreGeometry(QByteArray.fromHex(geo.encode()))

    # ------------------------------------------------------------ layout
    def _build(self):
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(GAP, 4, GAP, 0)
        outer.setSpacing(GAP)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.mark = Mark()
        top.addWidget(self.mark)
        top.addStretch(1)
        self.b_undo = IconButton("undo", self.tr("Undo (Ctrl+Z)"))
        self.b_redo = IconButton("redo", self.tr("Redo (Ctrl+Y)"))
        self.b_codes = IconButton("codes", self.tr("Show control codes (Ctrl+E)"), checkable=True)
        self.b_grid = IconButton("grid", self.tr("Cell grid (Ctrl+G)"), checkable=True)
        self.b_reveal = IconButton("eye", self.tr("Reveal concealed text (Ctrl+R)"), checkable=True)
        self.b_image = IconButton("image", self.tr("Import image (Ctrl+I)"))
        self.b_link = IconButton("link", self.tr("edit.tf link (Ctrl+L)"))
        self.b_export = IconButton("export", self.tr("Export PNG and .t42"))
        self.b_save = IconButton("save", self.tr("Save page (Ctrl+S)"))
        self.b_check = IconButton("check", self.tr("Check service (Ctrl+K)"))
        self.b_station = IconButton("antenna", self.tr("Station settings"))
        self.b_subs = IconButton("film", self.tr("Subtitle editor (Ctrl+U)"), checkable=True)
        self.b_theme = IconButton("theme", self.tr("Light / dark (Ctrl+T)"))
        self.b_help = IconButton("help", self.tr("Shortcuts (F1)"))
        for i, b in enumerate((self.b_undo, self.b_redo, None, self.b_codes, self.b_grid, self.b_reveal, None,
                               self.b_image, self.b_link, self.b_export, self.b_save, None, self.b_check,
                               self.b_station, self.b_subs, None, self.b_theme,
                               self.b_help)):
            if b is None:
                top.addSpacing(14)
            else:
                top.addWidget(b)
        outer.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(GAP)
        self.services = ServiceList()
        self.services.setFixedWidth(300)
        body.addWidget(self.services)

        self.editor_card = Card(self.tr("Page"))
        self.canvas = PageCanvas(Document())
        self.cellinfo = CellInfo()
        self.editor_card.lay.addWidget(self.canvas, 1)
        self.editor_card.lay.addWidget(self.cellinfo)
        body.addWidget(self.editor_card, 1)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(GAP)
        self.preview_card = Card(self.tr("Preview"))
        self.preview = Preview()
        self.play = IconButton("play", self.tr("Play the carousel at its cycle times"), checkable=True)
        self.preview_card.set_header_widget(self.play)
        self.preview_card.lay.addWidget(self.preview)
        self.tools = ToolPanel()
        self.page_panel = PagePanel()
        self.subpanel = SubpagePanel()
        self.fastext = FastextPanel()
        for w in (self.preview_card, self.tools, self.subpanel, self.fastext, self.page_panel):
            rl.addWidget(w)
        rl.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(right)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(430)
        body.addWidget(scroll)
        body_w = QWidget()
        body_w.setLayout(body)
        body.setContentsMargins(0, 0, 0, 0)
        from .subtitleview import SubtitleView
        self.subs = SubtitleView(self.prefs)
        self.stack = QStackedWidget()
        self.stack.addWidget(body_w)
        self.stack.addWidget(self.subs)
        outer.addWidget(self.stack, 1)
        self.status = StatusStrip()
        outer.addWidget(self.status)
        self.overlay = ShortcutOverlay(root)

        # wiring
        self.services.picked.connect(self.select_page)
        self.services.new_btn.clicked.connect(self.new_page)
        self.services.open_btn.clicked.connect(self.open_folder)
        self.services.tmpl_btn.clicked.connect(self.new_from_template)
        self.canvas.changed.connect(self.on_edited)
        self.canvas.cursorMoved.connect(self._cell)
        self.canvas.message.connect(self._message)
        self.canvas.subpageChanged.connect(lambda: (self.subpanel.load(), self._subpage_chosen()))
        for panel in (self.page_panel, self.subpanel, self.fastext):
            panel.edited.connect(self.on_edited)
        self.page_panel.liveToggled.connect(self.set_live)
        self.subpanel.chosen.connect(self._subpage_chosen)
        self.tools.toolPicked.connect(self.set_tool)
        self.tools.penPicked.connect(lambda c: setattr(self.canvas, "pen", c))
        self.tools.filledChanged.connect(lambda on: setattr(self.canvas, "rect_filled", on))
        self.tools.codePicked.connect(self.insert_code)
        self.play.clicked.connect(self._toggle_play)
        self.b_undo.clicked.connect(self.undo)
        self.b_redo.clicked.connect(self.redo)
        self.b_codes.clicked.connect(lambda: self.set_view(codes=not self.canvas.show_codes))
        self.b_grid.clicked.connect(lambda: self.set_view(grid=not self.canvas.grid))
        self.b_reveal.clicked.connect(lambda: self.set_view(reveal=not self.canvas.reveal))
        self.b_image.clicked.connect(self.import_image)
        self.b_link.clicked.connect(self.link_menu)
        self.b_export.clicked.connect(self.export_menu)
        self.b_save.clicked.connect(self.save_page)
        self.b_check.clicked.connect(lambda: self.check_service())
        self.b_station.clicked.connect(self.edit_station)
        self.b_subs.clicked.connect(lambda: self.set_subtitle_mode(self.stack.currentIndex() == 0))
        self.subs.message.connect(self._message)
        self.subs.changed.connect(self._title)
        self.b_theme.clicked.connect(self.toggle_theme)
        self.b_help.clicked.connect(self.overlay.open)
        self.set_view(codes=self.prefs["showCodes"], grid=self.prefs["grid"], reveal=self.prefs["reveal"])

    def _menus(self):
        mb = self.menuBar()

        def act(menu, text, fn, keys=None):
            a = QAction(text, self)
            if keys:
                a.setShortcuts([QKeySequence(k) for k in (keys if isinstance(keys, list) else [keys])])
            a.triggered.connect(fn)
            menu.addAction(a)
            return a

        f = mb.addMenu(self.tr("&File"))
        act(f, self.tr("New page…"), self.new_page, "Ctrl+N")
        act(f, self.tr("New page from template…"), self.new_from_template, "Ctrl+Shift+N")
        act(f, self.tr("Open service folder…"), self.open_folder, "Ctrl+O")
        act(f, self.tr("Open page…"), self.open_page_file)
        act(f, self.tr("Open received .t42…"), self.open_received, "Ctrl+Shift+O")
        f.addSeparator()
        act(f, self.tr("Save page"), self.save_page, "Ctrl+S")
        act(f, self.tr("Save all"), lambda: self.save_all(), "Ctrl+Shift+S")
        f.addSeparator()
        act(f, self.tr("Import image…"), self.import_image, "Ctrl+I")
        act(f, self.tr("Import edit.tf link…"), self.import_link, "Ctrl+L")
        act(f, self.tr("Copy edit.tf link…"), self.export_link)
        f.addSeparator()
        act(f, self.tr("Export PNG…"), self.export_png)
        act(f, self.tr("Export page as .t42…"), lambda: self.export_t42(False))
        act(f, self.tr("Export service as .t42…"), lambda: self.export_t42(True))
        f.addSeparator()
        act(f, self.tr("Delete page…"), self.delete_page)
        act(f, self.tr("Quit"), self.close, "Ctrl+Q")
        e = mb.addMenu(self.tr("&Edit"))
        act(e, self.tr("Undo"), self.undo, "Ctrl+Z")
        act(e, self.tr("Redo"), self.redo, ["Ctrl+Y", "Ctrl+Shift+Z"])
        e.addSeparator()
        act(e, self.tr("Cut"), self.cut, "Ctrl+X")
        act(e, self.tr("Copy"), self.copy, "Ctrl+C")
        act(e, self.tr("Paste"), self.paste, "Ctrl+V")
        act(e, self.tr("Copy as edit.tf link"), self.copy_link)
        act(e, self.tr("Copy as picture"), self.copy_picture)
        act(e, self.tr("Select all rows"), self.select_all, "Ctrl+A")
        e.addSeparator()
        act(e, self.tr("Insert row"), lambda: self._doc_op(self.doc.insert_row))
        act(e, self.tr("Delete row"), lambda: self._doc_op(self.doc.delete_row))
        act(e, self.tr("Clear row"), lambda: self._doc_op(self.doc.clear_row))
        act(e, self.tr("Clear subpage"), lambda: self._doc_op(self.doc.clear_page))
        v = mb.addMenu(self.tr("&View"))
        act(v, self.tr("Control codes"), lambda: self.set_view(codes=not self.canvas.show_codes), "Ctrl+E")
        act(v, self.tr("Cell grid"), lambda: self.set_view(grid=not self.canvas.grid), "Ctrl+G")
        act(v, self.tr("Reveal"), lambda: self.set_view(reveal=not self.canvas.reveal), "Ctrl+R")
        act(v, self.tr("Black foreground (Level 2.5 decoders)"), lambda: self.set_view(black=not self.doc.black_fg))
        act(v, self.tr("Live fields: sample data"), lambda: self.set_live_sample(not self.board.sample))
        act(v, self.tr("Subtitle editor"), lambda: self.set_subtitle_mode(self.stack.currentIndex() == 0), "Ctrl+U")
        act(v, self.tr("Light / dark theme"), self.toggle_theme, "Ctrl+T")
        act(v, self.tr("Full screen"), lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen(), "F11")
        t = mb.addMenu(self.tr("&Tools"))
        act(t, self.tr("Check service…"), lambda: self.check_service(), "Ctrl+K")
        act(t, self.tr("Station settings…"), self.edit_station)
        t.addSeparator()
        for i, (name, label) in enumerate([("text", self.tr("Text")), ("pen", self.tr("Pen")), ("line", self.tr("Line")), ("rect", "Rectangle"),
                                           ("fill", self.tr("Fill"))], 1):
            act(t, label, lambda _=False, n=name: self.set_tool(n), f"Ctrl+{i}")
        pg = mb.addMenu(self.tr("&Page"))
        self.live_menu = pg.addMenu(self.tr("Insert live field"))
        self.live_menu.aboutToShow.connect(lambda: self.live_field_menu(self.live_menu))
        act(pg, self.tr("Live page (filled by the runner)"), lambda: self.set_live(self.doc.page.live is None))
        pg.addSeparator()
        act(pg, self.tr("Add subpage"), lambda: self._sub_op(lambda: self.doc.add_subpage(False)))
        act(pg, self.tr("Duplicate subpage"), lambda: self._sub_op(lambda: self.doc.add_subpage(True)))
        act(pg, self.tr("Delete subpage"), lambda: self._sub_op(self.doc.delete_subpage))
        h = mb.addMenu(self.tr("&Help"))
        act(h, self.tr("Keyboard shortcuts"), self.overlay.open, "F1")
        act(h, self.tr("About OpenHamShack Teletext {version}").format(version=__version__), self.about)

    # ------------------------------------------------------------ pages
    @property
    def doc(self) -> Document:
        return self.canvas.doc

    def doc_for(self, page: Page) -> Document:
        d = self.docs.get(id(page))
        if d is None:
            d = Document(page, black_fg=self.prefs["blackFg"])
            self.docs[id(page)] = d
        return d

    def dirty_pages(self) -> set[int]:
        return {k for k, d in self.docs.items() if d.dirty}

    def select_page(self, i: int):
        if not 0 <= i < len(self.service.pages):
            return
        self.cur = i
        doc = self.doc_for(self.service.pages[i])
        self.canvas.set_document(doc)
        self.preview.set_document(doc)
        self.page_panel.set_document(doc)
        self.subpanel.set_document(doc)
        self.fastext.set_document(doc)
        self.services.populate(self.service, i, self.dirty_pages())
        self._title()
        self._cell()
        self.canvas.setFocus()

    def _blank_page(self, number: int) -> Page:
        s = Subpage()
        s.rows[0] = header.default_row0()
        return Page(number=number, description="", subpages=[s])

    def new_page(self):
        taken = {p.number for p in self.service.pages}
        dlg = NumberDialog(self.service.free_number(), taken, self)
        if not dlg.exec() or dlg.number is None:
            return
        self.add_page(self._blank_page(dlg.number))

    def new_from_template(self):
        from .servicedlg import TemplateDialog
        dlg = TemplateDialog(self.station, {p.number for p in self.service.pages}, self)
        if dlg.exec() and dlg.page is not None:
            self.add_page(dlg.page)
            page = f"P{dlg.page.label()}"
            self._message(self.tr("{page} from the template; live: save it and run python3 -m ohstxt.live").format(page=page)
                          if dlg.page.live is not None else
                          self.tr("{page} from the template").format(page=page), "info")

    def add_page(self, page: Page):
        self.service.pages.append(page)
        self.service.sort()
        d = self.doc_for(page)
        d.dirty = True
        self.select_page(self.service.pages.index(page))

    def delete_page(self):
        pg = self.service.pages[self.cur]
        if self.ask_before_discard:
            if pg.filename:
                text = self.tr("Delete P{page} and its file {file} from the service folder?").format(
                    page=pg.label(), file=pg.filename)
            else:
                text = self.tr("Delete P{page}? It has not been saved yet.").format(page=pg.label())
            r = QMessageBox.question(self, self.tr("Delete page"), text)
            if r != QMessageBox.StandardButton.Yes:
                return
        self.service.remove(pg)
        self.docs.pop(id(pg), None)
        if not self.service.pages:
            self.service.pages.append(self._blank_page(0x100))
        self.select_page(min(self.cur, len(self.service.pages) - 1))

    def open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, self.tr("Open service folder"), str(self.service.folder))
        if folder:
            self.open_service(Path(folder))

    def open_service(self, folder: Path) -> bool:
        if not self.confirm_discard():
            return False
        self.service = Service.open(folder)
        self.station = station.load(folder)
        self.board = self._make_board()
        self.live_problems = live.attach_service(self.service)
        self.docs.clear()
        if not self.service.pages:
            self.service.pages.append(self._blank_page(0x100))
        self.prefs["serviceDir"] = str(folder)
        self.select_page(0)
        self._message(self.tr("Opened {folder} - %n page(s)", "", len(self.service.pages)).format(folder=folder), "info")
        return True

    def open_page_file(self):
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Open page"), str(self.service.folder), self.tr("Teletext pages (*.tti)"))
        if not path:
            return
        p = Path(path)
        if p.parent != self.service.folder and not self.open_service(p.parent):
            return
        for i, pg in enumerate(self.service.pages):
            if pg.filename == p.name:
                self.select_page(i)

    def _make_board(self) -> live.Board:
        """The live data the preview fills tokens with: the runner's last
        values where it has them (its state file), sample data where not -
        or sample data throughout, from View."""
        b = live.Board(self.service.folder, self.station)
        b.preview = True
        b.sample = bool(self.prefs["liveSample"])
        b.load_state()
        if hasattr(self, "preview"):
            self.preview.board = b
        return b

    def _live_tick(self):
        """The runner may have fetched something: the preview of a live
        page shows what it has now."""
        if 0 <= self.cur < len(self.service.pages) and self.service.pages[self.cur].live is not None:
            self.board.load_state()
            self.preview.follow()

    def set_live_sample(self, on: bool):
        self.prefs["liveSample"] = on
        self.board.sample = on
        self.preview.follow()
        self._cell()

    def set_live(self, on: bool):
        """Mark the page live (its tokens filled by the runner) or not."""
        d = self.doc
        if (d.page.live is not None) == on:
            return
        d.checkpoint()
        d.page.live = live.Sidecar() if on else None
        self.on_edited()
        self._refresh_panels()

    def insert_live_field(self, name: str, width: int):
        token = "{" + name + "}"
        if width > len(token):
            token = "{" + name + "~" * (width - len(token)) + "}"
        if self.doc.page.live is None:
            self.set_live(True)
        self.doc.type_text(token)
        self.on_edited()
        self.canvas.setFocus()

    def live_field_menu(self, menu):
        """Page > Insert live field: every source a page can use and its
        fields, each with its value now (or a sample) to judge the width."""
        menu.clear()
        scope = self.board.scope(self.doc.page.live)
        seen_bare = set()
        for name, f in scope:
            sub = menu.addMenu(f"{name}  ({f.source.type_name})")
            sample = f.source.sample()
            for field, what in f.source.fields():
                bare_ok = field not in seen_bare and self.board.lookup(field, scope)[1] is f
                token_name = field if bare_ok else f"{name}.{field}"
                val = self.board._value(f, field) if f.good_at else sample.get(field, "")
                width = max(len(token_name) + 2, min(40, len(str(val or ""))))
                a = sub.addAction(f"{{{token_name}}}   {what}" + (f"   = {val}" if val else ""))
                a.triggered.connect(lambda _=False, n=token_name, w=width: self.insert_live_field(n, w))
            seen_bare |= f.source.field_names()

    def write_page(self, pg: Page):
        """One page to its file - and a live page's template to its sidecar,
        its .tti filled with the runner's last data."""
        if pg.live is not None:
            return live.save_page(self.service.folder, pg, self.board)
        path = self.service.save(pg)
        live.remove_sidecar(self.service.folder, pg)
        return path

    def open_received(self, path: str | None = None, accept: bool | None = None):
        """Pages from a .t42 capture into the service (as new, unsaved
        pages), chosen in a dialog that shows what arrived damaged."""
        from .receivedlg import ReceiveDialog
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, self.tr("Open received material"), self.prefs["exportDir"] or
                                                  str(Path.home()), self.tr("Teletext packets (*.t42);;All files (*)"))
            if not path:
                return None
        try:
            dlg = ReceiveDialog(path, {p.number for p in self.service.pages}, self)
        except OSError as e:
            self._message(self.tr("Could not read {name}: {error}").format(name=Path(path).name, error=e.strerror or e), "warn")
            return None
        ok = dlg.exec() if accept is None else accept
        if ok:
            self.import_received(dlg)
        return dlg

    def import_received(self, dlg):
        pages, moved = dlg.chosen()
        path = dlg.windowTitle().split(": ", 1)[-1]
        for pg in pages:
            self.service.pages.append(pg)
            self.doc_for(pg).dirty = True
        self.service.sort()
        if pages:
            self.select_page(self.service.pages.index(pages[0]))
        if moved:
            note = "; ".join(self.tr("P{old} became P{new}").format(old=f"{a:03X}", new=f"{b:03X}") for a, b in moved)
            text = self.tr("Imported %n page(s) from {path} ({renumbered})", "", len(pages))
        else:
            note, text = "", self.tr("Imported %n page(s) from {path}", "", len(pages))
        self._message(text.format(path=path, renumbered=note), "info")

    def save_page(self):
        pg = self.service.pages[self.cur]
        try:
            path = self.write_page(pg)
        except OSError as e:
            self._message(self.tr("Not saved: {error}").format(error=e), "warn")
            return
        self.doc.dirty = False
        self._message(self.tr("Saved {name}").format(name=path.name), "info")
        self._refresh_panels()

    def save_all(self, checked: bool = True):
        """Save every changed page - after the checker has looked at the
        service, which asks first only if it found errors."""
        if checked:
            rep = self.run_check()
            if rep.count(check.ERROR):
                dlg = self.show_check(rep, saving=self.ask_before_discard)
                if not self.ask_before_discard:
                    dlg.show()                     # tests and scripts: say it, save anyway
                elif not dlg.exec():
                    return
            elif rep.count(check.WARNING):
                self._message(self.tr("Saving; the checker has %n warning(s) (Ctrl+K)", "", rep.count(check.WARNING)), "warn")
        n = 0
        for pg in self.service.pages:
            d = self.docs.get(id(pg))
            if d is not None and d.dirty:
                self.write_page(pg)
                d.dirty = False
                n += 1
        self._message(self.tr("Saved %n page(s)", "", n), "info")
        self._refresh_panels()

    # ------------------------------------------------------------ the service as a whole
    def run_check(self) -> check.Report:
        """Check the pages as they are in the window. The raw files are
        read for the pages that have not changed since they were opened -
        only a file can hold a row longer than 40 cells."""
        raw = {}
        for pg in self.service.pages:
            d = self.docs.get(id(pg))
            if pg.filename and not (d and d.dirty):
                try:
                    raw[pg.filename] = (self.service.folder / pg.filename).read_bytes()
                except OSError:
                    pass
        opts = check.Options(callsign=self.station.callsign, lines=self.station.vbi_lines)
        return check.check_pages(self.service.pages, raw, opts)

    def show_check(self, rep: check.Report, saving: bool = False):
        from .servicedlg import CheckDialog
        if self.check_dlg is None:
            self.check_dlg = CheckDialog(self)
            self.check_dlg.jump.connect(self.jump_to)
            self.check_dlg.recheck.connect(lambda: self.check_dlg.show_report(self.run_check()))
        self.check_dlg.set_saving(saving)
        self.check_dlg.show_report(rep)
        return self.check_dlg

    def check_service(self):
        rep = self.run_check()
        dlg = self.show_check(rep)
        dlg.show()
        dlg.raise_()
        self._message(rep.summary(), "warn" if rep.count(check.ERROR) else "info")
        return dlg

    def jump_to(self, f: check.Finding):
        """Go to the page, subpage and cell a finding names."""
        if f is None or f.page is None:
            return
        for i, pg in enumerate(self.service.pages):
            if pg.number == f.page and (not f.filename or pg.filename == f.filename or not pg.filename):
                if i != self.cur:
                    self.select_page(i)
                d = self.doc
                d.sub_index = min(f.sub, len(d.page.subpages) - 1)
                d.clear_selection()
                if f.row is not None:
                    d.goto(f.row, f.col if f.col is not None else 0)
                self.subpanel.load()
                self._subpage_chosen()
                self.canvas.update()
                return

    def edit_station(self):
        from .servicedlg import StationDialog
        dlg = StationDialog(station.load(self.service.folder), self)
        if dlg.exec():
            try:
                station.save(dlg.st, self.service.folder)
            except OSError as e:
                self._message(self.tr("Station settings not saved: {error}").format(error=e), "warn")
                return
            self.station = dlg.st
            self._message(self.tr("Station {callsign} saved in station.json").format(
                callsign=dlg.st.callsign or self.tr("(no callsign)")), "info")

    def confirm_discard(self) -> bool:
        dirty = [pg for pg in self.service.pages if self.docs.get(id(pg)) and self.docs[id(pg)].dirty]
        if not dirty or not self.ask_before_discard:
            return True
        names = ", ".join(f"P{pg.label()}" for pg in dirty)
        r = QMessageBox.question(self, self.tr("Unsaved pages"), self.tr("Save {pages} first?").format(pages=names),
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                 QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Save:
            self.save_all(checked=False)
            return True
        return r == QMessageBox.StandardButton.Discard

    # ------------------------------------------------------------ editing
    def on_edited(self):
        self.canvas.refresh()
        self.preview.follow()
        self._cell()
        self._title()
        self._panels_timer.start(180)

    def _refresh_panels(self):
        self.page_panel.load()
        self.subpanel.load()
        self.fastext.load()
        if 0 <= self.cur < len(self.service.pages):
            pg = self.service.pages[self.cur]
            if self.services.list.count() != len(self.service.pages):
                self.services.populate(self.service, self.cur, self.dirty_pages())
            else:
                self.services.refresh_current(pg, self.doc.dirty)
        self._status()

    def _subpage_chosen(self):
        self.canvas.refresh()
        self.preview.follow()
        self.page_panel.load()
        self.fastext.load()
        self._cell()
        self._title()

    def _doc_op(self, fn):
        fn()
        self.on_edited()

    def _sub_op(self, fn):
        fn()
        self.subpanel.load()
        self.on_edited()
        self._subpage_chosen()

    def set_subtitle_mode(self, on: bool):
        """Swap the page editor for the subtitle editor, or back."""
        self.stack.setCurrentIndex(1 if on else 0)
        self.b_subs.set_checked(on)
        if not on and self.subs.playing:
            self.subs.set_playing(False)
        self._title()
        (self.subs.table if on else self.canvas).setFocus()

    def undo(self):
        if self.stack.currentIndex() == 1:
            if not self.subs.undo():
                self._message(self.tr("Nothing to undo"), "info")
            return
        if self.doc.undo():
            self.on_edited()
            self._subpage_chosen()
            self._refresh_panels()
        else:
            self._message(self.tr("Nothing to undo"), "info")

    def redo(self):
        if self.stack.currentIndex() == 1:
            if not self.subs.redo():
                self._message(self.tr("Nothing to redo"), "info")
            return
        if self.doc.redo():
            self.on_edited()
            self._subpage_chosen()
            self._refresh_panels()
        else:
            self._message(self.tr("Nothing to redo"), "info")

    # ------------------------------------------------------------ clipboard
    def _clip_mime(self, block):
        from PyQt6.QtCore import QMimeData
        m = QMimeData()
        m.setData(clipboard.MIME_BLOCK, QByteArray(clipboard.block_to_json(block)))
        return m

    def _selection_or_cell(self):
        d = self.doc
        return d.selection() or (d.row, d.col, d.row, d.col)

    def copy(self):
        from PyQt6.QtWidgets import QApplication
        blk = self.doc.copy_block()
        m = self._clip_mime(blk)
        m.setText(clipboard.block_to_text(blk))
        QApplication.clipboard().setMimeData(m)
        self._message(self.tr("Copied {width} × {height} cells").format(width=blk.width, height=blk.height), "info")

    def cut(self):
        from PyQt6.QtWidgets import QApplication
        blk = self.doc.cut_block()
        m = self._clip_mime(blk)
        m.setText(clipboard.block_to_text(blk))
        QApplication.clipboard().setMimeData(m)
        self.on_edited()
        self._message(self.tr("Cut {width} × {height} cells").format(width=blk.width, height=blk.height), "info")

    def copy_link(self):
        from PyQt6.QtWidgets import QApplication
        blk = self.doc.copy_block()
        m = self._clip_mime(blk)
        m.setText(clipboard.block_to_edittf(blk, self.doc.page.number, self.doc.black_fg))
        QApplication.clipboard().setMimeData(m)
        self._message(self.tr("Copied the selection as an edit.tf link"), "info")

    def copy_picture(self):
        from PyQt6.QtGui import QImage
        from PyQt6.QtWidgets import QApplication
        from .canvas import editor_rows
        blk = self.doc.copy_block()
        rgb = clipboard.block_image(editor_rows(self.doc), self._selection_or_cell(), self.doc.black_fg,
                                    self.canvas.reveal)
        m = self._clip_mime(blk)
        m.setImageData(QImage.fromData(clipboard.png_bytes(rgb), "PNG"))
        m.setData("image/png", QByteArray(clipboard.png_bytes(rgb)))
        QApplication.clipboard().setMimeData(m)
        self._message(self.tr("Copied a picture of {width} × {height} cells").format(width=blk.width, height=blk.height),
                      "info")

    def paste(self):
        """Our own block if it is there; else an edit.tf link's cells; else
        plain text, line by line at the cursor."""
        from PyQt6.QtWidgets import QApplication
        m = QApplication.clipboard().mimeData()
        blk = None
        if m is not None and m.hasFormat(clipboard.MIME_BLOCK):
            blk = clipboard.block_from_json(bytes(m.data(clipboard.MIME_BLOCK)))
        if blk is None and m is not None and m.hasText():
            text = m.text()
            blk = clipboard.edittf_to_block(text) or clipboard.text_to_block(text)
        if blk is None or not blk.rows:
            self._message(self.tr("Nothing on the clipboard to paste"), "info")
            return
        sel = self.doc.selection()
        top, left = (sel[0], sel[1]) if sel else (self.doc.row, self.doc.col)
        rep = self.doc.paste_block(blk, top, left)
        self.on_edited()
        warn = rep.message()
        if warn:
            self._message(warn, "warn")
        else:
            said = (self.tr("Pasted {width} × {height} cells, {clipped} off the page") if rep.clipped else
                    self.tr("Pasted {width} × {height} cells"))
            self._message(said.format(width=blk.width, height=blk.height, clipped=rep.clipped), "info")

    def select_all(self):
        self.doc.select(1, 0, 23, 39)
        self.canvas.update()
        self._cell()

    def insert_code(self, code: int):
        self.doc.put(code)
        self.on_edited()
        self.canvas.setFocus()

    def set_tool(self, name: str):
        self.canvas.tool = name
        self.tools.set_tool(name)
        self.canvas.update()
        hint = {"text": "Type to write; Esc then a key puts a control code",
                "pen": "Drag to draw sixels, right-drag to erase; q w a s z x toggle the cursor cell's",
                "line": "Drag a line of sixels (right button: erase)",
                "rect": "Drag a rectangle (Filled in Tools fills it)",
                "fill": "Click a run of sixels to fill it (right button: empty it)"}[name]
        self._message(hint, "info")
        self.canvas.setFocus()

    def set_view(self, codes=None, grid=None, reveal=None, black=None):
        if codes is not None:
            self.canvas.show_codes = codes
            self.b_codes.set_checked(codes)
            self.prefs["showCodes"] = codes
        if grid is not None:
            self.canvas.grid = grid
            self.b_grid.set_checked(grid)
            self.prefs["grid"] = grid
        if reveal is not None:
            self.canvas.reveal = reveal
            self.preview.reveal = reveal
            self.b_reveal.set_checked(reveal)
            self.prefs["reveal"] = reveal
        if black is not None:
            self.prefs["blackFg"] = black
            for d in self.docs.values():
                d.black_fg = black
            self.preview.black_fg = black
        self.canvas.refresh()
        self.preview.follow()

    def _toggle_play(self):
        on = not self.preview.playing
        self.preview.set_playing(on)
        self.play.set_checked(on)

    def toggle_theme(self):
        from PyQt6.QtWidgets import QApplication
        name = "light" if theme.t()["name"] == "dark" else "dark"
        theme.set_theme(name)
        self.prefs["theme"] = name
        (self if self.embedded else QApplication.instance()).setStyleSheet(theme.stylesheet())
        for w in self.findChildren(QWidget):
            w.update()

    # ------------------------------------------------------------ import and export
    def import_image(self):
        path = open_image(self, self.prefs["imageDir"] or str(Path.home()))
        if not path:
            return
        self.prefs["imageDir"] = str(Path(path).parent)
        self.run_image_dialog(path)

    def run_image_dialog(self, path: str, accept: bool | None = None):
        try:
            dlg = ImageDialog(self.doc, path, self)
        except (OSError, ValueError) as e:
            self._message(self.tr("Could not read the picture: {error}").format(error=e), "warn")
            return None
        if accept is None:
            ok = dlg.exec()
        else:
            ok = accept
        if ok:
            dlg.commit()
            self.on_edited()
            self._message(self.tr("Imported {name}: %n colour-code cell(s)", "", dlg.result.codes_spent).format(
                name=Path(path).name), "info")
        return dlg

    def link_menu(self):
        from PyQt6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction(self.tr("Import an edit.tf link…"), self.import_link)
        m.addAction(self.tr("Copy this subpage's edit.tf link…"), self.export_link)
        m.exec(self.b_link.mapToGlobal(self.b_link.rect().bottomLeft()))

    def export_menu(self):
        from PyQt6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction(self.tr("PNG of this subpage…"), self.export_png)
        m.addAction(self.tr(".t42 of this page…"), lambda: self.export_t42(False))
        m.addAction(self.tr(".t42 of the whole service…"), lambda: self.export_t42(True))
        m.exec(self.b_export.mapToGlobal(self.b_export.rect().bottomLeft()))

    def import_link(self, url: str | None = None):
        if url is None:
            dlg = LinkDialog("", True, self)
            if not dlg.exec() or dlg.parsed is None:
                return
            hp = dlg.parsed
        else:
            hp = edittf.parse(url)
        self.apply_link(hp)

    def apply_link(self, hp):
        d = self.doc
        _, sub = edittf.to_page(hp, into=d.page)
        d.checkpoint()
        cur = d.sub
        cur.rows = sub.rows
        if "PS" in hp.fields:
            cur.status = sub.status
        if "X270" in hp.fields:
            cur.links, cur.has_links = sub.links, sub.has_links
        cur.hash_fields = dict(hp.fields)
        if hp.black_fg and not self.prefs["blackFg"]:
            self._message(self.tr("The link asks for black foreground; turn it on in View to see it"), "warn")
        self.on_edited()
        self._refresh_panels()

    def export_link(self):
        url = edittf.from_page(self.doc.page, self.doc.sub, black_fg=self.doc.black_fg,
                               keep=self.doc.sub.hash_fields)
        LinkDialog(url, False, self).exec()

    def page_png(self, path: str, scale: int = 2, now: float | None = None):
        """The subpage as a set shows it (header expanded, flash on, conceal
        per Reveal), at TV proportions."""
        from PIL import Image
        now = time.time() if now is None else now
        sub = self.doc.sub
        rows = display_rows(self.doc.page, sub, now)
        if self.doc.page.live is not None:
            rows = self.board.render_rows(self.doc.page, self.doc.sub_index, rows)
        d = decoder.decode(rows, black_fg=self.doc.black_fg)
        rgb = render.render(d, reveal=self.canvas.reveal, mixed=bool(sub.status & 3))
        Image.fromarray(render.stretch(rgb, render.TV_ASPECT, scale)).save(path)

    def export_png(self):
        start = str(Path(self.prefs["exportDir"] or Path.home()) / f"P{self.doc.page.label()}.png")
        path, _ = QFileDialog.getSaveFileName(self, self.tr("Export PNG"), start, self.tr("PNG (*.png)"))
        if path:
            self.prefs["exportDir"] = str(Path(path).parent)
            self.page_png(path)
            self._message(self.tr("Wrote {name}").format(name=Path(path).name), "info")

    def export_t42(self, whole: bool):
        name = "service" if whole else f"P{self.doc.page.label()}"
        start = str(Path(self.prefs["exportDir"] or Path.home()) / f"{name}.t42")
        path, _ = QFileDialog.getSaveFileName(self, self.tr("Export .t42"), start, self.tr("Teletext packets (*.t42)"))
        if not path:
            return
        self.prefs["exportDir"] = str(Path(path).parent)
        pages = self.service.pages if whole else [self.doc.page]
        t42.save(pages, path)
        n = len(t42.dumps(pages)) // 42
        self._message(self.tr("Wrote {name}: %n packet(s)", "", n).format(name=Path(path).name), "info")

    def about(self):
        QMessageBox.about(self, self.tr("OpenHamShack Teletext"),
                          self.tr("OpenHamShack Teletext {version}\n\nA teletext page editor for the pages OpenHamShack "
                                  "ATV transmits: WST Level 1, MRG .tti files as vbit2 reads them, .t42, edit.tf links."
                                  "\n\nFormerly OpenHamShack Teletext.").format(version=__version__))

    # ------------------------------------------------------------ status
    def _cell(self):
        self.cellinfo.show_cell(self.doc, self.canvas.decoded())
        d = self.doc
        if d.page.live is not None:
            from ..live.template import tokens_in_row
            for t in tokens_in_row(d.sub.rows[d.row], d.row):
                if t.col <= d.col < t.col + t.width:
                    v, f = self.board.lookup(t.name, self.board.scope(d.page.live))
                    what = ("'No data since HH:MM' when a source fails" if t.name == "status" else
                            "no source has this field" if f is None else f"{f.source.name}: {v!r}")
                    self.cellinfo.text += f"   live {{{t.name}}} {t.width} cells, {what}"
                    self.cellinfo.update()
                    break

    def _message(self, text: str, level: str = "info"):
        self.status.set_message(text, level)
        self.cellinfo.set_warning(text if level == "warn" else "")

    def _title(self):
        if hasattr(self, "stack") and self.stack.currentIndex() == 1:
            sd = self.subs.doc
            name = Path(sd.path).name if sd.path else self.tr("untitled")
            self.mark.set_sub(self.tr("Subtitles  ·  {name}  ·  %n cue(s)  ·  page 888", "", len(sd.track.cues)).format(
                name=name) + (self.tr("  ·  unsaved") if sd.dirty else ""))
            self._status()
            return
        d = self.doc
        n = len(d.page.subpages)
        sub = self.tr("  ·  subpage {index}/{total}").format(index=d.sub_index + 1, total=n) if n > 1 else ""
        self.mark.set_sub(f"P{d.page.label()}  ·  " + (d.page.description or self.tr("untitled")) + sub
                          + (self.tr("  ·  unsaved") if d.dirty else ""))
        self.editor_card.set_title(f"P{d.page.label()}{sub}")
        self._status()

    def _status(self):
        dirty = len(self.dirty_pages())
        self.status.set_items([(str(self.service.folder), "text2"),
                               (self.tr("%n page(s)", "", len(self.service.pages)), "text2"),
                               (self.tr("%n unsaved", "", dirty) if dirty else self.tr("all saved"),
                                "warn" if dirty else "text3")])

    # ------------------------------------------------------------ window
    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.overlay.isVisible():
            self.overlay.setGeometry(self.centralWidget().rect())

    def closeEvent(self, e):
        if not self.confirm_discard():
            e.ignore()
            return
        # The checker's dialog is a window of its own: it goes with this one
        # (left open, it was painted after its parent had gone, and Qt
        # crashes painting a widget whose owner is being destroyed).
        if self.check_dlg is not None:
            self.check_dlg.close()
        if self.subs.playing:
            self.subs.set_playing(False)
        self.prefs["geometry"] = bytes(self.saveGeometry().toHex()).decode()
        self.prefs["serviceDir"] = str(self.service.folder)
        self.prefs.save()
        super().closeEvent(e)
