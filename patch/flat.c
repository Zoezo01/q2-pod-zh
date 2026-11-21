/* The flat look's shared parts (a theme's pages.own painter flat, flat_pages): its colours, what is
 * on, its bar, and a label on its paper. Each screen's flat painter sits beside Classic's in that
 * screen's file. */
#include "navigation.h"

/* Final colours, [0] dark, [1] light: a control at rest, the ink, a quiet line */
const unsigned flat_rest[2] = { 0x2c2c2e, 0xe9e9ec }, flat_ink[2] = { 0xffffff, 0x1c1c1e },
               flat_quiet[2] = { 0x98989d, 0x6e6e73 };
/* A page's ground: the half's paper, as the theme's lists have it */
unsigned flat_page(void) { return ipod_light() ? scheme()->paper : 0; }
/* What is on, final: the theme's selection bar, its middle row (paint_selection's SEL_BAR), so a
 * lit button matches the row that is chosen. A neutral accent's bar (Graphite's dark grey) is
 * about a button's grey at rest: it takes its red tone, the silver, as Classic's lit tiles do. */
unsigned flat_on(void) {
    const scheme_t *S = scheme();
    return flip_if(ipod_light(), neutral(S->tones[0]) ? accent_tone(TONE_RED)
                                                      : mix(S->tones[0], S->tones[1], 1, 2));
}
/* A 36 px bar, flat: r at rest, its first part px in what is on (none at 0, never less than its own
 * round end), and its marks in its ends (BAR_SUNS, BAR_SPEAKERS, or none): the near one on the
 * fill, white, or dark on a bright fill, and the far one quiet until the fill reaches it, then the
 * near one's ink. */
void flat_bar(void *w, void *canvas, const rect_t *r, int part, unsigned rest, int marks) {
    static const struct {
        const char *near, *far;
        signed char nx, ny, fx, fy; /* the near one's place from r's corner, the far one's in */
    } mark[] = { [BAR_SUNS] = { "drop_dim", "drop_bright", 6, 6, 33, 3 },
                 [BAR_SPEAKERS] = { "spk_quiet", "spk_loud", 12, 11, 34, 11 } };
    unsigned on = flat_on();
    const char *dark = bright_tone(on) ? "_l" : "";
    rect_t f = { r->x, r->y, part < r->h ? r->h : part, r->h };
    stops_t under = { 2, { 0, 100 }, { rest, rest } }, lit = { 2, { 0, 100 }, { on, on } };
    char name[32];
    paint_round_rows(canvas, r, &under, 18);
    if (part > 0) paint_round_rows(canvas, &f, &lit, 18);
    if (marks != BAR_SUNS && marks != BAR_SPEAKERS) return;
    /* ponytail: the near mark is the fill's ink even with no fill; every caller fills at least the
     * round end, a mark quiet in the half there would take images of its own */
    tk_snprintf(name, sizeof name, FLAT_ART "%s%s", mark[marks].near, dark);
    paint_art(w, canvas, name, r->x + mark[marks].nx, r->y + mark[marks].ny);
    tk_snprintf(name, sizeof name, FLAT_ART "%s%s", mark[marks].far,
                part > r->w - r->h / 2 ? dark
                : ipod_light()         ? "_ql"
                                       : "_qd");
    paint_art(w, canvas, name, r->x + r->w - mark[marks].fx, r->y + mark[marks].fy);
}
/* A page's label on the half's paper: slider_label's box and face, its ink the dark half's
 * 0xRRGGBB, no longer final, so Light Mode's style hook turns it with the page. */
void page_label(void *w, int x, int y, int ww, int h, int px, const char *face, unsigned rgb) {
    slider_label(w, x, y, ww, h, px, face);
    widget_set_prop_int(w, "_final_n", 0);
    widget_set_prop_int(w, "style:normal:text_color", (int)(0xff000000u | (RGBA(rgb) & 0xffffffu)));
}
