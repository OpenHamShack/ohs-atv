#!/usr/bin/env python3
"""Build the OpenHamShack ATV website, with its user guide, into one directory.

    tools/site-build.py [--out ~/openhamshack-atv-dist/site] [--releases ~/openhamshack-atv-dist]
                        [--url https://openhamshackatv.brian-henderson.co.uk/] [--no-pdf]

What lands in --out is the whole publishable site:

    index.html, favicon.svg          site/index.html filled in, and its icon
    openhamshack-atv_<v>_all.deb(.sha256) the newest .deb in --releases, and its checksum
    openhamshack-atv-<v>-setup.exe(.sha256) the newest Windows installer in --releases, if any
    shots/                           screenshots: a small copy inline, the full size behind it
    guide/index.html, guide/img/     the user guide as a web page (docs/guide/index.html)
    openhamshack-atv-guide.pdf            the same guide, A4, printed by tools/guide-pdf.py
    robots.txt, sitemap.xml          for crawlers
    .htaccess, 404.html              the server's rules for this site, and its not-found page

Shape copied from OpenHamShack NB's tools/site-build.py, with one difference:
NB's release folder IS its site, so a .deb is on the site because it is in
the folder. Here the .debs are built into --releases (tools/build-deb.sh
writes to the current directory, and the other half of the work happens
there) and the site is a separate staging directory, so the newest .deb is
copied across. "Newest" is by version number, not by file date: a rebuilt
old version must never displace a newer one. Older .debs in --out are
removed, so the staging directory holds exactly what the page offers; the
removal is printed, never silent.

Release notes are the commit subjects between "Release x.y.z" commits, as
NB's are (tools/release.sh makes them). A version that is published but has
no such commit yet - 0.1.0 was built before release.sh existed - gets the
commits since the last one, leaving out commits that touch only the site
and the guide, which are not in any .deb.

Images are re-encoded as JPEG when Pillow is there: a 1920 x 1080 UI
screenshot is ~850 kB as PNG and ~300 kB as JPEG at quality 90, and the
inline 960-wide copy ~95 kB, with no visible loss on this flat, dark UI.
Without Pillow the PNGs are copied unchanged (bigger, never broken) and
the build says so.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # optional: the site still builds, with full-size PNGs
    Image = None

ROOT = Path(__file__).resolve().parent.parent
SHOT_DIR = ROOT / "docs" / "screenshots"
GUIDE_SRC = ROOT / "docs" / "guide" / "index.html"
PDF_NAME = "openhamshack-atv-guide.pdf"
SHOTS = [  # (file in docs/screenshots, caption) - shown when present
    ("main-fm-23cm-1920.png", "FM-ATV on 23cm: a simulated station on 1255 MHz, locked in colour"),
    # Off the air, at the bench (docs/atv-research.md 15a and 16): real
    # radios, so they are labelled as such and dated - the simulator shots
    # around them show what the program does, these show it has been done.
    ("hardware-23cm-fm-colour-libre-to-nano-minus14.png",
     "Off the air: FM-ATV in colour with sound, a LibreSDR transmitting to a Pluto nano at 12.5 MS/s on 1251 MHz, "
     "across the bench (5 October 2026)"),
    ("hardware-23cm-minimac-sound-nano-to-libre-minus14.png",
     "Off the air: Mini-MAC from a Pluto nano at 5 MS/s, recognised by a LibreSDR receiving at 16 MS/s - colour "
     "and digital sound (5 October 2026)"),
    ("main-transmitting-1920.png", "Transmitting: your own test card through the simulator's loopback"),
    ("main-minimac-1920.png", "Mini-MAC at 5 MS/s: colour and digital sound where a USB Pluto could send only a "
                              "mono picture - OpenHamShack ATV only"),
    ("main-lowpal-1920.png", "Narrow colour PAL at 5 MS/s: colour on 1.6 MHz, FM sound on 2.2 MHz - "
                             "OpenHamShack ATV only"),
    ("main-am-1275-1920.png", "AM-VSB on 23cm: vision carrier, PAL chroma and the sound carrier on the spectrum"),
    ("main-nicam-1920.png", "NICAM-728 stereo from the simulated AM-VSB station: the badge and the carrier "
                            "beside the sound"),
    ("main-6cm-1920.png", "6cm at 5665 MHz, with the radio's limits on the band card"),
    ("main-70cm-1920.png", "70cm: AM-VSB allowed outside the bandplan, under a red warning that is logged"),
    ("main-405-transmitting-1920.png", "405 lines (System A) from a USB Pluto's 5 MS/s: the full 3 MHz picture and "
                                       "its AM sound, 4.3 MHz in all"),
    ("main-cable-feed-1920.png", "The cable feed: 405 lines on Band I B2 into a vintage set, by cable only, at the "
                                 "radio's least output"),
    ("main-cable-uhf-1920.png", "The cable feed on UHF E36: 625-line PAL with teletext into a 625-line set, by cable "
                                "only; the waveform monitor on line 12 shows the data"),
    ("windows-main-1366.png", "On Windows 11: the same window, here on the simulator in a test machine - the "
                              "Windows build is new and not yet tried with a radio or a webcam"),
    ("settings-radio.png", "Settings: the radio and its address, Find, Tezuka's 8-bit receive, and IQ recordings"),
    ("settings-transmit.png", "Settings: real-hardware transmit, the power ceiling and the timers"),
    ("main-light-1920.png", "The light theme"),
]
# Regions of the screenshots cut out for the guide, which shows one card at
# a time where the whole window would be unreadable at page width.
# name: (source, (left, top, right, bottom) in source pixels)
CROPS = {
    # (The FM transmit panel grew a Format row: 900, not 860, keeps its warnings.)
    "crop-txpanel-onair.png": ("main-transmitting-1920.png", (1508, 118, 1910, 900)),
    "crop-txpanel-idle.png": ("main-fm-23cm-1920.png", (1508, 118, 1910, 900)),
    "crop-txpanel-minimac.png": ("main-minimac-1920.png", (1508, 118, 1910, 860)),
    "crop-plan-minimac.png": ("main-minimac-1920.png", (1036, 394, 1496, 690)),
    "crop-plan-am.png": ("main-am-1275-1920.png", (1036, 394, 1496, 690)),
    "crop-band-6cm.png": ("main-6cm-1920.png", (1116, 704, 1496, 1040)),
    # (The AM transmit panel grew a Format row too, for 405 lines.)
    "crop-70cm-warning.png": ("main-70cm-1920.png", (1508, 680, 1910, 930)),
    "crop-spectrum-am.png": ("main-am-1275-1920.png", (10, 704, 1106, 1040)),
}
NOTES_RELEASES = 12
SKIP = re.compile(r"^(Merge |Release )")
INLINE_MAX_WIDTH = 960      # inline screenshot width on the page
GUIDE_MAX_WIDTH = 1500      # guide images: about 220 dpi across an A4 text column
NOT_IN_A_DEB = [":(exclude)site", ":(exclude)docs/guide", ":(exclude)tools/site-build.py",
                ":(exclude)tools/guide-pdf.py", ":(exclude)tools/release.sh"]


# ---------------------------------------------------------------- versions
def _version_key(name: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", name)]


def latest_deb(folder: Path) -> Path | None:
    debs = sorted(folder.glob("openhamshack-atv_*_all.deb"), key=lambda p: _version_key(p.stem))
    return debs[-1] if debs else None


def deb_version(deb: Path | None) -> str | None:
    m = re.match(r"^openhamshack-atv_(.+)_all\.deb$", deb.name) if deb else None
    return m.group(1) if m else None


def _sha256(path: Path) -> str:
    """A .sha256 beside the file is trusted only if it names this file;
    otherwise the hash is computed from the bytes."""
    sha_file = path.with_name(path.name + ".sha256")
    if sha_file.exists():
        parts = sha_file.read_text().split()
        if len(parts) >= 2 and parts[1].lstrip("*") == path.name:
            return parts[0]
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage_deb(releases: Path, out: Path) -> Path | None:
    """Copy the newest .deb from releases into out with a .sha256 beside it
    (sha256sum format, so `sha256sum -c` works on the downloaded pair), and
    remove older ones from out."""
    deb = latest_deb(releases)
    if deb is None:
        return None
    digest = _sha256(deb)
    dst = out / deb.name
    if deb.resolve() != dst.resolve():
        shutil.copy2(deb, dst)
    (out / (deb.name + ".sha256")).write_text(f"{digest}  {deb.name}\n")
    if hashlib.sha256(dst.read_bytes()).hexdigest() != digest:
        sys.exit(f"site-build: {dst} does not match the checksum of {deb}")
    for old in out.glob("openhamshack-atv_*_all.deb*"):
        if not old.name.startswith(deb.name):
            print(f"site-build: removing {old.name} from {out} (superseded by {deb.name})")
            old.unlink()
    return dst


def latest_exe(folder: Path) -> Path | None:
    """The newest Windows installer, by version number as the .deb is."""
    exes = sorted(folder.glob("openhamshack-atv-*-setup.exe"), key=lambda p: _version_key(p.stem))
    return exes[-1] if exes else None


def stage_exe(releases: Path, out: Path, deb: Path | None) -> Path | None:
    """The Windows installer, staged as the .deb is - but only when it is
    the same version as the .deb on offer. From 0.2.2 (8 October 2026) a
    release can carry one; an installer left from an older release beside
    a newer .deb would be offered as current when it is not, so it is left
    off the page and the build says so. Older installers in out are
    removed, printed, as older .debs are: the page offers one of each."""
    exe = latest_exe(releases)
    version = deb_version(deb)
    if exe is not None and version and exe.name != f"openhamshack-atv-{version}-setup.exe":
        print(f"site-build: not offering {exe.name}: the .deb on offer is {version}")
        exe = None
    if exe is not None:
        digest = _sha256(exe)
        dst = out / exe.name
        if exe.resolve() != dst.resolve():
            shutil.copy2(exe, dst)
        (out / (exe.name + ".sha256")).write_text(f"{digest}  {exe.name}\n")
        if hashlib.sha256(dst.read_bytes()).hexdigest() != digest:
            sys.exit(f"site-build: {dst} does not match the checksum of {exe}")
    for old in out.glob("openhamshack-atv-*-setup.exe*"):
        if exe is None or not old.name.startswith(exe.name):
            print(f"site-build: removing {old.name} from {out}" + (f" (superseded by {exe.name})" if exe else ""))
            old.unlink()
    return (out / exe.name) if exe is not None else None


# ---------------------------------------------------------------- notes
def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout


def releases(current: str | None) -> list[tuple[str, str, list[str]]]:
    """(version, date, [subjects]) newest first. Each release's notes are the
    commits reachable from it and not from the release before it - by date
    alone a branch merged late would land under the wrong release."""
    log = _git("log", "--first-parent", "--date=short", "--format=%H%x09%ad%x09%s")
    marks = [(sha, date, m.group(1)) for sha, date, subj in (l.split("\t", 2) for l in log.splitlines())
             if (m := re.match(r"^Release (\d+\.\d+\.\d+)$", subj))]
    out = []
    if current and current not in [v for _, _, v in marks]:
        rng = [f"{marks[0][0]}..HEAD"] if marks else ["HEAD"]
        subjects = _git("log", "--no-merges", "--format=%s", *rng, "--", ".", *NOT_IN_A_DEB).splitlines()
        out.append((current, datetime.date.today().isoformat(), [s for s in subjects if not SKIP.match(s)]))
    for (sha, date, ver), older in zip(marks, marks[1:] + [None]):
        rng = f"{older[0]}..{sha}" if older else sha
        subjects = _git("log", "--no-merges", "--format=%s", rng).splitlines()
        out.append((ver, date, [s for s in subjects if not SKIP.match(s)]))
    return out


def notes_html(rels: list[tuple[str, str, list[str]]]) -> str:
    parts = []
    for ver, date, subjects in rels[:NOTES_RELEASES]:
        items = "".join(f"<li>{html.escape(s)}</li>" for s in subjects) or "<li>Maintenance release.</li>"
        parts.append(f"<h3>{html.escape(ver)} <span class=\"meta\">{html.escape(date)}</span></h3><ul>{items}</ul>")
    return "\n".join(parts) if parts else '<span class="meta">No release yet.</span>'


# ---------------------------------------------------------------- download
SOURCE_SITE = "https://source.brian-henderson.co.uk/#openhamshack-atv"

def download_html(deb: Path | None, exe: Path | None = None) -> str:
    if deb is None:
        return '<span class="meta">No release has been published yet; check back shortly.</span>'
    size_mb = deb.stat().st_size / 1e6
    digest = _sha256(deb)
    linux = (f'<h3 class="sub">Linux</h3>'
             f'<a class="btn" href="{html.escape(deb.name)}">Download {html.escape(deb.name)}</a>'
             f'<span class="meta">{size_mb:.2f} MB · Debian, Ubuntu, Raspberry Pi OS (all architectures)</span>'
             f'<span class="meta">SHA-256 <span class="mono">{html.escape(digest)}</span> '
             f'(<a href="{html.escape(deb.name)}.sha256">.sha256</a>)</span>'
             f'<pre class="mono">sudo apt install ./{html.escape(deb.name)}</pre>')
    if exe is None:
        # The shared source site lists, per OpenHamShack program, the third-party
        # GPL/LGPL source its installers carry. The .deb carries none (its
        # dependencies come from Debian), and that page says so; the link is
        # kept so every OpenHamShack download page points to the same place.
        return linux + (f'<span class="meta">Third-party source: '
                        f'<a href="{SOURCE_SITE}">{SOURCE_SITE}</a> (none bundled; '
                        f'GPL-3.0 program, dependencies from Debian)</span>')
    return linux + windows_html(exe) + (
        # GPL-3 6(d) and the LGPL allow the source to be on another server
        # provided the page offering the installer gives clear directions to
        # it, saying which licences require it: this is that sentence.
        f'<span class="meta">Source: the Windows installer carries code by others under the GPL, LGPL and '
        f'MPL - Qt, PyQt6, FFmpeg, libiio, libhackrf, libusb and more - whose source, and OpenHamShack ATV\'s own '
        f'for this release, is at <a href="{SOURCE_SITE}">{SOURCE_SITE}</a>. The .deb carries none of it.</span>')


def windows_html(exe: Path) -> str:
    """The Windows installer and what is true of it. Brian chose to publish
    the first one (0.2.2, 8 October 2026) before it had met a radio: it was
    built, installed and run on a Windows 11 test machine with no Pluto, no
    HackRF and no camera, so the note says exactly that and nothing more -
    see HANDOVER.md's Windows section for what is owed. Signing and
    SmartScreen are as for NB's installer, whose note this follows."""
    size_mb = exe.stat().st_size / 1e6
    digest = _sha256(exe)
    return (f'<h3 class="sub">Windows 10/11 (64-bit) <span class="pill">New, untested with radios</span></h3>'
            f'<a class="btn" href="{html.escape(exe.name)}">Download {html.escape(exe.name)}</a>'
            f'<span class="meta">{size_mb:.1f} MB · SHA-256 <span class="mono">{html.escape(digest)}</span> '
            f'(<a href="{html.escape(exe.name)}.sha256">.sha256</a>)</span>'
            '<p class="note">The Windows build is new in this release. It has been installed and run on a '
            'Windows 11 test machine, on the simulator, and its tests pass there - but it has <b>not yet been '
            'used with a real Pluto, HackRF or webcam on Windows</b>. Expect rough edges, and please report what '
            'you find. Linux remains the tried platform.</p>'
            '<p class="note">Signed by <b>360 Technology</b>. While Windows is still learning a new publisher, '
            'SmartScreen may show "Windows protected your PC": choose <b>More info</b>, check it names 360 '
            'Technology, then <b>Run anyway</b>. S mode cannot run it. It installs for your account only. For a '
            'Pluto, libiio is built in and the installer offers Analog Devices\' <b>PlutoSDR USB drivers</b>: '
            'leave it ticked, answer <b>Install</b> when Windows asks about their device software, and do it '
            'before plugging the Pluto in. FFmpeg (webcam and video files) and libhackrf are built in. It does '
            'not update itself yet: download the next release from here.</p>')


