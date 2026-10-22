"""Frequencies for display in the user's locale (OpenHamShack I18N.md 4.1).

The same rules as ohs-libs' `shack_i18n.fmt.frequency`, and the same
vectors (tests/data/i18n-format.json, copied from ohs-libs
spec/vectors/i18n/format.json) prove it: kHz below 30 MHz and MHz from
30 MHz up unless the field fixes the unit, fixed decimals with trailing
zeros kept so a readout never changes width, the locale's decimal separator,
never any digit grouping ("10,489.750" and "10.489,750" each read as the
other in half the world), and a no-break space before the unit.

The decimal separator comes from QLocale, as the contract asks of a Qt app,
rather than from shack_i18n's own table: the app does not carry ohs-libs at
run time, and QLocale is what the rest of the window already formats with.
Arithmetic is on integers of Hz, so rounding is exact (half away from zero)
and no float ever shows a stray digit.
"""
from __future__ import annotations

UNITS = {"Hz": 1, "kHz": 1_000, "MHz": 1_000_000, "GHz": 1_000_000_000}
_MAX_DECIMALS = {"Hz": 0, "kHz": 3, "MHz": 6, "GHz": 9}
_DEFAULT_DECIMALS = {"Hz": 0, "kHz": 1, "MHz": 3, "GHz": 6}
_AUTO_MHZ_FROM = 30_000_000
NBSP = " "


class FormatError(ValueError):
    """A value the formatter refuses; `code` matches the shared vectors."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _decimal_point(locale) -> str:
    from PyQt6.QtCore import QLocale
    loc = locale if isinstance(locale, QLocale) else QLocale(locale) if locale else QLocale()
    return loc.decimalPoint()


def frequency(hz: int, unit: str = "auto", decimals: int | None = None, locale=None) -> str:
    """`frequency(437_000_000)` is "437.000 MHz" in English, "437,000 MHz"
    in German. `locale` is a tag or a QLocale; None is the app's default
    (which i18n.install() sets)."""
    if isinstance(hz, bool) or not isinstance(hz, int):
        raise FormatError("not-a-number", f"{hz!r} is not a whole number of hertz.")
    if hz < 0:
        raise FormatError("negative-frequency", "A frequency cannot be negative.")
    if unit == "auto":
        unit = "MHz" if hz >= _AUTO_MHZ_FROM else "kHz"
    if unit not in UNITS:
        raise FormatError("bad-unit", f"{unit!r} is not one of {', '.join(UNITS)}.")
    if decimals is None:
        decimals = _DEFAULT_DECIMALS[unit]
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= _MAX_DECIMALS[unit]:
        raise FormatError("bad-decimals", f"{unit} shows 0 to {_MAX_DECIMALS[unit]} decimals, not {decimals!r}.")
    step = UNITS[unit] // 10 ** decimals          # Hz per last shown digit
    shown = (hz + step // 2) // step              # half away from zero (hz >= 0)
    whole, frac = divmod(shown, 10 ** decimals)
    text = str(whole) + (_decimal_point(locale) + str(frac).zfill(decimals) if decimals else "")
    return text + NBSP + unit


def number(value: float, decimals: int | None = None, locale=None) -> str:
    """A plain number for a readout beside a unit that is not a frequency
    of the radio - a sample rate in MS/s, a span in MHz on an axis, a gain
    in dB: `decimals` fixed, or None for the shortest form (Python's `:g`,
    which these readouts showed before they were translated), with the
    locale's decimal separator and never any grouping, for frequency()'s
    reasons. `number(12.5)` is "12.5" in English and "12,5" in German."""
    text = f"{value:g}" if decimals is None else f"{value:.{decimals}f}"
    return text.replace(".", _decimal_point(locale))
