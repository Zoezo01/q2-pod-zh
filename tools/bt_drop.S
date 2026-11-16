/*
 * bluealsa: DROP flushes the PCM in the control thread, and the encoder's DROP signal only rewinds
 * (docs/internals.md#bluetooth-aac). tools/build.py (patch_bluealsa) links this at BT_DROP_PAYLOAD,
 * with CALLERS defined from BT_PCM_CALLERS, and jumps to drop from DROP's case (BT_DROP) and to reset
 * from the encoder's signal handler (BT_DROP_HANDLER), so a delayed signal cannot discard the next
 * track's first PCM.
 */
.set noreorder
.set noat
.text

/* DROP's case, a0 the PCM: o32, keeps the PCM and ra. Holds its fd mutex while flushing the
 * nonblocking FIFO in 32 KiB batches, then sends DROP. No encoder restart or codec change. */
.globl drop
drop:
    addiu $sp, $sp, -0x20     /* o32 frame */
    sw    $ra, 0x1c($sp)      /* keep ra */
    sw    $16, 0x18($sp)      /* and s0 */
    move  $16, $4             /* s0 = the PCM */
    jal   0x430690            /* pthread_mutex_lock(&pcm->mutex) */
    addiu $4, $16, 0xc        /* a0 = &pcm->mutex, its delay slot */
1:  jal   0x4140b8            /* io_pcm_flush(pcm): a batch, while it flushed some */
    move  $4, $16             /* a0 = the PCM, its delay slot */
    bgtz  $2, 1b              /* flushed some: again */
    nop                       /* delay slot */
    jal   0x4303a0            /* pthread_mutex_unlock(&pcm->mutex) */
    addiu $4, $16, 0xc        /* a0 = &pcm->mutex, its delay slot */
    lw    $4, 0($16)          /* the PCM's thread */
    addiu $5, $zero, 6        /* a1 = DROP */
    jal   0x40b738            /* send_signal(pcm's thread + 0x50, DROP) */
    addiu $4, $4, 0x50        /* a0 = its signal queue, the delay slot */
    lw    $16, 0x18($sp)      /* s0 back */
    lw    $ra, 0x1c($sp)      /* ra back */
    move  $2, $zero           /* return 0 */
    jr    $ra                 /* return */
    addiu $sp, $sp, 0x20      /* drop the frame, its delay slot */

/* The encoder's DROP signal, in io_poll_and_read_pcm (0x4145d0). The shared read helper receives a
 * raw tail pointer, not the ffb_t: rewind each audited caller's partial PCM and update the helper's
 * pointer and capacity before it repolls; otherwise old and new tracks share one encoder frame.
 * caller ret, off: io_poll_and_read_pcm's return address ret, and the PCM ffb_t's offset off in the
 * caller's io state (s2), or none for Q2's LDAC loop, which keeps 256 samples in its saved s6/s7. */
.macro caller ret, off
    lui   $10, (\ret) >> 16   /* t2 = ret: lui and ori, not li's */
    ori   $10, $10, (\ret) & 0xffff /* its low half */
    bne   $8, $10, 2f         /* another caller's */
    nop                       /* delay slot */
.ifnb \off
    addiu $11, $18, \off      /* t3 = the ffb_t */
    lw    $22, 0($11)         /* s6 = its head */
    sw    $22, 4($11)         /* tail = head: the partial PCM gone */
    lw    $21, 8($11)         /* s5 = its capacity */
.else
    lw    $11, 0x4c($sp)      /* the accumulated tail */
    lw    $12, 0x40($sp)      /* the buffer */
    subu  $22, $22, $11       /* s6 back to the buffer */
    addu  $22, $22, $12       /* (s6 - tail + buffer) */
    sw    $12, 0x4c($sp)      /* tail = the buffer */
    addiu $21, $zero, 0x100   /* s5 = 256 samples */
    sw    $21, 0x48($sp)      /* the room left */
.endif
    j     0x414790            /* the existing OPEN/RESUME pacing reset and repoll */
    nop                       /* delay slot */
2:
.endm

.globl reset
reset:
    lw    $8, 0x54($sp)       /* io_poll_and_read_pcm's saved return address */
    CALLERS
    j     0x414790            /* unknown caller: reset pacing without touching its buffer */
    nop                       /* delay slot */