def guide_html(pdf: Path | None, pages: int | None) -> str:
    links = '<a class="btn ghost" href="guide/">Read it online</a>'
    if pdf is not None and pdf.exists():
        n = f", {pages} pages" if pages else ""
        links = (f'<a class="btn" href="{PDF_NAME}">Download the guide (PDF, A4{n})</a>'
                 f'<span class="meta">{pdf.stat().st_size / 1e6:.1f} MB</span>' + links)
    return links


# ---------------------------------------------------------------- images
def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))


def _save(im, dst: Path, quality: int) -> Path:
    """JPEG, as dst with a .jpg suffix. Returns the path written."""
    dst = dst.with_suffix(".jpg")
    im.convert("RGB").save(dst, "JPEG", quality=quality, optimize=True, progressive=True)
    return dst


def image(src: Path, dst: Path, max_width: int, quality: int = 88, box=None) -> tuple[Path, tuple[int, int] | None]:
    """Write src (optionally cropped to box) to dst at most max_width wide.
    Returns (path written - its suffix may differ from dst's -, (w, h))."""
    if Image is None:
        if box is not None:
            print(f"site-build: no Pillow; {dst.name} is the whole of {src.name}, not the region")
        shutil.copy2(src, dst.with_suffix(".png"))
        return dst.with_suffix(".png"), _png_size(src.read_bytes())
    with Image.open(src) as im:
        if box is not None:
            im = im.crop(box)
        w, h = im.size
        if w > max_width:
            h, w = round(h * max_width / w), max_width
            im = im.resize((w, h), Image.LANCZOS)
        return _save(im, dst, quality), (w, h)


