"""How much the window costs per frame (copied from OpenHamShack NB's uibench).

    QT_QPA_PLATFORM=offscreen python3 -m ohsatv.ui.uibench [--seconds 10] [--size 1920x1080] [--system fm|am]

Runs the real window on the simulator in real time and reports, per frame:
the bridge pump (draining feeds and every signal handler: spectrum lines
into the waterfall, scope lines into the phosphor buffers, picture scaling),
each painted widget's paint, a forced repaint of the whole window, and how
much of one core the main thread spends - which matters more here than in
OpenHamShack NB, because the receive chain at 16-20 MS/s needs nearly two cores
of its own and every millisecond the window holds the GIL is one the
demodulator waits for. Also prints the receiver's lock, so a window that
costs too much shows up as a picture that stops locking.
"""
from __future__ import annotations

import atexit
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    secs = float(argv[argv.index("--seconds") + 1]) if "--seconds" in argv else 10.0
    size = argv[argv.index("--size") + 1] if "--size" in argv else "1920x1080"
    system = argv[argv.index("--system") + 1] if "--system" in argv else "fm"
    w, h = (int(x) for x in size.split("x"))
    tmp = tempfile.mkdtemp(prefix="ohsatv-bench-")
    # The throw-away config and data directory of a simulated radio, gone
    # when the tool exits however it exits (it used to stay in /tmp).
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["OHSATV_UI_CONF"] = str(Path(tmp) / "ui.json")
    os.environ["OHSATV_CONF"] = str(Path(tmp) / "config.json")

    from .. import state
    from ..audio import NullAudio
    from ..radio import Radio
    from . import build, make_app, shutdown
    from .prefs import Prefs

    app = make_app(["bench"])
    cfg = state.defaults(("setting", "control"))
    f = 1255.0e6 if system == "fm" else 1275.0e6
    cfg.update(device="sim", audioBackend="null", callsign="GM4BRI", band="23cm", system=system, rxFreq=f, txFreq=f)
    radio = Radio(cfg, persist=False, data_dir=Path(tmp), audio=NullAudio(realtime=True))
    win, bridge = build(radio, Prefs())
    win.resize(w, h)
    win.show()

    pumps: list[float] = []
    paints: dict[str, list[float]] = {}
    orig_pump = bridge.pump

    def timed_pump():
        t0 = time.perf_counter()
        orig_pump()
        pumps.append((time.perf_counter() - t0) * 1000)

    bridge.timer.timeout.disconnect()
    bridge.timer.timeout.connect(timed_pump)
    for name in ("monitor", "spectrum", "wfm", "vec"):
        wdg = getattr(win, name)
        store = paints.setdefault(name, [])
        orig = wdg.paintEvent

        def paint(e, orig=orig, store=store):
            t0 = time.perf_counter()
            orig(e)
            store.append((time.perf_counter() - t0) * 1000)
        wdg.paintEvent = paint

    busy = {"t": 0.0}

    def run(sec):
        end = time.monotonic() + sec
        while time.monotonic() < end:
            t0 = time.perf_counter()
            app.processEvents()
            busy["t"] += time.perf_counter() - t0
            time.sleep(0.002)

    run(5.0)                      # let the radio lock and the levels settle
    pumps.clear()
    for v in paints.values():
        v.clear()
    busy["t"] = 0.0
    frames0 = bridge.frames
    run(secs)
    fps = (bridge.frames - frames0) / secs
    load = busy["t"] / secs
    full = []
    for _ in range(10):
        t0 = time.perf_counter()
        win.repaint()
        full.append((time.perf_counter() - t0) * 1000)

    def med(x):
        return statistics.median(x) if x else float("nan")

    def p95(x):
        return sorted(x)[int(0.95 * (len(x) - 1))] if x else float("nan")

    st = radio.state()
    print(f"window {w}x{h}, {system.upper()} at {st['fs'] / 1e6:g} MS/s, {secs:.0f} s; "
          f"pictures shown {fps:.1f}/s, receiver {st['rxLock']} H {100 * st['hLock']:.0f}% SNR {st['snr']}")
    print(f"  bridge pump (feeds + handlers)  median {med(pumps):6.2f} ms   p95 {p95(pumps):6.2f} ms")
    for name, v in paints.items():
        print(f"  {name:9s} paint                 median {med(v):6.2f} ms   p95 {p95(v):6.2f} ms   {len(v) / secs:5.1f}/s")
    print(f"  whole-window repaint            median {med(full):6.2f} ms")
    print(f"  main thread busy {100 * load:.0f} % of one core (event loop incl. paints)")
    win.close()
    bridge.stop()
    shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
