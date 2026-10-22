#!/bin/sh
# Build the OpenHamShack store package for ATV: a signed .d360mod carrying
# its container image, so a controller with no route out can install it
# (D12).
#
#   tools/build-store.sh [--allow-dirty]
#
# Environment:
#   SHACK_STORE_OUT   where the package goes (default ~/build/apps/shack-atv)
#   SHACK_STAGE       scratch for the staged repository (default ~/build/shack-atv-stage;
#                     never /tmp, a small shared tmpfs)
#   DOCK360_SRC       a tree of Dock360's feature/ohs-edition (default ~/build/m3-flex/dock360)
#   DOCK360_PY        a Python with Dock360's requirements (default ~/build/venvs/dock360/bin/python)
#   SHACK_MODULES_KEY the OpenHamShack modules signing key, read in place and never copied
#                     (default ~/.config/openhamshack/keys/ohs-modules.key)
#
# How: the module's tree - store/ at the top, with ohsatv/, LICENSE and the
# pinned shared libraries beside it - is cut from HEAD with git archive (a
# package is exactly a commit; a dirty tree is refused unless
# --allow-dirty, and even then only committed files go in), committed into
# a scratch repository, and handed to ~/openhamshack-os/tools/package-app.sh,
# which builds the image from a fresh clone of it and signs the package, as
# every OpenHamShack app's is. QO-100 NB's tools/build-store.sh, for one app.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
ALLOW_DIRTY=0
case "${1:-}" in
    "") ;;
    --allow-dirty) ALLOW_DIRTY=1 ;;
    *) echo "usage: $0 [--allow-dirty]" >&2; exit 2 ;;
esac
OUT=${SHACK_STORE_OUT:-$HOME/build/apps/shack-atv}
STAGE=${SHACK_STAGE:-$HOME/build/shack-atv-stage}
DOCK360_SRC=${DOCK360_SRC:-$HOME/build/m3-flex/dock360}
export DOCK360_PY=${DOCK360_PY:-$HOME/build/venvs/dock360/bin/python}
KEY=${SHACK_MODULES_KEY:-$HOME/.config/openhamshack/keys/ohs-modules.key}
PACKAGER=${SHACK_PACKAGE_APP:-$HOME/openhamshack-os/tools/package-app.sh}

cd "$ROOT"
if [ "$ALLOW_DIRTY" = 0 ] && [ -n "$(git status --porcelain)" ]; then
    echo "The tree has uncommitted changes; commit them (or --allow-dirty to package HEAD anyway)." >&2
    exit 1
fi
[ -r "$KEY" ] || { echo "No signing key at $KEY" >&2; exit 1; }
COMMIT=$(git rev-parse --short=12 HEAD)

rm -rf "$STAGE"
mkdir -p "$STAGE"
git archive HEAD store | tar -x -C "$STAGE"
mv "$STAGE/store/"* "$STAGE/"
rm -rf "$STAGE/store"
git archive HEAD ohsatv tools/vendor LICENSE | tar -x -C "$STAGE"
# Not shipped: the window, and the string check's wheel (a development tool).
rm -rf "$STAGE/ohsatv/ui"
rm -f "$STAGE/tools/vendor/shack_i18n-"*.whl
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
echo "$COMMIT" > "$STAGE/ohsatv/COMMIT"
git -C "$STAGE" init -q -b main
git -C "$STAGE" add -A
git -C "$STAGE" -c user.name="build-store" -c user.email="build@openhamshack.invalid" \
    commit -q -m "shack-atv store package from openhamshack-atv $COMMIT"

"$PACKAGER" "$STAGE" . "$OUT" --key "$KEY" --dock360 "$DOCK360_SRC"
rm -rf "$STAGE"
ls -l "$OUT"
