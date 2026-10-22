"""Pixels from a decoded page: the one renderer behind the editor, the
preview and the PNG export.

The page is drawn at the character generator's own resolution, 12 x 20
pixels a cell, 480 x 500 for the page - one pixel per dot of the rounded
SAA5050 matrix, so nothing is resampled until the window scales it. A
television stretches that to 4:3; `TV_ASPECT` is the horizontal stretch
the preview and the "as on a TV" PNG apply (52 us of active line holding
40 x 12 dots against 2 x 250 lines).

Everything is whole-array numpy: the glyph masks for all 1000 cells are
gathered at once, double height is a row-index remap of the gathered
masks, and flash and conceal are masks over them. A full page costs about
a millisecond, so the preview can redraw at the flash rate without caring.
"""
from __future__ import annotations

import numpy as np

from .charset import CELL_H, CELL_W, GLYPHS
from .decoder import DH_BOTTOM, DH_TOP, Decoded
from .page import COLS, ROWS

PALETTE = np.array([(0, 0, 0), (255, 0, 0), (0, 255, 0), (255, 255, 0), (0, 0, 255), (255, 0, 255),
                    (0, 255, 255), (255, 255, 255)], np.uint8)
# The "television picture" behind a newsflash or subtitle page outside its
# boxes: a dim grey, so the boxed text reads as inset into something.
VIDEO = np.array((58, 64, 74), np.uint8)
TV_ASPECT = 1.2
PAGE_W, PAGE_H = COLS * CELL_W, ROWS * CELL_H

# Flash: on for 3/4 of a second-long cycle, off for 1/4, as an SAA5050
# divides its flash clock.
FLASH_PERIOD = 1.0
FLASH_ON_FRACTION = 0.75

_TOP = np.repeat(np.arange(CELL_H // 2), 2)
_BOTTOM = _TOP + CELL_H // 2


def flash_on(t: float) -> bool:
    return (t % FLASH_PERIOD) < FLASH_PERIOD * FLASH_ON_FRACTION


def masks(d: Decoded) -> np.ndarray:
    """[25, 40, 20, 12] bool: the foreground dots of every cell, with double
    height applied."""
    m = GLYPHS[d.gset, d.code]                 # gather: [25,40,20,12]
    top = d.dh == DH_TOP
    bot = d.dh == DH_BOTTOM
    if top.any():
        m[top] = m[top][:, _TOP, :]
    if bot.any():
        m[bot] = m[bot][:, _BOTTOM, :]
    return m


def render_indices(d: Decoded, flash_phase_on: bool = True, reveal: bool = False,
                   mixed: bool = False) -> np.ndarray:
    """[500, 480] colour indices 0-7, or 8 for "television picture" (only in
    the mixed newsflash/subtitle view)."""
    m = masks(d)
    hide = np.zeros((ROWS, COLS), bool)
    if not reveal:
        hide |= d.conceal
    if not flash_phase_on:
        hide |= d.flash
    m &= ~hide[:, :, None, None]
    idx = np.where(m, d.fg[:, :, None, None], d.bg[:, :, None, None]).astype(np.uint8)
    if mixed:
        idx = np.where(d.box[:, :, None, None], idx, np.uint8(8))
        idx[0] = 8                              # the header is suppressed on these pages
    return idx.transpose(0, 2, 1, 3).reshape(PAGE_H, PAGE_W)


def to_rgb(idx: np.ndarray) -> np.ndarray:
    pal = np.vstack([PALETTE, VIDEO[None, :]])
    return pal[idx]


def render(d: Decoded, flash_phase_on: bool = True, reveal: bool = False, mixed: bool = False) -> np.ndarray:
    """[500, 480, 3] uint8 RGB."""
    return to_rgb(render_indices(d, flash_phase_on, reveal, mixed))


def stretch(rgb: np.ndarray, aspect: float = TV_ASPECT, scale: int = 1) -> np.ndarray:
    """Nearest-neighbour widen by `aspect` and enlarge by `scale`: the page
    as a 4:3 set would draw it, still with hard pixel edges."""
    h, w = rgb.shape[:2]
    nw = int(round(w * aspect * scale))
    xs = (np.arange(nw) * w // nw).clip(0, w - 1)
    ys = np.repeat(np.arange(h), scale)
    return rgb[ys][:, xs]
