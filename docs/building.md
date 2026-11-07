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
154c17822d09be001be35c03d2d3488424dee195221790bd70864480d55b0f00
```

## Build

```sh
python3 tools/build.py 'Q2 Firmware V1.32.zip' --out /tmp/q2-build                 # release build
python3 tools/build.py 'Q2 Firmware V1.32.zip' --out /tmp/q2-dev --dev             # test build
```

Rebuilt firmware uses `assets/boot-logo.jpg` as the boot splash. Pass another 320x375 JPEG with `--logo` to use your own; see [boot.md](boot.md#custom-boot-logo).

The build replaces the stock equalizer page with a 30-band PEQ editor (bands, shelves, preamp, on/off, presets, stock's built-in genre presets, `/EQ` import) and patch `hciplayer`'s equalizer filter with the matching DSP. Both also clear the 44.1 kHz AAC capability bit in `bluealsa` (see [internals.md](internals.md#bluetooth-aac)).

### Stopped-audio notifier wait

Off by default, `--dev --aic-idle-wait` (on by default from 1.5.0 to 1.6.1). The audited audio driver's stopped-state 10 ms polling is replaced by an
event wait, woken by start/resume and teardown. Active PCM notification timing, period and
buffer limits are unchanged. See
[audio-notifier.md](audio-notifier.md) for the exact scope.

```sh
python3 tools/build.py 'Q2 Firmware V1.32.zip' --out /tmp/q2-notifier --dev --aic-idle-wait
```

### FLAC fast paths

On by default: `--flac-experiments sync,lpc,output` (all three). Pass a subset to build only some, or `--flac-experiments ""` for the stock decoder paths. Each is checked against the pinned V1.32 player.

### Versioning

The updater refuses only a firmware whose version literal is identical to the installed one (`update_firmware`, `0x4f8440`; the online check `check_otginfo`, `0x4f8968`). So each build carries a 5-character tag, `VERSION` (`x.y.z`, single digits) packed as `V<xyz>I`. For example, 1.0.0 is `V100I`. The mod installs over stock and over any other build, and stock V1.32 installs back over it.

About's **FW. Version** row reads the same literal, so it shows the stock firmware's version (`STOCK_VERSION`, from the stock `firmware_v20.info`). A **CFW. Version** row below it shows `<version> Pod`, plus ` dev N` in `--dev` builds. Release tags use the plain version (`1.0.0`); releases up to 1.0 were `V`-prefixed. Each release carries `update.tar`.

`--dev` numbers each test build from the untracked `.build-number` and tags it `V` plus that number in three base-36 digits, then `i` (`V001i`, `V002i` … `VZZZi`). No two test builds share a tag, and none equals a release's, so the updater installs each test build over the last and the release over any of them. `--dev` applies to that build only: the release procedure never passes it, and the manifest records the build number as `dev` (`false` otherwise).

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
python3 test/build.py 'Q2 Firmware V1.32.zip'   # packaging checks (custom logo, quoted paths)
python3 test/build.py --build BUILD         # a finished build's payloads and packaging
python3 test/aic.py STOCKFS               # stopped audio notifier: actual MIPS hooks and modeled wait races
python3 test/aic_build.py STOCKFS BUILD    # notifier development guard, ABI, default/experimental packaging
python3 test/remote.py                     # q2remote protocol, auth, event and framebuffer checks
python3 test/rotator.py STOCK_MODULE        # actual MIPS rotator clock ownership and failure paths
python3 test/rotator_build.py [BUILD...]    # dev-only guard (no build needed); given builds, packed/default module isolation
python3 test/playback.py /tmp/q2-build     # grouped traversal, snapshots and the wheel's ticks
python3 test/patch.py /tmp/q2-build        # the patched MIPS payload, wheel and UI scenarios
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
python3 test/headset_io.py /tmp/q2-build   # real MIPS headset worker with simulated kernel I/O
python3 test/bluealsa.py /tmp/q2-build     # the patched bluealsa's DROP paths against stock's, delayed encoder signal
python3 test/alsa.py BUILD QEMU_MIPSEL     # ALSA startup lock regression (see internals.md)
python3 test/ui_preview.py BEFORE AFTER OUT_DIR  # not a test: side-by-side iPod layout review sheets of two builds (or one build's two themes; --themes, ImageMagick)
python3 -m pytest test/unit                # the changed tools/ functions: no stock ZIP, no build, no Q2 (pip install -r test/unit/requirements.txt); GitHub runs it on every pull request
python3 -m pytest test/fw --basetemp ~/.cache/q2-pytest/fw  # the firmware's C on this machine: no stock ZIP, no build, no Q2 (see Firmware tests)
```

- **MIPS suite:** `test/patch.py` executes the actual patched payload and the stock key and touch filters under Unicorn. UI services are mocked; the one other mocked boundary is the `system()` call that Key Tone uses to click the speaker. Case coverage lives in `test/patch.py`. Run the payload, headset worker, metadata dispatch and packaging checks for both Stock and iPod builds.
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

