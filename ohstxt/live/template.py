"""Live-page tokens: where a page's cells are filled from data.

A token is written in the page itself, in the cells it will fill:

    {name}      the value, left-aligned in the token's cells
    {>name}     right-aligned (numbers in a column)
    {name~~~~}  tildes widen the field without changing the name

The field is exactly as wide as the token - braces, '>' and tildes
included - so a template is laid out in the ordinary editor, one cell per
character, and a filled page has every column where the template put it. A
value longer than its field is cut; a shorter one is padded with spaces.
A name is lower case: a letter, then letters, digits and '_', then
optionally '.part' segments (letters, digits, '_' and '-'): `sfi`,
`solar.sfi`, `dx.1`, `dx.1.call`, `beacons.3.status`.

On a set the braces are ¼ and ¾ (0x7B and 0x7D in the English set) and the
tilde ÷, so no page that means its own text is likely to hold a token by
accident; anything that does not match the grammar is left as it is.
Control codes cannot be inside a token, so a field never spans a colour
change - the template puts the colour before the token.

Values are text. Characters the set has no cell for become '?'.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .. import charset
from ..page import COLS

TOKEN = re.compile(r"\{(>?)([a-z][a-z0-9_]*(?:\.[a-z0-9_-]+)*)(~*)\}")


@dataclass(frozen=True)
class Token:
    row: int
    col: int
    width: int
    name: str
    right: bool = False


def _row_text(codes) -> str:
    """A row's codes as one character each, control codes as NUL - so a
    token can never run across one."""
    return "".join(chr(c) if 0x20 <= c < 0x7F else "\0" for c in codes)


def tokens_in_row(codes, row: int = 0) -> list[Token]:
    return [Token(row, m.start(), m.end() - m.start(), m.group(2), m.group(1) == ">")
            for m in TOKEN.finditer(_row_text(codes))]


def tokens_in(rows) -> list[Token]:
    out = []
    for r, codes in enumerate(rows):
        out.extend(tokens_in_row(codes, r))
    return out


def names_in(text: str) -> list[str]:
    """Token names in a string (a row read back as text by Subpage.text,
    where braces are already ¼ and ¾ - both spellings are looked for)."""
    t = text.replace("¼", "{").replace("¾", "}").replace("÷", "~").replace("#", "_")   # 0x5F reads back as #
    return [m.group(2) for m in TOKEN.finditer(t)]


def fit(value: str, width: int, right: bool = False) -> bytes:
    codes = bytes(charset.FROM_UNICODE.get(ch, 0x3F) for ch in str(value).replace("\n", " "))[:width]
    return codes.rjust(width, b" ") if right else codes.ljust(width, b" ")


def fill_row(codes, values) -> tuple[bytearray, list[str]]:
    """The row with every token replaced by values(name) (None: the field
    is left as dashes, so a page with no data yet still reads as a page).
    Returns the row and the names it used."""
    out = bytearray(codes)
    used = []
    for tok in tokens_in_row(codes):
        v = values(tok.name)
        used.append(tok.name)
        text = "-" * min(tok.width, 2) if v is None else v
        out[tok.col:tok.col + tok.width] = fit(text, tok.width, tok.right)
    return out[:COLS], used
