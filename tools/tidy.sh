#!/bin/sh
# clang-tidy (.clang-tidy) over the C test/fw builds (tools/build.py's NAVIGATION, tools/peq.py's UI_SOURCES), with the
# Q2's flags, against the headers test/fw/host.py writes: no stock ZIP. Then clang -fsyntax-only with the same flags and
# -Werror, so a compiler warning the build would fail on (.clang-tidy turns those off) fails here too. PY names a Python
# with test/unit's requirements.
# Usage: tools/tidy.sh [patch/file.c ...]
set -eu
cd "$(dirname "$0")/.."
PY=${PY:-python3}
inc=$(mktemp -d)
trap 'rm -rf "$inc"' EXIT
flags=$("$PY" -c 'import pathlib, sys; sys.path[:0] = ["test/fw", "tools"]; import build, host
host.headers(pathlib.Path(sys.argv[1]))
print(*(f for f in build.FLAGS if f != "-Werror"))' "$inc" 2>/dev/null)
[ $# -gt 0 ] || set -- $("$PY" -c 'import sys; sys.path[:0] = ["test/fw"]; import host; print(*host.SOURCES)' 2>/dev/null)
# shellcheck disable=SC2086 # flags is a word list
printf '%s\n' "$@" | xargs -P "$(nproc)" -I{} clang-tidy -quiet {} -- $flags -fno-math-errno -I "$inc" -I patch
# shellcheck disable=SC2086 # flags is a word list
printf '%s\n' "$@" | xargs -P "$(nproc)" -I{} clang -fsyntax-only -Werror {} $flags -fno-math-errno -I "$inc" -I patch
