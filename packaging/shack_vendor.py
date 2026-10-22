"""Unpack the pinned shared OpenHamShack libraries into a frozen build's sources.

    python3 packaging/shack_vendor.py <dest>

Every frozen build (the AppImage, the macOS .app, the Windows .exe) carries
shack-bandplan, shack-client and shack-pad as plain packages beside ohsatv,
taken from the wheels in tools/vendor after checking each one against its
SHA-256 in tools/vendor/requirements.lock - a wheel that does not match stops
the build rather than shipping. shack-i18n is the string check's wheel
(tools/i18n.py) and never runs in the program, so it stays out.

Unpacked without their .dist-info on purpose: they are this family's own
GPL-3.0 code, under the same LICENSE as ohsatv, and the Windows licence check
(packaging/windows/bundle_licences.py) counts every installed distribution
as a third party whose licence text must ship - these wheels carry none,
and THIRD-PARTY.txt is the wrong place to list our own code. As plain
packages they are found the way ohsatv is, and ohsatv.shacklibs's
import-first lookup finds them before it ever looks for a wheel.

Standard library only, so the Windows and macOS builders can run it with the
Python they fetch. (OpenHamShack NB has a twin, its own packaging/shack_vendor.py;
no code is shared between the family's repos at build time.)
"""
from __future__ import annotations

import hashlib
import re
import sys
import zipfile
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[1] / "tools" / "vendor"
# Build-time only: the string check's wheel.
NOT_BUNDLED = {"shack-i18n"}
# shack-pad's udev rule and keypad helper are for a system that installs
# them (the platform image); an app bundle never installs udev rules, and
# the .deb must carry none (libiio's own rule covers the Pluto; a second
# rule is a conflict waiting to happen). ATV opens no keypad.
LEFT_OUT = ("shack_pad/udev/",)
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s]+)\s+--hash=sha256:([0-9a-f]{64})")


def pins(lock: Path) -> list[tuple[str, str, str]]:
    """(name, version, sha256) for each pinned wheel in the lock file."""
    out = []
    for line in lock.read_text().splitlines():
        m = PIN.match(line.strip())
        if m:
            out.append((m.group(1), m.group(2), m.group(3)))
    return out


def unpack(dest: Path, vendor: Path = VENDOR) -> list[str]:
    """Check and unpack every bundled wheel into dest; the package names."""
    dest.mkdir(parents=True, exist_ok=True)
    names = []
    for name, version, sha in pins(vendor / "requirements.lock"):
        if name in NOT_BUNDLED:
            continue
        wheel = vendor / f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
        if not wheel.is_file():
            raise SystemExit(f"shack_vendor: {wheel.name} is pinned but not in {vendor} (tools/vendor.sh)")
        got = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if got != sha:
            raise SystemExit(f"shack_vendor: {wheel.name} does not match its pin ({got} != {sha})")
        with zipfile.ZipFile(wheel) as z:
            members = [m for m in z.namelist()
                       if not m.split("/")[0].endswith(".dist-info") and not m.startswith(LEFT_OUT)]
            z.extractall(dest, members)
        names.append(name.replace("-", "_"))
    return names


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: shack_vendor.py <dest>")
    print(" ".join(unpack(Path(sys.argv[1]))))
