#!/bin/bash
# Build "OpenHamShack ATV.app" and its .dmg (Apple Silicon), signed with the
# Developer ID and submitted for notarisation.
#
# From Linux (ohs-dev), one command does the whole round trip:
#
#   tools/build-macos.sh [--host mac] [--remote DIR] [--out DIR] [--sign] [--no-notarize] [--wait]
#
# sends `git archive HEAD` and the brand kit's atv icon PNGs to the Mac
# mini (ssh alias --host, default mac; everything under --remote, default
# /Volumes/OpenHamShackBuild/m3-atv - the internal disk is small), runs this same
# script there, and copies the .dmg and its .sha256 back to --out (default
# ~/build/m3-atv). On the Mac itself:
#
#   tools/build-macos.sh --icons DIR [--work DIR] [--sign] [--no-notarize] [--wait]
#
# The steps on the Mac, each a gate:
#  - Python 3.13.16 from python-build-standalone (the kit's and NB's pin,
#    SHA-256 checked), a venv with packaging/windows/requirements.txt's pins
#    of what the program imports (wheels only) plus cmake and setuptools.
#  - PortAudio v19.7.0 built static and linked into PyAudio 0.2.14 built
#    from its sdist, as NB's build-macos.sh does: PyPI has no macOS wheel of
#    PyAudio, and a dylib beside it would be one more thing to sign and find.
#  - The shared libraries, hash-checked and unpacked (packaging/shack_vendor.py).
#  - The .icns from the brand kit's PNGs (iconutil), the .app from
#    packaging/macos/openhamshack-atv.spec, which says what a Mac build does and
#    does not carry yet (no libiio: Pluto support on macOS needs ohsatv.host
#    work first).
#  - Signed: with --sign, the Developer ID in the Mac mini's ohs-signing
#    keychain, hardened runtime, secure timestamp, every Mach-O file inside
#    out, then the bundle with packaging/macos/entitlements.plist (the
#    keychain password goes from its file to `security` and nowhere else);
#    without, ad hoc, which an arm64 Mac needs to run anything at all.
#    The entitlements are only the microphone (a sound source for the
#    transmitted picture's audio: under the hardened runtime a process
#    without it records silence, with no prompt; Info.plist's
#    NSMicrophoneUsageDescription is the prompt's text). No camera: ATV
#    lists webcams on Linux and Windows only (ohsatv/sources.py). No
#    code-signing exceptions: everything in the bundle carries the same
#    Developer ID. The plist itself carries no comment: codesign's
#    entitlements parser (AMFI) rejects any, as NB's first signed build
#    found.
#  - The signed program run headless on the simulator, as build-windows.ps1
#    runs the frozen .exe: a bundle missing a module fails here.
#  - The .dmg (app + Applications link), signed; with --sign it is submitted
#    to Apple with notarytool (profile ohs-notary). Without --wait the
#    submission id is printed and the build ends there: Apple has taken over
#    half an hour for a new bundle id, and an unstapled image is still
#    accepted by Gatekeeper online once Apple accepts it. Staple later with
#    `xcrun stapler staple <dmg>` (then rewrite the .sha256). --wait waits,
#    staples and asks Gatekeeper, as ohs-libs' build-dmg.sh does.
#
# Not ohs-libs packaging/macos/build-dmg.sh itself: that is for web-UI
# apps (the kit's spec leaves Qt out, and its smoke test is the loopback
# server's launch token, which ATV has none of). The pins, the keychain and
# profile names are the same, so one keychain serves both.
set -euo pipefail

HOST=mac REMOTE=/Volumes/OpenHamShackBuild/m3-atv OUT="$HOME/build/m3-atv"
ICONS="" WORK="" SIGN=0 NOTARIZE=1 WAIT=0
while (($#)); do
    case $1 in
        --host) HOST=$2; shift ;;
        --remote) REMOTE=$2; shift ;;
        --out) OUT=$2; shift ;;
        --icons) ICONS=$2; shift ;;
        --work) WORK=$2; shift ;;
        --sign) SIGN=1 ;;
        --no-notarize) NOTARIZE=0 ;;
        --wait) WAIT=1 ;;
        *) echo "usage: $0 [--host H] [--remote DIR] [--out DIR] [--icons DIR] [--work DIR] [--sign] [--no-notarize] [--wait]" >&2; exit 2 ;;
    esac
    shift
