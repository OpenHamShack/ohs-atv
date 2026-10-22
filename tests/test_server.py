"""The store form's API (ohsatv/server, docs/SERVER-API.md) over a real ATV
engine on the simulator, through real HTTP on loopback: who may ask (only
what the proxy vouches for with this app's secret), what each level may do,
the transmit lease, the engine's guard behind PTT exactly as the window
meets it (70 cm outside 430-440 MHz a warning, 23 cm a refusal, the cable
feed's own check), the broker's say before every key-up, the station
profile, and the streams.

"Hardware" is the simulator dressed as a real radio (`real = True`, as
tests/test_radio.py does), so the guard treats it as a Pluto or a HackRF.
Transmit goes only into the simulator; the broker is a fake that answers as
the edition's does (CONTROLLER-CONTRACT 7). Nothing here opens a radio."""
import base64
import http.client
import json
import os
import socket
import struct
import subprocess
import sys
import time
import unittest
from pathlib import Path

import numpy as np

from helpers import tmp_dir
from ohsatv.devices.sim import SimDevice
from ohsatv.radio import Radio
from ohsatv.server import run as server_run
from ohsatv.server import shack
from ohsatv.server.app import Server, onair_body, scope_item, spectrum_item
from ohsatv.server.audio import StreamAudio

SECRET = "test-proxy-secret"
# AM-VSB at 10 MS/s on 23cm: the cheapest mode the simulator receives.
SIM = dict(device="sim", audioBackend="null", system="am", band="23cm", rxFreq=1249.0e6, txFreq=1249.0e6,
           amRate=10_000_000, simStations=True, callsign="GM4BRI", licenceAck=True, licenceClass="full")


class FakeHardware(SimDevice):
    """The simulator dressed as a real radio (tests/test_radio.py's)."""
    kind = "fakehw"
    real = True

    def __init__(self, cfg, fs, rng=(325e6, 3800e6), duplex=False):
        super().__init__(cfg, fs)
        self._rng, self._duplex = rng, duplex

    def freq_range(self):
        return self._rng

    def duplex(self):
        return self._duplex


class FakeHackRF(FakeHardware):
    kind = "hackrf"
    can_rx = False


class FakeBroker:
    """The edition's broker, as far as one app sees it: claims, modes, on
    air, heartbeats, and the knobs a test needs (refuse on air, take a
    claim back). QO-100 NB's test fake, with a HackRF's USB access."""

    def __init__(self):
        self.calls = []
        self.claims = {}
        self.refuse_onair = None
        self.n = 0
        self.grace = False          # just restarted: only {"reclaim": true} is granted
        self.refuse_next = []       # (status, answer) for the next claims, first first
        self.down = False           # the controller cannot be reached
        self.release_requested = None

    def restart(self):
        """The edition's broker restarting (a platform update): every claim
        is gone, and for its grace only re-claims are granted."""
        self.claims.clear()
        self.grace = True

    def __call__(self, method, path, body=None, headers=None):
        if self.down:
            raise OSError("connection refused")
        self.calls.append((method, path, body, dict(headers or {})))
        if method == "POST" and path == "/broker/claims":
            if self.refuse_next:
                return self.refuse_next.pop(0)
            if self.grace and not body.get("reclaim"):
                return 503, {"error": "The station has just restarted", "code": "broker_restarting",
                             "retryAfter": 15}
            self.n += 1
            cid = f"cl_{self.n}"
            self.claims[cid] = {"device": body["device"], "mode": body["mode"], "onAir": False}
            access = ({"reach": "usb", "usbId": "1d50:6089", "serial": "0000000000000000a06063c8"}
                      if body["device"].startswith("hackrf") else {"reach": "network", "address": "192.168.2.1"})
            return 201, {"claimId": cid, "device": body["device"], "mode": body["mode"],
                         "lease": {"seconds": 10, "heartbeatEvery": 2}, "access": access}
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[1] == "claims":
            c = self.claims.get(parts[2])
            if c is None:
                return 404, {"error": "no such claim", "code": "not_found"}
            if method == "DELETE":
                self.claims.pop(parts[2])
                return 200, {"released": True}
            what = parts[3]
            if what == "mode":
                c["mode"] = body["mode"]
                return 200, {"mode": body["mode"]}
            if what == "onair":
                if body.get("on") and self.refuse_onair:
                    return 403, {"error": self.refuse_onair, "code": "licence_refused"}
                c["onAir"] = bool(body.get("on"))
                return 200, {"onAir": c["onAir"], "lease": {"seconds": 6, "heartbeatEvery": 2}}
            if what == "heartbeat":
                return 200, {"state": "granted", "leaseSeconds": 10,
                             "releaseRequested": self.release_requested or False}
        return 404, {"error": "unknown", "code": "not_found"}

    def claim_bodies(self):
        return [b for m, p, b, _h in self.calls if m == "POST" and p == "/broker/claims"]

    def onairs(self):
        return [(c[2], c[3]) for c in self.calls if c[1].endswith("/onair")]

    def by_device(self, dev):
        return next((c for c in self.claims.values() if c["device"] == dev), None)


