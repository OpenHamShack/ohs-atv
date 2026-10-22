"""Settings › Controller: find an OpenHamShack controller on the network,
pair with it, see how the sync is going, and unpair.

The app works the same with or without one (BETA-PLAN architecture change
2): pairing only adds a copy of the station profile on the controller and
every other paired app. ATV keeps no logbook (ohsatv/station.py), so this
page talks about the profile; the store it pairs is shared with the other
OpenHamShack apps on this computer, whose logbooks pairing does share,
which is why the two-logbooks question can still come up. Nothing on this
page is needed to operate.

Copied from openhamshack-nb ohscore/ui/controller.py (no code is shared at run
time between the family's repos).

Discovery and pairing talk to the network, so both run on a worker thread
and report back through a signal; the page never blocks the window. The
password is used once, for the token exchange (CONTROLLER-CONTRACT.md
section 4.2), and never stored: only the device token is, by shack_client,
in pairing.json beside the store (mode 0600).
"""
from __future__ import annotations

import threading
import time

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                             QVBoxLayout, QWidget)

from .. import station as station_mod
from . import theme
from .controls import Button


class _Worker(QObject):
    done = pyqtSignal(str, object)        # (what, result or exception)

    def run(self, what: str, fn) -> None:
        def go():
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001  shown on the page, as a sentence
                res = e
            self.done.emit(what, res)
        threading.Thread(target=go, name=f"controller-{what}", daemon=True).start()