done
REPO=$(cd "$(dirname "$0")/.." && pwd)
step() { printf '\n== %s\n' "$*"; }
die() { echo "build-macos.sh: $*" >&2; exit 1; }
flags=()
((SIGN)) && flags+=(--sign)
((NOTARIZE)) || flags+=(--no-notarize)
((WAIT)) && flags+=(--wait)

# ---------------------------------------------------------------- from Linux
if [[ $(uname -s) != Darwin ]]; then
    brand=${SHACK_BRAND:-$HOME/openhamshack/brand}/icons/png/atv
    [[ -d $brand ]] || die "no brand-kit icons at $brand (set SHACK_BRAND)"
    rev=$(git -C "$REPO" rev-parse --short HEAD)
    step "Sending $rev and the icons to $HOST:$REMOTE"
    ssh "$HOST" "rm -rf '$REMOTE/src' '$REMOTE/icons' && mkdir -p '$REMOTE/src' '$REMOTE/icons' '$REMOTE/work'"
    git -C "$REPO" archive HEAD | ssh "$HOST" "tar -x -C '$REMOTE/src'"
    # Named as the kit's stage names them (shackpkg.py): <size>.png, the
    # 1024 being the brand kit's ios-1024.png.
    icons=$(mktemp -d "${TMPDIR:-$HOME/build}/atv-icons.XXXXXX")
    for s in 16 32 64 128 256 512; do cp "$brand/atv-$s.png" "$icons/$s.png"; done
    cp "$brand/ios-1024.png" "$icons/1024.png"
    tar -C "$icons" -c . | ssh "$HOST" "tar -x -C '$REMOTE/icons'"
    rm -rf "$icons"
    # bash -l: the Command Line Tools and the user's PATH, as a terminal has.
    ssh "$HOST" "bash -l '$REMOTE/src/tools/build-macos.sh' --icons '$REMOTE/icons' --work '$REMOTE/work' ${flags[*]-}"
    mkdir -p "$OUT"
    dmg=$(ssh "$HOST" "cat '$REMOTE/work/out/LATEST'")
    scp -q "$HOST:$REMOTE/work/out/$dmg" "$HOST:$REMOTE/work/out/$dmg.sha256" "$OUT/"
    (cd "$OUT" && sha256sum -c "$dmg.sha256")
    ssh "$HOST" "rm -rf '$REMOTE/src' '$REMOTE/icons'"
    echo "copied $OUT/$dmg"
    exit 0
fi

# ---------------------------------------------------------------- on the Mac
[[ $(uname -m) == arm64 ]] || die "runs on an Apple Silicon Mac"
[[ -n $ICONS && -f $ICONS/16.png ]] || die "--icons DIR (the brand kit's atv PNGs: 16.png ... 1024.png) is required"
WORK=${WORK:-$HOME/shack-atv-mac}
for t in clang codesign hdiutil iconutil otool shasum xcrun file plutil; do
    command -v $t >/dev/null || die "$t is missing: install Apple's Command Line Tools"
done
IDENTITY="Developer ID Application: Brian Henderson (GTVYVL6ZY7)"
KEYCHAIN="$HOME/Library/Keychains/ohs-signing.keychain-db"
PASSFILE="$HOME/.config/ohs-signing/keychain-pass"
PROFILE="ohs-notary"
if ((SIGN)); then
    [[ -f $KEYCHAIN ]] || die "--sign: no signing keychain at $KEYCHAIN (this is the Mac mini's)"
    [[ -r $PASSFILE ]] || die "--sign: no keychain password file at $PASSFILE"
fi
unlock() { security unlock-keychain -p "$(cat "$PASSFILE")" "$KEYCHAIN"; }

