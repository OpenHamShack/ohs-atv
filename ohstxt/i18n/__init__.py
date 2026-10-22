"""Which language the window speaks (docs/I18N.md, OpenHamShack I18N.md).

English is the source language: every user-visible string is written in
English inside `self.tr()` or `QCoreApplication.translate()`, and the
catalogs beside this file carry the translations. `install()` chooses one
language and puts its catalog in front of Qt.

The choice: `SHACK_LOCALE` if set (a tag such as `de`, `pt_BR` or `en-XA`,
for trying a language or the pseudo-locale without changing the desktop's),
otherwise the desktop's own list of preferred languages, first match wins.
A language with no catalog falls through to the next, and English is always
the last word.

The English catalog is installed even when English was chosen, and under
any other language too: it holds the English plural forms, without which
`tr("%n page(s)", "", n)` would show the source text, and with it a catalog
that lacks a string falls back to proper English rather than to the source.
Qt asks the most recently installed translator first, so the chosen
language goes in after English.
"""
from __future__ import annotations

import os
from pathlib import Path

CATALOG_DIR = Path(__file__).resolve().parent
DOMAIN = "ohstxt"

# Kept here, not just installed: Qt holds a pointer, Python the object, and
# a translator Python collects is a translator Qt reads freed memory from.
_installed: list = []


def candidates(tag: str) -> list[str]:
    """Catalog suffixes for a locale tag, most specific first:
    `pt-BR.UTF-8` -> `["pt_BR", "pt"]`."""
    tag = tag.split(".")[0].split("@")[0].replace("-", "_").strip()
    if not tag or tag in ("C", "POSIX"):
        return []
    parts = tag.split("_")
    return ["_".join(parts[:i]) for i in range(len(parts), 0, -1)]


def preferred() -> list[str]:
    """The locale tags to try, in order."""
    from PyQt6.QtCore import QLocale
    override = os.environ.get("SHACK_LOCALE", "").strip()
    if override:
        return [override]
    return list(QLocale.system().uiLanguages())


def choose(tags: list[str], catalog_dir: Path = CATALOG_DIR, domain: str = DOMAIN) -> str:
    """The first catalog suffix in `tags` that has a compiled catalog, else "en"."""
    for tag in tags:
        for cand in candidates(tag):
            if (catalog_dir / f"{domain}_{cand}.qm").is_file():
                return cand
    return "en"


def install(app=None, locale: str | None = None, catalog_dir: Path = CATALOG_DIR, domain: str = DOMAIN) -> str:
    """Put the chosen language in front of Qt and return its suffix.

    Safe to call again (a test does, to switch to the pseudo-locale and
    back): whatever this module installed before is removed first.
    """
    from PyQt6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator
    for t in _installed:
        QCoreApplication.removeTranslator(t)
    _installed.clear()
    chosen = choose([locale] if locale else preferred(), catalog_dir, domain)
    files = [catalog_dir / f"{domain}_en.qm"]
    if chosen != "en":
        files.append(catalog_dir / f"{domain}_{chosen}.qm")
    for f in files:
        t = QTranslator()
        if t.load(str(f)):
            QCoreApplication.installTranslator(t)
            _installed.append(t)
    if chosen != "en":
        # Qt's own words (the file dialog, Yes/No buttons) from Qt's catalogs,
        # where the installation has them.
        qt = QTranslator()
        if qt.load(f"qtbase_{chosen}", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
            QCoreApplication.installTranslator(qt)
            _installed.append(qt)
    # Numbers follow the locale that was asked for (fmt reads QLocale()):
    # the tag itself, not the catalog it landed on, so de_CH keeps its
    # decimal point while speaking the de catalog.
    asked = locale or os.environ.get("SHACK_LOCALE", "").strip()
    QLocale.setDefault(QLocale(asked) if asked else QLocale.system())
    return chosen
