"""OpenHamShack ATV with the real OpenHamShack Teletext editor, end to end.

The editor (ohstxt) has been part of this repository since it was folded
into ATV, so this always runs; it used to skip where ~/openhamshack-teletext
was missing. The editor is imported to *make and read* files - its
templates, sample service, live-page runner, SRT exporter, .tti and .t42
readers, and its service checker as a command. ATV's engine still imports
nothing of it: the files are the contract, and the engine's own reader
(ohsatv/tti.py) is what this checks against the editor's writer. Only the
window imports ohstxt, to open the editor (test_txt_handover.py).

1. A scratch service: the editor's sample service (index, newsflash, test
   page, a three-subpage carousel) and two of its templates, one of them a
   live page (beacons) saved with its `.live.json` sidecar beside it, plus
   station.json and beacons.csv; `python3 -m ohstxt.check` passes it.
2. Sent over the simulator's loopback and decoded: every page and every
   subpage equal to what the editor wrote, cell by cell - the codes, and
   so the attributes, which tests/test_teletext.py's Level 1 decoder is
   also run over to compare as laid out - with the Fastext links intact.
3. While on air, the editor's live runner rewrites the live page its way
   (a temp file renamed over the page) after beacons.csv changes: the new
   page arrives within one turn of the service, and the sidecars, state
   file, station.json and beacons.csv never reach the air.
4. An SRT exported by the editor's subtitle code, played on 888 against a
   video file source: each cue's rows equal the editor's own cue_rows().
5. The received pages saved, and opened by the editor's .tti reader and
   its .t42 reception code: the same pages again.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

from helpers import crop, tmp_dir  # noqa: F401  (crop: path setup)

REPO = Path(__file__).resolve().parent.parent


def wait(r, fn, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r.heartbeat()
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return None


def editor_service(folder: Path):
    """The scratch service, written by the editor's own code: its station
    templates (index, test card, contact, schedule, technical), its live
    beacons page with sidecar, and the sample service's weather carousel
    (three subpages) as page 310. Links to pages the scratch service does
    not have go to the index, by the editor's own set_link, so its checker
    passes it."""
    from ohstxt import live, samples, station, templates, tti as etti
    st = station.Station(callsign="GM4BRI", name="Brian", locator="IO86", vbi_lines=16)
    station.save(st, folder)
    (folder / "beacons.csv").write_text("# call, MHz, mode, locator, status, note\n"
                                        "GB3XX, 1297.5, ATV, IO86, on, test beacon\n")
    pages = [templates.get(k).build(st, templates.get(k).number)
             for k in ("index", "testcard", "contact", "schedule", "technical", "beacons")]
    carousel = samples.weather_carousel()
    carousel.number = 0x310
    for s in carousel.subpages:
        s.cycle = 2                         # a turn of the carousel in seconds, not half a minute
    carousel.renumber()
    pages.append(carousel)
    have = {p.number for p in pages}
    for p in pages:
        for s in p.subpages:
            for i, x in enumerate(s.links):
                if x != 0x8FF and x not in have:
                    s.set_link(i, 0x100)
    for p in pages:
        if p.live is not None:
            live.save_page(folder, p)
        else:
            etti.save(p, folder / f"P{p.number:03X}.tti")
    return st


class EditorEndToEnd(unittest.TestCase):
    def setUp(self):
        self.dir = tmp_dir(self, "ohsatv-e2e-")
        self.svc = self.dir / "service"
        self.svc.mkdir()
        editor_service(self.svc)

    def radio(self, **over):
        from ohsatv.audio import NullAudio
        from ohsatv.radio import Radio
        cfg = dict(device="sim", audioBackend="null", system="am", band="23cm", rxFreq=1249.0e6,
                   txFreq=1249.0e6, amRate=10_000_000, callsign="GM4BRI", licenceAck=True, ttxOn=True,
                   ttxDir=str(self.svc), ttxInitial="100")
        cfg.update(over)
        r = Radio(cfg, conf_path=self.dir / "c.json", audio=NullAudio(realtime=False), data_dir=self.dir / "data")
        self.addCleanup(r.stop)
        return r

    # ------------------------------------------------------------- checks
    def same_page(self, epage, got_fn, what):
        """Every subpage of the editor's page against what got_fn(label,
        subcode) returns: rows 1-24 cell by cell, then the same rows laid
        out by the Level 1 decoder, then the links."""
        from ohsatv import ttxrender
        label = f"{epage.number:03X}"
        for s in epage.subpages:
            got = got_fn(label, s.subcode if len(epage.subpages) > 1 else None)
            self.assertIsNotNone(got, (what, label, s.subcode))
            rows, links = got
            for y in range(1, 25):
                self.assertEqual(bytes(rows[y]), bytes(c & 0x7F for c in s.rows[y]), (what, label, s.subcode, y))
            want = [b""] + [bytes(s.rows[y]) for y in range(1, 25)]
            self.assertEqual(ttxrender.layout(want), ttxrender.layout([b""] + [bytes(rows[y]) for y in range(1, 25)]))
            wl = [None if x == 0x8FF else x for x in s.links] if s.has_links else [None] * 6
            self.assertEqual(links, wl, (what, label, s.subcode, "links"))

    def received(self, r):
        def got(label, sub):
            pg = r.ttx_page(label, sub)
            if pg is None:
                return None
            links = [None] * 6
            for i, ln in enumerate(pg["links"][:6]):
                links[i] = None if ln is None else int(ln[0], 16)
            return pg["rows"], links
        return got

    # ------------------------------------------------------------- the test
    def test_the_editors_service_on_air_live_edit_and_saved_back(self):
        from ohstxt import receive, service as eservice, tti as etti
        from ohstxt.live.runner import Runner
        # 1. The editor's checker passes the service.
        chk = subprocess.run([sys.executable, "-m", "ohstxt.check", str(self.svc), "--callsign", "GM4BRI", "--lines",
                              "16"], cwd=str(REPO), capture_output=True, text=True, timeout=60)
        self.assertEqual(chk.returncode, 0, chk.stdout + chk.stderr)
        self.assertTrue((self.svc / "P520.live.json").is_file())
        epages = eservice.Service.open(self.svc).pages
        self.assertEqual([f"{p.number:03X}" for p in epages], ["100", "199", "200", "300", "310", "400", "520"])
        # 2. On air and back.
        r = self.radio()
        toasts = r.subscribe("toast", 256)
        r.start()
        self.assertEqual(r.state()["ttxRefused"], "")
        r.ptt(True)
        got = self.received(r)

        def all_in():
            for p in epages:
                for s in p.subpages:
                    g = got(f"{p.number:03X}", s.subcode if len(p.subpages) > 1 else None)
                    if g is None or (s.has_links and not any(g[1])):
                        return False
            return True
        self.assertTrue(wait(r, all_in, 25), "not every page and subpage arrived")
        wait(r, lambda: False, 3.0)          # one more turn of every page, so each row is the newest
        for p in epages:
            self.same_page(p, got, "on air")
        # 3. A live rewrite while on air, the runner's way.
        (self.svc / "beacons.csv").write_text("GB3XX, 1297.5, ATV, IO86, off, rewritten on air\n"
                                              "GB3YY, 10368.9, CW, IO85, on, a second beacon\n")
        before = etti.load(self.svc / "P520.tti")
        runner = Runner(self.svc, log=lambda *a: None)
        written = runner.step(wait=True)
        runner.pool.shutdown(wait=True)
        self.assertEqual([p.name for p in written], ["P520.tti"])
        t0 = time.monotonic()
        new = etti.load(self.svc / "P520.tti")
        self.assertTrue(wait(r, lambda: all(bytes(got("520", None)[0][y]) == bytes(new.subpages[0].rows[y])
                                            for y in range(1, 25)), 10), "the live page's change did not arrive")
        delay = time.monotonic() - t0
        # One turn of the service: every page's packets at 16 lines a field.
        packets = sum(26 * len(p.subpages) for p in epages)
        turn = packets / (16 * 50)
        self.assertLess(delay, turn + 0.5 + 0.4 + 1.0, (delay, turn))      # + the folder poll and settle
        if os.environ.get("OHSATV_E2E_VERBOSE"):
            print(f"\nlive rewrite on air after {delay:.2f} s; one turn of the service {turn:.2f} s", flush=True)
        self.same_page(new, got, "live rewrite")
        self.assertEqual(r.ttx_pages(), ["100", "199", "200", "300", "310", "400", "520"])
        said = " ".join(t["text"] for t in toasts.drain())
        for name in ("live.json", "station.json", "beacons.csv", "ohstxt-live-state"):
            self.assertNotIn(name, said)
        self.assertTrue(r.state()["keyed"])
        r.ptt(False)
        # 5. Saved, and opened by the editor.
        out = self.dir / "saved"
        n, t42 = r.ttx_save_all(str(out))
        self.assertEqual(n, 7)
        epages = eservice.Service.open(self.svc).pages
        for p in epages:
            saved = etti.load(out / f"P{p.number:03X}.tti")
            self.assertEqual(len(saved.subpages), len(p.subpages), p.number)

            def from_saved(label, sub, saved=saved):
                s = saved.subpages[0] if sub is None else next(x for x in saved.subpages if x.subcode == sub)
                return s.rows, [None if x == 0x8FF else x for x in s.links]
            self.same_page(p, from_saved, "saved .tti")
        cap = receive.load(t42)
        self.assertEqual(cap.bad_address, 0)
        for p in epages:
            rx = cap.find(p.number)
            self.assertIsNotNone(rx, p.number)

            def from_t42(label, sub, rx=rx):
                s = rx.subpages[0] if sub is None else next((x for x in rx.subpages if x.subcode == sub), None)
                if s is None:
                    return None
                self.assertEqual(s.damaged, set())
                return s.sub.rows, [None if x == 0x8FF else x for x in s.sub.links]
            if p.number != 0x520:
                self.same_page(p, from_t42, ".t42 capture")
                continue
            # The capture spans the live rewrite, so it holds both versions
            # of 520; the editor's reception votes each cell among the
            # copies (its DESIGN.md, "Received material"). Every cell must be
            # one version's or the other's - never anything else.
            rows, _ = from_t42("520", None)
            for y in range(1, 25):
                for x in range(40):
                    self.assertIn(rows[y][x], (before.subpages[0].rows[y][x], p.subpages[0].rows[y][x]), (y, x))

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_an_srt_from_the_editor_on_888_against_a_file(self):
        from ohstxt import subtitles as esubs
        clip = self.dir / "clip.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-t", "14",
                        "-pix_fmt", "yuv420p", str(clip)], check=True, timeout=60)
        L = esubs.Line
        track = esubs.Track([
            esubs.Cue(1.0, 3.5, [L("Is the camera running?", 3), L("Plain lines are white.")]),
            esubs.Cue(4.5, 7.0, [L("Fish & chips <loud>")]),
            esubs.Cue(8.0, 10.5, [L("[MUSIC] costs £5 - ½ price", 6)]),
            esubs.Cue(11.0, 13.0, [L("x" * 37, 2), L("A line longer than the thirty-seven cells", 5)]),
        ])
        srt = self.dir / "subs.srt"
        srt.write_text(esubs.dumps_srt(track), encoding="utf-8")
        r = self.radio(ttxDir="", ttxSrt=str(srt), source="file", videoFile=str(clip))
        r.start()
        self.assertTrue(wait(r, lambda: r.state()["filePos"] is not None, 8))
        r.ptt(True)
        self.assertTrue(wait(r, lambda: r.state()["rxLock"] == "locked", 8))
        # Paused, then sought to each cue: the picture and the subtitle hold
        # there while the received 888 is compared (a cue is 2.5 s; the
        # loopback takes ~0.6 s to bring a change back).
        r.file_pause(True)
        parsed, _ = esubs.parse_srt(srt.read_text(encoding="utf-8"))
        for cue in parsed.cues:
            want = esubs.cue_rows(cue)
            r.file_seek(cue.start + 0.3)

            def matches():
                pg = r.ttx_page("888")
                return pg is not None and all(bytes(pg["rows"][y]) == bytes(want[y]) for y in range(1, 25))
            self.assertTrue(wait(r, matches, 6), (cue, r.ttx_page("888") and r.ttx_page("888")["rows"][18:23]))
        r.ptt(False)


if __name__ == "__main__":
    unittest.main()
