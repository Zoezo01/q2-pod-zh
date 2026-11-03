/* Independent-channel FLAC output for the stock Q2 hciplayer ABI.
 *
 * The vendor already fuses stereo decorrelation and output packing. Only its
 * independent-channel functions need these replacements: their inner sample
 * loop reloads input/output channel pointers on every sample. Cache the mono
 * or stereo pointers once, preserving the same sample/store order and widths.
 * Other channel counts use the original functions unchanged.
 *
 * Patch the DSP initializer's pointers, NOT the stock function entries, since
 * those entries remain the fallback. PCM planes are distinct decoder-owned
 * allocations, as in the original decoder's output contract. No SIMD, extra
 * alignment requirement, rounding, clipping, or intermediate PCM buffer.
 */
typedef unsigned int u32;
typedef unsigned short u16;
typedef int s32;
typedef void (*output_fn)(void **, s32 **, int, int, int);

#define OUTPUT(NAME, TYPE, PLANAR, STOCK)                                  \
void NAME(void **output, s32 **input, int channels, int length, int shift) \
{                                                                        \
    if (channels != 1 && channels != 2) {                                  \
        ((output_fn)(STOCK))(output, input, channels, length, shift);        \
        return;                                                          \
    }                                                                    \
    if (length <= 0) return;                                              \
    /* sllv in the vendor code masks the shift count to five bits. */      \
    unsigned int bits = (unsigned int)shift & 31;                          \
    const u32 *left = (const u32 *)input[0];                               \
    TYPE *dst = (TYPE *)output[0];                                        \
    if (channels == 1) {                                                 \
        do { *dst++ = (TYPE)(*left++ << bits); } while (--length);         \
    } else {                                                             \
        const u32 *right = (const u32 *)input[1];                          \
        if (PLANAR) {                                                    \
            TYPE *other = (TYPE *)output[1];                              \
            do {                                                         \
                *dst++ = (TYPE)(*left++ << bits);                         \
                *other++ = (TYPE)(*right++ << bits);                      \
            } while (--length);                                          \
        } else {                                                         \
            do {                                                         \
                *dst++ = (TYPE)(*left++ << bits);                         \
                *dst++ = (TYPE)(*right++ << bits);                        \
            } while (--length);                                          \
        }                                                                \
    }                                                                    \
}

OUTPUT(flac_output_s16i, u16, 0, 0x005b7e80u)
OUTPUT(flac_output_s32i, u32, 0, 0x005b8d18u)
OUTPUT(flac_output_s16p, u16, 1, 0x005b8ba0u)
OUTPUT(flac_output_s32p, u32, 1, 0x005ba83cu)
