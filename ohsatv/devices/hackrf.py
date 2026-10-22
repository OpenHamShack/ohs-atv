"""HackRF One as a transmit-only device, through libhackrf (ctypes).

Why a HackRF at all: it carries 8-bit IQ at up to 20 MS/s over USB 2
(40 MB/s), so a USB device can send full-width FM-ATV on 23cm and 6cm where
a USB Pluto (16-bit IQ, ~6.8 MS/s measured by ADI) cannot. Receive stays on
the Pluto, LibreSDR or the simulator: the HackRF is half duplex, and
receiving and transmitting ATV on one half-duplex radio would leave the
operator blind while keyed. The engine opens it as a second device
(`txDevice = "hackrf"`), so receive keeps running on the first.

Decisions:

- **libhackrf through ctypes**, not pyhackrf (not packaged in Debian) or
  SoapyHackRF (another module and another layer whose buffer behaviour we
  would have to infer). Debian 13 ships `libhackrf0`; the .deb recommends it.
  A missing library or radio is a sentence, never a crash.
- **On Windows** the installer carries conda-forge's build of the same
  libhackrf release (hackrf-0.dll, with the libusb and winpthreads DLLs it
  imports, in _internal\\hackrf), loaded by full path (host.hackrf_library
  says why, and why its libusb may be libiio's). No driver step: the HackRF
  firmware answers Windows' WCID request (firmware/hackrf_usb/
  usb_descriptor.c), so WinUSB is bound to it on plugging in. Not yet seen
  with a real HackRF on Windows; a PortaPack's Mayhem firmware may not
  carry the descriptors, and would then need WinUSB bound with Zadig.
- **Samples**: complex64 |x| <= 1 to interleaved int8 I/Q, scaled by 127
  and rounded, clipped to +-127 (never -128, so I and Q stay symmetric).
  The modulators peak at 0.95 (FM: constant 0.95; AM: sync tip plus sound
  scaled to 0.95), leaving ~0.4 dB of headroom for rounding and filter
  overshoot. 8 bits give ~48 dB of quantisation SNR in the band, plenty for
  FM-ATV and the AM picture's ~40 dB.
- **Pacing**: libhackrf pulls samples in a callback on its USB thread. Our
  write() fills a ring buffer (a quarter of a second) and blocks while it is
  full, so the transmitter thread is paced by the radio. When the callback
  finds the ring short it sends zeros for the gap and counts an **underrun**
  - the host did not keep up, and the receiver will see a break in the
  picture. status()["txUnderruns"] carries the count to the window; it is
  never silent.
- **Gain**: the engine's txGain is "dB below the device's full output"
  (-89.75..0, the Pluto's scale). The HackRF's TX VGA covers 0..47 dB, so
  VGA = 47 + txGain, clamped. The 14 dB RF amplifier is a separate setting,
  off by default (`hackrfAmp`, Settings only).
- **Range** 1 MHz - 6 GHz; rates 2-20 MS/s (we never ask for more than
  20). Output falls off with frequency: roughly 0-15 dBm below 2 GHz with
  the amplifier, a few dBm at 5.8 GHz (Great Scott Gadgets publish a
  frequency-dependent table and call the top of the range "much lower") -
  the band card says an external PA is needed on 6cm.
- **Quiet until keyed**: open() sets VGA 0 and the amplifier off and does
  not start streaming. tx_start() streams with VGA 0 and the amplifier off;
  the first four transfers are zeros (libhackrf fills them before anything
  is written), which leave the carrier from LO leakage. The amplifier and a
  4 ms ramp up to the gain come only once the first transfer of picture has
  gone to the device: 37 ms after tx_start at 20 MS/s, 83 at 10, 325 at 2
  on the model. tx_stop() ramps the gain to VGA 0 over 4 ms while the queued
  samples keep playing, then stops the stream (which is what turns the RF
  path off), then the amplifier: the radio is off within ~5 ms of the call
  on the modelled bus. The 10 ms test bound and the reasons for this order
  are in tx_stop() and research notes 19c.

- **Filter**: open() leaves libhackrf's default baseband filter (the
  largest setting under 0.75 fs); before each over the engine sets it for
  that over's plan (set_tx_bandwidth). An AM plan narrows it to the
  signal, because nothing else stands between the converter's images at
  +-fs and the antenna: 405 lines at 20 MS/s get 5 MHz, so their images,
  20 MHz out, are far down the MAX2837's skirt where at 8 MS/s and 6 MHz
  they reached -19 dBc (the model and its sources are under "the filter"
  below). An FM over puts the default back, which is what FM was measured
  through - every over sets it, because the device stays open from one
  over to the next and an AM over's 5 MHz would otherwise clip the next FM
  over's deviation.

- **Shared**: the radio lock (devices/shared.py, OpenHamShack DATV's
  docs/radio-lock.md) is taken at open(), after libhackrf is initialised
  and before the device is opened - opening it is when the sample rate and
  filter are written - and given back at close(), after the radio is
  closed. The radio is `hackrf-<serial>`: the configured serial, completed
  from libhackrf's device list (Settings may hold only its tail, as
  hackrf_info prints it), or the first HackRF's, which is the one
  hackrf_open() takes. A HackRF has no shell, so the lock is this
  computer's marker alone. Its `use` is tx from open to close: it is never
  a receiver here, and the note says whether it is keyed. Nothing in it
  can be read back (libhackrf has no getters for frequency or gain), so
  unlike the Pluto there is no check that another program changed it -
  only the lock.

NOT TESTED ON HARDWARE: there is no HackRF on the development host. The
library is mocked in tests/test_devices_hackrf.py.
"""
from __future__ import annotations

