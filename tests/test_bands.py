"""The transmit guard against the UK allocations and bandplans, and the
device-range check that keeps 6cm honest on a stock Pluto.

The allocations come from the station profile and the shared dataset
(ohsatv/txguard.py): these tests are a UK Full licensee's, GM4BRI, whose
answers are the ones ATV 0.2.3 gave from its own tables. Other countries
and classes are tests/test_txguard.py's."""
import unittest

from helpers import crop  # noqa: F401  (path setup)
from ohsatv import bands, modem, sysa, txguard

UK = txguard.profile({"callsign": "GM4BRI", "licenceClass": "full"})


def tx_check(freq, lo_off, hi_off):
    return bands.tx_check(freq, lo_off, hi_off, UK)

MHZ = 1e6
AM = modem.am_plan(10e6)["occupied"]              # about -1.25 .. +6.1 MHz around vision
FM = modem.fm_plan(20e6)["occupied"]              # IARU V.2 FM-ATV, about +-8.9 MHz


class Guard(unittest.TestCase):
    def test_70cm_analogue_is_a_warning_but_the_allocation_still_holds(self):
        refuse, warn = tx_check(433.0 * MHZ, *AM)
        self.assertEqual(refuse, [])
        self.assertTrue(warn[0].startswith("OUTSIDE THE BANDPLAN"))
        self.assertIn("2020", warn[0])
        self.assertTrue(any("40 W ERP" in w for w in warn))
        # 437 MHz puts the sound carrier above 440: a warning on 70cm (it is
        # mostly cabled into an attenuator), with where it would fit.
        refuse, warn = tx_check(437.0 * MHZ, *AM)
        self.assertEqual(refuse, [])
        self.assertTrue(any("OUTSIDE THE ALLOCATION" in w and "between 431.25 and 433.9 MHz" in w for w in warn))
        # The other bands still refuse a signal past their allocation.
        refuse, _ = tx_check(1324.0 * MHZ, *AM)
        self.assertTrue(refuse)

    def test_435_fits_405_and_625_without_sound_and_warns_for_625_with_it(self):
        self.assertIn(435.0 * MHZ, [c.freq for c in bands.BY_NAME["70cm"].channels])
        refuse, warn = tx_check(435.0 * MHZ, *AM)
        self.assertEqual(refuse, [])
        self.assertTrue(any("between 431.25 and 433.9 MHz" in w for w in warn))
        for occ in (sysa.plan(10e6)["occupied"], modem.am_plan(10e6, sound_offset=0)["occupied"],
                    modem.am_plan(5e6)["occupied"]):
            refuse, warn = tx_check(435.0 * MHZ, *occ)
            self.assertEqual(refuse, [], occ)
            self.assertFalse(any("OUTSIDE THE ALLOCATION" in w for w in warn), warn)
            self.assertTrue(any("Satellites only" in w for w in warn), warn)

    def test_23cm_simplex_is_allowed_with_its_overlaps_shown(self):
        refuse, warn = tx_check(1255.0 * MHZ, *FM)
        self.assertEqual(refuse, [])
        text = " ".join(warn)
        self.assertIn("1260-1270", text)          # satellite uplinks
        self.assertIn("Galileo", text)

    def test_23cm_repeater_input_with_am_vsb(self):
        refuse, warn = tx_check(1249.0 * MHZ, *AM)
        self.assertEqual(refuse, [])
        self.assertFalse(any("Galileo" in w for w in warn))

    def test_23cm_repeater_outputs_are_receive_only(self):
        refuse, _ = tx_check(1310.0 * MHZ, *FM)
        self.assertTrue(any("receive only" in r for r in refuse))

    def test_signal_off_the_bottom_of_the_band_is_refused(self):
        refuse, _ = tx_check(1245.0 * MHZ, *FM)
        self.assertTrue(any("outside the allocation 1240-1325 MHz (UK (Ofcom), Full)" in r for r in refuse), refuse)

    def test_6cm_5665_fits_its_block(self):
        refuse, warn = tx_check(5665.0 * MHZ, *FM)
        self.assertEqual(refuse, [])
        self.assertTrue(any("5668-5670" in w for w in warn))     # the narrowband slot beside it
        # A 30 MHz FPV-style signal exactly fills 5650-5680.
        refuse, _ = tx_check(5665.0 * MHZ, -15 * MHZ, 15 * MHZ)
        self.assertEqual(refuse, [])

    def test_6cm_outside_the_three_uk_blocks_is_refused(self):
        refuse, _ = tx_check(5705.0 * MHZ, *FM)          # FPV E2: inside 5650-5850, not UK
        self.assertTrue(any("outside the 6cm allocation (5650-5680, 5755-5765, 5820-5850 MHz" in r for r in refuse), refuse)
        refuse, _ = tx_check(5825.0 * MHZ, *FM)          # spills below 5820
        self.assertTrue(any("outside the allocation 5820-5850 MHz" in r for r in refuse), refuse)

    def test_narrowband_segment_is_not_an_atv_carrier(self):
        refuse, _ = tx_check(5761.0 * MHZ, -1 * MHZ, 1 * MHZ)
        self.assertTrue(any("not an ATV segment" in r for r in refuse))

    def test_off_band(self):
        refuse, _ = tx_check(2400 * MHZ, -1, 1)
        self.assertTrue(refuse)


