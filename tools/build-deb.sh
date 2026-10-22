#!/usr/bin/env bash
# Build openhamshack-atv_<version>_all.deb with plain dpkg-deb.
#
# No debhelper: the payload is pure Python (ohsatv/), arch-independent, and
# small, so debhelper's rules/control machinery would be pure ceremony for
# one launcher, one desktop entry, one icon, one metainfo file and one man
# page. This script does by hand exactly what debhelper would generate.
#
# Usage: tools/build-deb.sh [version]
#   version   defaults to the contents of ohsatv/VERSION (created as 0.1.0
#             if that file does not exist yet).
#
# The .deb is written to the CURRENT directory, not the repo, so running
# this from a scratch/temp dir (as the packaging test does) never leaves
# a build artefact in the tree.
set -euo pipefail

script_dir="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
pkg_src="$repo_root/packaging"
pkgname="openhamshack-atv"

version="${1:-}"
if [ -z "$version" ]; then
    version_file="$repo_root/ohsatv/VERSION"
    if [ ! -f "$version_file" ]; then
        echo "0.1.0" > "$version_file"
    fi
    version="$(tr -d ' \t\r\n' < "$version_file")"
fi
if [ -z "$version" ]; then
    echo "build-deb.sh: version is empty (check ohsatv/VERSION)" >&2
    exit 1
fi

out_deb="$(pwd)/${pkgname}_${version}_all.deb"

work="$(mktemp -d "${TMPDIR:-/tmp}/openhamshack-atv-build.XXXXXX")"
trap 'rm -rf "$work"' EXIT

root="$work/pkgroot"
lib="$root/usr/lib/openhamshack-atv"
mkdir -p \
    "$root/DEBIAN" \
    "$lib" \
    "$root/usr/bin" \
    "$root/usr/share/applications" \
    "$root/usr/share/icons/hicolor/scalable/apps" \
    "$root/usr/share/metainfo" \
    "$root/usr/share/man/man1" \
    "$root/usr/share/doc/$pkgname"

# --- payload: the ohsatv package, and ohstxt (the Teletext editor) -----------
# Plain copy, not a build step -- both are pure Python, nothing to compile.
# Caches are stripped; nothing else is touched. ohstxt is OpenHamShack
# Teletext, folded into ATV as a package of its own beside ohsatv: the
# window's Teletext editor button imports it, and `openhamshack-atv --teletext`
# runs it alone.
for pkg in ohsatv ohstxt; do
    cp -a "$repo_root/$pkg" "$lib/$pkg"
    find "$lib/$pkg" -depth -name '__pycache__' -type d -exec rm -rf {} +
    find "$lib/$pkg" -name '*.py[co]' -delete
done

# The pinned shared OpenHamShack libraries (shack-bandplan, shack-client,
# shack-pad), hash-checked against tools/vendor/requirements.lock and
# unpacked as plain packages beside ohsatv - the same as every frozen build
# carries them (packaging/shack_vendor.py says why unpacked). Without this
# ohsatv.shacklibs looks for tools/vendor beside the package, which an
# installed system does not have, and finds nothing.
python3 "$repo_root/packaging/shack_vendor.py" "$lib" >/dev/null
find "$lib" -depth -name '__pycache__' -type d -exec rm -rf {} +

# --- launcher ---------------------------------------------------------------
install -m 755 "$pkg_src/openhamshack-atv" "$root/usr/bin/openhamshack-atv"

# --- desktop integration -----------------------------------------------------
install -m 644 "$pkg_src/openhamshack-atv.desktop" "$root/usr/share/applications/openhamshack-atv.desktop"
install -m 644 "$pkg_src/openhamshack-atv.svg" "$root/usr/share/icons/hicolor/scalable/apps/openhamshack-atv.svg"
# An "Amateur Radio" submenu (packaging/openhamshack-atv.menu says why). Menu
# files are only read from the XDG config dirs, so it lives in /etc and is
# a conffile; its name is ours alone, so NB's and HAM's copies never collide.
install -d "$root/etc/xdg/menus/applications-merged" "$root/usr/share/desktop-directories"
install -m 644 "$pkg_src/openhamshack-atv.menu" "$root/etc/xdg/menus/applications-merged/openhamshack-atv.menu"
install -m 644 "$pkg_src/openhamshack-atv-hamradio.directory" \
    "$root/usr/share/desktop-directories/openhamshack-atv-hamradio.directory"
