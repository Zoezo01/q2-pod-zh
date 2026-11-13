# Building

| Path      | Holds                                                                        |
| --------- | ---------------------------------------------------------------------------- |
| `patch/`  | The firmware payload (C, MIPS assembly, linker script) and the iPod UI audit |
| `tools/`  | Build, port and release scripts                                              |
| `test/`   | Host and MIPS emulator tests, each runnable as `python3 test/<name>.py`      |
| `assets/` | Boot splash, added icons (`icons/`) and the README banner                    |
| `docs/`   | Documentation                                                                |

## Requirements

- clang, lld and llvm-objcopy
- squashfs-tools 4.6 or later (tested with 4.6.1 and 4.7.5)
- ImageMagick 6 or 7 for the iPod UI's settings icons (`convert` or `magick`; tested with 6.9.12)
- The test dependencies: `pip install -r requirements.txt`
- The original stock ZIP. Its SHA-256 is:

```text
4e298cab6c12d86cfc55565645f7e3ae11d3fb44677a93ca13991e7184ecec4e
```

## Build

```sh
python3 tools/build.py 'Q2 Firmware V1.36.zip' --out /tmp/q2-build                 # release build
python3 tools/build.py 'Q2 Firmware V1.36.zip' --out /tmp/q2-dev --dev             # test build
```

