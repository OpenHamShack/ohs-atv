"""Settings › Controller (ohsatv/ui/controller.py), driven as an operator
would on Qt's offscreen platform against shack_client's reference
controller over loopback HTTP: find one, type the account, pair, see it
say so, unpair and keep the profile. The password never outlives the
attempt, and a store that cannot open leaves the page saying so."""
import time
import unittest
from unittest import mock

from helpers import tmp_dir
from ohsatv import station as station_mod
from test_ui import HAVE_QT, UiBase


@unittest.skipUnless(HAVE_QT, "PyQt6 is not installed")
class ControllerPage(UiBase):
    def setUp(self):
        station_mod.client()
        from shack_client.testing import ReferenceController as Ref
        self.ctl = Ref(tmp_dir(self) / "controller.db", name="GM4BRI")
        self.url = self.ctl.serve()
        self.addCleanup(self.ctl.close)

    def page(self, link=True):
        from ohsatv.ui.controller import ControllerPage as Page
        from ohsatv.ui.settings import SettingsDialog
        win, bridge, radio = self.make()
        self.addCleanup(radio.close_station)
        if link:
            radio.link_profile()            # as the window's engine does (link_station)
        names = [n for _i, n in SettingsDialog.PAGES]
        d = SettingsDialog(bridge, self.prefs, win, page=names.index("Controller"))
        self.addCleanup(d.close)
        page = d.stack.currentWidget().findChild(Page)
        self.assertIsNotNone(page)
        return page, radio

    def wait(self, cond, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def test_pair_and_unpair_from_settings(self):
        page, radio = self.page()
        self.assertIn("Not paired", page.state.text())
        self.assertFalse(page.unpair_btn.isEnabled())
        page.address.setText(self.url)
        page.user.setText("brian")
        page.password.setText("pw")
        page.pair()
        self.assertTrue(self.wait(lambda: "Paired with GM4BRI" in page.result.text()), page.result.text())
        self.assertEqual(page.password.text(), "")
        self.assertIn("Paired with GM4BRI: the station profile is shared", page.state.text())
        self.assertTrue(page.unpair_btn.isEnabled())
        self.assertEqual(self.ctl.records("profile")["station"]["fields"]["callsign"], "GM4BRI")
        from PyQt6.QtWidgets import QMessageBox
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            page.unpair()
        self.assertIn("Not paired", page.state.text())
        self.assertEqual(radio.station.profile.get()["callsign"], "GM4BRI")

    def test_find_lists_controllers_but_not_classic_boxes(self):
        sc = station_mod.client()
        found = [sc.Controller("Shack", "a1b2c3d4", "http://192.0.2.10/"),
                 sc.Controller("OpenHamShack 0.9", "e5f6a7b8", "http://192.0.2.11/", classic=True)]
        page, radio = self.page()
        with mock.patch.object(radio.station_link, "discover", return_value=found):
            page.find()
            self.assertTrue(self.wait(lambda: page.find_btn.isEnabled()))
        self.assertEqual(page.found.count(), 1)
        page._pick(page.found.item(0))
        self.assertEqual(page.address.text(), "http://192.0.2.10/")
        with mock.patch.object(radio.station_link, "discover", side_effect=sc.DiscoveryUnavailable("no zeroconf")):
            page.find()
            self.assertTrue(self.wait(lambda: page.find_btn.isEnabled()))
        self.assertIn("python3-zeroconf", page.result.text())

    def test_a_wrong_password_says_so_and_stays_unpaired(self):
        page, _radio = self.page()
        page.address.setText(self.url)
        page.user.setText("brian")
        page.password.setText("wrong")
        page.pair()
        self.assertTrue(self.wait(lambda: page.pair_btn.isEnabled() and page.result.text() != "Pairing…"))
        self.assertNotIn("Paired with", page.result.text())
        self.assertIn("Not paired", page.state.text())
        self.assertEqual(page.password.text(), "")

    def test_nothing_typed_is_explained_not_sent(self):
        page, _radio = self.page()
        page.pair()
        self.assertIn("address", page.result.text())
        self.assertEqual(self.ctl.requests, [])

    def test_a_store_that_will_not_open_is_said_not_fatal(self):
        with mock.patch.object(station_mod, "open_station", side_effect=OSError("read-only")):
            page, radio = self.page(link=False)
        self.assertIn("could not be opened", page.state.text())
        self.assertFalse(page.pair_btn.isEnabled())
        self.assertIsNone(radio._station)


if __name__ == "__main__":
    unittest.main()
