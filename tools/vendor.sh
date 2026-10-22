#!/bin/sh
# Re-pin the shared OpenHamShack libraries (ohs-libs) this app vendors in
# tools/vendor (REPO-LAYOUT 4: exact pins, one commit per bump).
#
#     tools/vendor.sh [~/ohs-libs]
#
# Builds every wheel with ohs-libs' own tools/build.sh from that checkout as
# it stands (commit what you mean to pin there first: the commit is written
# into the lock), copies the ones this app uses over tools/vendor, and
# rewrites requirements.lock with their SHA-256s, the commit and the
# band-plan dataset's version. The store image installs from the lock with
# its hashes checked (store/Containerfile); a checkout and the desktop
# builds unpack the same wheels (ohsatv/shacklibs.py).
#
# The wheels' versions do not move with every change in ohs-libs (they are
# all 0.1.0 during the beta), so the hash is the pin: never copy a wheel in
# by hand without rewriting the lock, or the store build refuses it.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
LIBS=$(cd "${1:-$HOME/ohs-libs}" && pwd)
# What this app imports: the band-plan dataset and licence check, the shared
# profile/controller client, the pad and the string check (tools/i18n.py).
# Not shack_desktop: that is the webview shell for the web-UI apps, and ATV
# is a Qt app (BETA-PLAN architecture change 3).
WHEELS="shack_bandplan shack_client shack_pad shack_i18n"

if [ -n "$(git -C "$LIBS" status --porcelain -- python data)" ]; then
    echo "ohs-libs has uncommitted changes under python/ or data/: commit them first" >&2
    exit 1
fi
COMMIT=$(git -C "$LIBS" rev-parse --short HEAD)
DATA=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["dataVersion"])' "$LIBS/data/bandplan/manifest.json")

OUT=$(mktemp -d "${TMPDIR:-/tmp}/ohs-wheels.XXXXXX")
trap 'rm -rf "$OUT"' EXIT
"$LIBS/tools/build.sh" "$OUT" >/dev/null 2>&1

VENDOR="$REPO/tools/vendor"
LOCK="$VENDOR/requirements.lock"
{
    echo "# The shared OpenHamShack libraries this app pins (REPO-LAYOUT 4): built by"
    echo "# ~/ohs-libs tools/build.sh at ohs-libs $COMMIT (dataset $DATA) and written"
    echo "# here by tools/vendor.sh. store/Containerfile installs the first three into the"
    echo "# image with their hashes checked; shack-i18n is the string check's wheel"
    echo "# (tools/i18n.py)."
} > "$LOCK.new"
for w in $WHEELS; do
    f=$(cd "$OUT" && ls "$w"-*.whl)
    rm -f "$VENDOR/$w"-*.whl
    cp "$OUT/$f" "$VENDOR/$f"
    version=$(echo "$f" | cut -d- -f2)
    hash=$(sha256sum "$VENDOR/$f" | cut -d' ' -f1)
    echo "$(echo "$w" | tr _ -)==$version --hash=sha256:$hash" >> "$LOCK.new"
done
mv "$LOCK.new" "$LOCK"
echo "pinned ohs-libs $COMMIT, dataset $DATA:"
cat "$LOCK" | grep -v '^#'
