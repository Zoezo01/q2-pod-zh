#!/bin/sh
# Format every tracked C file and the Markdown docs. --check only reports C that isn't formatted (tests.yml,
# tools/check.sh). The .inc tables, patch/link.ld and the .S files have no usable formatter and stay out.
set -eu
cd "$(dirname "$0")/.."
c=$(git ls-files 'patch/*.[ch]' 'tools/*.[ch]' 'test/*.c')
# shellcheck disable=SC2086 # a file list
if [ "${1:-}" = --check ]; then exec clang-format --dry-run --Werror $c; fi
# shellcheck disable=SC2086
clang-format -i $c
prettier --write "*.md" "docs/*.md"
