"""The window's NICAM, offscreen against the simulator's AM-VSB station on
1275 MHz (which sends NICAM stereo): the badge that says which sound is
heard, and the transmit panel's switch and its refusal. Nothing here opens
a real radio."""
import unittest

from test_ui import HAVE_QT, UiBase


@unittest.skipUnless(HAVE_QT, "PyQt6 is not installed")
class Nicam(UiBase):
    def make(self, device=None, tx_device=None, **over):
        return super().make(device, tx_device, **dict(dict(simNicam=True), **over))

    def test_the_badge_says_which_sound_is_heard(self):
        win, bridge, radio = self.make()
        self.assertTrue(self.wait_locked())
        # (Up to 30 s: on a machine shared with other work the receiver can
        # take a while to hold NICAM's error rate under USE_BER.)
        skipped = radio.state()["deviceStatus"].get("skipped", 0)
        overran = ""
        if not self.pump(30, lambda: bridge.st.get("nicamActive")):
            # The simulator drops samples, as a radio would overrun, when
            # the receiver falls half a second behind real time. With the
            # window drawing beside it, the nightly VM's receiver (a 3 GHz
            # Broadwell Xeon) cannot keep 10 MS/s: it lost seconds of
            # signal, NICAM's error rate sat near 2e-2, and the badge never
            # had stereo to show. That is the machine, not the badge -
            # NICAM's own reception is test_radio_extras', which runs
            # without a window and passes there. So the badge is still
            # checked, on the status a receiver that kept up reports, and
            # the test ends skipped with the reason rather than passed.
            # A receiver that kept up and still found no NICAM fails.
            st = radio.state()
            lost = st["deviceStatus"].get("skipped", 0) - skipped
            if lost <= 0:
                self.fail(st)
            overran = (f"the receiver could not keep up in real time here ({lost / st['fs']:.1f} s of signal "
                       f"dropped, NICAM BER {st['nicamBer']}, {st['fps']} fps); the badge was checked on "
                       f"stereo status set by the test")
            bridge.st.update(nicamLock=True, nicamActive=True, nicamMode="stereo", nicamBer=1e-5)
        tiles = {c.split()[0]: (c, v, col) for c, v, col in win.badges.tiles()}
        cap, val, col = tiles["NICAM"]
        self.assertEqual((val, col), ("STEREO", "ok"))
        self.assertTrue(cap.startswith("NICAM "), cap)          # the error rate rides in the caption
        # FM when the analogue sound has taken over (asked of the badge
        # directly: the engine would set it straight back).
        bridge.st.update(nicamActive=False, nicamBer=2e-3)
        tiles = {c.split()[0]: (c, v, col) for c, v, col in win.badges.tiles()}
        self.assertEqual(tiles["NICAM"], ("NICAM 2E-3", "FM", "warn"))
        bridge.st.update(nicamLock=False)
        tiles = {c.split()[0]: (v, col) for c, v, col in win.badges.tiles()}
        self.assertEqual(tiles["NICAM"], ("—", "text3"))
        if overran:
            self.skipTest(overran)

    def test_the_switch_sets_the_engine_and_says_why_not(self):
        win, bridge, radio = self.make()
        panel = win.tx_panel
        panel.nicam.set_on(True, emit=True)
        self.pump(0.3)
        self.assertTrue(radio.state()["nicam"])
        self.assertTrue(panel.extras_note.isHidden())
        self.assertIn("NICAM-728", win.plan_card.extras.text())
        # At 6.25 MS/s it does not fit: the panel says so, nothing is sent.
        bridge.set(amRate=6_250_000)
        self.pump(3.0, lambda: not panel.extras_note.isHidden())
        self.assertFalse(panel.extras_note.isHidden())
        self.assertIn("no nicam", panel.extras_note.text().lower())


if __name__ == "__main__":
    unittest.main()
