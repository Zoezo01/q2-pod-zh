# Rotator clock experiment

The build defers `gate_rot` until the first valid conversion (on by default;
`--no-rotator-lazy-clock` keeps the stock module). It is independent of FLAC
decoding, CPU frequency, and audio output.

```sh
python3 tools/build.py 'Q2 Firmware V1.32.zip' --out /tmp/q2-rotator --dev --rotator-lazy-clock
python3 test/rotator.py /path/to/stock/soc_rotator.ko
```

The manifest records `rotator_clock`, the policy and both module hashes.

## Opportunity and limits

On the inspected Q2 boot, CCF reported `gate_rot` enabled/prepared once at
300 MHz with the screen off. No rotator interrupt entry was registered. The stock
boot script sets `is_rotated=0 rotator_angle=0`; `soc_fb` still depends on the
rotator module. This supports testing whether an unused block is held clocked.
It does not establish the block's watts or the energy saved by removing its reference.

If no conversion occurs, the new policy removes the driver's clock reference for
the whole session. After one valid conversion, it saves nothing for the remainder
of that boot. For a session lasting T with its first conversion at t, the potential
clock-off fraction is t/T (100% if never used). Battery improvement is that fraction
multiplied by the **measured** difference in system power, not by the 300 MHz rate.
Clock framework counts are software accounting; zero counts alone are not proof
that every downstream transistor or shared parent is powered off.

## Exact implementation

The supported input is the stock V1.32 module with SHA-256
`a341847285bc2e204f81be19d6cd942b0265275fba9c13ff8257a9f2574f2dc0`.
It is MIPS32 little-endian ELF32, with vermagic
`5.10.21+ SMP preempt mod_unload MIPS32_R5 32BIT `.
Unknown and already patched modules are rejected before writing output.

`tools/rotator.py` preserves section/symbol indices, relocations, kernel ABI,
exported functions and all stock conversion/interrupt code except the three small
hook windows. It appends executable helpers to `.text`, adds one zero-initialized
word after the entire original `.bss`, and assigns a new deterministic build ID.
It does not spoof vermagic or compile against a different kernel's headers.

- Probe still obtains the clock handle, registers the device, and allocates its
  descriptor. It skips prepare/enable.
- Invalid requests still return before taking the mutex or touching the clock.
- A valid request acquires the clock under the existing conversion mutex, before
  descriptor writes, MMIO or IRQ registration. Prepare failure returns its errno;
  enable failure unprepares once and returns its errno. Both unlock and leave the
  clock unowned so a later request can retry.
- Success retains the clock through completion, timeout, interruption, subsequent
  requests and all late/shared IRQ entries.
- Remove disables/unprepares only an acquired clock and clears the private flag;
  it always puts a valid handle. Original unload ordering is otherwise unchanged.
  Forced unload, sysfs unbind/rebind, and concurrent removal are outside the tested
  lifecycle; the stock driver has existing descriptor/IRQ ordering and rebind issues.

The three hooks are `.text+0xcc` (acquire), `+0x578` (conditional release), and
`+0x734` (skip probe acquisition). The displaced `memset` HI/LO pair is rebuilt in
full in the acquire helper, so no relocation depends on a cross-helper HI/LO pair.
The error path uses the original canary check and register/stack restoration.

## Why retain the clock after first use?

The actual IRQ handler wakes the conversion waiter and then performs three more
MMIO accesses. Even an IRQ with no completion status performs MMIO. Gating as soon
as the waiter wakes can race that handler; masking/synchronizing the current IRQ
alone does not prevent a later shared IRQ entry. Timeout also needs proven DMA
quiescence. This patch avoids those transitions entirely. A future per-conversion
policy needs engine-idle/reset proof and an IRQ lifetime that excludes every MMIO
access before releasing the clock.

