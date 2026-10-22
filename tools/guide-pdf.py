#!/usr/bin/env python3
"""Print an HTML page to an A4 PDF with page numbers, using the system
chromium. tools/site-build.py calls it for the user guide.

    tools/guide-pdf.py <in.html> <out.pdf> [--title "OpenHamShack ATV user guide"]

Why this way. The guide is written as HTML (docs/guide/index.html) because
the same file is published on the site and has annotated screenshots -
numbered pins over an image - that Markdown cannot express. Of what shack
has, only a browser lays that out faithfully; weasyprint is not installed
and pandoc's LaTeX route would need the annotations redone.

`chromium --print-to-pdf` alone can print it, but only with Chromium's own
header and footer (the file:// path and a timestamp, wrong on a document
read later) or none at all - no page numbers. The DevTools protocol's
Page.printToPDF takes a footer template, and it also builds the PDF's
bookmark outline from the headings. OpenHamShack's guide gets that through
Playwright under Node; here it is the protocol spoken directly over
python3-websocket (a Debian package), so building the site needs no npm
tree. Without python3-websocket it falls back to the plain flag - a PDF
without page numbers, said on stdout, rather than no PDF.

Nothing is fetched: chromium is pointed at a local file, and the guide has
no external references (house rule).
"""
from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# A4, margins sized for a hole-punched printout as OpenHamShack's guide is
# (18 mm top/bottom, 16 mm sides). The bottom margin also holds the footer.
A4 = (8.27, 11.69)
MARGIN_IN = {"marginTop": 0.71, "marginBottom": 0.75, "marginLeft": 0.63, "marginRight": 0.63}

FOOTER = ('<div style="width:100%; font-family:\'DejaVu Sans\',sans-serif; font-size:8px; color:#6b7684; '
          'padding:0 16mm; display:flex; justify-content:space-between;">'
          '<span>{title}</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span>'
          '</div>')


def chromium() -> str:
    for name in ("chromium", "chromium-browser", "google-chrome"):
        path = shutil.which(name)
        if path:
            return path
    sys.exit("guide-pdf: no chromium found (Debian: sudo apt install chromium)")


def _base_args(profile: str) -> list[str]:
    # A throw-away profile every time: a profile left locked by an earlier
    # crashed run is what makes headless chromium fail to start.
    return [chromium(), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--disable-background-networking", f"--user-data-dir={profile}"]


def plain(src: Path, out: Path, profile: str) -> None:
    """Chromium's own printer: no page numbers, but a PDF."""
    subprocess.run(_base_args(profile) + ["--no-pdf-header-footer", f"--print-to-pdf={out}", src.as_uri()],
                   check=True, capture_output=True, timeout=180)


class Cdp:
    """Just enough of the DevTools protocol: send a command, wait for its
    reply, remember the events seen on the way."""

    def __init__(self, ws):
        self.ws, self.n, self.events = ws, 0, []

    def call(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            self.events.append(msg.get("method"))

    def wait_event(self, name: str, timeout: float) -> None:
        end = time.monotonic() + timeout
        while name not in self.events:
            if time.monotonic() > end:
                raise TimeoutError(f"no {name} within {timeout:g} s")
            self.ws.settimeout(max(0.1, end - time.monotonic()))
            msg = json.loads(self.ws.recv())
            self.events.append(msg.get("method"))
        self.ws.settimeout(None)


def with_footer(src: Path, out: Path, profile: str, title: str) -> None:
    import websocket  # python3-websocket

    proc = subprocess.Popen(_base_args(profile) + ["--remote-debugging-port=0", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        port_file = Path(profile) / "DevToolsActivePort"
        end = time.monotonic() + 30
        while not port_file.exists() or not port_file.read_text().strip():
            if proc.poll() is not None or time.monotonic() > end:
                raise RuntimeError("chromium did not start its DevTools port")
            time.sleep(0.1)
        port = port_file.read_text().split()[0]
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT")
        with urllib.request.urlopen(req, timeout=10) as r:
            target = json.load(r)
        ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=60, suppress_origin=True)
        cdp = Cdp(ws)
        cdp.call("Page.enable")
        cdp.call("Page.navigate", url=src.as_uri())
        cdp.wait_event("Page.loadEventFired", 60)
        # Fonts are local (DejaVu) but laid out after load; one frame's grace.
        cdp.call("Runtime.evaluate", expression="document.fonts.ready.then(() => true)", awaitPromise=True)
        res = cdp.call("Page.printToPDF", paperWidth=A4[0], paperHeight=A4[1], printBackground=True,
                       displayHeaderFooter=True, headerTemplate="<span></span>",
                       footerTemplate=FOOTER.format(title=title), generateDocumentOutline=True,
                       generateTaggedPDF=True, **MARGIN_IN)
        out.write_bytes(base64.b64decode(res["data"]))
        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def render(src: Path, out: Path, title: str) -> str:
    """Write out from src; returns which printer was used."""
    src, out = Path(src).resolve(), Path(out).resolve()
    tmp_out = out.with_name(out.name + ".tmp")
    # Chromium's helper processes can still be writing to the profile for a
    # moment after the browser exits; a cleanup race must not fail a build
    # whose PDF is already written.
    with tempfile.TemporaryDirectory(prefix="ohsatv-guide-", ignore_cleanup_errors=True) as profile:
        try:
            with_footer(src, tmp_out, profile, title)
            how = "chromium (DevTools, with page numbers)"
        except ImportError:
            print("guide-pdf: python3-websocket is not installed; printing without page numbers")
            plain(src, tmp_out, profile)
            how = "chromium --print-to-pdf (no page numbers)"
    tmp_out.replace(out)
    return how


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--title", default="OpenHamShack ATV user guide")
    a = ap.parse_args()
    how = render(Path(a.src), Path(a.out), a.title)
    print(f"guide-pdf: {a.out} via {how}")


if __name__ == "__main__":
    main()
