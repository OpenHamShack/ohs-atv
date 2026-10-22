"""The window, driven offscreen against the simulator with a throw-away
config: it builds, the picture arrives and the badges say locked, transmit
is refused with the engine's own sentence until the licence is
acknowledged, the band chips and Settings change the radio, and the
screenshot tool will not run on anything but the simulator.

Nothing here opens a real radio. Every test runs the engine in real time,
so each waits (a few seconds) for the simulated station to lock."""
import atexit
import os
import shutil
import tempfile
import time
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from helpers import crop, narrow_station  # noqa: F401  (path setup)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="ohsatv-uitest-")
os.environ["OHSATV_UI_CONF"] = str(Path(_TMP) / "ui.json")
# Every directory a test here takes is made inside _TMP, so this one removal
# clears them all. At exit, not in tearDownModule: OHSATV_UI_CONF keeps
# pointing here for the rest of the process, and a later module that saves
# window preferences would otherwise recreate the directory after it went.
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

try:
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtGui import QImage
    from PyQt6.QtTest import QTest
    HAVE_QT = True
except ImportError:          # the engine's tests must still run without PyQt6
    HAVE_QT = False

from ohsatv import host, state
from ohsatv.audio import NullAudio
from ohsatv.radio import Radio

# AM-VSB on the simulator's 1275 MHz station: 10 MS/s, so the receive chain
# leaves the window plenty of CPU on a loaded test machine.
# The station's NICAM (simNicam) is left off here: these tests run in real
# time on a machine shared with other work, and decoding NICAM is a quarter
# of a core more; test_ui_extras.py turns it on.
BASE = dict(device="sim", audioBackend="null", band="23cm", system="am", rxFreq=1275.0e6, txFreq=1275.0e6,
            amRate=10_000_000, simStations=True, callsign="GM4BRI", locator="IO86", licenceAck=True, licenceClass="full",
            simNicam=False)


@unittest.skipUnless(HAVE_QT, "PyQt6 is not installed")
class UiBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ohsatv.ui import make_app
        cls.app = make_app(["test"])

    def make(self, device=None, tx_device=None, **over):
        from ohsatv.ui import build
        from ohsatv.ui.prefs import Prefs
        tmp = Path(tempfile.mkdtemp(prefix="ohsatv-ui-", dir=_TMP))
        cfg = state.defaults(("setting", "control"))
        cfg.update(BASE)
        cfg.update(over)
        if device is not None:
            device = device(cfg, float(cfg["amRate"] if cfg["system"] == "am" else cfg["fmRate"]))
        self.radio = Radio(cfg, conf_path=tmp / "c.json", data_dir=tmp / "data", audio=NullAudio(realtime=True),
                           device=device, tx_device=tx_device)
        self.prefs = Prefs(tmp / "ui.json")
        self.win, self.bridge = build(self.radio, self.prefs)
        self.win.resize(1366, 768)
        self.win.show()
        self.addCleanup(self._close)
        return self.win, self.bridge, self.radio

    def _close(self):
        from PyQt6 import sip
        if sip.isdeleted(self.win):
            return
        try:
            self.radio.ptt(False)
        finally:
            self.win.close()
            self.bridge.stop()
            self.pump(0.1)
            self._destroy(self.win, self.bridge)

    def _destroy(self, *objs):
        """Delete the window and the bridge now, on this - the GUI - thread.
        Closed and dropped, they were freed only by Python's cyclic garbage
        collector (the widgets and their Python halves refer to each other),
        which runs on whichever thread happens to allocate when it is due:
        one window in five went in the next test's engine thread
        (ohsatv-supervisor), deleting a whole widget tree while this thread
        was painting another - Qt objects may only be deleted on their own
        thread - and the suite now and then ended in a segfault (exit 139)
        late in the window tests. sip.delete runs the C++ destructors here;
        what the collector later frees is Python's half alone."""
        from PyQt6 import sip
        for o in objs:
            if not sip.isdeleted(o):
                sip.delete(o)
        self.app.processEvents()

    def pump(self, sec, until=None):
        end = time.monotonic() + sec
        while time.monotonic() < end:
            self.app.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.005)
        return until() if until is not None else True

    def wait_locked(self, timeout=20.0):
        win, bridge = self.win, self.bridge
        return self.pump(timeout, lambda: win.monitor.has_signal() and bridge.st.get("vLock")
                         and float(bridge.st.get("hLock") or 0) > 0.9)


