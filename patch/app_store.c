/* The App Store's look (docs/internals.md#q2-shelf): its Apps and Games tabs over the list, and at
 * each app's row's end its state, a word, drawn flat in every theme (no gloss). Classic and Modern
 * have a two-part switch, the open half in the selected row's colour, Classic's on a strip under
 * its bar and Modern's in the page's title row; Minimal names the tabs in its title row, the open
 * one by a dot. A tab with apps that can be updated has a badge with their count, red, or on the
 * open half in its text colour (Minimal: "N UPDATES" at the strip's end). netupdate.c builds the
 * page and keeps its state; this file draws it, in ringnav_paint after stock (store_paint), and
 * takes the tabs' taps. */
#include "navigation.h" /* st: the selected row; theme(), scheme() */

#define STRIP_H 44 /* Classic's strip, its bar holding the title */
/* The state's room at a row's end: the row's labels end 8 px before it. */
#define STATE_X (375 - 16 - 84)
#define SEG_X 14 /* the switch's box, SEG_H high, centred in the strip */
#define SEG_H 30
#define RIM 0x717171 /* Classic's rims: #8E8E8E on its paper, Light Mode's flip */
#define BADGE 0xe0261b
#define GAMES_X 120 /* Minimal's Games name, Apps' from 36 */

static char on_page __attribute__((section(".scratch"))); /* the App Store's page is open */
static unsigned tab_timer __attribute__((section(".scratch")));
static int tab_want __attribute__((section(".scratch")));

static int strip_h(void) { return theme()->page_title_h ? theme()->page_title_h : STRIP_H; }

/* A size given for stock's face, in the theme's: Classic's draws 0.70 of its px where stock's
 * draws 0.86 (AWTK scales a face by its ascent to descent, not its em), so its rows are 27 px to
 * the others' 20 and a size takes the same step. At the plain px its words were unreadably small.
 */
static unsigned face_px(unsigned px) {
    return theme()->font[ROLE_TITLE] == FONT_CLASSIC_LIGHT ? (px * 27 + 10) / 20 : px;
}

static int tab_go(const void *unused) {
    tab_timer = 0;
    shelf_tab(tab_want);
    return 0;
}

/* A tap on the strip picks the tab under it (Minimal's names, else the switch's halves) from a
 * timer: the new rows replace the strip, never inside its own event. */
static int tab_touch(void *ctx, event_t *event) {
    int xy[2] = { I(event, EVENT_X), I(event, EVENT_Y) };
    widget_to_local(ctx, xy);
    tab_want = xy[0] >= (theme()->painter[PAINT_SELECTION] == SEL_CARD ? GAMES_X - 20 : 188);
    rearm(&tab_timer, tab_go, 0);
    return 0;
}

/* The tabs over view, page_list's on page (or none: the page closed); the strip, its tab and
 * badges set by store_tabs_set. With a title row (Minimal, Modern) the tabs take its place and its
 * label hides; under Classic, whose bar holds the title, the list moves down under the strip. */
widget_t *store_tabs(widget_t *page, widget_t *view) {
    on_page = page != 0;
    if (!page) return 0;
    int h = strip_h();
    if (theme()->page_title_h) {
        widget_set_visible(widget_get_child(page, 0), 0, 0); /* page_title's label, the first */
    } else {
        widget_t *list = P(view, W_PARENT);
        int y = I(list, W_Y) + h, room = widget_get_prop_int(page, "h", client_h()) - y,
            rows = I(list, W_H) < room ? I(list, W_H) : room;
        widget_move_resize(list, 0, y, 375, rows);
        widget_move_resize(view, 0, 0, 375, rows);
    }
    widget_t *strip = widget_factory_create_widget(widget_factory(), "view", page, 0, 0, 375, h);
    widget_set_prop_int(strip, "_tab", 0);
    widget_on(strip, EVT_POINTER_DOWN, tab_touch, strip);
    return strip;
}

/* The open tab (0 Apps, 1 Games) and each tab's updates, for the strip's next paint. */
void store_tabs_set(widget_t *strip, int tab, const int *updates) {
    if (!strip) return;
    widget_set_prop_int(strip, "_tab", tab);
    widget_set_prop_int(strip, "_b0", updates[0]);
    widget_set_prop_int(strip, "_b1", updates[1]);
    widget_invalidate_force(strip, 0);
}

