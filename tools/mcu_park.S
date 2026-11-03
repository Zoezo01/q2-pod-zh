/*
 * Q2 pinned MCU idle-loop extension. Linked at 0xf4006c00.
 *
 * Host protocol (all addresses MCU view):
 *   0xf4007f00 REQ: host writes 1 to request park, 0 only after clocks are on.
 *   0xf4007f04 ACK: MCU writes 1 only while sticky-parked, 0 after IRQ restore.
 * Host serializes firmware writes/reset/boot/mailbox writes. Before reset release
 * it initializes REQ/ACK while reset is asserted. REQ publication follows all
 * command/ring/doorbell writes and a full memory barrier. An ACK timeout must
 * retain clocks; elapsed time never grants permission to gate.
 *
 * This replaces the original wait at 0xf4000f98. Only that existing idle boundary
 * can park: the synchronous buzzer/DMA stop/release has already returned. A queued
 * mailbox flag takes priority in the default polling protocol. Sleep protocol
 * requires REQ1 plus a nonempty ring to dispatch, ignoring stale mailbox flags.
 * After masking MCU PWM IRQ9 (controller bit6), recheck publication/ring state
 * with IRQs locally disabled. No PWM register accesses occur while parked.
 *
 * The mailbox IRQ remains operational while parked, but cannot release park.
 * The host must acquire all required clocks before writing REQ=0. Pending PWM
 * IRQs are preserved, then serviced only after that clock-on grant.
 */
.set noreorder
.set noat
.set mips32r2
.text
.align 2
.globl q2_pwm_idle
.type q2_pwm_idle, @function
q2_pwm_idle:
    addiu $sp, $sp, -32
    sw $t0, 0($sp)
    sw $t1, 4($sp)
    sw $t2, 8($sp)
    sw $t3, 12($sp)
    sw $t4, 16($sp)
    sw $t5, 20($sp)
    sw $t6, 24($sp)
    sw $t7, 28($sp)
    lui $t0, 0xf400
    ori $t1, $t0, 0x7f00
    lui $t2, 0x1000

.globl q2_pwm_idle_wait
q2_pwm_idle_wait:
#if !Q2_MCU_SLEEP
    lw $t3, 0x3f8c($t0)
    bne $t3, $zero, q2_pwm_command_ready
    nop
#endif
    /* Sleep protocol uses REQ=1 as the sole command publication boundary.
     * A delayed normal IRQ or a wake-only IRQ must never dispatch a partial
     * host write under REQ=0. Committed nonempty ring is sufficient below. */
    lw $t3, 0($t1)
    addiu $t6, $zero, 1
    bne $t3, $t6, q2_pwm_idle_wait
    nop
    /*
     * REQ=1 is published only after the complete command. Service a committed
     * nonempty ring even if its mailbox IRQ was delayed/coalesced. Never peek
     * for dispatch under REQ=0: the host may still be publishing the command.
     */
    sync
    lw $t6, 0x3ce8($t0)
    lw $t7, 0x3cec($t0)
    bne $t6, $t7, q2_pwm_command_ready
    nop

    /* Same CP0 Status IE bit used by exact stock init0x14e0..14e8. */
    mfc0 $t4, $12
    addiu $t6, $zero, -2
    and $t6, $t4, $t6
    mtc0 $t6, $12
    ehb
    lw $t6, 0x1038($t2)
    andi $t5, $t6, 0x40
    ori $t6, $t6, 0x40
    sw $t6, 0x1038($t2)
    lw $t6, 0x1038($t2)
    sync

    /* No current ISR survives into this main-loop critical section. */
    lw $t3, 0($t1)
    addiu $t6, $zero, 1
    bne $t3, $t6, q2_pwm_restore
    nop
#if !Q2_MCU_SLEEP
    lw $t3, 0x3f8c($t0)
    bne $t3, $zero, q2_pwm_restore
    nop
#endif
    lw $t6, 0x3ce8($t0)
    lw $t7, 0x3cec($t0)
    bne $t6, $t7, q2_pwm_restore
    nop

    /* Publish only after IRQ mask write/readback and empty-queue checks. */
    sync
    addiu $t3, $zero, 1
.globl q2_pwm_ack_park
q2_pwm_ack_park:
    sw $t3, 4($t1)
    sync
    mtc0 $t4, $12
    ehb

