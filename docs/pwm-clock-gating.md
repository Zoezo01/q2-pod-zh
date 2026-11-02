# Cooperative PWM clock gating

The build replaces the exact V1.32 PWM module, MCU module and MCU firmware as one
set (on by default; `--no-pwm-idle-clock` keeps all three stock files). The boot script loads PWM before MCU, as stock already does.

This document describes the polling park protocol. The optional additional
`--mcu-idle-sleep` uses a version2 protocol and two IRQ hooks; see
[MCU sleep](mcu-sleep.md) for its wake, timeout and validation behavior.

```sh
export PATH=/usr/lib/llvm-18/bin:$PATH
python3 tools/build.py 'Q2 Firmware V1.32.zip' --out /tmp/q2-pwm --dev --pwm-idle-clock
python3 test/pwm.py /path/to/stockfs
python3 test/pwm_mcu.py /path/to/stockfs
python3 test/mcu_park.py /path/to/stockfs/usr/data/libmcu-bare.bin
python3 test/pwm_build.py /path/to/stockfs /tmp/q2-pwm
```

LLVM 18 and Unicorn 2.1 with the virtual TLB interface are used for these checks.
The rotator experiment is separately selectable.
The package manifest records the policy, three input/output hashes and MCU layout.

## Why three files?

The Linux PWM driver keeps its mux, divider and gate clocks enabled after all
channels stop. However, the buzzer MCU also accesses that same block directly;
it does not use the Linux PWM API. Its looping DMA and independent PWM interrupt
make Linux's channel state insufficient evidence that the block is idle.

The new drivers own independent, balanced clock references. Linux releases its
references only after every public call has finished, all 16 channel enabled
flags are clear, its IRQ has been disabled and drained, and hardware activity
checks do not veto release. Every public entry reacquires clocks before MMIO and
IRQ reenable. Entering any Linux PWM DMA API permanently retains Linux's clocks
for that boot; its asynchronous completion is not assumed to be safe to gate.

The MCU firmware gains a small helper at its existing main-loop idle boundary,
after the synchronous buzzer command has stopped and released PWM/DMA. It drains
published commands, masks its PWM IRQ and acknowledges a sticky park request.
Linux waits for that acknowledgement before dropping the MCU's clock references.
On wake, Linux acquires clocks before releasing park and waits for the MCU to
restore its interrupt mask. The mailbox interrupt remains operational while
parked. There is no new MCU WAIT instruction or change to buzzer samples/timing.

A one-way readiness export keeps the Linux bootstrap lease held until the MCU
driver has successfully acquired all three clocks and initialized. The MCU module
imports that export, so it requires the paired PWM module. The two modules are
permanent for the boot: ordinary removal is disabled because an unknown or timed
out MCU cannot provide reliable DMA quiescence. Use the normal updater and reboot;
forced unload, driver unbind and manual mixed-module loading are unsupported.

## Failure behavior

An acquisition failure unwinds only new references and returns the actual clock
API error; subsequent calls can retry. A park/wake timeout never permits clock
off. Unknown firmware or an already running MCU discovered at module initialization
retains the MCU clocks until reboot. Unsupported stock inputs and already patched
files are rejected by the build's exact SHA-256 checks.

Firmware upload, boot, reset, shutdown and command publication serialize under a
new MCU mutex. Raw uploads stop MCU execution before writing SRAM and invalidate
its trusted-image state. Before a newly uploaded image may park, its initial data,
code, helper and protocol identity must match the paired image. Subsequent resets
check immutable ranges, allowing the already validated image's data to change.
REQ/ACK are initialized under reset, preventing reuse of an old park acknowledgement.
The stock 32 KiB upload bound remains intact; the expanded image is 32,528 bytes.

Unexpected hardware activity keeps clocks on. If a buzzer overlaps the last Linux
channel stopping, Linux may conservatively retain its references until the next
PWM call. This loses some potential savings but does not release another owner's
clock. A failed MCU load also leaves the Linux bootstrap lease held.

## Scope and verification limits

The patch preserves original PWM channel/configuration/IRQ instructions, module
vermagic and existing exports. Small compiled MIPS helpers link into the pinned
modules; they are not replacements built against different kernel headers.
The MCU hook preserves all stock bytes except two idle-loop instructions and
uses unused SRAM above the original stack. Neither helper changes PLL, voltage,
CPU/DDR frequency, DAC routing, volume or audio PCM.

