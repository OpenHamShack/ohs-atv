"""Transmit permission from the station profile and the shared dataset
(ohsatv/txguard.py, ohs-libs shack_bandplan): the same carrier gets each
country's and class's own answer, a region whose data is partial refuses
and warns as the dataset says, and the engine shows and enforces it.

The UK Full licensee's answers, which are ATV 0.2.3's, are test_bands.py's."""
import unittest

from helpers import tmp_dir
from ohsatv import bands, modem, txguard
from ohsatv.audio import NullAudio
from ohsatv.radio import Radio, TxRefused
from test_radio import FAST, FakeHardware

MHZ = 1e6
AM = modem.am_plan(10e6)["occupied"]
FM = modem.fm_plan(20e6)["occupied"]


def station(**st):
    return txguard.profile(st)


def dataset_says(prof, lo, hi, mode):
    """The library's own verdict, asked directly: what the guard must pass on."""
    bp = txguard.GUARD.bp
    return bp.check(bp.context(prof, bp.bundled()), bp.Signal.edges(lo, hi, mode=mode))


class OtherCountries(unittest.TestCase):
    US = station(callsign="K1ABC", licenceClass="technician")

    def test_a_us_technician_may_use_6cm_where_the_uk_has_no_allocation(self):
        # 5705 MHz (FPV E2) is outside all three UK 6cm blocks and inside
        # the FCC's 5650-5925 MHz.
        uk = station(callsign="GM4BRI", licenceClass="full")
        self.assertTrue(bands.tx_check(5705 * MHZ, *FM, uk, "fm")[0])
        refuse, warn = bands.tx_check(5705 * MHZ, *FM, self.US, "fm")
        self.assertEqual(refuse, [])
        # The FCC data has had one research pass: said, never a refusal.
        self.assertTrue(any("United States (FCC)" in w and "second-checked" in w for w in warn), warn)

    def test_the_us_plan_is_the_datasets_and_the_region_1_warning_is_not_shown(self):
        # 1255 MHz FM-ATV: the IARU Region 2 plan's point-to-point segment
        # is a plan warning, not a refusal, and the RSGB's ATV segments do
        # not apply in the US.
        refuse, warn = bands.tx_check(1255 * MHZ, *FM, self.US, "fm")
        self.assertEqual(refuse, [])
        self.assertTrue(any(w.startswith("Band plan:") for w in warn), warn)
        # 70cm: no OUTSIDE THE BANDPLAN (IARU R1 removed analogue ATV, R2
        # did not), and a signal reaching the R2 plan's beacons is refused
        # with the dataset's own sentence - its reserved segments always are.
        lo, hi = 433 * MHZ + AM[0], 433 * MHZ + AM[1]
        refuse, warn = bands.tx_check(433 * MHZ, *AM, self.US, "am")
        self.assertFalse(any("OUTSIDE THE BANDPLAN" in w for w in warn), warn)
        self.assertEqual(refuse, [dataset_says(self.US, lo, hi, "pal-am")["refusal"]["text"]])
        self.assertIn("beacons", refuse[0])

    def test_a_class_gets_its_own_limits(self):
        novice = station(callsign="K1ABC", licenceClass="novice")
        refuse, _ = bands.tx_check(1255 * MHZ, *FM, novice, "fm")
        self.assertEqual(refuse, ["Novice licence: 1240.0-1270.0 MHz is not available"])
        foundation = station(callsign="M7ABC", licenceClass="foundation")
        refuse, _ = bands.tx_check(1255 * MHZ, *FM, foundation, "fm")
        self.assertEqual(refuse, ["Foundation licence: 1240.0-1260.0 MHz is not available"])
        self.assertEqual(bands.tx_check(5665 * MHZ, *FM, foundation, "fm")[0], [])     # 2 W, but allowed

    def test_a_partial_regime_refuses_as_the_dataset_says(self):
        # The German entry's emission table lists no ATV on 23cm: the guard
        # passes that on, verbatim, rather than second-guessing the data.
        de = station(callsign="DL1ABC", licenceClass="de_a")
        refuse, _ = bands.tx_check(1255 * MHZ, *FM, de, "fm")
        lo, hi = 1255 * MHZ + FM[0], 1255 * MHZ + FM[1]
        self.assertEqual(refuse, [dataset_says(de, lo, hi, "pal-fm")["refusal"]["text"]])
        # A country with no licence written into the dataset is locked.
        refuse, _ = bands.tx_check(1255 * MHZ, *FM, station(callsign="JA1ABC", licenceClass="full"), "fm")
        self.assertTrue(refuse and refuse[0].startswith("No licence for Japan"), refuse)

    def test_the_country_setting_overrides_the_callsign(self):
        prof = station(callsign="GM4BRI", stationCountry="K", licenceClass="technician")
        self.assertEqual(txguard.GUARD.in_force(prof)["regimeId"], "fcc-2026")
        self.assertEqual(bands.tx_check(5705 * MHZ, *FM, prof, "fm")[0], [])