def shots_html(out: Path) -> tuple[str, tuple[str, tuple[int, int] | None] | None]:
    """The screenshot grid, and (path under out, size) of the first full-size
    shot, which is the page's og:image: SHOTS is ordered so the first is the
    picture that says what the program is."""
    if Image is None:
        print("site-build: Pillow is not installed (python3-pil); screenshots are copied at full size")
    d = out / "shots"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir()
    figs, first = [], None
    for name, caption in SHOTS:
        src = SHOT_DIR / name
        if not src.exists():
            continue
        full, full_dims = image(src, d / name, 10_000, quality=90)
        first = first or (f"shots/{full.name}", full_dims)
        small, dims = image(src, d / f"sm-{name}", INLINE_MAX_WIDTH, quality=85)
        attrs = f' width="{dims[0]}" height="{dims[1]}"' if dims else ""
        cap = html.escape(caption)
        f, s = html.escape(full.name), html.escape(small.name)
        figs.append(f'<figure><a href="shots/{f}"><img src="shots/{s}"{attrs} alt="{cap}" loading="lazy"></a>'
                    f'<figcaption>{cap} &middot; <a href="shots/{f}">full size</a></figcaption></figure>')
    return (f'<section class="grid">{"".join(figs)}</section>' if figs else ""), first


