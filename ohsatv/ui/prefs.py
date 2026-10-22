"""Preferences that belong to the window, not the radio.

The engine's config (ohsatv/state.py) holds everything that decides what the
radio does - including the licence acknowledgement, which in OpenHamShack ATV is
an engine key because the engine's transmit guard checks it. What is left -
theme, waterfall palette and levels, window geometry, the folder the File…
dialog last chose from - lives here, in `ui.json` beside the engine's
config, so the engine never learns about windows. Copied from OpenHamShack NB and cut down to what this window has.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

from .. import config

DEFAULTS = {
    "theme": "dark",
    "palette": "Ember",
    "wfAuto": True,
    "wfFloor": -110.0,
    "wfRange": 50.0,
    "geometry": "",
    "videoDir": "",
    # Teletext viewer: whether it was up, the page last asked for, and the
    # folder the pages dialog last chose from.
    "textView": False,
    "ttxPage": "",
    "ttxDirLast": "",
    "subtitles": True,
    "ttxSaveDir": "",
}


def path() -> Path:
    env = os.environ.get("OHSATV_UI_CONF")
    if env:
        return Path(env)
    return config.conf_path().with_name("ui.json")


class Prefs:
    def __init__(self, p: Path | None = None):
        self.path = Path(p) if p else path()
        self.d = copy.deepcopy(DEFAULTS)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k not in DEFAULTS:
                        continue
                    d = DEFAULTS[k]
                    if type(v) is type(d):
                        self.d[k] = copy.deepcopy(v)
                    elif isinstance(d, float) and isinstance(v, int) and not isinstance(v, bool):
                        self.d[k] = float(v)
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
            pass   # losing a window preference is not worth stopping the radio for
