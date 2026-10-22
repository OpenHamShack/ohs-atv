"""The systems as objects: modulator, demodulator, encoder, decoder and plan
from the engine's settings, and the seam-free 0.16 s loop a static picture is
sent as. Shared by the transmitter and the simulator, so the simulated
stations are generated exactly as our own transmission is.

The work is ohsatv/narrow.py's, which knows the picture formats - standard
PAL and the narrow ones (`videoFormat`); this keeps the names the rest of
the program has always called."""
from __future__ import annotations

import numpy as np

from . import modem, narrow


def plan(fs: float, system: str, p: dict) -> dict:
    return narrow.plan(fs, system, p)


def make_demod(fs: float, system: str, p: dict, fmt: str | None = None, rx_shape=None):
    return narrow.make_demod(fs, system, p, fmt=fmt, rx_shape=rx_shape)


def make_mod(fs: float, system: str, p: dict, fmt: str | None = None, tx_shape=None):
    return narrow.make_mod(fs, system, p, fmt=fmt, tx_shape=tx_shape)


def make_encoder(fs: float, system: str, p: dict, colour: bool = True):
    return narrow.make_encoder(fs, system, p, colour)


def make_decoder(fs: float, system: str, p: dict, colour: str = "auto", fmt: str | None = None):
    return narrow.make_decoder(fs, system, p, colour, fmt=fmt)


def test_tone(n: int, tone_hz: float, amp: float, stereo: bool, start: int = 0) -> np.ndarray:
    """The transmitter's test sound, 48 kHz from sample `start`: a tone, or
    for NICAM the stereo test - the tone on the left and twice its
    frequency on the right (1 and 2 kHz), so the two channels and their
    separation can be told apart by ear and on a meter. Both are multiples
    of 6.25 Hz, so 0.16 s holds whole cycles of each."""
    t = (start + np.arange(n)) / modem.AUDIO_RATE
    left = amp * np.sin(2 * np.pi * modem.snap(tone_hz) * t)
    if not stereo:
        return left.astype(np.float32)
    right = amp * np.sin(2 * np.pi * modem.snap(2 * tone_hz) * t)
    return np.stack([left, right], axis=1).astype(np.float32)


def build_loop(fs: float, system: str, params: dict, rgb: np.ndarray, tone_hz: float = 1000.0,
               tone_amp: float = 0.5, tx_shape=None) -> np.ndarray:
    """One 0.16 s (4-frame) IQ loop of a picture with a tone, seam-free:
    the encoder's frames repeat every 4, the tone (a multiple of 6.25 Hz)
    has whole cycles in 0.16 s, and the modulators' phasors are periodic
    tables. The modulator's filters - and, for a format that carries its
    sound in the picture, the encoder's sound packing - are primed by
    running the last frame first and discarding it, so the loop starts in
    the state its own end leaves.

    Where the plan carries NICAM the tone is the stereo test (test_tone)
    and NICAM's 160 frames close their own phase (ohsatv/nicam.py)."""
    mod = make_mod(fs, system, params, tx_shape=tx_shape)
    enc = make_encoder(fs, system, params)
    enc.colour = enc.colour and mod.plan["colour"]
    n_a = int(round(0.04 * modem.AUDIO_RATE))
    stereo = getattr(mod, "nicam", None) is not None
    tone = test_tone(4 * n_a, tone_hz, tone_amp, stereo)
    auds = [tone[k * n_a:(k + 1) * n_a] for k in range(4)]
    enc.encode(rgb, 3, auds[3])                      # primes the encoder's sound packing
    frames = [enc.encode(rgb, k, auds[k]) for k in range(4)]
    mod_audio = (lambda k: None) if enc.audio_in_video else (lambda k: auds[k])
    mod.pos = -len(frames[3].reshape(-1))            # phasor tables count from the loop start
    mod(frames[3], mod_audio(3))
    ph0 = getattr(mod, "phase", 0.0)
    out = [mod(frames[k], mod_audio(k)) for k in range(4)]
    loop = np.concatenate(out).astype(np.complex64)
    if system == "fm":
        # Close the FM phase: the deviation integrated over the loop is not a
        # whole number of cycles, so spread the remainder over the loop as a
        # constant frequency offset (under 3.2 Hz). Without it the loop would
        # step in phase at its seam - a tick in the picture and the sound
        # every 0.16 s.
        step = (mod.phase - ph0 + np.pi) % (2 * np.pi) - np.pi
        k = np.arange(len(loop))
        loop = loop * np.exp(-1j * step * k / len(loop)).astype(np.complex64)
    return loop