/* An app's row (page_row_detail's item) and its state for the row's end: 0 Install, 1 Update,
 * 2 Installed, -1 none (its second line says what is happening, and runs to the row's end). Its
 * name stays clear of the state's word. */
void store_state(widget_t *item, int state) {
    if (widget_get_prop_int(item, "_state", -2) == state) return; /* -2: none yet */
    const theme_t *T = theme(); /* page_row_detail's boxes for a row with a detail */
    int x = T->page_x, w = STATE_X - 8 - x;
    widget_move_resize(widget_get_child(item, 0), x, T->page_cap_y, w, T->page_cap_h);
    widget_move_resize(widget_get_child(item, 1), x, T->page_det_y, state < 0 ? 375 - 16 - x : w,
                       T->page_det_h);
    widget_set_prop_int(item, "_state", state);
    widget_invalidate_force(item, 0);
}

/* Text on the accent or a coloured band is final white (or the scheme's sel_text); everything
 * else is given in the dark half's colours, which Light Mode flips. */
static unsigned on_accent(void) {
    const scheme_t *S = scheme();
    return S->sel_text ? S->sel_text : 0xffffff;
}

/* The selected row's look, as paint_selection and ringnav_style_color (st.sel_accent) give it: its
 * colour, flat (Classic's band its middle stop, Modern's bar its tone), and in *ink its labels':
 * on_accent's on a band or a coloured bar or card, near-black on Minimal's light card, else white.
 * *raw: both final, else in the dark half's terms, which Light Mode flips. */
static unsigned row_look(unsigned *ink, int *raw) {
    const scheme_t *S = scheme();
    int stops = theme()->painter[PAINT_SELECTION] == SEL_STOPS, card = S->flags & SCHEME_CARD;
    *raw = stops || (!neutral(S->tones[0]) && !card);
    *ink = *raw ? on_accent() : card ? 0x171717 : 0xffffff;
    return stops ? S->sel.rgb[S->sel.n / 2] : card ? S->fill : S->tones[0];
}

/* A tab's count, ending at right: its half's far end, as beside the name it ran into "Games". On
 * the open half it is that half's colours turned round. */
static void badge(canvas_t *canvas, int right, int cy, int n, int open) {
    char d[12];
    snprintf(d, sizeof d, "%d", n);
    int w = n > 9 ? 22 : 16, raw = 1;
    unsigned ink = 0xffffff, fill = BADGE;
    if (open) ink = row_look(&fill, &raw); /* the digits in the row's colour, on its ink */
    rect_t r = { right - w, cy - 8, w, 16 };
    ui_ink_raw(raw);
    fill_box(canvas, &r, rgba(fill, 255), 8);
    caption(canvas, d, r.x, r.y, r.w, r.h, face_px(11), rgba(ink, 255));
    ui_ink_raw(0);
}