- **Writing one:** `fw.fn('lyrics__lrc_time', restype, *argtypes)` reaches a static as `<file>__<name>`, and `fw.mock` and `fw.calls` take stub names: a stock function's, or `fw_<name>` for libc (`fw_fopen`). Work out the expected answer from what the function is for before running it.
- **A known bug** stays as `@pytest.mark.xfail(strict=True, reason='BUG: ...')`: the test fails the job as soon as the bug is fixed, until the mark goes.
- **Limits:** the code runs 64-bit, so `long` and pointers are wider than on the Q2, and stock widgets' layouts don't hold. Code that depends on those stays with `test/patch.py`. `peq_player.c`, `mp3_gapless.c` and the FLAC paths are the player's, tested by `test/peq.py`, `test/mp3_gapless.py` and the `test/flac_*.py` scripts.

## Mutation tests

`.github/workflows/tests.yml` runs `test/unit` on every pull request and push to `main`. Every Sunday, or by hand from the Actions tab after a substantial change to `tools/`, `.github/workflows/mutation.yml` runs [mutmut](https://github.com/boxed/mutmut) on the `tools/` functions changed since tag 1.2.0: the same mutants over unchanged code find nothing new, so it doesn't run nightly. mutmut breaks a copy of each function in one small way at a time and checks that `test/unit` notices. `test/unit/targets.py` lists the functions from git, so a new one joins by itself. The job fails under the score floor in the workflow, and uploads every surviving mutant as `surviving-mutants`. About 6,700 mutants take three and a half minutes on four workers.

```sh
pip install -r requirements.txt -r test/unit/requirements.txt
mutmut run $(python3 test/unit/targets.py)                  # needs clang, lld and llvm
python3 test/unit/report.py --survivors /tmp/survivors.txt  # score, table, each survivor's diff
```

- **Needs no stock firmware:** the fixtures in `test/unit` are synthetic, and the stock ZIP is neither fetched nor kept. Functions that pin a Shanling file's hash or link against one are in `NEEDS_STOCK` in `targets.py`: `build`, `compile_payload`, `patch_asset`, `compile_common`, `patch_player`, the audio module's `validate_stock` and `patch_module`, the rotator's `patch_module`, and PWM's `link`, `patch_pwm`, `patch_mcu` and `patch_bundle`. They stay with the scripts above, which need a build.
- **Keep tests behind the fence:** a mutant changes a command before it runs, and `q2remote`'s commands are shell scripts for a live Q2. `test/unit/conftest.py` refuses every process but the compilers, along with sockets and signals, and hides `Q2_HOST`. `test/remote.py` runs real commands through a real shell, so it stays out of `test/unit`.
- **`build()`** carries `# pragma: no mutate block`. mutmut writes out a whole copy of a function per mutant, so its 440 lines alone made a 96 MB file that never finished generating.
- **Survivors are not all gaps.** Most left are equivalent (`<I` as `<i` on a word under 2^31) or machine code in `patch_bluealsa`, which only `test/bluealsa.py` can judge against a build.

### C

mutmut mutates Python only. For the C (1,158 functions added or changed since 1.2.0, 394 of them in `navigation.c`), the way is a source-level mutator ([universalmutator](https://github.com/agroce/universalmutator), or a small one of our own) run on the same schedule, one file at a time, against tests that run in seconds:

1. `library_scope.c`, `mp3_gapless.c` and `chapters.c`, through their own scripts. `test/library_scope.py` takes half a second; the other two need ffmpeg.
2. `coverflow.c` and the `peq*` files, through `test/coverflow.py` and `test/peq.py`, which need the 32-bit libraries.
3. Everything `test/fw` reaches, `navigation.c` and `playback.c` among them: the whole suite takes about 20 seconds, and a mutant needs only the tests of its own file. `test/patch.py` takes about 6 minutes a run and needs a stock build, so it can't run per mutant in public CI.

Mutants of C run as real code, so they belong in a container with no network, as the fence above can't reach them.

## Coverage

Both workflows count what their stock-free runs reach and fail under a floor, and each run's summary has the table.

- **`tools/`:** `tests.yml` runs `test/unit` under [coverage.py](https://coverage.readthedocs.io), every branch counted, and fails under `fail_under` in `pyproject.toml`. The functions in `targets.py`'s `NEEDS_STOCK` and the command lines are left out, so the floor is all of the rest.
- **C:** `firmware.yml` runs `test/fw` and the host suites with `test/coverage/cc` first on `PATH`: `cc` with gcov counters, which keeps each compile's counts under `$Q2_COV` when a suite deletes its build folder. [gcovr](https://gcovr.com) reads them, fails under the line and branch floors in the workflow, and uploads the per-line pages as `c-coverage`. The ASan run isn't counted. Most of `navigation.c` is `test/patch.py`'s, which needs a stock build, so it stays low here.
- **Floors:** they catch a collapse (a suite or the wrapper that stopped counting), not each change, so they sit a little under the measured figures. Raise one by hand when the measured figure is well clear of it. The host suites run only counted in CI; `test/fw` also runs plain under ASan.

```sh
pip install -r requirements.txt -r test/unit/requirements.txt gcovr==8.4
coverage run -m pytest test/unit --basetemp ~/.cache/q2-pytest/unit && coverage report  # needs clang, lld and llvm
export Q2_COV=~/.cache/q2-cov; rm -rf $Q2_COV; mkdir -p $Q2_COV  # counts add up across runs: start empty
PATH=$PWD/test/coverage:$PATH python3 -m pytest test/fw --basetemp ~/.cache/q2-pytest/fw  # and the host suites, the same way
gcovr --root . --filter patch/ --gcov-ignore-errors=source_not_found --html-details ~/.cache/c-coverage.html $Q2_COV
```