class Range(unittest.TestCase):
    def test_stock_pluto_cannot_reach_6cm_and_says_how_to_fix_it(self):
        ok, why = bands.reachable(bands.BY_NAME["6cm"], (325 * MHZ, 3800 * MHZ))
        self.assertFalse(ok)
        self.assertIn("ad9364", why)
        self.assertIn("LibreSDR", why)
        ok, _ = bands.reachable(bands.BY_NAME["23cm"], (325 * MHZ, 3800 * MHZ))
        self.assertTrue(ok)

    def test_ad9364_reaches_everything(self):
        for b in bands.BANDS:
            self.assertTrue(bands.reachable(b, (70 * MHZ, 6000 * MHZ))[0])

    def test_defaults_are_bandplan_channels(self):
        for b in bands.BANDS:
            self.assertIn(b.default, [c.freq for c in b.channels])


A405 = (-3.55 * MHZ, 0.75 * MHZ)                 # System A: sound carrier to the vestige's top
CHANNELS = {"B1": 45.00, "B2": 51.75, "B3": 56.75, "B4": 61.75, "B5": 66.75}     # vision, MHz


class AmateurOnly(unittest.TestCase):
    def test_nothing_outside_the_amateur_allocations_passes_the_guard(self):
        # Every carrier from 40 MHz to 6 GHz in 250 kHz steps, AM-VSB, 405
        # lines and FM: the guard refuses wherever any of the signal is
        # outside a UK amateur allocation block - Band I included. The one
        # exception (Brian, 8 Oct 2026): a carrier inside 430-440 MHz whose
        # sidebands spill past it is warned about, not refused, because 70cm
        # is mostly sent down a cable into an attenuator.
        blocks = [blk for b in bands.BANDS for blk in txguard.GUARD.spans(UK, b.lo, b.hi)]
        # The UK allocation as docs/atv-research.md gives it (Ofcom), which
        # ATV 0.2.3 carried as tables: the dataset says the same.
        self.assertEqual(blocks, [(430e6, 440e6), (1240e6, 1325e6), (5650e6, 5680e6), (5755e6, 5765e6),
                                  (5820e6, 5850e6)])
        for occ in (AM, A405, FM):
            for k in range(0, int((6000 - 40) / 0.25) + 1):
                f = (40 + 0.25 * k) * MHZ
                inside = any(lo <= f + occ[0] and f + occ[1] <= hi for lo, hi in blocks)
                refuse, warn = tx_check(f, *occ)
                if inside:
                    continue
                if 430 * MHZ <= f <= 440 * MHZ:
                    self.assertTrue(any("OUTSIDE THE ALLOCATION" in w for w in warn), f / MHZ)
                else:
                    self.assertTrue(refuse, f / MHZ)
        for v in CHANNELS.values():
            self.assertTrue(tx_check(v * MHZ, *A405)[0])
        for _, v, _ in bands.UHF_CHANNELS:                    # the UHF cable channels: none is ours on air
            self.assertTrue(tx_check(v, *AM)[0])


