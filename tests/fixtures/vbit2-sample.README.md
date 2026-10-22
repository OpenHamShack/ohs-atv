# vbit2's output, for the cross-check against the sample service

`vbit2-sample.t42` is what vbit2 (Peter Kwan,
github.com/peterkvt80/vbit2) transmitted from the sample service this
program builds in code (`ohstxt/samples.py`) - page 100 in full, and
subpage 2 of the 300 carousel, each from its header to the next packet's
(`test_txt_t42.AgainstVbit2`'s own docstring says so plainly:
"vbit2-sample.t42 is what vbit2 ... transmitted when given the sample
service as .tti files written by this program"). It came from OpenHamShack
Teletext's own `tests/fixtures` (the standalone editor, before it was
folded into this repository) and was copied in unchanged; no recipe for
remaking it from vbit2 itself is recorded anywhere this session could
find, and vbit2's own repository was not fetched to write one now.

`tests/fixtures/ohstxt-service/*.tti` is a *second*, separately
maintained copy of the same two pages, used by a different test
(`test_teletext.AgainstVbit2`, in the ATV app's own suite rather than the
editor's). It is meant to read the same as `samples.py`'s output, but is
not what vbit2 actually read to make this capture - treat `samples.py` as
the one true source for anything derived from this file, as this session
had to once it found `test_our_crc_is_vbit2s` disagreeing with the
*other* source's content for page 100 (below).

**Patched, not remade, 21/10/2026 (the OpenHamShack rename).** The sample
pages' own text named the product (`OL,0`'s header template on every page,
`OL,2`'s NEWS headline on page 100, `OL,22`'s "Pages from ... Teletext"
credit); the rename turned the old name (7 characters) into one too long
for a row (header.py's `DEFAULT_TEMPLATE` hit the same thing - Brian's
call there was "OHS", 21/10/2026, but that is 3 characters, not a length a
byte-for-byte patch could use, and `samples.py` and `ohstxt-service/`
both still had to read whatever this capture could actually be patched
to - see below). Here the pages now read "OHS-ATV" (header template,
NEWS headline) or "OHS ATV" (the credit line, which already had a space
before "Teletext") - 7 characters, like the word they replace - and
`vbit2-sample.t42` was patched to match, byte for byte:
a one-off script (run once on 21/10/2026, then removed so the old name
is recorded nowhere) changed only those bytes and asserted that nothing
else moved. A
teletext row is plain 7-bit text with odd parity in bit 7 (ETSI EN 300 706
12.2), which is why a same-length word swap works at all.

**Both pages' X/27/0 CRCs were recomputed, not left stale.** Each is
Hamming-8/4 coded over the header text, not parity text a word swap
leaves alone, and real vbit2 computed it from the old word. Unlike
hacktv.t42's own page (see that capture's README), this was safe to do
here: with the word fully reverted to the original, `ohstxt.t42.page_crc`
fed rows from `ohstxt.samples.news_index()`/`weather_carousel()`
reproduces both captured CRCs bit-exact (checked against a copy of this
file from before this session touched anything, byte for byte, not
assumed) - proving the routine sound for this exact content before
trusting it to compute the *new* value over the renamed header text.
The patch did this the same way: build the page from
`samples.py`, take the real captured header with only the word swapped,
and write back only the two CRC bytes.

**Why `samples.py`, not `ohstxt-service/`.** The two are meant to agree
and mostly do, but the rename's "OpenHamShack Teletext" (21 characters)
made `ohstxt-service/P100.tti`'s own `OL,22` line overflow 40 columns -
and one of the two readers of this repository's `.tti` files truncates an
overlong row rather than refusing it (`ohstxt.tti.loads`; `ohsatv.tti`
does not), silently dropping the trailing "P100" for as long as the
product's full name sat there unnoticed. That means `test_our_crc_is_vbit2s`
would not have validated against `ohstxt-service/`'s content either,
during the whole time between the family-wide rename landing and this
session fixing the word - not because the renamed word is wrong, but
because the row silently went out shorter than it should have, so this
session's own CRC check against it would have been checking a value
hacktv never sent. `samples.py`'s own `row()` refuses an overlong row
outright rather than truncating it (see its module docstring), which is
how a mismatch here was going to be caught at all, and why it is the
trustworthy source once the word itself is fixed.
