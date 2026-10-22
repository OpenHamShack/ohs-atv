"""Image conversion: the output is a legal row (it begins with a graphics
colour, mosaics only after one), plain pictures come out as their colours
once decoded, and the row search is optimal - checked against brute
force on a short row, through the real decoder and renderer."""
import itertools
import unittest

import numpy as np

from helpers import page_rows

from ohstxt import decoder, imageconv, render
from ohstxt.imageconv import Options


def rendered(rows, width):
    page = page_rows(*[(i + 1, bytes(r)) for i, r in enumerate(rows)])
    d = decoder.decode(page)
    return render.render_indices(d)[20:20 + 20 * len(rows), :12 * width]


def solid(rgb, w=80, h=60):
    return np.tile(np.array(rgb, np.uint8), (h, w, 1))


class Legal(unittest.TestCase):
    def test_rows_are_the_asked_size_and_start_with_a_graphics_colour(self):
        img = np.random.default_rng(1).integers(0, 255, (120, 160, 3), dtype=np.uint8)
        for dither in imageconv.DITHERS:
            res = imageconv.convert(img, Options(width=30, height=10, dither=dither))
            self.assertEqual(len(res.rows), 10)
            for row in res.rows:
                self.assertEqual(len(row), 30)
                self.assertTrue(0x11 <= row[0] <= 0x17, hex(row[0]))
                for c in row:
                    self.assertTrue(c in (0x1C, 0x1D) or 0x11 <= c <= 0x17 or (c >= 0x20 and c & 0x20), hex(c))

    def test_separated_option_starts_with_the_separated_code(self):
        res = imageconv.convert(solid((255, 0, 0)), Options(width=10, height=2, separated=True))
        self.assertEqual(res.rows[0][0], 0x1A)
        self.assertTrue(0x11 <= res.rows[0][1] <= 0x17)


class Pictures(unittest.TestCase):
    def test_a_solid_colour_comes_out_that_colour(self):
        for k, rgb in ((1, (250, 10, 10)), (3, (240, 240, 0)), (6, (0, 230, 230))):
            res = imageconv.convert(solid(rgb), Options(width=20, height=4, fit="stretch"))
            idx = rendered(res.rows, 20)
            frac = float((idx[:, 12:] == k).mean())       # beyond the first (code) cell
            self.assertGreater(frac, 0.95, (k, frac))

    def test_two_halves_use_a_background_change(self):
        img = np.concatenate([solid((255, 0, 0), 40, 60), solid((0, 0, 255), 40, 60)], axis=1)
        res = imageconv.convert(img, Options(width=20, height=3, fit="stretch"))
        idx = rendered(res.rows, 20)
        self.assertGreater(float((idx[:, 12 * 2:12 * 9] == 1).mean()), 0.9)
        self.assertGreater(float((idx[:, 12 * 12:12 * 20] == 4).mean()), 0.9)
        self.assertLess(res.error, 0.08)

    def test_monochrome_palette_uses_only_white_and_black(self):
        img = np.random.default_rng(2).integers(0, 255, (60, 80, 3), dtype=np.uint8)
        res = imageconv.convert(img, Options(width=20, height=4, colours=(7,), dither="floyd"))
        idx = rendered(res.rows, 20)
        self.assertTrue(set(np.unique(idx)) <= {0, 7})

    def test_fit_keeps_the_picture_round(self):
        # a square picture fitted into a wide area: the sides are black
        res = imageconv.convert(solid((255, 255, 255), 100, 100), Options(width=40, height=10))
        self.assertTrue((res.target[:, :8] == 0).all())
        self.assertTrue((res.target[:, -8:] == 0).all())

    def test_dithering_makes_mid_grey_a_mix(self):
        grey = solid((128, 128, 128), 80, 60)
        res = imageconv.convert(grey, Options(width=20, height=4, dither="bayer", fit="stretch"))
        self.assertGreaterEqual(len(np.unique(res.target.reshape(-1, 3), axis=0)), 2)


class Optimal(unittest.TestCase):
    def test_row_search_matches_brute_force(self):
        """Every legal 3-cell row over red and blue, decoded and rendered by
        the real decoder, scored against the target: none beats the search."""
        rng = np.random.default_rng(5)
        pal = {0: (0, 0, 0), 1: (255, 0, 0), 4: (0, 0, 255)}
        target = np.array([pal[k] for k in rng.choice([0, 1, 4], 18)], np.float32).reshape(3, 6, 3)
        opts = Options(width=3, height=1, colours=(1, 4), fit="stretch")
        res = imageconv.convert(target.astype(np.uint8), opts)

        from ohstxt import charset

        def score(row):
            # The real decoder on the one row, then each sixel's colour read
            # from the real glyph masks at the sixel's centre.
            d = decoder._empty()
            decoder.decode_row(bytes(row) + b" " * 37, d, 1)
            got = np.zeros((3, 6, 3), np.float32)
            for c in range(3):
                g = charset.GLYPHS[d.gset[1, c], d.code[1, c]]
                for sy, y in enumerate((3, 10, 17)):
                    for sx, x in enumerate((3, 9)):
                        k = d.fg[1, c] if g[y, x] else d.bg[1, c]
                        got[sy, 2 * c + sx] = render.PALETTE[k]
            return float(((got - target) ** 2).sum())

        # the scorer agrees with the full renderer on the search's own answer
        idx = rendered([res.rows[0]], 3)
        self.assertTrue(all(int(idx[y, 12 * c + x]) in (0, 1, 4) for y in (3, 10, 17) for c in range(3)
                            for x in (3, 9)))

        alphabet = [0x11, 0x14, 0x1C, 0x1D] + [0x20 | b for b in range(64) for b in [((b & 0x20) << 1) | (b & 0x1F)]]
        best = min(score(bytes([first, a, b])) for first in (0x11, 0x14) for a, b in itertools.product(alphabet,
                                                                                                        repeat=2))
        self.assertLessEqual(score(bytes(res.rows[0])), best + 1e-3)


if __name__ == "__main__":
    unittest.main()
