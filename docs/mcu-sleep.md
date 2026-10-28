# MCU idle sleep experiment

The cooperative PWM patch parks the buzzer MCU but leaves its core polling SRAM.
`--dev --mcu-idle-sleep` (with the default PWM gating) adds interruptible `WAIT` in that parked
state. It is off by default. PWM clocks remain governed by the existing paired
Linux/MCU ownership protocol. No CPU frequency, voltage, audio, DMA or clock-register
configuration changes are made by this option.

## Wake and publication

The paired firmware uses protocol version2 and a wake token at MCU0xf4007f10
(host0xb3429f10). Under the existing MCU mutex, the host acquires its PWM clocks,
publishes WAKE1 then REQ0, and asserts the existing software interrupt latch.
It requires both ACK0 and WAKE0 before writing a command. A coalesced interrupt
is retried while publication is excluded. Timeout retains clocks and rejects
further operations on this known running image until reboot.

The MCU wake-only handler clears the hardware source before clearing the token.
It does not invoke the buzzer callback. Normal interrupts retain the original
callback. REQ1 is the sole command-commit boundary for this image: a delayed
normal interrupt cannot dispatch a partial ring while REQ0.

An interrupt between the final REQ load and WAIT would otherwise be consumed
before the sleep instruction, leaving no pending wake. The IRQ return hook maps
EPCs within that five-instruction window back to its first load. This includes
the branch EPC reported for a delay-slot interrupt. Only original exception
scratch registers k0/k1 are used after restoring interrupted state.

WAIT requires IE1, EXL0, ERL0 and an unmasked software IRQ; other states poll.
Stock startup clears CP0 Status at0xf400001c, enables IE at0xf40014e8 and unmasks
logical IRQ36 (INTC4 bank1 bit1) before entering main. The guard still checks
runtime state on every pass.

## Hardware basis

The [Ingenic X2000 programming manual](https://whycan.com/files/members/3946/X2000_PM_20200515.pdf)
(SHA256563f3f72907eb04096b91a369890ab613b39c1b013be9f8f144a6fd2fe2ee4ce)
documents a MCU WAIT loop in23.7.8, printedp642; DMCS.SLEEP at physical0x13421030
bit31 in23.7.1,p638; CP0 exception state in23.7.6–7; and software interrupt
CPM_SFTINT at0x100000bc in17.1.2.25,p520. Host uncached aliases are used by the
paired driver. No assumption is made about wake with IE0.

## Validation

`test/mcu_sleep.py` executes the actual helper and stock exception save/restore:
six interrupt boundaries, four negative controls that reproduce the lost wake,
register preservation, normal/wake-only IRQs, masked IRQ fallback, stale FLAGs,
partial ring publication, exact stock-image rejection and deterministic output.
WAIT, IRQ arrival and C-dispatch selection are explicit emulator boundaries;
branch-delay injections supply branch EPC because Unicorn does not expose Cause.BD.

`test/mcu_sleep_host.py STOCKFS` executes the relocated linked host module at
three address placements. Its81 cases cover ordering, delayed/coalesced wake,
timeout fault handling, clock failures, lifecycle, zero writes and unknown-image
fallback. Kernel services and MCU acknowledgements are modeled. The inherited
Unicorn transfer-context workaround does not change linked instructions.

`test/pwm_build.py --sleep STOCKFS [BUILD ...]` validates pairing, guards,
determinism, module ABI, exports and package contents. The default MCU firmware
and module remain byte-identical to the prior PWM implementation.

Hardware sleep/wake and long-session validation are required before promoting
the option. Read-only DMCS.SLEEP provides a direct sleep witness. Tests must also
verify repeated buzzer commands, display/brightness, local FLAC and fault0.
There is no measured milliwatt or battery-runtime saving. Removing a continuously
polling MCU is a plausible additional saving; Linux CPU utilization cannot
quantify it because this is a separate core.

Install only a full paired firmware package through the normal TF updater with
a known-good rollback. The modules are permanent; do not hot-replace or unload
one member of the pair. Unknown firmware retains stock transport and clocks.