# ---------------------------------------------------------------- search and previews
# What the SEO audit of 6 October 2026 (~/seo-audit-2026-10-06.md, section 5)
# asked for, made here rather than written into the pages so that the URLs,
# the image sizes and the version always match what this build staged.
HUB = "https://brian-henderson.co.uk/"
SIBLINGS = [("OpenHamShack QO-100 WB", "https://openhamshack.brian-henderson.co.uk/"),
            ("OpenHamShack QO-100 NB", "https://openhamshacknb.brian-henderson.co.uk/"),
            ("OpenHamShack Radio", "https://openhamshackham.brian-henderson.co.uk/")]


def og_image_tags(url: str, shot: tuple[str, tuple[int, int] | None] | None) -> str:
    if shot is None:
        return ""
    path, dims = shot
    tags = f'<meta property="og:image" content="{html.escape(url + path)}">'
    if dims:
        tags += (f'\n<meta property="og:image:width" content="{dims[0]}">'
                 f'\n<meta property="og:image:height" content="{dims[1]}">')
    return tags


def jsonld(url: str, deb: Path | None, version: str | None, exe: Path | None = None) -> str:
    """schema.org SoftwareApplication for the home page. An unreleased build
    leaves out the version and the download rather than inventing them.
    The licence is given as its SPDX URL: schema.org's `license` is a URL or
    a CreativeWork, and a bare identifier is neither, so validators flag it."""
    data = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "OpenHamShack ATV",
        "description": "Analogue amateur television (AM-VSB and FM-ATV) on 70cm, 23cm and 6cm, from a Linux "
                       "laptop and a Pluto-family SDR.",
        "operatingSystem": "Linux, Windows 10/11 (64-bit)" if exe is not None else "Linux",
        "applicationCategory": "MultimediaApplication",
        "license": "https://spdx.org/licenses/GPL-3.0-or-later.html",
        "url": url,
        "author": {"@type": "Person", "name": "Brian Henderson", "alternateName": "GM4BRI", "url": HUB},
    }
    if deb is not None and version:
        data["softwareVersion"] = version
        data["downloadUrl"] = url + deb.name
    # "</" must not appear inside a <script> element; JSON allows "<\/" for it.
    body = json.dumps(data, indent=1, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/ld+json">\n{body}\n</script>'


def source_html(deb: Path | None, exe: Path | None = None) -> str:
    """Where the source is, beside the licence. The program is pure Python
    and the .deb carries every source file with nothing compiled, so the .deb
    is the GPL source of the program itself; the shared source site is for
    third-party source, of which this package bundles none and says so.
    With a Windows installer on offer (bytecode, beside PyQt6), the
    program's own source for the release is a tarball on the source site
    too, and the footer says so."""
    if exe is not None:
        return (f'Source of the program and of everything the Windows installer carries: '
                f'<a href="{SOURCE_SITE}">source.brian-henderson.co.uk</a>.')
    own = (f'the program\'s own source is the <a href="{html.escape(deb.name)}">.deb</a> itself (pure Python, '
           f'nothing compiled); third-party source is at ' if deb is not None else "Third-party source is at ")
    return f'{own[0].upper()}{own[1:]}<a href="{SOURCE_SITE}">source.brian-henderson.co.uk</a>.'


# ---------------------------------------------------------------- guide
def build_guide(out: Path, version: str, url: str) -> Path:
    """docs/guide/index.html -> out/guide/index.html, with every img/<name>
    it references made from docs/screenshots (resized, or cut out by
    CROPS). The source names the PNG; what is written may be a .jpg, and
    the reference is rewritten to match."""
    gdir = out / "guide"
    if gdir.exists():
        shutil.rmtree(gdir)
    (gdir / "img").mkdir(parents=True)
    page = GUIDE_SRC.read_text()
    dims: dict[str, tuple[int, int] | None] = {}
    for name in sorted(set(re.findall(r'src="img/([^"]+\.png)"', page))):
        if name in CROPS:
            src_name, box = CROPS[name]
            src = SHOT_DIR / src_name
        else:
            src, box = SHOT_DIR / name, None
        if not src.exists():
            sys.exit(f"site-build: the guide uses img/{name}, but {src} does not exist")
        written, size = image(src, gdir / "img" / name, GUIDE_MAX_WIDTH, quality=88, box=box)
        dims[f"img/{written.name}"] = size
        page = page.replace(f'src="img/{name}"', f'src="img/{written.name}"')
    # The icon is drawn on a square canvas and the cover's CSS shows it at 96 x 96.
    dims["img/openhamshack-atv.svg"] = (96, 96)
    page = _guide_nav(_guide_head(_guide_imgs(page, dims), url, dims))
    today = datetime.date.today()
    for key, value in {"{{VERSION}}": html.escape(version), "{{DATE}}": f"{today.day} {today:%B %Y}",
                       "{{PDF}}": f"../{PDF_NAME}"}.items():
        page = page.replace(key, value)
    icon = ROOT / "packaging" / "openhamshack-atv.svg"
    shutil.copy2(icon, gdir / "img" / "openhamshack-atv.svg")
    shutil.copy2(ROOT / "site" / "favicon.svg", gdir / "favicon.svg")
    (gdir / "index.html").write_text(page)
    return gdir / "index.html"


def _guide_imgs(page: str, dims: dict[str, tuple[int, int] | None]) -> str:
    """Every <img> gets its real width and height, so the page does not jump
    as images arrive, and loading="lazy". The cover's logo gets alt text: it
    is the program's own mark, not decoration. Done here, not in the source,
    because the sizes are those of the files this build wrote, and another
    session edits the source."""
    def fix(m: re.Match) -> str:
        tag = m.group(0)
        src = re.search(r'src="([^"]+)"', tag).group(1)
        add = ""
        if " width=" not in tag and dims.get(src):
            add += f' width="{dims[src][0]}" height="{dims[src][1]}"'
        if " loading=" not in tag:
            add += ' loading="lazy"'
        if src == "img/openhamshack-atv.svg":
            tag = tag.replace('alt=""', 'alt="OpenHamShack ATV"')
        return tag[:-1] + add + ">"
    return re.sub(r"<img\b[^>]*>", fix, page)


GUIDE_OG_IMAGE = "img/main-fm-23cm-1920.jpg"   # the guide's own picture of the main window
GUIDE_NAV_CSS = """<style>
/* Added by tools/site-build.py: the way back to the site, the hub and the
   siblings. Screen only (.screen-only) - a printed guide has no use for it. */
nav.site { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 14px; color: var(--dim);
  padding: 12px 0; border-bottom: 1px solid var(--line); }
nav.site.foot { border-bottom: 0; border-top: 1px solid var(--line); margin-top: 40px; }
nav.site a { color: var(--dim); }
nav.site a.home { color: var(--accent); font-weight: 700; }
</style>"""


def _guide_head(page: str, url: str, dims: dict[str, tuple[int, int] | None]) -> str:
    """Canonical, Open Graph and Twitter tags for the guide, placed after its
    own description, which they reuse. The picture falls back to the first
    image if the preferred one leaves the guide."""
    gurl = url + "guide/"
    title = re.search(r"<title>(.*?)</title>", page, re.S).group(1)
    desc = re.search(r'<meta name="description" content="([^"]*)">', page)
    img = GUIDE_OG_IMAGE if GUIDE_OG_IMAGE in dims else next(
        (k for k in dims if k.endswith((".jpg", ".png"))), None)
    tags = [f'<link rel="canonical" href="{html.escape(gurl)}">',
            '<meta property="og:type" content="article">',
            f'<meta property="og:title" content="{title}">',
            f'<meta property="og:url" content="{html.escape(gurl)}">']
    if desc:
        tags.append(f'<meta property="og:description" content="{desc.group(1)}">')
    if img:
        tags.append(f'<meta property="og:image" content="{html.escape(gurl + img)}">')
        if dims.get(img):
            tags += [f'<meta property="og:image:width" content="{dims[img][0]}">',
                     f'<meta property="og:image:height" content="{dims[img][1]}">']
    tags.append('<meta name="twitter:card" content="summary_large_image">')
    anchor = desc.group(0) if desc else f"<title>{title}</title>"
    page = page.replace(anchor, anchor + "\n" + "\n".join(tags), 1)
    return page.replace("</head>", GUIDE_NAV_CSS + "\n</head>", 1)


def _guide_nav(page: str) -> str:
    """A small nav at the top and bottom of the guide: until this, the PDF
    was the page's only link out (SEO audit, section 5)."""
    links = ['<a class="home" href="../">OpenHamShack ATV home</a>', '<a href="../#download">Download</a>',
             f'<a href="{HUB}">brian-henderson.co.uk</a>']
    links += [f'<a href="{u}">{html.escape(n)}</a>' for n, u in SIBLINGS]
    head = f'<nav class="site screen-only" aria-label="Site">{"".join(links)}</nav>'
    foot = (f'<nav class="site foot screen-only" aria-label="Site">{"".join(links)}'
            f'<a href="{SOURCE_SITE}">Source</a></nav>')
    if page.count("<main>") != 1 or page.count("</main>") != 1:
        sys.exit("site-build: the guide no longer has exactly one <main>; the site nav goes inside it")
    return page.replace("<main>", "<main>\n" + head, 1).replace("</main>", foot + "\n</main>", 1)


def pdf_pages(pdf: Path) -> int | None:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(pdf)).pages)
    except Exception:  # noqa: BLE001 - a count is a nicety; fall back to pdfinfo
        pass
    if shutil.which("pdfinfo"):
        m = re.search(r"^Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(pdf)], capture_output=True,
                                                         text=True).stdout, re.M)
        return int(m.group(1)) if m else None
    return None


