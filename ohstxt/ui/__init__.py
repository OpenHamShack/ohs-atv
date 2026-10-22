"""The Qt window (DESIGN.md "The window"). `run()` is what
`python3 -m ohstxt` calls."""
from __future__ import annotations

import sys
from pathlib import Path


def make_app(argv=None):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        app = QApplication(list(argv or sys.argv[:1]))
        # The name and organisation stay as they were before the
        # OpenHamShack rename: Qt derives per-user paths from them
        # (QSettings, QStandardPaths), so a change would strand an
        # installed copy's files the day something starts using them. The
        # display name is what the user sees.
        app.setApplicationName("OpenHamShack Teletext")
        app.setApplicationDisplayName("OpenHamShack Teletext")
        app.setOrganizationName("openhamshack-teletext")
        app.setDesktopFileName("openhamshack-teletext")
        # Before any window exists: tr() is answered when a widget is
        # built, so a catalog installed later changes nothing on screen.
        from .. import i18n
        i18n.install(app)
    return app


def shutdown():
    """Destroy every window and then the QApplication while Python is still
    whole (OpenHamShack NB found that PyQt's teardown left to interpreter exit
    could end a process with an access violation after its work was done)."""
    from PyQt6 import sip
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    for w in QApplication.topLevelWidgets():
        w.close()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    app.processEvents()
    sip.delete(app)


def build(prefs, folder: Path | None = None):
    """The window on a service folder (tests and the screenshot tool use this)."""
    from PyQt6.QtWidgets import QApplication

    from . import theme
    from .mainwindow import MainWindow, default_service_dir, ensure_service
    theme.set_theme(prefs["theme"])
    app = QApplication.instance()
    app.setFont(theme.app_font())
    app.setStyleSheet(theme.stylesheet())
    folder = Path(folder) if folder else Path(prefs["serviceDir"]) if prefs["serviceDir"] else default_service_dir()
    return MainWindow(prefs, ensure_service(folder))


def open_editor(folder: Path, theme_name: str | None = None, prefs=None):
    """The editor as a window of another program - OpenHamShack ATV's
    "Teletext editor" button - on the service folder that program sends.

    Inside someone else's QApplication, so nothing application-wide is
    touched: the style sheet and font go on this window alone (the host's
    own look is the app's, and build()'s app-wide sheet would repaint every
    one of its widgets in ours), and the window is marked `embedded` so its
    theme switch keeps to itself too. The catalog is installed here, before
    the window is built, because tr() is answered when a widget is made;
    ohstxt's translators are its own list, so the host's stay in place. The
    theme follows the host's when it says which (a dark editor over a light
    transceiver looks like a different program), and the window keeps the
    editor's own preferences file, so it opens where it was last left."""
    from PyQt6.QtCore import Qt

    from .. import i18n
    from . import theme
    from .mainwindow import MainWindow, ensure_service
    from .prefs import Prefs
    i18n.install()
    prefs = prefs if prefs is not None else Prefs()
    theme.set_theme(theme_name or prefs["theme"])
    win = MainWindow(prefs, ensure_service(Path(folder)))
    win.embedded = True
    win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    win.setFont(theme.app_font())
    win.setStyleSheet(theme.stylesheet())
    if not prefs["geometry"]:
        win.resize(1500, 920)
    return win


def run(folder: str | None = None, page: str | None = None) -> int:
    app = make_app()
    from .appicon import app_icon
    from .prefs import Prefs
    app.setWindowIcon(app_icon())
    prefs = Prefs()
    target = Path(folder) if folder else None
    pick = None
    if target is not None and target.is_file():
        target, pick = target.parent, target.name
    win = build(prefs, target)
    if pick:
        for i, pg in enumerate(win.service.pages):
            if pg.filename == pick:
                win.select_page(i)
    elif page:
        from ..page import parse_page_number
        n = parse_page_number(page)
        for i, pg in enumerate(win.service.pages):
            if pg.number == n:
                win.select_page(i)
    if not prefs["geometry"]:
        win.resize(1680, 1000)
    win.show()
    rc = app.exec()
    shutdown()
    return rc
