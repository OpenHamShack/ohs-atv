"""The AKP03E and USB keypads in ATV (CONTROLLER-PAD.md; BETA-PLAN
architecture change 6): the declared actions and default layout are valid
against shack_pad's schemas and its own rules, the actions do what they say
on the simulator, and on the desktop a pad keys only through the transmit
lease - refused exactly as the PTT button is - shows ON AIR, unkeys when it
is unplugged, and hands the pad over when another app's window takes the
focus. The pad is shack_pad's FakeHid, as in ohs-libs' own tests: no real
pad, and nothing keys outside the simulator."""
import json
import os
import re
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import tmp_dir
from ohsatv import padactions, shacklibs
from ohsatv.audio import NullAudio
from ohsatv.radio import Radio, TxRefused
from test_radio import FAST, FakeHardware
from test_ui import HAVE_QT

PKG = Path(padactions.__file__).resolve().parent
SCHEMAS = Path.home() / "ohs-libs" / "spec" / "schemas"


def conforms(value, schema, where="$"):
    """The draft 2020-12 keywords ohs-libs' pad schemas use, checked
    without jsonschema (not installed with the app): a list of problems."""
    out = []
    t = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool}
    if t and not isinstance(value, types[t]):
        return [f"{where}: not {t}"]
    if "const" in schema and value != schema["const"]:
        out.append(f"{where}: not {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{where}: {value!r} not in {schema['enum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            out.append(f"{where}: too short")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{where}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", len(value)):
            out.append(f"{where}: {len(value)} items")
        for i, x in enumerate(value):
            out += conforms(x, schema.get("items", {}), f"{where}[{i}]")
    if isinstance(value, dict):
        out += [f"{where}: {k} missing" for k in schema.get("required", []) if k not in value]
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for k, x in value.items():
            if k in props:
                out += conforms(x, props[k], f"{where}.{k}")
            elif extra is False:
                out.append(f"{where}: {k} not allowed")
            elif isinstance(extra, dict):
                out += conforms(x, extra, f"{where}.{k}")
    return out


def wait(cond, timeout=5.0, radio=None):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if radio is not None:
            radio.heartbeat()          # the window's 30 Hz service, which the watchdog expects
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def make_radio(tc, device=None, **over):
    d = tmp_dir(tc)
    cfg = dict(FAST, callsign="GM4BRI", licenceAck=True, licenceClass="full")
    cfg.update(over)
    r = Radio(cfg, conf_path=d / "c.json", audio=NullAudio(realtime=False), data_dir=d / "data", device=device)
    tc.addCleanup(r.close_station)
    tc.addCleanup(r.stop)
    return r


class Declarations(unittest.TestCase):
    def setUp(self):
        self.decl, self.layout = padactions.declaration()

    def test_valid_by_shack_pads_rules_and_every_control_resolves(self):
        shacklibs.shack_lib("shack_pad")
        from shack_pad import actions, mapping, validate_actions
        from shack_pad.drivers import by_model
        self.assertEqual(self.decl["app"], "atv")
        validate_actions(self.decl)
        acts, notes = actions.load(self.decl)
        self.assertEqual(notes, [])
        self.assertTrue(acts["ptt"].tx and acts["ptt"].hold)
        # Only ptt keys: nothing else on the pad changes what goes on air.
        self.assertEqual({a for a, x in acts.items() if x.tx}, {"ptt"})
        for model in ("ajazz-akp03e", "keypad"):
            _pages, notes = mapping.resolve(by_model(model), self.layout, None, acts)
            self.assertEqual(notes, [], model)
        self.assertEqual(self.layout["ajazz-akp03e"]["K1"], {"press": "ptt"})
        self.assertEqual(self.layout["keypad"]["KPENTER"], {"press": "ptt"})

    @unittest.skipUnless(SCHEMAS.is_dir(), "ohs-libs' schemas are not checked out here")
    def test_against_the_json_schemas(self):
        for name, value in (("pad-actions", self.decl), ("pad-layout", self.layout)):
            schema = json.loads((SCHEMAS / f"{name}.schema.json").read_text())
            self.assertEqual(conforms(value, schema), [], name)
        # The checker does catch what the schema forbids.
        bad = dict(self.decl, actions=[{"id": "Tune", "kind": "spin", "label": "Tune"}])
        self.assertEqual(len(conforms(bad, json.loads((SCHEMAS / "pad-actions.schema.json").read_text()))), 3)