class Window(UiBase):
    def test_a_closed_window_is_destroyed_on_the_gui_thread(self):
        # Not left for the garbage collector, which would free it on
        # whatever thread it ran on next - an engine thread, often (_destroy).
        from PyQt6 import sip
        win, bridge, _ = self.make()
        self.assertTrue(self.wait_locked())
        monitor = win.monitor
        self._close()
        self.assertTrue(sip.isdeleted(win) and sip.isdeleted(bridge) and sip.isdeleted(monitor))

    def test_hackrf_transmitter_is_named_and_its_underruns_shown(self):
        # Split operation: receive on the simulator, transmit on a HackRF
        # (fake libhackrf - nothing real is opened or keyed).
        from ohsatv.devices import hackrf
        from test_devices_hackrf import FakeLib
        lib = FakeLib()
        tx = hackrf.HackRFDevice(10e6, {}, lib=lib)
        win, bridge, radio = self.make(tx_device=tx, txDevice="hackrf", allowHardwareTx=True)
        self.assertTrue(self.wait_locked())
        self.assertIn("TX HackRF", " ".join(win.device.text()))
        radio.ptt(True)
        self.assertTrue(self.pump(3, until=lambda: lib.streaming))
        self.pump(0.5)
        for _ in range(100):                   # the USB side outruns the host
            lib.pull(262144)
        ok = self.pump(4, until=lambda: "HackRF underruns" in win.plan_card.short.text())
        radio.ptt(False)
        self.assertTrue(ok, win.plan_card.short.text())
        self.assertFalse(win.plan_card.short.isHidden())

    def test_a_radio_that_moved_by_itself_is_shown_until_it_is_history(self):
        # What the engine's guard publishes when a Pluto's rate or LO moves
        # (tests/test_radio.py, RadioMoves); the window must say it, in red,
        # on the plan card beside the other "the radio is not doing what
        # you think" lines.
        win, bridge, radio = self.make()
        self.pump(0.5)
        stuck = {"what": "sampleRate", "from": 10e6, "to": 2_330_997.0, "held": False, "t": time.time(),
                 "text": "The radio's sample rate moved from 10 MS/s to 2.330997 MS/s by itself and will not go back"}
        radio._status(radioMoved=[stuck])
        short = win.plan_card.short
        self.assertTrue(self.pump(4, until=lambda: "will not go back" in short.text()), short.text())
        self.assertFalse(short.isHidden())
        # Set back and held, long ago: no longer worth a red line.
        old = dict(stuck, held=True, t=time.time() - 60, text="The radio's sample rate moved ... set back, it held")
        radio._status(radioMoved=[old])
        self.assertTrue(self.pump(4, until=lambda: short.isHidden() or "moved" not in short.text()), short.text())

    def test_builds_receives_and_shows_locked(self):
        win, bridge, radio = self.make()
        self.assertTrue(self.wait_locked(), f"never locked: {radio.state()['rxLock']}")
        self.pump(0.5)
        self.assertIsNotNone(win.monitor.rx_img)
        self.assertEqual(win.monitor.mode(), "rx")
        self.assertGreater(bridge.frames, 5)
        tiles = {c: (v, col) for c, v, col in win.badges.tiles()}
        self.assertEqual(tiles["FIELD"], ("LOCKED", "ok"))
        self.assertEqual(tiles["SYNC"][1], "ok")
        self.assertTrue(tiles["SNR"][0].endswith("dB"))
        # The monitor shows a picture, not a flat field: grab it and look.
        img = win.monitor.grab().toImage()
        sr = win.monitor.screen_rect().toRect()
        grey = {QImage.pixel(img, QPoint(sr.x() + sr.width() * i // 9, sr.y() + sr.height() * j // 9)) & 0xFFFFFF
                for i in range(1, 9) for j in range(1, 9)}
        self.assertGreater(len(grey), 8)
        # The scopes received lines.
        self.assertTrue(win.wfm.last_t)
        self.assertTrue(win.spectrum.have_view)

    def test_no_signal_is_said_not_frozen(self):
        win, bridge, radio = self.make()
        self.assertTrue(self.wait_locked())
        win.tune(1290.0e6)          # nothing simulated here
        self.assertTrue(self.pump(10.0, lambda: not win.monitor.has_signal()))
        self.assertIn(win.monitor._no_signal_text()[0], ("NO SIGNAL", "PICTURE LOST", "LINE SYNC ONLY"))

    def test_snapshot_saves_a_4_3_png(self):
        win, bridge, radio = self.make()
        self.assertTrue(self.wait_locked())
        path = win.snapshot()
        self.assertTrue(path and Path(path).is_file(), path)
        self.assertEqual(Path(path).parent, Path(radio.data_dir))
        img = QImage(path)
        self.assertEqual((img.width(), img.height()), (768, 576))


class Transmit(UiBase):
    def test_refused_without_licence_with_the_engines_sentence(self):
        win, bridge, radio = self.make(licenceAck=False)
        self.pump(1.0, lambda: bool(bridge.st.get("txRefused")))
        reasons = radio.state()["txRefused"]
        self.assertTrue(reasons)
        self.assertIn("licence", reasons[0].lower())
        panel = win.tx_panel
        self.assertTrue(panel.refusal.isVisible())
        self.assertIn(reasons[0].rstrip("."), panel.refusal.text())
        self.assertEqual(panel.ptt.blocked, reasons[0])
        # Clicking PTT does not key, and the refusal is said in a toast.
        QTest.mouseClick(panel.ptt, Qt.MouseButton.LeftButton)
        self.pump(0.5)
        self.assertFalse(radio.state()["keyed"])
        self.assertTrue(any("licence" in t.lower() for t in win.toasts.texts()), win.toasts.texts())
        # The space bar cannot get round it either.
        win.space_press()
        self.pump(0.2)
        self.assertFalse(radio.state()["keyed"])
        win.space_release()

    def test_click_toggles_and_space_holds_on_the_simulator(self):
        win, bridge, radio = self.make()
        self.pump(1.0, lambda: bridge.st.get("running"))
        self.assertEqual(radio.state()["txRefused"], [])
        QTest.mouseClick(win.tx_panel.ptt, Qt.MouseButton.LeftButton)
        self.assertTrue(self.pump(3.0, lambda: radio.state()["keyed"]))
        self.assertTrue(self.pump(2.0, lambda: win.onair.isVisible()))
        QTest.mouseClick(win.tx_panel.ptt, Qt.MouseButton.LeftButton)
        self.assertTrue(self.pump(3.0, lambda: not radio.state()["keyed"]))
        win.space_press()
        self.assertTrue(self.pump(3.0, lambda: radio.state()["keyed"]))
        win.space_release()
        self.assertTrue(self.pump(3.0, lambda: not radio.state()["keyed"]))

    def test_power_slider_stops_at_the_ceiling(self):
        win, bridge, radio = self.make(txGainCeiling=-12.0)
        s = win.tx_panel.power
        self.assertEqual(s.cap, -12.0)
        s.setFocus()
        QTest.keyClick(s, Qt.Key.Key_End)
        self.pump(0.3)
        self.assertEqual(radio.state()["txGain"], -12.0)


class Formats(UiBase):
    def test_the_format_is_chosen_on_the_transmit_panel_and_said_to_be_non_standard(self):
        win, bridge, radio = self.make(system="fm", fmRate=5_000_000, fmDeviation=1.2e6, fmVideoBw=1.6e6,
                                       fmSub=0.0, rxFreq=1249.0e6, txFreq=1249.0e6)
        self.pump(2.0, lambda: bridge.st.get("running"))
        tp = win.tx_panel
        self.assertTrue(tp.format_row.isVisible())
        self.assertFalse(tp.format_note.isVisible())
        # Standard PAL at 5 MS/s: the plan card offers the narrow formats.
        self.assertIn("Mini-MAC", win.plan_card.why.text())
        sel = tp.format
        i = [v for v, _ in sel.options].index("mac")
        QTest.mouseClick(sel, Qt.MouseButton.LeftButton, pos=sel._cell(i).center().toPoint())
        self.pump(0.5, lambda: radio.state()["videoFormat"] == "mac")
        self.assertEqual(radio.state()["videoFormat"], "mac")
        self.pump(0.3)
        self.assertTrue(tp.format_note.isVisible())
        self.assertIn("OpenHamShack ATV only", tp.format_note.text())
        self.assertIn("not compatible with standard ATV receivers", tp.format_note.toolTip())
        self.assertIn("not compatible with standard ATV receivers", win.plan_card.format.text())
        self.assertEqual(win.plan_card.t_col.value, "MAC")

    def test_the_plan_card_describes_what_is_being_received(self):
        # 4 October, on air: Mini-MAC decoded in colour under a card that
        # still said PAL, "too narrow for colour". Another OpenHamShack sends
        # Mini-MAC; ours is set to PAL.
        win, bridge, radio = self.make(device=narrow_station("mac"), system="fm", fmRate=5_000_000,
                                       fmDeviation=1.2e6, fmVideoBw=1.6e6, fmSub=0.0, rxFreq=1255.0e6,
                                       txFreq=1255.0e6)
        card = win.plan_card
        ok = self.pump(8.0, lambda: bridge.st.get("rxFormat") == "mac" and card.t_col.value == "MAC")
        self.assertTrue(ok, (bridge.st.get("rxFormat"), card.t_col.value))
        self.assertEqual((card.t_snd.caption, card.t_snd.value), ("Sound", "DIGITAL"))
        self.assertNotIn("too narrow for colour", card.why.text().lower())
        self.assertIn("Receiving Mini-MAC", card.format.text())
        self.assertIn("Transmit stays PAL", card.format.text())
        # What PTT would send is unchanged.
        self.assertEqual(radio.state()["videoFormat"], "pal")
        self.assertFalse(radio.state()["plan"]["colour"])

    def test_am_offers_625_or_405_lines(self):
        # AM has one choice, and it is a standard: no OpenHamShack-only note.
        # (formatAuto off: the simulator's station here is 625 lines, which
        # the receiver would otherwise go back to - and the card with it.)
        win, bridge, radio = self.make(amRate=5_000_000, formatAuto=False)
        self.pump(1.0, lambda: bridge.st.get("running"))
        tp = win.tx_panel
        self.assertTrue(tp.format_row.isVisible())
        sel = tp.format
        self.assertEqual([v for v, _ in sel.options], ["pal", "405"])
        QTest.mouseClick(sel, Qt.MouseButton.LeftButton, pos=sel._cell(1).center().toPoint())
        self.assertTrue(self.pump(1.0, lambda: radio.state()["videoFormat"] == "405"))
        self.pump(0.3)
        self.assertFalse(tp.format_note.isVisible())
        card = win.plan_card
        self.assertTrue(self.pump(2.0, lambda: card.t_col.value == "405 MONO"))
        # At 5 MS/s without sound, and the card says why (research 21).
        self.assertEqual((card.t_snd.value, card.t_vid.value, card.t_occ.value), ("NONE", "3.00 MHz", "3.8 MHz"))
        self.assertIn("No sound at 5 MS/s", card.why.text())
        self.assertNotIn("not compatible", card.format.text())
        # Back on FM the narrow formats are offered again, and 405 is not.
        bridge.set(system="fm")
        self.assertTrue(self.pump(3.0, lambda: [v for v, _ in sel.options] == ["pal", "lowpal", "mac"]))

    def test_405_lines_found_on_the_air_are_named(self):
        # The simulator's 405-line station, with ours set to send 625 lines.
        win, bridge, radio = self.make(amRate=5_000_000, rxFreq=1287.5e6, txFreq=1287.5e6)
        card = win.plan_card
        ok = self.pump(10.0, lambda: bridge.st.get("rxFormat") == "405")
        self.assertTrue(ok, bridge.st.get("rxFormat"))
        self.pump(0.5)
        self.assertIn("Receiving 405 lines", card.format.text())
        self.assertIn("Transmit stays PAL", card.format.text())


class CableFeedUi(UiBase):
    def test_the_banner_band_and_power_follow_the_cable_feed(self):
        from ohsatv.ui.panels import CABLE_BANNER
        win, bridge, radio = self.make(amRate=5_000_000)
        self.pump(1.0, lambda: bridge.st.get("running"))
        tp = win.tx_panel
        self.assertFalse(tp.cable.isVisible())
        self.assertTrue(tp.power.isVisible())
        self.assertNotIn("Band I", [v for v, _ in win.band_sel.options])
        bridge.radio.set(authorise=True, cableFeedAck=True, cableFeed=True)
        self.assertTrue(self.pump(2.0, lambda: tp.cable.isVisible()))
        self.assertEqual(tp.cable.text(), CABLE_BANNER)
        self.assertIn("NOT FOR ANTENNAS", tp.cable.text())
        self.assertFalse(tp.power.isVisible())
        self.assertTrue(tp.power_fixed.isVisible())
        self.assertIn("Band I", [v for v, _ in win.band_sel.options])
        self.assertIn("UHF", [v for v, _ in win.band_sel.options])
        self.assertEqual(win.band_sel.value, "Band I")
        # UHF: chosen from the band list, banner and fixed power unchanged.
        bridge.set(band="UHF")
        self.assertTrue(self.pump(2.0, lambda: win.band_sel.value == "UHF"))
        self.assertTrue(tp.cable.isVisible() and tp.power_fixed.isVisible())
        bridge.radio.set(authorise=True, cableFeed=False)
        self.assertTrue(self.pump(2.0, lambda: not tp.cable.isVisible()))
        self.assertNotIn("Band I", [v for v, _ in win.band_sel.options])
        self.assertNotIn("UHF", [v for v, _ in win.band_sel.options])
        self.assertTrue(tp.power.isVisible())

    def test_settings_asks_for_the_confirmation_first(self):
        from ohsatv.ui.settings import SettingsDialog
        win, bridge, radio = self.make()
        page = [i for i, (_icon, name) in enumerate(SettingsDialog.PAGES) if name == "Transmit"][0]
        d = SettingsDialog(bridge, self.prefs, win, page=page)
        ack, on = d.extra["cableFeedAck"], d.extra["cableFeed"]
        self.assertFalse(ack.isChecked() or on.isChecked())
        self.assertFalse(on.isEnabled())
        self.assertIn("never to an antenna", ack.text)
        ack.setChecked(True)
        self.assertTrue(on.isEnabled())
        on.setChecked(True)
        self.assertTrue(d.save())
        st = radio.state()
        self.assertTrue(st["cableFeed"])
        self.assertEqual(st["band"], "Band I")
        d = SettingsDialog(bridge, self.prefs, win, page=page)
        d.extra["cableFeedAck"].setChecked(False)             # unticking the sentence turns it off
        self.assertFalse(d.extra["cableFeed"].isChecked())
        self.assertTrue(d.save())
        self.assertFalse(radio.state()["cableFeed"])


class Controls(UiBase):
    def test_a_band_the_radio_cannot_tune_is_greyed_with_the_reason(self):
        from ohsatv.devices.sim import SimDevice

        class StockPluto(SimDevice):
            """The simulator with a stock AD9363's tuning range."""
            def freq_range(self):
                return (325e6, 3800e6)
        win, bridge, radio = self.make(device=StockPluto)
        self.pump(1.0, lambda: bridge.st.get("range"))
        why = win.band_sel.disabled.get("6cm", "")
        self.assertIn("AD9364", why)
        self.assertNotIn("23cm", win.band_sel.disabled)
        # Choosing it anyway is refused by the chip itself: the band stays.
        sel = win.band_sel
        i = [v for v, _ in sel.options].index("6cm")
        QTest.mouseClick(sel, Qt.MouseButton.LeftButton, pos=sel._cell(i).center().toPoint())
        self.pump(0.3)
        self.assertEqual(radio.state()["band"], "23cm")

    def test_band_chip_changes_the_band(self):
        win, bridge, radio = self.make()
        self.pump(1.0, lambda: bridge.st.get("running"))
        sel = win.band_sel
        i = [v for v, _ in sel.options].index("6cm")
        cell = sel._cell(i).center().toPoint()
        QTest.mouseClick(sel, Qt.MouseButton.LeftButton, pos=cell)
        self.pump(0.5)
        st = radio.state()
        self.assertEqual(st["band"], "6cm")
        self.assertEqual(st["rxFreq"], 5665.0e6)
        # 6cm is FM: the engine restarts for the new system; let it finish.
        self.pump(10.0, lambda: radio.state()["running"] and radio.state()["fs"] == radio.state()["fmRate"])
        self.assertEqual(win.band_card.title.split(" · ")[1], "6cm")

    def test_channel_click_in_the_band_card_tunes(self):
        win, bridge, radio = self.make()
        self.pump(1.0, lambda: bridge.st.get("running"))
        cl = win.band_card.channels
        i = [c.freq for c in cl.channels()].index(1255.0e6)
        QTest.mouseClick(cl, Qt.MouseButton.LeftButton, pos=cl._chip(i).center().toPoint())
        self.pump(0.3)
        self.assertEqual(radio.state()["rxFreq"], 1255.0e6)

    def test_readout_wheel_and_arrows_stay_in_the_band(self):
        win, bridge, radio = self.make(rxFreq=1324.9e6, txFreq=1324.9e6)
        self.pump(0.5, lambda: bridge.st.get("running"))
        win.step_freq(+10)          # 1 MHz past the top of 23cm
        self.pump(0.2)
        self.assertEqual(radio.state()["rxFreq"], 1325.0e6)
        self.assertEqual(radio.state()["band"], "23cm")

    def test_settings_saves_a_setting_through_set(self):
        from ohsatv.ui.settings import SettingsDialog
        win, bridge, radio = self.make()
        calls = []
        orig = radio.set

        def spy(authorise=False, **kw):
            calls.append((authorise, dict(kw)))
            return orig(authorise=authorise, **kw)
        radio.set = spy
        d = SettingsDialog(bridge, self.prefs, win)
        d.fields["locator"].w.setText("IO86HA")
        d.fields["identText"].w.setText("23CM TEST")
        self.assertTrue(d.save())
        self.assertEqual(radio.state()["locator"], "IO86HA")
        self.assertEqual(radio.state()["identText"], "23CM TEST")
        self.assertTrue(calls and calls[-1][1] == {"locator": "IO86HA", "identText": "23CM TEST"}, calls)
        self.assertEqual(bridge.st["locator"], "IO86HA")

    def test_settings_licence_follows_the_country_and_the_band_card_shows_it(self):
        # The class list is the licence in force's, rebuilt as the country
        # is typed; saving it changes what the guard allows and what the
        # band card says (the shared dataset, not ATV's own tables).
        from ohsatv.ui.settings import SettingsDialog
        win, bridge, radio = self.make(band="6cm", system="fm", rxFreq=5665e6, txFreq=5665e6)
        self.pump(0.2)
        self.assertIn("5650–5680, 5755–5765, 5820–5850 MHz", win.band_card.title)
        d = SettingsDialog(bridge, self.prefs, win)
        uk = [d.class_combo.itemData(i) for i in range(d.class_combo.count())]
        self.assertEqual(uk, ["", "foundation", "intermediate", "full"])
        self.assertIn("UK (Ofcom), Full", d.licence_state.text())
        d.fields["stationCountry"].w.setText("K")
        us = [d.class_combo.itemData(i) for i in range(d.class_combo.count())]
        self.assertEqual(us, ["", "novice", "technician", "general", "advanced", "extra"])
        self.assertIn("No licence class chosen", d.licence_state.text())
        d.class_combo.setCurrentIndex(us.index("technician"))
        self.assertTrue(d.save())
        st = radio.state()
        self.assertEqual((st["stationCountry"], st["licenceClass"]), ("K", "technician"))
        self.assertEqual(st["licenceInForce"]["regimeShort"], "United States (FCC)")
        self.pump(0.2)
        self.assertIn("5650–5850 MHz", win.band_card.title)

    def test_settings_rate_list_says_what_each_rate_carries(self):
        from ohsatv.ui.settings import SettingsDialog
        win, bridge, radio = self.make()
        d = SettingsDialog(bridge, self.prefs, win, page=1)
        rl = d.extra["fmRate"]
        text, fits = rl.summary(20_000_000)
        self.assertTrue(fits)
        self.assertIn("colour", text)
        _t, fits16 = rl.summary(10_000_000)
        self.assertFalse(fits16)
        rl.select(16_000_000)
        self.assertEqual(d.collect().get("fmRate"), 16_000_000)

    @mock.patch.object(host, "WINDOWS", False)       # Linux's camera list; Windows' below and in test_windows_host
    def test_settings_sources_page_lists_and_saves_a_webcam(self):
        from unittest import mock

        from ohsatv.ui.settings import SettingsDialog
        win, bridge, radio = self.make()  # default webcam: /dev/video0
        sources_page = [i for i, (_icon, name) in enumerate(SettingsDialog.PAGES) if name == "Sources"][0]
        d = SettingsDialog(bridge, self.prefs, win)
        fake = [{"path": "/dev/video0", "name": "Integrated Camera"}, {"path": "/dev/video2", "name": "USB Camera"}]
        # Opening the page is what triggers the first listing (as Audio's
        # device lists do); do that under the mock so the real v4l2 probe is
        # never reached on a test host that may have no camera at all.
        with mock.patch("ohsatv.sources.list_webcams", return_value=fake):
            d.nav.select(sources_page)
        self.assertEqual(d.webcam_combo.count(), 2)
        self.assertEqual(d.webcam_combo.currentData(), "/dev/video0")  # the configured one, found and pre-selected
        self.assertIn("Integrated Camera", d.webcam_combo.currentText())
        d.webcam_combo.setCurrentIndex(d.webcam_combo.findData("/dev/video2"))
        self.assertTrue(d.save())
        self.assertEqual(radio.state()["webcam"], "/dev/video2")

    @mock.patch.object(host, "WINDOWS", False)       # Linux's camera list; Windows' below and in test_windows_host
    def test_settings_sources_page_keeps_a_missing_device_selected(self):
        from unittest import mock

        from ohsatv.ui.settings import SettingsDialog
        win, bridge, radio = self.make(webcam="/dev/video9")
        sources_page = [i for i, (_icon, name) in enumerate(SettingsDialog.PAGES) if name == "Sources"][0]
        d = SettingsDialog(bridge, self.prefs, win)
        with mock.patch("ohsatv.sources.list_webcams", return_value=[{"path": "/dev/video0", "name": "Other Cam"}]):
            d.nav.select(sources_page)
        self.assertEqual(d.webcam_combo.currentData(), "/dev/video9")
        self.assertIn("not connected", d.webcam_combo.currentText())
        self.assertTrue(d.save())
        self.assertEqual(radio.state()["webcam"], "/dev/video9")
        d.reject()

    @mock.patch.object(host, "WINDOWS", True)
    def test_settings_on_windows_offers_the_first_camera_and_names_each_once(self):
        from ohsatv.ui.settings import SettingsDialog
        win, bridge, radio = self.make(webcam="")
        sources_page = [i for i, (_icon, name) in enumerate(SettingsDialog.PAGES) if name == "Sources"][0]
        d = SettingsDialog(bridge, self.prefs, win)
        cams = [{"path": "Integrated Camera", "name": "Integrated Camera"},
                {"path": r"@device_pnp_\\?\usb#vid_0c45&pid_6366&mi_00#7&a", "name": "USB Camera"}]
        with mock.patch("ohsatv.sources.list_webcams", return_value=cams):
            d.nav.select(sources_page)
        items = [(d.webcam_combo.itemText(i), d.webcam_combo.itemData(i)) for i in range(d.webcam_combo.count())]
        self.assertEqual(items[0], ("First camera found", ""))
        self.assertEqual(d.webcam_combo.currentData(), "")              # the default, pre-selected
        self.assertEqual(items[1], ("Integrated Camera", "Integrated Camera"))
        self.assertTrue(items[2][0].startswith("USB Camera  (@device_pnp_"), items[2])
        d.webcam_combo.setCurrentIndex(1)
        self.assertTrue(d.save())
        self.assertEqual(radio.state()["webcam"], "Integrated Camera")


def fake_ffmpeg(directory: Path, rgb=(200, 40, 40)) -> tuple[Path, Path]:
    """An `ffmpeg` that records how it was called and writes solid `rgb`
    frames of the transmitted size to stdout at 25 a second - what a webcam
    through the real one would deliver, with no camera and no v4l2loopback.
    Returns (the program, the file its arguments are written to)."""
    import sys
    log = directory / "ffmpeg-args.jsonl"
    exe = directory / "ffmpeg"
    exe.write_text(f"""#!{sys.executable}
import json, sys, time
with open({str(log)!r}, "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
frame = bytes({list(rgb)!r}) * (720 * 576)
while True:
    sys.stdout.buffer.write(frame)
    sys.stdout.buffer.flush()
    time.sleep(0.04)
""")
    exe.chmod(0o755)
    return exe, log


def make_clip(path: Path, colour: str = "0x2850c8") -> None:
    """A two-second 16:9 clip of one solid colour with a 1 kHz tone."""
    import subprocess
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c={colour}:size=640x360:rate=25:duration=2", "-f", "lavfi", "-i",
                    "sine=frequency=1000:duration=2:sample_rate=48000", "-c:v", "ffv1", "-c:a", "flac",
                    "-shortest", str(path)], check=True)


