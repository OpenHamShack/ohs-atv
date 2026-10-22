"""Entry point PyInstaller freezes into openhamshack-atv.exe and
openhamshack-atv-console.exe - the same `python -m ohsatv` main(), so the Windows
build takes exactly the command-line options the .deb's launcher does.
(Copied in shape from OpenHamShack NB's and DATV's packaging/windows/launch.py.)

ohsatv.host.self_command() names the frozen .exe itself when it tells an
administrator what to run (--clear-stale-lock); that works because this
file is the whole of the entry point and passes argv through untouched."""
import sys

from ohsatv.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
