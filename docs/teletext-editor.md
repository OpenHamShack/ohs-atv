> Copied from OpenHamShack Teletext (`~/openhamshack-teletext`, commit 1cda694)
> when the editor was folded into ATV as `ohstxt/`. Paths below are that
> repository's: its `tests/test_*.py` are this one's `tests/test_txt_*.py`,
> and its packaging is now ATV's (`openhamshack-atv --teletext`). Keep this file
> as the editor's contract and change it with the editor.

# OpenHamShack Teletext — design

A **broadcast teletext page editor**: one native desktop application that
makes and edits World System Teletext pages (ETS 300 706 **Level 1**, 40 ×
25 characters) for **OpenHamShack ATV** to transmit with analogue amateur
television. It is a member of the OpenHamShack family and keeps its habits — one
process, an engine that never imports Qt, a hand-painted PyQt6 window,
copied (not shared) look and controls — and like its siblings it runs on
Linux first (Debian 13).

The pages go to air through OpenHamShack ATV's teletext insertion, which another
agent is building in parallel. **The contract between the two programs is
files**: a folder of MRG `.tti` files, written exactly as vbit2 reads them
(OpenHamShack ATV watches the folder and sends a changed page within a carousel
turn); an **SRT** file for the page-888 subtitles (the subset in "Subtitles:
the SRT subset" below); and the **.t42** packets and vbit2-dialect `.tti`
that OpenHamShack ATV saves from what it receives, which this program opens
("Received material"). Nothing else passes between them: no shared code, no
socket, no index file. A live page's sidecar (`P301.live.json`) is ours and
OpenHamShack ATV never needs to read it: the `.tti` beside it is always a
complete page.

## Shape

```
  keyboard, mouse ──► Qt window (ohstxt.ui) ─────► ohstxt.editor.Document ──► Page / Subpage (bytes)
                        │  editor grid, preview,          (undo, cursor,          │
                        │  panels, dialogs                 sixel rules)           │
                        │                                                         ▼
                        └──── draws only through ── ohstxt.decoder → ohstxt.render ◄─┘
                                                                                  │
   service folder of .tti ◄──── ohstxt.tti (read/write, vbit2's rules) ◄───────────┤
   .t42 packets      ◄──────── ohstxt.t42 (Hamming 8/4, parity, X/27/0, CRC) ◄─────┤
   edit.tf / zxnet links ◄────► ohstxt.edittf ◄────────────────────────────────────┤
   picture ──► ohstxt.imageconv (Pillow, row-by-row search) ──► rows ──────────────┤
   clipboard ◄────────────────► ohstxt.clipboard (blocks: ours, text, link, PNG) ◄─┤
   .t42 capture ──► ohstxt.receive (Hamming, vote across repeats) ──► pages ───────┤
   station.json ──► ohstxt.station, ohstxt.templates (new pages for a station) ─────┤
   service folder ──► ohstxt.check (the service checker; CLI too)                  │
   P301.live.json + data sources ──► ohstxt.live (runner: rewrites P301.tti) ──────┘
   .srt ◄──────► ohstxt.subtitles (cues ↔ page 888 rows)
```

- `python3 -m ohstxt [FOLDER | FILE.tti] [--page NNN]` opens the window.
  `python3 -m ohstxt --t42 OUT FOLDER` and `--png OUT FILE.tti` convert
  without a window (scripts, and the packaging test).
- `python3 -m ohstxt.check FOLDER` checks a service; `python3 -m ohstxt.live
  FOLDER` keeps its live pages filled (a systemd user unit runs it);
  `python3 -m ohstxt.receive CAPTURE.t42 [--out FOLDER]` turns a capture
  back into pages. None of them needs Qt.
- Engine: `numpy` and `Pillow` (picture import, PNG export). Window:
  `python3-pyqt6`. Nothing is fetched, nothing is vendored: the character
  set is drawn in code (`ohstxt/charset.py`), the icons are paths.
- Window preferences: `$OHSTXT_UI_CONF`, else
  `~/.config/openhamshack-teletext/ui.json`. The default service folder is
  `$OHSTXT_DATA/service`, else `~/.local/share/openhamshack-teletext/service/`,
  created with the sample pages on first run.
- The engine never imports Qt; the window never decodes a page itself.

## The page model — `ohstxt/page.py`

A **page** (`Page`) is a number (0x100–0x8FF, magazine 1–8), a description
and one or more **subpages**. A subpage (`Subpage`) is what a set holds at
once: 25 rows of 40 seven-bit codes (row 0 the header, rows 1–23 the page,
row 24 the Fastext labels), a subcode, the page-status word, a cycle time
(seconds, or magazine cycles), a region, and six Fastext links. Everything
is stored as the bytes that will be written: a colour *is* a byte in a cell,
and an editor that kept anything richer would have to reinvent the decoder
to save.

Kept verbatim and written back unchanged, so a page from a Level 2.5 editor
survives being opened and saved here: rows 25–29 (X/25, X/26–X/28 as OL
lines), and any line this program does not understand (DS, SP, MS, RD, PF,
unknown commands), in the place it came from.

A **service** is a folder of `.tti` files, one page per file. The folder
listing *is* the service — it is what vbit2 is pointed at, and what OpenHamShack
ATV's insertion reads — so there is no index of our own to drift out of
step. New pages are named `P<mpp>.tti`; a page from elsewhere keeps its
file name. Two files claiming one page number are shown in the service list
(an inserter would transmit one and hide the other).

## The .tti contract — `ohstxt/tti.py`

The format is MRG Systems' TTI v1.1 (zxnet.co.uk/teletext/documents/
ttiformat.pdf) **as vbit2's `File::LoadTTI` reads it** (vbit2
`filemonitor.cpp`, `ttxline.cpp`, read from source in October 2026).
Where MRG's document is silent, vbit2 decides, because vbit2 is what
transmits these files.

