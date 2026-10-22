"""gettext for sentences no widget says: the `--server` API's JSON errors and
status words today, and the engine's refusals and toasts as M3 converts them
(OpenHamShack I18N.md section 2: Qt's tr() for widgets, gettext for the
rest, one domain per package).

Why not QCoreApplication.translate everywhere: the engine runs without Qt in
`--headless` and `--server`, and in a store container with no PyQt at all;
an engine import must not need it.

ATV's one domain is `ohsatv`, its English template beside the Qt catalog
(`ohsatv/i18n/ohsatv.pot`, written by `tools/i18n.py pot`) and compiled
translations under `ohsatv/i18n/locale/<lang>/LC_MESSAGES/ohsatv.mo`. A module
takes the functions as they are named here:

    from ohsatv.i18n.text import _, ngettext

shack-i18n-check recognises calls spelt `_`, `ngettext` and `pgettext` and
checks their msgids against the template, so keep those names. The same
shape as QO-100 NB's ohscore/i18n/text.py (copied, not shared: no code is
shared at run time between the family's repos).

The language: SHACK_LOCALE if set (the override the window takes too), else
the process locale (LANGUAGE, LC_ALL, LC_MESSAGES, LANG, as gettext reads
them). English, the source language, needs no catalog.
"""
from __future__ import annotations

import gettext
import os
from pathlib import Path

from . import candidates

DIR = Path(__file__).resolve().parent
NAME = "ohsatv"
_translation = None


def languages() -> list[str] | None:
    """gettext's language list: SHACK_LOCALE alone when set, else None for
    gettext's own environment lookup."""
    tag = os.environ.get("SHACK_LOCALE", "").strip()
    if not tag:
        return None
    return candidates(tag) or None


def _tr():
    global _translation
    if _translation is None:
        _translation = gettext.translation(NAME, str(DIR / "locale"), languages=languages(), fallback=True)
    return _translation


def _(msg: str) -> str:
    return _tr().gettext(msg)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _tr().ngettext(singular, plural, n)


def pgettext(context: str, msg: str) -> str:
    return _tr().pgettext(context, msg)