# ---------------------------------------------------------------- main
def main() -> None:
    home = Path.home()
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(home / "openhamshack-atv-dist" / "site"))
    ap.add_argument("--releases", default=str(home / "openhamshack-atv-dist"),
                    help="where the .debs are built (the newest is published)")
    ap.add_argument("--url", default="https://openhamshackatv.brian-henderson.co.uk/")
    ap.add_argument("--no-pdf", action="store_true", help="skip printing the guide PDF")
    a = ap.parse_args()
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    url = a.url if a.url.endswith("/") else a.url + "/"

    deb = stage_deb(Path(a.releases).expanduser(), out)
    exe = stage_exe(Path(a.releases).expanduser(), out, deb)
    version = deb_version(deb) or "unreleased"

    guide = build_guide(out, version, url)
    pdf, pages = None, None
    if not a.no_pdf:
        sys.path.insert(0, str(ROOT / "tools"))
        gp = __import__("guide-pdf")
        pdf = out / PDF_NAME
        how = gp.render(guide, pdf, f"OpenHamShack ATV {version} user guide")
        pages = pdf_pages(pdf)
        print(f"site-build: {PDF_NAME}, {pages} pages, via {how}")

    shots, first_shot = shots_html(out)
    page = (ROOT / "site" / "index.html").read_text()
    for key, value in {
        "{{VERSION}}": html.escape(version),
        "{{SITE_URL}}": html.escape(url),
        "{{OG_IMAGE}}": og_image_tags(url, first_shot),
        "{{JSONLD}}": jsonld(url, deb, deb_version(deb), exe),
        "{{SOURCE}}": source_html(deb, exe),
        "{{DOWNLOAD}}": download_html(deb, exe),
        "{{GUIDE}}": guide_html(pdf, pages),
        "{{SHOTS}}": shots,
        "{{NOTES}}": notes_html(releases(deb_version(deb))),
        "{{BUILT}}": datetime.date.today().isoformat(),
    }.items():
        page = page.replace(key, value)
    left = re.findall(r"\{\{[A-Z_]+\}\}", page)
    if left:
        sys.exit(f"site-build: unfilled placeholders in site/index.html: {', '.join(sorted(set(left)))}")
    shutil.copy2(ROOT / "site" / "favicon.svg", out / "favicon.svg")
    tmp = out / "index.html.tmp"
    tmp.write_text(page)
    tmp.replace(out / "index.html")
    write_crawl_files(out, url, datetime.date.today().isoformat())
    write_server_files(out, url)
    print(f"site built in {out} for {version}")


