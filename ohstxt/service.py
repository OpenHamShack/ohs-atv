"""A service: one folder of .tti files, one page (with its subpages) per file.

The folder is the unit because it is what an inserter takes: vbit2 is
pointed at a directory and transmits every .tti in it, re-reading a file
when it changes; OpenHamShack ATV's teletext insertion takes the same folder.
So the service has no index file of its own to drift out of step with the
pages - the folder listing *is* the service, and saving a page is writing
its one file.

New pages are named P<mpp>.tti; a page that came from elsewhere keeps its
own file name, so saving it here does not leave a stale twin beside it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import tti
from .page import Page


@dataclass
class Service:
    folder: Path
    pages: list[Page] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)   # files that could not be read, and why

    @classmethod
    def open(cls, folder) -> "Service":
        folder = Path(folder)
        s = cls(folder=folder)
        if folder.is_dir():
            for p in sorted(folder.iterdir()):
                if p.suffix.lower() != ".tti" or p.name.startswith(".") or not p.is_file():
                    continue
                try:
                    s.pages.append(tti.load(p))
                except (OSError, ValueError) as e:
                    s.problems.append(f"{p.name}: {e}")
        s.sort()
        return s

    def sort(self):
        self.pages.sort(key=lambda pg: (pg.number, pg.filename))

    def duplicates(self) -> dict[int, list[str]]:
        """Page numbers claimed by more than one file - an inserter would
        transmit one and hide the other."""
        seen: dict[int, list[str]] = {}
        for pg in self.pages:
            seen.setdefault(pg.number, []).append(pg.filename or pg.label())
        return {k: v for k, v in seen.items() if len(v) > 1}

    def path_for(self, page: Page) -> Path:
        return self.folder / (page.filename or f"P{page.number:03X}.tti")

    def save(self, page: Page) -> Path:
        p = self.path_for(page)
        tti.save(page, p)
        return p

    def find(self, number: int) -> Page | None:
        return next((pg for pg in self.pages if pg.number == number), None)

    def free_number(self, start: int = 0x100) -> int:
        used = {pg.number for pg in self.pages}
        n = start
        while n in used or (n & 0xFF) > 0x99:
            n += 1
            if (n & 0xFF) > 0x99:
                n = ((n >> 8) + 1) << 8
            if n > 0x899:
                return start
        return n

    def remove(self, page: Page, delete_file: bool = True):
        if delete_file and page.filename:
            try:
                (self.folder / page.filename).unlink()
            except FileNotFoundError:
                pass
        self.pages = [p for p in self.pages if p is not page]
