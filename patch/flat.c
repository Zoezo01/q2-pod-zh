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
/* A round box in a final colour: stock's, one call, as a row at a time costs a page's other parts
 * their share of a paint. Light Mode's ink is held off it, as flipping a colour back is not exact.
 */
void flat_box(void *canvas, rect_t *r, unsigned rgba, unsigned radius) {
    unsigned raw = ui_ink_raw(1);
    fill_box(canvas, r, rgba, radius);
    ui_ink_raw(raw);
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

/* The flat look's prompts (dialog.confirm painter flat): navigation.c's confirm_track,
 * paint_selection and confirm_labels hand over to these. */
/* confirm_track's, flat: Form A's paper, or the tray, the toast's box, then each cell at rest, the
 * pages' grey, or a lighter one on the tray's dark box. */
#define CONFIRM_TRAY_REST 0x48484a
void confirm_track_flat(void *w, void *canvas, int form) {
    void *lcd = P(canvas, CANVAS_LCD),
         *cell[2] = { widget_lookup(w, "img_cancel", 0), widget_lookup(w, "img_enter", 0) };
    if (!lcd || !cell[0] || !cell[1]) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    int light = ipod_light();
    unsigned grey = form == FORM_TRAY ? CONFIRM_TRAY_REST : flat_rest[light];
    stops_t rest = { 2, { 0, 100 }, { grey, grey } };
    if (form == FORM_TRAY) {
        rect_t tray = { 38, 248, 299, 56 };
        flat_box(canvas, &tray, TOAST_RGBA, 12);
    } else {
        bar_rect(canvas, RGBA(scheme()->paper), 0, BAR_ROWS, 375, 320 - BAR_ROWS);
        void *bar = *(void *const *)system_bar; /* over Quick Settings nothing else draws it */
        if (bar) widget_paint(bar, canvas);
        st.paint_w = w;
    }
    for (int k = 0; k < 2; ++k) {
        rect_t r = { I(cell[k], W_X), I(cell[k], W_Y), I(cell[k], W_W), I(cell[k], W_H) };
        paint_round_rows(canvas, &r, &rest, 18);
    }
    stock_canvas_set_fill_color(canvas, fill);
}
/* The flat look's cell that holds, flat in what is on, from paint_selection's confirm branch. */
void confirm_lit(void *canvas, const rect_t *c) {
    unsigned on = flat_on();
    stops_t lit = { 2, { 0, 100 }, { on, on } };
    paint_round_rows(canvas, c, &lit, 18);
}
/* confirm_labels', flat: no shadow; on the cell that holds white, or the ink on a bright fill; on
 * the other the half's ink, or white on the tray's dark box. */
void confirm_labels_flat(void *w, void *canvas, int form) {
    void *cell[2] = { widget_lookup(w, "img_cancel", 0), widget_lookup(w, "img_enter", 0) };
    int light = ipod_light(), lit = st.sel_w == w ? st.sel_row : -1;
    if (!cell[0] || !cell[1]) return;
    unsigned over = bright_tone(flat_on()) ? flat_ink[1] : 0xffffff,
             plain = form == FORM_TRAY ? 0xffffff : flat_ink[light];
    unsigned raw = ui_ink_raw(1); /* final, as flat_box */
    for (int k = 0; k < 2; ++k) {
        const unsigned *s = widget_get_text(cell[k]);
        rect_t r = { I(cell[k], W_X), I(cell[k], W_Y), I(cell[k], W_W), I(cell[k], W_H) };
        draw_centred(canvas, s, wide_len(s), &r, CONFIRM_FLAT_PX, RGBA(k == lit ? over : plain));
    }
    ui_ink_raw(raw);
}