class ControllerPage(QWidget):
    """`link` is the engine's ohsatv.station.StationLink, or None when the
    store cannot be opened: the page then says so and offers nothing."""

    def __init__(self, link, parent=None):
        super().__init__(parent)
        self.link = link
        self.worker = _Worker(self)
        self.worker.done.connect(self._done)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.state = QLabel("")
        self.state.setWordWrap(True)
        self.state.setFont(theme.font(13, theme.QFont.Weight.DemiBold))
        lay.addWidget(self.state)
        self.detail = QLabel("")
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        lay.addWidget(self.detail)

        # Find
        row = QHBoxLayout()
        self.find_btn = Button(self.tr("Find controllers"), icon="link")
        self.find_btn.clicked.connect(self.find)
        row.addWidget(self.find_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.found = QListWidget()
        self.found.setMaximumHeight(110)
        self.found.itemClicked.connect(self._pick)
        lay.addWidget(self.found)

        # Pair
        self.address = QLineEdit()
        self.address.setPlaceholderText(self.tr("Controller address, for example http://198.51.100.208/"))
        lay.addWidget(self.address)
        creds = QHBoxLayout()
        self.user = QLineEdit()
        self.user.setPlaceholderText(self.tr("User name"))
        self.password = QLineEdit()
        self.password.setPlaceholderText(self.tr("Password"))
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.totp = QLineEdit()
        self.totp.setPlaceholderText(self.tr("Code (if asked)"))
        self.totp.setMaximumWidth(140)
        for w in (self.user, self.password, self.totp):
            creds.addWidget(w)
        lay.addLayout(creds)
        acts = QHBoxLayout()
        self.pair_btn = Button(self.tr("Pair"), kind="primary")
        self.pair_btn.clicked.connect(self.pair)
        self.sync_btn = Button(self.tr("Sync now"))
        self.sync_btn.clicked.connect(self.sync_now)
        self.unpair_btn = Button(self.tr("Unpair"), kind="danger")
        self.unpair_btn.clicked.connect(self.unpair)
        for b in (self.pair_btn, self.sync_btn, self.unpair_btn):
            acts.addWidget(b)
        acts.addStretch(1)
        lay.addLayout(acts)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        lay.addWidget(self.result)

        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    # ---------------------------------------------------------- showing
    def refresh(self) -> None:
        if self.link is None:
            self.state.setText(self.tr("The station store (shack-client) could not be opened on this computer, so "
                                       "this app cannot pair. It works the same without a controller."))
            for w in (self.find_btn, self.pair_btn, self.sync_btn, self.unpair_btn):
                w.setEnabled(False)
            return
        st = self.link.status()
        p = self.link.pairing()
        name = (p or {}).get("name") or (p or {}).get("baseUrl", "")
        words = {
            "unpaired": self.tr("Not paired. This app keeps its own station profile on this computer."),
            "idle": self.tr("Paired with {name}: the station profile is shared."),
            "syncing": self.tr("Paired with {name}: syncing now."),
            "offline": self.tr("Paired with {name}, which cannot be reached just now. Everything works here, "
                               "and changes are shared when it is back."),
            "error": self.tr("Paired with {name}, but the last sync failed: {error}"),
            "needsDecision": self.tr("Paired with {name}: choose what to do with the two logbooks (Pair again). They are the "
                                   "other OpenHamShack apps' on this computer: ATV keeps none."),
        }
        self.state.setText(words.get(st["state"], st["state"]).format(name=name, error=st.get("error", "")))
        parts = []
        if p:
            parts.append(self.tr("Signed in as {user}; this computer is device {device}.").format(
                user=p.get("user", ""), device=p.get("deviceId", "")))
        if st.get("lastSyncAt"):
            parts.append(self.tr("Last synced {when} UTC.").format(when=st["lastSyncAt"][:19].replace("T", " ")))
        if st.get("pendingRecords"):
            parts.append(self.tr("%n change(s) waiting to be sent.", "", int(st["pendingRecords"])))
        self.detail.setText(" ".join(parts))
        paired = p is not None
        self.unpair_btn.setEnabled(paired)
        self.sync_btn.setEnabled(paired)
        if paired and not self.address.text():
            self.address.setText(p.get("baseUrl", ""))

    def _say(self, text: str, ok: bool = True) -> None:
        self.result.setText(text)
        self.result.setStyleSheet(f"color: {theme.t()['ok' if ok else 'err']};")

    # ---------------------------------------------------------- actions
    def find(self) -> None:
        self.found.clear()
        self.find_btn.setEnabled(False)
        self._say(self.tr("Looking for controllers on this network…"))
        self.worker.run("find", lambda: self.link.discover(timeout=3.0))

    def _pick(self, item: QListWidgetItem) -> None:
        self.address.setText(item.data(Qt.ItemDataRole.UserRole))

    def pair(self) -> None:
        url = self.address.text().strip()
        if not url:
            self._say(self.tr("Type the controller's address, or find one first."), False)
            return
        if "://" not in url:
            url = "http://" + url
        user, pw, code = self.user.text().strip(), self.password.text(), self.totp.text().strip()
        if not user or not pw:
            self._say(self.tr("Pairing needs your controller user name and password."), False)
            return
        self.pair_btn.setEnabled(False)
        self._say(self.tr("Pairing…"))
        self.worker.run("pair", lambda: self.link.pair(url, user, pw, code or None))

    def sync_now(self) -> None:
        self.link.sync_now()
        self._say(self.tr("Syncing…"))

    def unpair(self) -> None:
        ans = QMessageBox.question(
            self, self.tr("Unpair"),
            self.tr("Unpair from this controller? The station profile stays on this computer as it is; it is no "
                    "longer shared."))
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.link.unpair()
        self._say(self.tr("Unpaired. The station profile is still here."))
        self.refresh()

    # ---------------------------------------------------------- results
    def _done(self, what: str, res) -> None:
        self.password.clear()
        self.find_btn.setEnabled(True)
        self.pair_btn.setEnabled(True)
        sc = station_mod.client()
        if what == "find":
            if isinstance(res, list):
                # A Classic 0.9.x box answers the old _openhamshack._tcp advert
                # too; it is a QO-100 WB box, not a controller to pair with.
                res = [c for c in res if not c.classic]
            if isinstance(res, sc.DiscoveryUnavailable):
                self._say(self.tr("Finding controllers needs python3-zeroconf. Type the address instead."), False)
            elif isinstance(res, Exception):
                self._say(self.tr("Could not look for controllers: {why}").format(why=res), False)
            elif not res:
                self._say(self.tr("No controller answered. Type its address instead."), False)
            else:
                for c in res:
                    it = QListWidgetItem(f"{c.name}  {c.url}")
                    it.setData(Qt.ItemDataRole.UserRole, c.url)
                    self.found.addItem(it)
                self._say(self.tr("%n controller(s) found: pick one.", "", len(res)))
            return
        if isinstance(res, sc.Offline):
            self._say(self.tr("The controller did not answer at that address."), False)
        elif isinstance(res, sc.PairingError):
            code = getattr(res, "code", "")
            if code == "totp_required":
                self._say(self.tr("This account has a second factor: type the code from your app and pair "
                                  "again."), False)
            else:
                self._say(str(res), False)
        elif isinstance(res, sc.NeedsDecision):
            self._decide(res)
        elif isinstance(res, Exception):
            self._say(self.tr("Pairing failed: {why}").format(why=res), False)
        else:
            self._say(self.tr("Paired with {name}.").format(name=res.name or res.base_url))
        self.refresh()

    def _decide(self, d) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(self.tr("Two logbooks"))
        box.setText(self.tr("This computer's logbook is for {mine} and the controller's for {theirs}. Merge "
                            "them into one, or use the controller's and keep this computer's as a file?").format(
            mine=d.local_callsign or "?", theirs=d.controller_callsign or "?"))
        merge = box.addButton(self.tr("Merge"), QMessageBox.ButtonRole.AcceptRole)
        theirs = box.addButton(self.tr("Use the controller's"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is merge:
            choice = "merge"
        elif box.clickedButton() is theirs:
            choice = "use-controller"
        else:
            self.link.unpair()
            self._say(self.tr("Not paired: nothing was shared."), False)
            return
        self._say(self.tr("Pairing…"))
        self.worker.run("pair", lambda: self.link.resolve(d, choice))