def centre_rgb(img) -> tuple[int, int, int]:
    c = img.pixelColor(img.width() // 2, img.height() // 2)
    return c.red(), c.green(), c.blue()


def near(a, b, tol=24) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


class Sources(UiBase):
    """The transmit panel's Webcam and File… choices, end to end: the click,
    the engine starting ffmpeg, frames reaching the preview."""

    def pick(self, value):
        sel = self.win.tx_panel.source
        i = [v for v, _ in sel.options].index(value)
        QTest.mouseClick(sel, Qt.MouseButton.LeftButton, pos=sel._cell(i).center().toPoint())

    def fake_ffmpeg(self):
        from unittest import mock
        d = Path(tempfile.mkdtemp(prefix="ohsatv-cam-", dir=_TMP))
        exe, log = fake_ffmpeg(d)
        patch = mock.patch("ohsatv.sources.ffmpeg_path", return_value=str(exe))
        patch.start()
        self.addCleanup(patch.stop)
        return d, log

    @unittest.skipIf(sys.platform == "win32", "the stand-in ffmpeg is a script with a #! line, which Windows cannot run")
    def test_webcam_is_chosen_on_the_panel_and_its_picture_previewed(self):
        import json
        d, log = self.fake_ffmpeg()
        cam = d / "video7"          # exists, as a camera node would
        cam.touch()
        win, bridge, radio = self.make(webcam=str(cam))
        self.pump(2.0, lambda: bridge.st.get("running"))
        self.pick("webcam")
        thumb = win.tx_panel.thumb
        ok = self.pump(5.0, lambda: thumb.img is not None and near(centre_rgb(thumb.img), (200, 40, 40)))
        self.assertTrue(ok, (bridge.st.get("sourceStatus"), thumb.img and centre_rgb(thumb.img)))
        self.assertEqual(radio.state()["source"], "webcam")
        args = [json.loads(x) for x in log.read_text().splitlines()]
        self.assertEqual(len(args), 1)
        a = args[0]
        self.assertEqual(a[a.index("-f"):a.index("-i") + 2], ["-f", "v4l2", "-i", str(cam)])
        # A camera delivering frames is not something to warn about.
        self.pump(1.0, lambda: "frames" in (bridge.st.get("sourceStatus") or ""))
        self.assertIn("webcam", bridge.st["sourceStatus"])
        self.pump(0.2)
        self.assertFalse(win.tx_panel.src_status.isVisible(), win.tx_panel.src_status.text())
        # The monitor's TX preview is the camera, ident and all.
        QTest.mouseClick(win.view_sel, Qt.MouseButton.LeftButton, pos=win.view_sel._cell(1).center().toPoint())
        self.assertTrue(self.pump(2.0, lambda: win.monitor.mode() == "tx"))
        self.pump(0.3)
        self.assertTrue(near(centre_rgb(win.monitor.grab().toImage()), (200, 40, 40), tol=40))

    @mock.patch.object(host, "WINDOWS", False)       # "No webcam at <node>": the V4L2 check
    def test_a_missing_webcam_is_said_on_the_panel(self):
        d, log = self.fake_ffmpeg()
        win, bridge, radio = self.make(webcam=str(d / "video9"))
        self.pump(2.0, lambda: bridge.st.get("running"))
        self.pick("webcam")
        self.assertTrue(self.pump(3.0, lambda: win.tx_panel.src_status.isVisible()))
        self.assertIn("No webcam at", win.tx_panel.src_status.text())
        self.assertFalse(log.exists())
        # Keyed anyway, what goes out is black - but still carries the
        # callsign (DESIGN.md safety rule 6). It went out with none.
        thumb = win.tx_panel.thumb
        QTest.mouseClick(win.tx_panel.ptt, Qt.MouseButton.LeftButton)
        self.assertTrue(self.pump(3.0, lambda: radio.state()["keyed"]))
        # Let the previews already queued arrive first. The whole sequence
        # above can take 0.15 s, and a preview of the test card put just
        # before the switch to the webcam then came out of the feed after
        # this point: the "stray" failure of full-suite runs, traced on
        # 5 October with the source and the previews timestamped.
        self.pump(0.6)
        thumb.img = None
        self.assertTrue(self.pump(2.0, lambda: thumb.img is not None))
        img = thumb.img
        lit = [(x, y) for x in range(0, img.width(), 4) for y in range(0, img.height(), 4)
               if img.pixelColor(x, y).lightness() > 200]
        self.assertTrue(lit, "no ident in the transmitted picture")
        stray = [(x, y) for x, y in lit if not (x < img.width() / 2 and y > img.height() / 2)]
        self.assertFalse(stray, f"ident not bottom left: {len(stray)} of {len(lit)} lit points elsewhere in a "
                                f"{img.width()} x {img.height()} picture, e.g. {stray[:6]}; "
                                f"source {radio.state()['source']!r} {radio.state()['sourceStatus']!r}")

    def _answer_file_dialog(self, path: str | None, seen: list):
        """Answer the real (non-native) file dialog the panel opens: choose
        `path`, or cancel if None."""
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication, QFileDialog, QLineEdit

        def answer():
            dlg = QApplication.activeModalWidget()
            if not isinstance(dlg, QFileDialog):
                QTimer.singleShot(20, answer)
                return
            seen.append((dlg.windowTitle(), dlg.directory().absolutePath(), dlg.nameFilters()))
            # Typed and Enter, as an operator would: accept() itself is
            # out of reach on a dialog the static getter made in C++.
            edit = dlg.findChild(QLineEdit, "fileNameEdit")
            if path is None:
                QTest.keyClick(edit, Qt.Key.Key_Escape)
            else:
                edit.setText(path)
                QTest.keyClick(edit, Qt.Key.Key_Return)
        QTimer.singleShot(20, answer)

    def fake_home(self, videos: bool) -> Path:
        """A home directory of the test's own, with or without ~/Videos."""
        from unittest import mock
        home = Path(tempfile.mkdtemp(prefix="ohsatv-home-", dir=_TMP))
        if videos:
            (home / "Videos").mkdir()
        # USERPROFILE too: it is what Path.home() reads on Windows.
        patch = mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)})
        patch.start()
        self.addCleanup(patch.stop)
        return home

    def native_dialogs_off(self):
        # The Qt dialog rather than a desktop portal's: it is the one a test
        # can answer, and the offscreen platform has no other.
        self.app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
        self.addCleanup(self.app.setAttribute, Qt.ApplicationAttribute.AA_DontUseNativeDialogs, False)

    @unittest.skipUnless(__import__("shutil").which("ffmpeg"), "ffmpeg not installed")
    def test_file_is_chosen_in_the_dialog_and_plays_in_the_preview(self):
        home = self.fake_home(videos=True)
        d = Path(tempfile.mkdtemp(prefix="ohsatv-clip-", dir=_TMP))
        clip = d / "clip.mkv"
        make_clip(clip)
        win, bridge, radio = self.make()
        self.pump(2.0, lambda: bridge.st.get("running"))
        self.native_dialogs_off()
        self.assertFalse(win.tx_panel.file_row.isVisible())
        # Cancelled: nothing changes, and the chip goes back to the card.
        # Never used before, the dialog opens in ~/Videos.
        seen = []
        self._answer_file_dialog(None, seen)
        self.pick("file")
        self.assertTrue(seen)
        self.assertEqual(Path(seen[0][1]), home / "Videos")         # Qt may give C:/ for C:\
        self.pump(0.3)
        self.assertEqual(radio.state()["source"], "testcard")
        self.assertEqual(win.tx_panel.source.value, "testcard")
        self.assertFalse(win.tx_panel.file_row.isVisible())
        # Chosen: the engine plays it and the preview shows it.
        seen = []
        self._answer_file_dialog(str(clip), seen)
        self.pick("file")
        self.assertTrue(seen)
        self.assertTrue(any("*.mkv" in f for f in seen[0][2]), seen)
        st = radio.state()
        self.assertEqual((st["source"], Path(st["videoFile"])), ("file", clip))     # Qt's C:/ is C:\ too
        self.assertEqual(win.tx_panel.source.value, "file")
        # The panel says which file, by name, with the path on hover.
        self.pump(0.2)
        self.assertTrue(win.tx_panel.file_row.isVisible())
        self.assertEqual(win.tx_panel.file_name.text(), "clip.mkv")
        self.assertEqual(win.tx_panel.file_name.toolTip(), str(clip))
        thumb = win.tx_panel.thumb
        ok = self.pump(5.0, lambda: thumb.img is not None and near(centre_rgb(thumb.img), (0x28, 0x50, 0xc8)))
        self.assertTrue(ok, (bridge.st.get("sourceStatus"), thumb.img and centre_rgb(thumb.img)))
        self.pump(1.0, lambda: "frames" in (bridge.st.get("sourceStatus") or ""))
        self.pump(0.2)
        self.assertFalse(win.tx_panel.src_status.isVisible(), win.tx_panel.src_status.text())
        # Opened again, the dialog starts where that file was; cancelling
        # keeps it.
        seen = []
        self._answer_file_dialog(None, seen)
        self.pick("file")
        self.assertEqual(Path(seen[0][1]), d)
        self.pump(0.3)
        self.assertEqual((radio.state()["source"], radio.state()["videoFile"]), ("file", str(clip)))
        self.assertEqual(win.tx_panel.source.value, "file")
        # The folder is remembered across runs: in the window's preferences
        # file, read back afresh as the next start would.
        from ohsatv.ui.prefs import Prefs
        self.assertEqual(Prefs(self.prefs.path)["videoDir"], str(d))

    def test_the_file_dialog_opens_in_home_with_no_videos_folder(self):
        home = self.fake_home(videos=False)
        win, bridge, radio = self.make()
        self.native_dialogs_off()
        seen = []
        self._answer_file_dialog(None, seen)
        self.pick("file")
        self.assertEqual(Path(seen[0][1]), home)

    def test_a_long_file_name_is_shortened_to_fit_the_panel(self):
        # 1366 x 768 is the narrow panel. Nothing needs to play: the name is
        # the setting's, shown whether or not ffmpeg can open the file.
        d = Path(tempfile.mkdtemp(prefix="ohsatv-clip-", dir=_TMP))
        long_name = "A holiday video with a name far too long for any panel, summer 2026 - final edit.mkv"
        win, bridge, radio = self.make(source="file", videoFile=str(d / long_name))
        self.pump(0.5)
        label = win.tx_panel.file_name
        self.assertTrue(label.isVisible())
        shown = label.text()
        self.assertNotEqual(shown, long_name)
        self.assertTrue(shown.startswith("A holiday") and shown.endswith(".mkv"), shown)
        from PyQt6.QtGui import QFontMetricsF
        self.assertLessEqual(QFontMetricsF(label.font()).horizontalAdvance(shown), label.width())
        self.assertLessEqual(label.mapTo(win.tx_panel, label.rect().topRight()).x(), win.tx_panel.width())
        self.assertEqual(label.toolTip(), str(d / long_name))


    def test_the_file_dialog_is_readable_in_the_windows_theme(self):
        # Where the desktop offers no file dialog of its own (no platform
        # theme: a bare X session, a kiosk), Qt's is shown, styled by the
        # window's sheet. Its places list and its buttons were left to the
        # stock palette under the theme's light text: white on white, and
        # pale grey on grey.
        from PyQt6.QtCore import QSize, QTimer
        from PyQt6.QtWidgets import QApplication, QFileDialog, QListView, QPushButton
        win, bridge, radio = self.make()
        self.native_dialogs_off()
        found = {}

        def look():
            dlg = QApplication.activeModalWidget()
            if not isinstance(dlg, QFileDialog):
                QTimer.singleShot(20, look)
                return
            self.app.processEvents()
            side = dlg.findChild(QListView, "sidebar")
            # Without its icon, an entry is only its name on the background.
            side.setIconSize(QSize(0, 0))
            self.app.processEvents()
            found["sidebar"] = side.viewport().grab(side.visualRect(side.model().index(0, 0))).toImage()
            found["buttons"] = [b.grab().toImage() for b in dlg.findChildren(QPushButton) if b.isEnabled()]
            QTest.keyClick(dlg, Qt.Key.Key_Escape)
        QTimer.singleShot(20, look)
        self.pick("file")
        self.assertIn("sidebar", found)
        self.assertGreaterEqual(contrast(found["sidebar"]), 4.5, "places list")
        self.assertTrue(found["buttons"])
        for img in found["buttons"]:
            self.assertGreaterEqual(contrast(img), 4.5, "button")