class Actions(unittest.TestCase):
    def test_tune_step_channel_band_mode(self):
        r = make_radio(self, system="fm", rxFreq=1255.0e6, txFreq=1255.0e6)
        pa = padactions.PadActions(r)
        pa.perform("tune", "turn", 3)
        self.assertEqual(r.state()["rxFreq"], 1255.3e6)
        self.assertEqual(r.state()["txFreq"], 1255.3e6)        # follows, as the dial does without split
        pa.perform("step")
        pa.perform("tune", "turn", -1)
        self.assertEqual(r.state()["rxFreq"], 1255.05e6)
        pa.perform("channel", "turn", 1)                       # the band card's channels, in order
        self.assertEqual(r.state()["rxFreq"], 1275.0e6)
        pa.perform("channel", "turn", -2)
        self.assertEqual(r.state()["rxFreq"], 1249.0e6)
        pa.perform("mode:next")
        self.assertEqual(r.state()["system"], "am")
        pa.perform("band:next")
        self.assertEqual(r.state()["band"], "6cm")
        with self.assertRaisesRegex(ValueError, "FM-ATV only"):
            pa.perform("mode:next")
        pa.perform("band", "turn", -2)
        self.assertEqual(r.state()["band"], "70cm")
        # Off the band is the engine's refusal, as a typed frequency's is.
        with self.assertRaisesRegex(ValueError, "outside 70cm"):
            pa.perform("tune", "turn", 400)

    def test_source_view_line_mute_volume_record(self):
        r = make_radio(self)
        pa = padactions.PadActions(r)
        pa.perform("source")
        self.assertEqual(r.state()["source"], "bars")
        pa.perform("view")
        self.assertEqual(r.state()["view"], "tx")
        pa.perform("scope-line", "turn", 5)
        self.assertEqual(r.state()["scopeLine"], 105)
        pa.perform("mute")
        self.assertTrue(pa.values()["mute"])
        v = r.state()["volume"]
        pa.perform("volume", "turn", 2)
        self.assertAlmostEqual(r.state()["volume"], min(1.0, v + 0.1))
        with self.assertRaisesRegex(ValueError, "Start the radio"):
            pa.perform("record")
        r.start()
        pa.perform("record")
        self.assertTrue(r.state()["recording"])
        pa.perform("record")
        self.assertFalse(r.state()["recording"])
        pa.perform("no-such-action")       # a layout naming an unknown action does nothing


@unittest.skipUnless(HAVE_QT, "PyQt6 is not installed")
class DesktopPad(unittest.TestCase):
    """The window's PadLink with a fake AKP03E: K1 held is PTT through the
    lease; unplugged while keyed, the transmission stops."""

    @classmethod
    def setUpClass(cls):
        from ohsatv.ui import make_app
        cls.app = make_app(["test"])

    def link(self, radio, fakes, token=None):
        shacklibs.shack_lib("shack_pad")
        from PyQt6.QtWidgets import QWidget
        from shack_pad.drivers.ajazz import AKP03E
        from shack_pad.fake import FakeHid
        from ohsatv.ui.padlink import PadLink

        def open_device():
            f = FakeHid()
            fakes.append(f)
            return AKP03E(f)
        win = QWidget()
        self.addCleanup(win.close)
        link = PadLink(win, radio, open_device=open_device, token=token)
        self.addCleanup(link.close)
        return link

    def start(self, **over):
        self.radio = make_radio(self, **over)
        self.radio.start()
        self.fakes = []
        self.pad = self.link(self.radio, self.fakes)
        self.assertTrue(wait(lambda: self.fakes and self.pad.pad.serving()))

    def press(self, code, state):
        from shack_pad.fake import akp03e_report
        self.fakes[-1].push(akp03e_report(code, state))

    def keyed(self):
        return self.radio.state()["keyed"]

    def test_k1_held_is_ptt_through_the_lease_and_shows_on_air(self):
        self.start()
        self.assertFalse(self.pad.faces()["onAir"])
        self.press(0x01, 1)
        self.assertTrue(wait(self.keyed, radio=self.radio))
        self.assertIsNotNone(self.pad.lease.holder)
        self.assertTrue(self.pad.faces()["onAir"])
        self.assertEqual([e["event"] for e in self.radio.tx_log()], ["key"])
        self.press(0x01, 0)
        self.assertTrue(wait(lambda: not self.keyed(), radio=self.radio))
        self.assertFalse(self.pad.faces()["onAir"])

    def test_unplugged_while_keyed_unkeys(self):
        self.start()
        self.press(0x01, 1)
        self.assertTrue(wait(self.keyed, radio=self.radio))
        self.fakes[-1].unplug()
        self.assertTrue(wait(lambda: not self.keyed(), timeout=2.0, radio=self.radio))
        self.assertEqual(self.radio.tx_log()[-1]["event"], "unkey")

    def test_a_key_up_the_button_would_refuse_is_refused_the_same(self):
        # A stand-in real radio without "let a real radio transmit": the
        # PTT button's refusal, word for word, and nothing on the air.
        self.start(device=FakeHardware({}, 10e6))
        with self.assertRaises(TxRefused) as cm:
            self.radio.ptt(True)
        self.assertEqual(self.pad._key(), str(cm.exception))
        self.press(0x01, 1)
        time.sleep(0.5)
        self.assertFalse(wait(self.keyed, timeout=0.3, radio=self.radio))
        self.assertIsNone(self.pad.lease.holder)
        self.press(0x01, 0)
        self.assertEqual(self.radio.tx_log(), [])

    def test_the_window_in_front_gets_the_pad(self):
        # Two OpenHamShack windows on one desktop share the lock beside
        # the station store; the one that gains focus claims the pad and
        # the other lets go.
        d = tmp_dir(self)
        with mock.patch.dict(os.environ, {"SHACK_STATION_DB": str(d / "station.db")}):
            self.start()
            other_fakes = []
            other = self.link(make_radio(self), other_fakes, token="second-window")
            other.pad.focus_gained()
            self.assertTrue(wait(lambda: other.pad.serving() and not self.pad.pad.serving(), timeout=5.0))
            self.pad.pad.focus_gained()
            self.assertTrue(wait(lambda: self.pad.pad.serving() and not other.pad.serving(), timeout=5.0))


if __name__ == "__main__":
    unittest.main()
