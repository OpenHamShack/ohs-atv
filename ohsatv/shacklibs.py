"""The shared OpenHamShack libraries (ohs-libs) where ATV needs them.

The store image installs the pinned wheels with pip, so there an import just
works. A checkout - the test suite, a developer running the window, the
.deb - has the same wheels vendored in tools/vendor (REPO-LAYOUT 4: exact
pins, one commit per bump, tools/vendor.sh), and this unpacks them into a
cache folder once and puts them on sys.path. The PyInstaller builds (Windows,
macOS, AppImage) collect the unpacked packages like any other import.

Unpacked rather than imported from the zip as tools/i18n.py does with the
check: shack-bandplan reads its dataset beside its own __file__, which a
zipped import has no folder for. The folder is named after the wheel's
SHA-256, so a new pin is a new folder and an old one is never half-reused.
Copied from the box's appliance/openhamshacklib/shacklibs.py (no code is shared
at run time between the family's repos).
"""

from __future__ import annotations

import hashlib
import importlib
import os
import sys
import tempfile
import zipfile
from pathlib import Path

SHACK_VENDOR = Path(__file__).resolve().parent.parent / "tools" / "vendor"
# The wheel each import name comes from, as tools/vendor names them.
SHACK_WHEELS = {"shack_bandplan": "shack_bandplan", "shack_client": "shack_client", "shack_pad": "shack_pad"}


def shack_wheel_cache() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "openhamshack" / "wheels"


def shack_wheel_unpack(wheel: Path) -> Path:
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()[:16]
    for root in (shack_wheel_cache(), Path(tempfile.gettempdir()) / f"openhamshack-wheels-{os.getuid()}"):
        dest = root / f"{wheel.stem}-{digest}"
        if (dest / ".complete").is_file():
            return dest
        try:
            root.mkdir(parents=True, exist_ok=True)
            tmp = Path(tempfile.mkdtemp(prefix=".unpack-", dir=root))
            with zipfile.ZipFile(wheel) as z:
                z.extractall(tmp)
            (tmp / ".complete").write_text(wheel.name)
            try:
                tmp.rename(dest)
            except OSError:
                pass        # another process got there first; theirs is complete
            if (dest / ".complete").is_file():
                return dest
        except OSError:
            continue
    raise OSError(f"could not unpack {wheel.name}")


def shack_lib(name: str, vendor: Path | None = None):
    """The module `name` (e.g. "shack_bandplan"), importing it from the
    vendored wheel when it is not installed; None when neither has it."""
    try:
        return importlib.import_module(name)
    except ImportError:
        pass
    folder = vendor or SHACK_VENDOR
    wheels = sorted(folder.glob(f"{SHACK_WHEELS.get(name, name)}-*.whl")) if folder.is_dir() else []
    if not wheels:
        return None
    try:
        path = str(shack_wheel_unpack(wheels[-1]))
    except (OSError, zipfile.BadZipFile):
        return None
    if path not in sys.path:
        sys.path.append(path)
    try:
        return importlib.import_module(name)
    except ImportError:
        return None
