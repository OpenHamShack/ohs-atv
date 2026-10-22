"""Solar and propagation data from hamqsl.com's XML feed (N0NBH).

    https://www.hamqsl.com/solarxml.php

One small XML document: solar flux, A and K index, X-ray class, sunspot
number, geomagnetic field, noise level, and calculated HF band conditions
(day and night for 80-40, 30-20, 17-15, 12-10 m) and VHF phenomena (aurora,
E-skip by region). The feed is updated about every three hours, so it is
fetched no more than once every fifteen minutes whatever the configuration
asks - being a polite client of a free service matters more than freshness
the data does not have.

Field names are short on purpose: a field is as wide as its token, so
`{d30}` (5 cells) can hold "Good" where `{solar.hf_30m_20m_day}` could
not sit in a table.
"""
from __future__ import annotations

import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from .. import __version__
from .sources import Source, SourceError, register

URL = "https://www.hamqsl.com/solarxml.php"
MAX_BYTES = 256 * 1024          # the real document is a few kilobytes; anything huge is not it
BANDS = (("80m-40m", "80"), ("30m-20m", "30"), ("17m-15m", "17"), ("12m-10m", "12"))
VHF = (("vhf-aurora", "northern_hemi", "aurora_vhf"), ("E-Skip", "europe", "es_eu"),
       ("E-Skip", "north_america", "es_na"), ("E-Skip", "europe_6m", "es_6m"), ("E-Skip", "europe_4m", "es_4m"))
SCALARS = (("solarflux", "sfi"), ("aindex", "a"), ("kindex", "k"), ("xray", "xray"), ("sunspots", "ssn"),
           ("geomagfield", "geomag"), ("signalnoise", "noise"), ("updated", "updated"), ("aurora", "aurora"),
           ("solarwind", "wind"), ("protonflux", "protons"), ("magneticfield", "bz"))


def parse(xml: bytes | str) -> dict[str, str]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise SourceError(f"not the solar XML: {e}") from e
    data = root.find("solardata") if root.tag != "solardata" else root
    if data is None:
        raise SourceError("not the solar XML: no <solardata>")
    v: dict[str, str] = {}
    for tag, name in SCALARS:
        el = data.find(tag)
        if el is not None and el.text is not None:
            v[name] = " ".join(el.text.split())
    if "sfi" not in v:
        raise SourceError("the solar XML has no solar flux")
    for band in data.iter("band"):
        for full, short in BANDS:
            if band.get("name") == full:
                when = "d" if band.get("time") == "day" else "n"
                v[f"{when}{short}"] = (band.text or "").strip()
    for ph in data.iter("phenomenon"):
        for name, loc, short in VHF:
            if ph.get("name") == name and ph.get("location") == loc:
                v[short] = (ph.text or "").strip()
    return v


@register("solar")
class SolarSource(Source):
    every = 3600.0
    min_every = 900.0

    def fields(self):
        out = [("sfi", "solar flux index"), ("a", "A index"), ("k", "K index"), ("xray", "X-ray class"),
               ("ssn", "sunspot number"), ("geomag", "geomagnetic field"), ("noise", "signal noise"),
               ("updated", "when hamqsl last updated"), ("aurora", "aurora"), ("wind", "solar wind"),
               ("protons", "proton flux"), ("bz", "Bz")]
        for full, short in BANDS:
            out += [(f"d{short}", f"{full} by day"), (f"n{short}", f"{full} by night")]
        out += [(short, f"{name} {loc}") for name, loc, short in VHF]
        return out

    def fetch(self):
        url = self.config.get("url", URL)
        req = urllib.request.Request(url, headers={"User-Agent": f"openhamshack-teletext/{__version__}"})
        try:
            with urllib.request.urlopen(req, timeout=float(self.config.get("timeout", 15))) as r:
                body = r.read(MAX_BYTES + 1)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise SourceError(f"{url}: {getattr(e, 'reason', e)}") from e
        if len(body) > MAX_BYTES:
            raise SourceError(f"{url}: more than {MAX_BYTES} bytes; not the solar XML")
        return parse(body)

    def sample(self):
        return parse(SAMPLE_XML)


SAMPLE_XML = """<?xml version="1.0" encoding="ISO-8859-1"?>
<solar><solardata>
<source url="http://www.hamqsl.com/solar.html">N0NBH</source>
<updated> 05 Oct 2026 1800 GMT</updated>
<solarflux>162</solarflux><aindex>8</aindex><kindex>2</kindex><kindexnt>No Report</kindexnt>
<xray>C1.4</xray><sunspots>134</sunspots><heliumline>142.0</heliumline><protonflux>0.33</protonflux>
<electonflux>1430</electonflux><aurora>2</aurora><normalization>1.99</normalization>
<latdegree>67.5</latdegree><solarwind>412.6</solarwind><magneticfield>-1.8</magneticfield>
<calculatedconditions>
<band name="80m-40m" time="day">Fair</band><band name="30m-20m" time="day">Good</band>
<band name="17m-15m" time="day">Good</band><band name="12m-10m" time="day">Fair</band>
<band name="80m-40m" time="night">Good</band><band name="30m-20m" time="night">Good</band>
<band name="17m-15m" time="night">Fair</band><band name="12m-10m" time="night">Poor</band>
</calculatedconditions>
<calculatedvhfconditions>
<phenomenon name="vhf-aurora" location="northern_hemi">Band Closed</phenomenon>
<phenomenon name="E-Skip" location="europe">Band Closed</phenomenon>
<phenomenon name="E-Skip" location="north_america">Band Closed</phenomenon>
<phenomenon name="E-Skip" location="europe_6m">50MHz ES</phenomenon>
<phenomenon name="E-Skip" location="europe_4m">Band Closed</phenomenon>
</calculatedvhfconditions>
<geomagfield>QUIET</geomagfield><signalnoise>S1-S2</signalnoise><fof2>7.85</fof2>
<muffactor>3.04</muffactor><muf>23.87</muf>
</solardata></solar>
"""
