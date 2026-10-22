# hacktv's teletext, for a second cross-check

The vbit2 capture beside this folder (`../vbit2-sample.t42`) checks our
packets against one inserter. This checks them against another, written
independently: hacktv (Philip Heron, https://github.com/fsphil/hacktv,
GPLv3 like this program), `src/teletext.c`.

- `demo.tti` - hacktv's own demo page, `src/demo.tti`, copied unchanged
  (md5 35f7f5acd469e66452ddeb7e9b842b62).
- `P2A0.tti` - ours, written for this check: control codes in both forms
  (ESC + 0x40 and MRG's 0x80 + n), a Latin-1 '£', mosaics, a sub-code,
  PS 8009 (C5, C8), and fastext links to its own magazine, to others and
  to none (8FF).
- `hacktv.t42` - the first 64 packets hacktv made from those two files,
  42 bytes each (run-in and framing code checked, then dropped).
- `hacktv-drv.c` - the program that made it: it `#include`s hacktv's
  `teletext.c`, so hacktv's own `_load_tti` and `_next_packet` run, with
  the clock fixed at 2025-10-05 16:00:00 UTC.

Where the source came from: hacktv master at commit
80d98eabdc70f046b7d2d12b2473b56113648f83 (2025-12-31), the tarball
https://codeload.github.com/fsphil/hacktv/tar.gz/refs/heads/master
(sha256 5125a2669b69f70ccda2b3108efb267fbda0c101ca7cc93db7f2a96e00aa21ec),
fetched on 5 October 2026 by the session that wrote NICAM-728 (its
`tests/data/hacktv_nicam_*` come from the same tree); this fixture was
built from that local copy, not fetched again. To make it again:

    cc -I hacktv-master/src -o hacktv-drv hacktv-drv.c -lm
    TZ=UTC ./hacktv-drv hacktv.t42 64 demo.tti P2A0.tti

Nothing of hacktv is run or imported by the tests.

**Patched, not remade, 21/10/2026 (the OpenHamShack rename).** P2A0.tti's
own text named the product; the rename turned its old name (7 characters)
into one too long for the row it sits in. Rather than refetch hacktv at
the pinned commit above - it no longer resolves from upstream - P2A0.tti
now reads "OHS-ATV" (also 7 characters, the same short form used in
ohstxt/header.py's DEFAULT_TEMPLATE and ohstxt/samples.py, Brian's call
21/10/2026) in its place, and `hacktv.t42` was patched to match: a
teletext row is plain 7-bit text with odd parity in bit 7 (ETSI EN 300 706
12.2), so swapping one word for another of the same length changes only
that word's bytes, with their parity bit recomputed - everything else in
the capture is still hacktv's own, unmoved output. The patch was a
one-off script, run once on 21/10/2026 and then removed so that the old
name is recorded nowhere; it asserted that only the swapped word's bytes
(and, below, the CRC bytes) moved. The pre-rename capture is in the
rename archive if it is ever needed.

**P2A0's X/27/0 CRC is derived, not captured.** The page CRC covers the
rows, so the word swap above changes it, and hacktv cannot be rerun. Before
the rename, `test_our_packets_are_hacktvs` passed on this capture: our
`ttx.page_crc` reproduced every CRC hacktv sent, P2A0's included, bit for
bit. That makes our routine a faithful stand-in for hacktv's here, so the
four X/27/0 packets for P2A0 carry the CRC it computes over the patched
rows, and only their last two bytes (the CRC itself) differ from what
hacktv sent; the patch asserted exactly that. If this capture is ever
remade with a real hacktv run, its CRC should come out the same. (An
earlier note here said P2A0's CRC never matched even over the original
content; that was wrong. It came from a working copy of P2A0.tti that had
been briefly mangled during the rename work, and the pre-rename suite
proves the match.)
