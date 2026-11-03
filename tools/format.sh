#!/bin/sh
# Format patch/navigation.c and the Markdown docs. patch/link.ld and
# patch/contexts.inc have no usable formatter and are deliberately absent.
set -eu
cd "$(dirname "$0")/.."

clang-format -i patch/navigation.c
prettier --write "*.md" "docs/*.md"
