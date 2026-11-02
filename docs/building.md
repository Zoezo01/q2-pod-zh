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

On by default. The audited audio driver's stopped-state 10 ms polling is replaced by an
event wait, woken by start/resume and teardown. Active PCM notification timing, period and
buffer limits are unchanged. `--no-aic-idle-wait` keeps the stock module. See
[audio-notifier.md](audio-notifier.md) for the exact scope.

```sh
python3 tools/build.py 'Q2 Firmware V1.32.zip' --out /tmp/q2-stock-audio --no-aic-idle-wait
```

### FLAC fast paths

On by default: `--flac-experiments sync,lpc,output` (all three). Pass a subset to build only some, or `--flac-experiments ""` for the stock decoder paths. Each is checked against the pinned V1.32 player.

### Versioning

The updater refuses only a firmware whose version literal is identical to the installed one (`update_firmware`, `0x4f8440`; the online check `check_otginfo`, `0x4f8968`). So each build carries a 5-character tag, `VERSION` (`x.y.z`, single digits) packed as `V<xyz>I`. For example, 1.0.0 is `V100I`. The mod installs over stock and over any other build, and stock V1.32 installs back over it.

About's **FW. Version** row reads the same literal, so it shows the stock firmware's version (`STOCK_VERSION`, from the stock `firmware_v20.info`). A **CFW. Version** row below it shows `<version> Pod`, plus ` dev N` in `--dev` builds. Release tags use the plain version (`1.0.0`); releases up to 1.0 were `V`-prefixed. Each release carries `update.tar`.

`--dev` numbers each test build from the untracked `.build-number` and tags it `V` plus that number in three base-36 digits, then `i` (`V001i`, `V002i` … `VZZZi`). No two test builds share a tag, and none equals a release's, so the updater installs each test build over the last and the release over any of them. `--dev` applies to that build only: the release procedure never passes it, and the manifest records the build number as `dev` (`false` otherwise).

`--update-repo OWNER/NAME` points **Network Update** at another GitHub repo's latest release, so a fork's test builds update from the fork's own releases. It defaults to `DiamondBond/q2-pod`, so a build without it is unchanged. The manifest records it as `update_repo`, and `tools/release.py` refuses a build that points anywhere else.

Development builds also start `q2remote`, a key-gated root shell with screenshots and input over Wi-Fi, for developers and agents; see [remote.md](remote.md).

On by default: the rotator clock is held only from its first conversion (`--no-rotator-lazy-clock` keeps the stock module byte for byte). See [clock gating](clock-gating.md) for the exact module guard and tests.

On by default: `--pwm-idle-clock`, the paired PWM/MCU clock gating. It needs all three matching files and a reboot (`--no-pwm-idle-clock` for stock). See [PWM clock gating](pwm-clock-gating.md) for its ownership protocol and fallbacks.

`--dev --mcu-idle-sleep` is off in release builds. It adds an interruptible MCU sleep with an explicit wake handshake. See [MCU sleep](mcu-sleep.md).

## Tests