### What is written

One page per file, one subpage block after another, CRLF line ends, no
blank lines, 7-bit ASCII:

```
DE,<description>
<head lines kept from the file it came from, if any>
PN,<mpp><ss>           ss = 00 for a single page, else 01, 02 ... (index mod 100, decimal)
SC,<subcode, 4 hex>    0000 for a single page; carousels 0001-0009, 0010 ... 0079 (Annex A.1)
PS,<status, 4 hex>     8000 = transmit; table below
CT,<n>,<T|C>           per subpage, after its PN (T = seconds, C = magazine cycles), 1-255
RE,<h>                 only if the region is not 0
<unknown lines kept inside this subpage>
OL,<row>,<text>        rows 0-24 that are not all spaces; row 0 only if the header row has content
OL,<25-29>,<verbatim>  enhancement rows exactly as read
FL,rrr,ggg,yyy,ccc,lll,iii   six hex page numbers, 8FF for none; only if the subpage has links
```

`OL` text: a code below 0x20 is written **ESC (0x1B) followed by the code
+ 0x40** (the "Viewdata" escape); every other code as itself (0x20–0x7F).
So alpha red is `ESC A`, double height `ESC M`, graphics white `ESC W`,
ESC itself `ESC [`. The writer can also produce MRG's own form (code +
0x80, `escape="high"`), but does not by default: that is not 7-bit clean,
and vbit2, wxTED, edit.tf and zxnet all read the ESC form.

Files are written to a temporary name beginning with `.` and ending
`.tti.tmp` in the same folder, then renamed over the target, so an
inserter watching the folder never reads half a page.

### What is read

- Lines end CRLF, LF or CR; a UTF-8 BOM is skipped; bytes are taken as
  Latin-1, then every code modulo 0x80. Blank lines are skipped.
- `PN`: starts a new subpage. The page number is the first three hex digits
  of the **first** PN in the file; the `ss` digits are ignored (vbit2
  ignores them too and renumbers carousels itself).
- `CT,n,T|C`, `PS,hhhh`: before the first PN they are defaults for every
  subpage; after a PN they belong to that subpage **and carry forward** to
  the subpages after it (vbit2 keeps them in variables that new subpages
  inherit). `RE,h` applies only to the subpage it is in (vbit2 shadows its
  variable, so RE does not carry), and before any PN it is the default.
  `CT` clamps n to 1–255; anything but `C` is timed.
- `SC,hhhh`: the current subpage's subcode (masked to 0x3F7F).
- `OL,n,text`: decoded as vbit2's `TTXLine` does — ESC then the next
  character modulo 0x40 is a control code; any other byte below 0x20 **ends
  the row** (so a stray CR is harmless); everything else modulo 0x80. Short
  rows are padded with spaces, long ones cut at 40. Rows 0–24 are page
  rows; 25–29 are kept verbatim. An `OL` before any `PN` starts the first
  subpage (vbit2 would drop it; a lone page with no PN is kept here).
- `FL`: six hex values; 0, anything below 0x100 or above 0x8FF means "no
  link" (stored as 8FF). Missing values are "no link".
- `DE`: the rest of the line, commas and all.
- Anything else is kept verbatim (before the first PN: in the page's head;
  after: in the current subpage).
- A file with no command at all is refused as "not a .tti page".

MRG notes that double height (0x0D) "is often replaced with 0x10" by some
writers. That is *not* undone here: 0x10 is graphics black, and guessing
would corrupt every page that means it.

### Page status (PS)

The bit positions are MRG's; vbit2 maps them into the header as shown.

| PS bit | value | meaning | in the header |
| --- | --- | --- | --- |
| 15 | 8000 | transmit the page | (not sent) |
| 14 | 4000 | C4 erase page | S2 byte, bit 4 |
| 0 | 0001 | C5 newsflash | S4 byte, bit 3 |
| 1 | 0002 | C6 subtitle | S4 byte, bit 4 |
| 2 | 0004 | C7 suppress header | C7–C10 byte, bit 1 |
| 3 | 0008 | C8 update | bit 2 |
| 4 | 0010 | C9 interrupted sequence | bit 3 |
| 5 | 0020 | C10 inhibit display | bit 4 |
| 6 | 0040 | C11 serial magazine | **always sent 0** (vbit2 transmits parallel) |
| 7–9 | 0380 | C12–C14 national option (000 English) | C11–C14 byte, bits 2–4 |