static void paint_strip(widget_t *w, canvas_t *canvas) {
    const theme_t *T = theme();
    int h = I(w, W_H), tab = widget_get_prop_int(w, "_tab", 0),
        n[2] = { widget_get_prop_int(w, "_b0", 0), widget_get_prop_int(w, "_b1", 0) };
    static const char *const names[2] = { "Apps", "Games" };
    canvas_set_fill_color(canvas, rgba(0, 255)); /* the paper */
    canvas_fill_rect(canvas, 0, 0, 375, h);
    if (T->painter[PAINT_SELECTION] == SEL_CARD) { /* Minimal: the names, the open one dotted */
        int x = 36;
        for (int i = 0; i < 2; ++i) {
            if (i == tab) {
                rect_t dot = { x - 14, h / 2 - 2, 5, 5 };
                fill_box(canvas, &dot, rgba(0xffffff, 255), 3);
            }
            text_at(canvas, names[i], x, 0, h, 17, rgba(i == tab ? 0xffffff : 0x5c5c5c, 255), LEFT);
            x = GAMES_X;
        }
        if (n[0] + n[1]) {
            char s[24];
            snprintf(s, sizeof s, "%d update%s", n[0] + n[1], n[0] + n[1] == 1 ? "" : "s");
            text_at(canvas, s, 375 - 20, 0, h, 12, rgba(SUDO_MUTED, 255), RIGHT);
        }
        return;
    }
    /* Classic and Modern: the switch, its open half the selected row's colour; Classic's rim grey
     * under a hairline, Modern's the row's colour */
    int classic = T->painter[PAINT_SELECTION] == SEL_STOPS, y = (h - SEG_H) / 2,
        half = (375 - 2 * SEG_X) / 2, raw;
    unsigned ink, band = row_look(&ink, &raw);
    rect_t box = { SEG_X, y, 375 - 2 * SEG_X, SEG_H },
           in = { SEG_X + 1, y + 1, box.w - 2, SEG_H - 2 },
           open = { tab ? SEG_X + half : SEG_X + 1, y + 1, half - 1 + tab * (box.w - 2 * half),
                    SEG_H - 2 };
    if (classic) {
        canvas_set_fill_color(canvas, rgba(0x2f2f2f, 255));
        canvas_fill_rect(canvas, 0, h - 1, 375, 1);
    }
    ui_ink_raw(!classic && raw);
    fill_box(canvas, &box, rgba(classic ? RIM : band, 255), 7);
    ui_ink_raw(0);
    fill_box(canvas, &in, rgba(0, 255), 6);
    ui_ink_raw(raw);
    fill_box(canvas, &open, rgba(band, 255), 6);
    canvas_set_fill_color(canvas, rgba(band, 255)); /* fill_box's vgcanvas leaves it as it was */
    canvas_fill_rect(canvas, tab ? open.x : open.x + open.w - 8, open.y, 8,
                     open.h); /* square inside */
    ui_ink_raw(0);
    for (int i = 0; i < 2; ++i) {
        int cx = SEG_X + half * i + half / 2;
        unsigned c = i == tab ? ink : classic ? 0xffffff : band;
        ui_ink_raw((i == tab || !classic) && raw);
        rect_t r = { cx - half / 2, y, half, SEG_H };
        caption(canvas, names[i], r.x, r.y, r.w, r.h, face_px(classic ? 17 : 16), rgba(c, 255));
        ui_ink_raw(0);
        if (n[i]) badge(canvas, SEG_X + half * (i + 1) - 10, y + SEG_H / 2, n[i], i == tab);
    }
}

/* An app's row: its state, a word at the row's end, after stock drew its labels. Update is in the
 * accent (Minimal: white) and the others muted; on the selected row, the row's ink (lit_ink). The
 * accent goes through Light Mode's flip, which keeps a colour and turns a neutral accent dark. */
static void paint_row(widget_t *w, canvas_t *canvas, int state) {
    static const char *const words[3] = { "INSTALL", "UPDATE", "INSTALLED" };
    int h = I(w, W_H), card = theme()->painter[PAINT_SELECTION] == SEL_CARD,
        lit = st.sel_w == P(w, W_PARENT) && st.sel_row == I(w, W_Y) / h, raw = 0;
    unsigned ink = state != 1 ? SUDO_MUTED : card ? 0xffffff : accent_tone(0);
    if (lit) row_look(&ink, &raw);
    if (raw) ui_ink_raw(1);
    text_at(canvas, words[state], card ? 375 - 16 : 375 - 20, 0, h, face_px(12), rgba(ink, 255),
            RIGHT);
    if (raw) ui_ink_raw(0);
}

/* ringnav_paint, every widget after stock painted it: the App Store's strip and its apps' rows. */
void store_paint(widget_t *w, canvas_t *canvas) {
    if (!on_page || !P(canvas, CANVAS_LCD)) return;
    int h = I(w, W_H);
    if (h != strip_h() && h != page_h(64)) return; /* no prop lookups for other widgets */
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    int state = widget_get_prop_int(w, "_state", -1);
    if (state >= 0)
        paint_row(w, canvas, state);
    else if (widget_get_prop_int(w, "_tab", -1) >= 0)
        paint_strip(w, canvas);
    stock_canvas_set_fill_color(canvas, fill);
}