class UkConditionsAndClass(unittest.TestCase):
    def test_no_class_locks(self):
        refuse, _ = bands.tx_check(1255 * MHZ, *FM, station(callsign="GM4BRI"), "fm")
        self.assertIn("No licence class chosen", refuse[0])
        # A class from another country's licence is not this one's.
        refuse, _ = bands.tx_check(1255 * MHZ, *FM, station(callsign="GM4BRI", licenceClass="technician"), "fm")
        self.assertIn("different licence than UK (Ofcom)", refuse[0])

    def test_near_london_refuses_431_to_432_even_on_70cm(self):
        # The 70cm leniency is for the allocation's edge only (cable and
        # attenuator); a licence condition refuses as it says.
        prof = station(callsign="G4ABC", licenceClass="full", stationConditions={"nearLondon": True})
        refuse, _ = bands.tx_check(433 * MHZ, *AM, prof, "am")
        self.assertTrue(any("Charing Cross" in r for r in refuse), refuse)
        refuse, _ = bands.tx_check(433 * MHZ, *AM, station(callsign="G4ABC", licenceClass="full"), "am")
        self.assertEqual(refuse, [])

    def test_conditions_and_classes_are_offered_from_the_licence_in_force(self):
        prof = station(callsign="GM4BRI")
        self.assertEqual([c for c, _ in txguard.GUARD.classes(prof)], ["foundation", "intermediate", "full"])
        cond = {c["key"]: c["value"] for c in txguard.GUARD.conditions(prof)}
        self.assertEqual(cond["nearLondon"], False)
        self.assertEqual(cond["scotland40km"], True)          # GM: derived from the callsign


class Engine(unittest.TestCase):
    def make(self, **over):
        tmp = tmp_dir(self)
        cfg = dict(FAST, callsign="GM4BRI", licenceAck=True, allowHardwareTx=True)
        cfg.update(over)
        r = Radio(cfg, conf_path=tmp / "c.json", audio=NullAudio(realtime=False),
                  device=FakeHardware({}, 10e6, duplex=True), data_dir=tmp / "data")
        self.addCleanup(r.stop)
        return r

    def test_no_class_refuses_the_real_radio_and_says_why(self):
        r = self.make()
        r.start()
        st = r.state()
        self.assertFalse(st["licenceInForce"]["canTransmit"])
        self.assertTrue(any("No licence class chosen" in x for x in st["txRefused"]), st["txRefused"])
        with self.assertRaisesRegex(TxRefused, "No licence class chosen"):
            r.ptt(True)
        self.assertFalse(r.state()["keyed"])
        r.set(authorise=True, licenceClass="full")
        st = r.state()
        self.assertEqual(st["txRefused"], [])
        self.assertEqual(st["licenceInForce"]["className"], "Full")

    def test_a_us_profile_changes_the_band_limits_the_window_shows(self):
        r = self.make(licenceClass="full", band="6cm", rxFreq=5665e6, txFreq=5665e6, system="fm")
        self.assertEqual(r.state()["bandLicence"]["spans"], [[5650e6, 5680e6], [5755e6, 5765e6], [5820e6, 5850e6]])
        self.assertEqual(r.state()["bandLicence"]["limit"], {"watts": 400, "unit": "PEP"})
        r.set(authorise=True, stationCountry="K", licenceClass="technician")
        self.assertEqual(r.state()["bandLicence"]["spans"], [[5650e6, 5850e6]])
        self.assertEqual(r.state()["bandLicence"]["limit"], {"watts": 1500, "unit": "PEP"})
        self.assertEqual(r.state()["licenceInForce"]["regimeShort"], "United States (FCC)")

    def test_licence_keys_change_only_from_settings(self):
        r = self.make()
        for k, v in (("licenceClass", "full"), ("licence", "fcc-2026"), ("stationCountry", "K"),
                     ("stationConditions", {"nearLondon": True})):
            with self.assertRaisesRegex(ValueError, "only be changed in Settings"):
                r.set(**{k: v})
        with self.assertRaisesRegex(ValueError, "yes or no"):
            r.set(authorise=True, stationConditions={"nearLondon": "maybe"})


if __name__ == "__main__":
    unittest.main()