def write_crawl_files(out: Path, url: str, today: str) -> None:
    """robots.txt and sitemap.xml, added to the live site by hand on 6
    October 2026 at Brian's request. They are written here so a rebuild keeps
    them and every page this script regenerates gets today's <lastmod>; a new
    .html page the build makes must be added to `pages` below."""
    pages = ["", "guide/"]
    urls = "".join(f"  <url><loc>{html.escape(url + p)}</loc><lastmod>{today}</lastmod></url>\n"
                   for p in pages)
    (out / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                                     '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                                     f"{urls}</urlset>\n")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {url}sitemap.xml\n")


# The site's own server rules. The host is 20i: Apache, with .htaccess and
# mod_rewrite/mod_headers allowed, behind 20i's StackCDN, which ends TLS and
# talks plain HTTP to Apache. There was no .htaccess in this staging
# directory before 6 October 2026; if one appears on the live site that this
# build did not write (a hosting control panel can add one), merge it into
# this text rather than letting an upload overwrite it.
HTACCESS = """\
# Written by tools/site-build.py in the openhamshack-atv repository; change it
# there, or the next build puts this back.

AddDefaultCharset utf-8
ErrorDocument 404 /404.html

# A directory asked for without its slash (/guide) is redirected here, to
# an absolute https:// URL, before mod_dir sees it. Apache's own slash
# redirect builds its Location from the scheme it was reached by, and behind
# the CDN that is plain http, so /guide went https -> http -> https: a
# plaintext hop a scanner flags (SEO audit, server-level item 3). mod_rewrite's
# per-directory rules run in the fixup phase ahead of mod_dir's, so this
# answers first and DirectorySlash can stay on as the fallback.
<IfModule mod_rewrite.c>
RewriteEngine On
RewriteCond %{{REQUEST_FILENAME}} -d
RewriteRule ^(.*[^/])$ {url}$1/ [R=301,L]
</IfModule>

# Caching. A file whose name carries the version (the .deb, the Windows
# installer and their .sha256 files) never changes in place, so it is cached for a year and never revalidated.
# Everything else is renamed by nothing: the pages, robots.txt, sitemap.xml,
# the guide PDF (openhamshack-atv-guide.pdf, the same name every release) and the
# screenshots (same names, re-cut when the window changes). Those get ten
# minutes, after which the browser and the CDN revalidate with the ETag.
<IfModule mod_headers.c>
<FilesMatch "\\.(deb|exe|sha256)$">
Header set Cache-Control "public, max-age=31536000, immutable"
</FilesMatch>
<FilesMatch "\\.(html|txt|xml|pdf|jpg|png|svg)$">
Header set Cache-Control "public, max-age=600"
</FilesMatch>
</IfModule>
"""

NOT_FOUND = """\
<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Not found · OpenHamShack ATV</title>
<meta name="robots" content="noindex">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<style>
/* The site's tokens, inline: this page is served at whatever path was
   missing, so it can rely on nothing beside it (and its links are rooted). */
:root {{ --bg: #0a0d12; --card: #10141b; --ink: #e8edf4; --dim: #a3adbb; --line: #232a36; --accent: #4fd1e8; }}
@media (prefers-color-scheme: light) {{
  :root {{ --bg: #eef1f5; --card: #ffffff; --ink: #0f1722; --dim: #4a5566; --line: #dde2e9; --accent: #0a8fb0; }}
}}
html, body {{ margin: 0; background: var(--bg); color: var(--ink); }}
body {{ font: 17px/1.55 "DejaVu Sans", "Segoe UI", system-ui, -apple-system, sans-serif; }}
a {{ color: var(--accent); }}
.wrap {{ max-width: 640px; margin: 0 auto; padding: 64px 16px; }}
.card {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 20px; }}
h1 {{ margin: 0 0 8px; font-size: 32px; }}
p {{ margin: 8px 0; }}
.meta {{ color: var(--dim); font-size: 14px; }}
</style>
</head>
<body>
<div class="wrap"><div class="card">
  <h1>Not found</h1>
  <p>There is nothing at this address on the OpenHamShack ATV site.</p>
  <p><a href="/">OpenHamShack ATV home</a> · <a href="/guide/">User guide</a> · <a href="/#download">Download</a></p>
  <p class="meta">Elsewhere: <a href="{hub}">brian-henderson.co.uk</a>{siblings}</p>
</div></div>
</body>
</html>
"""


def write_server_files(out: Path, url: str) -> None:
    """.htaccess and 404.html. Both assume the site is the root of its host
    (ErrorDocument and the 404 page's links are rooted at /), so a --url with
    a path is refused rather than built wrong."""
    if re.match(r"^https?://[^/]+/$", url) is None:
        sys.exit(f"site-build: .htaccess and 404.html assume the site is a host's root; {url} is not")
    (out / ".htaccess").write_text(HTACCESS.format(url=url))
    sib = "".join(f' · <a href="{u}">{html.escape(n)}</a>' for n, u in SIBLINGS)
    (out / "404.html").write_text(NOT_FOUND.format(hub=HUB, siblings=sib))


if __name__ == "__main__":
    main()