def beat_now(srv, silent_for=0.0):
    """Make every claim's heartbeat due on the broker loop's next pass, as
    if the broker had last answered `silent_for` seconds ago."""
    with srv.broker.lock:
        for c in srv.broker.claims.values():
            c["beat"] = -1e9
            c["ok"] -= silent_for


class ServerTest(unittest.TestCase):
    broker = False
    cfg: dict = {}

    def radio_kwargs(self, cfg):
        return {}

    def setUp(self):
        self.tmp = tmp_dir(self, "ohsatv-servertest-")
        self.audio = StreamAudio(realtime=False)
        cfg = {**SIM, **self.cfg}
        self.radio = Radio(cfg, conf_path=self.tmp / "c.json", data_dir=self.tmp / "data", audio=self.audio,
                           persist=False, **self.radio_kwargs(cfg))
        self.fake = FakeBroker() if self.broker else None
        bc = shack.BrokerClient(self.fake) if self.fake else None
        self.now = [1000.0]
        self.srv = Server(self.radio, self.audio, version="9.9.9", secret=SECRET, broker=bc,
                          clock=lambda: self.now[0])
        httpd = self.srv.serve("127.0.0.1", 0)
        self.port = httpd.server_address[1]
        self.addCleanup(self.srv.shutdown)
        self.assertEqual(self.srv.start_radio(), "")

    def headers(self, level="transmit", secret=SECRET, user="brian"):
        h = {"Content-Type": "application/json"}
        if secret is not None:
            h[shack.SHACK_SECRET_HEADER] = secret
        if level is not None:
            h.update({"X-OpenHamShack-User": user, "X-OpenHamShack-Level": level,
                      "X-OpenHamShack-Device": "dev_TEST", "X-OpenHamShack-Auth": "device"})
        return h

    def req(self, method, path, body=None, level="transmit", secret=SECRET, user="brian", extra=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        c.request(method, path, json.dumps(body) if body is not None else None,
                  {**self.headers(level, secret, user), **(extra or {})})
        r = c.getresponse()
        raw = r.read()
        c.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def wait(self, cond, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.02)
        return False

    def key(self, remote="phone-1"):
        s, a = self.req("POST", "/api/ptt", {"on": True, "remote": remote})
        self.assertEqual(s, 200, a)
        return a


class WhoMayAsk(ServerTest):
    def test_nothing_without_the_proxy_secret(self):
        for secret in (None, "wrong", ""):
            s, a = self.req("GET", "/api/status", secret=secret)
            self.assertEqual((s, a["code"]), (401, "proxy_required"))
        # Forged identity headers without the secret change nothing.
        s, _a = self.req("POST", "/api/ptt", {"on": True, "remote": "x"}, secret=None)
        self.assertEqual(s, 401)
        self.assertFalse(self.radio.state()["keyed"])

    def test_no_secret_configured_is_a_locked_app(self):
        self.srv.secret = ""
        s, a = self.req("GET", "/api/status")
        self.assertEqual((s, a["code"]), (401, "proxy_required"))

    def test_view_reads_and_changes_nothing(self):
        s, a = self.req("GET", "/api/status", level="view")
        self.assertEqual((s, a["app"], a["kind"]), (200, "shack-atv", "atv"))
        self.assertIn("remote", a["tx"])            # the lease is offered (GUIDE 2)
        s, a = self.req("POST", "/api/set", {"volume": 0.5}, level="view")
        self.assertEqual((s, a["code"], a["needs"]), (403, "level_too_low", "control"))

    def test_control_tunes_but_never_transmits(self):
        s, a = self.req("POST", "/api/set", {"rxFreq": 1255.0e6, "volume": 0.4}, level="control")
        self.assertEqual(s, 200, a)
        self.assertEqual(self.radio.state()["rxFreq"], 1255.0e6)
        s, a = self.req("POST", "/api/ptt", {"on": True, "remote": "r1"}, level="control")
        self.assertEqual((s, a["needs"]), (403, "transmit"))
        self.assertFalse(self.radio.state()["keyed"])
        # What goes on the air is Transmit's, through /api/set as anywhere.
        for change in ({"txGain": -30.0}, {"identText": "HELLO"}, {"ttxOn": True}, {"txGainCeiling": -20.0}):
            s, a = self.req("POST", "/api/set", change, level="control")
            self.assertEqual((s, a.get("needs")), (403, "transmit"), change)
        self.assertEqual(self.req("POST", "/api/set", {"txGain": -30.0})[0], 200)

    def test_tuning_while_on_the_air_needs_transmit(self):
        self.assertTrue(self.key()["onAir"])
        s, a = self.req("POST", "/api/set", {"rxFreq": 1255.0e6}, level="control")
        self.assertEqual((s, a["needs"]), (403, "transmit"))
        self.assertEqual(self.req("POST", "/api/set", {"volume": 0.3}, level="control")[0], 200)
        # Nor may Control end somebody's over by stopping the radio: halt is
        # its way to stop a transmission.
        self.assertEqual(self.req("POST", "/api/stop", level="control")[0], 403)
        self.assertTrue(self.radio.state()["keyed"])

    def test_the_profile_and_the_hardware_are_the_controllers(self):
        for change, word in (({"callsign": "M0XYZ"}, "station profile"), ({"licenceAck": True}, "station profile"),
                             ({"licenceClass": "foundation"}, "station profile"),
                             ({"plutoUri": "ip:10.0.0.1"}, "controller looks after")):
            s, a = self.req("POST", "/api/set", change)
            self.assertEqual(s, 400)
            self.assertIn(word, a["error"])
        self.assertEqual(self.radio.state()["callsign"], "GM4BRI")

    def test_the_radios_shell_password_never_leaves(self):
        s, st = self.req("GET", "/api/state", level="view")
        self.assertEqual(s, 200)
        self.assertIn("rxFreq", st)
        self.assertNotIn("radioSshPassword", st)
        self.assertNotIn("radioSshPassword", self.req("GET", "/api/spec", level="view")[1])

    def test_platform_calls_reach_only_their_routes(self):
        self.assertEqual(self.req("GET", "/api/shack/busy", level="none")[1], {"busy": False, "why": ""})
        self.assertEqual(self.req("GET", "/api/status", level="none")[0], 403)

    def test_status_page_needs_nothing_from_outside_and_builds_no_absolute_url(self):
        s, page = self.req("GET", "/", level="view", extra={"X-Forwarded-Prefix": "/shack-atv"})
        self.assertEqual(s, 200)
        text = page.decode()
        self.assertIn("OpenHamShack ATV", text)
        self.assertNotIn("http", text.replace("content='width", ""))      # no external references
        self.assertIn("src='/shack-atv/api/video/rx.mjpeg'", text)
        # A prefix that is not a plain path is not echoed into the page.
        text = self.req("GET", "/", level="view", extra={"X-Forwarded-Prefix": "/x'><script>"})[1].decode()
        self.assertNotIn("<script>", text)
        self.assertIn("src='/api/video/rx.mjpeg'", text)


class Lease(ServerTest):
    def test_key_hold_and_lose_the_remote(self):
        a = self.key()
        self.assertTrue(a["onAir"], a["txError"])
        self.assertEqual(a["tx"]["remote"]["id"], "phone-1")
        s, a = self.req("POST", "/api/ptt/heartbeat", {"remote": "phone-2"})
        self.assertEqual((s, a["code"]), (409, "not_lease_holder"))
        self.assertEqual(self.req("POST", "/api/ptt/heartbeat", {"remote": "phone-1"})[0], 200)
        self.now[0] += 5.5                   # the remote goes quiet
        self.assertTrue(self.wait(lambda: not self.radio.state()["keyed"]))
        s, a = self.req("GET", "/api/status")
        self.assertIn("remote controller lost", a["txError"])
        s, a = self.req("POST", "/api/ptt/heartbeat", {"remote": "phone-1"})
        self.assertEqual((s, a["onAir"]), (409, False))
        # The transmit log says why the over ended.
        unkey = [e for e in self.req("GET", "/api/txlog", level="view")[1]["entries"] if e["event"] == "unkey"][-1]
        self.assertIn("remote controller lost", unkey["why"])

    def test_heartbeats_keep_the_engines_watchdog_fed(self):
        self.radio.set(authorise=True, watchdogSec=0.5)
        self.assertTrue(self.key()["onAir"])
        end = time.monotonic() + 1.5          # three watchdog periods with nobody draining a feed
        while time.monotonic() < end:
            self.assertEqual(self.req("POST", "/api/ptt/heartbeat", {"remote": "phone-1"})[0], 200)
            time.sleep(0.2)
        self.assertTrue(self.radio.state()["keyed"])

    def test_another_remote_cannot_take_the_air(self):
        self.key("phone-1")
        a = self.key("phone-2")
        self.assertEqual(a["tx"]["remote"]["id"], "phone-1")
        self.assertTrue(a["txError"])

    def test_unkey_and_halt(self):
        self.key()
        self.req("POST", "/api/ptt", {"on": False})
        self.assertFalse(self.radio.state()["keyed"])
        self.key()
        s, a = self.req("POST", "/api/halt", level="control", user="alice")
        self.assertFalse(self.radio.state()["keyed"])
        self.assertIn("alice", a["txError"])
        self.assertEqual(self.req("GET", "/api/shack/busy", level="none")[1]["busy"], False)

    def test_the_window_s_refusal_is_200_with_the_reason(self):
        self.radio.set(licenceAck=False)
        a = self.key()
        self.assertFalse(a["onAir"])
        self.assertEqual(a["txError"], self.radio.state()["txRefused"][0])
        self.assertIn("licence", a["txError"])
        self.assertFalse(self.radio.state()["keyed"])

    def test_bad_remote(self):
        s, a = self.req("POST", "/api/ptt", {"on": True, "remote": "no spaces allowed"})
        self.assertEqual(s, 400)

    def test_busy_while_on_the_air(self):
        self.key()
        busy = self.req("GET", "/api/shack/busy", level="none")[1]
        self.assertTrue(busy["busy"])
        self.assertTrue(busy["why"])


class Guard(ServerTest):
    """The engine's guard on real hardware, reached through the API."""
    cfg = {"device": "pluto", "allowHardwareTx": True}

    def radio_kwargs(self, cfg):
        return {"device": FakeHardware(cfg, 10e6)}

    def tune(self, band, freq):
        s, a = self.req("POST", "/api/set", {"band": band, "system": "am", "rxFreq": freq, "txFreq": freq})
        self.assertEqual(s, 200, a)

    def test_70cm_outside_430_440_is_a_warning_not_a_refusal(self):
        self.tune("70cm", 437.0e6)          # AM-VSB with sound: 435.75-443.1 MHz
        a = self.key()
        self.assertTrue(a["onAir"], a["txError"])
        self.assertTrue(any(w.startswith("OUTSIDE THE ALLOCATION") for w in a["txWarnings"]), a["txWarnings"])
        self.assertTrue(any(w.startswith("OUTSIDE THE BANDPLAN") for w in a["txWarnings"]))
        key = [e for e in self.radio.tx_log() if e["event"] == "key"][-1]
        self.assertTrue(any(w.startswith("OUTSIDE THE ALLOCATION") for w in key["warnings"]))

    def test_23cm_outside_the_allocation_is_refused(self):
        self.tune("23cm", 1240.5e6)
        a = self.key()
        self.assertFalse(a["onAir"])
        self.assertIn("outside the allocation 1240-1325 MHz", a["txError"])
        self.assertFalse(self.radio.state()["keyed"])

    def test_the_cable_feed_has_its_own_check_not_the_amateur_one(self):
        self.assertEqual(self.req("POST", "/api/set", {"cableFeedAck": True}, level="control")[0], 403)
        s, a = self.req("POST", "/api/set", {"cableFeedAck": True})
        self.assertEqual(s, 200, a)
        self.assertEqual(self.req("POST", "/api/set", {"cableFeed": True})[0], 200)
        st = self.req("GET", "/api/status")[1]
        self.assertEqual((st["band"], st["cableFeed"]), ("Band I", True))
        a = self.key()
        self.assertFalse(a["onAir"])
        # This radio tunes from 325 MHz: the cable check names the channels
        # it cannot reach; the amateur bandplan says nothing.
        self.assertIn("Band I channels", a["txError"])
        self.assertFalse(any("70cm, 23cm or 6cm" in r for r in a["txRefused"]))
        self.assertFalse(any("BANDPLAN" in w for w in a["txWarnings"]))


class Broker(ServerTest):
    broker = True
    cfg = {"device": "pluto", "allowHardwareTx": True}

    def radio_kwargs(self, cfg):
        return {"device": FakeHardware(cfg, 10e6)}

    def test_receive_claim_at_start_and_where_the_radio_is(self):
        first = self.fake.calls[0]
        self.assertEqual((first[0], first[1], first[2]["device"], first[2]["mode"]),
                         ("POST", "/broker/claims", "pluto-1", "rx"))
        self.assertEqual(self.radio.state()["plutoUri"], "ip:192.168.2.1")

    def test_key_up_asks_the_broker_first_and_unkey_tells_it_after(self):
        a = self.key()
        self.assertTrue(a["onAir"], a["txError"])
        body, headers = self.fake.onairs()[0]
        self.assertTrue(body["on"])
        self.assertEqual(headers[shack.SHACK_ACTING_HEADER], "dev_TEST")
        self.assertEqual(body["chain"], ["pluto-1"])
        # The whole occupied signal, not the vision carrier alone.
        lo, hi = self.radio.state()["plan"]["occupied"]
        self.assertEqual((body["freqHz"], body["bandwidthHz"], body["mode"]),
                         (int(round(1249.0e6 + (lo + hi) / 2)), int(round(hi - lo)), "pal-am"))
        self.req("POST", "/api/ptt", {"on": False})
        self.assertFalse(self.fake.onairs()[-1][0]["on"])
        claim = self.fake.by_device("pluto-1")
        self.assertEqual((claim["mode"], claim["onAir"]), ("rx", False))

    def test_the_engines_refusal_never_troubles_the_broker(self):
        self.radio.set(licenceAck=False)
        self.assertFalse(self.key()["onAir"])
        self.assertEqual(self.fake.onairs(), [])
        self.assertEqual(self.fake.by_device("pluto-1")["mode"], "rx")

    def test_broker_refusal_means_no_key_up(self):
        self.fake.refuse_onair = "The station licence does not allow this."
        a = self.key()
        self.assertEqual((a["onAir"], a["txError"]), (False, "The station licence does not allow this."))
        self.assertFalse(self.radio.state()["keyed"])
        self.assertEqual(self.fake.by_device("pluto-1")["mode"], "rx")

    def beat_now(self, silent_for=0.0):
        """Make every claim's heartbeat due on the broker loop's next pass,
        as if the broker had last answered `silent_for` seconds ago."""
        beat_now(self.srv, silent_for)

    def count_stops(self):
        stops = []
        real = self.radio.stop
        self.radio.stop = lambda *a, **k: (stops.append(1), real(*a, **k))[1]
        return stops

    def test_a_claim_taken_back_unkeys_stops_and_is_reclaimed_for_receive(self):
        self.key()
        self.assertTrue(self.radio.state()["keyed"])
        stops = self.count_stops()
        self.fake.claims.clear()              # the admin's "Stop all transmitting"
        self.beat_now()
        self.assertTrue(self.wait(lambda: self.fake.claim_bodies()[-1]["reclaim"]
                                  and self.radio.state()["running"] and self.srv.reclaim is None))
        # RF off and the engine stopped before anything else; then back,
        # receiving only: a lost claim never resumes a transmission.
        self.assertEqual(len(stops), 1)
        self.assertFalse(self.radio.state()["keyed"])
        self.assertEqual([c["mode"] for c in self.fake.claims.values()], ["rx"])
        s, a = self.req("GET", "/api/status")
        self.assertIn("took pluto-1 back", a["txError"])
        self.assertEqual((a["radioError"], a["reconnecting"], a["onAir"]), ("", False, False))

    def test_a_broker_restart_is_reclaimed_after_its_retry_after(self):
        # QO-100 NB on ohs-box 10/10: a platform update restarted the
        # broker, the heartbeat met 404, and the app stayed stopped for
        # hours. ATV had the same code.
        self.fake.restart()
        self.fake.refuse_next = [(503, {"error": "restarting", "code": "broker_restarting", "retryAfter": 5})]
        self.beat_now()
        self.assertTrue(self.wait(lambda: len(self.fake.claim_bodies()) == 2 and self.srv.status()["reconnecting"]))
        st = self.srv.status()
        self.assertEqual((st["running"], st["radioError"]), (False, "Reconnecting to the station…"))
        # Not before the broker's retryAfter...
        time.sleep(1.2)
        self.assertEqual(len(self.fake.claim_bodies()), 2)
        self.assertFalse(self.radio.state()["running"])
        # ...and then the claim is re-taken during the grace, and the radio runs.
        self.now[0] += 5
        self.assertTrue(self.wait(lambda: self.radio.state()["running"] and self.srv.reclaim is None))
        self.assertEqual(len(self.fake.claim_bodies()), 3)
        self.assertTrue(self.fake.claim_bodies()[-1]["reclaim"])
        st = self.srv.status()
        self.assertEqual((st["reconnecting"], st["radioError"]), (False, ""))
        self.assertTrue(self.srv.wanted)

    def test_an_unreachable_controller_is_reclaimed_when_it_answers(self):
        self.fake.down = True
        self.beat_now(silent_for=60)
        self.assertTrue(self.wait(lambda: self.srv.status()["reconnecting"]))
        self.assertFalse(self.radio.state()["running"])
        # Backing off while it is down: each try doubles the wait.
        for _n in range(3):
            self.now[0] += 30
            time.sleep(0.6)
        self.assertGreater(self.srv.reclaim["delay"], 1.0)
        self.assertFalse(self.radio.state()["running"])
        self.fake.down = False
        self.now[0] += 30
        self.assertTrue(self.wait(lambda: self.radio.state()["running"] and self.srv.reclaim is None))
        self.assertTrue(self.fake.claim_bodies()[-1]["reclaim"])
        self.assertFalse(self.srv.status()["reconnecting"])

    def test_another_app_holding_it_ends_the_reconnection(self):
        self.fake.restart()
        self.fake.refuse_next = [(409, {"error": "QO-100 WB has the Pluto (receiving).", "code": "device_held"})]
        self.beat_now()
        self.assertTrue(self.wait(lambda: self.srv.status()["radioError"] == "QO-100 WB has the Pluto (receiving)."))
        st = self.srv.status()
        self.assertEqual((st["reconnecting"], st["running"]), (False, False))
        self.assertTrue(self.srv.wanted)        # run.py's plain retry carries on, as at start-up

    def test_a_person_stopping_it_ends_the_reconnection(self):
        self.fake.down = True
        self.beat_now(silent_for=60)
        self.assertTrue(self.wait(lambda: self.srv.status()["reconnecting"]))
        self.fake.down = False
        self.req("POST", "/api/stop", level="control")
        self.assertTrue(self.wait(lambda: self.srv.reclaim is None))
        n = len(self.fake.claim_bodies())
        self.now[0] += 60
        time.sleep(1.2)
        self.assertEqual(len(self.fake.claim_bodies()), n)
        self.assertFalse(self.radio.state()["running"])

    def test_a_release_requested_on_the_heartbeat_is_a_hand_over(self):
        self.fake.release_requested = {"for": "shack-qo100nb", "deadlineSeconds": 60, "stop": False}
        self.beat_now()
        self.assertTrue(self.wait(lambda: not self.radio.state()["running"] and not self.fake.claims))
        self.assertFalse(self.srv.wanted)
        n = len(self.fake.claim_bodies())
        self.now[0] += 60
        time.sleep(1.2)
        self.assertEqual(len(self.fake.claim_bodies()), n)
        st = self.srv.status()
        self.assertEqual((st["running"], st["reconnecting"], st["radioError"]),
                         (False, False, "The radio was handed to another app"))

    def test_release_request(self):
        self.key()
        s, a = self.req("POST", "/api/shack/release", {"device": "pluto-1"}, level="none")
        self.assertEqual((s, a), (409, {"onAir": True}))
        self.req("POST", "/api/ptt", {"on": False})
        s, a = self.req("POST", "/api/shack/release", {"device": "pluto-1"}, level="none")
        self.assertEqual(s, 202)
        self.assertFalse(self.radio.state()["running"])
        self.assertEqual(self.fake.claims, {})


class BrokerWithHackRF(ServerTest):
    """A HackRF transmitting beside the Pluto receiver: both claimed for
    receive at start, and only the HackRF goes on the air."""
    broker = True
    cfg = {"device": "pluto", "txDevice": "hackrf", "allowHardwareTx": True}

    def radio_kwargs(self, cfg):
        return {"device": FakeHardware(cfg, 10e6),
                "tx_device": FakeHackRF(cfg, 10e6, rng=(1e6, 6e9), duplex=True)}

    def test_both_claimed_and_the_hackrf_keyed(self):
        claimed = [c[2]["device"] for c in self.fake.calls if c[1] == "/broker/claims"]
        self.assertEqual(claimed, ["pluto-1", "hackrf-1"])
        self.assertEqual(self.radio.state()["hackrfSerial"], "0000000000000000a06063c8")
        a = self.key()
        self.assertTrue(a["onAir"], a["txError"])
        self.assertEqual(self.fake.onairs()[0][0]["chain"], ["hackrf-1"])
        self.assertEqual((self.fake.by_device("hackrf-1")["mode"], self.fake.by_device("pluto-1")["mode"]),
                         ("tx", "rx"))
        self.req("POST", "/api/ptt", {"on": False})
        self.assertEqual(self.fake.by_device("hackrf-1")["mode"], "rx")

    def test_a_broker_restart_reclaims_both_radios_for_receive(self):
        # Both claims are lost in one pass; the engine runs both radios,
        # so both are taken back together, receiving, the HackRF off the air.
        self.key()
        self.fake.restart()
        beat_now(self.srv)
        self.assertTrue(self.wait(lambda: self.radio.state()["running"] and self.srv.reclaim is None
                                  and len(self.fake.claims) == 2))
        self.assertFalse(self.radio.state()["keyed"])
        reclaimed = [b["device"] for b in self.fake.claim_bodies() if b["reclaim"]]
        self.assertEqual(reclaimed, ["pluto-1", "hackrf-1"])
        self.assertEqual(sorted((c["device"], c["mode"], c["onAir"]) for c in self.fake.claims.values()),
                         [("hackrf-1", "rx", False), ("pluto-1", "rx", False)])
        self.assertIn("The controller took", self.srv.status()["txError"])


class Profile(unittest.TestCase):
    """The callsign, locator and licence answer come from the controller's
    station profile (run.apply_profile)."""

    def make(self):
        tmp = tmp_dir(self, "ohsatv-servertest-")
        r = Radio({**SIM, "callsign": "", "licenceAck": False}, conf_path=tmp / "c.json", data_dir=tmp / "d",
                  audio=StreamAudio(realtime=False), persist=False)
        self.addCleanup(r.stop)
        return r

    def test_the_profile_reaches_the_engine(self):
        r = self.make()
        prof = {"callsign": "gm4bri", "locator": "IO75RL", "dxccPrefix": "GM",
                "licence": {"regime": "auto", "class": "intermediate"}, "derived": {"canTransmit": True}}
        self.assertEqual(server_run.apply_profile(r, lambda m, p: (200, prof)), "")
        st = r.state()
        self.assertEqual((st["callsign"], st["locator"], st["licenceAck"]), ("GM4BRI", "IO75RL", True))
        # The licence the engine's band check reads is the profile's, so a
        # store app's limits follow the controller's station, not a default.
        self.assertEqual((st["stationCountry"], st["licenceClass"]), ("GM", "intermediate"))
        prof["derived"]["canTransmit"] = False
        server_run.apply_profile(r, lambda m, p: (200, prof))
        self.assertFalse(r.state()["licenceAck"])

    def test_an_unreachable_controller_is_said_and_changes_nothing(self):
        r = self.make()

        def down(m, p):
            raise OSError("connection refused")
        self.assertIn("did not answer", server_run.apply_profile(r, down))
        self.assertIn("HTTP 503", server_run.apply_profile(r, lambda m, p: (503, {})))
        self.assertEqual(r.state()["callsign"], "")


class Streams(ServerTest):
    cfg = {"rxFreq": 1275.0e6, "txFreq": 1275.0e6}       # the simulator's AM-VSB station

    def ws(self, path, level="transmit"):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        h = "".join(f"{k}: {v}\r\n" for k, v in self.headers(level).items())
        s.sendall((f"GET {path} HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n{h}\r\n").encode())
        f = s.makefile("rb")
        self.assertIn(b"101", f.readline())
        while f.readline() not in (b"\r\n", b""):
            pass
        self.addCleanup(s.close)
        return s, f

    @staticmethod
    def frame(f):
        b0, b1 = f.read(2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack("!H", f.read(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", f.read(8))[0]
        return b0 & 0x0F, f.read(n)

    def test_events_carry_the_engines_feeds_and_no_pictures(self):
        s, f = self.ws("/api/events?kinds=state,meter,spectrum,video")
        hello = json.loads(self.frame(f)[1])
        self.assertEqual((hello["kind"], hello["status"]["app"]), ("hello", "shack-atv"))
        self.req("POST", "/api/set", {"volume": 0.25}, level="control")
        kinds = set()
        end = time.monotonic() + 10
        while time.monotonic() < end and not {"state", "meter", "spectrum"} <= kinds:
            msg = json.loads(self.frame(f)[1])
            kinds.add(msg["kind"])
            if msg["kind"] == "state":
                self.assertNotIn("radioSshPassword", msg["changes"])
            if msg["kind"] == "spectrum":
                self.assertEqual(len(base64.b64decode(msg["cdb"])), 2 * msg["n"])
        self.assertLessEqual({"state", "meter", "spectrum"}, kinds)
        self.assertNotIn("video", kinds)

    def test_the_received_picture_is_mjpeg_at_4_3(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        c.request("GET", "/api/video/rx.mjpeg", headers=self.headers("view"))
        r = c.getresponse()
        self.assertEqual(r.getheader("Content-Type"), "multipart/x-mixed-replace; boundary=frame")
        self.assertEqual(r.fp.readline(), b"--frame\r\n")
        head = {}
        while (line := r.fp.readline()) != b"\r\n":
            k, v = line.decode().split(":", 1)
            head[k.strip().lower()] = v.strip()
        self.assertEqual(head["content-type"], "image/jpeg")
        jpeg = r.fp.read(int(head["content-length"]))
        c.close()
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(jpeg))
        self.assertEqual(img.size, (768, 576))
        # The simulator's test card, decoded: not a blank frame.
        self.assertGreater(np.asarray(img.convert("L"), np.float32).std(), 20)

    def test_receive_audio_is_announced_pcm(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        c.request("GET", "/api/audio/rx", headers=self.headers("view"))
        r = c.getresponse()
        self.assertEqual(r.getheader("Content-Type"), "audio/L16; rate=48000; channels=1")
        self.assertEqual(json.loads(r.getheader("X-Audio-Format")),
                         {"encoding": "s16le", "rate": 48000, "channels": 1})
        self.assertEqual(len(r.read(4800)), 4800)
        c.close()

    def test_a_gone_listener_is_dropped_while_the_radio_is_silent(self):
        # QO-100 NB on ohs-box 10/10: the radio stopped, a TV reconnecting
        # every ~7 s, and status counted 20 listeners: with nothing to send,
        # the handler never wrote, so never learnt the client had gone.
        self.radio.stop()
        for _n in range(3):
            s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
            s.sendall((f"GET /api/audio/rx HTTP/1.1\r\nHost: x\r\n{shack.SHACK_SECRET_HEADER}: {SECRET}\r\n"
                       "X-OpenHamShack-User: b\r\nX-OpenHamShack-Level: view\r\n\r\n").encode())
            f = s.makefile("rb")
            self.assertIn(b"200", f.readline())
            while f.readline() not in (b"\r\n", b""):
                pass
            # Silence, in real time, rather than nothing.
            self.assertEqual(f.read(9600), bytes(9600))
            f.close()
            s.close()
        self.assertTrue(self.wait(lambda: self.audio.listeners() == 0, timeout=8), self.audio.listeners())

    def test_remote_microphone_needs_transmit_and_feeds_the_mic(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/api/audio/tx", headers={**self.headers("control"), "Upgrade": "websocket",
                                                   "Connection": "Upgrade", "Sec-WebSocket-Key": "eA=="})
        self.assertEqual(c.getresponse().status, 403)
        c.close()
        s, f = self.ws("/api/audio/tx")
        self.assertEqual(json.loads(self.frame(f)[1])["format"]["rate"], 48000)
        pcm = struct.pack("<h", 8000) * 1200
        mask = os.urandom(4)
        payload = bytes(c ^ mask[i % 4] for i, c in enumerate(pcm))
        s.sendall(struct.pack("!BBH", 0x82, 0x80 | 126, len(pcm)) + mask + payload)
        self.assertTrue(self.wait(lambda: len(self.audio.mic) >= 1200))
        self.assertAlmostEqual(float(self.audio.read_mic(1200)[0]), 8000 / 32768, places=4)

    def test_items_are_compact(self):
        item = spectrum_item("spectrum", (1.0, 1244e6, 9765.625, np.array([-100.0, -50.25, 0.0])))
        back = np.frombuffer(base64.b64decode(item["cdb"]), "<i2") / 100.0
        self.assertEqual(list(back), [-100.0, -50.25, 0.0])
        sc = scope_item({"t": 1.0, "line": 100, "fs": 10e6, "wave": np.array([-0.3, 0.0, 0.7]),
                         "uv": (np.array([0.1]), np.array([-0.2]))})
        self.assertEqual(list(np.frombuffer(base64.b64decode(sc["mv"]), "<i2")), [-300, 0, 700])
        self.assertEqual(list(np.frombuffer(base64.b64decode(sc["vMv"]), "<i2")), [-200])

    def test_the_onair_body_is_the_whole_signal(self):
        self.radio.set(system="fm", rxFreq=1255.0e6, txFreq=1255.0e6)
        body = onair_body(self.radio)
        lo, hi = self.radio.state()["plan"]["occupied"]
        self.assertEqual(body["mode"], "pal-fm")
        self.assertEqual(body["bandwidthHz"], int(round(hi - lo)))
        self.assertFalse(body["cableFeed"])


class NoServerInTheDesktopApp(unittest.TestCase):
    """GUIDE section 1: no servers inside the desktop apps. The window's and
    the engine's import graph must not reach ohsatv.server at all."""

    def test_the_window_does_not_load_the_server(self):
        code = ("import sys, ohsatv.ui, ohsatv.ui.mainwindow, ohsatv.radio, ohsatv.__main__; "
                "print(sorted(m for m in sys.modules if m.startswith('ohsatv.server')))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                             cwd=Path(__file__).resolve().parent.parent,
                             env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "[]")


if __name__ == "__main__":
    unittest.main()
