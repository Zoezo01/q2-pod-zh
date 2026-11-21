/* The screen's flush (docs/internals.md#hooks): the UI is drawn landscape, the panel is portrait,
 * and each changed rectangle is turned into the framebuffer on its way to the screen. */
#include "navigation.h"

extern int stock_rotate_trampoline(void *fb, void *img, const int *r, int o);

/* rotate_bgra8888_image(fb, img, r, o), which every flush of this 32-bit screen goes through (its
 * one caller, soft_rotate_image, sends it only BGRA8888 pairs). Stock's worker (0x6e0f30) copies a
 * pixel at a time by a 4-byte memcpy call, about 45 instructions a pixel, down the framebuffer's
 * columns. For the Q2's turn, 270, the same pixels go to the same places a word at a time, along
 * the framebuffer's rows: image pixel (x, y) is framebuffer pixel (image height - 1 - y, x). Any
 * other turn, an empty rectangle or buffers that aren't whole words are stock's own body. */
/* ponytail: it writes along the rows where stock writes down the columns, so the result is stock's
 * while no two pixels of the rectangle share a framebuffer word (4 * (h - 1) under the page's line
 * length: at most 1276 against 1280) and page and canvas are apart, as AWTK's flushes, inside its
 * canvas, keep. Like stock, it checks no bounds. A lock that fails is unlocked too, which stock
 * skips: its graphic buffer's unlock (0x69da60) does nothing. */
int ringnav_rotate(void *fb, void *img, const int *r, int o) {
    if (o != 270 || r[2] <= 0 || r[3] <= 0) return stock_rotate_trampoline(fb, img, r, o);
    unsigned fl = bitmap_get_physical_line_length(fb), il = bitmap_get_physical_line_length(img);
    int x = r[0], y = r[1], w = r[2], h = r[3], ih = (int)bitmap_get_physical_height(img);
    unsigned char *d0 = bitmap_lock_buffer_for_write(fb);
    const unsigned char *s0 = bitmap_lock_buffer_for_read(img);
    int words = d0 && s0 && !((fl | il | (unsigned)d0 | (unsigned)s0) & 3);
    /* a framebuffer row at a time: the image's column x + k, upwards */
    for (int k = 0; words && k < w; ++k) {
        const unsigned *s = (const unsigned *)(s0 + (unsigned)(y + h - 1) * il) + x + k;
        unsigned *d = (unsigned *)(d0 + (unsigned)(x + k) * fl) + (ih - y - h);
        for (int i = h; i > 0; --i, s = (const unsigned *)((const unsigned char *)s - il))
            *d++ = *s;
    }
    bitmap_unlock_buffer(fb), bitmap_unlock_buffer(img);
    return words ? 0 : stock_rotate_trampoline(fb, img, r, o);
}
