"""Render the screenshots in docs/screenshots from the sample service.

    QT_QPA_PLATFORM=offscreen python3 -m ohstxt.ui.screenshots [outdir] [--only name,...]

Runs the real window on a throw-away copy of the sample service (a fresh
config, data and preferences directory, so nothing of the operator's is
touched), drives it the way an operator would, and grabs it. The page
renders (page-*.png) are the PNG export itself. The clock in the header is
held at one moment so the pictures do not change from run to run.

The picture used for the image-import shot is drawn here - a sunset over
the sea with an aerial on a mast - so no photograph is copied into the
repository.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

FIXED_NOW = time.mktime((2026, 10, 5, 20, 15, 30, 0, 0, -1))


def synthetic_picture(w: int = 640, h: int = 480):
    """A sunset: sky gradient, sun, sea with reflections, a mast with an aerial."""
    import numpy as np
    from PIL import Image, ImageDraw
    y = np.linspace(0, 1, h)[:, None]
    sky = np.stack([255 * (0.25 + 0.75 * y), 120 * y + 40 * (1 - y), 160 * (1 - y) + 30 * y], -1)
    img = np.broadcast_to(sky, (h, w, 3)).astype(np.uint8).copy()
    horizon = int(h * 0.62)
    sea = np.linspace(0, 1, h - horizon)
    img[horizon:] = np.stack([20 + 30 * sea, 40 + 40 * sea, 120 + 80 * sea], -1).astype(np.uint8)[:, None, :]
    im = Image.fromarray(img)
    d = ImageDraw.Draw(im)
    d.ellipse((w * 0.55, h * 0.36, w * 0.75, h * 0.62), fill=(255, 220, 60))
    d.rectangle((0, horizon, w, horizon + 2), fill=(255, 200, 80))
    for i in range(8):
        yy = horizon + 12 + i * 18
        d.rectangle((w * 0.58 + i * 3, yy, w * 0.72 - i * 3, yy + 4), fill=(255, 210, 90))
    mx = int(w * 0.2)
    d.rectangle((mx - 4, int(h * 0.25), mx + 4, horizon), fill=(10, 10, 20))
    for k, half in enumerate((60, 45, 32)):
        yy = int(h * 0.27) + k * 22
        d.rectangle((mx - half, yy, mx + half, yy + 4), fill=(10, 10, 20))
    d.polygon([(0, horizon), (w * 0.35, horizon - 40), (w * 0.5, horizon)], fill=(30, 60, 30))
    return im


# An invented scene for the subtitle shot: two speakers, one line too long.
SAMPLE_SRT = """1
00:00:01,000 --> 00:00:03,200
<font color="#ffff00">Is the camera running?</font>

2
00:00:03,600 --> 00:00:06,000
<font color="#00ffff">Running, and the teletext is on air.</font>

3
00:00:06,400 --> 00:00:09,000
<font color="#ffff00">Then wave to everyone on 23cm!</font>
<font color="#00ffff">Hello from the club shack.</font>

4
00:00:09,400 --> 00:00:12,000
And this line is far too long to fit on one subtitle row

