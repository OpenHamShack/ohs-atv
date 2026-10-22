"""What a block of cells becomes on the clipboard, and what comes back from
it - without Qt, so the tests can check every form.

A copied block goes out in more than one form because the places it is
pasted want different things:

- **our own form** (`MIME_BLOCK`, JSON): the codes exactly, control codes
  and all, and where the block came from. Pasting it back here is lossless.
- **plain text**: what a person reads - the English set's characters as
  Unicode (£, ½, ← ...), control cells as spaces, trailing spaces trimmed.
  Pasting text in turns it back into codes line by line at the cursor.
- **an edit.tf link**: the block on an otherwise blank page, in the place
  it was cut from, so edit.tf and zxnet open it where it belongs. Pasting
  a link (as text) takes the cells it has that are not blank, as a block.
- **a picture**: the cells as the decoder draws them *in their row*. A
  block's look depends on the codes to its left (a mosaic is only a mosaic
  after a graphics colour), so the picture is cut from the decoded page,
  not drawn from the block alone, and is stretched to a set's 4:3 pixels.
"""
from __future__ import annotations

import io
import json

from . import charset, decoder, edittf, render
from .editor import Block
from .page import COLS, ROWS, Page, Subpage

MIME_BLOCK = "application/x-openhamshack-teletext-block"


def block_to_json(block: Block) -> bytes:
    return json.dumps({"format": "ohstxt-block/1", "top": block.top, "left": block.left,
                       "rows": [bytes(r).hex() for r in block.rows]}).encode()


def block_from_json(data: bytes) -> Block | None:
    try:
        d = json.loads(bytes(data).decode())
        rows = [bytes(b & 0x7F for b in bytes.fromhex(h)) for h in d["rows"]]
        return Block(rows, top=int(d.get("top", 1)), left=int(d.get("left", 0)))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def block_to_text(block: Block) -> str:
    lines = []
    for r in block.rows:
        lines.append("".join(charset.UNICODE.get(c, " ") if c >= 0x20 else " " for c in r).rstrip())
    return "\n".join(lines)


def text_to_block(text: str) -> Block:
    """Plain text as a block: one row per line, each as long as its own line
    (so pasting does not blank what is beside a short line). A character
    the set has no cell for becomes '?'; a tab is a space."""
    rows = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.replace("\t", " ")
        rows.append(bytes(charset.FROM_UNICODE.get(ch, 0x3F) for ch in line[:COLS]))
    while len(rows) > 1 and not rows[-1]:
        rows.pop()                    # a trailing newline is not a blank row to paste
    return Block(rows[:ROWS], top=0, left=0)


def block_to_edittf(block: Block, page_number: int = 0x100, black_fg: bool = False) -> str:
    sub = Subpage()
    for i, data in enumerate(block.rows):
        y = block.top + i
        for j, code in enumerate(data):
            x = block.left + j
            if 0 <= y < ROWS and 0 <= x < COLS:
                sub.rows[y][x] = code & 0x7F
    return edittf.from_page(Page(number=page_number), sub, black_fg=black_fg)


def edittf_to_block(url: str) -> Block | None:
    """The non-blank cells of an edit.tf link's page as a block (the
    smallest rectangle holding them), or None if it is not a link."""
    try:
        hp = edittf.parse(url)
    except edittf.HashError:
        return None
    used = [(y, x) for y in range(ROWS) for x in range(COLS) if hp.rows[y][x] != 0x20]
    if not used:
        return Block([b""], top=1, left=0)
    t = min(y for y, _ in used)
    b = max(y for y, _ in used)
    lft = min(x for _, x in used)
    rgt = max(x for _, x in used)
    return Block([bytes(hp.rows[y][lft:rgt + 1]) for y in range(t, b + 1)], top=t, left=lft)


def block_image(rows, rect: tuple[int, int, int, int], black_fg: bool = False, reveal: bool = False,
                scale: int = 2):
    """[h, w, 3] RGB of the cells in `rect` (top, left, bottom, right,
    inclusive) as the decoder draws them in the page `rows`."""
    d = decoder.decode(rows, black_fg=black_fg)
    rgb = render.render(d, reveal=reveal)
    t, l, b, r = rect
    from .charset import CELL_H, CELL_W
    crop = rgb[t * CELL_H:(b + 1) * CELL_H, l * CELL_W:(r + 1) * CELL_W]
    return render.stretch(crop, render.TV_ASPECT, scale)


def png_bytes(rgb) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="PNG")
    return buf.getvalue()
