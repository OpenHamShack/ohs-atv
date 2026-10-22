"""Render the screenshots in docs/screenshots from a live simulated radio.

    QT_QPA_PLATFORM=offscreen python3 -m ohsatv.ui.screenshots [outdir] [--only name,...]

Runs the real window against the simulator in real time, with a throw-away
config, data and window-preferences directory, so nothing of the operator's
is touched. Each shot waits for the picture to lock and the waterfall to
fill first. One shot keys the transmitter - which is allowed here only
because the device is the simulator, which sends nothing: the tool refuses
to run on anything else, and checks again immediately before keying.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path


class NotSimulator(RuntimeError):
    pass


def require_sim(radio) -> None:
    """The one rule this tool must never break: it keys, so the radio must be
    the simulator - by the setting *and* by the device object, which says
    whether it is real hardware. Checked at start and again before keying."""
    s = radio.state()
    dev = getattr(radio, "dev", None)
    if s.get("device") != "sim" or (dev is not None and getattr(dev, "real", True)):
        raise NotSimulator("The screenshot tool keys the transmitter and runs only on the simulator; "
                           f"the device here is {s.get('device')!r}.")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    only = None
    args = []
    it = iter(argv)
    for a in it:
        if a == "--only":
            only = set(next(it, "").split(","))
        elif not a.startswith("--"):
            args.append(a)
    out = Path(args[0]) if args else Path(__file__).resolve().parents[2] / "docs" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="ohsatv-shots-")
    # The throw-away config and data directory of a simulated radio, gone
    # when the tool exits however it exits (it used to stay in /tmp).
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["OHSATV_CONF"] = str(Path(tmp) / "config.json")
    os.environ["OHSATV_DATA"] = str(Path(tmp) / "data")
    os.environ["OHSATV_UI_CONF"] = str(Path(tmp) / "ui.json")

    from .. import state
    from ..audio import NullAudio
    from ..radio import Radio
    from . import build, make_app, shutdown
    from .prefs import Prefs

    app = make_app(["shots"])

    def wait(sec):
        end = time.monotonic() + sec
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.005)

    def want(*names):
        return not only or bool(only & set(names))

    def shot(widget, name, size=None):
        if size:
            widget.resize(*size)
            wait(1.0)
        if not want(name):
            return
        wait(0.3)
        p = out / f"{name}.png"
        widget.grab().save(str(p))
        print("wrote", p, flush=True)

    def wait_locked(bridge, timeout=15.0, settle=5.0, keyed=False):
        # On a loaded machine the decoder can acquire badly and stay there
        # (locked, but with a fraction of its lines and a doubled picture);
        # a restart re-acquires. Up to two of them before shooting anyway.
        for _attempt in range(3):
            if _good_lock(bridge, timeout):
                break
            print("lock poor (H %.0f %%); restarting the receiver" % (100 * float(bridge.st.get("hLock") or 0)),
                  flush=True)
            radio.stop(save=False)       # unkeys, as any restart does
            radio.start()
            wait(1.0)
            if keyed:
                require_sim(radio)
                bridge.ptt(True)
        wait(settle)

    def _good_lock(bridge, timeout):
        end = time.monotonic() + timeout
        # Locked *well*: a decoder that has just acquired can report "locked"
        # with a fraction of its lines found, and that is not the picture to show.
        while time.monotonic() < end and not (bridge.st.get("rxLock") == "locked" and bridge.picture_age() < 0.3
                                              and float(bridge.st.get("hLock") or 0) >= 0.98):
            wait(0.1)
        return float(bridge.st.get("hLock") or 0) >= 0.98

    cfg = state.defaults(("setting", "control"))
    cfg.update(device="sim", audioBackend="null", callsign="GM4BRI", locator="IO86", identText="OPENHAMSHACK ATV",
               licenceAck=True, allowHardwareTx=False, band="23cm", system="fm", rxFreq=1255.0e6, txFreq=1255.0e6,
               simStations=True, txGain=-30.0)
    radio = Radio(cfg, persist=False, data_dir=Path(tmp) / "data", audio=NullAudio(realtime=True))
    require_sim(radio)
    prefs = Prefs()
    prefs["theme"] = "dark"
    win, bridge = build(radio, prefs)
    require_sim(radio)
    win.resize(1920, 1080)
    win.show()
    try:
        # --- FM on 23cm, receiving the simulated station on 1255
        wait_locked(bridge, settle=7.0)
        shot(win, "main-fm-23cm-1920")
        shot(win, "main-1366", (1366, 768))
        win.resize(1920, 1080)
        wait(1.0)
        if want("main-light-1920"):
            win.toggle_theme()
            wait(5.0)         # the rebuild costs the receiver a few frames; let colour come back
            shot(win, "main-light-1920")
            win.toggle_theme()
            wait(1.0)
        if want("settings-station", "settings-radio", "settings-transmit", "settings-fm"):
            from .settings import SettingsDialog
            d = SettingsDialog(bridge, prefs, win, page=0)
            d.resize(1000, 740)
            d.show()
            shot(d, "settings-station")
            for page, name in ((1, "settings-radio"), (2, "settings-transmit"), (4, "settings-fm")):
                d.nav.select(page)
                wait(0.4)
                shot(d, name)
            d.reject()
            wait(0.3)
        if want("shortcuts-1920"):
            win.overlay.open()
            wait(0.5)
            shot(win, "shortcuts-1920")
            win.overlay.close_()
        # --- keyed on the simulator: our own GM4BRI card round the loop
        if want("main-transmitting-1920"):
            require_sim(radio)
            bridge.set(view="rx")
            if not bridge.ptt(True):
                raise RuntimeError("the simulator refused to key: " + bridge.refusal())
            wait_locked(bridge, timeout=15.0, settle=6.0, keyed=True)
            shot(win, "main-transmitting-1920")
            bridge.ptt(False)
            wait(1.0)
        # --- teletext: the sample service keyed on the simulator, sliced off
        # our own loopback and shown in the viewer
        ttx_shots = ("main-teletext-1920", "teletext-test-page", "teletext-mix", "main-teletext-1366",
                     "main-subtitles-1920")
        if want(*ttx_shots):
            from .. import ttxsample
            svc = ttxsample.write(Path(tmp) / "teletext", "GM4BRI")
            bridge.set(view="rx", ttxDir=str(svc), ttxOn=True, ttxInitial="100")
            require_sim(radio)
            if not bridge.ptt(True):
                raise RuntimeError("the simulator refused to key: " + bridge.refusal())
            wait_locked(bridge, timeout=15.0, settle=2.0, keyed=True)
            win.show_text(True)
            end = time.monotonic() + 15
            while time.monotonic() < end and not all(radio.ttx_page(x) for x in ("100", "101", "102", "103")):
                wait(0.2)
            win.ttx_view.go("100")
            # The waveform monitor on a teletext line: the data on the air.
            bridge.set(scopeLine=12)
            wait(2.0)
            shot(win, "main-teletext-1920")
            bridge.set(scopeLine=100)
            win.ttx_view.go("103")
            win.ttx_view.set_reveal(True)
            wait(1.5)
            shot(win.picture_card, "teletext-test-page")
            win.ttx_view.set_reveal(False)
            win.ttx_view.go("101")
            win.ttx_view.set_mix(True)
            wait(1.5)
            shot(win.picture_card, "teletext-mix")
            win.ttx_view.set_mix(False)
            win.ttx_view.go("102")
            shot(win, "main-teletext-1366", (1366, 768))
            win.resize(1920, 1080)
            win.show_text(False)
            if want("main-subtitles-1920"):
                # A caption typed on the transmit panel, back as page 888
                # over the received picture.
                win.tx_panel.caption.setText("Subtitles on page 888 | typed live on the transmit panel")
                win.tx_panel._send_caption()
                wait(2.0)
                shot(win, "main-subtitles-1920")
            bridge.ptt(False)
            wait(1.0)
        # --- the narrow formats, keyed on the simulator at the USB Pluto's
        # 5 MS/s with its narrow FM profile: our own card round the loop
        narrow = [("main-minimac-1920", "mac", None), ("main-minimac-1366", "mac", (1366, 768)),
                  ("main-lowpal-1920", "lowpal", None)]
        if want(*(n for n, _, _ in narrow), "main-teletext-refused-1920"):
            bridge.set(fmRate=5_000_000, fmDeviation=1.2e6, fmVideoBw=1.6e6, fmSub=0.0, view="rx")
            wait(3.0)
            for name, fmt, size in narrow:
                if not want(name):
                    continue
                bridge.set(videoFormat=fmt)
                win.resize(*(size or (1920, 1080)))
                require_sim(radio)
                if not bridge.ptt(True):
                    raise RuntimeError("the simulator refused to key: " + bridge.refusal())
                wait_locked(bridge, timeout=15.0, settle=6.0, keyed=True)
                shot(win, name)
                bridge.ptt(False)
                wait(1.0)
            win.resize(1920, 1080)
            if want("main-teletext-refused-1920"):
                # The narrow profile cannot carry teletext: switched on, the
                # transmit panel says why, in amber, and nothing is inserted.
                bridge.set(videoFormat="pal", ttxOn=True)
                wait(2.0)
                shot(win, "main-teletext-refused-1920")
                bridge.set(ttxOn=False)
            bridge.set(videoFormat="pal", fmRate=20_000_000, fmDeviation=7.0e6, fmVideoBw=5.0e6, fmSub=6.0e6)
            wait(3.0)
        # --- AM-VSB on 1275 (a restart: the system is a restart key)
        if want("main-am-1275-1920"):
            bridge.set(system="am", rxFreq=1275.0e6)
            wait(2.0)
            wait_locked(bridge, settle=7.0)
            shot(win, "main-am-1275-1920")
        # --- NICAM stereo from the same station: the badge says STEREO once
        # it is heard, and the spectrum marks its carrier.
        if want("main-nicam-1920"):
            if bridge.st.get("system") != "am" or float(bridge.st.get("rxFreq") or 0) != 1275.0e6:
                bridge.set(system="am", rxFreq=1275.0e6)
                wait(2.0)
                wait_locked(bridge, settle=3.0)
            end = time.monotonic() + 20
            while time.monotonic() < end and not bridge.st.get("nicamActive"):
                wait(0.2)
            wait(2.0)
            shot(win, "main-nicam-1920")
        if want("main-6cm-1920"):
            bridge.set(band="6cm")
            wait(2.0)
            wait_locked(bridge, settle=7.0)
            shot(win, "main-6cm-1920")
        if want("main-70cm-1920"):
            # The simulated 70cm station on 433 MHz, where an AM-VSB signal
            # stays inside 430-440: the shot shows 70cm transmit allowed under
            # its outside-the-bandplan banner.
            bridge.set(band="70cm", rxFreq=433.0e6)
            wait(2.0)
            wait_locked(bridge, settle=7.0)
            shot(win, "main-70cm-1920")
        # --- 405 lines (System A) at a USB Pluto's 5 MS/s: the simulator's
        # 405-line station found by formatAuto with ours set to 625; then our
        # own 405-line card round the loop; then the cable feed on B2.
        if want("main-405-1287-1920", "main-405-transmitting-1920", "main-cable-feed-1920"):
            bridge.set(band="23cm", system="am", amRate=5_000_000, rxFreq=1287.5e6, videoFormat="pal", view="rx")
            wait(3.0)
            if want("main-405-1287-1920"):
                wait_locked(bridge, timeout=20.0, settle=7.0)
                shot(win, "main-405-1287-1920")
            if want("main-405-transmitting-1920"):
                bridge.set(rxFreq=1249.0e6, videoFormat="405")
                wait(1.0)
                require_sim(radio)
                if not bridge.ptt(True):
                    raise RuntimeError("the simulator refused to key: " + bridge.refusal())
                wait_locked(bridge, timeout=15.0, settle=6.0, keyed=True)
                shot(win, "main-405-transmitting-1920")
                bridge.ptt(False)
                wait(1.0)
            if want("main-cable-feed-1920"):
                require_sim(radio)
                radio.set(authorise=True, cableFeedAck=True, cableFeed=True)
                bridge.set(rxFreq=51.75e6)
                wait(1.0)
                if not bridge.ptt(True):
                    raise RuntimeError("the simulator refused to key: " + bridge.refusal())
                wait_locked(bridge, timeout=15.0, settle=6.0, keyed=True)
                shot(win, "main-cable-feed-1920")
                bridge.ptt(False)
                radio.set(authorise=True, cableFeed=False)
                wait(1.0)
        # --- the cable feed on UHF: 625-line PAL on E36 with the sample
        # teletext service, at 10 MS/s (5 MS/s cannot carry teletext). The
        # waveform monitor on line 12 shows the data; then the viewer.
        if want("main-cable-uhf-1920", "main-cable-uhf-text-1920"):
            from .. import ttxsample
            svc = ttxsample.write(Path(tmp) / "teletext-cable", "GM4BRI")
            bridge.set(band="23cm", system="am", amRate=10_000_000, rxFreq=1249.0e6, videoFormat="pal", view="rx")
            wait(3.0)
            require_sim(radio)
            radio.set(authorise=True, cableFeedAck=True, cableFeed=True)
            bridge.set(band="UHF")
            bridge.set(ttxDir=str(svc), ttxOn=True, ttxInitial="100")
            wait(2.0)
            require_sim(radio)
            if not bridge.ptt(True):
                raise RuntimeError("the simulator refused to key: " + bridge.refusal())
            wait_locked(bridge, timeout=15.0, settle=4.0, keyed=True)
            bridge.set(scopeLine=12)
            wait(2.0)
            shot(win, "main-cable-uhf-1920")
            bridge.set(scopeLine=100)
            win.show_text(True)
            end = time.monotonic() + 15
            while time.monotonic() < end and not all(radio.ttx_page(x) for x in ("100", "101", "102", "103")):
                wait(0.2)
            win.ttx_view.go("100")
            wait(1.5)
            shot(win, "main-cable-uhf-text-1920")
            win.show_text(False)
            bridge.ptt(False)
            radio.set(authorise=True, cableFeed=False)
            bridge.set(ttxOn=False)
            wait(1.0)
        print(f"paint cost (EMA, ms): picture {win.monitor.cost_paint:.2f}  spectrum {win.spectrum.cost_paint:.2f} "
              f"(ingest {win.spectrum.cost_ingest:.2f}/line)  waveform {win.wfm.cost_paint:.2f}  "
              f"vectorscope {win.vec.cost_paint:.2f}  bridge pump {bridge.frame_ms:.2f}", flush=True)
    finally:
        try:
            bridge.ptt(False)
        finally:
            win.close()
            bridge.stop()
            shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
