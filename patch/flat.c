/* The flat look's shared parts (a theme's pages.own painter flat, flat_pages): its colours, what is
 * on, its bar, and a label on its paper. Each screen's flat painter sits beside Classic's in that
 * screen's file. */
#include "navigation.h"

/* Final colours, [0] dark, [1] light: a control at rest, the ink, a quiet line */
const unsigned flat_rest[2] = { 0x2c2c2e, 0xe9e9ec }, flat_ink[2] = { 0xffffff, 0x1c1c1e },
               flat_quiet[2] = { 0x98989d, 0x6e6e73 };
/* A page's ground: the half's paper (a card theme's page), as the theme's lists have it */
unsigned flat_page(void) { return ipod_paper() ? scheme()->paper : 0; }
/* What is on, final: the theme's selection bar, its middle row (paint_selection's SEL_BAR), so a
 * lit button matches the row that is chosen. A neutral accent's bar (Graphite's dark grey) is
 * about a button's grey at rest: it takes its red tone, the silver, as Classic's lit tiles do.
 * Over Classic's own pages (classic_pages) it is those tiles' colour (quick_page's qs_tile): the
 * theme's own scheme's blue (Classic Blue's), another accent's red tone. */
unsigned flat_on(void) {
    const scheme_t *S = scheme();
    if (classic_pages() && theme()->schemes[accent()].label) return 0x207cde;
    return flip_if(ipod_light(), classic_pages() || neutral(S->tones[0])
                                     ? accent_tone(TONE_RED)
                                     : mix(S->tones[0], S->tones[1], 1, 2));
}
/* The flat look's headings and words: the theme's own face (theme_face's), or bold */
const char *flat_face(void) {
    return theme()->font[ROLE_TITLE] == FONT_CLASSIC_LIGHT ? "classic-light" : "bold";
}
/* A round box in a final colour: stock's, one call, as a row at a time costs a page's other parts
 * their share of a paint. Light Mode's ink is held off it, as flipping a colour back is not exact.
 */
void flat_box(canvas_t *canvas, rect_t *r, unsigned rgba, unsigned radius) {
    unsigned raw = ui_ink_raw(1);
    fill_box(canvas, r, rgba, radius);
    ui_ink_raw(raw);
}
/* A 36 px bar, flat: r at rest, its first part px in what is on (none at 0, never less than its own
 * round end), and its marks in its ends (BAR_SUNS, BAR_SPEAKERS, or none): the near one on the
 * fill, white, or dark on a bright fill, and the far one quiet until the fill reaches it, then the
 * near one's ink. */
void flat_bar(widget_t *w, canvas_t *canvas, const rect_t *r, int part, unsigned rest, int marks) {
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
    int reached = part > r->w - r->h / 2;
    /* over Classic's own pages the speakers are Classic's (tools/art.py SPEAKER_INKS): white or _l
     * on the fill, the far one unreached the half's _l or _d, not the quiet greys */
    int classic = marks == BAR_SPEAKERS && classic_pages();
    const char *on_ink = classic ? (*dark ? "_l" : "_w") : dark;
    tk_snprintf(name, sizeof name, "%s%s%s", classic ? "classic_light_" : FLAT_ART,
                mark[marks].near, on_ink);
    paint_art(w, canvas, name, r->x + mark[marks].nx, r->y + mark[marks].ny);
    tk_snprintf(name, sizeof name, "%s%s%s", classic ? "classic_light_" : FLAT_ART, mark[marks].far,
                reached        ? on_ink
                : classic      ? (ipod_light() ? "_l" : "_d")
                : ipod_light() ? "_ql"
                               : "_qd");
    paint_art(w, canvas, name, r->x + r->w - mark[marks].fx, r->y + mark[marks].fy);
}
/* A page's label on the half's paper: slider_label's box and face, its ink the dark half's
 * 0xRRGGBB, no longer final, so Light Mode's style hook turns it with the page. */
void page_label(widget_t *w, int x, int y, int ww, int h, int px, const char *face, unsigned rgb) {
    slider_label(w, x, y, ww, h, px, face);
    widget_set_prop_int(w, "_final_n", 0);
    widget_set_prop_int(w, "style:normal:text_color", (int)(0xff000000u | (RGBA(rgb) & 0xffffffu)));
}

/* The flat look's prompts (dialog.confirm painter flat): navigation.c's confirm_track,
 * paint_selection and confirm_labels hand over to these. */
/* confirm_track's, flat: Form A's paper, or the tray, the toast's box, then each cell at rest, the
 * pages' grey, or a lighter one on the tray's dark box. */
#define CONFIRM_TRAY_REST 0x48484a
void confirm_track_flat(widget_t *w, canvas_t *canvas, int form) {
    void *lcd = P(canvas, CANVAS_LCD);
    widget_t *cell[2] = { widget_lookup(w, "img_cancel", 0), widget_lookup(w, "img_enter", 0) };
    if (!lcd || !cell[0] || !cell[1]) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    int light = ipod_light();
    unsigned grey = form == FORM_TRAY ? CONFIRM_TRAY_REST : flat_rest[light];
    stops_t rest = { 2, { 0, 100 }, { grey, grey } };
    if (form == FORM_TRAY) {
        rect_t tray = { 38, 248, 299, 56 };
        flat_box(canvas, &tray, TOAST_RGBA, 12);
    } else {
        bar_rect(canvas, RGBA(flat_page()), 0, BAR_ROWS, 375, 320 - BAR_ROWS);
        widget_t *bar = *(void *const *)system_bar; /* over Quick Settings nothing else draws it */
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
void confirm_lit(canvas_t *canvas, const rect_t *c) {
    unsigned on = flat_on();
    stops_t lit = { 2, { 0, 100 }, { on, on } };
    paint_round_rows(canvas, c, &lit, 18);
}
/* confirm_labels', flat: no shadow; on the cell that holds white, or the ink on a bright fill; on
 * the other the half's ink, or white on the tray's dark box. */
void confirm_labels_flat(widget_t *w, canvas_t *canvas, int form) {
    widget_t *cell[2] = { widget_lookup(w, "img_cancel", 0), widget_lookup(w, "img_enter", 0) };
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

/* The hold menu's card, flat (hold_paint): its header and rows on the half's paper, then HOLD_FOOT
 * rows of it, the height of its corners, so the last row's band stays inside them. over: 0 before
 * the dialog's children (the card, and a line under its header), 1 after them (a hairline round the
 * card, a pixel outside it, over whatever lies there). Final colours. */
void hold_card_flat(canvas_t *canvas, const rect_t *card, int head, int over) {
    unsigned line = ipod_light() ? 0xd1d1d6 : 0x38383a, paper = flat_page();
    if (over) {
        rect_t ring = { card->x - 1, card->y - 1, card->w + 2, card->h + 2 };
        round_ring(canvas, &ring, 11, 0xff000000u | line);
        return;
    }
    stops_t face = { 2, { 0, 100 }, { paper, paper } };
    paint_round_rows(canvas, card, &face, 10);
    bar_rect(canvas, RGBA(line), card->x, card->y + head - 1, card->w, 1);
}