import ctypes
import math
import threading
import time

import numpy as np

from .. import host, radiolock
from . import shared
from .base import Device, DeviceError

MAX_FS = 20_000_000.0
MIN_FS = 2_000_000.0
RANGE = (1e6, 6e9)
VGA_MAX = 47
SCALE = 127.0
RING_S = 0.25
# Unkey: the TX VGA follows a raised-cosine amplitude envelope from the keyed
# gain to VGA 0 over RAMP_S: a control transfer whenever the envelope has
# crossed a 1 dB step (several at once if the bus was slow; each takes a few
# tenths of a millisecond on USB 2), the clock checked every RAMP_POLL_S
# in between. 4 ms is long against one TV line (64 us) and
# short against the 10 ms we allow from ptt(False) to the RF path being off.
RAMP_S = 0.004
RAMP_POLL_S = 0.00025
# libhackrf 2024.02 keeps TRANSFER_COUNT transfers in flight and asks for a
# refill as each one completes, in order: the (n + TRANSFERS)th call of the
# sample callback means transfer n has gone to the device.
TRANSFERS = 4


class HackrfTransfer(ctypes.Structure):
    """hackrf_transfer from libhackrf's hackrf.h (2024.02)."""
    _fields_ = [("device", ctypes.c_void_p), ("buffer", ctypes.POINTER(ctypes.c_uint8)),
                ("buffer_length", ctypes.c_int), ("valid_length", ctypes.c_int),
                ("rx_ctx", ctypes.c_void_p), ("tx_ctx", ctypes.c_void_p)]


class HackrfDeviceList(ctypes.Structure):
    _fields_ = [("serial_numbers", ctypes.POINTER(ctypes.c_char_p)), ("usb_board_ids", ctypes.c_void_p),
                ("usb_device_index", ctypes.POINTER(ctypes.c_int)), ("devicecount", ctypes.c_int),
                ("usb_devices", ctypes.c_void_p), ("usb_devicecount", ctypes.c_int)]


CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(HackrfTransfer))


