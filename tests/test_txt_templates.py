"""Page templates: each builds a whole, valid page for the station in the
settings - its callsign, name, locator and QTH where they belong, cut to
fit rather than breaking a row when they are long - and the live ones carry
tokens that every one resolve to a source, so a template page fills
completely with sample data and passes the checker's identification test."""
import unittest

from helpers import page_rows  # noqa: F401  (path setup)

from ohstxt import check, live, templates, tti
from ohstxt.live.template import tokens_in
from ohstxt.station import Station

ST = Station(callsign="M0TXT", name="Alex", locator="IO91wm", qth="Reading")
LONG = Station(callsign="VP2V/M0TXTLONG", name="A very long operator name indeed",
               qth="Somewhere with an extremely long place name", lat=51.45, lon=-0.97)


class Templates(unittest.TestCase):
    def test_every_template_builds_a_valid_page(self):
        for st in (ST, LONG, Station()):
            for t in templates.TEMPLATES:
                pg = t.build(st, t.number)
                self.assertEqual(pg.number, t.number)
                for s in pg.subpages:
                    self.assertEqual(len(s.rows), 25)
                    self.assertTrue(all(len(r) == 40 for r in s.rows), t.key)
                back = tti.loads(tti.dumps(pg))
                self.assertEqual([bytes(r) for r in back.subpages[0].rows],
                                 [bytes(r) for r in pg.subpages[0].rows], t.key)

    def test_station_details_appear(self):
        text = "\n".join(templates.contact(ST).subpages[0].text(r) for r in range(25))
        for v in ("M0TXT", "Alex", "Reading", "IO91wm"):
            self.assertIn(v, text)
        card = "\n".join(templates.test_card(ST).subpages[0].text(r) for r in range(25))
        self.assertIn("M0TXT", card)
        self.assertIn("IO91wm  Reading", card)

    def test_locator_from_position_and_a_placeholder_without_a_callsign(self):
        text = "\n".join(templates.contact(LONG).subpages[0].text(r) for r in range(25))
        self.assertIn("IO91mk", text)
        self.assertIn("N0CALL", "\n".join(templates.contact(Station()).subpages[0].text(r) for r in range(25)))

    def test_the_header_identifies_the_station(self):
        pages = [templates.station_index(ST), templates.contact(ST)]
        rep = check.check_pages(pages, opts=check.Options(callsign="M0TXT"))
        self.assertNotIn("station-id", [f.kind for f in rep.findings])

    def test_the_templates_link_to_each_other(self):
        pages = [t.build(ST, t.number) for t in templates.TEMPLATES]
        rep = check.check_pages(pages, opts=check.Options(callsign="M0TXT"))
        self.assertEqual([f for f in rep.findings if f.kind in ("link", "index-link")], [])

    def test_live_templates_fill_completely_with_sample_data(self):
        b = live.Board(".", ST)
        b.sample = True
        for t in templates.TEMPLATES:
            pg = t.build(ST, t.number)
            self.assertEqual(pg.live is not None, t.live, t.key)
            if not t.live:
                self.assertEqual(tokens_in(pg.subpages[0].rows), [], t.key)
                continue
            scope = b.scope(pg.live)
            toks = tokens_in(pg.subpages[0].rows)
            self.assertTrue(toks)
            for tok in toks:
                if tok.name != "status":
                    self.assertIsNotNone(b.lookup(tok.name, scope)[1], f"{t.key}: {{{tok.name}}}")
            filled = b.render(pg, live.template_of(pg, pg.live))
            self.assertEqual(tokens_in(filled.subpages[0].rows), [])
            text = "\n".join(filled.subpages[0].text(r) for r in range(1, 24))
            self.assertNotIn(" -- ", text, t.key)

    def test_the_propagation_page_shows_the_figures(self):
        b = live.Board(".", ST)
        b.sample = True
        pg = templates.propagation(ST)
        filled = b.render(pg, live.template_of(pg, pg.live))
        rows = [filled.subpages[0].text(r) for r in range(25)]
        self.assertRegex(rows[3], r"Solar flux +162")
        self.assertRegex(rows[10], r"30m-20m +Good +Good")


if __name__ == "__main__":
    unittest.main()
