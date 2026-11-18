/* Per-script display: which face draws which characters, what happens where a face lacks them,
 * and the per-character rules other files ask about (peq.h's script.c heading: script_cjk,
 * script_upper). The build's half is tools/build.py's font block: the faces' ranges, and the
 * letters stock's font gains. Not here: sorting, letter groups, lyric search folding and the UI's
 * own language (docs/internals.md#themes, Scripts). */
#include "navigation.h"

#define WEIGHT_PROP "_q2_bold_fallback"

/* Retain a theme's bold intent when its selected face falls back to stock's full Unicode font.
 * Shared by Cover Flow captions, its track panel, themed rows and Now Playing titles. */
void font_weight_mark(void *w, int bold) {
    bold = bold != 0;
    if (w && widget_get_prop_int(w, WEIGHT_PROP, 0) != bold)
        widget_set_prop_int(w, WEIGHT_PROP, bold);
}

/* The existing border hook runs after native self painting. Repeat only marked labels' own
 * foreground one pixel right, keeping their native shaping, marquee, clip and style. Calling
 * the vtable self painter does not dispatch paint events or re-enter border painting. */
void font_weight_paint(void *w, void *canvas) {
    if (!w || !canvas || !widget_get_prop_int(w, WEIGHT_PROP, 0)) return;
    const unsigned *text = widget_get_text(w);
    if (!text || !*text) return;
    canvas_translate(canvas, 1, 0);
    widget_vtable_on_paint_self(w, canvas);
    canvas_translate(canvas, -1, 0); /* restore even when the native painter returns an error */
}

static const unsigned short bold_ranges[] = BOLD_RANGES,
                            classic_light_ranges[] = CLASSIC_LIGHT_RANGES;

/* 1 iff every character of c (n < 0: up to the NUL; NULL has none) is in face's cmap. */
static int face_has(int face, const unsigned *c, int n) {
    const unsigned short *r = face == FONT_CLASSIC_LIGHT ? classic_light_ranges : bold_ranges;
    unsigned k = face == FONT_CLASSIC_LIGHT
                     ? sizeof classic_light_ranges / sizeof *classic_light_ranges
                     : sizeof bold_ranges / sizeof *bold_ranges;
    if (face == FONT_DEFAULT) return 1;
    if (!c) return 0;
    for (; n < 0 ? *c : n; ++c, --n) {
        unsigned i = 0;
        while (i < k && (*c < r[i] || *c > r[i + 1])) i += 2;
        if (i >= k) return 0;
    }
    return 1;
}

/* bold.ttf or stock's default.ttf standing in for classic-light.ttf: that much of the px gives the
 * same capitals, their cap heights' ratio (test_ipod; stock's face, cap 183 of 299, comes to 83
 * too), so a CJK name sits a little over the capitals beside it, not a third. Only the fallback
 * takes it: Classic's bold stays at its px. */
#define FACE_FALLBACK_PCT 83
static int fallback_px(int px) { return (px * FACE_FALLBACK_PCT + 50) / 100; }

/* The FONT_* to draw s (n as face_has) in for a theme's face: face where it has every character,
 * else bold where it does (classic-light's fallback, at fallback_px of *px unless px is 0), else
 * stock's, which has CJK. bold.ttf falling to stock's keeps its px. */
int script_face(int face, const unsigned *s, int n, int *px) {
    if (face_has(face, s, n)) return face;
    if (face != FONT_CLASSIC_LIGHT) return FONT_DEFAULT; /* CJK */
    if (px) *px = fallback_px(*px);
    return face_has(FONT_BOLD, s, n) ? FONT_BOLD : FONT_DEFAULT; /* Cyrillic: still bold */
}

/* Canvas text in the theme's face where it has the characters, else bold.ttf where it has them
 * (still bold), else the system font, either at the fallback's px (draw_centred). A theme without
 * a face of its own draws the system font, as before. */
const char *theme_face(const unsigned *s, int n, int *px) {
    if (theme()->font[ROLE_TITLE] != FONT_CLASSIC_LIGHT) return (const char *)0;
    int face = script_face(FONT_CLASSIC_LIGHT, s, n, px);
    return face == FONT_CLASSIC_LIGHT ? "classic-light"
           : face == FONT_BOLD        ? "bold"
                                      : (const char *)0;
}
