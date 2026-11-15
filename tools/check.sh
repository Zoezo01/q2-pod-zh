#!/bin/sh
# Before a push: what CI runs that needs no stock ZIP (types.yml, tests.yml and its format check,
# firmware.yml's test/fw and tidy).
# --full adds a --dev build and every suite that reads one (tools/release.py suites), the host suites
# and test/fw under ASan. PY names a Python with test/unit's requirements and mypy (docs/building.md#type-check).
# Usage: tools/check.sh [--full [stock.zip]]
set -eu
cd "$(dirname "$0")/.."
PY=${PY:-python3}

"$PY" -m mypy tools test/unit
"$PY" -m mypy test/fw # its own run: test/unit has a conftest module too
"$PY" -m coverage run -m pytest test/unit -q
"$PY" -m coverage report >/dev/null || { "$PY" -m coverage report; exit 1; } # under pyproject.toml's floor
"$PY" -m pytest test/fw -q
PY="$PY" tools/tidy.sh
tools/format.sh --check

[ "${1:-}" = --full ] || exit 0
zip=${2:-Q2 Firmware V1.36.zip}
out=dist/dev-$(git rev-parse --short HEAD)
# The build of this commit, if one is there already, so the build checked is the one installed.
[ -e "$out/update.tar" ] || "$PY" tools/build.py "$zip" --dev --out "$out"
"$PY" -c 'import pathlib, sys; sys.path.insert(0, "tools"); import release; release.suites(pathlib.Path(sys.argv[1]).resolve())' "$out"
for t in library_scope coverflow_position chapters; do "$PY" "test/$t.py"; done
"$PY" test/rotator_build.py "$out"
Q2_FW_SANITIZE=1 ASAN_OPTIONS=detect_leaks=0:allocator_may_return_null=1 \
	LD_PRELOAD="$(cc -print-file-name=libasan.so)" "$PY" -m pytest test/fw -q
echo "check.sh --full: all passed ($out)"
