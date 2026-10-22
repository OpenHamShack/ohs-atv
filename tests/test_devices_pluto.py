"""The Pluto driver against a fake `iio` module: no radio is opened and
nothing is transmitted. The fake is injected as sys.modules["iio"], so even
on a host with python3-libiio and a Pluto attached, the real bindings are
never reached.

The fake AD9361 behaves like the driver where it matters here: it refuses
LO frequencies and sample rates outside what its personality allows (as
ad9361_validate_rfpll() does), clamps rf_bandwidth as
ad9361_validate_rf_bw() does, and keeps what was written to its attributes
and pushed to its transmit buffer, so the tests measure what the radio
would have been told."""
from __future__ import annotations

import errno
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

from helpers import crop, tmp_dir  # noqa: F401  (crop: path setup)
from ohsatv import host
from ohsatv.devices import pluto
from ohsatv.devices.base import DeviceError

MHZ = 1e6
PLUTO_SERIAL = "UQRUR7HPFYWAMKGY"
AD9363_RX = "[325000000 1 3800000000]"
AD9363_TX = "[325000000 1 3800000000]"
# ADI's iio_info example of a Pluto switched to AD9364 (docs/atv-research.md 5.2).
AD9364_RX = "[70000000 1 6000000000]"
AD9364_TX = "[46875001 1 6000000000]"


class Attr:
    def __init__(self, node, name, value=""):
        self.node, self.name, self._v = node, name, value

    @property
    def value(self):
        stuck = self.node.radio.stuck.get((self.node.label, self.name))
        return self._v if stuck is None else stuck[0]

    @value.setter
    def value(self, v):
        self._v = self.node.radio.check(self.node, self.name, str(v))
        self.node.radio.log.append((self.node.label, self.name, self._v))
        self.node.radio.wrote(self.node.label, self.name)


class PathRates(Attr):
    """ad9361-phy rx_path_rates / tx_path_rates, read-only and worked out
    from the rate as the driver's nominal rate governor does: the converter
    at 8 x the rate, a half-band (x2) at each step down, then the FIR -
    x1 when it is off, x4 when somebody's 128-tap FIR is on (a LibreSDR read
    on 5 October 2026: "BBPLL:800000000 DAC:100000000 T2:50000000
    T1:25000000 TF:12500000 TXSAMP:12500000" at 12.5 MS/s)."""

    def __init__(self, node, name, path):
        super().__init__(node, name)
        self.path = path

    @property
    def value(self):
        radio = self.node.radio
        r = radio.rate or 0
        fir = 4 if radio.fir_on() else 1
        names = ("ADC", "R2", "R1", "RF", "RXSAMP") if self.path == "rx" else ("DAC", "T2", "T1", "TF", "TXSAMP")
        vals = (8 * fir * r, 4 * fir * r, 2 * fir * r, fir * r, r)
        return f"BBPLL:{8 * fir * r * 8} " + " ".join(f"{n}:{v}" for n, v in zip(names, vals))

    @value.setter
    def value(self, v):
        raise OSError(errno.EACCES, "Permission denied")


class Node:
    """A device or channel: attrs by name, channels by (name, output)."""

    def __init__(self, radio, label, attrs=None):
        self.radio, self.label = radio, label
        self.attrs = {n: Attr(self, n, v) for n, v in (attrs or {}).items()}
        self.chans = {}
        self.enabled = False
        self.kernel_buffers = None

    def find_channel(self, name, output=False):
        return self.chans.get((name, output))

    def set_kernel_buffers_count(self, n):
        self.kernel_buffers = n


class FakeRadio:
    """An AD936x context. `chip` is the driver personality; `available`
    False removes frequency_available (firmware older than 2017)."""

    def __init__(self, chip="ad9363a", available=True, rx_avail=None, tx_avail=None,
                 hw_model="Analog Devices PlutoSDR Rev.B (Z7010-AD9363)", phy_attr=True, rx_samples=None,
                 fw=None, packs8=False, fir=None, rate=None, path_rates=True, unpacked_first=0,
                 unpacks8=False, loopback="0", swaps8=False):
        # packs8: the bitstream packs int8 I/Q into voltage0 when voltage1 is
        # disabled (Tezuka's 8-bit receive). Without it, voltage0 alone is
        # what the stock unpacker gives: 16-bit I, and no Q at all.
        self.packs8 = packs8
        # unpacks8: the same on the transmit side - voltage0 alone carries
        # int8 I in byte 0 and Q in byte 1, put in the top 8 of the DAC's 12
        # bits. No Tezuka bitstream does this (docs/atv-research.md 18); the
        # default is what the nano does: the stock unpacker, which takes the
        # word as one 16-bit I sample and gives the DAC no Q (13, measured in
        # loopback). swaps8 is a bitstream that unpacks with the bytes the
        # other way round. loopback is the ad9361-phy debug attribute (None:
        # the radio has none); while it reads "1" the receive buffers carry
        # what the DAC was given, as the AD9361's digital loopback does.
        self.packs8, self.unpacks8, self.swaps8 = packs8, unpacks8, swaps8
        self.dac = []                 # what the DAC was given at each push: complex, 12-bit counts
        self.pushes = []              # (TX LO powerdown, attenuation, loopback) at each push
        # unpacked_first: that many 8-bit checks come back as plain 16-bit
        # words before the packing answers - what the nano did at two opens
        # on 5 October 2026 (docs/atv-research.md 17a).
        self.unpacked_first = unpacked_first
        # (label, attribute) -> [value, writes]: the attribute reads `value`
        # whatever is written to it, for the next `writes` writes (inf: for
        # good) - something else holding the radio, as Tezuka's own control
        # script can.
        self.stuck = {}
        self.rxbufs = []
        self.chip = chip
        self.lo, self.hi, self.bw_max = pluto.CHIPS[chip]
        self.log = []
        self.rx_samples = rx_samples
        lo_attrs = {"frequency": "0", "powerdown": "0"}
        # fir: "1"/"0" gives the phy sysfs in_out_voltage_filter_fir_en, a
        # FIR loaded and on or off; None leaves it out. libiio presents that
        # file as input channel "out", attribute "voltage_filter_fir_en" -
        # read from a real LibreSDR on 5 October 2026, after a fake that had
        # it as a device attribute let a driver pass that bypassed nothing on
        # the radio. rate is what the radio was left at (a DATV program
        # leaves 666 kS/s, which only the FIR's x4 makes possible).
        self.phy = Node(self, "phy", {"filter_fir_config": "FIR Rx: 128,4 Tx: 128,4"} if fir else {})
        self.phy.debug_attrs = {} if loopback is None else {"loopback": Attr(self.phy, "loopback", loopback)}
        if path_rates:
            for path in ("rx", "tx"):
                self.phy.attrs[f"{path}_path_rates"] = PathRates(self.phy, f"{path}_path_rates", path)
        self.fir_ch = None
        if fir is not None:
            self.fir_ch = Node(self, "fir", {"voltage_filter_fir_en": fir})
        self.rate = rate
        self.rx_lo = Node(self, "rx_lo", dict(lo_attrs))
        self.tx_lo = Node(self, "tx_lo", dict(lo_attrs))
        if available:
            self.rx_lo.attrs["frequency_available"] = Attr(self.rx_lo, "frequency_available",
                                                           rx_avail or f"[{self.lo:.0f} 1 {self.hi:.0f}]")
            self.tx_lo.attrs["frequency_available"] = Attr(self.tx_lo, "frequency_available",
                                                           tx_avail or f"[{self.lo:.0f} 1 {self.hi:.0f}]")
        names = ["sampling_frequency", "rf_bandwidth", "rf_port_select", "gain_control_mode", "hardwaregain",
                 "quadrature_tracking_en", "rf_dc_offset_tracking_en", "bb_dc_offset_tracking_en"]
        self.rx_ch = Node(self, "rx", {n: "" for n in names})
        self.tx_ch = Node(self, "tx", {n: "" for n in ("hardwaregain", "rf_bandwidth", "rf_port_select")})
        self.phy.chans = {("altvoltage0", True): self.rx_lo, ("altvoltage1", True): self.tx_lo,
                          ("voltage0", False): self.rx_ch, ("voltage0", True): self.tx_ch}
        if self.fir_ch is not None:
            self.phy.chans[("out", False)] = self.fir_ch
        self.rxdev = Node(self, "rxdev")
        self.txdev = Node(self, "txdev")
        for i in (0, 1):
            self.rxdev.chans[(f"voltage{i}", False)] = Node(self, f"rxdev{i}")
            self.txdev.chans[(f"voltage{i}", True)] = Node(self, f"txdev{i}")
        self.attrs = {"hw_model": hw_model, "hw_serial": PLUTO_SERIAL}
        if fw:
            self.attrs["fw_version"] = fw
        if phy_attr:
            self.attrs["ad9361-phy,model"] = chip
        self.txbufs = []

    def check(self, node, name, v):
        if name == "frequency":
            if not (self.lo <= float(v) <= self.hi):
                raise OSError(errno.EINVAL, "Invalid argument")
        elif name == "sampling_frequency":
            # ad9361_validate_trx_clock_chain: below 2.083 MS/s only with the FIR.
            if not ((pluto.NO_FIR_MIN if not self.fir_on() else 520_833) <= int(v) <= 61_440_000):
                raise OSError(errno.EINVAL, "Invalid argument")
            self.rate = int(v)
        elif name == "voltage_filter_fir_en":
            if v == "0" and self.rate is not None and self.rate < pluto.NO_FIR_MIN:
                raise OSError(errno.EINVAL, "Invalid argument")
        elif name == "rf_bandwidth":
            v = str(int(min(float(v), self.bw_max)))
        return v

    def wrote(self, label, name):
        stuck = self.stuck.get((label, name))
        if stuck is not None:
            if stuck[1] > 0:
                stuck[1] -= 1
            else:
                del self.stuck[(label, name)]

    def stick(self, label, name, value, writes=float("inf")):
        """From now on `label`.`name` reads `value`, through the next
        `writes` writes; the one after that takes."""
        self.stuck[(label, name)] = [str(value), writes]

    def move(self, label, name, value):
        """Something other than the program changes an attribute: no write
        through the driver, nothing in the log - the radio just holds
        another value, as the nano did 17 s into a receive run."""
        node = {"rx": self.rx_ch, "tx": self.tx_ch, "rx_lo": self.rx_lo, "tx_lo": self.tx_lo}[label]
        node.attrs[name]._v = str(value)
        if name == "sampling_frequency":
            self.rate = int(value)

    def looped(self):
        a = self.phy.debug_attrs.get("loopback")
        return a is not None and a.value == "1"

    def fir_on(self):
        return self.fir_ch is not None and self.fir_ch.attrs["voltage_filter_fir_en"].value == "1"

    def written(self, label, name):
        """Every value written to one attribute, in order."""
        return [v for lab, n, v in self.log if lab == label and n == name]


