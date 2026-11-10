#!/bin/sh
# Format patch/navigation.c, the files split out of it and the Markdown docs. patch/link.ld and
# patch/contexts.inc have no usable formatter and are deliberately absent.
set -eu
cd "$(dirname "$0")/.."

# tools/build.py's NAVIGATION, so a file split out is formatted once it builds
nav=$(python3 -c 'import sys; sys.path.insert(0, "tools"); import build; print(" ".join("patch/" + f for f in build.NAVIGATION))')
clang-format -i $nav patch/navigation.h patch/theme.h patch/coverflow.h patch/coverflow_thumb.c patch/coverflow_letters.c
prettier --write "*.md" "docs/*.md"
