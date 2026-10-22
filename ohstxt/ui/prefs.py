"""Window preferences: theme, geometry, the service folder last open, view
toggles, the folders the file dialogs last chose from.

As in OpenHamShack ATV, a JSON file beside the config (`ui.json` in
~/.config/openhamshack-teletext/, or $OHSTXT_UI_CONF), written atomically, read
forgivingly: a key of the wrong type keeps its default rather than
crashing the window, and losing a preference is never worth an error.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

DEFAULTS = {
    "theme": "dark",
    "geometry": "",
    "serviceDir": "",
    "showCodes": False,
    "grid": False,
    "reveal": False,
    "blackFg": False,
    "imageDir": "",
    "exportDir": "",
    "penColour": -1,
    "liveSample": False,
    "subtitleDir": "",
}


def conf_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "openhamshack-teletext"


def data_dir() -> Path:
    env = os.environ.get("OHSTXT_DATA")
    if env:
        return Path(env)
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "openhamshack-teletext"


def path() -> Path:
    env = os.environ.get("OHSTXT_UI_CONF")
    return Path(env) if env else conf_dir() / "ui.json"


class Prefs:
    def __init__(self, p: Path | None = None):
        self.path = Path(p) if p else path()
        self.d = copy.deepcopy(DEFAULTS)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k in DEFAULTS and type(v) is type(DEFAULTS[k]):
                        self.d[k] = copy.deepcopy(v)
        except (OSError, ValueError):
            pass

    def __getitem__(self, k):
        return self.d[k]

    def get(self, k, default=None):
        return self.d.get(k, default)

    def __setitem__(self, k, v):
        self.d[k] = v

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".ui-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.d, f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass
