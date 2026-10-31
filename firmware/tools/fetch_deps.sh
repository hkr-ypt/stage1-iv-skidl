#!/usr/bin/env bash
# Fetch the pinned third-party sources listed in firmware/third_party.lock into
# firmware/third_party/<name>, at exactly the pinned commit, and verify it.
#
# Usage: firmware/tools/fetch_deps.sh [--check]
#   --check   only verify that every dependency is present at its pinned commit
#
# The sources are not vendored into the repository (the repository's path guard
# scans tracked text files, and the dependencies are large); they are fetched
# by commit hash, so every build uses byte-identical third-party code.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
lock="$here/third_party.lock"
dest="$here/third_party"
check_only=0
[ "${1:-}" = "--check" ] && check_only=1
mkdir -p "$dest"
status=0
while read -r name url commit tag sparse; do
    case "$name" in ""|\#*) continue ;; esac
    dir="$dest/$name"
    if [ -d "$dir/.git" ] && [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null)" = "$commit" ]; then
        echo "ok      $name $tag ($commit)"
        continue
    fi
    if [ "$check_only" = 1 ]; then
        echo "MISSING $name $tag ($commit)" >&2
        status=1
        continue
    fi
    echo "fetch   $name $tag ($commit)"
    rm -rf "$dir"
    git init -q "$dir"
    git -C "$dir" remote add origin "$url"
    if [ -n "${sparse:-}" ]; then
        git -C "$dir" sparse-checkout set --no-cone "/$sparse/"
    fi
    git -C "$dir" fetch -q --depth 1 origin "$commit"
    git -C "$dir" -c advice.detachedHead=false checkout -q --detach FETCH_HEAD
    got="$(git -C "$dir" rev-parse HEAD)"
    if [ "$got" != "$commit" ]; then
        echo "ERROR   $name: got $got, expected $commit" >&2
        exit 1
    fi
done < "$lock"
exit $status