The instruction tests execute the actual relocated helpers and stock PWM paths,
with modeled kernel services and MCU acknowledgement delivery. A separate suite
executes the actual MCU assembly across interrupt/queue/park boundaries. The Linux
suites execute original delay slots and restore integer CPU context after completed
transfers to avoid an independently reproduced Unicorn branch-chaining defect;
they do not rewrite helper instructions. The MCU assembly suite runs continuously. These
check software ownership and failure behavior, not physical clock/DMA timing.
The exact clock provider preserves hardware divider/parent fields on enable, but
its wrapper discards an internal busy timeout; a successful clock API return is
not proof of every silicon transition.

A stock GPIO-allocation failure path unlocks the wrong channel mutex. The PWM
tests reproduce that defect in stock and patched images as a negative control;
this clock experiment does not repair it or claim clean GPIO-failure recovery.

Confirm the loaded build IDs, three installed hashes, repeated display sleep/wake,
brightness transitions and real buzzer commands on hardware. Keep the previous
firmware package for rollback. Clock framework reference counts are software
accounting, not measured watts. No battery-life or milliwatt benefit follows from
the configured 300 MHz rate; it requires matched power measurements.

## Kernel relocation validation

External kernel calls use full-address JALR sequences (`-mlong-calls`). A MIPS
J/JAL only encodes a target in the caller's 256 MiB region, while this kernel
allocates modules at c... and kernel services at 8.... The MCU init hook uses a
same-init-section veneer with a full-address jump to core text, because init and
core allocations can also occupy different regions. No final R_MIPS_26 points
to an import or another section.

The independent loader checks c... core, d... init and 8... imports. Negative
controls reject both unencodable jumps and the earlier rejected module; positive
controls execute the init veneer in three split layouts. The final MCU module
passes 111 semantic cases plus three real init-entry checks; PWM passes 16 cases;
the MCU assembly passes 13 methods. Both final modules also loaded successfully
on the device before the final firmware update. An initial development package
was rejected by the real kernel, which prompted these full-address fixes and
regression checks; it is superseded and must not be installed.

## Device check: 2026-10-25

V034i (development build 112) was installed through the stock TF updater with
USB power. This device image combines the clock experiments with the separate
Smart Wi-Fi/FLAC work; the clock-gating PR itself remains independent. The kernel,
player and remote helper remained byte-identical to the prior installed image.
All three installed PWM/MCU file hashes and both loaded module build IDs matched
the candidate. The final boot loaded both modules without relocation errors.

During local FLAC playback, five settled display wake/sleep cycles restored and
stopped the PWM clocks correctly. Brightness decreased, increased and returned to
the prior setting. Ten stock `cmd_mcu write_str buzzer` commands each returned the
expected seven-byte count, increased both MCU wake and park counts by ten, and
left `q2_mcu_fault` and `q2_pwm_errors` zero. This validates command completion and
clock ownership; acoustic output was not independently measured.

With the display off, `mux_pwm`, `div_pwm` and `gate_pwm` each reported zero enable
and prepare references (previously 2/2, 2/2 and 1/1). Read-only physical register
checks independently confirmed the gate and divider state:

| State         | CPM_CLKGR1 at 0x10000028 | PWMCDR at 0x1000006c          |
| ------------- | ------------------------ | ----------------------------- |
| Display awake | 0x11bcfddf: bit 5 clear  | 0x40000004: stop bit 27 clear |
| Display off   | 0x31bcfdff: bit 5 set    | 0x48000004: stop bit 27 set   |

The masks come from the exact kernel clock descriptors and agree with the stock
MCU clock code. PWMCDR busy/change-enable bits were clear in every settled sample.
The UI's backlight flag changes before the physical wake sequence completes;
transition checks allowed 1.5 seconds for normal display initialization. No raw
clock-register writes were used. Local FLAC continued across track changes, and
the device was left paused at volume 44 with the original brightness restored.

The installed files were:

| File            | SHA-256                                                          |
| --------------- | ---------------------------------------------------------------- |
| soc_pwm.ko      | cbc82d18824f9a6b259735e3628cc57cb90e7528c7ea962cbdd3d5c1576b76c7 |
| soc_mcu.ko      | 5edc4d35931eb0b275350149b872343082ee2bb50023d07fe8da57f5eaf87983 |
| libmcu-bare.bin | 76284a0a7327f9af69e0eae70290e9afed728dba38385cb29488f81ec1c9f68c |

Loaded build IDs: PWM `2bbd27190cbc686e8c9bdfd9f5d313f7ba432dff`, MCU
`52fc5972890555516ceeeb244b233f9a3daf5a14`.

The final combined iPod payload passed 1,837 MIPS execution scenarios. Packaging
checks passed for the experimental build and an unchanged default build;
the shared loader also passed the existing 84 rotator cases and two delay-slot
controls. No electrical power
or battery-runtime improvement has been measured, and these checks do not cover
every peripheral workload or long-duration suspend/resume combination.
