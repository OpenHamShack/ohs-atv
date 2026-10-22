"""The window photographing itself, for an install proof run over SSH.

When $SHACK_DESKTOP_SNAPSHOT names a file, the running program saves a PNG
of its own front window there once, a few seconds after it starts, and
carries on. Nothing else changes and nothing is written without it.

Why the program and not the proof script: on the Mac mini, `screencapture`
from an SSH session is refused (Screen Recording needs someone at the
screen to grant it), so ohs-libs packaging/macos/try-dmg.sh asks the app
for its own picture under this variable - the name the web-UI apps' shell
(shack_desktop) already answers to, so one proof script serves both kinds.
QWidget.grab() renders the window's widgets into a picture without reading
the screen, so nothing else on the desktop can end up in it (the rule
GUIDE.md keeps for Brian's Windows desktop holds here as well)."""
from __future__ import annotations

import os

ENV = "SHACK_DESKTOP_SNAPSHOT"
# Long enough for the window to have drawn and the simulator's picture to
# have arrived; try-dmg.sh looks at 12 s and waits ten more for the file.
AFTER_MS = 8000


def front_window():
    """What the person at the screen would see in front: a modal dialog if
    one is up, else the active window, else the largest visible top-level
    window (an app opened from a script is not always made active)."""
    from PyQt6.QtWidgets import QApplication
    w = QApplication.activeModalWidget() or QApplication.activeWindow()
    if w is not None:
        return w
    shown = [t for t in QApplication.topLevelWidgets() if t.isVisible() and t.width() > 1]
    return max(shown, key=lambda t: t.width() * t.height(), default=None)


def save(path: str) -> bool:
    """The front window as a PNG at `path`; False when no window is showing.
    Written beside it first and renamed, so a script waiting for the file
    never copies half of one."""
    w = front_window()
    if w is None:
        return False
    part = f"{path}.part"
    if not w.grab().save(part, "PNG"):
        return False
    os.replace(part, path)
    return True


def arm(app, path: str | None = None, after_ms: int = AFTER_MS):
    """Schedule the one photograph if $SHACK_DESKTOP_SNAPSHOT (or `path`)
    asks for it; returns the timer, or None when nothing was asked."""
    path = path or os.environ.get(ENV, "")
    if not path:
        return None
    from PyQt6.QtCore import QTimer
    timer = QTimer(app)
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: save(path))
    timer.start(after_ms)
    return timer
