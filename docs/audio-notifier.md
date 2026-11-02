# Stopped audio notifier: experimental event wait

A configured Q2 PCM stream keeps `aic_dma_notice` polling every 10 ms after pause.
The build puts that stopped notifier on its existing wait queue until start/resume or
teardown. It is on by default; `--no-aic-idle-wait` keeps the stock module.

This is a driver experiment for the audited V1.32 firmware. It does not alter
active notification intervals, PCM period/buffer constraints, DMA descriptors,
decoder code, sample data, volume, output routing, or peripheral clocks. The
kernel and all modules except `module_driver/soc_aic.ko` remain unchanged.
It is independent of the Smart Wi-Fi/FLAC and PWM/rotator PRs.

## Why this change

Read-only Q2 captures on the previously installed firmware measured:

| Condition                          | Duration | Notifier voluntary switches | Scheduled CPU time |
| ---------------------------------- | -------: | --------------------------: | -----------------: |
| Playing local 44.1 kHz stereo FLAC | 90.049 s |            4,732 (~52.55/s) |          116.28 ms |
| Paused, screen off                 | 30.053 s |            3,001 (~99.86/s) |           24.72 ms |

The paused PCM pointers stayed unchanged and TF block I/O was zero. It used
approximately 0.082% of one CPU, so the absolute CPU cost is small. Context
switches are not a hardware wakeup count and do not measure energy. These
are **baseline observations**, not a measured result from this patch. No battery
runtime or watt improvement is established.

The active stream negotiated S16_LE stereo at 44,100 Hz, period 848 frames and
buffer 22,048 frames (about 500 ms). Stock truncates that period to a 19 ms sleep.
This change preserves that active behavior; high-rate PCM sizing remains a
separate investigation.

## Exact patch and wake protocol

The patcher accepts only stock `soc_aic.ko` SHA-256:

```text
97b141982aa39a0551aa0505cbf041ab4001d4a136bc3b5637ca11a1292d591c
```

It additionally checks section layout, original instructions and relocations.
The existing per-stream state has exit at +0x28, exit acknowledgement at +0x2c,
running at +0x30 and its initialized wait queue at +0x34.

Three hooks in the module's original .text are changed:

| Offset | Change                                                                               |
| ------ | ------------------------------------------------------------------------------------ |
| 0x183c | Replace only the stopped 10 ms sleep with `q2_aic_wait_stopped`                      |
| 0x21dc | Preserve the running=1 store, then wake the queue                                    |
| 0x1ff0 | Preserve the exit=1 store, then wake the queue before the stock acknowledgement wait |

Trigger commands 1, 4 and 6 share the start/resume path; 0, 3 and 5 retain the
original stop path. Invalid commands retain their original error return.

The waiter initializes a nonexclusive 20-byte entry, registers it and sets
`TASK_IDLE` (0x402), then rereads exit or running. If neither is set, it calls
`schedule_timeout(LONG_MAX)`, which schedules without a timer in the Q2 kernel.
It repeats the condition check after every wake and calls `finish_wait` before
returning to stock code. This enqueue-before-check ordering handles a producer
before enqueue, during condition testing, or immediately before scheduling.
An unrelated wake only causes another condition check.

Each producer publishes its original store before an explicit MIPS `sync` and
`__wake_up(queue, 3, 0, NULL)`. The idle state is wakeable through its
uninterruptible bit but excluded from load accounting. The notifier removes
its wait entry before returning to active `usleep_range`, so start wakes do
not shorten the active PCM timer. Teardown retains stock acknowledgement
and resource-release behavior.

All helper functions are already imported by the original module. The
20-byte entry matches stock hw_free's stack layout. Retained kernel disassembly
with the device's 5.10.21+ #251 build banner confirms the LONG_MAX path,
TASK_IDLE value and exported wait functions. This follows the documented
[Linux 5.10 wait-queue protocol](https://docs.kernel.org/5.10/driver-api/basics.html#c.wait_event_idle).

Helper calls use full addresses and JALR; local hook jumps stay in the same
.text section. The patcher rejects nonlocal R_MIPS_26 jumps, keeps the module
ABI/dependencies and unload entry, and emits a deterministic, distinct build ID.
The manifest records input/output module hashes and policy
`stopped-event-wait-v1`. Normal builds keep the stock module.

## Validation

```sh
python3 test/aic.py /path/to/stockfs
python3 test/aic.py /path/to/stockfs --module /path/to/experimental-build/soc_aic.ko
python3 test/aic_build.py /path/to/stockfs /path/to/experimental-build /path/to/default-build
```

The instruction suite passes 97 cases across three module/kernel address placements.
It executes the relocated MIPS hooks and stock continuations
with modeled kernel calls and wait-queue scheduling. It exercises wake ordering,
spurious wakes, active pacing, start/stop commands and teardown. Kernel services
are modeled; this is not proof of live kernel scheduling or audio quality.
An independent valid-instruction control reproduces a Unicorn MIPS branch-chaining
exception. The runner restores integer CPU context after completed control transfers
to avoid that emulator defect; it does not rewrite the tested instructions.

Packaging checks cover the development guard, deterministic module output,
original ABI and resolved data/string targets (with six corruption controls),
expected module/default selection, unchanged kernel/other
clock modules, rootfs size and update archive checksums.

## Remaining validation and inherited limits

The notifier wait has **not been measured on hardware for power**. Verify the installed module hash/build ID,
quiet paused notifier activity, resume latency and PCM progress, repeated
pause/resume, seek, stop/close/reopen, track/sample-rate transitions, suspend/
resume, and shutdown. Include local high-rate tracks and a longer mixed session.
Use the normal full firmware updater and a known-good rollback package.

Stock hw_free gives its notifier at most 100 jiffies to acknowledge exit, then
proceeds even without acknowledgement. Stock thread-creation failure also
returns success. This focused patch retains those behaviors and does not claim
to repair all driver lifetime failures. The new exit wake prevents the new
indefinite stopped wait from relying on that timeout.

Keep draft status until hardware validation. Current-meter or discharge testing
is needed to establish battery savings.
