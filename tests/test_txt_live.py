"""Live pages: the token grammar, the sidecar, every built-in source against
fakes on local sockets (an HTTP server for the solar XML, a telnet server
that behaves like a DX cluster), failure keeping the last good data and
saying "no data since", the runner writing pages atomically and only when
they change, plugins, and the editor's save. Nothing here touches the real
network."""
import http.server
import json
import os
import socket
import socketserver
import subprocess
import sys
import textwrap
import threading
import time
import unittest
from pathlib import Path

from helpers import page_rows, tmp_dir  # noqa: F401  (path setup)

from ohstxt import live, station, tti
from ohstxt.live import Board, Feed, Sidecar, cluster, solar, sources
from ohstxt.live.runner import Runner
from ohstxt.live.template import fill_row, names_in, tokens_in_row
from ohstxt.page import Page, Subpage

REPO = Path(__file__).resolve().parent.parent


def row(text: bytes) -> bytearray:
    return bytearray(text.ljust(40, b" ")[:40])


# ---------------------------------------------------------------- fakes
class _Quiet(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass


class FakeHTTP:
    """A local HTTP server whose reply the test sets."""

    def __init__(self):
        self.status, self.body, self.hits = 200, solar.SAMPLE_XML.encode(), 0
        outer = self

        class H(_Quiet):
            def do_GET(self):
                outer.hits += 1
                self.send_response(outer.status)
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                self.wfile.write(outer.body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/solarxml.php"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class FakeCluster:
    """A DX cluster on a local port: telnet negotiation, a login prompt, a
    prompt after login, `sh/dx N` answered with a listing, a live
    announcement thrown in, and `bye`."""

    LISTING = (" 14025.0  JA1ZZA      5-Oct-2026 2012Z  CQ DX                         <G4ZZB>\r\n"
               "144300.0  PA3ZZG      5-Oct-2026 2005Z  tropo JO22                    <G8ZZH>\r\n"
               " 14030.0  JA1ZZA      5-Oct-2026 2001Z  again                         <G4ZZC>\r\n"
               "1296200.0  F6ZZL       5-Oct-2026 2001Z  EME                           <DL1ZZM>\r\n")

    def __init__(self, behaviour="ok"):
        self.behaviour = behaviour
        self.logins, self.commands = [], []
        outer = self

        class H(socketserver.BaseRequestHandler):
            def handle(self):
                s = self.request
                if outer.behaviour == "close":
                    return
                s.sendall(b"\xff\xfb\x01\xff\xfb\x03Welcome to the fake cluster\r\nlogin: ")
                login = outer._line(s)
                outer.logins.append(login)
                if outer.behaviour == "silent":
                    time.sleep(5)
                    return
                s.sendall(f"Hello {login}\r\nDX de G0ZZZ:     7012.0  OH0ZZZ       CW            2015Z\r\n"
                          f"{login} de FAKE 5-Oct-2026 2016Z dxspider >\r\n".encode())
                cmd = outer._line(s)
                outer.commands.append(cmd)
                if outer.behaviour == "slow":
                    time.sleep(3)
                s.sendall(outer.LISTING.encode() + f"{login} de FAKE >\r\n".encode())
                outer._line(s)

        class S(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = S(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @staticmethod
    def _line(s) -> str:
        buf = b""
        s.settimeout(5)
        try:
            while not buf.endswith(b"\n"):
                c = s.recv(1)
                if not c:
                    break
                buf += c
        except OSError:
            pass
        return buf.decode().strip()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]          # closed again: nothing listens there


# ---------------------------------------------------------------- tokens
class Tokens(unittest.TestCase):
    def test_a_token_is_as_wide_as_it_is_written(self):
        [t] = tokens_in_row(row(b"SFI {sfi} today"))
        self.assertEqual((t.col, t.width, t.name, t.right), (4, 5, "sfi", False))
        [t] = tokens_in_row(row(b"{>dx.1.freq~~~}"))
        self.assertEqual((t.width, t.name, t.right), (15, "dx.1.freq", True))

    def test_what_is_not_a_token(self):
        for text in (b"{Upper}", b"{1abc}", b"{a b}", b"{}", b"{sfi", b"{.x}"):
            self.assertEqual(tokens_in_row(row(text)), [], text)

    def test_a_control_code_breaks_a_token(self):
        self.assertEqual(tokens_in_row(row(b"{s\x01fi}")), [])

    def test_fill_cuts_pads_and_aligns(self):
        out, used = fill_row(row(b"[{a}][{>b~~}][{c}]"), {"a": "toolong", "b": "7", "c": None}.get)
        self.assertEqual(bytes(out[:20]), b"[too][     7][-- ]  ")
        self.assertEqual(used, ["a", "b", "c"])

    def test_values_go_through_the_english_set(self):
        out, _ = fill_row(row(b"{a~~~}"), lambda n: "£½#☃")
        self.assertEqual(bytes(out[:6]), b"#\x5c\x5f?  ")

    def test_names_in_text_read_back_from_a_page(self):
        self.assertEqual(names_in("Call ¼call¾ at ¼>time÷÷¾"), ["call", "time"])


# ---------------------------------------------------------------- the sidecar
class SidecarFile(unittest.TestCase):
    def test_round_trip_keeps_rows_sources_and_unknown_keys(self):
        sc = Sidecar(subpages=[{3: "\x1bCSFI {sfi}"}, {}], sources={"dx2": {"type": "dxcluster", "bands": ["2m"]}},
                     extra={"future": [1, 2]})
        d = tmp_dir(self, "ohstxt-test-")
        p = d / "P301.live.json"
        p.write_text(live.dumps_sidecar(sc, 0x301))
        raw = json.loads(p.read_text())
        self.assertEqual(raw["format"], "ohstxt-live/1")
        self.assertEqual(raw["page"], "301")
        self.assertEqual(raw["subpages"][0]["rows"]["3"], "\x1bCSFI {sfi}")
        back = live.load_sidecar(p)
        self.assertEqual((back.subpages, back.sources, back.extra), (sc.subpages, sc.sources, sc.extra))

    def test_the_sidecar_sits_beside_its_page(self):
        self.assertEqual(live.sidecar_path("/s/P301.tti").name, "P301.live.json")
        self.assertEqual(live.sidecar_path("/s/weather.tti").name, "weather.live.json")

    def test_another_format_is_refused(self):
        p = tmp_dir(self, "ohstxt-test-") / "x.live.json"
        p.write_text('{"format": "something-else/9"}')
        with self.assertRaises(ValueError):
            live.load_sidecar(p)


# ---------------------------------------------------------------- sources
class StationAndBeacons(unittest.TestCase):
    def test_station_fields(self):
        src = sources.make("station", {"type": "station"}, station.Station(callsign="M0TXT", lat=51.45, lon=-0.97))
        v = src.values_at(time.mktime((2026, 10, 5, 9, 7, 0, 0, 0, -1)))
        self.assertEqual((v["call"], v["locator"], v["time"], v["isodate"]), ("M0TXT", "IO91mk", "09:07",
                                                                               "2026-10-05"))
        self.assertTrue(src.local)

    def test_beacon_file_is_a_status_board(self):
        d = tmp_dir(self, "ohstxt-test-")
        (d / "beacons.csv").write_text('# comment\nGB3ZZA, 144.430, CW, IO91, on, "2m, mast"\n\n'
                                       "GB3ZZB,1296.9,CW,IO92,off\n")
        src = sources.make("beacons", {"type": "beacons"}, station.Station(), d)
        v = src.fetch()
        self.assertEqual((v["count"], v["on"], v["off"]), ("2", "1", "1"))
        self.assertEqual(v["1.note"], "2m, mast")
        self.assertEqual(v["2.status"], "OFF")
        self.assertEqual(v["1"], "GB3ZZA    144.430 CW    ON   2m, mast")

    def test_a_missing_beacon_file_is_a_source_error(self):
        src = sources.make("beacons", {"type": "beacons", "file": "nope.csv"}, station.Station(),
                           tmp_dir(self, "ohstxt-test-"))
        with self.assertRaises(sources.SourceError):
            src.fetch()

    def test_an_unknown_type_is_a_source_error(self):
        with self.assertRaises(sources.SourceError):
            sources.make("x", {"type": "no-such"}, station.Station())


class Solar(unittest.TestCase):
    def setUp(self):
        self.http = FakeHTTP()
        self.addCleanup(self.http.close)
        self.src = sources.make("solar", {"type": "solar", "url": self.http.url, "timeout": 3}, station.Station())

    def test_fetch_and_parse(self):
        v = self.src.fetch()
        self.assertEqual((v["sfi"], v["a"], v["k"], v["xray"], v["ssn"]), ("162", "8", "2", "C1.4", "134"))
        self.assertEqual((v["d30"], v["n12"], v["es_6m"], v["geomag"]), ("Good", "Poor", "50MHz ES", "QUIET"))
        self.assertEqual(v["updated"], "05 Oct 2026 1800 GMT")

    def test_never_polled_more_than_every_fifteen_minutes(self):
        src = sources.make("solar", {"type": "solar", "every": 10}, station.Station())
        self.assertEqual(src.every, 900)

    def test_http_error_garbage_and_oversize_are_source_errors(self):
        self.http.status = 500
        with self.assertRaises(sources.SourceError):
            self.src.fetch()
        self.http.status, self.http.body = 200, b"<html>maintenance</html>"
        with self.assertRaises(sources.SourceError):
            self.src.fetch()
        self.http.body = b"<solar><solardata>" + b" " * (solar.MAX_BYTES + 10) + b"</solardata></solar>"
        with self.assertRaises(sources.SourceError):
            self.src.fetch()

    def test_nothing_listening(self):
        src = sources.make("solar", {"type": "solar", "url": f"http://127.0.0.1:{free_port()}/", "timeout": 2},
                           station.Station())
        with self.assertRaises(sources.SourceError):
            src.fetch()


class Cluster(unittest.TestCase):
    def make(self, behaviour="ok", **cfg):
        fake = FakeCluster(behaviour)
        self.addCleanup(fake.close)
        conf = {"type": "dxcluster", "host": "127.0.0.1", "port": fake.port, "timeout": 4, "quiet": 0.4, **cfg}
        return fake, sources.make("dx", conf, station.Station(callsign="M0TXT"))

    def test_both_spot_formats(self):
        spots = cluster.parse_spots(FakeCluster.LISTING + "DX de G0ZZZ:     7012.0  OH0ZZZ  CW up 2  2015Z\r\n")
        self.assertEqual([(s["call"], s["band"], s["spotter"]) for s in spots],
                         [("JA1ZZA", "20m", "G4ZZB"), ("PA3ZZG", "2m", "G8ZZH"), ("JA1ZZA", "20m", "G4ZZC"),
                          ("F6ZZL", "23cm", "DL1ZZM"), ("OH0ZZZ", "40m", "G0ZZZ")])
        self.assertEqual(spots[4]["comment"], "CW up 2")

    def test_telnet_negotiation_is_stripped(self):
        self.assertEqual(cluster.strip_telnet(b"\xff\xfb\x01ab\xff\xfa\x18\x01\xff\xf0c\xff\xffd"), b"abc\xffd")

    def test_a_session_logs_in_with_the_station_callsign_and_reads_spots(self):
        fake, src = self.make()
        v = src.fetch()
        self.assertEqual(fake.logins, ["M0TXT"])
        self.assertEqual(fake.commands, ["sh/dx 20"])
        self.assertEqual(v["1.call"], "JA1ZZA")
        self.assertEqual(v["2.call"], "PA3ZZG")          # JA1ZZA's second spot on 20m is not repeated
        self.assertEqual(v["1"], " 14025.0 JA1ZZA    2012 G4ZZB")

    def test_band_filter_and_login_override(self):
        fake, src = self.make(bands=["2m", "23cm"], login="G0LOG")
        v = src.fetch()
        self.assertEqual(fake.logins, ["G0LOG"])
        self.assertEqual((v["count"], v["1.call"], v["2.call"], v["bands"]), ("2", "PA3ZZG", "F6ZZL", "23cm 2m"))

    def test_failures_are_source_errors_not_hangs(self):
        for behaviour in ("close", "silent"):
            _, src = self.make(behaviour, timeout=2)
            t = time.monotonic()
            with self.assertRaises(sources.SourceError):
                src.fetch()
            self.assertLess(time.monotonic() - t, 8)
        src = sources.make("dx", {"type": "dxcluster", "host": "127.0.0.1", "port": free_port(), "timeout": 2},
                           station.Station(callsign="M0TXT"))
        with self.assertRaises(sources.SourceError):
            src.fetch()

    def test_no_host_configured(self):
        src = sources.make("dx", {"type": "dxcluster"}, station.Station(callsign="M0TXT"))
        with self.assertRaisesRegex(sources.SourceError, "no DX cluster host"):
            src.fetch()


# ---------------------------------------------------------------- feeds and the board
class Flaky(sources.Source):
    """A source the test switches between working and failing."""
    type_name = "flaky"
    every = 1.0
    answer = {"v": "1"}
    fail = False

    def fields(self):
        return [("v", "a value")]

    def fetch(self):
        if Flaky.fail:
            raise OSError("link down")
        return dict(Flaky.answer)

    def sample(self):
        return {"v": "S"}


sources.REGISTRY["flaky"] = Flaky


class Feeds(unittest.TestCase):
    def setUp(self):
        Flaky.fail, Flaky.answer = False, {"v": "1"}

    def test_failure_keeps_the_last_good_values_and_says_since_when(self):
        f = Feed(Flaky("f", {}, station.Station()))
        self.assertEqual(f.notice(), "No data yet")
        t0 = time.mktime((2026, 10, 5, 14, 5, 0, 0, 0, -1))
        f.poll(t0)
        self.assertEqual((f.values, f.notice()), ({"v": "1"}, ""))
        Flaky.fail = True
        f.poll(t0 + 600)
        self.assertEqual(f.values, {"v": "1"})
        self.assertEqual(f.notice(), "No data since 14:05")
        self.assertIn("link down", f.error)

    def test_names_resolve_page_sources_first_then_service(self):
        d = tmp_dir(self, "ohstxt-test-")
        b = Board(d, station.Station(callsign="M0TXT", sources={"f": {"type": "flaky"}}))
        b.sample = True
        sc = Sidecar(sources={"g": {"type": "station"}})
        scope = b.scope(sc)
        self.assertEqual([n for n, _ in scope][:1], ["g"])
        self.assertEqual(b.lookup("v", scope)[0], "S")
        self.assertEqual(b.lookup("f.v", scope)[0], "S")
        self.assertEqual(b.lookup("g.call", scope)[0], "M0TXT")
        self.assertEqual(b.lookup("nothing", scope), (None, None))


# ---------------------------------------------------------------- the runner
def live_page(folder: Path, number=0x301, rows=None, sources_=None):
    s = Subpage()
    s.rows[1][:12] = b"\x03STATIC TEXT"
    for r, text in (rows or {}).items():
        s.rows[r] = row(text)
    pg = Page(number=number, description="live", subpages=[s], live=Sidecar(sources=sources_ or {}))
    return live.save_page(folder, pg)


class Running(unittest.TestCase):
    def setUp(self):
        Flaky.fail, Flaky.answer = False, {"v": "1"}
        self.dir = tmp_dir(self, "ohstxt-live-")
        station.save(station.Station(callsign="M0TXT", sources={"f": {"type": "flaky"}}), self.dir)

    def rows_of(self, path):
        return [tti.load(path).subpages[0].text(r) for r in range(25)]

    def test_save_puts_the_template_in_the_sidecar_and_local_data_in_the_page(self):
        p = live_page(self.dir, rows={2: b"Call {call~~~} V {v}", 4: b"{status~~~~~~~~~~~}"})
        sc = json.loads(live.sidecar_path(p).read_text())
        self.assertEqual(sc["subpages"][0]["rows"]["2"].rstrip(), "Call {call~~~} V {v}")
        self.assertNotIn("1", sc["subpages"][0]["rows"])          # rows without tokens stay in the .tti only
        rows = self.rows_of(p)
        self.assertEqual(rows[2].rstrip(), "Call M0TXT     V --")    # the station is local; f was never fetched
        self.assertEqual(rows[4].rstrip(), "No data yet")
        self.assertEqual(rows[1].rstrip(), " STATIC TEXT")

    def test_the_runner_fills_and_rewrites_only_on_change(self):
        p = live_page(self.dir, rows={3: b"V={v}"})
        r = Runner(self.dir, log=lambda m: None)
        self.assertEqual(r.step(wait=True), [p])
        self.assertEqual(self.rows_of(p)[3].rstrip(), "V=1")
        ino = p.stat().st_ino
        r.step(now=time.time() + 5, wait=True)
        self.assertEqual(r.written, [])                          # same data: the file is not touched
        self.assertEqual(p.stat().st_ino, ino)
        Flaky.answer = {"v": "2"}
        r.step(now=time.time() + 10, wait=True)
        self.assertEqual(r.written, [p])
        self.assertNotEqual(p.stat().st_ino, ino)                # replaced by rename, never written in place
        self.assertEqual(self.rows_of(p)[3].rstrip(), "V=2")
        self.assertEqual([x.name for x in self.dir.iterdir() if x.name.endswith(".tmp")], [])

    def test_a_failing_source_keeps_its_data_and_the_page_says_so(self):
        p = live_page(self.dir, rows={3: b"V={v}", 5: b"{f.status~~~~~~~~~~~~}"})
        r = Runner(self.dir, log=lambda m: None)
        t0 = time.mktime((2026, 10, 5, 14, 5, 0, 0, 0, -1))
        r.step(now=t0, wait=True)
        Flaky.fail = True
        r.step(now=t0 + 60, wait=True)
        rows = self.rows_of(p)
        self.assertEqual(rows[3].rstrip(), "V=1")
        self.assertEqual(rows[5].rstrip(), "No data since 14:05")
        Flaky.fail = False
        r.step(now=t0 + 120, wait=True)
        self.assertEqual(self.rows_of(p)[5].rstrip(), "")

    def test_without_a_status_token_the_notice_goes_on_row_23(self):
        p = live_page(self.dir, rows={3: b"V={v}"})
        Flaky.fail = True
        Runner(self.dir, log=lambda m: None).step(wait=True)
        page = tti.load(p)
        self.assertTrue(page.subpages[0].text(23).endswith("No data yet"))
        self.assertEqual(page.subpages[0].rows[23][40 - 12], 0x03)    # in yellow
        self.assertEqual(page.subpages[0].text(3).rstrip(), "V=--")

    def test_a_restarted_runner_keeps_the_last_good_data(self):
        p = live_page(self.dir, rows={3: b"V={v}"})
        Runner(self.dir, log=lambda m: None).step(wait=True)
        Flaky.fail = True
        Flaky.answer = {"v": "9"}
        r2 = Runner(self.dir, log=lambda m: None)
        r2.step(now=time.time() + 5, wait=True)
        self.assertEqual(self.rows_of(p)[3].rstrip(), "V=1")
        self.assertTrue(self.rows_of(p)[23].rstrip().endswith(time.strftime("%H:%M")) or
                        "No data since" in self.rows_of(p)[23])

    def test_only_the_template_rows_are_replaced(self):
        p = live_page(self.dir, rows={3: b"V={v}"})
        pg = tti.load(p)
        pg.subpages[0].rows[10] = row(b"edited in the editor meanwhile")
        tti.save(pg, p)
        Runner(self.dir, log=lambda m: None).step(wait=True)
        self.assertEqual(self.rows_of(p)[10].rstrip(), "edited in the editor meanwhile")

    def test_a_bad_sidecar_does_not_stop_the_other_pages(self):
        good = live_page(self.dir, rows={3: b"V={v}"})
        live_page(self.dir, number=0x302, rows={3: b"V={v}"})
        live.sidecar_path(self.dir / "P302.tti").write_text("{not json")
        logs = []
        Runner(self.dir, log=logs.append).step(wait=True)
        self.assertEqual(self.rows_of(good)[3].rstrip(), "V=1")
        self.assertTrue(any("P302.live.json" in m for m in logs))

    def test_unused_sources_are_never_fetched(self):
        http = FakeHTTP()
        self.addCleanup(http.close)
        station.save(station.Station(callsign="M0TXT", sources={"f": {"type": "flaky"},
                                                                 "solar": {"type": "solar", "url": http.url}}),
                     self.dir)
        live_page(self.dir, rows={3: b"V={v}"})
        Runner(self.dir, log=lambda m: None).step(wait=True)
        self.assertEqual(http.hits, 0)
        live_page(self.dir, number=0x303, rows={3: b"SFI {>sfi}"})
        r = Runner(self.dir, log=lambda m: None)
        r.step(wait=True)
        self.assertEqual(http.hits, 1)
        self.assertEqual(self.rows_of(self.dir / "P303.tti")[3].rstrip(), "SFI    162")

    def test_a_page_level_source_with_its_own_band_filter(self):
        fake = FakeCluster()
        self.addCleanup(fake.close)
        live_page(self.dir, rows={3: b"{dx2.1.call~~~~}"},
                  sources_={"dx2": {"type": "dxcluster", "host": "127.0.0.1", "port": fake.port, "bands": ["2m"],
                                    "timeout": 4, "quiet": 0.4}})
        Runner(self.dir, log=lambda m: None).step(wait=True)
        self.assertEqual(self.rows_of(self.dir / "P301.tti")[3].rstrip(), "PA3ZZG")

    def test_a_plugin_registers_a_source_type(self):
        plug = tmp_dir(self, "ohstxt-test-")
        (plug / "ohstxt_test_plugin.py").write_text(textwrap.dedent("""
            from ohstxt.live.sources import Source, register

            @register("constant")
            class Constant(Source):
                local = True
                def fields(self): return [("word", "a word")]
                def fetch(self): return {"word": self.config.get("word", "hello")}
        """))
        sys.path.insert(0, str(plug))
        self.addCleanup(sys.path.remove, str(plug))
        station.save(station.Station(callsign="M0TXT", plugins=["ohstxt_test_plugin"],
                                     sources={"c": {"type": "constant", "word": "plugged"}}), self.dir)
        p = live_page(self.dir, rows={3: b"{word~~~}"})
        Runner(self.dir, log=lambda m: None).step(wait=True)
        self.assertEqual(self.rows_of(p)[3].rstrip(), "plugged")

    def test_the_command_line_once(self):
        live_page(self.dir, rows={3: b"Call {call~~~}"})
        (self.dir / "station.json").write_text(json.dumps({"callsign": "G4CLI"}))
        r = subprocess.run([sys.executable, "-m", "ohstxt.live", str(self.dir), "--once"], cwd=REPO,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("wrote P301.tti", r.stdout)
        self.assertEqual(self.rows_of(self.dir / "P301.tti")[3].rstrip(), "Call G4CLI")

    def test_the_command_line_refuses_a_missing_folder(self):
        r = subprocess.run([sys.executable, "-m", "ohstxt.live", str(self.dir / "nope"), "--once"], cwd=REPO,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)

    def test_attach_puts_the_tokens_back_for_editing(self):
        from ohstxt.service import Service
        live_page(self.dir, rows={3: b"V={v}"})
        svc = Service.open(self.dir)
        self.assertEqual(svc.pages[0].subpages[0].text(3).rstrip(), "V=--")
        self.assertEqual(live.attach_service(svc), [])
        self.assertEqual(bytes(svc.pages[0].subpages[0].rows[3][:5]), b"V={v}")
        self.assertIsNotNone(svc.pages[0].live)


if __name__ == "__main__":
    unittest.main()
