#!/bin/sh
# Format patch/navigation.c, the files split out of it and the Markdown docs. patch/link.ld and
# patch/contexts.inc have no usable formatter and are deliberately absent.
set -eu
cd "$(dirname "$0")/.."

clang-format -i patch/navigation.c patch/theme.h patch/playlists.c patch/home.c patch/volume.c
prettier --write "*.md" "docs/*.md"