# ---------------------------------------------------------------- the filter
# A HackRF has no interpolating filter: the MAX5864 converts the samples as
# they come, so every component at f has images at f +- fs, and the only
# things between them and the antenna are the converter's own hold and the
# MAX2837's baseband lowpass filter. On 5 October 2026 at 8 MS/s, with the
# filter libhackrf picks by default (6 MHz, the largest under 0.75 fs), the
# 405-line sound carrier's image came out 8 MHz above it at -18.6 dBc and the
# vision carrier's 8 MHz below at -29 (docs/atv-research.md 21). The cure is
# room and a narrow filter: at 20 MS/s the images are 20 MHz away and a
# filter just wide enough for the signal is far down its skirt there.
#
# The MAX2837's settings (Maxim datasheet 19-5235 Rev 3, Table 5 "LPF
# Register 1", D7:D4; the same field filters receive and transmit - D3:D2
# only chooses which path the block serves) as libhackrf takes them, the
# "RF channel bandwidth", twice the baseband corner.
FILTERS = (1.75e6, 2.5e6, 3.5e6, 5.0e6, 5.5e6, 6.0e6, 7.0e6, 8.0e6, 9.0e6, 10.0e6, 12.0e6, 14.0e6, 15.0e6,
           20.0e6, 24.0e6, 28.0e6)
# The datasheet's transmit "baseband frequency response": within 0.2 dB from
# 0 to 2.3 MHz at the 5 MHz setting, to 4.6 at 10 - flat to 0.92 of half
# the setting. A filter is chosen so the signal stays inside that.
FILTER_FLAT = 0.92
# Its rejection (receive table, the one with points on the skirt: 7 dB at
# 3.3 MHz for 5 MHz; 7 dB at 6.7, 57 at 15, 75 at 20 for 10 MHz) fits a
# Butterworth-shaped magnitude, 10 log10(1 + (x / 1.208) ** 14.42) with x the
# frequency over half the setting, to within 0.4 dB at all four. The
# transmit table gives only "80 dB" far out (beyond 25 MHz at 5 MHz), so
# that is where the model stops: nothing here claims more.
FILTER_SHAPE = (1.208, 14.42)
FILTER_FLOOR_DB = 80.0
# The model with the hold falls short of what was measured at 8 MS/s on 5
# October: it puts the sound carrier's image 38 dB down and the vision
# carrier's 51 where 19 and 29 were seen. Why is not known (the filter's
# corner in transmit, the converter's output, or the libre-2 receiving it
# are all candidates); until it is, the images are taken to be this much
# stronger than the datasheet makes them. The 20 MS/s burst of 21a measured
# it again.
FILTER_SHORTFALL_DB = 20.0


def default_filter(fs: float) -> float:
    """The filter libhackrf picks for a rate when asked for 0.75 fs
    (hackrf_compute_baseband_filter_bw rounds down): what open() sets."""
    want = 0.75 * fs
    ok = [b for b in FILTERS if b <= want]
    return ok[-1] if ok else FILTERS[0]


def filter_for(fs: float, span: float) -> float:
    """The narrowest setting that passes a signal reaching `span` Hz either
    side of the LO within the datasheet's flat 0.2 dB, or the widest."""
    for b in FILTERS:
        if FILTER_FLAT * b / 2 >= span:
            return b
    return FILTERS[-1]


def attenuation_db(f, bw: float, fs: float) -> np.ndarray:
    """What the converter's hold and the baseband filter at setting `bw` take
    off a component `f` Hz from the LO, in dB (positive), on the datasheet
    model above."""
    f = np.abs(np.asarray(f, np.float64))
    c, n2 = FILTER_SHAPE
    lpf = np.minimum(10 * np.log10(1 + (f / (c * bw / 2)) ** n2), FILTER_FLOOR_DB)
    hold = -20 * np.log10(np.maximum(np.abs(np.sinc(f / fs)), 1e-6))
    return lpf + hold


def image(fs: float, f: float, bw: float) -> tuple[float, float]:
    """(where, Hz from the LO; dB against the component itself, negative) of
    the stronger image a HackRF at `fs` with filter `bw` makes of a
    component `f` Hz from the LO: the nearer one, at f - fs above the LO and
    f + fs below. With FILTER_SHORTFALL_DB taken off what the model says."""
    at = f - fs if f > 0 else f + fs
    db = -(float(attenuation_db(at, bw, fs)) - float(attenuation_db(f, bw, fs))) + FILTER_SHORTFALL_DB
    return at, min(db, 0.0)


def to_int8(iq: np.ndarray) -> np.ndarray:
    """complex64 (|x| <= 1) -> interleaved int8 I, Q, +-127."""
    iq = np.asarray(iq, np.complex64)
    out = np.empty(2 * len(iq), np.float32)
    out[0::2] = iq.real
    out[1::2] = iq.imag
    np.multiply(out, np.float32(SCALE), out=out)
    np.rint(out, out=out)
    np.clip(out, -SCALE, SCALE, out=out)
    return out.astype(np.int8)


