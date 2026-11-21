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
unsigned quick_on(void) {
    const scheme_t *S = scheme();
    return flip_if(ipod_light(), neutral(S->tones[0]) ? accent_tone(TONE_RED)
                                                      : mix(S->tones[0], S->tones[1], 1, 2));
}
/* A 36 px bar, flat: r at rest, its first part px in what is on, and its marks in its ends
 * (BAR_SUNS, BAR_SPEAKERS, or none): the near one on the fill, white, or dark on a bright fill,
 * and the far one quiet until the fill reaches it. */
void flat_bar(void *w, void *canvas, const rect_t *r, int part, unsigned rest, int marks) {
    unsigned on = quick_on();
    int light = ipod_light(), dark = bright_tone(on), reached = part > r->w - r->h / 2;
    rect_t f = { r->x, r->y, part, r->h };
    stops_t under = { 2, { 0, 100 }, { rest, rest } }, lit = { 2, { 0, 100 }, { on, on } };
    paint_round_rows(canvas, r, &under, 18);
    paint_round_rows(canvas, &f, &lit, 18);
    if (marks == BAR_SUNS) {
        paint_art(w, canvas, dark ? FLAT_ART "drop_dim_l" : FLAT_ART "drop_dim", r->x + 6,
                  r->y + 6);
        paint_art(w, canvas,
                  reached ? dark ? FLAT_ART "drop_bright_l" : FLAT_ART "drop_bright"
                  : light ? FLAT_ART "drop_bright_ql"
                          : FLAT_ART "drop_bright_qd",
                  r->x + r->w - 33, r->y + 3);
    } else if (marks == BAR_SPEAKERS) {
        paint_art(w, canvas, dark ? FLAT_ART "spk_quiet_l" : FLAT_ART "spk_quiet_w", r->x + 12,
                  r->y + 11);
        paint_art(w, canvas,
                  reached ? dark ? FLAT_ART "spk_loud_l" : FLAT_ART "spk_loud_w"
                  : light ? FLAT_ART "spk_loud_l"
                          : FLAT_ART "spk_loud_d",
                  r->x + r->w - 34, r->y + 11);
    }
}
/* A page's label on the half's paper: slider_label's box and face, its ink the dark half's
 * 0xRRGGBB, no longer final, so Light Mode's style hook turns it with the page. */
void page_label(void *w, int x, int y, int ww, int h, int px, const char *face, unsigned rgb) {
    slider_label(w, x, y, ww, h, px, face);
    widget_set_prop_int(w, "_final_n", 0);
    widget_set_prop_int(w, "style:normal:text_color", (int)(0xff000000u | (RGBA(rgb) & 0xffffffu)));
}