CACHE=$WORK/cache BUILD=$WORK/build VENDOR=$WORK/vendor OUTDIR=$WORK/out
mkdir -p "$CACHE" "$BUILD" "$VENDOR" "$OUTDIR"
VERSION=$(tr -d ' \t\r\n' < "$REPO/ohsatv/VERSION")
export MACOSX_DEPLOYMENT_TARGET=12.0

pin() {   # NAME -> "file sha256 url" (bash 3.2 on a Mac: no associative arrays)
    case $1 in
        python) echo "cpython-3.13.16+20261003-aarch64-apple-darwin-install_only.tar.gz d8975d7df4f08f7b1c7aafcdfacbddcec3d366415f2c1a72b2466b6850815933 https://github.com/astral-sh/python-build-standalone/releases/download/20261003/cpython-3.13.16%2B20261003-aarch64-apple-darwin-install_only.tar.gz" ;;
        portaudio) echo "pa_stable_v190700_20210406.tgz 47efbf42c77c19a05d22e627d42873e991ec0c1357219c0d74ce6a2948cb2def http://files.portaudio.com/archives/pa_stable_v190700_20210406.tgz" ;;
        pyaudio) echo "PyAudio-0.2.14.tar.gz 78dfff3879b4994d1f4fc6485646a57755c6ee3c19647a491f790a0895bd2f87 https://files.pythonhosted.org/packages/26/1d/8878c7752febb0f6716a7e1a52cb92ac98871c5aa522cba181878091607c/PyAudio-0.2.14.tar.gz" ;;
        *) die "no pin named $1" ;;
    esac
}
fetch() {   # NAME -> prints the cached, checked path
    local file sha url dest
    read -r file sha url <<<"$(pin "$1")"
    dest=$CACHE/$file
    if [[ -f $dest && $(shasum -a 256 "$dest" | cut -d' ' -f1) == "$sha" ]]; then echo "$dest"; return; fi
    echo "   fetching $url" >&2
    curl -fsSL -A "Wget/1.21" -o "$dest.part" "$url"
    [[ $(shasum -a 256 "$dest.part" | cut -d' ' -f1) == "$sha" ]] || { rm -f "$dest.part"; die "$file does not match its pinned SHA-256"; }
    mv "$dest.part" "$dest"; echo "$dest"
}
unpack() { rm -rf "$2"; mkdir -p "$2"; tar -xzf "$1" -C "$2" --strip-components 1; }

step "Python 3.13.16 and the pinned wheels"
PYHOME=$WORK/python
[[ -x $PYHOME/bin/python3.13 ]] || unpack "$(fetch python)" "$PYHOME"
VPY=$WORK/venv/bin/python
[[ -x $VPY ]] || "$PYHOME/bin/python3.13" -m venv "$WORK/venv"
req=$BUILD/requirements.txt
grep -E '^(numpy|PyQt6|PyQt6-Qt6|PyQt6-sip|Pillow|pyinstaller|pyinstaller-hooks-contrib)==' \
    "$REPO/packaging/windows/requirements.txt" > "$req"
# cmake builds PortAudio; setuptools builds PyAudio (no build isolation, so
# the build uses this venv's pinned tools). NB's macOS pins.
printf '%s\n' cmake==4.1.2 setuptools==80.9.0 >> "$req"
"$VPY" -m pip install -q --disable-pip-version-check --cache-dir "$CACHE/pip" --only-binary :all: -r "$req"
CMAKE=$WORK/venv/bin/cmake

