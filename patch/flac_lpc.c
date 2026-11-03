/* FLAC LPC reconstruction for the pinned V1.32 hciplayer (tools/peq.py PLAYER_SHA).
 *
 * ABI: decoded samples/residuals, reversed coefficients (oldest history first),
 * order, nonnegative predictor shift, total block size; o32's fifth arg is on
 * the caller's stack. The warm-up samples [0, order) stay unchanged.
 *
 * Stock's callbacks are 0x5ba9bc (32-bit accumulator) and 0x5bab3c (64-bit).
 * Its DSP initializer at 0x40a17c installs them at context+0x8c and +0x90.
 * Replace the pointer initialization, never the original entries: uncommon
 * orders/shift values still call those unchanged implementations.
 *
 * Unroll orders 1-12, covering the subset's usual encoder search range. Keeping
 * the paths separate preserves stock's accumulator width and wraparound. The
 * 32-bit path uses unsigned arithmetic to avoid signed-overflow undefined
 * behavior. The 64-bit MIPS multiply/accumulate chain wraps in HI:LO exactly as
 * stock does, but avoids moving HI:LO to/from general registers for every tap.
 *
 * test/flac_lpc.py compares the emitted MIPS against the stock binary, including
 * 24-bit input, extreme overflow, odd tails, unsupported orders and the o32 ABI.
 * Instruction-count reductions are not CPU-cycle or battery measurements.
 */
typedef unsigned u32;
typedef signed i32;

/* Coefficients live on the decoder stack, separately from the sample buffer. */
#define LPC32(N)                                                                            \
    static __attribute__((always_inline)) inline void                                        \
    lpc32_##N(i32 *restrict data, const i32 *restrict co, int sh, int len) {                    \
        for (int i = N; i < len; ++i) {                                                      \
            u32 sum = 0;                                                                     \
            _Pragma("clang loop unroll(full)")                                               \
            for (int k = 0; k < N; ++k) sum += (u32)co[k] * (u32)data[i - N + k];             \
            data[i] = (i32)((u32)data[i] + (u32)((i32)sum >> sh));                             \
        }                                                                                    \
    }
LPC32(1) LPC32(2) LPC32(3) LPC32(4) LPC32(5) LPC32(6)
LPC32(7) LPC32(8) LPC32(9) LPC32(10) LPC32(11) LPC32(12)

#define CASE(N) case N: lpc32_##N(data, co, sh, len); return;
void flac_lpc32(i32 *restrict data, const i32 *restrict co, int order, int sh, int len) {
    if ((unsigned)sh > 31) goto fallback;
    switch (order) {
        CASE(1) CASE(2) CASE(3) CASE(4) CASE(5) CASE(6)
        CASE(7) CASE(8) CASE(9) CASE(10) CASE(11) CASE(12)
    }
fallback:
    ((void (*)(i32 *, const i32 *, int, int, int))0x5ba9bc)(data, co, order, sh, len);
}
#undef CASE

#define LPC64(N)                                                                            \
    static __attribute__((always_inline)) inline void                                        \
    lpc64_##N(i32 *restrict data, const i32 *restrict co, int sh, int len) {                    \
        for (int i = N; i < len; ++i) {                                                      \
            u32 lo, hi;                                                                      \
            __asm__ volatile("mult %0,%1" : : "r"(co[0]), "r"(data[i - N]) : "hi", "lo");      \
            _Pragma("clang loop unroll(full)")                                               \
            for (int k = 1; k < N; ++k)                                                      \
                __asm__ volatile("madd %0,%1" : : "r"(co[k]), "r"(data[i - N + k])             \
                                 : "hi", "lo");                                              \
            __asm__ volatile("mflo %0\n\tmfhi %1" : "=r"(lo), "=r"(hi) : : "hi", "lo");         \
            /* The low word of a 64-bit shift, also defined when sh is zero. */               \
            u32 sum = (lo >> sh) | ((hi << 1) << (31 - sh));                                  \
            data[i] = (i32)((u32)data[i] + sum);                                               \
        }                                                                                    \
    }
LPC64(1) LPC64(2) LPC64(3) LPC64(4) LPC64(5) LPC64(6)
LPC64(7) LPC64(8) LPC64(9) LPC64(10) LPC64(11) LPC64(12)

#define CASE(N) case N: lpc64_##N(data, co, sh, len); return;
void flac_lpc64(i32 *restrict data, const i32 *restrict co, int order, int sh, int len) {
    if ((unsigned)sh > 31) goto fallback;
    switch (order) {
        CASE(1) CASE(2) CASE(3) CASE(4) CASE(5) CASE(6)
        CASE(7) CASE(8) CASE(9) CASE(10) CASE(11) CASE(12)
    }
fallback:
    ((void (*)(i32 *, const i32 *, int, int, int))0x5bab3c)(data, co, order, sh, len);
}
#undef CASE