```sh
python3 test/peq.py                        # PEQ parser, storage, DSP, EQ page (both tabs), 3EQ filters, player, visualizer FFT and scrobble upload (host cc; player needs -m32 libs, upload libcrypto)
python3 test/peq.py --captures /tmp/eq --font BUILD/default.ttf  # the same, plus the EQ page's Simple tab in both iPod themes as PNGs
python3 test/coverflow.py                  # Coverflow art cache and depth renderer (host cc -m32, pthreads)
python3 test/coverflow.py --captures /tmp/cf  # the same, plus the renderer's frames as PNGs
python3 test/mp3.py                        # native Xing seek bounds, CRC and truncated headers; TXXX encodings
python3 test/mp3_gapless.py                # gapless parser/PCM and generated LAME/FFmpeg comparisons (cc, lame, ffmpeg, pkg-config, libavcodec/libavutil/libmad development files)
python3 test/build.py                      # JPEG header checks; no emulator required
python3 test/build.py 'Q2 Firmware V1.32.zip'   # packaging checks (custom logo, quoted paths)
python3 test/aic.py STOCKFS               # stopped audio notifier: actual MIPS hooks and modeled wait races
python3 test/aic_build.py STOCKFS BUILD    # notifier development guard, ABI, default/experimental packaging
python3 test/remote.py                     # q2remote protocol, auth, event and framebuffer checks
python3 test/rotator.py STOCK_MODULE        # actual MIPS rotator clock ownership and failure paths
python3 test/rotator_build.py BUILD...      # dev-only guard and packed/default module isolation
python3 test/playback.py /tmp/q2-build     # grouped traversal, snapshots and the wheel's ticks
python3 test/patch.py /tmp/q2-build        # the patched MIPS payload, wheel and UI scenarios
python3 test/chapters.py                   # embedded chapter parsing, both MP4 formats and async extraction
python3 test/m4b.py /tmp/q2-build          # native TagLib M4B/MP4 metadata dispatch
python3 test/id3.py /tmp/q2-build          # the built player's MP3 ReplayGain on ID3v2.3/2.4 tags
python3 test/hci_wait.py /tmp/q2-build     # the built player's futex waits for Play and while paused
python3 test/bt_resample.py /tmp/q2-build  # the built player's Bluetooth resampler across a gapless change
python3 test/xfade_player.py /tmp/q2-build  # the built player's crossfade: the hold, the mix at the same and another rate, the drops
python3 test/mp3_gapless_player.py /tmp/q2-build  # built MIPS gapless hooks, preload isolation, seeks and cleanup
python3 test/ldac.py /tmp/q2-build         # shipped LDAC intermediate rates and native adaptive controller
python3 test/headset_io.py /tmp/q2-build   # real MIPS headset worker with simulated kernel I/O
python3 test/bluealsa.py /tmp/q2-build     # the patched bluealsa's DROP paths against stock's, delayed encoder signal
python3 test/alsa.py BUILD QEMU_MIPSEL     # ALSA startup lock regression (see internals.md)
python3 test/ui_preview.py BEFORE AFTER OUT_DIR  # not a test: side-by-side iPod layout review sheets of two builds (or one build's two themes; --themes, ImageMagick)
```

- **MIPS suite:** `test/patch.py` executes the actual patched payload and the stock key and touch filters under Unicorn. UI services are mocked; the one other mocked boundary is the `system()` call that Key Tone uses to click the speaker. Case coverage lives in `test/patch.py`. Run the payload, headset worker, metadata dispatch and packaging checks for both Stock and iPod builds.
- **Known gap:** the stock rounded vgcanvas branch can't run end to end under Unicorn 2.1.4. Stock is built `-mfp64`, and Unicorn's MIPS32 FPU only implements `FR=0`, so its 64-bit conversions trap. The test runs the branch up to that instruction and asserts the calls before it.
- **Reproducibility:** two fresh builds must produce identical `update.tar` files. Packaging verifies MD5 entries, unchanged kernel and rootfs metadata, and a rootfs no larger than stock.
- **Space:** the payload is built with `-Oz`, and demo's `.pdr` section (MIPS procedure descriptors, 423 KB past every LOAD segment, never read at run time) ships zeroed. mksquashfs packs file tails into fragments (`-tailends`), which saves about 26 KB. For rootfs headroom see [Boot](boot.md#making-a-logo-that-works).
- **Manifest checks:** the runner refuses a `manifest.json` whose `source_sha256` doesn't match the current patch sources, and verifies the stock, patched and payload hashes against it.
- **Guards:** the builder rejects any `patch/contexts.inc` name that isn't a window name in the stock rootfs UI assets, so an allowlist typo can't silently disable a screen. Widget offsets and shared ABI constants live in `patch/offsets.inc`, which both the payload and the test mocks read.
- **Limits:** instruction counts are mocked-service measurements, not hardware latency.