step "PortAudio v19.7.0 (static) and PyAudio 0.2.14"
if ! "$VPY" -c 'import pyaudio, sys; sys.exit(pyaudio.get_portaudio_version_text().find("V19.7.0") < 0)' 2>/dev/null; then
    unpack "$(fetch portaudio)" "$BUILD/portaudio"
    "$CMAKE" -S "$BUILD/portaudio" -B "$BUILD/portaudio/b" -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES=arm64 \
        -DCMAKE_OSX_DEPLOYMENT_TARGET=$MACOSX_DEPLOYMENT_TARGET -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
        -DCMAKE_POSITION_INDEPENDENT_CODE=ON >/dev/null
    "$CMAKE" --build "$BUILD/portaudio/b" -j 8 --target portaudio_static >/dev/null
    mkdir -p "$VENDOR/portaudio/static" "$VENDOR/portaudio/include"
    cp "$BUILD/portaudio/b/libportaudio.a" "$VENDOR/portaudio/static/"
    cp "$BUILD/portaudio/include/"*.h "$VENDOR/portaudio/include/"
    unpack "$(fetch pyaudio)" "$BUILD/pyaudio"
    # A folder holding only libportaudio.a, so -lportaudio cannot find a
    # dylib; and the frameworks PortAudio's Core Audio host uses.
    CFLAGS="-I$VENDOR/portaudio/include" \
    LDFLAGS="-L$VENDOR/portaudio/static -framework CoreAudio -framework AudioToolbox -framework AudioUnit -framework CoreFoundation -framework CoreServices" \
        "$VPY" -m pip install -q --disable-pip-version-check --no-build-isolation --no-cache-dir "$BUILD/pyaudio"
fi
"$VPY" -c 'import pyaudio; print("   PyAudio", pyaudio.__version__, "with", pyaudio.get_portaudio_version_text())'
if otool -L "$("$VPY" -c 'import pyaudio._portaudio as m; print(m.__file__)')" | tail -n +2 | grep -q libportaudio; then
    die "PyAudio links a PortAudio dylib; it must have it linked in"
fi
LIC=$VENDOR/licences
rm -rf "$LIC"; mkdir -p "$LIC"
tar -xzf "$(fetch portaudio)" -O portaudio/LICENSE.txt > "$LIC/portaudio-LICENSE.txt"

step "Shared libraries (tools/vendor, hash-checked)"
rm -rf "$VENDOR/shack"
echo "   $("$VPY" "$REPO/packaging/shack_vendor.py" "$VENDOR/shack")"

step "Icon and app bundle"
iconset=$BUILD/app.iconset
rm -rf "$iconset" && mkdir "$iconset"
for s in 16 32 128 256 512; do
    cp "$ICONS/$s.png" "$iconset/icon_${s}x${s}.png"
    cp "$ICONS/$((s * 2)).png" "$iconset/icon_${s}x${s}@2x.png"
done
iconutil -c icns -o "$BUILD/shack-atv.icns" "$iconset"
OHSATV_SHACK=$VENDOR/shack OHSATV_ICNS=$BUILD/shack-atv.icns OHSATV_VERSION=$VERSION OHSATV_LICENCES=$LIC \
    "$WORK/venv/bin/pyinstaller" --noconfirm --clean --log-level WARN \
    --distpath "$BUILD/dist" --workpath "$BUILD/pyi" "$REPO/packaging/macos/openhamshack-atv.spec"
APP="$BUILD/dist/OpenHamShack ATV.app"
EXE="$APP/Contents/MacOS/shack-atv"
[[ -x $EXE ]] || die "PyInstaller made no $EXE"

if ((SIGN)); then
    step "Sign ($IDENTITY)"
    unlock
    sign() { codesign --force --options runtime --timestamp --keychain "$KEYCHAIN" -s "$IDENTITY" "$@" 2>&1 \
                 | grep -v "replacing existing signature" || true; }
else
    step "Sign ad hoc (no --sign: for this Mac only)"
    sign() { codesign --force -s - "$@" 2>&1 | grep -v "replacing existing signature" || true; }
fi
# Inside out: every Mach-O file but the main executable, deepest first.
# `file` lists a universal binary once per slice, as "<path> (for
# architecture x86_64)"; that suffix is cut and the list made unique, or
# codesign is handed a path that does not exist (NB's first signed build
# failed exactly so, 09/10/2026).
find "$APP/Contents" -type f ! -path "$EXE" -print0 | xargs -0 file -h \
    | awk -F': ' '/Mach-O/ {sub(/ \(for architecture [^)]*\)$/, "", $1); print $1}' | sort -u \
    | awk -F/ '{print NF "\t" $0}' | sort -rn | cut -f2- | while IFS= read -r f; do sign "$f"; done