class CableFeed(unittest.TestCase):
    """bands.cable_check: 405 lines on B1-B5 only, whole inside the radio."""

    def test_the_five_channels_on_a_radio_that_tunes_them(self):
        hackrf = (1 * MHZ, 6000 * MHZ)
        for name, v in CHANNELS.items():
            self.assertEqual(bands.cable_check(v * MHZ, *A405, "405", "am", hackrf), [], name)
            self.assertEqual(bands.cable_channel(v * MHZ)[0], name)
        # The channel table: sound 3.5 MHz below vision on every one.
        for name, v, a in bands.CABLE_CHANNELS:
            self.assertAlmostEqual(v - a, 3.5 * MHZ)
            self.assertAlmostEqual(v / MHZ, CHANNELS[name])

    def test_405_lines_on_am_only(self):
        hackrf = (1 * MHZ, 6000 * MHZ)
        for system, fmt in (("am", "pal"), ("fm", "pal"), ("fm", "mac"), ("fm", "lowpal")):
            refuse = bands.cable_check(45.0 * MHZ, *A405, fmt, system, hackrf)
            self.assertTrue(any("405-line System A only" in r for r in refuse), (system, fmt))

    def test_only_the_channels(self):
        hackrf = (1 * MHZ, 6000 * MHZ)
        for f in (44.99, 45.002, 48.25, 50.0, 1255.0, 433.0, 5665.0):
            refuse = bands.cable_check(f * MHZ, *A405, "405", "am", hackrf)
            self.assertTrue(any("B1-B5" in r or "Band I channels" in r for r in refuse), f)

    def test_a_tezuka_pluto_sends_b2_to_b5_and_a_stock_one_none(self):
        tezuka = (46.875001 * MHZ, 6000 * MHZ)
        refuse = bands.cable_check(45.0 * MHZ, *A405, "405", "am", tezuka)
        self.assertEqual(len(refuse), 1)
        self.assertIn("B2, B3, B4, B5", refuse[0])          # B1's sound is at 41.5 MHz
        for name in ("B2", "B3", "B4", "B5"):
            self.assertEqual(bands.cable_check(CHANNELS[name] * MHZ, *A405, "405", "am", tezuka), [])
        stock = (70 * MHZ, 6000 * MHZ)
        for v in CHANNELS.values():
            refuse = bands.cable_check(v * MHZ, *A405, "405", "am", stock)
            self.assertTrue(refuse and "none of B1-B5" in refuse[0])



