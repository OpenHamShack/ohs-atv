"""The device interface every radio implements (DESIGN.md "Devices")."""
from __future__ import annotations

import numpy as np


class DeviceError(RuntimeError):
    """A device problem with a sentence the operator can act on."""


class Device:
    kind = "none"
    can_tx = False
    # A simulated device: nothing it "transmits" leaves the machine, so the
    # transmit guard's refusals are shown but do not stop it (DESIGN.md rule 3).
    real = True
    fs = 0.0
    # How the host reaches the radio ("usb", "usb-net", "ethernet", "sim") and
    # the complex sample rate that link is reported to sustain for receive
    # (docs/atv-research.md 5.1), or None when unknown.
    link = "unknown"
    link_msps: float | None = None
    # The radio's own fixed filtering, which the modems undo
    # (devices/ad936x.py): an object giving |H(f)| for f in Hz from the LO,
    # and its correction(). None: taken as flat - the simulator, and a
    # HackRF, whose MAX2837 filter has not been measured here.
    rx_response = None
    tx_response = None
    # The radio lock (devices/shared.py). The engine sets the first three
    # before open(): whether to take it, the marker's note (frequency, mode,
    # format), and what to call if it is lost. A device with nothing to lock
    # (the simulator, a recording) ignores them.
    lock_radio = False
    lock_note = ""
    on_lock_lost = None
    radio_lock = None           # the RadioLock while held
    lock_lost = ""              # why it was lost, if it was
    # Set when the lock is lost: the radio is somebody else's now, so close()
    # puts nothing back (the Pluto's FIR) - that would be writing to their
    # radio. Unkeying is still done; that is never wrong.
    leave_as_is = False

    def open(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def freq_range(self) -> tuple[float, float] | None:
        """(lo, hi) Hz the LOs tune, as measured from the radio at open();
        None if unknown."""
        return None

    def duplex(self) -> bool:
        """True if receive can keep streaming while transmitting at fs."""
        return False

    def set_rx_lo(self, hz: float) -> None:
        raise NotImplementedError

    def read(self, n: int) -> np.ndarray:
        """complex64, blocks until n samples are available."""
        raise NotImplementedError

    rx_lo: float | None = None

    @property
    def read_lo(self) -> float | None:
        """The receive LO the samples the last read() returned were taken
        at, or None when that is not certain. A radio that buffers between
        its converter and the host (devices/pluto.py) delivers samples taken
        at the old LO for a while after a retune; on 5 October 2026 the
        spectrum feed labelled such frames with the new LO, and a transmit
        guard took the vision carrier, seen through the old LO, for a new
        signal (docs/atv-research.md 21a). Here, with no such buffering
        (the simulator makes its samples when they are read, a recording
        plays where it is told), it is the LO now."""
        return self.rx_lo

    def set_tx_lo(self, hz: float) -> None:
        raise DeviceError("This device cannot transmit")

    def set_tx_gain(self, db: float) -> None:
        raise DeviceError("This device cannot transmit")

    def tx_start(self) -> None:
        raise DeviceError("This device cannot transmit")

    def tx_stop(self) -> None:
        pass

    def write(self, iq: np.ndarray) -> None:
        """complex64, |x| <= 1; blocks (paced by the device)."""
        raise DeviceError("This device cannot transmit")

    def status(self) -> dict:
        return {"kind": self.kind}