if ((SIGN)); then sign --entitlements "$REPO/packaging/macos/entitlements.plist" "$APP"; else sign "$APP"; fi
codesign --verify --deep --strict "$APP" || die "the signed bundle does not verify"
codesign -dv "$APP" 2>&1 | grep -E '^(Identifier|Authority=Developer|TeamIdentifier|Runtime)' | sed 's/^/   /' || true

step "Smoke test: the signed program, headless, on the simulator"
# Its own config and data, so nothing of an operator's is read or written.
smoke=$BUILD/smoke
rm -rf "$smoke" && mkdir -p "$smoke"
said=$(OHSATV_CONF=$smoke/config.json OHSATV_DATA=$smoke/data "$EXE" --headless --device sim --no-audio --seconds 5 2>&1) \
    || { echo "$said"; die "the frozen program failed"; }
echo "$said" | sed 's/^/   /' | tail -4
[[ $(grep -c " MHz @" <<<"$said") -ge 3 ]] && grep -q "^stopped" <<<"$said" \
    || die "the frozen program did not run the simulator for 5 s"
OHSATV_CONF=$smoke/config.json OHSATV_DATA=$smoke/data "$EXE" --teletext --help >/dev/null 2>&1 \
    || die "the frozen program's Teletext editor does not start"

step "Disk image"
dmg_name="OpenHamShack-ATV-$VERSION-macos-arm64.dmg"
dmg=$OUTDIR/$dmg_name
root=$BUILD/dmgroot
rm -rf "$root" "$dmg" && mkdir "$root"
ditto "$APP" "$root/OpenHamShack ATV.app"
ln -s /Applications "$root/Applications"
hdiutil create -quiet -ov -fs HFS+ -format UDZO -volname "OpenHamShack ATV" -srcfolder "$root" "$dmg"
sign "$dmg"
rm -rf "$root"

if ((SIGN && NOTARIZE)); then
    step "Notarise (notarytool, profile $PROFILE)"
    unlock     # other sessions on the Mac mini use, and lock, the same keychain
    if ((WAIT)); then
        xcrun notarytool submit "$dmg" --keychain-profile "$PROFILE" --keychain "$KEYCHAIN" --wait \
            --output-format plist > "$BUILD/notary.plist"
    else
        xcrun notarytool submit "$dmg" --keychain-profile "$PROFILE" --keychain "$KEYCHAIN" \
            --output-format plist > "$BUILD/notary.plist"
    fi
    sub=$(plutil -extract id raw -o - "$BUILD/notary.plist")
    echo "   submission $sub"
    echo "$sub  $dmg_name" >> "$OUTDIR/notary-submissions.txt"
    if ((WAIT)); then
        status=$(plutil -extract status raw -o - "$BUILD/notary.plist")
        [[ $status == Accepted ]] || { xcrun notarytool log "$sub" --keychain-profile "$PROFILE" --keychain "$KEYCHAIN" || true; die "notarisation: $status"; }
        xcrun stapler staple -q "$dmg"
        spctl -a -t open --context context:primary-signature -vv "$dmg" 2>&1 | sed 's/^/   /'
    else
        echo "   not waiting: check with xcrun notarytool info $sub --keychain-profile $PROFILE, then staple"
    fi
elif ((SIGN)); then
    echo; echo "NOT NOTARISED (--no-notarize)"
else
    echo; echo "UNSIGNED (ad hoc): for this Mac only, never to be shared"
fi

(cd "$OUTDIR" && shasum -a 256 "$dmg_name" > "$dmg_name.sha256")
echo "$dmg_name" > "$OUTDIR/LATEST"
echo
echo "built $dmg ($(du -h "$dmg" | cut -f1))"
