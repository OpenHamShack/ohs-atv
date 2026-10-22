"""The look: colour tokens, the spacing scale, fonts and the animation timing.

Everything that paints asks this module for colours by *meaning* (rx, tx,
lockOk, segment kind...) rather than by value, so the light theme is one more
dict and not a hunt through every paintEvent. The stock widgets that remain
(line edits, combo boxes, tables, scroll bars) are styled from the same tokens
by `stylesheet()`.

Fonts: the machine may be offline and must not be sent fetching anything, so
the families are chosen from what Debian and Raspberry Pi OS install, best
first, and the first one present wins. Figures that change while the
operator watches (the frequency, meter values) are drawn digit-by-digit in
fixed cells by the widgets themselves, so a sans with proportional figures
still never makes a number jitter sideways.
"""
from __future__ import annotations

from PyQt6.QtCore import QEasingCurve
from PyQt6.QtGui import QColor, QFont, QFontDatabase

# Spacing scale (px). Every gap and padding in the UI is one of these; that is
# most of what makes a dense screen look calm rather than busy.
S1, S2, S3, S4, S5, S6 = 4, 8, 12, 16, 24, 32
RADIUS = 8          # cards
RADIUS_SM = 6       # controls, chips

# One animation language: state changes ease out over ~180 ms; nothing loops
# except things that are genuinely in progress (a lock searching, TX on air).
ANIM_MS = 180
EASE = QEasingCurve.Type.OutCubic

DARK = {
    "name": "dark",
    "bg0": "#0a0d12",        # window
    "bg1": "#10141b",        # cards
    "bg2": "#171c25",        # inputs, raised
    "bg3": "#1f2531",        # hover
    "scope": "#06080c",      # behind the spectrum
    "line": "#232a36",       # hairlines
    "line2": "#2f3846",      # stronger hairlines, grid majors
    "grid": "#141a23",       # scope grid
    "text": "#e8edf4",
    "text2": "#a3adbb",
    "text3": "#667284",
    "accent": "#4fd1e8",     # the one accent: focus, selection, RX
    "accentText": "#041016",
    "rx": "#4fd1e8",
    "tx": "#ff4d5e",
    "txGlow": "#ff2e45",
    "ok": "#3ddc84",
    "warn": "#ffb547",
    "err": "#ff5d6c",
    "beacon": "#c39bff",
    "own": "#ffd166",        # our own signal: the occupied band of what we send
    "sound": "#c39bff",      # sound carrier / subcarrier markers
    "chroma": "#ff8fa3",     # colour subcarrier marker
    "phosphor": "#5cf08a",   # waveform monitor trace
    "segAtv": "#38c98d",     # ATV / DATV: a carrier may go here
    "segAll": "#8592a8",     # all modes: a carrier may go here too
    "segRx": "#5b8def",      # repeater outputs: receive only
    "segNarrow": "#d9a84e",  # narrowband, beacons: protected
    "segSat": "#a77bf3",     # satellites: protected
    "segInfo": "#4a5260",
    "trace": "#cfe9ff",
    "peak": "#7f93ad",
    "shadow": "#000000",
}

LIGHT = {
    "name": "light",
    "bg0": "#eef1f5",
    "bg1": "#ffffff",
    "bg2": "#f3f5f8",
    "bg3": "#e6eaf0",
    "scope": "#0b0f15",      # scopes stay dark: a waterfall on white is unreadable
    "line": "#dde2e9",
    "line2": "#c7ced8",
    "grid": "#161d27",
    "text": "#0f1722",
    "text2": "#4a5566",
    "text3": "#8592a3",
    "accent": "#0a8fb0",
    "accentText": "#ffffff",
    "rx": "#0a8fb0",
    "tx": "#e5283c",
    "txGlow": "#ff2e45",
    "ok": "#12a35a",
    "warn": "#d98a00",
    "err": "#e0303f",
    "beacon": "#8a5cf6",
    "own": "#d99a00",
    "sound": "#8a5cf6",
    "chroma": "#e0446a",
    "phosphor": "#5cf08a",
    "segAtv": "#20a872",
    "segAll": "#6b7891",
    "segRx": "#3f74e0",
    "segNarrow": "#c08a28",
    "segSat": "#8a5cf6",
    "segInfo": "#9aa3b0",
    "trace": "#cfe9ff",
    "peak": "#7f93ad",
    "shadow": "#1a2230",
}

# Colours drawn *inside* the scopes always come from the dark set, because the
# scope background is dark in both themes.
SCOPE_KEYS = ("rx", "tx", "ok", "warn", "err", "beacon", "own", "sound", "chroma", "phosphor", "trace", "peak",
              "segAtv", "segAll", "segRx", "segNarrow", "segSat", "segInfo", "text", "text2", "text3", "line",
              "line2")

_current = DARK


def set_theme(name: str) -> dict:
    global _current
    _current = LIGHT if name == "light" else DARK
    return _current


def t() -> dict:
    return _current


def c(key: str, alpha: float | None = None) -> QColor:
    col = QColor(_current[key])
    if alpha is not None:
        col.setAlphaF(max(0.0, min(1.0, alpha)))
    return col


def sc(key: str, alpha: float | None = None) -> QColor:
    """A colour for use on a scope (always dark-backed)."""
    col = QColor(DARK[key]) if key in SCOPE_KEYS else QColor(_current[key])
    if alpha is not None:
        col.setAlphaF(max(0.0, min(1.0, alpha)))
    return col


