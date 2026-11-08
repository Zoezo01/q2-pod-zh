#!/bin/sh
# Format patch/navigation.c, the files split out of it and the Markdown docs. patch/link.ld and
# patch/contexts.inc have no usable formatter and are deliberately absent.
set -eu
cd "$(dirname "$0")/.."

clang-format -i patch/navigation.c patch/navigation.h patch/theme.h patch/playlists.c patch/home.c patch/volume.c patch/classic_pages.c patch/np_bar.c patch/theme_engine.c patch/coverflow.h patch/coverflow_thumb.c patch/smart_wifi.c patch/system.c patch/tags.c patch/scrobble_ui.c patch/library_lists.c patch/ratings.c
prettier --write "*.md" "docs/*.md"