5
00:00:12,400 --> 00:00:14,000
[MUSIC]
"""


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    only = None
    args = []
    it = iter(argv)
    for a in it:
        if a == "--only":
            only = set(next(it, "").split(","))
        elif not a.startswith("--"):
            args.append(a)
    out = Path(args[0]) if args else Path(__file__).resolve().parents[2] / "docs" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="ohstxt-shots-"))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["OHSTXT_UI_CONF"] = str(tmp / "ui.json")
    os.environ["OHSTXT_DATA"] = str(tmp / "data")

    from PyQt6.QtCore import QCoreApplication, QPoint, Qt
    from PyQt6.QtTest import QTest

    from . import build, make_app, shutdown
    from .prefs import Prefs

    app = make_app(["shots"])

    def wait(sec=0.3):
        end = time.monotonic() + sec
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.005)

    def want(name):
        return not only or name in only

    def shot(widget, name):
        if not want(name):
            return
        wait(0.4)
        p = out / f"{name}.png"
        widget.grab().save(str(p))
        print("wrote", p, flush=True)

    prefs = Prefs(tmp / "ui.json")
    win = build(prefs, tmp / "service")
    win.ask_before_discard = False
    win.preview.now = FIXED_NOW
    win.resize(1920, 1080)
    win.show()
    wait(0.5)

    def goto(number, sub=0):
        for i, pg in enumerate(win.service.pages):
            if pg.number == number:
                win.select_page(i)
                win.doc.sub_index = sub
                win._subpage_chosen()
                win.subpanel.load()
                wait(0.2)
                return
        raise KeyError(number)

    # 1. the index page, as it opens
    goto(0x100)
    win.doc.goto(6, 1)
    win.canvas.setFocus()
    shot(win, "main-news-1920")

    # 2. the test page with the code view and grid
    goto(0x199)
    win.set_view(codes=True, grid=True)
    win.doc.goto(12, 9)
    win._cell()
    shot(win, "main-codes-1920")
    win.set_view(codes=False, grid=False)

    # 3. the carousel on its second subpage, with Reveal on the test page's sake
    goto(0x300, 1)
    shot(win, "main-carousel-1920")

    # 4. the newsflash: boxes over the picture in the preview
    goto(0x150)
    shot(win, "main-newsflash-1920")

    # 5. drawing: a new page, graphics codes down the left, a rectangle and a line
    win.add_page(win._blank_page(0x400))
    win.doc.page.description = "Drawing"
    d = win.doc
    for r in range(4, 16):
        d.goto(r, 0)
        d.put(0x16 if r < 10 else 0x13)
    d.goto(2, 1)
    d.type_text("Sixels: pen, line, rectangle, fill")
    d.rect(4, 13, 40, 28)
    d.fill(10, 18)
    d.line(4, 30, 76, 46)
    d.rect(50, 13, 76, 26, on=True)
    win.set_tool("pen")
    win.on_edited()
    win._refresh_panels()
    cv = win.canvas
    a = cv.sixel_rect(46, 36).center()
    QTest.mouseMove(cv, QPoint(int(a.x()), int(a.y())))
    win.set_view(grid=True)
    shot(win, "main-drawing-1920")
    win.set_view(grid=False)
    win.set_tool("text")

    # 6. image import, before committing
    pic = tmp / "sunset.png"
    synthetic_picture().save(pic)
    if want("image-import"):
        from .dialogs import ImageDialog
        dlg = ImageDialog(win.doc, str(pic), win)
        dlg.dither.set_value("floyd", animate=False)
        dlg.convert()
        dlg.resize(1280, 760)
        dlg.show()
        wait(0.5)
        shot(dlg, "image-import")
        dlg.close()
    # and committed, with no dither
    win.add_page(win._blank_page(0x401))
    win.doc.page.description = "Imported picture"
    from .. import imageconv
    res = imageconv.convert(synthetic_picture(), imageconv.Options(width=40, height=22))
    win.doc.replace_rows(res.rows, top=1)
    win.on_edited()
    win._refresh_panels()
    shot(win, "main-image-1920")

    # 7. smaller screen, light theme, shortcuts
    goto(0x100)
    win.resize(1366, 768)
    wait(0.5)
    shot(win, "main-1366")
    win.resize(1920, 1080)
    win.toggle_theme()
    wait(0.3)
    shot(win, "main-light-1920")
    win.toggle_theme()
    win.overlay.open()
    shot(win, "shortcuts-1920")
    win.overlay.close_()

    # 9. a block selection, pasted where its colour code repaints the row
    goto(0x100)
    win.doc.select(8, 32, 9, 35)                  # cyan "103" and "104", their colour codes too
    win.copy()
    win.doc.clear_selection()
    win.doc.goto(17, 0)                           # over "Test page"'s white: the rest turns cyan
    win.paste()
    win.canvas.setFocus()
    shot(win, "main-selection-1920")
    win.undo()
    win.doc.clear_selection()
    win._message("", "info")

    # 10. the service checker, a finding clicked
    from .. import station as station_mod
    st = station_mod.Station(callsign="N0CALL", name="Alex", locator="IO91wm", qth="Anytown")
    station_mod.save(st, win.service.folder)
    win.station = station_mod.load(win.service.folder)
    win.board = win._make_board()
    if want("check-1920"):
        from PyQt6.QtGui import QPainter
        dlg = win.check_service()
        dlg.resize(780, 640)
        rows = [dlg.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dlg.list.count())]
        i = next(k for k, f in enumerate(rows) if f.kind == "link" and f.page == 0x300)
        dlg.list.setCurrentRow(i)
        win.canvas.setFocus()
        wait(0.4)
        pix = win.grab()
        over = dlg.grab()
        p = QPainter(pix)
        x, y = pix.width() - over.width() - 20, pix.height() - over.height() - 50
        p.fillRect(x - 1, y - 1, over.width() + 2, over.height() + 2, Qt.GlobalColor.black)
        p.drawPixmap(x, y, over)
        p.end()
        path = out / "check-1920.png"
        pix.save(str(path))
        print("wrote", path, flush=True)
        dlg.close()

    # 11. templates: the dialog, a station page, and the live propagation page with sample data
    from .. import templates
    if want("template-dialog"):
        from .servicedlg import TemplateDialog
        tdlg = TemplateDialog(win.station, {p.number for p in win.service.pages}, win)
        tdlg.list.setCurrentRow(4)
        tdlg.show()
        wait(0.4)
        shot(tdlg, "template-dialog")
        tdlg.close()
    for t in templates.TEMPLATES:
        taken = {p.number for p in win.service.pages}
        n = next(c for c in (t.number, t.number + 0x10, t.number - 1) if c not in taken and (c & 0xFF) <= 0x99)
        win.add_page(t.build(win.station, n))       # beside the samples' own P100 and P199
    goto(0x200)
    shot(win, "main-template-1920")
    win.set_live_sample(True)
    win._message(QCoreApplication.translate("MainWindow", "Live fields show sample data (View > Live fields: sample data)"),
                 "info")
    goto(0x500)
    win.doc.goto(3, 13)
    win._cell()
    shot(win, "main-live-1920")

    # 12. the subtitle editor
    if want("subtitles-1920"):
        srt = tmp / "evening.srt"
        srt.write_text(SAMPLE_SRT, encoding="utf-8")
        win.set_subtitle_mode(True)
        win.subs.import_srt(str(srt))
        win.subs.doc.index = 2
        win.subs.load()
        wait(0.3)
        shot(win, "subtitles-1920")
        win.set_subtitle_mode(False)

    # 13. received material, with damage
    if want("receive-dialog"):
        from .. import samples as smp
        from .. import t42 as t42mod
        data = bytearray(t42mod.dumps([smp.news_index(), smp.weather_carousel()], now=FIXED_NOW))
        import random
        rnd = random.Random(7)
        for _ in range(40):                       # a weak signal: bit errors in single copies
            k = rnd.randrange(0, len(data) // 42) * 42 + rnd.randrange(2, 42)
            data[k] ^= 1 << rnd.randrange(7)
        cap = tmp / "capture.t42"
        cap.write_bytes(bytes(data))
        from .receivedlg import ReceiveDialog
        rdlg = ReceiveDialog(str(cap), {p.number for p in win.service.pages}, win)
        rdlg.list.setCurrentRow(0)
        rdlg.show()
        wait(0.4)
        shot(rdlg, "receive-dialog")
        rdlg.close()

    # 8. the pages themselves, through the PNG export
    for number, sub, name in ((0x100, 0, "page-p100"), (0x199, 0, "page-p199"), (0x300, 0, "page-p300-1"),
                              (0x150, 0, "page-p150"), (0x401, 0, "page-image"), (0x500, 0, "page-p500-live"),
                              (0x198, 0, "page-p198-testcard")):
        if not want(name):
            continue
        goto(number, sub)
        p = out / f"{name}.png"
        win.page_png(str(p), scale=2, now=FIXED_NOW)
        print("wrote", p, flush=True)
    win.close()
    shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
