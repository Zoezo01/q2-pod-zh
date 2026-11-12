/* Coalesce FLAC's redundant heartbeat publication; never batch cancellation checks.
 *
 * Q2 V1.36 hciplayer, pinned by tools/peq.py PLAYER_SHA:
 *   0x42b710 locks 0xb11db4, sets byte 0xb11e68 to 1, unlocks, and returns
 *   (word 0xb11e70 == 1 || word 0xb11e6c == 1), in that read order.
 * The residual decoder at 0x5b6854 invokes it after each residual at five sites:
 *   0x5b69c4, 0x5b6a78, 0x5b6c60, 0x5b6cc0, 0x5b6d30.
 * Only these calls may be redirected here; all other codec callers stay stock.
 *
 * Direct heartbeat access audit of the pinned binary:
 *   set 1, under the same mutex: 0x42b6ac, 0x42b734, 0x42da40,
 *                               0x42f04c, 0x42f910;
 *   snapshot then clear, under that mutex: 0x42b6ec/0x42b6f0 and
 *                                         0x42d8a4/0x42d8a8.
 * The latter consumer runs at >=1000 ms intervals (0x42d840) and exits after
 * six empty observations (0x42d9d0). This byte carries liveness, not PCM or a
 * sample count. The stop words have independent, unguarded stock writers.
 *
 * A coherent byte load of 1 makes another set-to-1 redundant. Its publication
 * can be ordered before any overlapping consumer's snapshot/clear operation.
 * A load of anything else uses the original locked operation, so a consumer
 * that already cleared the byte cannot lose the next publication. There is no
 * counter, per-stream state, skipped stop test, or unlocked heartbeat write.
 * The fast path keeps a MIPS memory-order barrier before reading the stop
 * words, and preserves stock's ==1 tests and short circuit. A request arriving
 * after its word was read is seen at the next residual, just as stock.
 *
 * test/flac_sync.py executes the actual stock functions and residual decoder,
 * including consumer interleavings, before this experiment is used on device.
 */
int flac_sync_check(void) {
    if (*(volatile unsigned char *)0xb11e68u != 1)
        return ((int (*)(void))0x42b710u)();
    __asm__ volatile("sync" ::: "memory");
    if (*(volatile unsigned *)0xb11e70u == 1) return 1;
    return *(volatile unsigned *)0xb11e6cu == 1;
}