class CableUhf(unittest.TestCase):
    """bands.cable_check on UHF: 625-line PAL (System I) on E21-E48 except
    E38, inside its 8 MHz channel and inside the radio (DESIGN.md "Cable
    feed")."""
    STOCK = (325 * MHZ, 3800 * MHZ)

    def test_the_channel_plan(self):
        # BVWS: vision 8N + 303.25 MHz, sound 6 MHz above; ITU-R BT.470-6 /
        # Wikipedia "CCIR System I": E21 470-478 (471.25 / 477.25), E36
        # 590-598 (591.25 / 597.25).
        table = {n: (v, a) for n, v, a in bands.UHF_CHANNELS}
        self.assertEqual(table["E21"], (471.25 * MHZ, 477.25 * MHZ))
        self.assertEqual(table["E36"], (591.25 * MHZ, 597.25 * MHZ))
        self.assertEqual(table["E48"], (687.25 * MHZ, 693.25 * MHZ))
        self.assertEqual(sorted(int(n[1:]) for n in table), [n for n in range(21, 49) if n != 38])
        for n, (v, a) in table.items():
            self.assertAlmostEqual(v, (8 * int(n[1:]) + 303.25) * MHZ)
            self.assertAlmostEqual(a - v, 6.0 * MHZ)
            self.assertEqual(bands.cable_channel(v)[0], n)
        # Every whole 8 MHz channel inside the band; E48's top is 694 MHz,
        # where the 700 MHz mobile band begins.
        u = bands.CABLE_UHF
        self.assertEqual((u.lo, u.hi), (470 * MHZ, 694 * MHZ))
        self.assertEqual(u.default, 591.25 * MHZ)
        self.assertIn(u.default, [c.freq for c in u.channels])
        self.assertTrue(all(bands.cable_channel(c.freq) for c in u.channels))

    def test_pal_on_every_channel_passes_on_a_stock_pluto(self):
        for n, v, _ in bands.UHF_CHANNELS:
            for fs in (8e6, 10e6, 16e6):
                occ = modem.am_plan(fs)["occupied"]
                self.assertEqual(bands.cable_check(v, *occ, "pal", "am", self.STOCK), [], (n, fs))

    def test_405_on_uhf_and_pal_on_band_i_are_refused(self):
        refuse = bands.cable_check(591.25 * MHZ, *AM, "405", "am", self.STOCK)
        self.assertTrue(any("625-line PAL (System I) only on UHF" in r for r in refuse))
        refuse = bands.cable_check(51.75 * MHZ, *A405, "pal", "am", (1 * MHZ, 6000 * MHZ))
        self.assertTrue(any("405-line System A only on Band I" in r for r in refuse))
        for system, fmt in (("fm", "pal"), ("fm", "mac"), ("fm", "lowpal")):
            refuse = bands.cable_check(591.25 * MHZ, *FM, fmt, system, self.STOCK)
            self.assertTrue(any("choose AM-VSB" in r for r in refuse), (system, fmt))

    def test_only_the_channels(self):
        # Between channels, E38 (radio astronomy), E49-E68 (700 and 800 MHz
        # mobile), E69, and the amateur bands' own channels.
        for f in (591.0, 591.252, 595.25, 607.25, 695.25, 799.25, 847.25, 855.25, 433.0, 1255.0, 1249.0):
            refuse = bands.cable_check(f * MHZ, *AM, "pal", "am", self.STOCK)
            self.assertTrue(any("E21-E48 except E38" in r and "not one" in r for r in refuse), f)

    def test_the_signal_stays_inside_its_8_mhz_channel(self):
        v = 591.25 * MHZ
        self.assertEqual(bands.cable_check(v, -1.25 * MHZ, 6.6 * MHZ, "pal", "am", self.STOCK), [])  # 6.5 MHz sound
        for occ in ((-1.25 * MHZ, 6.9 * MHZ), (-1.5 * MHZ, 6.1 * MHZ)):
            refuse = bands.cable_check(v, *occ, "pal", "am", self.STOCK)
            self.assertTrue(any("E36's 8 MHz channel (590-598 MHz)" in r for r in refuse), occ)

    def test_outside_what_the_radio_tunes(self):
        refuse = bands.cable_check(471.25 * MHZ, *AM, "pal", "am", (472 * MHZ, 6000 * MHZ))
        self.assertEqual(len(refuse), 1)
        self.assertIn("This radio tunes 472-6000 MHz; E21's signal", refuse[0])
        self.assertTrue(bands.reachable(bands.CABLE_UHF, self.STOCK)[0])
        self.assertFalse(bands.reachable(bands.CABLE_UHF, (1240 * MHZ, 6000 * MHZ))[0])


if __name__ == "__main__":
    unittest.main()