def vga_for(tx_gain_db: float) -> int:
    """Engine txGain (dB below full output) -> TX VGA setting 0..47."""
    return int(max(0, min(VGA_MAX, round(VGA_MAX + float(tx_gain_db)))))


def load_lib(candidates=None):
    """libhackrf with the prototypes we use, or None. `candidates` are what
    to try, in order (host.hackrf_library(): the system's libhackrf on
    Linux; on Windows the installer's hackrf-0.dll by full path, then any
    on PATH); the first that loads is used."""
    lib = None
    for name in (candidates if candidates is not None else host.hackrf_library()):
        try:
            lib = ctypes.CDLL(name)
            break
        except OSError:
            continue
    if lib is None:
        return None
    p = ctypes.c_void_p
    sig = {
        "hackrf_init": ([], ctypes.c_int),
        "hackrf_exit": ([], ctypes.c_int),
        "hackrf_open": ([ctypes.POINTER(p)], ctypes.c_int),
        "hackrf_open_by_serial": ([ctypes.c_char_p, ctypes.POINTER(p)], ctypes.c_int),
        "hackrf_close": ([p], ctypes.c_int),
        "hackrf_set_freq": ([p, ctypes.c_uint64], ctypes.c_int),
        "hackrf_set_sample_rate": ([p, ctypes.c_double], ctypes.c_int),
        "hackrf_compute_baseband_filter_bw": ([ctypes.c_uint32], ctypes.c_uint32),
        "hackrf_set_baseband_filter_bandwidth": ([p, ctypes.c_uint32], ctypes.c_int),
        "hackrf_set_txvga_gain": ([p, ctypes.c_uint32], ctypes.c_int),
        "hackrf_set_amp_enable": ([p, ctypes.c_uint8], ctypes.c_int),
        "hackrf_start_tx": ([p, CALLBACK, p], ctypes.c_int),
        "hackrf_stop_tx": ([p], ctypes.c_int),
        "hackrf_error_name": ([ctypes.c_int], ctypes.c_char_p),
        "hackrf_device_list": ([], ctypes.POINTER(HackrfDeviceList)),
        "hackrf_device_list_free": ([ctypes.POINTER(HackrfDeviceList)], None),
    }
    for fn, (args, res) in sig.items():
        f = getattr(lib, fn, None)
        if f is None:
            return None
        f.argtypes = args
        f.restype = res
    return lib


def discover(lib=None, notes: list[str] | None = None) -> list[str]:
    """Serial numbers of attached HackRFs; [] (with a note) if none or no library."""
    lib = lib if lib is not None else load_lib()
    if lib is None:
        if notes is not None:
            notes.append(host.hint("libhackrf-scan"))
        return []
    if lib.hackrf_init() != 0:
        if notes is not None:
            notes.append("libhackrf would not initialise")
        return []
    try:
        lst = lib.hackrf_device_list()
        if not lst:
            return []
        try:
            d = lst.contents
            return [(d.serial_numbers[i] or b"").decode("ascii", "replace") for i in range(d.devicecount)]
        finally:
            lib.hackrf_device_list_free(lst)
    finally:
        lib.hackrf_exit()