.globl q2_pwm_parked
q2_pwm_parked:
#if Q2_MCU_SLEEP
    /* WAIT is only used with normal IRQ acceptance enabled. Never depend on
     * undocumented wake behavior with IE=0, EXL=1 or ERL=1. IRQ_SOFT is INTC4
     * bank1 bit1; if masked, retain the old polling fallback. */
    mfc0 $t3, $12
    andi $t3, $t3, 7
    addiu $t6, $zero, 1
    bne $t3, $t6, q2_pwm_poll
    nop
    lw $t3, 0x1044($t2)
    andi $t3, $t3, 2
    bne $t3, $zero, q2_pwm_poll
    nop
.globl q2_pwm_sleep_check
q2_pwm_sleep_check:
    lw $t3, 0($t1)
    beq $t3, $zero, q2_pwm_unpark
    nop
    sync
    wait
.globl q2_pwm_sleep_end
q2_pwm_sleep_end:
    nop
    b q2_pwm_parked
    nop
q2_pwm_poll:
#endif
    lw $t3, 0($t1)
    bne $t3, $zero, q2_pwm_parked
    nop
q2_pwm_unpark:
    sync
    mfc0 $t4, $12
    addiu $t6, $zero, -2
    and $t6, $t4, $t6
    mtc0 $t6, $12
    ehb

.globl q2_pwm_restore
q2_pwm_restore:
    /* Restore just the owned mask bit, preserving every other IRQ mask. */
    lw $t6, 0x1038($t2)
    addiu $t7, $zero, -65
    and $t6, $t6, $t7
    or $t6, $t6, $t5
    sw $t6, 0x1038($t2)
    lw $t6, 0x1038($t2)
    sync
    mtc0 $t4, $12
    ehb
.globl q2_pwm_ack_run
q2_pwm_ack_run:
    sw $zero, 4($t1)
    sync
    b q2_pwm_idle_wait
    nop

.globl q2_pwm_command_ready
q2_pwm_command_ready:
    lw $t0, 0($sp)
    lw $t1, 4($sp)
    lw $t2, 8($sp)
    lw $t3, 12($sp)
    lw $t4, 16($sp)
    lw $t5, 20($sp)
    lw $t6, 24($sp)
    lw $t7, 28($sp)
    j 0xf4000fa0
    addiu $sp, $sp, 32
.size q2_pwm_idle, .-q2_pwm_idle

#if Q2_MCU_SLEEP
/* Replaces only the stock IRQ epilogue's EPC restore. The hook's delay slot
 * loads saved EPC into k1 after all interrupted GPR/HI/LO are restored. k0/k1
 * are the stock exception scratch registers. Redirect the whole narrow range,
 * including a branch EPC when Cause.BD is set, to a fresh REQ read. This closes
 * the IRQ-after-check-before-WAIT race without assuming masked IRQ wake. */
.globl q2_pwm_irq_return
.type q2_pwm_irq_return, @function
q2_pwm_irq_return:
    lui $k0, %hi(q2_pwm_sleep_check)
    addiu $k0, $k0, %lo(q2_pwm_sleep_check)
    subu $k0, $k1, $k0
    sltiu $k0, $k0, 20 /* pinned five-instruction check/WAIT window */
    beq $k0, $zero, 1f
    nop
    lui $k1, %hi(q2_pwm_sleep_check)
    addiu $k1, $k1, %lo(q2_pwm_sleep_check)
1:
    mtc0 $k1, $14
    ehb
    eret
    nop
.size q2_pwm_irq_return, .-q2_pwm_irq_return

/* A wake-only software interrupt is not a buzzer command. Clear its hardware
 * source before acknowledging the token, with no later stock-wrapper clear
 * that could erase a newly published command's interrupt. Normal software IRQs
 * replay the two displaced instructions and retain the exact stock callback. */
.globl q2_pwm_soft_irq
.type q2_pwm_soft_irq, @function
q2_pwm_soft_irq:
    lui $v0, 0xf400
    lw $v1, 0x7f10($v0)
    beq $v1, $zero, 2f
    nop
    lui $v1, 0x1000
    sw $zero, 0xbc($v1)
    sync
    sw $zero, 0x7f10($v0)
    sync
    jr $ra
    nop
2:
    lw $t9, 0x3f90($v0)
    j 0xf40011e8
    nop
.size q2_pwm_soft_irq, .-q2_pwm_soft_irq
#endif