echo "/etc/xdg/menus/applications-merged/openhamshack-atv.menu" > "$root/DEBIAN/conffiles"
chmod 644 "$root/DEBIAN/conffiles"
sed "s/@VERSION@/$version/" "$pkg_src/openhamshack-atv.metainfo.xml" \
    > "$root/usr/share/metainfo/openhamshack-atv.metainfo.xml"
chmod 644 "$root/usr/share/metainfo/openhamshack-atv.metainfo.xml"

# --- man page (compressed, as Debian policy expects) -------------------------
gzip -n -9 -c "$pkg_src/openhamshack-atv.1" > "$root/usr/share/man/man1/openhamshack-atv.1.gz"
chmod 644 "$root/usr/share/man/man1/openhamshack-atv.1.gz"

# --- doc/copyright ------------------------------------------------------------
install -m 644 "$pkg_src/copyright" "$root/usr/share/doc/$pkgname/copyright"

# --- maintainer scripts --------------------------------------------------------
install -m 755 "$pkg_src/postinst" "$root/DEBIAN/postinst"
install -m 755 "$pkg_src/postrm" "$root/DEBIAN/postrm"

# --- permissions, belt and braces ---------------------------------------------
# install(1) above already set the right modes; this just guarantees that
# *everything* under usr/ ends up world-readable regardless of the current
# umask, which cp -a (for ohsatv/) does not promise.
find "$root/usr" -type d -exec chmod 755 {} +
find "$root/usr" -type f -exec chmod 644 {} +
chmod 755 "$root/usr/bin/openhamshack-atv"

# --- Installed-Size (KiB), only the files actually installed -----------------
installed_size="$(du -sk "$root/usr" | cut -f1)"

# Depends is what the window needs to open at all. Recommends: libiio for
# the Pluto family (the simulator needs nothing), avahi-daemon so libiio's
# network scan works (discover() copes without it, as OpenHamShack NB's does),
# ffmpeg for the webcam and video-file sources, and libhackrf0 for a HackRF
# One as the transmitter (loaded through ctypes, so no Python binding) -
# each missing one disables one feature with a sentence, never the program.
# The hackrf tools package (hackrf_info) is not needed. python3-pil is a
# Depends because the Teletext editor's image import, PNG export and
# copy-as-picture use it, and an exception in a Qt slot ends the whole
# program - the transceiver with it.
cat > "$root/DEBIAN/control" <<EOF
Package: $pkgname
Version: $version
Section: hamradio
Priority: optional
Architecture: all
Installed-Size: $installed_size
Depends: python3 (>= 3.11), python3-numpy, python3-pyqt6, python3-pyaudio, python3-pil
Recommends: python3-libiio, avahi-daemon, ffmpeg, libhackrf0
Maintainer: Brian Henderson <noreply@openhamshack.com>
Description: analogue amateur-television transceiver
 OpenHamShack ATV generates, transmits, receives and decodes 625-line PAL
 amateur television with sound: AM-VSB and FM-ATV (IARU R1 V.2, CCIR 405
 pre-emphasis, 6 MHz sound subcarrier) on 23cm and 6cm, and analogue
 receive on 70cm, with a Pluto-family SDR (ADALM-Pluto, Pluto+, Pluto
 nano, LibreSDR) through libiio, optionally transmitting through a HackRF
 One. Test card with ident, colour bars, webcam
 or video file; in-program PAL decoder with waveform monitor and
 vectorscope. Teletext (WST Level 1) is sent with the picture and decoded
 on receive, and the Teletext editor that makes the pages is included. A
 simulated radio lets it run, and be tested, with no hardware and without
 transmitting anything.
EOF

# --- md5sums, relative to the package root, usr/ only (DEBIAN/ is metadata) --
( cd "$root" && find usr -type f -exec md5sum {} + ) > "$root/DEBIAN/md5sums"
chmod 644 "$root/DEBIAN/md5sums"

dpkg-deb --build --root-owner-group "$root" "$out_deb" >/dev/null

echo "built $out_deb"
