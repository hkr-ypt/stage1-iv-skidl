#!/usr/bin/env bash
#
# install_kicad_libs.sh — install the official KiCad symbol + footprint
# libraries at a pinned upstream revision, so that every identifier this
# project uses can be READ from a real library file rather than asserted.
#
# Usage:
#     sudo tools/install_kicad_libs.sh [PREFIX] [TAG]
#
#     PREFIX  install root                (default: /usr/share/kicad)
#     TAG     upstream git tag to check out (default: $KICAD_LIB_TAG or 9.0.9.1)
#
# Produces:
#     $PREFIX/symbols/*.kicad_sym
#     $PREFIX/footprints/*.pretty/*.kicad_mod
#     $PREFIX/{symbols,footprints}/.kicad_lib_provenance.json
#
# The provenance stamp records the upstream repository, tag and commit SHA so
# tools/resolve_kicad_libs.py can report exactly which library revision a value
# was read from.  Point KICAD9_SYMBOL_DIR / KICAD9_FOOTPRINT_DIR (or
# kicad_libs.json) at the result.
#
set -euo pipefail

PREFIX="${1:-/usr/share/kicad}"
TAG="${2:-${KICAD_LIB_TAG:-9.0.9.1}}"

SYM_REPO="https://gitlab.com/kicad/libraries/kicad-symbols.git"
FP_REPO="https://gitlab.com/kicad/libraries/kicad-footprints.git"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

install_one() {
    local repo="$1" dest="$2" name="$3"
    echo "==> cloning $name @ $TAG"
    git clone --depth 1 --branch "$TAG" "$repo" "$WORK/$name" --quiet
    local sha
    sha="$(git -C "$WORK/$name" rev-parse HEAD)"
    rm -rf "$WORK/$name/.git"

    mkdir -p "$(dirname "$dest")"
    rm -rf "$dest"
    mv "$WORK/$name" "$dest"

    cat >"$dest/.kicad_lib_provenance.json" <<EOF
{
  "repository": "$repo",
  "tag": "$TAG",
  "commit": "$sha",
  "installed_by": "tools/install_kicad_libs.sh"
}
EOF
    echo "    $dest  ($TAG @ ${sha:0:12})"
}

install_one "$SYM_REPO" "$PREFIX/symbols"    kicad-symbols
install_one "$FP_REPO"  "$PREFIX/footprints" kicad-footprints

echo
echo "Installed. Verify with:"
echo "    python3 tools/resolve_kicad_libs.py --paths --json"