Rebuilt firmware uses `assets/boot-logo.jpg` as the boot splash. Pass another 320x375 JPEG with `--logo` to use your own; see [boot.md](boot.md#custom-boot-logo).

The build replaces the stock equalizer page with a 30-band PEQ editor (bands, shelves, preamp, on/off, presets, stock's built-in genre presets, `/EQ` import) and patch `hciplayer`'s equalizer filter with the matching DSP. Both also clear the 44.1 kHz AAC capability bit in `bluealsa` (see [internals.md](internals.md#bluetooth-aac)).

### Stopped-audio notifier wait

Off by default, `--dev --aic-idle-wait` (on by default from 1.5.0 to 1.6.1). The audited audio driver's stopped-state 10 ms polling is replaced by an
event wait, woken by start/resume and teardown. Active PCM notification timing, period and
buffer limits are unchanged. See
[audio-notifier.md](audio-notifier.md) for the exact scope.

```sh
python3 tools/build.py 'Q2 Firmware V1.36.zip' --out /tmp/q2-notifier --dev --aic-idle-wait
```

### FLAC fast paths

On by default: `--flac-experiments sync,lpc,output` (all three). Pass a subset to build only some, or `--flac-experiments ""` for the stock decoder paths. Each is checked against the pinned V1.36 player.

### Versioning

The updater refuses only a firmware whose version literal is identical to the installed one (`update_firmware`, `0x502bd0`; the online check `check_otginfo`, `0x5030f8`). So each build carries a 5-character tag, `VERSION` (`x.y.z`, single digits) and `BETA` (a pre-release's beta number, 0 for the release) packed as `V<xyz><beta>`; a release build stops unless both are single digits. For example, 1.8.0 beta 2 is `V1802` and 1.8.0 is `V1800`, so each beta and then the release installs over the one before. Releases up to 1.8.0 BETA were packed as `V<xyz>I` (1.0.0 was `V100I`). The mod installs over stock and over any other build, and stock V1.36 installs back over it.

About's **FW. Version** row reads the same literal, so it shows the stock firmware's version (`STOCK_VERSION`, from the stock `firmware_v20.info`). A **CFW. Version** row below it shows `<version> Pod`, plus ` beta N` in a beta, or ` dev N` in `--dev` builds. Release tags use the plain version (`1.0.0`); releases up to 1.0 were `V`-prefixed. Each release carries `update.tar`.

`--dev` numbers each test build one past the untracked `.build-number` and the `build-number` in the git directory that every worktree of the checkout shares, and tags it `V` plus that number in three base-36 digits, then `i` (`V001i`, `V002i` … `VZZZi`). No two test builds from one repository's worktrees share a tag (a separate clone counts on its own), and none equals a release's, so the updater installs each test build over the last and the release over any of them. `--dev` applies to that build only: the release procedure never passes it, and the manifest records the build number as `dev` (`false` otherwise).

`--update-repo OWNER/NAME` points **Network Update** at another GitHub repo's latest release, so a fork's test builds update from the fork's own releases. It defaults to `DiamondBond/q2-pod`, so a build without it is unchanged. The manifest records it as `update_repo`, and `tools/release.py` refuses a build that points anywhere else.

Development builds also start `q2remote`, a key-gated root shell with screenshots and input over Wi-Fi, for developers and agents; see [remote.md](remote.md).

Off by default, `--dev --rotator-lazy-clock`: the rotator clock is held only from its first conversion. See [clock gating](clock-gating.md) for the exact module guard and tests.

Off by default, `--dev --pwm-idle-clock`: the paired PWM/MCU clock gating. It needs all three matching files and a reboot. See [PWM clock gating](pwm-clock-gating.md) for its ownership protocol and fallbacks.

All three kernel-module experiments were on by default from 1.5.0 to 1.6.1 and went back to `--dev` in 1.6.2: no battery saving was measured for any of them, and none handles suspend/resume, which stock Standby does by default.

`--dev --mcu-idle-sleep` is off in release builds. It adds an interruptible MCU sleep with an explicit wake handshake. See [MCU sleep](mcu-sleep.md).

## Tests

```sh
python3 test/peq.py                        # PEQ parser, storage, DSP, EQ page (both tabs), 3EQ filters, player, visualizer FFT and scrobble upload (host cc; player needs -m32 libs, upload libcrypto)
python3 test/peq.py --captures /tmp/eq --font BUILD/default.ttf  # the same, plus the EQ page's Simple tab in both iPod themes as PNGs
python3 test/coverflow.py                  # Coverflow art cache and depth renderer (host cc -m32, pthreads)
python3 test/coverflow.py --captures /tmp/cf  # the same, plus the renderer's frames as PNGs
python3 test/mp3.py                        # native Xing seek bounds, CRC and truncated headers; TXXX encodings
python3 test/mp3_gapless.py                # gapless parser/PCM and generated LAME/FFmpeg comparisons (cc, lame, ffmpeg, pkg-config, libavcodec/libavutil/libmad development files)
python3 test/mp3_gapless_player.py BUILD   # the built gapless hooks, then stock's demuxer and libmad under Unicorn on generated files (lame, unsquashfs, readelf)
python3 test/build.py                      # JPEG headers, dev tags, and the boot scripts (S90play's dual boot and crash log, S11logo's splash) under the host sh; no ZIP or emulator
python3 test/build.py 'Q2 Firmware V1.36.zip'   # packaging checks (custom logo, quoted paths)
python3 test/build.py --build BUILD         # a finished build's payloads and packaging
python3 test/aic.py STOCKFS               # stopped audio notifier: actual MIPS hooks and modeled wait races
python3 test/aic_build.py STOCKFS BUILD    # notifier development guard, ABI, default/experimental packaging
python3 test/remote.py                     # q2remote protocol, auth, event and framebuffer checks
python3 test/rotator.py STOCK_MODULE        # actual MIPS rotator clock ownership and failure paths
python3 test/rotator_build.py [BUILD...]    # dev-only guard (no build needed); given builds, packed/default module isolation
python3 test/playback.py /tmp/q2-build     # grouped traversal, snapshots and the wheel's ticks (cc)
python3 test/patch.py /tmp/q2-build        # the patched MIPS payload, wheel and UI scenarios (cc)
python3 test/chapters.py                   # embedded chapter parsing, both MP4 formats and async extraction
python3 test/m4b.py /tmp/q2-build          # native TagLib M4B/MP4 metadata dispatch
python3 test/id3.py /tmp/q2-build          # the built player's MP3 ReplayGain on ID3v2.3/2.4 tags
python3 test/hci_wait.py /tmp/q2-build     # the built player's futex waits for Play and while paused
python3 test/hci_ao.py /tmp/q2-build       # a natural change in the real player against a kernel-faithful PCM: reopens, a kept PCM, a dry first write
python3 test/bt_resample.py /tmp/q2-build  # the built player's Bluetooth resampler across a gapless change
python3 test/xfade_player.py /tmp/q2-build  # the built player's crossfade: the hold, the mix at the same and another rate, the drops
python3 test/hci_natural.py /tmp/q2-build   # natural changes in the real player: a bit-exact seam, a bad next file, an understated length, a late preload, Crossfade's late next and moved fade
python3 test/player_seam.py /tmp/q2-build   # AAC, Opus and Vorbis gapless trims in the real player, each format's seam and each wrong tag against stock (ffmpeg, unsquashfs)
python3 test/mp3_gapless_player.py /tmp/q2-build  # built MIPS gapless hooks, preload isolation, seeks and cleanup
python3 test/ldac.py /tmp/q2-build         # shipped LDAC intermediate rates and native adaptive controller
python3 test/headset_io.py /tmp/q2-build   # real MIPS headset worker with simulated kernel I/O (cc)
python3 test/bluealsa.py /tmp/q2-build     # the patched bluealsa's DROP paths against stock's, delayed encoder signal
python3 test/alsa.py BUILD QEMU_MIPSEL     # ALSA startup lock regression (see internals.md)
python3 test/ui_preview.py BEFORE AFTER OUT_DIR  # not a test: side-by-side iPod layout review sheets of two builds (or one build's two themes; --themes, ImageMagick)
python3 -m pytest test/unit                # tools/: no stock ZIP, no build, no Q2 (pip install -r test/unit/requirements.txt); GitHub runs it on every pull request
python3 -m pytest test/fw --basetemp ~/.cache/q2-pytest/fw  # the firmware's C on this machine: no stock ZIP, no build, no Q2 (see Firmware tests)
```

- **MIPS suite:** `test/patch.py` executes the actual patched payload and the stock key and touch filters under Unicorn. UI services are mocked; the one other mocked boundary is the `system()` call that Key Tone uses to click the speaker. Case coverage lives in `test/patch.py`. Run the payload, headset worker, metadata dispatch and packaging checks for both Stock and iPod builds.
- **Where a machine's hook runs:** `Machine.hook` (the mocks, and what a test's subclass adds) is Python, so Unicorn does not call it at every instruction: a small C filter, compiled as the suite starts (`cc`), calls it at each symbol's address, at each address a mock is set at and anywhere in the mocks' own region (`0x1000000` up). A hook that tests any other address itself (a place inside a stock function) names it first with `watch(address)`, or is never called there. `test/playback.py` and `test/headset_io.py` run the same machine.
- **Draw-call trace:** `Q2_TRACE=FILE python3 test/patch.py BUILD` also writes every draw and widget-property call, each hook's return and each passed case to FILE, by value: payload addresses appear as symbol names (or `P`), stack ones as `S`. Two builds that draw the same give byte-identical files, so `cmp` checks a refactor that should change nothing on screen.
- **Known gap:** the stock rounded vgcanvas branch can't run end to end under Unicorn 2.1.4. Stock is built `-mfp64`, and Unicorn's MIPS32 FPU only implements `FR=0`, so its 64-bit conversions trap. The test runs the branch up to that instruction and asserts the calls before it.
- **Reproducibility:** two fresh builds must produce identical `update.tar` files. Packaging verifies MD5 entries, unchanged kernel and rootfs metadata, and a rootfs no larger than stock.
- **Space:** the payload is built with `-Oz`, and demo's `.pdr` section (MIPS procedure descriptors, 423 KB past every LOAD segment, never read at run time) ships zeroed. mksquashfs packs file tails into fragments (`-tailends`), which saves about 26 KB. For rootfs headroom see [Boot](boot.md#making-a-logo-that-works).
- **Manifest checks:** the runner refuses a `manifest.json` whose `source_sha256` doesn't match the current patch sources, and verifies the stock, patched and payload hashes against it.
- **Guards:** the builder rejects any `patch/contexts.inc` name that isn't a window name in the stock rootfs UI assets, so an allowlist typo can't silently disable a screen. Widget offsets and shared ABI constants live in `patch/offsets.inc`, which both the payload and the test mocks read.
- **Limits:** instruction counts are mocked-service measurements, not hardware latency.

## Firmware tests

`test/patch.py` runs the real MIPS payload, but it needs a build, so a stock ZIP. `test/fw` runs the firmware's C without one, on every pull request and push to `main` (`.github/workflows/firmware.yml`, with the host suites above that need no build).

`test/fw/host.py` compiles `navigation.c` and `tools/peq.py`'s `UI_SOURCES`, the payload's own files, as they are for the Q2, not through `PEQ_HOST`, and links them into one library the tests load with ctypes. Their `stock.h` is `tools/build.py`'s own (`stock_header`) and `themes.inc` `tools/theme.py`'s, and `peq_platform.h` lays stock's functions out as `tools/peq.py` does, so the colour path through `peq.h` is the device's. A stock call, or a libc call with effects outside the process, is a stub: it records the call and returns 0 of its declared type (a float in the float register), or what the test's `fw.mock` returns; `tk_snprintf` and `tk_strcmp` answer as `vsnprintf` and `strcmp` unless mocked. So a test can't touch the card, the network or this machine.

```sh
python3 -m pytest test/fw --basetemp ~/.cache/q2-pytest/fw
Q2_FW_SANITIZE=1 ASAN_OPTIONS=detect_leaks=0:allocator_may_return_null=1 LD_PRELOAD=$(cc -print-file-name=libasan.so) \
    python3 -m pytest test/fw --basetemp ~/.cache/q2-pytest/fw-asan  # AddressSanitizer and UBSan
Q2_FW_COVERAGE=1 python3 -m pytest test/fw  # .gcda files for gcov
Q2_FW_STRICT=1 python3 -m pytest test/fw    # fail a test that reaches a stub tools/ doesn't declare, unmocked
```

- **Where it writes:** each test loads its own copy of the library (about 20 MB, more under ASan), deleted as soon as it's loaded, and a passing test's directory goes (`tmp_path_retention_policy`); a run leaves the session's build, a few MB. pytest's default base is under `/tmp`, which is RAM on many systems, so `--basetemp` on disk is the safer habit. A test that crashes the C (a segfault, a divide by zero) ends the whole run: `-v` names it.
- **Python:** stock's fixed addresses (0x400000-0xc00000) must be free, so the python has to be position-independent. Debian's and Ubuntu's own `/usr/bin/python3` isn't; one built with the toolchain's default `-pie` (Arch's, Fedora's, `actions/setup-python`'s builds) is. conftest stops with a message on one that isn't.
- **Mirrors:** `fw.var` checks a ctypes Structure mirror against the symbol's size, so a C struct that changed fails there instead of reading wrong offsets; `host.offsets(obj, type)` reads a struct's offsets from the debug info where a mirror would be fragile.
- **q2video:** `patch/video.c` is a program of its own on the Q2, so `test/fw/video_host.py` builds it into a library of its own, the same way: the glibc and alsa-lib calls it declares are stubs, and `fw_video_run` runs `main` to its return or its `_exit`. There is no second process: when `fork` answers a pid, the harness first runs `ffmpeg()`'s child's half in place, with that call's own arguments read off its frame where the debug info puts them, so the child's `dup2`s and `execv` reach the stubs too. Reading that frame takes x86-64's layout, so the harness builds nowhere else. `test/fw/test_video.py` answers the stubs as a simulated Q2.

- **Writing one:** `fw.fn('lyrics__lrc_time', restype, *argtypes)` reaches a static as `<file>__<name>`, and `fw.mock` and `fw.calls` take stub names: a stock function's, or `fw_<name>` for libc (`fw_fopen`). Work out the expected answer from what the function is for before running it.
- **A known bug** stays as `@pytest.mark.xfail(strict=True, reason='BUG: ...')`: the test fails the job as soon as the bug is fixed, until the mark goes.
- **Limits:** the code runs 64-bit, so `long` and pointers are wider than on the Q2, and stock widgets' layouts don't hold. Code that depends on those stays with `test/patch.py`. `peq_player.c`, `mp3_gapless.c` and the FLAC paths are the player's, tested by `test/peq.py`, `test/mp3_gapless.py` and the `test/flac_*.py` scripts.

## Type check

`.github/workflows/types.yml` runs [mypy](https://mypy-lang.org) over `tools/`, `test/unit` and `test/fw` on every pull request and push to `main`. Few functions carry annotations, so mypy checks the bodies of unannotated ones too (`check_untyped_defs` in `pyproject.toml`): a misspelt name, a wrong argument count, a `None` used as a value, bytes for a str.

```sh
pip install -r requirements.txt -r test/unit/requirements.txt mypy==2.4.1
mypy tools test/unit
mypy test/fw  # its own run: test/unit has a conftest module too
```

- **`need(value, message)`** in `tools/build.py` is `check` for a search or lookup that can come back `None`: it returns the value or raises the same `ValueError`. mypy can't see through `check(m is not None, ...)`, and `re.search(...)[1]` alone fails.
- **Off on purpose:** `func-returns-value` (the tests' mocks return `list.append`'s `None`) and `var-annotated` (an empty list or dict). A name may take a new type (`allow_redefinition_new`), as in `d = pathlib.Path(d)`.
- **ctypes `_fields_`** is one list, `[*((k, C.c_int) for k in ...), ('x', C.c_void_p)]`: mypy reads lists added together as a list of `object`, which `Structure` refuses.

## Mutation tests

`.github/workflows/tests.yml` runs `test/unit` on every pull request and push to `main`. `.github/workflows/mutation.yml` runs [mutmut](https://github.com/boxed/mutmut) on every function in `tools/`, and on Sundays `test/cmut` over the C ([below](#c)). mutmut breaks a copy of each function in one small way at a time and checks that `test/unit` notices. It uploads every surviving mutant and mutmut's log as `surviving-mutants`.

- **Pull requests:** mutate only the functions they add or change since their base commit (`targets.py --since`), and report the score in the run's summary without failing. `targets.py` asks mutmut's own generator which functions have mutants, so a change to a decorated function (but for a lone `@staticmethod` or `@classmethod`) or to one with nothing to mutate, such as `return self.x`, mutates nothing.
- **Nightly:** mutmut keeps a hash of each function with its results in `mutants/`, which the workflow caches, and reruns only the mutants of functions whose code changed. It finds each function's tests afresh, so a function added since Sunday gets its tests, and copies `patch/`, `assets/`, `docs/` and `test/` again. A function already marked as reached by no test keeps that until Sunday. A night with no change to `tools/` still makes every mutant and finds the tests, about three minutes on a desktop. A change to `test/unit`, `test/release.py`, the requirements, or a `.S`, `.c` or `.h` file in `tools/` starts the cache afresh: mutmut keeps its old copies of those, and tests read them. The job fails under 90%, a floor under a fresh run's score: it catches a collapse, not each survivor.
- **Sundays, and by hand from the Actions tab:** everything from scratch, because a cached result can't see a change outside its own function, such as a module's constant or a function it calls. 15,920 mutants took 75 minutes on four workers of a Ryzen 7 7800X3D desktop, and 91.7% were caught (96.1% outside art.py). art.py's 2,634 take most of that time, each about 5 seconds against everything else's fraction of one. GitHub's runners are slower, so the job allows 240 minutes. Every art.py mutant runs `test_art.py`, so only the two tests that read the pane's icons draw them, the slow part, and the rest share one drawing. One of them checks the other tests' images are the shipped theme's. mutmut forks each mutant's run from a process that has already run the tests, so `conftest.py` clears every `functools` cache at the start of a session, or art's cached images would hide the mutants under them, and caps each process at 4 GB, as a mutant can loop for ever filling memory.

```sh
pip install -r requirements.txt -r test/unit/requirements.txt
mutmut run                                                  # all of tools/; needs clang, lld and llvm
mutmut run $(python3 test/unit/targets.py --since main)     # or only what a branch changes
python3 test/unit/report.py --survivors /tmp/survivors.txt  # score, table, each survivor's diff (--since as above)
```

- **Needs no stock firmware:** the fixtures in `test/unit` are synthetic, and the stock ZIP is neither fetched nor kept. A function that pins a Shanling file's hash or links against one carries `# pragma: no mutate block` on its `def` line, so it has no mutants: `compile_payload`, `patch_asset`, `compile_common`, `patch_player`, the audio module's `validate_stock` and `patch_module`, the rotator's `patch_module`, and PWM's `link`, `patch_pwm`, `patch_mcu` and `patch_bundle`. They stay with the scripts above, which need a build. A new one gets the same pragma.
- **Keep tests behind the fence:** a mutant changes a command before it runs, and `q2remote`'s commands are shell scripts for a live Q2. `test/unit/conftest.py` refuses every process but the compilers, along with sockets and signals, and hides `Q2_HOST`. `test/remote.py` runs real commands through a real shell, so it stays out of `test/unit`.
- **`build()`** carries `# pragma: no mutate block`. mutmut writes out a whole copy of a function per mutant, so its 440 lines alone made a 96 MB file that never finished generating. The pragma is on art.py's `wave()` too: only `WAVES` calls it, at import, before mutmut can switch a mutant on, so all 21 of its mutants survived.
- **Survivors are not all gaps.** Most left are equivalent (`<I` as `<i` on a word under 2^31) or machine code in `patch_bluealsa`, which only `test/bluealsa.py` can judge against a build.

### C

mutmut mutates Python only. For the C (1,158 functions added or changed since 1.2.0, 394 of them in `navigation.c`), `test/cmut` works one file at a time. `mutate.py` writes each mutant as a whole copy of the file, one token-sized change each: a comparison's neighbours, `&&` for `||`, a constant one up, one down or 0, a condition held true or false, a statement gone, a return of 0. `run.py` compiles each as its tests build it and tests only those that compile to a new object: one the compiler rejects, or that compiles to the original's or an earlier mutant's object, can't behave differently, and none of those count towards the score. A host suite's own build refusing a mutant counts as the compiler's rejection too, not a catch: `mp3_gapless.py`'s wrapper calls `mp3_trim`, which the file alone leaves unused, so it rejects a few mutants the table's flags let through. A compile or link that fails without the compiler naming a line of the source, or for want of room or memory, is the sandbox's failure: the run stops there, unanswered, and its report says it stopped short. Mutants of C run as real code, so `sandbox.sh` runs them in a container with no network, a read-only root and only a 1 GB tmpfs to write, as the fence above can't reach them.

```sh
docker build -t q2-cmut test/cmut
python3 test/cmut/mutate.py patch/radio.c /tmp/cmut/radio  # each mutant a whole copy, and manifest.jsonl saying where
CMUT_PYTHON=$pythonLocation test/cmut/sandbox.sh patch/radio.c /tmp/cmut/radio /tmp/cmut/radio.jsonl --workers 4  # rerun to resume after a stop
python3 test/cmut/report.py patch/radio.c /tmp/cmut/radio /tmp/cmut/radio.jsonl --survivors /tmp/cmut/radio.txt --copy /tmp/cmut/radio-left  # the score; each survivor as a diff
# after a new test: the survivors alone, into a fresh results file
CMUT_PYTHON=$pythonLocation test/cmut/sandbox.sh patch/radio.c /tmp/cmut/radio-left /tmp/cmut/radio-left.jsonl --workers 4
python3 test/cmut/report.py patch/radio.c /tmp/cmut/radio-left /tmp/cmut/radio-left.jsonl --survivors /tmp/cmut/radio-left.txt
```

- **Wired up:** a table in `sandbox.sh` holds each file, its test and what that test reads. `library_scope.c`, `chapters.c` and `mp3_gapless.c` run through their own scripts, which build the file themselves, with the flags the table repeats. `lockscreen.c`, `photos.c`, `headset.c`, `radio.c` and `visualizer.c` run through their own `test/fw/test_<name>.py` alone. Another file `test/fw` builds is one more name in the table's `test/fw` line, and its run takes longer the less of it its tests select: a test file that forks runs all its tests for every mutant, as below, so `navigation.c`'s and `playback.c`'s would.
- **`test/fw`'s files:** `test/fw/host.py` builds the library once. A mutant compiles its one file with `host.py`'s flags, its statics renamed as `host.build` renames them, and links with the other objects into a library of its own, which `conftest.py` loads instead of building one when `Q2_FW_LIBRARY` names it. `run.py` stops if that link makes a library unlike `host.build`'s, or the original compiles to other symbols. Each mutant's object waits for its test on the tmpfs while 256 MB of it is left, and is compiled again for its test after that, so a large file's mutants take longer but can't fill it.
- **Only the tests that reach it:** a test that never runs a mutant's lines sees what the original does. So `run.py` builds the original with gcov's counters and runs each test alone first, and a mutant runs only the tests that ran its lines; a line with no code of its own (a table, a `#define`'s body), a line with a `case` label, as the `switch` jumps there from tests that ran other lines, or a line no test runs, runs them all. Not yet for a test file that forks (`in_fork`): a child's counts used to end with it, unwritten; they now reach the file when the child closes the library, so `run.py` could select for those files too, once measured. pytest stops at the first failure and runs the test that killed the last mutant first, as neighbouring mutants mostly fall to the same test, once the original passes its tests backwards as well as forwards. Each run forks from a process that has already imported pytest and `host.py`, and has seconds of CPU, not of the clock, so a loaded machine can't time out a survivor.
- **`CMUT_PYTHON`:** the image's own Python can't load `test/fw` (see [Firmware tests](#firmware-tests)), so `sandbox.sh` mounts the folder `CMUT_PYTHON` names, read-only: a position-independent Python 3.12 with `requirements.txt` and `test/unit/requirements.txt` installed, as `actions/setup-python`'s (`$pythonLocation`). The host suites don't need it.
- **Killing survivors:** each survivor in the `--survivors` file is `=== <id>.c` and its diff against the file, line numbers in the `@@` header; a killed mutant's `reason` in the results names the first test that failed, or the test a crash was in. `report.py --copy` puts the survivors and their lines of the manifest in a folder of their own, so a new test runs against them alone in a minute or two; `CMUT_TEST` mounts another copy of the test in place of the file's own. Some survivors can't be killed: they change nothing a caller can see, or need a failed allocation or another thread.
- **Weekly:** `mutation.yml`'s `cmut` job mutates every wired file on Sundays, with mutmut's run from scratch, and by hand from the Actions tab, each file on a runner of its own, side by side. Each uploads its survivors and results as `c-surviving-mutants-<file>`, and fails only when its score falls under its floor in the job, a little under the score below, or its run stops short: it catches a collapse, not each survivor.

| File              | Its tests                    | Mutants | Tested | Caught | Floor | Four threads |
| ----------------- | ---------------------------- | ------: | -----: | -----: | ----: | -----------: |
| `library_scope.c` | `test/library_scope.py`      |     612 |    427 |  89.0% |   87% |         58 s |
| `chapters.c`      | `test/chapters.py`           |   1,050 |    773 |  92.2% |   90% |   3 min 58 s |
| `mp3_gapless.c`   | `test/mp3_gapless.py`        |   2,069 |  1,106 |  70.8% |   68% |   6 min 44 s |
| `lockscreen.c`    | `test/fw/test_lockscreen.py` |   2,161 |  1,740 |  79.2% |   77% |   4 min 45 s |
| `photos.c`        | `test/fw/test_photos.py`     |   1,707 |  1,449 |  88.8% |   86% |   4 min 53 s |
| `headset.c`       | `test/fw/test_headset.py`    |   1,701 |  1,429 |  94.0% |   92% |   5 min 30 s |
| `radio.c`         | `test/fw/test_radio.py`      |   2,198 |  1,884 |  89.4% |   87% |   5 min 59 s |
| `visualizer.c`    | `test/fw/test_visualizer.py` |   2,160 |  1,910 |  91.1% |   89% |   4 min 46 s |

All 13,658 took 38 minutes one file after another, pinned with `CMUT_CPUS` to four threads on two cores of a Ryzen 7 7800X3D desktop, as a 4-vCPU runner's threads are, at a load average under 6, and 45 minutes before `test/fw`'s files had their survivors' tests, at a load average of 10 to 22. A runner's slower cores could take that past an hour, so the job gives each file a runner of its own and 60 minutes, against 7 for the longest here. The results' last lines add up to 7,604 seconds of the container's CPU time (`cpu_seconds`, which load from outside stretches less than the clock's). Runs with the same tests agree on every mutant but one, `photos.c`'s `y <= sh` in a loop that writes one row past its buffer, which crashed once: what lies past the buffer decides it. What `test/fw`'s files leave alive is mostly design constants (colours, coordinates, dither tables, waits) that the tests check by relation rather than by value, and lines with no code of their own, which every test runs against. The host suites check their limits by relation too, so a mutant that moves one consistently lives: `SCOPE_MAX` one lower for both the scan and the reader, or `chapters_poll`'s reads a tick and bytes a read. `mp3_gapless.c`'s checks from the pilot's branch expect a tag frame too small for LAME's extension to be refused, which the parser now reads as a silent frame, so they stay behind and its score is the suite's as it is.

The order for the rest:

1. The rest of `test/fw`'s files, by size: `navigation.c` and `playback.c` among them. `test/patch.py` takes about 3.5 minutes a run and needs a stock build, so it can't run per mutant in public CI.
2. `coverflow.c` and the `peq*` files, through `test/coverflow.py` and `test/peq.py`, which need the 32-bit libraries.

## Coverage

Both workflows count what their stock-free runs reach and fail under a floor, and each run's summary has the table.

- **`tools/`:** `tests.yml` runs `test/unit` under [coverage.py](https://coverage.readthedocs.io), every branch counted, and fails under `fail_under` in `pyproject.toml`. The functions with `# pragma: no mutate block` on their def line and the command lines are left out, so the floor is all of the rest.
- **C:** `firmware.yml` runs `test/fw` and the host suites with `test/coverage/cc` first on `PATH`: `cc` with gcov counters, which keeps each compile's counts under `$Q2_COV` when a suite deletes its build folder. [gcovr](https://gcovr.com) reads them, fails under the line and branch floors in the workflow, and uploads the per-line pages as `c-coverage`. The ASan run isn't counted. Most of `navigation.c` is `test/patch.py`'s, which needs a stock build, so it stays low here.
- **Floors:** they catch a collapse (a suite or the wrapper that stopped counting), not each change, so they sit a little under the measured figures. Raise one by hand when the measured figure is well clear of it. The host suites run only counted in CI; `test/fw` also runs plain under ASan.

```sh
pip install -r requirements.txt -r test/unit/requirements.txt gcovr==8.4
coverage run -m pytest test/unit --basetemp ~/.cache/q2-pytest/unit && coverage report  # needs clang, lld and llvm
export Q2_COV=~/.cache/q2-cov; rm -rf $Q2_COV; mkdir -p $Q2_COV  # counts add up across runs: start empty
PATH=$PWD/test/coverage:$PATH python3 -m pytest test/fw --basetemp ~/.cache/q2-pytest/fw  # and the host suites, the same way
gcovr --root . --filter patch/ --gcov-ignore-errors=source_not_found --html-details ~/.cache/c-coverage.html $Q2_COV
```
