/*
 * bluealsa: keep the A2DP stream going with silence through a skip (docs/internals.md#bluetooth-aac).
 * tools/build.py (bt_silence) links this at BT_SILENCE, with BUDGET defined, and jumps here from
 * io_poll_and_read_pcm (0x4145d0): s0 its result, s1 the PCM, s2 the io state (frames 0x1c,
 * poll timeout 0x30, -1 or Drain's 100), s5 samples wanted, s6 the buffer, fp -1.
 *
 * A skip: hciplayer sends Drain (its last PCM drains, then Drain's 100 ms poll times out), or demo
 * pauses it first and it sends PAUSE, which the build points at Drain's case. Stock then sends
 * nothing until the next song's PCM, ~300 ms on, and the headset's buffer runs dry. Here that
 * 100 ms timeout returns silence, and later polls don't wait, so the encoder's
 * own pacing sets the rate (catching up the 100 ms in a burst) until real PCM comes back, or
 * BUDGET samples of silence have gone since the last real PCM.
 * ponytail: one quiet/silent for every encoder thread, fine for one headset at a time; a second
 * stream, or a stream torn down mid-silence, shares or inherits up to BUDGET of silence.
 */
.set noreorder
.set noat
.text

/* 0x41465c, before poll (its delay slot sets a1): while filling, poll without waiting. */
.globl timeout
timeout:
    lw    $6, 0x30($18)
    addiu $3, $zero, -1
    bne   $6, $3, 1f          /* Drain's wait: as stock */
    lui   $8, %hi(silent)
    lw    $9, %lo(silent)($8)
    beqz  $9, 1f
    nop
    lw    $9, %lo(quiet)($8)
    movn  $6, $zero, $9       /* budget left: timeout 0 */
1:  j     0x414664
    nop

/* 0x414684, poll timed out (its delay slot clears a1). */
.globl fill
fill:
    jal   0x430ac0            /* pthread_setcancelstate(DISABLE), as stock */
    addiu $4, $zero, 1
    lw    $3, 0x30($18)
    addiu $8, $zero, -1
    beq   $3, $8, 2f          /* our timeout 0 */
    nop
    jal   0x42ff70            /* Drain's: wake its waiter, block again, as stock */
    addiu $4, $17, 0x78
    sw    $30, 0x30($18)
2:  lui   $8, %hi(quiet)
    lw    $9, %lo(quiet)($8)
    beqz  $9, 5f              /* nothing played lately: return 0, as stock */
    nop
    beqz  $21, 5f
    addiu $9, $zero, 1
    sw    $9, %lo(silent)($8)
    lw    $9, %lo(quiet)($8)
    subu  $10, $9, $21
    sltu  $9, $9, $21
    movn  $10, $zero, $9      /* quiet = max(quiet - samples, 0) */
    sw    $10, %lo(quiet)($8)
    lhu   $3, 0x2a($17)
    ext   $3, $3, 8, 6        /* bytes a sample */
    mul   $3, $3, $21
    addu  $3, $3, $22
    move  $9, $22
3:  beq   $9, $3, 4f
    nop
    sb    $zero, 0($9)
    b     3b
    addiu $9, $9, 1
4:  move  $16, $21            /* as if read: pacing starts if it hasn't, return them */
    j     0x4147c8
    lw    $2, 0x1c($18)
5:  j     0x41469c            /* return s0, 0 */
    nop

/* 0x414754, in place of jal io_pcm_read: real PCM re-arms the budget and ends a fill. */
.globl read
read:
    addiu $sp, $sp, -24
    sw    $31, 20($sp)
    jal   0x41414c
    nop
    lw    $31, 20($sp)
    blez  $2, 6f
    addiu $sp, $sp, 24
    lui   $8, %hi(quiet)
    sw    $zero, %lo(silent)($8)
    li    $9, BUDGET
    sw    $9, %lo(quiet)($8)
6:  jr    $31
    nop

.align 2
.globl quiet
quiet:  .word 0
silent: .word 0