### Verified against vbit2 itself

vbit2 was built from source and given the sample service as written by
this program; it transmitted every row, every link and the three carousel
subpages with subcodes 0001–0003 exactly as meant. A capture of what it
sent is `tests/fixtures/vbit2-sample.t42`, and `tests/test_t42.py` checks
rows, links and the page CRC against it on every run.

## The decoder — `ohstxt/decoder.py`, `ohstxt/render.py`

**One decoder draws everything**: the editor grid, the preview, the PNG
export, the service thumbnails and the image-import preview. So what the
editor shows is what a set shows. It works as a Level 1 decoder does
(ETS 300 706 §12.2), a row at a time from a reset state (alpha white on
black, contiguous, no hold, steady, normal height, no box) at the left:

| Set-At (in the code's own cell) | Set-After (from the next cell) |
| --- | --- |
| 0x09 steady · 0x0C normal height · 0x18 conceal · 0x19 contiguous · 0x1A separated · 0x1C black background · 0x1D new background (= foreground) · 0x1E hold graphics | 0x00–07 alpha colours · 0x08 flash · 0x0A end box · 0x0B start box · 0x0D double height · 0x10–17 graphics colours · 0x1B ESC · 0x1F release graphics |

- A control cell shows a space in the current background, or under hold
  graphics in graphics mode **the held mosaic**: the last mosaic character
  (bit 5 set) shown in the row, in the contiguous/separated style it was
  shown in, in the foreground in force *before* any Set-After change in that
  cell. The held mosaic resets to space at the start of the row, on a change
  between alpha and graphics, and on a change of size — not on a repeated
  colour, not on hold/release.
- 0x40–0x5F in graphics mode are capitals ("blast-through") and never become
  the held mosaic.
- An alpha or graphics colour code ends conceal (Set-After).
- Double height takes the row below: that row's own codes are not shown; it
  shows the lower halves of the upper row's double-height characters, and a
  space in the upper cell's background under normal-height ones. Double
  height has no effect in rows 0, 23 and 24.
- 0x00 and 0x10 (black foreground) do nothing at Level 1, as on an SAA5050;
  View ▸ Black foreground makes them act as Level 2.5 sets do (edit.tf's
  "render black" flag, bit 3 of its character-set digit, sets the same).
- Boxes matter only on newsflash and subtitle pages (C5, C6): there the
  preview shows the boxed cells over a grey stand-in for the picture, and no
  header.

The renderer draws at the character generator's resolution, 12 × 20 dots a
cell, 480 × 500 a page, with whole-array numpy (about 2 ms a page). On
screen and in the "as a set shows it" PNG the dots are 1.2 times as wide as
high, as a 4:3 set draws them. Flash is 3/4 on, 1/4 off, a one-second cycle;
the editor shows it steady, the preview flashes.

### The character set — `ohstxt/charset.py`

No font file and nothing fetched. G0 is 96 hand-drawn 5 × 9 dot patterns in
the SAA5050's proportions (capitals seven dots, lower case five with true
descenders), placed in a 6 × 10 cell, doubled to 12 × 20 and **character
rounded** as the SAA5050 does it (where two dots touch only at a corner,
the half-dots either side are filled). The English national option: 0x23 £,
0x24 $, 0x40 @, 0x5B ←, 0x5C ½, 0x5D →, 0x5E ↑, 0x5F #, 0x60 ―, 0x7B ¼,
0x7C ‖, 0x7D ¾, 0x7E ÷, 0x7F ■. Typing `#` gives 0x5F (where the English
set keeps it); `£` gives 0x23.

