/* Right-to-left text (docs/internals.md#arabic-text) where Q2 Pod draws a string itself. Stock's
 * widgets draw theirs through AWTK's bidi (widget_draw_text_in_rect:
 * canvas_draw_text_bidi_in_rect), which puts right-to-left runs in visual order and, through
 * FriBidi, shapes Arabic letters into their Presentation Forms; canvas_draw_text and
 * canvas_draw_text_in_rect take the string as it is, so an Arabic title drawn by them would read
 * backwards, its letters unjoined. These give the payload's painters the same visual string the
 * widgets draw. */
#include "navigation.h"

/* Stock's bidi_t, as canvas_draw_text_bidi_in_rect keeps it on its stack (0x64cc00: the frame from
 * sp + 0x18 to sp + 0x234): the visual string's pointer and length first, then the requested and
 * resolved directions, the two position tables, wchar_t vis_str_static[128] (where a shorter
 * string's visual copy lives, else bidi_log2vis allocates it and bidi_deinit frees it) and two
 * bool_t. */
#define BIDI_T_WORDS (0x21c / 4)

/* 1 iff s has a character of a right-to-left script: Hebrew, Arabic, Syriac, Thaana, NKo and the
 * Arabic supplements and presentation forms. Text without one is drawn as it is, as before. */
int bidi_rtl(const unsigned *s, unsigned n) {
    for (; s && n; ++s, --n)
        if ((*s >= 0x590 && *s <= 0x8ff) || (*s >= 0xfb1d && *s <= 0xfdff) ||
            (*s >= 0xfe70 && *s <= 0xfeff))
            return 1;
    return 0;
}

/* s[0..n) in visual order and shaped, into out (n characters: FriBidi keeps the count, a lam-alef
 * ligature's second letter becoming U+FEFF, which tools/build.py maps to an empty glyph). 0, out
 * untouched, when s has no right-to-left character or bidi fails: draw s itself then. */
int bidi_visual(const unsigned *s, unsigned n, unsigned *out) {
    unsigned bidi[BIDI_T_WORDS + 1] = { 0 }; /* zeroed: a visual string is never assumed */
    if (!n || !bidi_rtl(s, n)) return 0;
    bidi_init(bidi, 0, 0, bidi_type_from_name((const char *)0)); /* auto, as a widget's */
    const unsigned *vis = (void *)0;
    int ok = !bidi_log2vis(bidi, s, n) && (vis = P(bidi, 0)) && (unsigned)I(bidi, 4) == n;
    if (ok) memcpy(out, vis, n * sizeof *out);
    bidi_deinit(bidi);
    return ok;
}