The recovered [X2000 driver family](https://github.com/SwordofMorning/Ingenic_X2000/blob/5e7f32b2e08e1a15f77acb20137356eff3d1c2e3/vendor/module_driver/soc/x2000_510/rotator/rotator.c)
corroborates the binary's lifecycle. The available
[Q2 kernel SDK](https://github.com/DiamondBond/q2-kernel/blob/655f42c032402719881ff2a1b801bd797dc514c1/stock/README.md)
is explicitly not the exact Q2 build. Both recovered source trees use 5.10.186;
this module runs on 5.10.21+. Neither is treated as a compatible replacement build.

## Validation and measurement

`test/rotator.py` relocates and executes the actual stock and patched MIPS module
with an independent ELF loader and mocked kernel services (84 lifecycle cases across
three load addresses, plus two branch-delay controls). It single-steps real
instructions and their delay slots, rebuilding the CPU context after completed
transfers to avoid Unicorn branch-state artifacts. Those tests check software
control flow and reference ownership; they cannot prove silicon behavior, DMA
quiescence, kernel loading, or power savings.

For a device check, use the normal TF updater with a verified known-good rollback
package. Do not force clock registers, unload `soc_rotator` underneath `soc_fb`, or
load a second copy alongside it. Read snapshots before/after boot, screen wake,
local playback, and any feature that might rotate an image:

```sh
# Using the existing authenticated device connection:
python3 - <<'PY'
import pathlib, sys
sys.path.insert(0, 'tools')
import q2remote
output, status = q2remote.run(pathlib.Path('tools/clock_snapshot.sh').read_text())
print(output.decode())
raise SystemExit(status)
PY
```

The snapshot script temporarily mounts debugfs, reads counters, and unmounts it.
It never writes a clock control. Verify the module hash, boot ID, kernel identity,
UI/playback behavior, clock counts and IRQ registration. Keep prior FLAC/Smart Wi-Fi
changes in any comparison firmware so they do not confound the result.

Measure watts with matched A-B-B-A runs: same file, volume, output, screen state,
radio policy and temperature, with charging excluded and no remote polling inside
the measurement windows. Until that measurement, no mW or battery-percentage claim
is justified. The most useful next step is proving unused-session gating first;
only pursue per-conversion gating if real workloads actually use this block.

## Device check, 2026-10-25

Installed development V02Yi with this module layered onto the previously tested
Smart Wi-Fi/FLAC firmware. The player, remote helper and kernel image stayed byte
identical. The normal TF updater completed, installed hashes matched, the boot ID
changed, and the loaded module's GNU build ID matched the patched file:
`bb773a7f6bfe17a818595215742bb0a3a8add9d2`.
Patched module SHA-256:
`d310e7d7dffccea49209a017bd7983a0e63f5b6fc00c7a32a98265901c2d39b6`.

| Observed state                                     | gate_rot enable/prepare | div_ahb0 enable/prepare |
| -------------------------------------------------- | ----------------------- | ----------------------- |
| Stock module, screen off                           | 1 / 1                   | 1 / 1                   |
| Patched module, local FLAC, screen on              | 0 / 0                   | 1 / 1                   |
| Patched module, local FLAC, screen off             | 0 / 0                   | 0 / 0                   |
| Patched module, after wake, track change and pause | 0 / 0                   | 1 / 1                   |

Both clocks still report a nominal 300 MHz when their reference counts are zero;
that field is a configured rate, not measured switching activity. The display
continues to take its own references when it wakes. No rotator IRQ registered in
this tested boot, and the player/UI processes remained stable with no observed
kernel Oops. These checks establish normal unused-session behavior and real module
loading. They do **not** exercise the first hardware conversion; that path remains
covered by instruction tests only. Longer use and less common consumers remain
unverified.

This kernel did not expose the optional `gate_rot/hwdesc` register description.
The snapshots therefore establish software clock ownership, not a direct physical
gate measurement. No system-power or battery-runtime measurement was made.

Final software checks: 84 module cases plus 2 branch-delay controls; 1,837 iPod
payload scenarios; opt-in/default module packaging; asset
validation; two byte-identical default packages. The iPod asset suite still flags
`scrsaver_playcover.png` corner alpha: that exact image is byte-identical to the
upstream baseline, so it is an existing unrelated failure.