class FakeContext:
    def __init__(self, radio):
        self.radio = radio
        self.attrs = radio.attrs

    def find_device(self, name):
        return {"ad9361-phy": self.radio.phy, "cf-ad9361-lpc": self.radio.rxdev,
                "cf-ad9361-dds-core-lpc": self.radio.txdev}.get(name)

    def set_timeout(self, ms):
        pass


class FakeRxBuf:
    """Delivers IQ cut from `samples`, one block per refill, in the layout
    the enabled channels ask for: both - interleaved 12-bit in int16; only
    voltage0 - packed int8 I/Q (the top 8 bits) if the radio packs, else the
    16-bit I samples alone."""

    def __init__(self, samples, n, layout="iq16"):
        self.samples, self.n, self.pos, self.raw = samples, n, 0, b""
        self.layout = layout
        self.refills = 0

    def refill(self):
        self.refills += 1
        if self.samples is None:
            blk = np.zeros(self.n, np.complex64)
        else:
            blk = np.resize(self.samples[self.pos:], self.n) if self.pos < len(self.samples) else np.zeros(self.n)
            self.pos += self.n
        i = np.clip(np.round(blk.real * 2048), -2048, 2047).astype(np.int16)
        q = np.clip(np.round(blk.imag * 2048), -2048, 2047).astype(np.int16)
        if self.layout == "i16":
            self.raw = i.tobytes()
            return
        if self.layout == "packed8":
            s8 = np.empty(2 * self.n, np.int8)
            s8[0::2], s8[1::2] = i >> 4, q >> 4
            self.raw = s8.tobytes()
            return
        s = np.empty(2 * self.n, np.int16)
        s[0::2], s[1::2] = i, q
        self.raw = s.tobytes()

    def read(self):
        return self.raw


class FakeTxBuf:
    """Takes whole buffers in the layout the enabled channels make - two
    bytes a sample per channel - and works out what the DAC is given, as the
    bitstream's unpacker would (FakeRadio's unpacks8)."""

    def __init__(self, radio, n, cyclic=False):
        self.radio, self.n, self.cur, self.pushed, self.raw = radio, n, b"", [], []
        self.cyclic = cyclic
        self.both = radio.txdev.chans[("voltage1", True)].enabled
        self.bps = 4 if self.both else 2

    def write(self, b):
        assert len(b) == self.bps * self.n, "a TX buffer write must fill the buffer exactly"
        self.cur = bytes(b)

    def push(self):
        radio = self.radio
        s = np.frombuffer(self.cur, np.int16)
        self.pushed.append(s.copy())
        self.raw.append(self.cur)
        if self.both:
            i, q = s[0::2] >> 4, s[1::2] >> 4
        elif radio.unpacks8:
            b = np.frombuffer(self.cur, np.int8).astype(np.int16)
            i, q = (b[1::2], b[0::2]) if radio.swaps8 else (b[0::2], b[1::2])
            i, q = i << 4, q << 4
        else:
            i, q = s >> 4, np.zeros_like(s)
        radio.dac.append((i + 1j * q).astype(np.complex64))
        radio.pushes.append((radio.tx_lo.attrs["powerdown"].value, radio.tx_ch.attrs["hardwaregain"].value,
                             radio.looped()))
        # What the transmitter's gain was when this block went out.
        radio.log.append(("txbuf", "push", radio.tx_ch.attrs["hardwaregain"].value))