class HackRFDevice(Device):
    kind = "hackrf"
    can_tx = True
    can_rx = False
    link = "usb"
    link_msps = 20.0
    # tx_stop() may be called while another thread is blocked in write():
    # it wakes that write, which then returns. The engine uses this to unkey
    # at once even if the USB stream has stalled.
    stop_from_any_thread = True

    def __init__(self, fs: float, cfg: dict | None = None, lib=None, clock=None, sleep=None):
        self.fs = float(fs)
        # The unkey ramp is timed; tests drive it on a simulated radio's clock.
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.cfg = cfg or {}
        self.serial = (self.cfg.get("hackrfSerial") or "").strip()
        self.amp = bool(self.cfg.get("hackrfAmp", False))
        self._lib = lib
        self.lib = None
        self.dev = ctypes.c_void_p()
        self.tx_on = False
        self.streaming = False          # libhackrf's stream is open (start_tx until stop_tx)
        self.primed = False
        self.feeding = False            # _callback hands libhackrf samples (False: it ends the stream)
        self.vga_set = 0                # the TX VGA the radio last accepted
        self.fills = 0                  # sample callbacks since tx_start
        self.first_real = None          # the first transfer filled entirely from the ring
        self.radiating = False          # ... and it has completed: the picture is on the air
        # For measuring on air: when, on this clock, key-up and unkey did
        # what, so a recording can be lined up against the driver's events.
        self.t_key = None               # tx_start() called
        self.gain_up_ms = None          # ... until the ramp up reached the gain
        self.stop_ms = None             # the last tx_stop(), call to amplifier off
        self.tx_lo = None
        self.gain = -89.75
        self.bb_filter = None           # the baseband filter setting the radio last accepted (Hz)
        # Two locks, because libhackrf's control calls (gain, amplifier,
        # start, stop) are synchronous libusb transfers whose completion is
        # reaped by libhackrf's own event thread - the same thread that runs
        # _callback. Holding the ring's lock across one of them deadlocks
        # if a bulk transfer comes due first: the event thread sits in
        # _callback waiting for the lock, so the control transfer is never
        # reaped and its caller never lets go. `lock` guards the ring and is
        # all _callback takes; `ctl` serialises the control calls with the
        # decisions that go with them (raise the gain now? stop now?) and
        # _callback never touches it.
        self.lock = threading.Condition()
        self.ctl = threading.Lock()
        size = 2 * int(self.fs * RING_S)
        self.ring = np.zeros(size, np.int8)
        self.r = 0
        self.n = 0
        self.underruns = 0
        self.underrun_samples = 0
        self.sent = 0
        self.t_start = None
        self._cb = CALLBACK(self._callback)       # kept alive for as long as the device
        self.notes: list[str] = []

    # ------------------------------------------------------------- helpers
    def _check(self, ret: int, what: str):
        if ret != 0:
            try:
                name = self.lib.hackrf_error_name(ret)
                name = name.decode() if isinstance(name, bytes) else str(name)
            except Exception:  # noqa: BLE001
                name = str(ret)
            raise DeviceError(f"HackRF: {what} failed ({name})")

    def _vga(self, v: int) -> int:
        ret = self.lib.hackrf_set_txvga_gain(self.dev, v)
        if ret == 0:
            self.vga_set = v
        return ret

    # ----------------------------------------------------------- lifecycle
    def open(self) -> None:
        if not (MIN_FS <= self.fs <= MAX_FS):
            raise DeviceError(f"The HackRF runs at {MIN_FS / 1e6:g}-{MAX_FS / 1e6:g} MS/s; "
                              f"{self.fs / 1e6:g} MS/s is outside that")
        self.lib = self._lib if self._lib is not None else load_lib()
        if self.lib is None:
            raise DeviceError(host.hint("libhackrf"))
        self._check(self.lib.hackrf_init(), "initialising libhackrf")
        if self.lock_radio:
            try:
                self._take_lock()
            except shared.RadioBusy:
                self.lib.hackrf_exit()
                raise
        if self.serial:
            ret = self.lib.hackrf_open_by_serial(self.serial.encode(), ctypes.byref(self.dev))
        else:
            ret = self.lib.hackrf_open(ctypes.byref(self.dev))
        if ret != 0:
            self.lib.hackrf_exit()
            shared.release(self)            # not opened: not ours to hold
            who = f" with serial {self.serial}" if self.serial else ""
            raise DeviceError(f"No HackRF{who} found ({host.hint('hackrf-none')})")
        try:
            self._check(self.lib.hackrf_set_sample_rate(self.dev, self.fs), "setting the sample rate")
            bw = self.lib.hackrf_compute_baseband_filter_bw(int(0.75 * self.fs))
            self._check(self.lib.hackrf_set_baseband_filter_bandwidth(self.dev, bw), "setting the filter")
            self.bb_filter = float(bw)
            # Quiet: no gain, no amplifier, nothing streaming.
            self._check(self._vga(0), "setting TX gain")
            self._check(self.lib.hackrf_set_amp_enable(self.dev, 0), "switching the amplifier off")
        except DeviceError:
            self.close()
            raise
        if self.fs > 16e6:
            self.notes.append("20 MS/s is the HackRF's limit over USB 2; a busy USB bus shows as underruns")

    def close(self) -> None:
        try:
            self.tx_stop()
        finally:
            if self.lib is not None and self.dev:
                self.lib.hackrf_close(self.dev)
                self.lib.hackrf_exit()
            self.dev = ctypes.c_void_p()
            shared.release(self)

    def _serials(self) -> list[str]:
        """Serial numbers of the attached HackRFs, in libhackrf's order.
        Not discover(): that initialises and exits libhackrf itself, and an
        exit with nothing open shuts down the libusb this open() is using."""
        fn = getattr(self.lib, "hackrf_device_list", None)
        if fn is None:
            return []
        try:
            lst = fn()
            if not lst:
                return []
            try:
                d = lst.contents
                return [(d.serial_numbers[i] or b"").decode("ascii", "replace") for i in range(d.devicecount)]
            finally:
                self.lib.hackrf_device_list_free(lst)
        except Exception:  # noqa: BLE001 - no list: the radio is named without its serial
            return []

    def _take_lock(self) -> None:
        serials = self._serials()
        if self.serial:
            full = next((s for s in serials if s.lower().endswith(self.serial.lower())), self.serial)
        else:
            full = serials[0] if serials else ""
        rid = radiolock.radio_id("hackrf", full or None)
        label = f"the HackRF {full.lstrip('0') or full}" if full else "the HackRF"
        shared.take(self, rid, label, None, use="tx")

    def freq_range(self):
        return RANGE

    def duplex(self) -> bool:
        # Transmit only: receiving is another device's job, so the receive
        # stream on that device carries on while this one transmits.
        return True

    def set_rx_lo(self, hz: float) -> None:
        raise DeviceError("The HackRF is used for transmit only here; receive comes from the Pluto, "
                          "LibreSDR or the simulator")

    def read(self, n: int) -> np.ndarray:
        raise DeviceError("The HackRF is used for transmit only here")

    # ------------------------------------------------------------- transmit
    def set_tx_lo(self, hz: float) -> None:
        if not (RANGE[0] <= hz <= RANGE[1]):
            raise DeviceError(f"The HackRF tunes 1 MHz-6 GHz; {hz / 1e6:.3f} MHz is outside it")
        self.tx_lo = float(hz)
        self._check(self.lib.hackrf_set_freq(self.dev, int(round(hz))), "tuning")

    def set_tx_bandwidth(self, span: float | None) -> None:
        """Set the baseband filter for an over: narrowed to what a signal
        reaching `span` Hz either side of the LO needs (filter_for), so its
        images at +-fs are far down the filter's skirt, or with `span` None
        libhackrf's default for the rate (default_filter), as open() leaves
        it. The engine calls it before the gain is set for every over, so
        no over inherits the last one's filter. The default at 8 MS/s let
        AM's images through at -19 dBc (the module notes). Under ctl, so it
        cannot land between a decision to raise the gain and the gain."""
        bw = default_filter(self.fs) if span is None else filter_for(self.fs, span)
        with self.ctl:
            self._check(self.lib.hackrf_set_baseband_filter_bandwidth(self.dev, int(bw)), "setting the filter")
            self.bb_filter = bw

    def set_tx_gain(self, db: float) -> None:
        with self.ctl:
            self.gain = float(db)
            if self.tx_on and self.primed:
                self._check(self._vga(vga_for(db)), "setting TX gain")

    def tx_start(self) -> None:
        with self.ctl:
            if self.streaming:
                return
            with self.lock:
                self.r = self.n = 0
                self.ring[:] = 0
            self.underruns = 0
            self.underrun_samples = 0
            self.sent = 0
            self.t_key = self.clock()
            self.gain_up_ms = None
            # VGA 0 and the amplifier off until the picture is on the air:
            # start_tx fills its first four transfers before write() has
            # queued anything, so they are zeros - the carrier from LO
            # leakage - for 26 ms at 20 MS/s, 262 ms at 2. _prime() brings
            # the amplifier and the gain up once they have gone.
            self._check(self._vga(0), "setting TX gain")
            self._check(self.lib.hackrf_set_amp_enable(self.dev, 0), "switching the amplifier off")
            self.primed = False
            with self.lock:
                # Before start_tx: libhackrf calls back for the first four
                # transfers from inside it.
                self.fills = 0
                self.first_real = None
                self.radiating = False
                self.feeding = True
            try:
                self._check(self.lib.hackrf_start_tx(self.dev, self._cb, None), "starting transmit")
            except DeviceError:
                with self.lock:
                    self.feeding = False
                raise
            self.streaming = True
            with self.lock:
                self.tx_on = True
            self.t_start = time.monotonic()

    def write(self, iq: np.ndarray) -> None:
        if not self.tx_on:
            raise DeviceError("Transmit is not started")
        b = to_int8(iq)
        size = len(self.ring)
        pos = 0
        while pos < len(b):
            with self.lock:
                while self.n >= size and self.tx_on and not (self.radiating and not self.primed):
                    self.lock.wait(0.2)
                if not self.tx_on:
                    return
                # The ring is full and the picture reached the air while we
                # waited for room: bring the gain up now, not after the next
                # drain (a whole transfer, 65 ms at 2 MS/s).
                prime = self.n >= size
                if not prime:
                    k = min(len(b) - pos, size - self.n)
                    w = (self.r + self.n) % size
                    k1 = min(k, size - w)
                    self.ring[w:w + k1] = b[pos:pos + k1]
                    self.ring[:k - k1] = b[pos + k1:pos + k]
                    self.n += k
                    pos += k
            if prime:
                self._prime()
        if self.radiating and not self.primed:
            self._prime()

    def _prime(self) -> None:
        """Amplifier on (if set) and the gain ramped up, once the first
        transfer of real samples has gone to the device. Under ctl, so a
        tx_stop() on another thread cannot slip in between the check and
        the gain going up: it waits for ctl, and then takes down whatever
        the gain has reached. Never from _callback: libhackrf's thread must
        not make control calls."""
        with self.ctl:
            if self.primed or not self.tx_on:
                return
            self.primed = True
            if self.amp:
                self._check(self.lib.hackrf_set_amp_enable(self.dev, 1), "setting the amplifier")
            self._ramp(vga_for(self.gain))
            if self.tx_on:
                self.gain_up_ms = round(1000 * (self.clock() - self.t_key), 2)

    def _callback(self, transfer_p) -> int:
        """libhackrf's USB thread asks for transfer.buffer_length bytes."""
        t = transfer_p.contents
        want = int(t.buffer_length)
        with self.lock:
            if not self.feeding:
                # Unkeying: end the stream rather than queue more of the
                # picture behind the ramp. libhackrf resubmits nothing after
                # a non-zero return; what is already in flight carries the
                # ramp until tx_stop() cancels it.
                return 1
            # Which transfer has just completed tells us when the picture is
            # on the air: the first one filled entirely from the ring,
            # once it has gone to the device (its last byte in the 32 KiB
            # device buffer, so nearly all of it already played). Earlier
            # than that the device may still be playing start_tx's zeros,
            # and the gain must not be up for them.
            self.fills += 1
            if self.first_real is not None and self.fills - TRANSFERS >= self.first_real:
                if not self.radiating:
                    self.radiating = True
                    self.lock.notify_all()
            if self.first_real is None and self.n >= want:
                self.first_real = self.fills
            size = len(self.ring)
            k = min(want, self.n)
            k -= k % 2
            r = self.r
            k1 = min(k, size - r)
            dst = ctypes.addressof(t.buffer.contents)
            if k1:
                ctypes.memmove(dst, self.ring[r:r + k1].ctypes.data, k1)
            if k - k1:
                ctypes.memmove(dst + k1, self.ring[:k - k1].ctypes.data, k - k1)
            self.r = (r + k) % size
            self.n -= k
            self.lock.notify_all()
        if k < want:
            ctypes.memset(dst + k, 0, want - k)
            if self.primed and self.tx_on:
                self.underruns += 1
                self.underrun_samples += (want - k) // 2
        self.sent += want // 2
        t.valid_length = want
        return 0

    def tx_stop(self) -> None:
        # First wake any write() waiting for room in the ring, without ctl:
        # a writer blocked on a stalled stream must not hold up the unkey.
        t0 = self.clock()
        with self.lock:
            self.tx_on = False
            self.feeding = False
            self.lock.notify_all()
        # A second tx_stop() (the transmit thread's, after the engine's)
        # waits here until the first has finished, so neither returns while
        # the radio is still transmitting.
        with self.ctl:
            if not self.streaming:
                return
            self.streaming = False
            # The order, from libhackrf 2024.02 and its firmware (research
            # notes, section 19c):
            # 1. Ramp the gain down while the transfers already queued keep
            #    the picture going. A gain write is a control transfer the
            #    firmware applies at once, ahead of any queued samples, so
            #    the ramp is heard when it is sent. Ramping the samples
            #    instead could only start after the queue: four transfers of
            #    256 KiB, 26 ms at 20 MS/s and 262 ms at 2.
            # 2. hackrf_stop_tx: cancels the queue, then sets transceiver
            #    mode off, which puts the MAX2837 and the DAC in standby,
            #    disables the mixer and unpowers the TX amplifier. Ending the
            #    stream alone (the callback returning non-zero) does not:
            #    the firmware keeps transmitting zeros - the carrier from LO
            #    leakage - and older firmware repeated the last buffer.
            # 3. Amplifier off, so the next key-up starts quiet.
            # Not done: retuning the LO away (hackrf_set_freq re-enables the
            # mixer that step 2 turned off) or hackrf_set_antenna_enable
            # (that is the antenna port's DC bias, not an RF switch).
            try:
                self._ramp(0)
            except Exception:  # noqa: BLE001 - stopping the stream below matters more
                pass
            self.lib.hackrf_stop_tx(self.dev)
            self.lib.hackrf_set_amp_enable(self.dev, 0)
            self.stop_ms = round(1000 * (self.clock() - t0), 2)

    def _ramp(self, target: int) -> None:
        """TX VGA from where it is to `target` along a raised cosine in
        amplitude over RAMP_S: down at unkey (from the keyed gain towards
        nothing), up at key-up (from nothing towards the keyed gain), each
        clamped to the VGA's 0..47. Each pass writes the step the envelope
        has reached by now, so a slow bus skips steps rather than stretching
        the ramp: it ends at RAMP_S plus at most two control transfers (the
        step that straddles the end, and the last write) whatever the bus
        does. A ramp up stops where it is if tx_stop() has been asked for."""
        v0 = self.vga_set
        up = target > v0
        t0 = self.clock()
        while self.vga_set != target and (self.tx_on or not up):
            el = self.clock() - t0
            if el >= RAMP_S:
                break
            c = math.cos(math.pi * el / RAMP_S)
            a = 0.5 * (1.0 - c) if up else 0.5 * (1.0 + c)
            top = target if up else v0
            v = max(0, int(math.floor(top + 20.0 * math.log10(max(a, 1e-6)))))
            if (up and v > self.vga_set) or (not up and v < self.vga_set):
                if self._vga(v) != 0:
                    break
            else:
                self.sleep(RAMP_POLL_S)
        if self.vga_set != target and (self.tx_on or not up):
            self._check(self._vga(target), "setting TX gain")

    def status(self) -> dict:
        el = (time.monotonic() - self.t_start) if (self.t_start and self.tx_on) else 0.0
        return {"kind": "hackrf", "serial": self.serial or "first found", "fs": self.fs, "txLo": self.tx_lo,
                "tx": self.tx_on, "vga": vga_for(self.gain), "amp": self.amp, "range": RANGE,
                "txUnderruns": self.underruns, "txUnderrunMs": round(1000 * self.underrun_samples / self.fs, 3),
                "txRate": round(self.sent / el / 1e6, 2) if el > 0.5 else None,
                "txGainUpMs": self.gain_up_ms, "txStopMs": self.stop_ms, "notes": list(self.notes),
                "bbFilter": self.bb_filter,
                "radioLock": self.radio_lock.describe() if self.radio_lock is not None else {}}
