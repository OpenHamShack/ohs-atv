"""The station profile kept on this computer and shared with a controller
(ohsatv/station.py over ohs-libs shack_client): BETA-PLAN architecture
change 2, offline first, a controller joined when there is one and left
without losing anything. ATV keeps no logbook, so only the profile is
tested here.

The controller is shack_client's reference controller, served over real
HTTP on loopback: the same merge and sequence code the Dock360 edition
mounts, so pairing, an edit coming back and an unpair are the real
protocol, not a mock. Every engine keeps its store in its own temporary
data folder, so no test can touch the operator's real profile."""
import time
import unittest
from unittest import mock

from helpers import tmp_dir
from ohsatv import station as station_mod
from ohsatv.audio import NullAudio
from ohsatv.radio import Radio
from test_radio import FAST


def radio(tc, link=True, **over):
    d = tmp_dir(tc)
    cfg = dict(FAST, callsign="GM4BRI", locator="IO75RL", licenceAck=True, licenceClass="full")
    cfg.update(over)
    r = Radio(cfg, conf_path=d / "c.json", audio=NullAudio(realtime=False), data_dir=d / "data",
              link_station=link)
    tc.addCleanup(r.close_station)          # after stop(); before the folder goes
    tc.addCleanup(r.stop)
    return r


def wait(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class Profile(unittest.TestCase):
    def test_settings_fill_an_empty_profile(self):
        r = radio(self)
        p = r.station.profile.get()
        self.assertEqual((p["callsign"], p["locator"], p["licence"]["class"]), ("GM4BRI", "IO75RL", "full"))

    def test_a_profile_changed_elsewhere_reaches_the_settings_and_the_guard(self):
        r = radio(self, band="6cm", system="fm", rxFreq=5665e6, txFreq=5665e6)
        # Another app (or the controller) moves the station to the US.
        r.station.profile.update({"dxccPrefix": "K", "licence": {"regime": "auto", "class": "technician"}})
        r.station_link.pull()
        self.assertEqual((r.st["stationCountry"], r.st["licenceClass"]), ("K", "technician"))
        self.assertEqual(r.state()["bandLicence"]["spans"], [[5650e6, 5850e6]])

    def test_a_value_atv_cannot_hold_is_left_out(self):
        r = radio(self)
        # An operator's own licence (Radio and NB have it; ATV does not) and
        # a class no licence in the dataset has: neither is forced in.
        r.station.profile.update({"licence": {"regime": "custom", "class": "own", "custom": [
            {"lo": 430e6, "hi": 440e6, "watts": 10, "unit": "PEP"}]}})
        r.station_link.pull()
        self.assertEqual((r.st["licence"], r.st["licenceClass"]), ("auto", "full"))

    def test_operator_changes_go_into_the_profile_without_wiping_others(self):
        r = radio(self)
        custom = [{"lo": 430e6, "hi": 440e6, "watts": 10, "unit": "PEP"}]
        r.station.profile.update({"licence": {"regime": "auto", "class": "full", "custom": custom}})
        r.set(locator="IO86AA")
        r.set(authorise=True, licenceClass="intermediate")
        p = r.station.profile.get()
        self.assertEqual(p["locator"], "IO86AA")
        self.assertEqual(p["licence"]["class"], "intermediate")
        self.assertEqual(p["licence"]["custom"], custom)       # NB's and Radio's, kept

    def test_an_engine_not_linked_shares_nothing(self):
        r = radio(self, link=False)
        r.set(locator="IO86AA")
        self.assertIsNone(r._station)

    def test_a_store_that_will_not_open_never_stops_the_radio(self):
        with mock.patch.object(station_mod, "open_station", side_effect=OSError("disk full")), \
                self.assertLogs("ohsatv.radio", "WARNING"):
            r = radio(self)
            self.assertFalse(r.link_profile())
        r.set(locator="IO86AA")
        r.start()
        self.assertTrue(r.running)


class Controller(unittest.TestCase):
    """Pair, share the profile both ways, unpair and keep the local copy."""

    def setUp(self):
        self.sc = station_mod.client()
        from shack_client.testing import ReferenceController
        d = tmp_dir(self)
        self.ctl = ReferenceController(d / "controller.db", name="GM4BRI")
        self.url = self.ctl.serve()
        self.addCleanup(self.ctl.close)
        self.r = radio(self)
        self.link = self.r.station_link

    def test_pair_share_both_ways_and_unpair(self):
        result = self.link.pair(self.url, "brian", "pw", device_name="test laptop")
        self.assertIsInstance(result, self.sc.Pairing)
        self.assertEqual(self.link.pairing()["name"], "GM4BRI")
        prof = self.ctl.records("profile")["station"]["fields"]
        self.assertEqual((prof["callsign"], prof["locator"]), ("GM4BRI", "IO75RL"))
        # The profile edited on the controller (another app, the Remote)
        # comes back into ATV's settings.
        self.ctl.sync.edit("profile", "station", "update", {"locator": "IO86HA"},
                           {"app": "remote", "device": "dev_X", "user": "brian"})
        self.link.sync_now()
        self.assertTrue(wait(lambda: self.r.st["locator"] == "IO86HA"))
        self.assertEqual(self.link.status()["state"], "idle")
        self.link.unpair()
        self.assertEqual(self.link.status()["state"], "unpaired")
        self.assertIsNone(self.link.pairing())
        # The local copy is complete, and the app carries on standalone.
        self.assertEqual(self.r.station.profile.get()["locator"], "IO86HA")
        self.r.set(locator="JO62AB")
        self.assertEqual(self.r.station.profile.get()["locator"], "JO62AB")
        self.assertEqual(self.ctl.records("profile")["station"]["fields"]["locator"], "IO86HA")

    def test_wrong_password_is_a_sentence(self):
        with self.assertRaises(self.sc.PairingError) as cm:
            self.link.pair(self.url, "brian", "nope")
        self.assertTrue(str(cm.exception))
        self.assertEqual(self.link.status()["state"], "unpaired")

    def test_a_controller_that_is_not_there_is_offline_not_a_crash(self):
        self.ctl.close()
        with self.assertRaises(self.sc.Offline):
            self.link.pair(self.url, "brian", "pw")
        self.r.set(locator="IO86AA")
        self.assertEqual(self.r.station.profile.get()["locator"], "IO86AA")


if __name__ == "__main__":
    unittest.main()