SEGMENT_KEY = {"atv": "segAtv", "all": "segAll", "rx": "segRx", "narrow": "segNarrow", "beacon": "segNarrow",
               "sat": "segSat", "info": "segInfo"}


def segment_colour_key(kind: str) -> str:
    """Bandplan segment kind (ohsatv.bands.Segment.kind) -> colour token. The
    colours say what the guard does there: green and slate a carrier may go,
    blue receive only, amber and violet protected neighbours."""
    return SEGMENT_KEY.get(kind, "segInfo")


# ---------------------------------------------------------------- fonts
SANS = ["Inter", "Cantarell", "Noto Sans", "Lato", "Piboto", "Ubuntu", "Roboto", "DejaVu Sans", "Liberation Sans"]
MONO = ["JetBrains Mono", "Fira Mono", "Noto Sans Mono", "Ubuntu Mono", "DejaVu Sans Mono", "Liberation Mono",
        "Monospace"]
_families: dict[str, str] = {}


def _pick(cands: list[str], key: str) -> str:
    if key not in _families:
        have = set(QFontDatabase.families())
        _families[key] = next((f for f in cands if f in have), cands[-1])
    return _families[key]


def sans_family() -> str:
    return _pick(SANS, "sans")


def mono_family() -> str:
    return _pick(MONO, "mono")


def font(px: float, weight: QFont.Weight = QFont.Weight.Normal, mono: bool = False, spacing: float = 0.0) -> QFont:
    f = QFont(mono_family() if mono else sans_family())
    f.setPixelSize(max(6, int(round(px))))
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def caps(px: float = 10.5) -> QFont:
    """Small spaced capitals for section labels."""
    f = font(px, QFont.Weight.DemiBold, spacing=1.1)
    f.setCapitalization(QFont.Capitalization.AllUppercase)
    return f


# ---------------------------------------------------------------- stock widgets
def app_font():
    """The application font. Set with QApplication.setFont, not in the style
    sheet: a font in a style sheet overrides every widget's own setFont."""
    return font(13)


def stylesheet(scale: float = 1.0) -> str:
    k = _current
    fs = int(round(13 * scale))
    return f"""
    QWidget {{ color: {k['text']}; }}
    QMainWindow, QDialog, #Root {{ background: {k['bg0']}; }}
    QToolTip {{ background: {k['bg2']}; color: {k['text']}; border: 1px solid {k['line2']};
               padding: 6px 8px; border-radius: 6px; }}
    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {k['bg2']}; border: 1px solid {k['line']}; border-radius: {RADIUS_SM}px;
        padding: 5px 8px; selection-background-color: {k['accent']}; selection-color: {k['accentText']}; }}
    QPlainTextEdit, QTextEdit {{ padding: 6px; }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QComboBox:focus {{ border: 1px solid {k['accent']}; }}
    QLineEdit:disabled, QComboBox:disabled {{ color: {k['text3']}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{ background: {k['bg2']}; border: 1px solid {k['line2']};
        selection-background-color: {k['bg3']}; selection-color: {k['text']}; outline: none; padding: 4px; }}
    QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        width: 0; border: none; }}
    QTableView, QTableWidget, QListWidget, QListView, QTreeView {{ background: {k['bg1']}; border: none;
        alternate-background-color: {k['bg2']}; gridline-color: transparent; outline: none;
        selection-background-color: {k['bg3']}; selection-color: {k['text']}; }}
    QTableView::item, QTableWidget::item {{ padding: 0 6px; }}
    QPushButton, QToolButton {{ background: {k['bg2']}; border: 1px solid {k['line2']};
        border-radius: {RADIUS_SM}px; padding: 5px 14px; }}
    QToolButton {{ padding: 3px; }}
    QPushButton:hover, QToolButton:hover {{ border-color: {k['text3']}; }}
    QPushButton:default {{ border-color: {k['accent']}; }}
    QPushButton:disabled {{ color: {k['text3']}; }}
    QHeaderView::section {{ background: {k['bg1']}; color: {k['text3']}; border: none;
        border-bottom: 1px solid {k['line']}; padding: 6px; font-size: {int(fs * 0.85)}px;
        font-weight: 600; text-transform: uppercase; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {k['line2']}; border-radius: 3px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {k['text3']}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {k['line2']}; border-radius: 3px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QSplitter::handle {{ background: {k['bg0']}; }}
    QSplitter::handle:hover {{ background: {k['line']}; }}
    QMenuBar {{ background: {k['bg0']}; color: {k['text2']}; }}
    QMenuBar::item {{ padding: 4px 10px; background: transparent; border-radius: 4px; }}
    QMenuBar::item:selected {{ background: {k['bg2']}; color: {k['text']}; }}
    QMenu {{ background: {k['bg1']}; border: 1px solid {k['line2']}; padding: 6px; border-radius: 8px; }}
    QMenu::item {{ padding: 6px 28px 6px 14px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {k['bg3']}; }}
    QMenu::separator {{ height: 1px; background: {k['line']}; margin: 5px 8px; }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {k['line2']};
        background: {k['bg2']}; }}
    QCheckBox::indicator:checked {{ background: {k['accent']}; border-color: {k['accent']}; }}
    QCheckBox::indicator:focus {{ border-color: {k['accent']}; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QStackedWidget {{ background: transparent; }}
    QLabel#Muted {{ color: {k['text2']}; }}
    QLabel#Faint {{ color: {k['text3']}; }}
    """
