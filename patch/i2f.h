#ifndef Q2_I2F_H
#define Q2_I2F_H

/* int to float without LLVM's unsigned path: for a value it knows is not negative, the MIPS
 * backend converts through a double (the 2^52 trick), which the test emulator cannot run. The
 * host render builds no MIPS and shares this header with coverflow.c and coverflow_thumb.c in
 * one translation unit, so the helper lives here instead of in either file. */
static inline float i2f(int v) {
#ifdef __mips__
    float f;
    __asm__("mtc1 %1, %0\n\tcvt.s.w %0, %0" : "=f"(f) : "r"(v));
    return f;
#else
    return (float)v; /* the host render builds no MIPS: nothing to steer around */
#endif
}

#endif /* Q2_I2F_H */
