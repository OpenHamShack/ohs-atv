"""The Qt window (DESIGN.md "User interface"). `run()` is what
`python3 -m ohsatv` calls when not --headless."""
from __future__ import annotations

import sys


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
        app.setApplicationName("OpenHamShack ATV")
        app.setApplicationDisplayName("OpenHamShack ATV")
        app.setOrganizationName("openhamshack-atv")
        app.setDesktopFileName("openhamshack-atv")
        # Before any window exists: tr() is answered when a widget is
        # built, so a catalog installed later changes nothing on screen.
        from .. import i18n
        i18n.install(app)
        # An install proof's photograph, only when one is asked for
        # (snapshot.py says why the app takes it itself).
        from . import snapshot
        app._snapshot_timer = snapshot.arm(app)
    return app


def shutdown():
    """Destroy every window and then the QApplication, now, while Python is
    still whole (OpenHamShack NB: left to interpreter shutdown, PyQt's teardown
    order ended processes on Windows with an access violation after their
    work was done)."""
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


def build(radio, prefs, start: bool = True):
    """The window and its bridge for an existing Radio (tests and the
    screenshot tool use this)."""
    from PyQt6.QtWidgets import QApplication

    from . import theme
    from .bridge import Bridge
    from .mainwindow import MainWindow
    theme.set_theme(prefs["theme"])
    app = QApplication.instance()
    app.setFont(theme.app_font())
    app.setStyleSheet(theme.stylesheet())
    bridge = Bridge(radio)
    err = bridge.start() if start else ""
    win = MainWindow(bridge, prefs, start_error=err)
    return win, bridge


def run(cfg: dict, persist: bool = True, band: str | None = None, system: str | None = None) -> int:
    app = make_app()
    from ..radio import Radio
    from . import theme
    from .appicon import app_icon
    from .prefs import Prefs
    app.setWindowIcon(app_icon())
    prefs = Prefs()
    theme.set_theme(prefs["theme"])
    # The window's engine joins the shared station profile (ohsatv/station.py);
    # an override run (--device, --play...) saves nothing and shares nothing.
    radio = Radio(cfg, persist=persist, link_station=persist)
    # --band / --system from the command line: applied before start, so the
    # radio opens where it was asked to rather than retuning a moment later.
    try:
        if band:
            radio.set(band=band)
        if system:
            radio.set(system=system)
    except ValueError as e:
        print(f"Ignoring the override: {e}", file=sys.stderr)
    win, bridge = build(radio, prefs)
    if not prefs["geometry"]:
        win.resize(1600, 960)
    win.show()
    # The AKP03E or a keypad, when there is one (ohsatv/ui/padlink.py). Only
    # here, not in build(): the suite builds hundreds of windows and none
    # of them should look for a pad.
    from . import padlink
    pad = padlink.start(win, radio)
    # Ctrl-C / SIGTERM close the window properly (unkey, save) rather than
    # killing the process mid-transmission; Python only sees the signal when
    # it next runs, hence the idle timer.
    import signal
    from PyQt6.QtCore import QTimer
    signal.signal(signal.SIGINT, lambda *_: win.close())
    signal.signal(signal.SIGTERM, lambda *_: win.close())
    wake = QTimer()
    wake.timeout.connect(lambda: None)
    wake.start(250)
    try:
        return app.exec()
    finally:
        if pad is not None:
            pad.close()
        bridge.stop()
        radio.close_station()
        wake.stop()
        shutdown()