def contrast(img) -> float:
    """WCAG contrast between an image's commonest colour (the background)
    and the colour furthest from it (the core of the text)."""
    from collections import Counter

    def lum(c):
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])
    px = [img.pixelColor(x, y) for x in range(img.width()) for y in range(img.height())]
    px = [(c.red(), c.green(), c.blue()) for c in px]
    bg = Counter(px).most_common(1)[0][0]
    lb = lum(bg)
    lt = max((lum(c) for c in px), key=lambda v: abs(v - lb))
    hi, lo = max(lb, lt), min(lb, lt)
    return (hi + 0.05) / (lo + 0.05)


class HalfDuplex(UiBase):
    """Keyed on a radio that cannot receive while it transmits, the monitor
    shows what is being sent - receive is paused, and a frozen received
    picture would look live - and goes back to receive on unkey."""

    def test_keyed_shows_the_tx_preview_on_air_and_unkey_returns_to_receive(self):
        from ohsatv.devices.sim import SimDevice

        class HalfDuplexSim(SimDevice):
            """The simulator, one direction at a time, as a USB Pluto is."""
            def duplex(self):
                return False
        # Bars go out; the station received is a test card: the two are told
        # apart across the monitor's middle.
        win, bridge, radio = self.make(device=HalfDuplexSim, source="bars")
        self.assertTrue(self.wait_locked())
        self.assertFalse(bridge.st["duplex"])
        mon = win.monitor
        self.assertEqual(mon.mode(), "rx")
        QTest.mouseClick(win.tx_panel.ptt, Qt.MouseButton.LeftButton)
        self.assertTrue(self.pump(3.0, lambda: radio.state()["keyed"]))
        self.assertTrue(self.pump(2.0, lambda: mon.mode() == "tx" and mon.tx_img is not None))
        self.assertTrue(self.pump(2.0, lambda: win.onair.isVisible()))
        self.assertEqual(win.picture_card.title, "Picture · transmit preview")
        self.assertIs(mon.shown_image(), mon.tx_img)
        self.assertEqual({v for _c, v, _col in win.badges.tiles()}, {"PAUSED"})
        self.pump(0.3)
        shown = mon.grab().toImage()
        sr = mon.screen_rect()
        tx = mon.tx_img
        # The bars, column by column, at mid-height (clear of the labels).
        for i in range(1, 7):
            x = (i + 0.5) / 8
            want = tx.pixelColor(int(tx.width() * x), tx.height() // 2)
            got = shown.pixelColor(int(sr.x() + sr.width() * x), int(sr.y() + sr.height() / 2))
            self.assertTrue(near((got.red(), got.green(), got.blue()), (want.red(), want.green(), want.blue()), 40),
                            (i, got.name(), want.name()))
        QTest.mouseClick(win.tx_panel.ptt, Qt.MouseButton.LeftButton)
        self.assertTrue(self.pump(3.0, lambda: not radio.state()["keyed"]))
        self.assertTrue(self.pump(2.0, lambda: mon.mode() == "rx"))
        self.assertEqual(win.picture_card.title, "Picture · received")
        self.assertTrue(self.wait_locked(10.0), "receive did not come back after unkey")
        self.assertIs(mon.shown_image(), mon.rx_img)
        self.assertTrue(self.pump(2.0, lambda: not win.onair.isVisible()))
        self.assertEqual(win.badges.tiles()[1][1], "LOCKED")

    def test_the_monitors_labels_keep_clear_of_each_other_while_keyed(self):
        # At 1366 x 768 "TX PREVIEW · RECEIVE PAUSED" and the centred
        # "ON AIR 0:02" were drawn over each other, cutting both.
        from ohsatv.devices.sim import SimDevice

        class HalfDuplexSim(SimDevice):
            def duplex(self):
                return False
        win, bridge, radio = self.make(device=HalfDuplexSim)
        self.pump(2.0, lambda: bridge.st.get("running"))
        QTest.mouseClick(win.tx_panel.ptt, Qt.MouseButton.LeftButton)
        self.assertTrue(self.pump(3.0, lambda: radio.state()["keyed"]))
        mon = win.monitor
        for size in ((1366, 768), (1920, 1080), (1100, 700)):
            win.resize(*size)
            self.pump(0.4)
            mon.grab()                      # paint now, at this size
            pills = mon.pills
            self.assertEqual(set(pills), {"mode", "onair", "info"}, size)
            sr = mon.screen_rect()
            for name, r in pills.items():
                self.assertTrue(sr.contains(r), (size, name))
                self.assertFalse(r.intersects(mon.snap_rect), (size, name))
            names = sorted(pills)
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    self.assertFalse(pills[a].intersects(pills[b]), (size, a, b, pills))

    def test_keyed_the_spectrum_shows_what_is_sent_labelled_tx_and_receive_returns(self):
        # It froze on the last received trace, which reads as a quiet band.
        # Split, so what is shown is plainly the carrier sent (1278 MHz) and
        # not the station received on 1275.
        from ohsatv.devices.sim import SimDevice

        class HalfDuplexSim(SimDevice):
            def duplex(self):
                return False
        win, bridge, radio = self.make(device=HalfDuplexSim, split=True, txFreq=1278.0e6)
        self.assertTrue(self.wait_locked())
        spec = win.spectrum
        self.assertTrue(self.pump(3.0, lambda: spec.have_view and spec.avg is not None))
        rx_scale = (spec.db_lo, spec.db_hi)
        self.assertFalse(spec.showing_tx())
        red = int(spec._tx_red)
        QTest.mouseClick(win.tx_panel.ptt, Qt.MouseButton.LeftButton)
        self.assertTrue(self.pump(3.0, lambda: radio.state()["keyed"]))
        self.assertTrue(self.pump(3.0, spec.showing_tx))
        rows = spec.wf.rows_total
        self.pump(1.0)
        spec.grab()
        # Labelled, over the spectrum.
        self.assertTrue(spec.r_spec.contains(spec.tx_label), spec.tx_label)
        # The waterfall keeps moving, each row marked as sent: red at both
        # edges, grey between - no palette colour to pass for received.
        self.assertGreater(spec.wf.rows_total - rows, 6)
        row = spec.wf.buf[spec.wf.head]
        self.assertEqual((int(row[0]), int(row[-1])), (red, red))
        mid = row[len(row) // 4: 3 * len(row) // 4]
        self.assertTrue(np.all(((mid >> 16) & 255) == (mid & 255)), "transmitted rows are grey")
        # AM-VSB: the vision carrier is the peak, where we transmit.
        n = len(spec.tx_avg)
        peak = spec.fmap.f((int(np.argmax(spec.tx_avg)) + 0.5) * spec.width() / n)
        self.assertAlmostEqual(peak, 1278.0e6, delta=3 * spec.fmap.span / n)
        # Unkeyed: receive is back, on its own scale, and new rows are its.
        QTest.mouseClick(win.tx_panel.ptt, Qt.MouseButton.LeftButton)
        self.assertTrue(self.pump(3.0, lambda: not radio.state()["keyed"]))
        self.assertTrue(self.pump(2.0, lambda: not spec.showing_tx()))
        self.assertEqual((spec.db_lo, spec.db_hi), rx_scale)
        rows = spec.wf.rows_total
        self.assertTrue(self.pump(5.0, lambda: spec.wf.rows_total > rows + 3))
        spec.grab()
        self.assertTrue(spec.tx_label.isEmpty())
        self.assertNotEqual(int(spec.wf.buf[spec.wf.head][0]), red)

    def test_full_duplex_keeps_the_received_spectrum(self):
        # Receive carries on (and shows our own signal off the air): no TX
        # label, and nothing the engine sends on the transmit feed.
        win, bridge, radio = self.make()
        self.pump(2.0, lambda: bridge.st.get("running"))
        QTest.mouseClick(win.tx_panel.ptt, Qt.MouseButton.LeftButton)
        self.assertTrue(self.pump(3.0, lambda: radio.state()["keyed"]))
        self.pump(1.0)
        win.spectrum.grab()
        self.assertFalse(win.spectrum.showing_tx())
        self.assertTrue(win.spectrum.tx_label.isEmpty())

    def test_the_labels_keep_off_the_ident_in_the_tx_preview(self):
        # On air, 5 October: the monitor's frequency-and-source pill sat on
        # the ident's second line (locator and text) in the TX preview; and
        # with the ident put top left, "TX PREVIEW · RECEIVE PAUSED" and
        # ON AIR sat on its callsign. A missing webcam sends the ident on
        # black, so every lit pixel of the transmitted picture is the
        # ident; none may be under any label, in any corner. (The snapshot
        # button is a control, not a label: it stays top right.)
        from ohsatv.devices.sim import SimDevice
        from unittest import mock

        class HalfDuplexSim(SimDevice):
            def duplex(self):
                return False
        d = Path(tempfile.mkdtemp(prefix="ohsatv-cam-", dir=_TMP))
        exe, _log = fake_ffmpeg(d)
        patch = mock.patch("ohsatv.sources.ffmpeg_path", return_value=str(exe))
        patch.start()
        self.addCleanup(patch.stop)
        win, bridge, radio = self.make(device=HalfDuplexSim, source="webcam", webcam=str(d / "video9"),
                                       identText="23CM TEST STATION")
        self.pump(2.0, lambda: bridge.st.get("running"))
        mon = win.monitor
        QTest.mouseClick(win.tx_panel.ptt, Qt.MouseButton.LeftButton)
        self.assertTrue(self.pump(3.0, lambda: radio.state()["keyed"]))
        for corner in ("bottom-left", "bottom-right", "top-left", "top-right"):
            radio.set(identPosition=corner)
            self.pump(0.5)              # frames from before the change, still in the feed
            mon.tx_img = None
            self.assertTrue(self.pump(3.0, lambda: mon.tx_img is not None))
            img = mon.tx_img
            lit = [(x, y) for x in range(0, img.width(), 2) for y in range(0, img.height(), 2)
                   if img.pixelColor(x, y).lightness() > 200]
            self.assertTrue(lit, corner)
            # The ident is in the corner asked for, so a monitor that only
            # moved its bottom label would fail the top corners.
            self.assertEqual(max(y for _x, y in lit) < img.height() / 2, "top" in corner, corner)
            for size in ((1366, 768), (1920, 1080)):
                win.resize(*size)
                self.pump(0.4)
                mon.grab()
                sr = mon.screen_rect()
                pills = mon.pills
                self.assertEqual(set(pills), {"mode", "onair", "info"}, (corner, size))
                for name, r in pills.items():
                    self.assertTrue(sr.contains(r), (corner, size, name))
                    under = [(x, y) for x, y in lit
                             if r.contains(sr.x() + (x + 0.5) * sr.width() / img.width(),
                                           sr.y() + (y + 0.5) * sr.height() / img.height())]
                    self.assertEqual(under, [], (corner, size, name, r))
                    self.assertFalse(r.intersects(mon.snap_rect), (corner, size, name))
                names = sorted(pills)
                for i, a in enumerate(names):
                    for b in names[i + 1:]:
                        self.assertFalse(pills[a].intersects(pills[b]), (corner, size, a, b))


class PlutoFind(UiBase):
    """Settings › Radio › Find against a fake libiio: the radios found are
    listed, choosing one fills in the address and makes the Pluto driver the
    radio, and nothing opens a radio to stream - Find only reads context
    attributes. The radio itself is the simulator passed in as the device,
    so a save that switches the setting to "pluto" restarts on the
    simulator: nothing here can reach a real radio."""

    LIBRE = "ip:198.51.100.50"

    def fake_iio(self):
        from test_devices_pluto import REAL_SCAN, FakeIIO
        scan = dict(REAL_SCAN)
        scan[self.LIBRE] = "198.51.100.50 (LibreSDR Rev.5 (Z7020-AD9363)), serial=1044730a1997000b"

        class Watched(FakeIIO):
            buffers = 0

            def Buffer(self, dev, n, cyclic):
                Watched.buffers += 1
                return super().Buffer(dev, n, cyclic)
        return Watched(scan=scan)

    def run_find(self, d, fake):
        import sys
        from unittest import mock

        from ohsatv.devices import pluto
        with mock.patch.dict(sys.modules, {"iio": fake}), \
                mock.patch.object(pluto, "_usb_network_addresses", return_value=[]), \
                mock.patch.object(pluto, "_resolve_ipv4", return_value=None):
            QTest.mouseClick(d.find_btn, Qt.MouseButton.LeftButton)
            self.assertFalse(d.find_btn.isEnabled())
            return self.pump(10.0, lambda: d.find_btn.isEnabled())

    def open_radio_page(self):
        from ohsatv.ui.settings import SettingsDialog
        page = [i for i, (_icon, name) in enumerate(SettingsDialog.PAGES) if name == "Radio"][0]
        d = SettingsDialog(self.bridge, self.prefs, self.win, page=page)
        d.show()
        self.addCleanup(d.close)
        return d

    def test_found_radios_are_listed_and_choosing_one_makes_it_the_radio(self):
        from ohsatv.devices.sim import SimDevice
        win, bridge, radio = self.make(device=SimDevice)
        self.pump(1.0, lambda: bridge.st.get("running"))
        calls = []
        orig = radio.set

        def spy(authorise=False, **kw):
            calls.append((authorise, dict(kw)))
            return orig(authorise=authorise, **kw)
        radio.set = spy
        d = self.open_radio_page()
        self.assertEqual(d.fields["device"].get(), "sim")
        fake = self.fake_iio()
        self.assertTrue(self.run_find(d, fake))
        self.assertTrue(d.found.isVisible())
        uris = [d.found.itemData(i) for i in range(d.found.count())]
        # The nano (seen over USB and over IP, kept once, as USB) and the LibreSDR.
        self.assertEqual(sorted(uris), sorted(["usb:3.4.5", self.LIBRE]))
        self.assertTrue(all(uri in d.found.itemText(i) for i, uri in enumerate(uris)))
        field = d.fields["plutoUri"].w
        self.assertEqual(field.text(), uris[0])          # the first is filled in at once
        self.assertEqual(d.fields["device"].get(), "pluto")
        # Back to the simulator by hand, then another radio picked from the
        # list: picking is what chooses the driver, every time.
        d.fields["device"].w.set_value("sim")
        d.found.setFocus()
        QTest.keyClick(d.found, Qt.Key.Key_Down)
        self.assertEqual(field.text(), uris[1])
        self.assertEqual(d.fields["device"].get(), "pluto")      # the LibreSDR is a Pluto to the driver
        self.assertFalse(d.find_msg.isVisible())
        self.assertTrue(d.save())
        # One set(), from Settings, carrying both - so Save changes the radio.
        self.assertEqual(calls, [(True, {"device": "pluto", "plutoUri": uris[1]})])
        st = radio.state()
        self.assertEqual((st["device"], st["plutoUri"]), ("pluto", uris[1]))
        # The restart that follows comes back receiving, not keyed, and
        # nothing was streamed through libiio.
        self.assertTrue(self.pump(10.0, lambda: radio.state()["running"]))
        self.pump(0.5)
        self.assertFalse(radio.state()["keyed"])
        self.assertEqual(type(fake).buffers, 0)

    def test_nothing_found_says_why_and_leaves_the_address(self):
        from test_devices_pluto import FakeIIO
        win, bridge, radio = self.make(plutoUri="ip:192.168.2.1")
        d = self.open_radio_page()
        self.assertTrue(self.run_find(d, FakeIIO(scan={})))
        self.assertFalse(d.found.isVisible())
        self.assertTrue(d.find_msg.isVisible())
        self.assertIn("No Pluto found", d.find_msg.text())
        self.assertEqual(d.fields["plutoUri"].w.text(), "ip:192.168.2.1")
        self.assertEqual(d.fields["device"].get(), "sim")     # nothing chosen, nothing switched


@unittest.skipUnless(HAVE_QT, "PyQt6 is not installed")
class Teletext(UiBase):
    """The transmit panel's teletext row and the viewer, keyed on the
    simulator with the sample service: pages arrive over our own loopback
    and the viewer shows, navigates and steps them."""

    def service(self):
        from ohsatv import ttxsample
        return str(ttxsample.write(Path(tempfile.mkdtemp(prefix="ohsatv-ttx-", dir=_TMP))))

    def test_viewer_shows_and_navigates_the_received_pages(self):
        win, bridge, radio = self.make(ttxOn=True, ttxDir=self.service(), ttxInitial="100")
        self.pump(2.0, lambda: bridge.st.get("running"))
        self.assertIn("Sending 4 pages", win.tx_panel.ttx_note.text())
        bridge.ptt(True)
        self.assertTrue(self.pump(15.0, lambda: all(radio.ttx_page(x) for x in ("100", "101", "102", "103"))))
        # T swaps the picture for the viewer; it opens on the sender's
        # initial page (8/30) and shows it.
        QTest.keyClick(win, Qt.Key.Key_T)
        self.pump(0.5)
        self.assertIs(win.picture_stack.currentWidget(), win.ttx_view)
        v = win.ttx_view
        # The viewer learns the sender's initial page from the 8/30 header,
        # which it only notices on its own 0.5 s tick (on_tick) - a second
        # poll on top of the one above, on a page already confirmed
        # received. Give it the same 15 s as that one: under load the two
        # ticks can both lag, and a short wait here read a None page and
        # failed on the subscript below rather than on this line.
        self.assertTrue(self.pump(15.0, lambda: v.page is not None))
        self.assertEqual(v.page["label"], "100")
        self.assertIn(b"TELETEXT", v.rows[1])
        self.assertTrue(win.picture_card.title.startswith("Teletext"))
        # The fastext keys carry the page's links; F1 follows red.
        self.assertEqual([k.target for k in v.keys], ["101", "102", "103", "100"])
        QTest.keyClick(win, Qt.Key.Key_F1)
        self.pump(0.3)
        self.assertEqual(v.page["label"], "101")
        # Three digits go to a page; the header shows them as they are typed.
        for d in "10":
            QTest.keyClick(win, getattr(Qt.Key, f"Key_{d}"))
        self.pump(0.2)
        self.assertTrue(v.rows[0].startswith(b"P10-"))
        QTest.keyClick(win, Qt.Key.Key_3)
        self.pump(0.3)
        self.assertEqual(v.page["label"], "103")
        # Reveal shows the concealed word; the pixels change.
        before = v.image().copy()
        QTest.keyClick(win, Qt.Key.Key_R)
        self.pump(0.2)
        self.assertTrue(v.reveal)
        self.assertNotEqual(before, v.image())
        # The carousel: both subpages held, PgDn steps and holds.
        v.go("102")
        self.assertTrue(self.pump(10.0, lambda: len((radio.ttx_page("102") or {}).get("subs", [])) == 2))
        v.refresh()
        first = v.page["sub"]
        QTest.keyClick(win, Qt.Key.Key_PageDown)
        self.pump(0.3)
        self.assertTrue(v.hold)
        self.assertNotEqual(v.page["sub"], first)
        # A page number in the text is a link: clicking "101" on the index.
        v.go("100")
        self.pump(0.3)
        y = next(i for i, r in enumerate(v.rows) if b"101" in r)
        x = v.rows[y].index(b"101") + 1
        sr = v.canvas.screen_rect()
        pos = QPoint(int(sr.x() + (x + 0.5) * sr.width() / 40), int(sr.y() + (y + 0.5) * sr.height() / 25))
        QTest.mouseClick(v.canvas, Qt.MouseButton.LeftButton, pos=pos)
        self.pump(0.3)
        self.assertEqual(v.page["label"], "101")
        bridge.ptt(False)
        QTest.keyClick(win, Qt.Key.Key_T)
        self.pump(0.3)
        self.assertIs(win.picture_stack.currentWidget(), win.monitor)

    def test_the_panel_says_why_teletext_is_not_sent(self):
        win, bridge, radio = self.make(system="fm", fmRate=5_000_000, fmDeviation=1.2e6, fmVideoBw=1.6e6,
                                       fmSub=0.0, rxFreq=1249.0e6, txFreq=1249.0e6, ttxDir=self.service())
        self.pump(2.0, lambda: bridge.st.get("running"))
        tp = win.tx_panel
        self.assertFalse(tp.ttx_note.isVisible())
        tp.ttx_on.set_on(True, emit=True)
        self.pump(0.5, lambda: tp.ttx_note.isVisible())
        self.assertTrue(radio.state()["ttxOn"])
        self.assertIn("Teletext and subtitles are not sent", tp.ttx_note.toolTip())
        self.assertIn("1.60 MHz", tp.ttx_note.toolTip())
        self.assertEqual(tp.ttx_note.colour, "warn")
        # A bad initial page is refused with the engine's sentence and put back.
        tp.ttx_initial.setText("999")
        tp._set_initial()
        self.assertEqual(radio.state()["ttxInitial"], "100")
        self.assertEqual(tp.ttx_initial.text(), "100")

    def test_a_typed_caption_comes_back_over_the_picture(self):
        win, bridge, radio = self.make(ttxOn=True, ttxDir="")
        self.pump(2.0, lambda: bridge.st.get("running"))
        tp = win.tx_panel
        self.assertTrue(tp.caption.isVisible())
        bridge.ptt(True)
        self.assertTrue(self.wait_locked())
        # Long enough that subtitles.caption_seconds clamps it to its 10 s
        # ceiling (max(3.0, min(10.0, 1.5 + len/13)) - 142 characters is
        # already past the point where more text buys more time). A short
        # caption ("Hello from the panel", ~3 s) raced the round trip to
        # the viewer on a loaded host and lost: ttx_store keeps only the
        # page's latest state, so once the carousel moved on and erased
        # 888 there was nothing left to wait for - no timeout, however
        # long, recovers a page that was overwritten before this window's
        # own poll (on a 0.2 s tick of its own) ever saw it.
        caption = ("Hello from the panel, typed long enough that its time on screen outlasts a slow round "
                   "trip through the simulated channel on a loaded test host")

        def send():
            tp.caption.setFocus()
            QTest.keyClicks(tp.caption, caption)
            QTest.keyClick(tp.caption, Qt.Key.Key_Return)
            self.assertEqual(tp.caption.text(), "")                    # sent, and the box cleared
            self.assertTrue(self.pump(2.0, lambda: radio.state().get("ttxSub") == caption))

        send()
        mon = win.monitor
        # Even the 10 s ceiling is a wall clock the viewer races against,
        # not a budget this thread controls: GUI-thread scheduling delay
        # under heavy contention is unbounded in the worst case, however
        # generous the window. So the precondition is kept alive instead
        # of just waited on longer - retyping (a real operator facing a
        # frozen set would do the same) buys a fresh 10 s each time,
        # rather than hoping one window is enough.
        for _ in range(3):
            if self.pump(7.0, lambda: mon.sub_img is not None):
                break
            send()
        else:
            self.fail("the typed caption never came back as a subtitle")
        # Drawn: the bottom of the screen changes when subtitles are switched off.
        on = mon.grab().toImage()
        win.show_subtitles(False)
        self.pump(0.3)
        off = mon.grab().toImage()
        self.assertNotEqual(on, off)
        self.assertFalse(win.subs_btn.checked)
        bridge.ptt(False)

    @unittest.skipUnless(__import__("shutil").which("ffmpeg"), "ffmpeg not installed")
    def test_the_file_row_pauses_and_seeks(self):
        import subprocess
        clip = str(Path(tempfile.mkdtemp(prefix="ohsatv-clip-", dir=_TMP)) / "clip.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-t", "10",
                        "-pix_fmt", "yuv420p", clip], check=True, timeout=60)
        win, bridge, radio = self.make(source="file", videoFile=clip)
        tp = win.tx_panel
        self.assertTrue(self.pump(6.0, lambda: (bridge.st.get("filePos") or 0) > 0.3))
        self.assertTrue(tp.file_pos.isVisible())
        self.assertAlmostEqual(tp.file_pos.hi, 10.0, delta=0.2)
        QTest.mouseClick(tp.file_pause, Qt.MouseButton.LeftButton)
        self.assertTrue(self.pump(2.0, lambda: bridge.st.get("filePaused")))
        self.assertEqual(tp.file_pause.text, "Play")
        tp.file_pos.committed.emit(6.0)                                  # a drag released at 6 s
        self.pump(0.5)
        self.assertAlmostEqual(radio.state()["filePos"], 6.0, delta=0.05)
        QTest.mouseClick(tp.file_pause, Qt.MouseButton.LeftButton)
        self.assertTrue(self.pump(4.0, lambda: (bridge.st.get("filePos") or 0) > 6.3))

    def test_the_viewer_saves_a_page_and_everything(self):
        from ohsatv import tti
        win, bridge, radio = self.make(ttxOn=True, ttxDir=self.service(), ttxInitial="101")
        self.pump(2.0, lambda: bridge.st.get("running"))
        bridge.ptt(True)
        self.assertTrue(self.pump(15.0, lambda: all(radio.ttx_page(x) for x in ("100", "101", "103"))))
        win.show_text(True)
        v = win.ttx_view
        # Same catch-up-to-the-header lag as the viewer test above: give it
        # the 15 s already given to receiving the pages themselves, not a
        # shorter number invented for this one call.
        self.assertTrue(self.pump(15.0, lambda: (v.page or {}).get("label") == "101"))     # the initial page
        out = Path(tempfile.mkdtemp(prefix="ohsatv-save-", dir=_TMP))
        said = []
        bridge.toast.connect(lambda it: said.append(it["text"]))
        p = v.save_page(str(out / "page.tti"))
        self.assertEqual(tti.load_service(p).labels(), ["101"])
        self.assertTrue(v.save_all(str(out / "all")))
        self.assertIn("P100.tti", os.listdir(out / "all"))
        self.assertTrue(any(f.endswith(".t42") for f in os.listdir(out / "all")))
        self.assertTrue(any("Saved page 101" in s for s in said))
        self.assertTrue(any("and the .t42 capture" in s for s in said))
        bridge.ptt(False)

    def test_captions_without_teletext_say_why(self):
        win, bridge, radio = self.make()
        self.pump(2.0, lambda: bridge.st.get("running"))
        tp = win.tx_panel
        self.assertFalse(tp.caption.isVisible())                       # teletext off: no 888 row
        seen = []
        bridge.toast.connect(lambda it: seen.append(it["text"]))
        tp.caption.setText("x")
        tp._send_caption()
        self.assertTrue(seen and "teletext" in seen[-1].lower())

    def test_no_teletext_is_said_in_the_viewer(self):
        win, bridge, radio = self.make()
        self.pump(2.0, lambda: bridge.st.get("running"))
        win.show_text(True)
        self.pump(1.5)
        self.assertEqual(win.ttx_view.message(), "No teletext on this signal")
        self.assertIsNone(win.ttx_view.page)


class ScreenshotGuard(unittest.TestCase):
    def test_refuses_anything_but_the_simulator(self):
        from ohsatv.ui.screenshots import NotSimulator, require_sim

        class Fake:
            def __init__(self, device, real):
                self._d = device
                self.dev = type("D", (), {"real": real})()

            def state(self):
                return {"device": self._d}
        with self.assertRaises(NotSimulator):
            require_sim(Fake("pluto", True))
        with self.assertRaises(NotSimulator):
            require_sim(Fake("sim", True))          # a "sim" setting on a device that says it is real
        require_sim(Fake("sim", False))


if __name__ == "__main__":
    unittest.main()
