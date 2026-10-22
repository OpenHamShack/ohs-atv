"""The service checker, one known fault at a time on otherwise clean
services, the folder as it is on disk (rows longer than 40 cells, files
that are not pages, station.json), and the command line's output and exit
status as a script sees them. Also the station settings it reads."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import page_rows, tmp_dir

from ohstxt import check, station, tti
from ohstxt.page import NO_LINK, Page, Subpage
from ohstxt.samples import AW, AY, DOUBLE

REPO = Path(__file__).resolve().parent.parent


def page(number, *rows, links=None, subs=1, cycle=8, timed=True, head=b"", filename=""):
    out = []
    for _ in range(subs):
        s = Subpage(rows=page_rows(*rows), cycle=cycle, timed=timed)
        if head:
            s.rows[0][8:8 + len(head)] = head
        if links:
            s.links = list(links) + [NO_LINK] * (6 - len(links))
            s.has_links = True
        out.append(s)
    return Page(number=number, subpages=out, filename=filename or f"P{number:03X}.tti")


def clean():
    """A service with nothing wrong: P100 names the station and links to P101."""
    return [page(0x100, (1, b"M0TXT index"), (3, b"News 101"), links=[0x101, 0x100]),
            page(0x101, (1, b"News"), links=[0x100])]


def kinds(rep, severity=None):
    return sorted(f.kind for f in rep.findings if severity is None or f.severity == severity)


class Clean(unittest.TestCase):
    def test_a_clean_service_has_no_findings(self):
        rep = check.check_pages(clean(), opts=check.Options(callsign="M0TXT"))
        self.assertEqual(rep.findings, [])
        self.assertIn("no problems found", rep.summary())


class Links(unittest.TestCase):
    def test_a_fastext_link_to_a_missing_page_is_an_error_at_its_label(self):
        pages = clean()
        pages[1].subpages[0].links[2] = 0x555
        rep = check.check_pages(pages, opts=check.Options(callsign="M0TXT"))
        [f] = rep.findings
        self.assertEqual((f.severity, f.kind, f.page, f.row, f.col), ("error", "link", 0x101, 24, 21))
        self.assertIn("yellow", f.message)
        self.assertIn("P555", f.message)

    def test_the_index_key_is_checked_too(self):
        pages = clean()
        pages[1].subpages[0].links[5] = 0x199
        rep = check.check_pages(pages, opts=check.Options(callsign="M0TXT"))
        self.assertIn("index", rep.findings[0].message)

    def test_a_carousel_repeating_a_broken_link_is_one_finding(self):
        pages = clean() + [page(0x300, links=[0x777], subs=3)]
        rep = check.check_pages(pages, opts=check.Options(callsign="M0TXT"))
        links = [f for f in rep.findings if f.kind == "link"]
        self.assertEqual(len(links), 1)
        self.assertIn("on 3 subpages", links[0].message)

    def test_page_numbers_written_on_a_page_are_index_links(self):
        pages = clean()
        pages[0].subpages[0].rows[4][:20] = b"Sport 201  P202 2010"
        rep = check.check_pages(pages, opts=check.Options(callsign="M0TXT"))
        refs = [f for f in rep.findings if f.kind == "index-link"]
        self.assertEqual([(f.severity, f.row, f.col) for f in refs], [("warning", 4, 6), ("warning", 4, 12)])

    def test_quantities_are_not_page_numbers(self):
        pages = clean()
        pages[0].subpages[0].rows[6][:30] = b"144.750 MHz, 437 MHz, 333 kS/s"
        pages[0].subpages[0].rows[7][:9] = b"Ask P1A5 "
        rep = check.check_pages(pages, opts=check.Options(callsign="M0TXT"))
        self.assertEqual([(f.row, f.col) for f in rep.findings], [(7, 5)])     # only the one with a P

    def test_numbers_in_mosaics_or_hidden_rows_are_not_read(self):
        pages = clean()
        pages[0].subpages[0].rows[5][:4] = bytes([0x12]) + b"201"   # graphics green: mosaics, not digits
        rep = check.check_pages(pages, opts=check.Options(callsign="M0TXT"))
        self.assertEqual(kinds(rep), [])


class ServiceShape(unittest.TestCase):
    def test_no_p100(self):
        rep = check.check_pages([page(0x101, (1, b"x"))], opts=check.Options(callsign="M0TXT"))
        self.assertIn("no-p100", kinds(rep, "error"))

    def test_duplicates_name_both_files(self):
        pages = clean() + [page(0x101, filename="other.tti")]
        rep = check.check_pages(pages, opts=check.Options(callsign="M0TXT"))
        dups = [f for f in rep.findings if f.kind == "duplicate"]
        self.assertEqual(sorted(f.filename for f in dups), ["P101.tti", "other.tti"])
        self.assertTrue(all(f.severity == "error" for f in dups))

    def test_a_long_carousel(self):
        pages = clean() + [page(0x300, subs=10, cycle=20)]
        rep = check.check_pages(pages, opts=check.Options(callsign="M0TXT", max_carousel=120))
        [f] = [f for f in rep.findings if f.kind == "carousel"]
        self.assertIn("200 s", f.message)
        rep = check.check_pages(pages, opts=check.Options(callsign="M0TXT", max_carousel=300))
        self.assertNotIn("carousel", kinds(rep))

    def test_cycle_counts_are_turned_into_seconds_by_the_service_size(self):
        # 2 pages of 26 packets at 1 line a field = 52/50 s a pass; 100 cycles
        # of each of 2 subpages is about 208 s
        pages = clean() + [page(0x300, subs=2, cycle=100, timed=False)]
        rep = check.check_pages(pages, opts=check.Options(callsign="M0TXT", lines=1))
        self.assertAlmostEqual(rep.cycle_seconds, (26 + 26 + 25) / 50)
        self.assertIn("carousel", kinds(rep))

    def test_size_budget(self):
        pages = clean() + [page(0x300, subs=20)]
        rep = check.check_pages(pages, opts=check.Options(callsign="M0TXT", budget=400, max_carousel=1e9))
        [f] = [f for f in rep.findings if f.kind == "size"]
        self.assertIn("500 packets", f.message)


class DoubleHeight(unittest.TestCase):
    def test_content_under_double_height_is_flagged_at_its_first_cell(self):
        pages = clean()
        pages[1].subpages[0].rows[5][:4] = bytes([DOUBLE]) + b"BIG"
        pages[1].subpages[0].rows[6][10:15] = b"lost!"
        rep = check.check_pages(pages, opts=check.Options(callsign="M0TXT"))
        [f] = rep.findings
        self.assertEqual((f.severity, f.kind, f.row, f.col), ("warning", "double-height", 6, 10))

    def test_a_blank_row_under_double_height_is_fine(self):
        pages = clean()
        pages[1].subpages[0].rows[5][:4] = bytes([DOUBLE]) + b"BIG"
        self.assertEqual(check.check_pages(pages, opts=check.Options(callsign="M0TXT")).findings, [])

    def test_double_height_on_row_23_is_a_note(self):
        pages = clean()
        pages[1].subpages[0].rows[23][:4] = bytes([AY, DOUBLE]) + b"Hi"   # 4 cells
        rep = check.check_pages(pages, opts=check.Options(callsign="M0TXT"))
        self.assertEqual([(f.severity, f.row, f.col) for f in rep.findings], [("info", 23, 1)])


class Identification(unittest.TestCase):
    def test_callsign_in_a_header_row(self):
        pages = [page(0x100, (1, b"index")), page(0x101, head=b"GB3TV teletext")]
        self.assertEqual(kinds(check.check_pages(pages, opts=check.Options(callsign="gb3tv"))), [])

    def test_callsign_only_on_another_page_body_does_not_count(self):
        pages = [page(0x100, (1, b"index")), page(0x101, (2, b"GB3TV"))]
        rep = check.check_pages(pages, opts=check.Options(callsign="GB3TV"))
        self.assertEqual(kinds(rep, "error"), ["station-id"])

    def test_no_callsign_set_is_a_warning(self):
        rep = check.check_pages(clean())
        self.assertEqual(kinds(rep), ["station-id"])
        self.assertEqual(rep.findings[0].severity, "warning")


class OnDisk(unittest.TestCase):
    def setUp(self):
        self.dir = tmp_dir(self, "ohstxt-check-")
        for pg in clean():
            tti.save(pg, self.dir / pg.filename)
        station.save(station.Station(callsign="M0TXT"), self.dir)

    def test_rows_over_forty_cells_are_found_in_the_file(self):
        p = self.dir / "P101.tti"
        p.write_bytes(p.read_bytes() + b"OL,7," + b"x" * 38 + b"\x1bA" + b"yy\r\n")
        rep = check.check_folder(self.dir)
        [f] = rep.findings
        self.assertEqual((f.kind, f.row, f.severity), ("long-row", 7, "error"))
        self.assertIn("41 cells", f.message)

    def test_files_that_are_not_pages(self):
        (self.dir / "junk.tti").write_bytes(b"\x00\x01 not a page")
        rep = check.check_folder(self.dir)
        self.assertEqual(rep.findings[0].kind, "unreadable")

    def test_callsign_comes_from_station_json(self):
        station.save(station.Station(callsign="G9NOT"), self.dir)
        self.assertEqual(kinds(check.check_folder(self.dir)), ["station-id"])

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "ohstxt.check", *map(str, args)], cwd=REPO,
                              capture_output=True, text=True)

    def test_cli_exit_status_and_output(self):
        r = self.run_cli(self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no problems found", r.stdout)
        pg = tti.load(self.dir / "P101.tti")
        pg.subpages[0].links[1] = 0x404
        tti.save(pg, self.dir / "P101.tti")
        r = self.run_cli(self.dir)
        self.assertEqual(r.returncode, 1)
        self.assertRegex(r.stdout, r"error\s+P101 r24 c11\s+The green link goes to P404")
        r = self.run_cli(self.dir, "--json")
        data = json.loads(r.stdout)
        self.assertEqual(data["findings"][0]["page"], 0x101)

    def test_cli_strict_fails_on_warnings(self):
        pg = tti.load(self.dir / "P100.tti")
        pg.subpages[0].rows[8][:8] = b"See 333 "
        tti.save(pg, self.dir / "P100.tti")
        self.assertEqual(self.run_cli(self.dir).returncode, 0)
        self.assertEqual(self.run_cli(self.dir, "--strict").returncode, 1)

    def test_cli_refuses_a_missing_folder(self):
        self.assertEqual(self.run_cli(self.dir / "nope").returncode, 2)


class Station(unittest.TestCase):
    def test_round_trip_and_forgiving_read(self):
        d = tmp_dir(self, "ohstxt-st-")
        station.save(station.Station(callsign="m0abc", name="Alex", lat=51.5, lon=-0.9), d)
        st = station.load(d)
        self.assertEqual((st.callsign, st.name, st.lat), ("M0ABC", "Alex", 51.5))
        (d / "station.json").write_text(json.dumps({"callsign": 7, "name": "Sam", "vbi_lines": "x"}))
        st = station.load(d)
        self.assertEqual((st.callsign, st.name, st.vbi_lines), ("", "Sam", 16))
        (d / "station.json").write_text("{")
        self.assertEqual(station.load(d).callsign, "")

    def test_maidenhead_known_places(self):
        # Worked by hand from the definition: 20 x 10 degree fields from
        # 180W 90S, 2 x 1 degree squares, 5' x 2.5' subsquares.
        self.assertEqual(station.maidenhead(51.4778, -0.0015), "IO91xl")      # Greenwich
        self.assertEqual(station.maidenhead(-33.8568, 151.2153), "QF56od")    # Sydney
        self.assertEqual(station.maidenhead(40.7, -74.006, 4), "FN20")    # New York, just west of FN30
        self.assertEqual(station.maidenhead(51.4778, -0.0015, 8), "IO91xl94")

    def test_grid_falls_back_to_the_position(self):
        self.assertEqual(station.Station(lat=40.7, lon=-74.006).grid, "FN20xq")
        self.assertEqual(station.Station(locator="IO91", lat=1, lon=1).grid, "IO91")


if __name__ == "__main__":
    unittest.main()