class FakeIIO:
    """Stands in for python3-libiio's `iio`: uri -> FakeRadio for
    Context(); anything unmapped raises as a dark address does."""

    def __init__(self, radios=None, scan=None, scan_error=None, contexts=None):
        self.radios = radios or {}
        self._scan, self._scan_error = scan or {}, scan_error
        self._contexts = contexts or {}

    def scan_contexts(self):
        if self._scan_error is not None:
            raise self._scan_error
        return dict(self._scan)

    def Context(self, uri):
        if uri in self.radios:
            return FakeContext(self.radios[uri])
        c = self._contexts.get(uri, OSError(errno.ECONNREFUSED, f"no such context: {uri}"))
        if isinstance(c, BaseException):
            raise c
        return c

    def Buffer(self, dev, n, cyclic):
        radio = dev.radio
        # Only the 8-bit transmit check, in loopback, may repeat a buffer:
        # on air a cyclic buffer would repeat one field for ever.
        assert not cyclic or (dev is radio.txdev and radio.looped()), "ATV streams: no cyclic buffers"
        if dev is radio.rxdev:
            both = radio.rxdev.chans[("voltage1", False)].enabled
            layout = "iq16" if both else ("packed8" if radio.packs8 else "i16")
            if layout == "packed8" and radio.unpacked_first > 0:
                radio.unpacked_first -= 1
                layout = "i16"
            samples = radio.rx_samples
            if radio.looped():
                # The transmit side's last buffer, repeating (cyclic), or
                # nothing if none was pushed.
                looped = radio.txbufs[-1] if radio.txbufs and radio.txbufs[-1].pushed else None
                samples = None if looped is None else np.tile(radio.dac[-1] / 2048, -(-4 * n // looped.n))
            buf = FakeRxBuf(samples, n, layout)
            radio.rxbufs.append(buf)
            return buf
        buf = FakeTxBuf(radio, n, cyclic)
        radio.txbufs.append(buf)
        return buf


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def opened(radio=None, uri="ip:192.168.2.1", fs=10 * MHZ, usb_net=(), cfg=None, sys_usb=None, usb_scan=None):
    """`usb_scan`: what libiio's USB scan finds, {uri: description}."""
    radio = radio or FakeRadio()
    fake = FakeIIO(radios={uri: radio})
    with mock.patch.dict(sys.modules, {"iio": fake}), \
         mock.patch.object(pluto, "_usb_network_addresses", return_value=list(usb_net)), \
         mock.patch.object(pluto, "_backend_scan", side_effect=lambda _m, b: dict(usb_scan or {}) if b == b"usb" else {}), \
         mock.patch.object(pluto, "SYS_USB_DEVICES", sys_usb or "/nonexistent"):
        d = pluto.PlutoDevice(uri, fs, cfg or {})
        d.open()
    d.clock = Clock()
    d.threaded_rx = False          # one refill per read, on the test's clock
    return d, radio


class TuningRange(unittest.TestCase):
    def test_ad9363_frequency_available(self):
        d, _ = opened(FakeRadio("ad9363a", rx_avail=AD9363_RX, tx_avail=AD9363_TX))
        self.assertEqual(d.freq_range(), (325e6, 3800e6))

    def test_ad9364_is_the_intersection_of_rx_and_tx(self):
        # The TX LO reaches 46.875 MHz, the RX LO only 70 MHz: a band must be
        # tunable by both.
        d, _ = opened(FakeRadio("ad9364", rx_avail=AD9364_RX, tx_avail=AD9364_TX))
        self.assertEqual(d.freq_range(), (70e6, 6000e6))

    def test_frequency_available_wins_over_the_model_attribute(self):
        d, _ = opened(FakeRadio("ad9364", rx_avail=AD9363_RX, tx_avail=AD9363_TX))
        self.assertEqual(d.freq_range(), (325e6, 3800e6))

    def test_falls_back_to_the_phy_model(self):
        for chip, rng in (("ad9363a", (325e6, 3800e6)), ("ad9364", (70e6, 6000e6)), ("ad9361", (70e6, 6000e6))):
            with self.subTest(chip=chip):
                d, _ = opened(FakeRadio(chip, available=False))
                self.assertEqual(d.freq_range(), rng)

    def test_unknown_is_none_even_when_hw_model_names_a_chip(self):
        # A Pluto nano's hw_model says AD9361; that is the board, not what
        # the driver will tune.
        d, _ = opened(FakeRadio(available=False, phy_attr=False, hw_model="Nano PlutoSDR (Z7010-AD9361)"))
        self.assertIsNone(d.freq_range())
        self.assertIsNone(d.status()["range"])

    def test_tuning_outside_the_range_names_the_ad9364_remedy(self):
        d, radio = opened(FakeRadio("ad9363a"))
        with self.assertRaises(DeviceError) as cm:
            d.set_rx_lo(5665 * MHZ)
        self.assertIn("ad9364", str(cm.exception))
        self.assertIn("325-3800 MHz", str(cm.exception))
        with self.assertRaises(DeviceError):
            d.set_tx_lo(5665 * MHZ)
        self.assertEqual(radio.written("rx_lo", "frequency"), [])     # refused before the radio was asked
        d.set_rx_lo(1255 * MHZ)
        self.assertEqual(radio.written("rx_lo", "frequency"), ["1255000000"])
        self.assertEqual(d.status()["rxLo"], 1255 * MHZ)

    def test_an_ad9364_tunes_6cm(self):
        d, radio = opened(FakeRadio("ad9364"))
        d.set_tx_lo(5665 * MHZ)
        self.assertEqual(radio.written("tx_lo", "frequency"), ["5665000000"])

    def test_with_no_range_known_the_radios_own_refusal_still_arrives_as_a_sentence(self):
        d, _ = opened(FakeRadio("ad9363a", available=False, phy_attr=False))
        with self.assertRaises(DeviceError) as cm:
            d.set_rx_lo(5665 * MHZ)
        self.assertIn("frequency", str(cm.exception))


class RateAndBandwidth(unittest.TestCase):
    def test_rate_is_set_directly(self):
        d, radio = opened(fs=10 * MHZ)
        self.assertEqual(radio.written("rx", "sampling_frequency"), ["10000000"])
        self.assertEqual(radio.written("rx", "rf_bandwidth"), ["10000000"])
        self.assertEqual(radio.written("tx", "rf_bandwidth"), ["10000000"])

    def test_rf_bandwidth_is_clamped_to_20_mhz_on_an_ad9363(self):
        d, radio = opened(FakeRadio("ad9363a"), fs=25 * MHZ)
        self.assertEqual(radio.written("rx", "rf_bandwidth"), ["20000000"])
        self.assertEqual(d.status()["rfBandwidth"], 20e6)

    def test_an_ad9364_takes_the_full_rate_as_bandwidth(self):
        d, radio = opened(FakeRadio("ad9364"), fs=25 * MHZ)
        self.assertEqual(radio.written("rx", "rf_bandwidth"), ["25000000"])

    def test_a_refused_rate_is_a_sentence(self):
        with self.assertRaises(DeviceError) as cm:
            opened(fs=70 * MHZ)
        self.assertIn("refused 70 MS/s", str(cm.exception))

    def test_open_leaves_the_transmitter_quiet(self):
        d, radio = opened()
        self.assertEqual(radio.tx_ch.attrs["hardwaregain"].value, "-89.75")
        self.assertEqual(radio.tx_lo.attrs["powerdown"].value, "1")
        self.assertFalse(d.status()["tx"])


class ReadBack(unittest.TestCase):
    """What was set is read back, because on 5 October 2026 the nano twice
    held 2.330997 MS/s and an RX LO of 1254.666998 MHz while the program
    showed the 10 MS/s and 1247 MHz it had set (docs/atv-research.md 17a)."""

    def test_a_rate_that_holds_when_set_again_is_noted_and_runs(self):
        radio = FakeRadio("ad9361")
        radio.stick("rx", "sampling_frequency", 2_330_997, writes=1)
        d, _ = opened(radio, fs=10 * MHZ)
        self.assertEqual(radio.written("rx", "sampling_frequency"), ["10000000", "10000000"])
        self.assertEqual(radio.rx_ch.attrs["sampling_frequency"].value, "10000000")
        self.assertIn("The radio's sample rate read 2.330997 MS/s after it was set to 10 MS/s; set again, it held",
                      d.status()["notes"])

    def test_a_rate_the_radio_will_not_hold_is_refused_with_both_figures(self):
        radio = FakeRadio("ad9361")
        radio.stick("rx", "sampling_frequency", 2_330_997)
        with self.assertRaises(DeviceError) as cm:
            opened(radio, fs=10 * MHZ)
        msg = str(cm.exception)
        self.assertIn("will not hold its sample rate: set to 10 MS/s, it reads 2.330997 MS/s", msg)
        self.assertIn("Tezuka", msg)

    def test_a_read_back_within_the_synthesisers_step_is_what_was_set(self):
        # The driver reports the frequency its synthesiser made: 2 Hz off on
        # the nano. That is not a move, and is not set twice.
        d, radio = opened(FakeRadio("ad9361"))
        before = d.status()["notes"]
        radio.stick("rx_lo", "frequency", 1_247_000_002)
        d.set_rx_lo(1247 * MHZ)
        self.assertEqual(radio.written("rx_lo", "frequency"), ["1247000000"])
        self.assertEqual(d.status()["notes"], before)
        self.assertEqual(d.status()["rxLo"], 1247 * MHZ)

    def test_an_lo_that_holds_when_set_again_is_noted(self):
        d, radio = opened(FakeRadio("ad9361"))
        radio.stick("rx_lo", "frequency", 1_254_666_998, writes=1)
        d.set_rx_lo(1247 * MHZ)
        self.assertEqual(radio.rx_lo.attrs["frequency"].value, "1247000000")
        self.assertEqual(d.status()["rxLo"], 1247 * MHZ)
        self.assertTrue(any("receive LO read 1254.666998 MHz" in n for n in d.status()["notes"]))

    def test_an_lo_the_radio_will_not_hold_is_refused_and_not_reported_as_set(self):
        d, radio = opened(FakeRadio("ad9361"))
        d.set_rx_lo(1251 * MHZ)
        radio.stick("rx_lo", "frequency", 1_254_666_998)
        with self.assertRaises(DeviceError) as cm:
            d.set_rx_lo(1247 * MHZ)
        self.assertIn("will not hold its receive LO: set to 1247 MHz, it reads 1254.666998 MHz", str(cm.exception))
        self.assertEqual(d.status()["rxLo"], 1251 * MHZ)
        # Transmit is refused the same way, which the engine takes as no key-up.
        radio.stick("tx_lo", "frequency", 1_254_666_998)
        with self.assertRaises(DeviceError):
            d.set_tx_lo(1247 * MHZ)
        self.assertIsNone(d.status()["txLo"])

    def test_a_transmitter_that_will_not_take_full_attenuation_is_refused(self):
        radio = FakeRadio("ad9361")
        radio.stick("tx", "hardwaregain", "-10.000000 dB")
        with self.assertRaises(DeviceError) as cm:
            opened(radio)
        self.assertIn("transmit attenuation: set to -89.75 dB, it reads -10 dB", str(cm.exception))

    def test_bandwidth_and_manual_gain_are_read_back(self):
        radio = FakeRadio("ad9361")
        radio.stick("rx", "rf_bandwidth", 5_000_000, writes=1)
        radio.stick("rx", "hardwaregain", "30.000000 dB", writes=1)
        d, _ = opened(radio, cfg={"rxGainMode": "manual", "rxGain": 40.0})
        notes = " ".join(d.status()["notes"])
        self.assertIn("receive RF bandwidth read 5 MHz after it was set to 10 MHz", notes)
        self.assertIn("receive gain read 30 dB after it was set to 40 dB", notes)
        self.assertEqual(radio.rx_ch.attrs["hardwaregain"].value, "40")

    def test_a_gain_mode_the_radio_will_not_hold_is_refused(self):
        radio = FakeRadio("ad9361")
        radio.stick("rx", "gain_control_mode", "manual")
        with self.assertRaises(DeviceError) as cm:
            opened(radio)
        self.assertIn("receive gain mode: set to slow_attack, it reads manual", str(cm.exception))


class Watch(unittest.TestCase):
    """watch(): what the radio holds now, against what was set - the nano
    moved under a receive run 17 s in (docs/atv-research.md 17a)."""

    def tuned(self, **kw):
        d, radio = opened(FakeRadio("ad9361"), fs=10 * MHZ, **kw)
        d.set_rx_lo(1247 * MHZ)
        return d, radio

    def test_a_radio_that_holds_what_was_set_is_left_alone(self):
        d, radio = self.tuned()
        n = len(radio.log)
        self.assertEqual(d.watch(), [])
        self.assertEqual(len(radio.log), n)                       # read, never written
        self.assertEqual(d.status()["moved"], [])

    def test_a_rate_and_lo_moved_underneath_are_set_back_and_said(self):
        d, radio = self.tuned()
        radio.move("rx", "sampling_frequency", 2_330_997)
        radio.move("rx", "rf_bandwidth", 2_000_000)
        radio.move("rx_lo", "frequency", 1_254_666_998)
        moves = d.watch()
        self.assertEqual([(m["what"], m["from"], m["to"], m["held"]) for m in moves],
                         [("sampleRate", 10e6, 2_330_997, True), ("rxLo", 1247e6, 1_254_666_998, True)])
        self.assertEqual(moves[0]["text"], "The radio's sample rate moved from 10 MS/s to 2.330997 MS/s by itself; "
                                           "set back, it held")
        self.assertEqual(radio.rx_ch.attrs["sampling_frequency"].value, "10000000")
        self.assertEqual(radio.rx_ch.attrs["rf_bandwidth"].value, "10000000")    # the filter came back with it
        self.assertEqual(radio.rx_lo.attrs["frequency"].value, "1247000000")
        st = d.status()
        self.assertEqual((st["moves"], [m["what"] for m in st["moved"]]), (2, ["sampleRate", "rxLo"]))
        self.assertEqual(st["rxLo"], 1247 * MHZ)
        self.assertEqual(d.watch(), [])                            # and now it holds

    def test_a_value_that_will_not_go_back_says_so(self):
        d, radio = self.tuned()
        radio.stick("rx", "sampling_frequency", 2_330_997)
        (m,) = d.watch()
        self.assertFalse(m["held"])
        self.assertIn("moved from 10 MS/s to 2.330997 MS/s by itself and will not go back", m["text"])
        self.assertEqual(radio.written("rx", "sampling_frequency")[-1], "10000000")      # it was tried once

    def test_a_moved_tx_lo_while_keyed_is_reported_not_set_back(self):
        d, radio = self.tuned()
        d.set_tx_lo(1251 * MHZ)
        d.tx_start()
        writes = len(radio.written("tx_lo", "frequency"))
        radio.move("tx_lo", "frequency", 1_254_666_998)
        (m,) = d.watch()
        self.assertEqual((m["what"], m["from"], m["to"], m["held"]), ("txLo", 1251e6, 1_254_666_998, None))
        self.assertIn("transmitting: off frequency", m["text"])
        self.assertEqual(len(radio.written("tx_lo", "frequency")), writes)

    def test_the_tx_lo_is_not_watched_unkeyed(self):
        # Powered down and attenuated, it sends nothing wherever it sits;
        # the engine's set_tx_lo before every key-up reads it back anyway.
        d, radio = self.tuned()
        d.set_tx_lo(1251 * MHZ)
        radio.move("tx_lo", "frequency", 1_254_666_998)
        self.assertEqual(d.watch(), [])

    def test_a_closed_radio_has_nothing_to_watch(self):
        d, radio = self.tuned()
        d.close()
        self.assertEqual(d.watch(), [])


class OwnResponse(unittest.TestCase):
    """The radio's fixed filters, which the modems undo
    (ohsatv/devices/ad936x.py; docs/atv-research.md 17)."""

    def test_at_12_5_msps_the_response_is_the_one_seen_on_air(self):
        d, radio = opened(FakeRadio("ad9361"), fs=12.5 * MHZ)
        rx, tx = d.rx_response, d.tx_response
        self.assertEqual((rx.stages, tx.stages), ((2, 2, 2), (2, 2, 2)))
        self.assertEqual((rx.rf_bw, tx.rf_bw), (12.5e6, 12.5e6))
        # The 5.5 MHz sound subcarrier and PAL chroma, transmitter and
        # receiver together: the 7.8 and 2.3 dB the 5 October burst lost
        # (within the tolerance of a radio measured on air).
        db = lambda f: 20 * np.log10(float(rx(f) * tx(f)))
        self.assertAlmostEqual(db(5.5e6), -7.8, delta=0.1)
        self.assertAlmostEqual(db(-5.5e6), -7.8, delta=0.1)
        self.assertAlmostEqual(db(4.43e6), -2.3, delta=0.1)
        self.assertGreater(db(1e6), -0.05)
        self.assertIn("12.5 MS/s", d.status()["rxCorrection"])

    def test_a_narrower_rf_bandwidth_on_an_ad9363_is_part_of_it(self):
        # 25 MS/s on an AD9363: the driver holds rf_bandwidth at 20 MHz, so the
        # analogue filter is narrower than the band and the response says so.
        d, _ = opened(FakeRadio("ad9363a"), fs=25 * MHZ)
        wide, _ = opened(FakeRadio("ad9364"), fs=25 * MHZ)
        f = 11e6
        self.assertEqual(d.rx_response.rf_bw, 20e6)
        self.assertLess(float(d.rx_response(f)), float(wide.rx_response(f)))

    def test_a_fir_that_could_not_be_bypassed_leaves_the_response_unknown(self):
        radio = FakeRadio("ad9361", fir="1")
        orig = radio.check

        def refuse_fir_off(node, name, v):
            if name == "voltage_filter_fir_en" and v == "0":
                raise OSError(errno.EBUSY, "Device or resource busy")
            return orig(node, name, v)
        radio.check = refuse_fir_off
        d, _ = opened(radio, fs=12.5 * MHZ)
        self.assertIsNone(d.rx_response)
        self.assertIsNone(d.tx_response)
        self.assertIsNone(d.status()["rxCorrection"])

    def test_a_radio_that_does_not_report_its_path_rates_is_taken_as_nominal(self):
        d, _ = opened(FakeRadio("ad9361", path_rates=False), fs=12.5 * MHZ)
        self.assertEqual(d.rx_response.stages, (2, 2, 2))
        self.assertIsNone(d.status()["rxPathRates"])


class LeftoverFir(unittest.TestCase):
    """On 5 October 2026 the LibreSDR still held pluto_dvb's x4 RRC FIR from
    a DATV run, and FM-ATV went out with no chroma and no sound subcarrier
    (docs/atv-research.md 15)."""

    def test_a_fir_left_by_a_datv_program_is_bypassed_after_the_rate_is_set(self):
        radio = FakeRadio("ad9364", fir="1", rate=666_000)
        d, radio = opened(radio, uri="ip:198.51.100.74", fs=12.5 * MHZ)
        # The rate first: at 666 kS/s the FIR cannot be turned off.
        order = [(lab, n) for lab, n, _ in radio.log if n in ("sampling_frequency", "voltage_filter_fir_en")]
        self.assertEqual(order[:2], [("rx", "sampling_frequency"), ("fir", "voltage_filter_fir_en")])
        self.assertFalse(radio.fir_on())
        self.assertEqual(radio.rate, 12_500_000)
        st = d.status()
        self.assertTrue(st["firBypassed"])
        self.assertTrue(any("FIR" in n for n in st["notes"]))

    def test_close_turns_the_other_programs_fir_back_on_with_the_transmitter_quiet(self):
        d, radio = opened(FakeRadio("ad9364", fir="1", rate=666_000), uri="ip:198.51.100.74", fs=12.5 * MHZ)
        d.set_tx_gain(-20)
        d.tx_start()
        d.close()
        self.assertTrue(radio.fir_on())
        # The FIR came back only after the transmitter was attenuated and its LO down.
        fir_at = max(i for i, (lab, n, v) in enumerate(radio.log) if n == "voltage_filter_fir_en")
        gain_at = max(i for i, (lab, n, v) in enumerate(radio.log) if lab == "tx" and n == "hardwaregain")
        lo_at = max(i for i, (lab, n, v) in enumerate(radio.log) if lab == "tx_lo" and n == "powerdown")
        self.assertLess(gain_at, fir_at)
        self.assertLess(lo_at, fir_at)
        self.assertEqual(radio.tx_ch.attrs["hardwaregain"].value, "-89.75")

    def test_a_radio_with_its_fir_off_or_without_one_is_left_alone(self):
        for fir in ("0", None):
            with self.subTest(fir=fir):
                d, radio = opened(FakeRadio(fir=fir), fs=10 * MHZ)
                self.assertEqual(radio.written("fir", "voltage_filter_fir_en"), [])
                self.assertFalse(d.status()["firBypassed"])
                d.close()
                self.assertEqual(radio.written("fir", "voltage_filter_fir_en"), [])


class Link(unittest.TestCase):
    def test_classification(self):
        cases = [
            ("usb:3.4.5", "", (), ("usb", 6.8)),
            ("ip:192.168.2.1", "", (), ("usb-net", 5.3)),
            ("ip:192.168.3.1", "", ("192.168.3.10",), ("usb-net", 5.3)),
            ("ip:198.51.100.50", "Analog Devices PlutoSDR Rev.C (Z7010-AD9363)", (), ("ethernet", None)),
            ("ip:198.51.100.50", "LibreSDR (Z7020-AD9361)", (), ("ethernet", 16.2)),
            ("ip:198.51.100.50", "Pluto+ (Z7010-AD9363)", (), ("ethernet", 28.0)),
        ]
        for uri, model, usb_net, want in cases:
            with self.subTest(uri=uri, model=model):
                self.assertEqual(pluto.classify_link(uri, model, list(usb_net)), want)

    def test_opened_device_reports_its_link(self):
        for uri, link, msps in (("usb:3.4.5", "usb", 6.8), ("ip:192.168.2.1", "usb-net", 5.3),
                                ("ip:198.51.100.50", "ethernet", None)):
            with self.subTest(uri=uri):
                d, _ = opened(uri=uri)
                self.assertEqual((d.link, d.link_msps), (link, msps))
                self.assertEqual((d.status()["link"], d.status()["linkMsps"]), (link, msps))

    def test_too_fast_for_the_link_is_said(self):
        d, _ = opened(uri="usb:3.4.5", fs=10 * MHZ)
        self.assertTrue(any("6.8 MS/s" in n for n in d.status()["notes"]))
        d, _ = opened(uri="usb:3.4.5", fs=6.25 * MHZ)
        self.assertEqual(d.status()["notes"], [])

    def test_pluto_plus_figure_is_called_a_reported_ceiling(self):
        d, _ = opened(FakeRadio(hw_model="Pluto+ (Z7010-AD9363)"), uri="ip:198.51.100.50")
        self.assertTrue(any("reported ceiling" in n for n in d.notes))

    def test_duplex_only_on_ethernet_with_room_for_both(self):
        libre = "LibreSDR (Z7020-AD9361)"
        # LibreSDR, measured: both directions held at 6.25 MS/s, not at 8.
        self.assertTrue(opened(FakeRadio(hw_model=libre), uri="ip:198.51.100.50", fs=6.25 * MHZ)[0].duplex())
        self.assertFalse(opened(FakeRadio(hw_model=libre), uri="ip:198.51.100.50", fs=8 * MHZ)[0].duplex())
        self.assertFalse(opened(uri="ip:198.51.100.50", fs=6.25 * MHZ)[0].duplex())     # unknown model
        self.assertFalse(opened(uri="usb:3.4.5", fs=2.5 * MHZ)[0].duplex())
        self.assertFalse(opened(uri="ip:192.168.2.1", fs=2.5 * MHZ)[0].duplex())


def fake_sys_usb(root, bus, dev, nets, serial=None):
    """A /sys/bus/usb/devices with one device, BUS DEV (USB serial `serial`),
    whose interfaces carry the network interfaces `nets`, {name: up}, and a
    second device that is not it with an interface up."""
    def device(name, b, d, ser=None):
        os.makedirs(os.path.join(root, name))
        for f, v in (("busnum", b), ("devnum", d)) + ((("serial", ser),) if ser else ()):
            with open(os.path.join(root, name, f), "w") as fh:
                fh.write(f"{v}\n")
    device("3-4", bus, dev, serial)
    for i, (ifname, up) in enumerate(nets.items()):
        os.makedirs(os.path.join(root, f"3-4:1.{i}", "net", ifname))
        with open(os.path.join(root, f"3-4:1.{i}", "net", ifname, "flags"), "w") as fh:
            fh.write("0x1003\n" if up else "0x1002\n")
    device("3-1", bus, dev + 1)
    os.makedirs(os.path.join(root, "3-1:1.0", "net", "enxother"))
    with open(os.path.join(root, "3-1:1.0", "net", "enxother", "flags"), "w") as fh:
        fh.write("0x1003\n")


@unittest.skipIf(sys.platform == "win32", "sysfs is Linux's (its names hold colons, which Windows forbids); "
                 "off Linux the lookups say 'not known', tested in test_windows_host")
class UsbNetwork(unittest.TestCase):
    """The nano's own USB network, up on the host, takes a fifth of what
    transmit can carry (research 18a): the driver finds it and says so."""

    def test_an_up_network_on_the_radios_own_usb_device_is_named(self):
        with tempfile.TemporaryDirectory() as root:
            fake_sys_usb(root, 3, 19, {"enx00e022121743": True})
            d, _ = opened(uri="usb:3.19.5", fs=5 * MHZ, sys_usb=root)
        self.assertEqual(d.status()["usbNetUp"], ["enx00e022121743"])
        note = [n for n in d.status()["notes"] if "USB network" in n]
        self.assertEqual(len(note), 1)
        self.assertIn("sudo ip link set enx00e022121743 down", note[0])

    def test_down_or_on_another_device_is_not(self):
        with tempfile.TemporaryDirectory() as root:
            fake_sys_usb(root, 3, 19, {"enx00e022121743": False})
            d, _ = opened(uri="usb:3.19.5", fs=5 * MHZ, sys_usb=root)
        self.assertEqual(d.status()["usbNetUp"], [])
        self.assertEqual(d.status()["notes"], [])
        with tempfile.TemporaryDirectory() as root:
            fake_sys_usb(root, 3, 7, {"enx00e022121743": True})      # another device is up, not the radio
            d, _ = opened(uri="usb:3.19.5", fs=5 * MHZ, sys_usb=root)
        self.assertEqual(d.status()["usbNetUp"], [])

    def test_only_asked_of_a_usb_link(self):
        with tempfile.TemporaryDirectory() as root:
            fake_sys_usb(root, 3, 19, {"enx00e022121743": True})
            d, _ = opened(uri="ip:192.168.2.1", fs=5 * MHZ, sys_usb=root)
        self.assertEqual(d.status()["usbNetUp"], [])

    def test_unreadable_sysfs_is_none(self):
        self.assertEqual(pluto.usb_net_up("usb:3.19.5", "/nonexistent"), [])
        self.assertEqual(pluto.usb_net_up("usb:", "/nonexistent"), [])

    def test_transmit_carries_6_25_msps_only_with_the_network_known_to_be_down(self):
        # 18a: 6.36 MS/s with it down (405 lines' sound needs 6.25), 4.38
        # with it up. Up, or not known (bare usb:, not in sysfs), the old
        # ceiling stands.
        cases = (({"enx00e022121743": False}, "usb:3.19.5", 6.36), ({}, "usb:3.19.5", 6.36),
                 ({"enx00e022121743": True}, "usb:3.19.5", pluto.USB_TX_MSPS),
                 ({"enx00e022121743": False}, "usb:3.7.5", pluto.USB_TX_MSPS))
        for nets, uri, cap in cases:
            with self.subTest(nets=nets, uri=uri), tempfile.TemporaryDirectory() as root:
                fake_sys_usb(root, 3, 19, nets)
                d, _ = opened(uri=uri, fs=5 * MHZ, sys_usb=root)
                self.assertEqual(d.tx_link_msps, cap)
        d, _ = opened(uri="usb:3.19.5", fs=5 * MHZ, sys_usb="/nonexistent")
        self.assertEqual(d.tx_link_msps, pluto.USB_TX_MSPS)

    PLUTO_USB = f"0456:b673 (Analog Devices Inc. PlutoSDR (ADALM-PLUTO)), serial={PLUTO_SERIAL}"

    def test_the_default_bare_usb_address_is_resolved_to_its_device(self):
        # The default plutoUri is usb:, which names no device: libiio's USB
        # scan says which it is, by serial when there are two Plutos.
        other = "0456:b673 (Analog Devices Inc. PlutoSDR (ADALM-PLUTO)), serial=1044730a1997000b0b00"
        for scan in ({"usb:3.19.5": self.PLUTO_USB}, {"usb:3.7.5": other, "usb:3.19.5": self.PLUTO_USB}):
            for nets, cap, up in (({"enx00e022121743": False}, 6.36, []),
                                  ({"enx00e022121743": True}, pluto.USB_TX_MSPS, ["enx00e022121743"])):
                with self.subTest(scan=list(scan), nets=nets), tempfile.TemporaryDirectory() as root:
                    fake_sys_usb(root, 3, 19, nets)
                    d, _ = opened(uri="usb:", fs=5 * MHZ, sys_usb=root, usb_scan=scan)
                    st = d.status()
                    self.assertEqual((st["usbAddr"], st["txLinkMsps"], st["usbNetUp"]), ("usb:3.19.5", cap, up))

    def test_without_the_scan_sysfs_finds_it_by_serial(self):
        with tempfile.TemporaryDirectory() as root:
            fake_sys_usb(root, 3, 19, {"enx00e022121743": False}, serial=PLUTO_SERIAL)
            d, _ = opened(uri="usb:", fs=5 * MHZ, sys_usb=root)
        self.assertEqual((d.status()["usbAddr"], d.tx_link_msps), ("usb:3.19", 6.36))

    def test_a_bare_address_that_cannot_be_placed_keeps_the_old_ceiling(self):
        # Two Plutos and neither is this one's serial; nothing in sysfs.
        scan = {"usb:3.7.5": "PlutoSDR, serial=aaaa", "usb:3.19.5": "PlutoSDR, serial=bbbb"}
        with tempfile.TemporaryDirectory() as root:
            fake_sys_usb(root, 3, 19, {"enx00e022121743": False})
            d, _ = opened(uri="usb:", fs=5 * MHZ, sys_usb=root, usb_scan=scan)
        self.assertEqual((d.status()["usbAddr"], d.tx_link_msps), (None, pluto.USB_TX_MSPS))

    def test_the_6_25_steering_follows_on_the_default_address(self):
        # End to end through the engine: a nano on the default usb: with its
        # USB network down is steered to send 405 lines at 6.25 MS/s with
        # their sound (profiles._usb_405).
        from ohsatv.audio import NullAudio
        from ohsatv.radio import Radio
        from test_radio import FAST
        with tempfile.TemporaryDirectory() as root:
            fake_sys_usb(root, 3, 19, {"enx00e022121743": False})
            radio = FakeRadio(hw_model="Analog Devices PlutoSDR Rev.C (Z7010-AD9363A)")
            fake = FakeIIO(radios={"usb:": radio})
            with mock.patch.dict(sys.modules, {"iio": fake}), \
                 mock.patch.object(pluto, "_usb_network_addresses", return_value=[]), \
                 mock.patch.object(pluto, "_backend_scan",
                                   side_effect=lambda _m, b: {"usb:3.19.5": self.PLUTO_USB} if b == b"usb" else {}), \
                 mock.patch.object(pluto, "SYS_USB_DEVICES", root):
                tmp = tmp_dir(self, "ohsatv-usbaddr-")
                r = Radio(dict(FAST, device="pluto", plutoUri="usb:", videoFormat="405"),
                          conf_path=f"{tmp}/c.json", audio=NullAudio(realtime=False), data_dir=f"{tmp}/data")
                self.addCleanup(r.stop)
                r.start()
                st = r.state()
        self.assertEqual(st["a405Rate"], 6_250_000, st["profile"])
        self.assertIn("405 lines at 6.25 MS/s with their sound", st["profile"])


class Receive(unittest.TestCase):
    def test_long_buffers_scaled_to_full_scale(self):
        # A quarter of a second, at most 1 M samples: measured on a real nano
        # over USB, longer buffers carry more (6.61 MS/s at 64 k, 7.08 at 1 M).
        fs = 2.5 * MHZ
        rng = np.random.default_rng(1)
        x = ((rng.integers(-2048, 2048, 900_000) + 1j * rng.integers(-2048, 2048, 900_000)) / 2048).astype(np.complex64)
        d, radio = opened(FakeRadio(rx_samples=x), fs=fs)
        self.assertEqual(d.rxbuf.n, 625_000)
        self.assertEqual(opened(fs=20 * MHZ)[0].rxbuf.n, 1 << 20)
        self.assertEqual(radio.rxdev.kernel_buffers, 4)
        y = np.concatenate([d.read(200_000) for _ in range(4)])
        self.assertEqual(y.dtype, np.complex64)
        np.testing.assert_allclose(y, x[:800_000], atol=1e-6)
        self.assertEqual(d.rxbuf.refills, 2)

    def test_the_reader_thread_keeps_order_and_stops(self):
        fs = 2.5 * MHZ
        x = (np.arange(3_000_000) % 2000 / 2048).astype(np.complex64)
        d, radio = opened(FakeRadio(rx_samples=x), fs=fs)
        d.threaded_rx = True
        y = np.concatenate([d.read(100_000) for _ in range(12)])
        np.testing.assert_allclose(y, x[:1_200_000], atol=1e-6)
        d.close()
        self.assertIsNone(d._rx_thread)

    def deliver(self, d, per_block_s, blocks):
        """`blocks` fields of samples, each arriving `per_block_s` after the
        last. Refills are the buffer's length (several fields), so the clock
        moves by that many fields' worth per refill."""
        fields = d.rxbuf.n / d.block
        for _ in range(max(1, round(blocks / fields))):
            d.clock.t += per_block_s * fields
            d.read(d.rxbuf.n)

    def test_a_link_that_cannot_keep_up_is_shown_short(self):
        d, _ = opened(fs=10 * MHZ)
        self.assertIsNone(d.status()["rxShort"])                 # nothing measured yet
        self.deliver(d, 0.030, 100)                               # a 20 ms field every 30 ms
        st = d.status()
        self.assertAlmostEqual(st["rxRate"], 10 * MHZ * 2 / 3, delta=1e3)
        self.assertTrue(st["rxShort"])

    def test_a_link_at_full_rate_is_not_short(self):
        d, _ = opened(fs=10 * MHZ)
        self.deliver(d, 0.020, 200)
        st = d.status()
        self.assertAlmostEqual(st["rxRate"], 10 * MHZ, delta=1e3)
        self.assertFalse(st["rxShort"])

    def test_the_measurement_forgets_old_seconds(self):
        d, _ = opened(fs=10 * MHZ)
        self.deliver(d, 0.030, 100)
        self.assertTrue(d.status()["rxShort"])
        self.deliver(d, 0.020, 300)                               # 6 s at full rate: the window has moved on
        self.assertFalse(d.status()["rxShort"])

    def test_a_pause_in_reading_restarts_the_measurement(self):
        # While keyed the engine stops reading; afterwards the kernel's four
        # stale buffers come back at once. That is not a fast link.
        d, _ = opened(fs=10 * MHZ)
        self.deliver(d, 0.020, 100)
        d.clock.t += 5.0
        self.deliver(d, 0.0, 4)
        self.assertIsNone(d.status()["rxRate"])


def noise(n, rms=0.05, seed=3):
    """Receiver noise as the ADC sees it (rms as a fraction of full scale):
    I and Q matched, as quadrature tracking keeps them."""
    rng = np.random.default_rng(seed)
    return (rng.normal(0, rms, n) + 1j * rng.normal(0, rms, n)).astype(np.complex64)


NANO = "Nano PlutoSDR (Z7010-AD9361)"
TEZUKA = "tezuka-v0.3.21"


class EightBitReceive(unittest.TestCase):
    """Tezuka's packed int8 receive (docs/atv-research.md 13). The 8-bit
    transmit check is off here, so the buffers and notes counted are
    receive's alone; EightBitTransmit has it on."""

    def nano(self, fs=10 * MHZ, packs8=True, fw=TEZUKA, uri="usb:3.8.5", samples=None, cfg=None, unpacked_first=0):
        x = noise(3_000_000) if samples is None else samples
        return opened(FakeRadio(chip="ad9361", hw_model=NANO, fw=fw, packs8=packs8, rx_samples=x,
                                unpacked_first=unpacked_first), uri=uri, fs=fs,
                      cfg=dict({"plutoTx8": False}, **(cfg or {}))) + (x,)

    def test_a_first_check_that_reads_16_bit_is_made_again_and_the_second_wins(self):
        # The nano, twice on 5 October 2026: plain 16-bit words at the first
        # check, packed at the next open.
        d, radio, x = self.nano(fs=12.5 * MHZ, unpacked_first=1)
        self.assertTrue(d.rx8_capable)
        self.assertEqual(d.rx_bits, 8)
        self.assertEqual(d.rxbuf.layout, "packed8")
        self.assertEqual(d.link_msps, pluto.USB_RX8_MSPS)
        st = d.status()
        self.assertEqual(st["rx8Checks"], 2)
        self.assertIn("read plain 16-bit samples at the first check and packed I/Q at the second", " ".join(st["notes"]))

    def test_two_checks_that_read_16_bit_fall_back_and_say_both_were_made(self):
        d, radio, x = self.nano(fs=12.5 * MHZ, unpacked_first=2)
        self.assertFalse(d.rx8_capable)
        self.assertEqual(d.rx_bits, 16)
        self.assertEqual(d.rxbuf.layout, "iq16")
        self.assertEqual(d.link_msps, pluto.USB_MSPS)
        self.assertEqual(d.status()["rx8Checks"], 2)
        self.assertIn("did not answer as packed I/Q, checked twice", " ".join(d.status()["notes"]))

    def test_a_first_check_that_answers_is_the_only_one(self):
        d, radio, _ = self.nano(fs=10 * MHZ)
        self.assertEqual(d.status()["rx8Checks"], 1)
        self.assertEqual(len(radio.rxbufs), 2)                  # one check, then the receive buffer

    def test_a_tezuka_nano_receives_8_bit_above_6_25_msps(self):
        d, radio, x = self.nano(fs=10 * MHZ)
        self.assertTrue(d.rx8_capable)
        self.assertEqual(d.rx_bits, 8)
        self.assertTrue(radio.rxdev.chans[("voltage0", False)].enabled)
        self.assertFalse(radio.rxdev.chans[("voltage1", False)].enabled)
        self.assertEqual(d.rxbuf.layout, "packed8")
        self.assertEqual(d.link_msps, pluto.USB_RX8_MSPS)
        self.assertEqual(d.status()["notes"], [])             # 10 MS/s is inside 15
        st = d.status()
        self.assertEqual((st["rxBits"], st["rx8"], st["fw"], st["linkMsps"]), (8, True, TEZUKA, 15.0))
        # Each fake buffer starts from the first sample; int8 is the top 8
        # of the 12 bits (an arithmetic shift), scaled so full scale is 1.
        y = d.read(500_000)
        ref = x[:500_000]
        want = (np.floor(np.round(ref.real * 2048) / 16) + 1j * np.floor(np.round(ref.imag * 2048) / 16)) / 128
        np.testing.assert_allclose(y, want.astype(np.complex64), atol=1e-6)

    def test_the_delivered_rate_counts_two_bytes_a_sample(self):
        d, _, _ = self.nano(fs=10 * MHZ)
        for _ in range(100):
            d.read(d.rx_buf_n)
            d.clock.t += d.rx_buf_n / (10 * MHZ)
        self.assertAlmostEqual(d.status()["rxRate"], 10 * MHZ, delta=1e3)
        self.assertFalse(d.status()["rxShort"])

    def test_at_6_25_msps_and_below_the_extra_bits_are_kept(self):
        d, radio, x = self.nano(fs=6.25 * MHZ)
        self.assertTrue(d.rx8_capable)                          # known, and reported for steering
        self.assertEqual(d.rx_bits, 16)
        self.assertTrue(radio.rxdev.chans[("voltage1", False)].enabled)
        self.assertEqual(d.rxbuf.layout, "iq16")
        np.testing.assert_allclose(d.read(100_000), np.round(x[:100_000] * 2048) / 2048, atol=1e-6)

    def test_tezuka_without_the_packing_falls_back_to_16_bit_and_says_so(self):
        d, radio, x = self.nano(fs=10 * MHZ, packs8=False)
        self.assertFalse(d.rx8_capable)
        self.assertEqual(d.rx_bits, 16)
        self.assertTrue(radio.rxdev.chans[("voltage1", False)].enabled)
        self.assertEqual(d.link_msps, pluto.USB_MSPS)
        notes = " ".join(d.status()["notes"])
        self.assertIn("did not answer as packed", notes)
        self.assertIn("more than this usb link", notes)
        np.testing.assert_allclose(d.read(100_000), np.round(x[:100_000] * 2048) / 2048, atol=1e-6)

    def test_silence_is_not_taken_for_packing(self):
        d, _, _ = self.nano(samples=np.zeros(3_000_000, np.complex64))
        self.assertFalse(d.rx8_capable)
        self.assertEqual(d.rx_bits, 16)

    def test_a_strong_signal_on_a_stock_bitstream_is_not_taken_for_packing(self):
        # Large I puts a few counts of sign extension in byte 1: still far
        # less spread than byte 0's.
        d, _, _ = self.nano(packs8=False, samples=noise(3_000_000, rms=0.4))
        self.assertFalse(d.rx8_capable)

    def test_other_firmware_ethernet_or_the_setting_off_are_not_probed(self):
        for kw in ({"fw": "v0.38"}, {"fw": None}, {"uri": "ip:192.168.2.1"}, {"cfg": {"plutoRx8": False}}):
            with self.subTest(**{k: str(v) for k, v in kw.items()}):
                d, radio, _ = self.nano(**kw)
                self.assertFalse(d.rx8_capable)
                self.assertEqual(d.rx_bits, 16)
                self.assertEqual(len(radio.rxbufs), 1)          # only the receive buffer itself

    def test_transmit_keeps_the_16_bit_usb_ceiling(self):
        d, _, _ = self.nano(fs=10 * MHZ)
        self.assertEqual(d.tx_link_msps, pluto.USB_TX_MSPS)
        self.assertEqual(d.status()["txLinkMsps"], pluto.USB_TX_MSPS)
        self.assertIsNone(opened(uri="ip:198.51.100.50")[0].tx_link_msps)


class EightBitTransmit(unittest.TestCase):
    """8-bit transmit, checked through the digital loopback at open
    (docs/atv-research.md 18). The fake's DAC sees what the bitstream's
    unpacker would make of the buffer; the nano's is the stock one."""

    def nano(self, fs=10 * MHZ, cfg=None, **radio_kw):
        radio = FakeRadio(chip="ad9361", hw_model=NANO, fw=TEZUKA, packs8=True, rx_samples=noise(3_000_000),
                          **radio_kw)
        return opened(radio, uri="usb:3.19.5", fs=fs, cfg=cfg)

    def assert_quiet_check(self, radio):
        # Every sample of the check went out with the TX LO down, at full
        # attenuation, in loopback - and loopback is back as it was.
        self.assertTrue(radio.pushes)
        for powerdown, gain, looped in radio.pushes:
            self.assertEqual((powerdown, float(gain), looped), ("1", pluto.TX_ATTEN_MAX, True))
        self.assertEqual(radio.written("phy", "loopback"), ["1", "0"])
        self.assertFalse(radio.looped())
        self.assertEqual(radio.tx_lo.attrs["powerdown"].value, "1")

    def test_the_nanos_stock_unpacker_is_found_out_and_transmit_stays_16_bit(self):
        d, radio = self.nano()
        self.assert_quiet_check(radio)
        st = d.status()
        self.assertEqual((st["tx8"], st["tx8Check"], st["txBits"]), (False, "real", 16))
        self.assertEqual(st["txLinkMsps"], pluto.USB_TX_MSPS)
        notes = " ".join(st["notes"])
        self.assertIn("as a real signal", notes)
        self.assertIn("transmitting 16-bit, up to 5.2 MS/s", notes)
        self.assertTrue(d.rx8_capable)                     # receive is untouched by it
        d.tx_start()
        self.assertTrue(radio.txdev.chans[("voltage1", True)].enabled)
        d.write(np.full(d.tx_buf_n, 0.5 - 0.25j, np.complex64))
        np.testing.assert_array_equal(radio.dac[-1][:3], [(16384 >> 4) - 1j * (8192 >> 4)] * 3)

    def test_a_bitstream_that_unpacks_int8_transmits_8_bit_above_the_16_bit_ceiling(self):
        d, radio = self.nano(unpacks8=True)
        self.assert_quiet_check(radio)
        st = d.status()
        self.assertEqual((st["tx8"], st["tx8Check"], st["txBits"]), (True, "packed", 8))
        self.assertEqual(d.tx_link_msps, pluto.USB_TX8_MSPS)
        self.assertEqual(st["notes"], [])
        d.set_tx_gain(-20.0)
        d.tx_start()
        self.assertEqual(radio.tx_lo.attrs["powerdown"].value, "0")
        self.assertFalse(radio.txdev.chans[("voltage1", True)].enabled)     # the pair rides voltage0
        rng = np.random.default_rng(5)
        x = (rng.uniform(-1, 1, d.tx_buf_n + 5) + 1j * rng.uniform(-1, 1, d.tx_buf_n + 5)).astype(np.complex64)
        x[0] = 1.5 - 1.5j                                  # out of range: clipped symmetrically, not wrapped
        d.write(x)
        self.assertEqual(len(radio.txbufs[-1].raw[-1]), 2 * d.tx_buf_n)   # two bytes a sample
        dac = radio.dac[-1]
        self.assertEqual(dac[0], 127 * 16 - 127j * 16)
        np.testing.assert_array_equal(dac[1:].real, np.round(x[1:d.tx_buf_n].real * 127) * 16)
        np.testing.assert_array_equal(dac[1:].imag, np.round(x[1:d.tx_buf_n].imag * 127) * 16)
        self.assertEqual(radio.tx_ch.attrs["hardwaregain"].value, "-20.0")  # only after samples flowed

    def test_late_pushes_count_two_bytes_a_sample(self):
        d, radio = self.nano(unpacks8=True)
        d.tx_start()
        blk = np.zeros(d.tx_buf_n, np.complex64)
        for _ in range(10):                                # paced at 10 MS/s: never late
            d.write(blk)
            d.clock.t += d.tx_buf_n / d.fs
        self.assertEqual(d.status()["txLate"], 0)
        d.clock.t += 0.2
        d.write(blk)
        self.assertEqual(d.status()["txLate"], 1)
        d.tx_stop()
        self.assertEqual(len(radio.txbufs[-1].raw[-1]), 2 * d.tx_buf_n)   # the closing zeros, 8-bit too
        self.assertFalse(radio.dac[-1].any())
        self.assertEqual(radio.tx_lo.attrs["powerdown"].value, "1")

    def test_a_static_loop_goes_out_8_bit_seamlessly(self):
        import threading
        d, radio = self.nano(unpacks8=True)
        d.tx_start()
        L = 1_600_000                                      # a 0.16 s loop at 10 MS/s; buffers are 1 M
        loop = (0.9 * np.exp(2j * np.pi * np.arange(L) / 997)).astype(np.complex64)
        stop = threading.Event()
        real_push = d._push

        def counting(data, count=True):
            real_push(data, count)
            if len(radio.txbufs[-1].raw) >= 4:
                stop.set()
        d._push = counting
        d.write_loop(loop, stop)
        out = np.concatenate(radio.dac[-4:])
        np.testing.assert_array_equal(out, to_dac8(np.tile(loop, 4)[: 4 * d.tx_buf_n]))

    def test_at_5_msps_the_extra_bits_are_kept(self):
        d, radio = self.nano(fs=5 * MHZ, unpacks8=True)
        self.assertTrue(d.tx8_capable)
        self.assertEqual(d.tx_bits, 16)
        d.tx_start()
        self.assertTrue(radio.txdev.chans[("voltage1", True)].enabled)

    def test_a_mirrored_unpacker_is_not_taken(self):
        d, radio = self.nano(unpacks8=True, swaps8=True)
        self.assert_quiet_check(radio)
        self.assertEqual((d.tx8_check, d.tx_bits), ("mirrored", 16))
        self.assertIn("mirrored", " ".join(d.notes))

    def test_a_loopback_that_brings_nothing_back_is_not_taken(self):
        real_buffer = FakeIIO.Buffer

        def deaf(self_, dev, n, cyclic):
            buf = real_buffer(self_, dev, n, cyclic)
            if dev is dev.radio.rxdev:
                buf.samples = None
            return buf
        with mock.patch.object(FakeIIO, "Buffer", deaf):
            d, _ = self.nano(unpacks8=True)
        self.assertEqual((d.tx8_check, d.tx_bits), ("silent", 16))

    def test_no_check_without_a_confirmed_quiet_transmitter(self):
        radio = FakeRadio(chip="ad9361", hw_model=NANO, fw=TEZUKA, unpacks8=True)
        radio.stick("tx_lo", "powerdown", "0")             # will not power down
        d, radio = opened(radio, uri="usb:3.19.5")
        self.assertEqual((d.tx8_check, d.tx_bits), ("not-quiet", 16))
        self.assertEqual(radio.txbufs, [])                 # nothing was sent
        self.assertEqual(radio.written("phy", "loopback"), [])

    def test_no_loopback_no_check(self):
        d, radio = self.nano(unpacks8=True, loopback=None)
        self.assertEqual((d.tx8_check, d.tx_bits), ("no-loopback", 16))
        self.assertEqual(radio.txbufs, [])

    def test_a_loopback_that_will_not_go_back_refuses_the_radio(self):
        radio = FakeRadio(chip="ad9361", hw_model=NANO, fw=TEZUKA, unpacks8=True)
        real_check = radio.check

        def keeps_loopback(node, name, v):
            return "1" if name == "loopback" else real_check(node, name, v)
        radio.check = keeps_loopback
        with self.assertRaises(DeviceError) as cm:
            opened(radio, uri="usb:3.19.5")
        self.assertIn("will not go back to 0", str(cm.exception))

    def test_other_firmware_ethernet_or_the_setting_off_are_not_checked(self):
        for uri, fw, cfg in (("usb:3.19.5", "v0.38", {}), ("ip:198.51.100.50", TEZUKA, {}),
                             ("usb:3.19.5", TEZUKA, {"plutoTx8": False})):
            with self.subTest(uri=uri, fw=fw, cfg=str(cfg)):
                radio = FakeRadio(chip="ad9361", hw_model=NANO, fw=fw, unpacks8=True)
                d, radio = opened(radio, uri=uri, cfg=cfg)
                self.assertIsNone(d.status()["tx8Check"])
                self.assertEqual(d.tx_bits, 16)
                self.assertEqual(radio.txbufs, [])
                self.assertEqual(radio.written("phy", "loopback"), [])


def to_dac8(x):
    """What an 8-bit unpacker gives the DAC for complex64 x: int8 rounded,
    clipped at +-127, in the top 8 of 12 bits."""
    i = np.clip(np.round(x.real * 127), -127, 127) * 16
    q = np.clip(np.round(x.imag * 127), -127, 127) * 16
    return (i + 1j * q).astype(np.complex64)


class EightBitQuantisation(unittest.TestCase):
    """What 8 bits cost the picture: the modulators' own test-card loops,
    quantised as the driver sends them."""

    def snr(self, fs, system, **params):
        from ohsatv import state, systems, testcard
        p = dict(state.defaults(("setting", "control")), **params)
        iq = systems.build_loop(fs, system, p, testcard.test_card("GM4BRI", "IO86"))
        d = pluto.PlutoDevice("usb:", fs)
        d.tx_bits = 8
        b = d.to_native(iq)
        self.assertEqual(b.dtype, np.int8)
        self.assertLessEqual(np.abs(b.astype(np.int16)).max(), 127)
        back = (b[0::2].astype(np.float64) + 1j * b[1::2]) / 127
        return 10 * np.log10(np.mean(np.abs(iq) ** 2) / np.mean(np.abs(back - iq) ** 2))

    def test_scaling_rounding_and_symmetric_clipping(self):
        d = pluto.PlutoDevice("usb:", 10e6)
        d.tx_bits = 8
        iq = np.array([0.5 + 0.25j, -1.0 - 1.0j, 1.5 - 2.0j, 0.004 + 0j], np.complex64)
        np.testing.assert_array_equal(d.to_native(iq), [64, 32, -127, -127, 127, -127, 1, 0])

    def test_am_vsb_colour_with_sound_at_10_msps_keeps_over_40_db(self):
        self.assertGreater(self.snr(10e6, "am"), 40)

    def test_fm_colour_with_sound_at_12_5_msps_keeps_over_45_db(self):
        self.assertGreater(self.snr(12.5e6, "fm", fmDeviation=1.2e6, fmVideoBw=4.9e6, fmSub=5.5e6), 45)


class FallingBehind(unittest.TestCase):
    """When the engine falls behind the radio, whole frames are skipped, so
    line and field timing carry on where they were (docs/atv-research.md
    13a: the old whole-buffer drops were timing jumps)."""

    def test_skips_whole_frames_down_to_a_tenth_of_a_second_and_counts_them(self):
        fs = 2.5 * MHZ
        k = np.arange(4_000_000)
        x = ((k % 2000) + 1j * ((k // 2000) % 2000)).astype(np.complex64) / 2048    # every sample unique
        d, radio = opened(FakeRadio(rx_samples=x), fs=fs)
        d.threaded_rx = True
        d._init_queue()
        d._rx_thread = object()          # the queue is filled by hand, not by a reader thread
        frame = int(fs * 0.04)

        def arrive(nbuf):
            for _ in range(nbuf):
                d.rxbuf.refill()
                raw = d.rxbuf.read()
                d._rx_q.append(raw)
                d._rx_qn += len(raw) // d._bps

        arrive(1)                                       # 0.25 s waiting: not behind
        y0 = d.read(frame)
        np.testing.assert_allclose(y0, x[:frame], atol=1e-6)
        self.assertEqual(d.status()["rxDropped"], 0)
        arrive(3)                                       # now 0.96 s waiting
        y1 = d.read(frame)
        skip = d.status()["rxDropped"]
        self.assertEqual(skip % frame, 0)
        self.assertEqual(d.status()["rxSkips"], 1)
        waiting = 4 * d.rx_buf_n - frame - skip
        self.assertLessEqual(waiting, fs * pluto.RX_CATCHUP_S + frame)
        self.assertGreater(waiting, fs * pluto.RX_CATCHUP_S - frame)
        # The stream carries on exactly a whole number of frames later.
        np.testing.assert_allclose(y1, x[frame + skip: 2 * frame + skip], atol=1e-6)


class SamplesCarryTheirLo(unittest.TestCase):
    """read_lo: the LO the samples read() returned were taken at. A retune
    reaches the stream late - the host's queue and the radio's own buffers
    hold samples from before it - and on 5 October 2026 frames from the old
    LO went out labelled with the new one (docs/atv-research.md 21a)."""

    def walk(self, d, blocks, frame):
        """(first sample, read_lo) for `blocks` reads of `frame` samples."""
        out = []
        for _ in range(blocks):
            pos = d._rx_pos
            d.read(frame)
            out.append((pos, d.read_lo))
        return out

    def test_old_samples_keep_the_old_lo_and_the_pipeline_is_unknown(self):
        fs = 2.5 * MHZ
        d, _ = opened(fs=fs)
        n, frame = d.rx_buf_n, int(fs * 0.04)
        pipe = pluto.RX_LO_PIPELINE_BUFS * n
        d.set_rx_lo(1255e6)
        # From open the radio's buffers hold samples from whatever LO it had.
        got = self.walk(d, (pipe + 2 * n) // frame + 1, frame)
        for pos, lo in got:
            self.assertEqual(lo, None if pos < pipe else 1255e6, pos)
        # Retuned with part of a buffer still held by read(): that part was
        # taken at 1255, then the pipeline's worth is unknown, then 1260.
        held = len(d.left)
        self.assertGreater(held, 0)
        at = d._rx_pos
        d.set_rx_lo(1260e6)
        got = self.walk(d, (held + pipe + 2 * n) // frame, frame)
        for pos, lo in got:
            end = pos + frame
            want = 1255e6 if end <= at + held else 1260e6 if pos >= at + held + pipe else None
            self.assertEqual(lo, want, pos - at)
        self.assertEqual(got[-1][1], 1260e6)

    def test_a_recording_s_segments_follow_the_samples(self):
        from ohsatv import iqrec
        fs = 2.5 * MHZ
        d, _ = opened(fs=fs)
        n = d.rx_buf_n
        pipe = pluto.RX_LO_PIPELINE_BUFS * n
        d.set_rx_lo(1255e6)
        d.read(pipe)                                 # past the pipeline from open
        tmp = tmp_dir(self, "ohsatv-lo-")
        rec = iqrec.Recorder(os.path.join(tmp, "r"), fs, 1255e6, "ci16_le", max_seconds=60, min_free=0,
                             disk_usage=lambda _p: shutil.disk_usage(_p)._replace(free=1 << 40))
        d.raw_tap = rec.feed
        d.read(n)
        start = rec.queued                           # the retune happens with nothing held
        self.assertEqual(len(d.left), 0)
        d.set_rx_lo(1260e6)
        d.read(pipe + 2 * n)
        rec.stop()
        r = iqrec.Recording(rec.meta_path)
        self.assertEqual(r.segments, [(0, 1255e6), (start + pipe, 1260e6)])
        unsure = [a for a in r.meta["annotations"] if a.get("openhamshack:lo_unsure")]
        self.assertEqual([(a["core:sample_start"], a["core:sample_count"]) for a in unsure], [(start, pipe)])
        # The recording's samples are a memory map of the data file, and
        # Windows will not delete a mapped file: let it go first.
        del r
        for p in (rec.meta_path, rec.data_path):
            os.unlink(p)

    def test_a_move_set_back_by_watch_leaves_nothing_held_trusted(self):
        # Something else moved the LO at a moment nobody saw: even what the
        # host already holds may be from the foreign LO.
        fs = 2.5 * MHZ
        d, radio = opened(fs=fs)
        n, frame = d.rx_buf_n, int(fs * 0.04)
        d.set_rx_lo(1255e6)
        d.read(pluto.RX_LO_PIPELINE_BUFS * n)
        d.read(frame)
        self.assertEqual(d.read_lo, 1255e6)
        self.assertGreater(len(d.left), frame)
        radio.move("rx_lo", "frequency", 1_300_000_000)
        self.assertEqual([m["what"] for m in d.watch()], ["rxLo"])
        d.read(frame)
        self.assertIsNone(d.read_lo)


class Transmit(unittest.TestCase):
    def keyed(self, fs=6.25 * MHZ):
        d, radio = opened(fs=fs)
        d.set_tx_gain(-10.1)
        self.assertEqual(radio.tx_ch.attrs["hardwaregain"].value, "-89.75")   # stored, not applied unkeyed
        d.tx_start()
        return d, radio, radio.txbufs[-1]

    def test_gain_is_clamped_to_quarter_db_steps(self):
        d, _ = opened()
        for asked, got in ((-10.1, -10.0), (-10.2, -10.25), (5.0, 0.0), (-120.0, -89.75)):
            d.set_tx_gain(asked)
            self.assertEqual(d.tx_gain, got)

    def test_keying_powers_the_lo_but_stays_attenuated_until_samples_flow(self):
        d, radio, buf = self.keyed()
        self.assertEqual(radio.tx_lo.attrs["powerdown"].value, "0")
        self.assertEqual(radio.tx_ch.attrs["hardwaregain"].value, "-89.75")
        # A fifth of a second per TX buffer (1.25 M samples at 6.25 MS/s, capped
        # at 1 M): measured on the nano, field-sized buffers ran late.
        self.assertEqual(buf.n, d.tx_buf_n)
        self.assertEqual(d.tx_buf_n, 1 << 20)
        d.write(np.zeros(d.tx_buf_n, np.complex64))
        # The first block left at full attenuation; the stored gain came after it.
        self.assertEqual([v for lab, n, v in radio.log if lab == "txbuf"], ["-89.75"])
        self.assertEqual(radio.tx_ch.attrs["hardwaregain"].value, "-10.0")
        d.set_tx_gain(-5.0)
        self.assertEqual(radio.tx_ch.attrs["hardwaregain"].value, "-5.0")

    def test_samples_are_packed_msb_aligned_in_whole_blocks(self):
        d, radio, buf = self.keyed()
        n = d.tx_buf_n
        rng = np.random.default_rng(2)
        x = (rng.uniform(-1, 1, 3 * n + 777) + 1j * rng.uniform(-1, 1, 3 * n + 777)).astype(np.complex64)
        x[0] = 1.5 - 1.5j                                    # out of range: clipped, not wrapped
        for i in range(0, len(x), 123_457):                  # the engine's chunks are not the device's
            d.write(x[i:i + 123_457])
        self.assertEqual(len(buf.pushed), 3)
        self.assertEqual(len(d.tx_pending), 777)
        s = np.concatenate(buf.pushed)
        self.assertEqual(s.dtype, np.int16)
        self.assertEqual((s[0], s[1]), (32767, -32767))
        np.testing.assert_array_equal(s[2::2], np.round(x[1:3 * n].real * 32767).astype(np.int16))
        np.testing.assert_array_equal(s[3::2], np.round(x[1:3 * n].imag * 32767).astype(np.int16))

    def test_stop_attenuates_pushes_zeros_and_powers_the_lo_down(self):
        d, radio, buf = self.keyed()
        d.write(np.full(d.tx_buf_n + 10, 0.5 + 0.5j, np.complex64))
        d.tx_stop()
        self.assertEqual(len(buf.pushed), 2)
        self.assertFalse(buf.pushed[-1].any())
        # The zeros went out already attenuated.
        self.assertEqual([v for lab, n, v in radio.log if lab == "txbuf"], ["-89.75", "-89.75"])
        self.assertEqual(radio.tx_ch.attrs["hardwaregain"].value, "-89.75")
        self.assertEqual(radio.tx_lo.attrs["powerdown"].value, "1")
        self.assertFalse(d.status()["tx"])
        with self.assertRaises(DeviceError):
            d.write(np.zeros(10, np.complex64))

    def test_close_while_keyed_stops_transmitting(self):
        d, radio, buf = self.keyed()
        d.close()
        self.assertEqual(radio.tx_ch.attrs["hardwaregain"].value, "-89.75")
        self.assertEqual(radio.tx_lo.attrs["powerdown"].value, "1")

    def test_late_pushes_are_counted(self):
        d, radio, buf = self.keyed()
        blk = np.zeros(d.tx_buf_n, np.complex64)
        for _ in range(10):                                   # exactly paced: never late
            d.write(blk)
            d.clock.t += d.tx_buf_n / d.fs
        self.assertEqual(d.status()["txLate"], 0)
        d.clock.t += 0.5                                      # a stall longer than what was queued
        d.write(blk)
        self.assertEqual(d.status()["txLate"], 1)
        d.clock.t += 1.0
        d.tx_stop()                                           # the closing zeros are not an underrun
        self.assertEqual(d.status()["txLate"], 1)


class WriteLoop(unittest.TestCase):
    def test_a_static_loop_goes_out_seamlessly_in_whole_buffers(self):
        # write_loop pre-converts the loop and pushes prepared slices; what
        # reaches the device must be the loop's samples in order, wrapping
        # without a seam, buffer after buffer.
        import threading
        d, radio = opened(fs=5 * MHZ)
        d.set_tx_gain(-20.0)
        d.tx_start()
        buf = radio.txbufs[-1]
        L = 800_000                                    # a 0.16 s loop at 5 MS/s; buffers are 1 M
        loop = ((np.arange(L) % 997) / 997.0 * (1 + 0j)).astype(np.complex64)
        stop = threading.Event()
        pushes = []
        real_push = d._push
        def counting(data, count=True):
            pushes.append(1)
            real_push(data, count)
            if len(pushes) >= 6:
                stop.set()
        d._push = counting
        d.write_loop(loop, stop)
        out = np.concatenate(buf.pushed)
        self.assertEqual(len(out), 2 * 6 * d.tx_buf_n)
        want = d.to_native(np.tile(loop, 9)[: 6 * d.tx_buf_n])
        np.testing.assert_array_equal(out, want)
        self.assertEqual(radio.tx_ch.attrs["hardwaregain"].value, "-20.0")   # gain only after samples flowed


class Status(unittest.TestCase):
    def test_every_field(self):
        d, _ = opened(FakeRadio("ad9364", hw_model="LibreSDR (Z7020-AD9361)"), uri="ip:198.51.100.50")
        st = d.status()
        for key in ("kind", "uri", "model", "phyModel", "range", "link", "linkMsps", "fs", "rxLo", "txLo", "tx",
                    "rxRate", "rxShort", "txLate", "notes"):
            self.assertIn(key, st)
        self.assertEqual(st["kind"], "pluto")
        self.assertEqual(st["model"], "LibreSDR (Z7020-AD9361)")
        self.assertEqual(st["phyModel"], "ad9364")
        self.assertEqual(st["range"], [70e6, 6000e6])


# Linux's sentences (an apt line, iio_info); Windows' are tested in
# test_windows_host, so these hold on the Windows build machine too.
@mock.patch.object(host, "WINDOWS", False)
class OpenFails(unittest.TestCase):
    def test_no_radio_is_a_sentence(self):
        with mock.patch.dict(sys.modules, {"iio": FakeIIO()}):
            with self.assertRaises(DeviceError) as cm:
                pluto.PlutoDevice("ip:198.51.100.99", 10 * MHZ, {}).open()
        self.assertIn("iio_info -s", str(cm.exception))

    def test_no_bindings_is_a_sentence(self):
        with mock.patch.dict(sys.modules, {"iio": None}):
            with self.assertRaises(DeviceError) as cm:
                pluto.PlutoDevice("ip:192.168.2.1", 10 * MHZ, {}).open()
        self.assertIn("python3-libiio", str(cm.exception))


# -- discover(), adapted from OpenHamShack NB's tests --------------------------------

# The exact three entries libiio's scan_contexts() returned on a real laptop
# (Debian 13, libiio 0.26) with a Pluto nano on USB and avahi running.
REAL_SCAN = {
    "ip:Nano.local": f"192.168.2.1 (Nano PlutoSDR (Z7010-AD9361)), serial={PLUTO_SERIAL}",
    "local:": "(ADP0,BAT0,coretemp,nvme on LENOVO)",
    "usb:3.4.5": f"0456:b673 (Analog Devices Inc. PlutoSDR (ADALM-PLUTO)), serial={PLUTO_SERIAL}",
}


class _ProbeContext:
    def __init__(self, devices=None, attrs=None):
        self._devices = devices or {}
        self.attrs = attrs or {}

    def find_device(self, name):
        return self._devices.get(name)

    def set_timeout(self, ms):
        pass


def _pluto_context(model="Nano PlutoSDR (Z7010-AD9361)", serial=PLUTO_SERIAL):
    attrs = {"hw_model": model}
    if serial:
        attrs["hw_serial"] = serial
    return _ProbeContext(devices={"ad9361-phy": object()}, attrs=attrs)


def _discover(fake_iio, **kw):
    """discover() with no real USB-network interfaces in play, so results
    depend only on the fake, never on the host the suite runs on."""
    with mock.patch.dict(sys.modules, {"iio": fake_iio}), \
         mock.patch.object(pluto, "_usb_network_addresses", return_value=[]):
        return pluto.discover(timeout_s=0.2, **kw)


class DiscoverFullScanWorks(unittest.TestCase):
    def test_local_and_unrecognised_entries_are_dropped(self):
        uris = [uri for uri, _ in _discover(FakeIIO(scan=REAL_SCAN))]
        self.assertNotIn("local:", uris)
        for uri in uris:
            self.assertIn(uri, ("ip:Nano.local", "usb:3.4.5", "ip:192.168.2.1"))

    def test_same_serial_over_ip_and_usb_collapses_to_one_entry_preferring_usb(self):
        # The USB network route carries ~5.2 MS/s, libusb the full 6.25
        # (measured on the laptop's nano): for video, usb: wins.
        fake = FakeIIO(scan=REAL_SCAN, contexts={"ip:192.168.2.1": _pluto_context()})
        uris = [uri for uri, _ in _discover(fake)]
        self.assertEqual(uris, ["usb:3.4.5"])

    def test_a_real_ethernet_address_beats_usb_for_the_same_radio(self):
        self.assertLess(pluto._preference("ip:198.51.100.50"), pluto._preference("usb:3.4.5"))
        self.assertLess(pluto._preference("usb:3.4.5"), pluto._preference("ip:192.168.2.1"))

    def test_a_probe_that_answers_but_is_not_an_ad936x_is_dropped(self):
        fake = FakeIIO(contexts={"ip:192.168.2.1": _ProbeContext(attrs={"hw_model": "something else"})})
        self.assertEqual(_discover(fake), [])

    def test_nothing_found_returns_an_empty_list_without_raising(self):
        self.assertEqual(_discover(FakeIIO(scan={})), [])


class DiscoverFullScanFails(unittest.TestCase):
    def test_avahi_failure_falls_back_to_a_usb_backend_scan(self):
        fake = FakeIIO(scan_error=OSError(26, "Text file busy"))
        usb_only = {"usb:3.4.5": f"0456:b673 (Analog Devices Inc. PlutoSDR (ADALM-PLUTO)), serial={PLUTO_SERIAL}"}

        def fake_backend_scan(iio_mod, backend):
            self.assertIs(iio_mod, fake)
            return usb_only if backend == b"usb" else {}

        with mock.patch.object(pluto, "_backend_scan", side_effect=fake_backend_scan):
            notes = []
            results = _discover(fake, notes=notes)
        self.assertEqual(results, [("usb:3.4.5", usb_only["usb:3.4.5"])])
        self.assertTrue(notes)

    def test_a_scan_exception_never_propagates_out_of_discover(self):
        fake = FakeIIO(scan_error=RuntimeError("libiio blew up"))
        with mock.patch.object(pluto, "_backend_scan", return_value={}):
            self.assertEqual(_discover(fake), [])


# Linux's sentences (an apt line, iio_info); Windows' are tested in
# test_windows_host, so these hold on the Windows build machine too.
@mock.patch.object(host, "WINDOWS", False)
class DiscoverMissingLibrary(unittest.TestCase):
    def test_no_python3_libiio_returns_an_empty_list_with_a_note(self):
        with mock.patch.dict(sys.modules, {"iio": None}):
            notes = []
            results = pluto.discover(notes=notes)
        self.assertEqual(results, [])
        self.assertIn("python3-libiio", notes[0])


if __name__ == "__main__":
    unittest.main()


class Release(unittest.TestCase):
    def test_close_lets_go_of_the_context(self):
        # The libiio binding frees the radio (and on usb: the claimed USB
        # interface) when the last reference to its context goes; a reopen on
        # the real nano failed while channels still held it.
        import gc
        import weakref
        d, radio = opened(fs=6.25 * MHZ)
        ref = weakref.ref(d.ctx)
        d.read(1000)
        d.close()
        gc.collect()
        for name in ("ctx", "phy", "rx_ch", "tx_ch", "rx_lo_ch", "tx_lo_ch", "rxdev", "txdev", "rxbuf", "txbuf"):
            self.assertIsNone(getattr(d, name, None), name)
        self.assertIsNone(ref())


class DiscoverByName(unittest.TestCase):
    def test_a_radio_found_by_name_is_offered_by_its_ipv4_address(self):
        # Brian's LibreSDR: no serial, scan entry keyed by its mDNS name with
        # the IPv6 address as description (the IPv4 entry lost in the dict).
        scan = {"ip:libre-2.local": "fdb7:df0:b5e4:46d7:8d9b:4be:a63b:b62e "
                                    "(ad9361-phy,xadc,cf-ad9361-dds-core-lpc,cf-ad9361-lpc)"}
        with mock.patch.object(pluto, "_resolve_ipv4", return_value="198.51.100.74"):
            found = _discover(FakeIIO(scan=scan))
        self.assertEqual([u for u, _ in found], ["ip:198.51.100.74"])
        self.assertTrue(found[0][1].startswith("198.51.100.74 (libre-2.local)"))

    def test_a_name_that_does_not_resolve_is_still_offered(self):
        scan = {"ip:libre-2.local": "fdb7::1 (ad9361-phy,cf-ad9361-lpc)"}
        with mock.patch.object(pluto, "_resolve_ipv4", return_value=None):
            found = _discover(FakeIIO(scan=scan))
        self.assertEqual([u for u, _ in found], ["ip:libre-2.local"])
