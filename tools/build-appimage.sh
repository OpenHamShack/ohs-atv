#!/bin/sh
# Build OpenHamShack ATV's AppImage with the shared kit (ohs-libs
# packaging/appimage, inside Debian bookworm with podman, so it runs on any
# x86-64 Linux from Debian 12 / Ubuntu 22.04 on).
#
#   tools/build-appimage.sh [--out DIR]
#
# Environment:
#   SHACK_KIT   the kit's packaging/ folder (default ~/ohs-libs/packaging).
#               It needs the "[pyinstaller] spec" and "[linux] host_libs"
#               support (ohs-libs feature/qt-apps-kit, 63f1004 and 85acf14);
#               until that is merged, SHACK_KIT=~/wt/ohs-libs-qtkit/packaging.
#   SHACK_WORK  scratch for the staged sources (default ~/build/m3-atv-work/appimage;
#               never /tmp, a small shared tmpfs)
#
# The sources are one commit (git archive HEAD), never the working tree, so
# an AppImage names exactly what is in it: ohsatv, ohstxt (the Teletext
# editor), the icon the window draws (packaging/openhamshack-atv.svg, where
# ohsatv.ui.appicon looks beside the package), and the pinned shared
# libraries unpacked beside them (packaging/shack_vendor.py, hash-checked).
# The frozen requirements are packaging/windows/requirements.txt's pins of
# what the program imports (numpy, PyQt6, PyAudio, Pillow), plus pylibiio
# for ADI's ctypes bindings. Not Paramiko: on Linux the radio lock drives
# OpenSSH through sshpass. libiio, libhackrf and ffmpeg are the host's, as
# the .deb recommends them; each missing one disables one feature.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=$(pwd)
if [ "${1:-}" = "--out" ]; then OUT=$(mkdir -p "$2" && cd "$2" && pwd); fi
KIT=${SHACK_KIT:-$HOME/ohs-libs/packaging}
WORK=${SHACK_WORK:-$HOME/build/m3-atv-work/appimage}
grep -q host_libs_file "$KIT/tools/shackpkg.py" 2>/dev/null || {
    echo "build-appimage.sh: $KIT has no [pyinstaller] spec / [linux] host_libs support;" \
         "set SHACK_KIT to ohs-libs feature/qt-apps-kit's packaging/" >&2; exit 1; }

rm -rf "$WORK"
mkdir -p "$WORK/src"
cd "$ROOT"
VERSION=$(git show HEAD:ohsatv/VERSION | tr -d ' \t\r\n')
git archive HEAD ohsatv ohstxt packaging/openhamshack-atv.svg LICENSE | tar -x -C "$WORK/src"
mv "$WORK/src/LICENSE" "$WORK/LICENSE"
python3 "$ROOT/packaging/shack_vendor.py" "$WORK/src" >/dev/null
cp "$ROOT/packaging/appimage/qt-app.spec" "$ROOT/packaging/appimage/host-libs.txt" "$WORK/"
# PyAudio's Linux wheel is built from source against bookworm's PortAudio
# in the kit's container.
REQ=$(grep -E '^(numpy|PyQt6|PyQt6-Qt6|PyQt6-sip|PyAudio|Pillow)==' \
      "$ROOT/packaging/windows/requirements.txt" | sed 's/.*/"&"/' | paste -sd, -)
# numpy 2.5 needs Python 3.12; the kit's container is bookworm's 3.11 (for
# its glibc 2.36), so the AppImage takes 2.4.6, the newest for 3.11.
REQ=$(printf '%s' "$REQ" | sed 's/"numpy==[^"]*"/"numpy==2.4.6"/')
REQ="$REQ,\"pylibiio==0.25\""
sed -e "s|@VERSION@|$VERSION|" -e "s|@REQUIREMENTS@|$REQ|" \
    "$ROOT/packaging/appimage/app.toml.in" > "$WORK/shack-app.toml"

"$KIT/appimage/build-appimage.sh" "$WORK/shack-app.toml" --out "$OUT"
rm -rf "$WORK"
