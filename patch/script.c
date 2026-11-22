/* Per-script display: which face draws which characters, what happens where a face lacks them,
 * and the per-character rules other files ask about (peq.h's script.c heading: script_cjk,
 * script_upper). The build's half is tools/build.py's font block: the faces' ranges, and the
 * letters stock's font gains. Not here: sorting, letter groups, lyric search folding and the UI's
 * own language (docs/internals.md#themes, Scripts). */
#include "navigation.h"

#define WEIGHT_PROP "_q2_bold_fallback"

/* Retain a theme's bold intent when its selected face falls back to stock's full Unicode font.
 * Shared by Cover Flow captions, its track panel, themed rows and Now Playing titles. */
void font_weight_mark(widget_t *w, int bold) {
    bold = bold != 0;
    if (w && widget_get_prop_int(w, WEIGHT_PROP, 0) != bold)
        widget_set_prop_int(w, WEIGHT_PROP, bold);
}

/* The existing border hook runs after native self painting. Repeat only marked labels' own
 * foreground one pixel right, keeping their native shaping, marquee, clip and style. Calling
 * the vtable self painter does not dispatch paint events or re-enter border painting. */
void font_weight_paint(widget_t *w, canvas_t *canvas) {
    if (!w || !canvas || !widget_get_prop_int(w, WEIGHT_PROP, 0)) return;
    const unsigned *text = widget_get_text(w);
    if (!text || !*text) return;
    canvas_translate(canvas, 1, 0);
    widget_vtable_on_paint_self(w, canvas);
    canvas_translate(canvas, -1, 0); /* restore even when the native painter returns an error */
}

static const unsigned short bold_ranges[] = BOLD_RANGES,
                            classic_light_ranges[] = CLASSIC_LIGHT_RANGES,
                            glyph_ranges[] = GLYPH_FALLBACK_RANGES;

/* 1 iff c is in one of r's k / 2 first-last pairs. */
static int in_ranges(unsigned c, const unsigned short *r, unsigned k) {
    unsigned i = 0;
    while (i < k && (c < r[i] || c > r[i + 1])) i += 2;
    return i < k;
}

/* 1 iff every character of c (n < 0: up to the NUL; NULL has none) is in face's cmap. */
static int face_has(int face, const unsigned *c, int n) {
    const unsigned short *r = face == FONT_CLASSIC_LIGHT ? classic_light_ranges : bold_ranges;
    unsigned k = face == FONT_CLASSIC_LIGHT
                     ? sizeof classic_light_ranges / sizeof *classic_light_ranges
                     : sizeof bold_ranges / sizeof *bold_ranges;
    if (face == FONT_DEFAULT) return 1;
    if (!c) return 0;
    for (; n < 0 ? *c : n; ++c, --n)
        if (!in_ranges(*c, r, k)) return 0;
    return 1;
}

/* A language pack's font, for the characters GLYPH_FALLBACK_RANGES lists: right-to-left scripts and
 * their joining and direction controls, which no face here has (build.py checks stock's maps none).
 * language.c copies it to the Q2; it is read at the first such character drawn or measured, the
 * UI thread's. Without it they stay as before: stock's blank .notdef. */
#define GLYPH_FONT PEQ_ROOT "/mnt/data/q2-font.ttf"
#define GLYPH_FONT_BYTES (4u << 20)
static struct {
    int tried;
    font_t *font;
    void *bytes; /* stb reads them as long as the font lives: for good */
} gf;

/* font_get_glyph, which the build jumps here from (a leaf: FONT_GLYPH_LEAF), the whole of it: the
 * font's get_glyph, the pack's font's for a character it is for. Bad params (0x10) as stock's for
 * no font, no get_glyph or no glyph. */
int ringnav_font_glyph(font_t *font, unsigned chr, unsigned size, void *glyph) {
    if (in_ranges(chr, glyph_ranges, sizeof glyph_ranges / sizeof *glyph_ranges)) {
        if (!gf.tried) {
            unsigned n = 0;
            gf.tried = 1;
            gf.bytes = read_all(GLYPH_FONT, GLYPH_FONT_BYTES, &n);
            gf.font = gf.bytes ? font_stb_create("q2-font", gf.bytes) : (font_t *)0;
        }
        if (gf.font) font = gf.font;
    }
    int (*get)(font_t *, unsigned, unsigned, void *) = font ? P(font, FONT_GET_GLYPH) : (void *)0;
    return get && glyph ? get(font, chr, size, glyph) : 0x10;
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