G1 mosaics are generated: a cell is 2 × 3 sixels, columns 6 + 6 dots, rows
6 + 8 + 6 (the SAA5050's 3 + 4 + 3 lines); separated graphics take 2 dots
off the left and bottom of each sixel. Sixel bits: top-left 0x01, top-right
0x02, middle 0x04 / 0x08, bottom-left 0x10, bottom-right **0x40** (0x20 is
the bit that makes the code a mosaic).

### The header row — `ohstxt/header.py`

Row 0 belongs to the inserter, not the page: vbit2 sends its own header
(from vbit.conf, or row 0 of page mFF of each magazine), and the set writes
the page number over columns 0–7. So `OL,0` is kept and round-tripped, the
editor edits columns 8–39 only and shows the template unexpanded (a
template's `%%#` shows as `%%£`, since 0x23 *is* £ on a set), and the
preview shows what a viewer sees: ` P100   ` then the template with vbit2's
substitutions — each token replaced at its first occurrence, in vbit2's
order: `%%#` page, `%%a` day, `%%b` month, `%d`, `%e`, `%m`, `%y`, `%H`,
`%M`, `%S`. A page with C7 set shows no header.

## .t42 — `ohstxt/t42.py`

42 bytes a packet: MRAG (two Hamming 8/4 bytes: magazine + row bit 0, then
row bits 1–4), then 40 bytes. Rows 1–24 are 40 characters with **odd
parity**. The header carries eight Hamming 8/4 bytes — page units, page
tens, S1, S2 + C4, S3, S4 + C5 C6, C7–C10, C11–C14 — then the 32 header
characters (columns 8–39, template expanded) with parity. Links go in
**X/27/0**: designation 0, six links of six Hamming bytes (page units,
tens, S1, S2 + M1, S3, S4 + M2 M3, the magazine relative to the page's,
subcode 3F7F), link control 0xF (row 24 shown), and the page **CRC** as
vbit2 computes it: x¹⁶ + x¹² + x⁹ + x⁷ + 1 over header columns 8–31 and
rows 1–25 **as transmitted, parity bits included** (the first version left
parity out and disagreed with every packet vbit2 sent). Byte values are the
transmitted bytes with the first-sent bit least significant, as every .t42
reader expects; the Hamming table is ETS 300 706's (0x15, 0x02, 0x49 …).

A page is header, all 24 rows (blank ones too, so a set never keeps a row
of the previous subpage), then X/27/0 if the subpage has links. A carousel
is its subpages in order, each with C4 erase set. A service export is every
page in page order. `read_packets` decodes a .t42 back (Hamming with
single-error correction and double-error detection, parity checked) for the
tests and for checking a file before it goes to air.

## edit.tf and zxnet links — `ohstxt/edittf.py`

The "teletext page hashstring" (github.com/rawles/edit-tf wiki): after `#`,
colon-separated parts. One hex digit — low three bits the SAA505x set (0,
English), bit 3 "render black foreground" — then the 25 × 40 seven-bit
codes packed most significant bit first, row by row, as 1167 base64url
digits (two padding bits unused; old 24-row links of 1120 digits are read
with a blank row 24). Then `key=value` parts in any order: `PN` (three hex
digits), `SC` (hex), `PS` (MRG PS bits; bit 15 ignored), `X270` (six
`MPPSSSS` groups and the link-control nibble; magazines absolute). Keys
this program does not use (`X25`, `X26`, `X280`, `X284`, `zx`…) are kept on
the subpage and written back unchanged on export, as the format asks.
Importing replaces the current subpage's rows (and status and links if the
link has them); the page keeps its own number. Export gives an
`https://edit.tf/#…` link and, at a click, the same hash on zxnet's editor.

## Image import — `ohstxt/imageconv.py`

A row of teletext has no colour per pixel: colour is state along the row,
changed only by a control code that **costs a whole cell** and shows as
background. So each character row is chosen as a whole by dynamic
programming (Viterbi) over its cells: the state is (foreground,
background); at each cell the row either draws a mosaic in the current
colours (each sixel taking the nearer of the two), or spends the cell on a
graphics-colour, new-background or black-background code. The cost is the
squared colour error of every sixel, including the ones a control cell
blanks. Every row starts as a decoder starts it — alpha white on black — so
its first cell is a graphics-colour code. A test compares the search with
brute force over every legal three-cell row, through the real decoder.

Dithering is applied first and changes what the search aims at: none (each
sixel's own colour), Floyd–Steinberg or 4 × 4 ordered, to the allowed
colours (all eight, white only, red/yellow/white, blue/cyan/white). The
teletext colours are the RGB cube's corners, so with all eight both dithers
are per-channel thresholds. Options: area (top row, rows, left column,
columns), fit / fill / stretch (a sixel is 1.08 times as wide as high on a
set, so circles stay round), background changes on or off, separated
graphics (costs the first cell), brightness and contrast. The dialog shows
the page **as the decoder will draw it** — colour-code cells and all —
before anything is committed, and Commit is one undo step.

## Editing — `ohstxt/editor.py`

Everything the window does to a page goes through a `Document`: the
cursor, type-over, control codes, cells and rows inserted and deleted,
sixels, subpages, cycle times, links and labels. Undo keeps whole-page
snapshots (200 deep), so it cannot be wrong about what an operation
touched; a drag stroke, a line, a rectangle, a fill or an image commit is
one step. Each page keeps its own Document, so its history survives moving
to another page.

**Sixels** live on an 80 × 75 grid. A sixel can only be lit in a mosaic
cell, so `set_sixel`:

1. refuses a control cell, a capital in graphics mode, the header row and a
   row hidden by double height above (the stroke carries on; refusals are
   counted and the commonest reason is shown);
2. in a graphics cell of the pen's colour (or with the pen on "keep"), just
   changes the bit;
3. otherwise turns the **blank cell to its left** into a graphics-colour
   code. If that would change how anything further along the row is drawn,
   the cell to the right is used for a code restoring what was in force
   there, if it is blank; if not, the sixel is refused rather than repaint
   the row. The test is "decode the row before and after and compare every
   other cell", so it holds for any combination of attributes.

Line (Bresenham), rectangle (outline or filled) and flood fill (4-connected
sixels in the same state, through mosaic cells of the same colours only)
are built on `set_sixel`.

**Fastext labels**: row 24 as four quarters, each a colour code and up to
nine characters, in the colours of the keys (red, green, yellow, cyan). A
row 24 with another layout is left alone until a label is typed.

## Blocks and the clipboard — `ohstxt/editor.py`, `ohstxt/clipboard.py`

A selection is a rectangle between an anchor and the cursor (drag with the
Text tool, Shift+arrows, Shift+click). Copy, cut and paste work on blocks;
cut and paste are one undo step each; row 0's first eight cells are never
written; a block is clipped at the page's edge, never wrapped.

**Attribute-aware paste.** A teletext block is not just its cells:
attributes are state carried along the row, so a pasted colour code
repaints everything after it, a double-height code takes the row below, and
spaces pasted over a code undo what it did further along. `paste_block`
decodes the subpage before and after and compares every cell outside the
block; codes, mosaic style, background, double height and boxes count
everywhere, foreground, flash and conceal only where something is drawn
(a blank turned from white to cyan looks the same, and warning about it
taught people to ignore the warning). If anything outside changed, the
status line says how many cells, which rows, and whether the block's own
codes or the codes it covered are to blame.

**On the clipboard** a block goes out as our own form
(`application/x-openhamshack-teletext-block`, JSON: `top`, `left`, `rows` as
hex — lossless) plus plain text (the English set as Unicode, control cells
as spaces). *Copy as edit.tf link* puts the block on an otherwise blank
page where it was cut from; *Copy as picture* puts a PNG of the cells as
the decoder draws them in their row (a block's look depends on the codes to
its left, so it is cut from the decoded page, not drawn alone). Paste
prefers our own form, then an edit.tf link (its non-blank cells, as a
block), then plain text, line by line at the cursor, each line only as long
as itself.

## Station settings — `ohstxt/station.py`

`station.json` in the service folder: `callsign`, `name`, `locator`, `qth`,
`lat`, `lon`, `vbi_lines` (teletext lines per field, 16 by default, used
only for time estimates), `sources` and `plugins` (live pages, below). In
the folder, not the window's preferences, because the checker CLI and the
live-page runner (started by systemd with nothing but the folder's path)
need it too; `.tti` readers ignore it. Read forgivingly (a field of the
wrong type keeps its default), written atomically. A locator is worked out
from latitude and longitude when none is set.

## The service checker — `ohstxt/check.py`

The window runs it from Tools ▸ Check service (Ctrl+K) and before Save
all; `python3 -m ohstxt.check FOLDER [--callsign C] [--lines N]
[--max-carousel S] [--budget P] [--json] [--strict]` runs it for scripts
(exit 1 on errors, on warnings too with `--strict`, 2 for a bad argument).
Every finding has a severity and the page, subpage, row and column it is
about; clicking one in the window goes there.

| severity | finding |
| --- | --- |
| error | a Fastext key (FL, including the index key) to a page not in the service — one finding for a carousel that repeats it, saying on how many subpages |
| error | no P100 (a set starts there) |
| error | two files claiming one page number |
| error | a row over 40 cells in the file (only the file can show it: reading cuts at 40) |
| error | the station's callsign in no page's header row and not on P100 (a `{call}` live token counts) |
| warning | a page number written on a page that is not in the service: three digits alone, read from what the decoder shows (not mosaics, not rows hidden under double height); not part of a decimal or followed by a single space and a word (`437 MHz` is a quantity); `P201` always counts |
| warning | cells of a row's own under double height on the row above: a Level 1 set never shows them, other decoders differ |
| warning | a carousel whose full turn is longer than `--max-carousel` (120 s) |
| warning | a page over `--budget` packets (400), all subpages |
| warning | no callsign set, so identification cannot be checked |
| info | double height where it does nothing (rows 0, 23, 24) |

Time estimates assume vbit2's way: one subpage of each page per pass of its
magazine, every row sent (as `ohstxt.t42` sends them), 50 fields a second,
`vbi_lines` packets a field. They turn `C` cycle counts into seconds and
give the pass length in the summary. Save all asks only when there are
errors; warnings are said in the status line and saved past.

## Live pages — `ohstxt/live/`

A page can be **live**: some of its cells are filled from data, and the
headless runner

```
python3 -m ohstxt.live SERVICE_DIR [--once] [--interval 2] [--plugin MODULE]...
```

rewrites its `.tti` on each source's schedule, so OpenHamShack ATV (watching the
folder) sends fresh data within a carousel turn. `--once` fills every page
once, waiting for the sources, and exits. A systemd **user** unit,
`openhamshack-teletext-live.service` (installed in `/usr/lib/systemd/user/`, not
enabled), runs it on `~/.local/share/openhamshack-teletext/service`, or on
`OHSTXT_SERVICE` from `~/.config/openhamshack-teletext/live.env`; logs go to the
journal.

### Tokens

A token is written in the page, in the cells it fills:

| written | means |
| --- | --- |
| `{name}` | the value, left-aligned |
| `{>name}` | right-aligned (numbers in a column) |
| `{name~~~~}` | tildes widen the field without changing the name |

The field is **exactly as wide as the token** — braces, `>` and tildes
included — so a template is laid out in the ordinary editor and every
column of the filled page is where the template put it. A longer value is
cut, a shorter one padded with spaces; a value with no data yet is `--`.
Names are lower case: a letter, then letters, digits, `_`; then optional
`.part`s of letters, digits, `_`, `-`. A control code cannot be inside a
token (so a field never spans a colour change: the colour goes before it).
`{source.field}` names a source; a bare `{field}` is the first source (the
page's own, then the service's) that has it. On a set the braces are ¼ and
¾, so a page meaning its own text is not likely to hold a token by
accident; the editor draws every token over as its name in a tinted box the
width of its field.

### The sidecar: `<stem>.live.json`

Beside `P301.tti`, `P301.live.json` (the `.tti`'s file name with `.tti`
replaced by `.live.json`). The `.tti` is always a complete, valid page
filled with the last good data, so a plain `.tti` reader never needs the
sidecar. Format, JSON, UTF-8:

```json
{
 "format": "ohstxt-live/1",
 "page": "301",
 "sources": {
  "dx2m": {"type": "dxcluster", "bands": ["2m", "70cm"], "every": 120}
 },
 "subpages": [
  {"rows": {"3": "\u001bFSolar flux  {>sfi}", "22": "{status~~~~~~~~~~~~~~~~~}"}},
  {"rows": {}}
 ]
}
```

- `format` must be `ohstxt-live/1`; `page` is informative (the `.tti` is
  authoritative).
- `subpages[i].rows` holds, for subpage *i* of the `.tti`, **only the rows
  that contain tokens**, keyed by row number (0–24) as a decimal string,
  each as `.tti` `OL` text: a control code is ESC (`\u001b`) then the code +
  0x40, every other character itself (7-bit). Every other row, the links,
  flags and cycle times live only in the `.tti`, so the editor and the
  runner never disagree about them. A template subpage beyond the `.tti`'s
  subpages is ignored.
- `sources` (optional): page-level sources by name, the same shape as
  `station.json`'s, looked up before the service's.
- Unknown keys are kept and written back.

The runner reads both files on every pass, replaces only the template's
rows, writes the `.tti` only when its bytes change, by a temporary file in
the same folder (a name beginning `.` and ending `.tti.tmp`, which a watcher
should ignore) renamed over the page, and never touches the sidecar.

### Sources

Configured by name in `station.json` `sources` (and a sidecar's); every
service has these four unless it says otherwise, and only the sources a
live page uses are ever fetched:

| name, type | fields | notes |
| --- | --- | --- |
| `station` (station) | `call` `name` `locator` `qth` `time` (HH:MM local) `utc` (HH:MMZ) `date` (Mon 05 Oct 2026) `day` `isodate` | from station.json and the clock; local, never fails |
| `solar` (solar) | `sfi` `a` `k` `xray` `ssn` `geomag` `noise` `updated` `aurora` `wind` `protons` `bz`; HF `d80 n80 d30 n30 d17 n17 d12 n12` (80-40, 30-20, 17-15, 12-10 m, day and night); VHF `aurora_vhf` `es_eu` `es_na` `es_6m` `es_4m` | hamqsl.com's `solarxml.php` (`url` to change), never more often than every 15 minutes |
| `dx` (dxcluster) | `count` `bands`; `1`…`N` (a formatted spot line), `N.freq` `N.call` `N.time` `N.spotter` `N.comment` `N.band` | `host`, `port` (7300), `login` (the callsign), `bands` (e.g. `["20m","2m"]`; empty = all), `count` (10), `every` (120 s, at least 30) |
| `beacons` (beacons) | `count` `on` `off`; `1`…`N` (a formatted line), `N.call` `N.freq` `N.mode` `N.loc` `N.status` `N.note` | `file` (`beacons.csv` in the folder): `call, MHz, mode, locator, status, note`, `#` comments; re-read each fetch |

Every source also answers `{<name>.status}`, and a page answers `{status}`:
empty while its sources are fresh, `No data since HH:MM` (local time of the
last good fetch) when one has failed, `No data yet` before the first. A
failing source keeps its last good values. A subpage with no status token
whose sources are stale gets the notice in yellow at the right of row 23.

The DX cluster is one short telnet session per fetch — connect, answer the
login prompt with the callsign, `sh/dx N`, read until the cluster is quiet
for 1.2 s, `bye` — rather than a held connection that must detect a
half-dead link and reconnect; bands are filtered here from each spot's
frequency (cluster filter syntax differs between programs); telnet
negotiation is stripped and never answered; one line per call and band,
newest first.

**Plugins.** A source is a class registered under a type name:

```python
from ohstxt.live.sources import Source, register

@register("weather")
class Weather(Source):
    every = 600
    def fields(self): return [("temp", "temperature")]
    def fetch(self): return {"temp": read_my_sensor()}   # raise on failure
    def sample(self): return {"temp": "14C"}
```

A module named in `station.json` `"plugins"` (or `--plugin`) is imported
first; its types can then be used in `"sources"`. Anything a fetch raises
is a failure like any other.

**State.** `.ohstxt-live-state.json` in the folder holds every feed's last
values, when they were good, and the last error: a restarted runner keeps
the last good data, and the editor's preview shows what the runner has.
Fetches run on worker threads, so a slow source never holds up the clock.

**In the editor.** *Live page* in the page panel (or the Page menu) marks a
page live; Page ▸ Insert live field lists every source and field with its
value now (or a sample) and types a token as wide as that value. The
preview fills tokens from the runner's state where it has data and from
sample data where not (View ▸ Live fields: sample data shows samples
throughout). Saving writes the template to the sidecar and the `.tti`
filled with the runner's last data — never sample data, which would go to
air until the runner next wrote; local sources (station, beacons) are read
there and then. A page no longer live loses its sidecar when saved.

## Subtitles — `ohstxt/subtitles.py`

A mode of the window (View ▸ Subtitle editor, Ctrl+U): cues with start and
end times and one or two lines, each line with its speaker's colour (the
seven a set has). Page 888 is shown in the ordinary preview, so a cue is
seen as the decoder draws a subtitle page. A cue becomes rows as
broadcasters built them: each line double height, centred, at the foot (row
22; rows 20 and 22 for two lines); before the text, double height, start
box twice (a set that misses one sees the other) and the colour inside the
box, where its cell is a black margin; after it, end box twice, the first a
margin. At 37 characters only one start box fits; past 37 the line is cut.
Status C6 subtitle and C4 erase. The editor warns about a line over 37
characters, more than two lines, a cue that ends before it starts or
overlaps the one before, and characters the set shows differently (`[` is
`←`).

### Subtitles: the SRT subset

OpenHamShack ATV reads SRT for page 888. Reading here is lenient (CRLF, BOM,
wrong numbering, `.` in times, any tags, colour names and #rgb). **Writing
is exactly this**, so a reader need handle only this:

```
1
00:00:01,000 --> 00:00:03,200
<font color="#ffff00">Is the camera running?</font>
Plain lines are white.

2
00:00:03,600 --> 00:00:06,000
Fish &amp; chips &lt;loud&gt;
```

- UTF-8, no BOM; lines end LF (`\n`) — a reader should accept CRLF too.
- Cues in time order, numbered from 1, each: the number, the timing line
  `HH:MM:SS,mmm --> HH:MM:SS,mmm` (two-digit hours, comma, three-digit
  milliseconds), one or two text lines, then one empty line.
- A text line is either plain (white) or **wholly** wrapped in exactly one
  `<font color="#rrggbb">…</font>`, where `#rrggbb` is lower case and one
  of `#ff0000` red, `#00ff00` green, `#ffff00` yellow, `#0000ff` blue,
  `#ff00ff` magenta, `#00ffff` cyan. White is never wrapped. No other tags,
  no position codes.
- `&amp;` `&lt;` `&gt;` are the only entities (for `&`, `<`, `>`).
- Every character is one of the 96 English G0 characters as Unicode — one
  character, one cell: ASCII 0x20–0x7E as themselves except `£` (0x23),
  `←` (0x5B), `½` (0x5C), `→` (0x5D), `↑` (0x5E), `#` (0x5F), `―` U+2015
  (0x60), `¼` (0x7B), `‖` U+2016 (0x7C), `¾` (0x7D), `÷` (0x7E), `■` U+25A0
  (0x7F). So `[MUSIC]` is written `←MUSIC→`, as the set shows it.
- A line may exceed 37 characters or a cue two lines only if the operator
  ignored the warnings; the exporter writes at most two lines. A reader
  should lay out as above (37 a line) and cut the rest.

## Received material — `ohstxt/receive.py`

File ▸ Open received .t42 (and `python3 -m ohstxt.receive CAPTURE.t42
[--out FOLDER]`): a capture — what OpenHamShack ATV saves, an off-air recording,
a tape recovery — back into pages.

- Addresses (MRAG, the header's page, subcode and control bits) are Hamming
  8/4: single errors corrected; a packet whose address cannot be read is
  dropped; a header whose page number cannot be read ends its magazine's
  page in progress, so the rows after it are not filed under the wrong page.
- Rows follow the last header of their magazine (parallel, as vbit2
  sends); a C11 (serial) header ends every magazine's page; mFF is time
  filling, never a page; rows 25–31 other than X/27/0 are skipped.
- Every reception of a subpage is kept and each **cell is decided by vote**
  among the copies that arrived with good parity. A cell with no good copy
  is **damaged**: it keeps the commonest value, except that a damaged
  control code becomes a space (a wrong colour or double height repaints
  the rest of the row, worse than a missing letter). Rows never received are
  blank, not damaged — broadcasters do not send blank rows.
- Subpages are gathered by subcode in subcode order (a capture that starts
  mid-carousel still lists it from the start); C4 is dropped from a
  carousel's subpages (it is how a carousel is sent, not what each means).
  Links come from X/27/0, link by link, by vote.

The dialog lists every page with its subpages, receptions and damaged
cells, shows each subpage through the decoder with damaged cells framed in
red and struck through, and imports the ticked pages (undamaged and free
ones come ticked; a taken number becomes the next free one in its
magazine). Damage marks stay on the subpage in the editor until each cell
is typed over; they are never written to a `.tti`.

Round trip: the sample service exported as `.t42` and decoded gives the
same pages; vbit2's own capture decodes to the samples it was made from.

## Templates — `ohstxt/templates.py`

File ▸ New page from template (Ctrl+Shift+N): station index (P100),
contact and QSL (P200), transmission schedule (P300), technical details
(P400), test card (P199), and three live pages — propagation (P500), DX
cluster spots (P510), beacons and repeaters (P520) — offered at their usual
number or the nearest free one in the magazine. The callsign, name, locator
and QTH come from `station.json`; without a callsign the pages say N0CALL,
the conventional placeholder. The header row carries the callsign, so every
template page identifies the station; the templates link to each other and
the checker finds nothing wrong with a service made of them. All content is
invented placeholder text.

## The window — `ohstxt/ui/`

```
  top bar   mark · page | undo redo | codes grid reveal | image link export save | check station subtitles | theme help
  body      service list    | the page (hero), cell line under it  | preview (a 4:3 set, live) ▶ play
            (thumbnails,    |                                      | tools: Text Pen Line Rect Fill,
             subpage counts)|                                      |   pen colours, every control code
                            |                                      | subpages (cycle each) · Fastext · page
  footer    status strip: folder, pages, unsaved, the last message
```

- The editor grid is the decoder's pixels with the cursor, an optional cell
  grid and the **code view** laid over them: every control cell labelled
  with a two-letter mnemonic in the code's colour (AR alpha red, GB graphics
  blue, Dh double height, Hg hold, Nb new background…) — on a set those
  cells are blank, and a page cannot be edited without seeing them. Columns
  0–7 of row 0 are dimmed: the set owns them.
- The line under the editor names the code under the cursor and the
  attributes the decoder has in force there; refusals appear on its right
  in amber.
- The preview animates flash, ticks the header clock, shows newsflash and
  subtitle pages boxed over a picture, follows Reveal, and with ▶ steps
  through the subpages at their own cycle times (a "C" cycle count is
  shown as that many seconds).
- Keys, in the style of edit.tf:

| key | does |
| --- | --- |
| typing | type-over at the cursor, which moves right (and wraps) |
| Esc, then r g y b m c w k | alpha red … white, black |
| Esc, then R G Y B M C W K | graphics red … white, black |
| Esc f / F | flash / steady |
| Esc d / D | double / normal height |
| Esc n / N | new / black background |
| Esc h / H | hold / release graphics |
| Esc s / S | separated / contiguous |
| Esc o | conceal |
| Esc [ / ] | start / end box |
| q w a s z x | with a drawing tool: toggle that sixel of the cursor cell |
| Ins / Del | insert / delete a cell; with Ctrl, a row |
| Backspace | clear the cell to the left |
| PgUp / PgDn | previous / next subpage |
| Ctrl+1…5 | Text, Pen, Line, Rect, Fill |
| Ctrl+E / G / R | code view / grid / reveal |
| Ctrl+Z, Ctrl+Y (Ctrl+Shift+Z) | undo, redo |
| Shift+arrows, drag (Text tool) | select a block |
| Ctrl+C, Ctrl+X, Ctrl+V | copy, cut, paste (Delete clears the selection) |
| Ctrl+A | select rows 1–23 |
| Ctrl+K | check service |
| Ctrl+U | subtitle editor |
| Ctrl+S, Ctrl+Shift+S | save page, save all (the checker first) |
| Ctrl+N, Ctrl+Shift+N | new page, new page from a template |
| Ctrl+O, Ctrl+Shift+O | open folder, open received .t42 |
| Ctrl+I, Ctrl+L | import image, edit.tf link |
| F1 | every shortcut |

- The theme, controls, icons and window furniture are OpenHamShack ATV's (which
  are OpenHamShack NB's), copied so the family looks one.

## Samples — `ohstxt/samples.py`

Built in code, all invented (the "OpenHamShack" service is ours; no
broadcaster's page, name or logo): P100 a news index with a block-graphics
logo and Fastext; P150 a boxed newsflash; P199 an engineering test page
with one row per attribute and the whole character set; P300 a three-page
weather carousel (10, 10, 15 s). They are the first-run service, the
screenshots' subject, and known cases for the tests.

## Verification

`python3 -m unittest discover -s tests` (the UI tests run offscreen):
decoder rules against hand-built rows; .tti round trips and other tools'
files; Hamming 8/4 (every single error corrected, every double detected),
parity, header and X/27/0 fields; **vbit2's own output**; edit.tf packing
against a hand-worked case and round trips; image conversion including
brute-force optimality; the editing model; blocks and the paste warning,
every clipboard form out and back; the checker, one fault at a time, and
its command line's output and exit status; live pages against fakes on
local sockets (an HTTP server for the solar XML, a telnet server that
behaves like a DX cluster) — failure keeping the last good data, atomic
writes only on change, plugins, restart; SRT read leniently and written
strictly, cues through the decoder; captures with repeats, interleaving,
filling, serial headers, corrected and uncorrectable errors, and the
round trip; templates; the window driven by key presses and clicks; the
.deb built, extracted and run, and the systemd unit's own command run
against it. Nothing touches the real network.
