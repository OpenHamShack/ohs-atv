"""Waterfall colour maps, computed in code as 256-entry 0xAARRGGBB tables.

Each palette is a handful of control points interpolated in *linear* light
and converted back to sRGB, which keeps the middle of each ramp from going
muddy (interpolating sRGB directly darkens every blend). The first entry is
near-black in all of them: the noise floor should be quiet, so signals are
what the eye finds.

The table is applied with one numpy fancy-index per waterfall row
(`LUT[indices]`) straight into the image buffer: no per-pixel Python, which
is what keeps the waterfall affordable on a Raspberry Pi.
"""
from __future__ import annotations

import numpy as np

# name -> [(position 0..1, "#rrggbb"), ...]
STOPS = {
    "Ember": [(0.00, "#04050a"), (0.18, "#1b0b3a"), (0.38, "#5a1270"), (0.56, "#b3264f"),
              (0.74, "#f0602a"), (0.88, "#fbb33a"), (1.00, "#fff6d6")],
    "Glacier": [(0.00, "#03060b"), (0.22, "#0a2240"), (0.45, "#0f5a7a"), (0.65, "#1ea3a8"),
                (0.82, "#7fe0c8"), (1.00, "#f2fffb")],
    "Classic": [(0.00, "#020414"), (0.20, "#0b1f7a"), (0.40, "#0f7fd0"), (0.55, "#22c7a0"),
                (0.70, "#b8e04a"), (0.85, "#ffb020"), (1.00, "#ff3b2f")],
    "Phosphor": [(0.00, "#020604"), (0.30, "#06301a"), (0.60, "#14904a"), (0.82, "#5cf08a"),
                 (1.00, "#eaffef")],
}
NAMES = list(STOPS)


def _lin(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _srgb(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(np.clip(c, 0, None), 1 / 2.4) - 0.055)


def _hex(h: str) -> np.ndarray:
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], np.float64) / 255.0


def lut(name: str) -> np.ndarray:
    """256 x uint32 (0xFFRRGGBB), for QImage.Format.Format_RGB32."""
    stops = STOPS.get(name) or STOPS[NAMES[0]]
    pos = np.array([p for p, _ in stops])
    cols = _lin(np.array([_hex(h) for _, h in stops]))
    x = np.linspace(0.0, 1.0, 256)
    rgb = np.stack([np.interp(x, pos, cols[:, k]) for k in range(3)], axis=1)
    rgb = np.clip(np.round(_srgb(rgb) * 255.0), 0, 255).astype(np.uint32)
    return (0xFF000000 | (rgb[:, 0] << 16) | (rgb[:, 1] << 8) | rgb[:, 2]).astype(np.uint32)


def gradient_stops(name: str, n: int = 8) -> list[tuple[float, int]]:
    """A few (position, 0xRRGGBB) samples of a palette, for drawing its swatch."""
    table = lut(name)
    return [(i / (n - 1), int(table[int(i * 255 / (n - 1))]) & 0xFFFFFF) for i in range(n)]
