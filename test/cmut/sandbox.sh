#!/bin/sh
# Run a directory of C mutants with no network, a read-only root, no capabilities and a non-root user.
# Only a 1 GB tmpfs is writable; results leave on stdout, so no mutant can reach them. Rerun to resume.
# usage: sandbox.sh SOURCE MUTANT_DIR RESULTS.jsonl [run.py options]
# CMUT_TEST is another copy of the test, to try it against the same mutants. CMUT_CPUS pins the run, to time it.
# CMUT_IMAGE is the image test/cmut/Dockerfile builds, q2-cmut by default. A file test/fw tests needs CMUT_PYTHON,
# the folder of a position-independent Python 3.12 with requirements.txt and test/unit/requirements.txt installed
# (actions/setup-python's $pythonLocation; see docs/building.md#firmware-tests), mounted read-only where it is.
set -eu
here=$(dirname "$(realpath "$0")")
root=$(git -C "$here" rev-parse --show-toplevel)
source=$(realpath --relative-to="$root" "$1")
mutants=$(realpath "$2")
results=$(realpath -m "$3")
shift 3

# Each file's test, the flags that test builds it with, and what else in the repository the test reads.
# mp3_gapless.py wraps the file in code that calls the static mp3_trim, which is unused on its own.
# A test/fw file is the one named for the source, against the library test/fw/host.py builds from the tree.
fw=
case $source in
patch/library_scope.c)
    test=test/library_scope.py
    cflags='-fPIC -pthread -DPEQ_HOST -DPEQ_ROOT=\"/tmp/q2\" -O1 -Wall -Wextra -Werror'
    mounts=patch ;;
patch/chapters.c)
    test=test/chapters.py
    cflags='-fPIC -DPEQ_HOST -Wall -Wextra -Werror'
    mounts=patch ;;
patch/mp3_gapless.c)
    test=test/mp3_gapless.py
    cflags='-fPIC -DPEQ_HOST -fsanitize=undefined -fno-sanitize-recover=undefined -Wall -Wextra -Werror
        -Wno-unused-function'
    mounts=patch ;;
patch/lockscreen.c|patch/photos.c|patch/headset.c|patch/radio.c|patch/visualizer.c)
    name=${source#patch/}
    test=test/fw/test_${name%.c}.py
    fw=1
    mounts='patch tools assets test/fw pyproject.toml' ;;
*)
    echo "$0: no test wired up for $source" >&2
    exit 2 ;;
esac

image=${CMUT_IMAGE:-q2-cmut}
if [ -n "$fw" ]; then
    python=$(realpath "${CMUT_PYTHON:?a position-independent Python for test/fw: see the top of $0}")
    set -- -v "$python:$python:ro" -e LD_LIBRARY_PATH="$python/lib" \
        "$image" "$python/bin/python3" /run.py --fw --source "$source" --test "$test" "$@"
else
    set -- "$image" python3 /run.py --source "$source" --test "$test" --cflags="$cflags" "$@"
fi
for path in $mounts; do set -- -v "$root/$path:/tree/$path:ro" "$@"; done
touch "$results"
sed -i '$ { /}$/!d }' "$results"  # a line cut by a stop
exec docker run --rm --init --network none --read-only --tmpfs /tmp:size=1g,mode=1777,exec \
    --cap-drop ALL --security-opt no-new-privileges --user 1000:1000 \
    --pids-limit 1024 --memory 8g ${CMUT_CPUS:+--cpuset-cpus "$CMUT_CPUS"} \
    -v "${CMUT_TEST:-$root/$test}:/tree/$test:ro" \
    -v "$here/run.py:/run.py:ro" \
    -v "$mutants:/mutants:ro" \
    -v "$results:/done.jsonl:ro" \
    "$@" >> "$results"
