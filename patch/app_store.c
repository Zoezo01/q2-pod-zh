/* The App Store's look (docs/internals.md#q2-shelf): its Apps and Games tabs over the list, and on
 * each app's row a tile and its state, drawn flat in every theme (no gloss). Classic and Modern
 * have a two-part switch, the open half in the accent, Classic's on a strip under its bar and
 * Modern's in the page's title row; Minimal names the tabs in its title row, the open one by a dot.
 * A tab with apps that can be updated has a red badge with their count (Minimal: "N UPDATES" at
 * the strip's end). netupdate.c builds the page and keeps its state; this file draws it, in
 * ringnav_paint after stock (store_paint), and takes the tabs' taps. */
#include "navigation.h" /* st: the selected row; theme(), scheme() */

#define STRIP_H 44 /* Classic's strip, its bar holding the title */
#define TILE 40    /* the tile's side, from x 20 */
#define TEXT_X 72  /* a row's text, past the tile */
#define PILL_W 84  /* the state's box, 16 px in from the right */
#define PILL_H 22
#define PILL_X (375 - 16 - PILL_W)
#define SEG_X 14 /* the switch's box, SEG_H high, centred in the strip */
#define SEG_H 30
#define RIM 0x717171 /* Classic's rims: #8E8E8E on its paper, Light Mode's flip */
#define BADGE 0xe0261b
#define GAMES_X 120 /* Minimal's Games name, Apps' from 36 */

/* The tiles' flat colours, by the app's id: stand-ins for icons the list can't show. */
static const unsigned hues[] = { 0x5a6b7d, 0x8d6bd6, 0x2fb85a, 0xe8962a, 0xb05ad8,
                                 0xe05a4a, 0x3f8fe0, 0xd9a400, 0xe8557e, 0x2a9d8f };

static char on_page __attribute__((section(".scratch"))); /* the App Store's page is open */
static unsigned tab_timer __attribute__((section(".scratch")));
static int tab_want __attribute__((section(".scratch")));

static int strip_h(void) { return theme()->page_title_h ? theme()->page_title_h : STRIP_H; }

static int tab_go(const void *unused) {
    tab_timer = 0;
    shelf_tab(tab_want);
    return 0;
}

/* A tap on the strip picks the tab under it (Minimal's names, else the switch's halves) from a
 * timer: the new rows replace the strip, never inside its own event. */
static int tab_touch(void *ctx, void *event) {
    int xy[2] = { I(event, EVENT_X), I(event, EVENT_Y) };
    widget_to_local(ctx, xy);
    tab_want = xy[0] >= (theme()->painter[PAINT_SELECTION] == SEL_CARD ? GAMES_X - 20 : 188);
    rearm(&tab_timer, tab_go, 0);
    return 0;
}

/* The tabs over view, page_list's on page (or none: the page closed); the strip, its tab and
 * badges set by store_tabs_set. With a title row (Minimal, Modern) the tabs take its place and its
 * label hides; under Classic, whose bar holds the title, the list moves down under the strip. */
void *store_tabs(void *page, void *view) {
    on_page = page != 0;
    if (!page) return 0;
    int h = strip_h();
    if (theme()->page_title_h) {
        widget_set_visible(widget_get_child(page, 0), 0, 0); /* page_title's label, the first */
    } else {
        void *list = P(view, W_PARENT);
        int y = I(list, W_Y) + h, room = widget_get_prop_int(page, "h", client_h()) - y,
            rows = I(list, W_H) < room ? I(list, W_H) : room;
        widget_move_resize(list, 0, y, 375, rows);
        widget_move_resize(view, 0, 0, 375, rows);
    }
    void *strip = widget_factory_create_widget(widget_factory(), "view", page, 0, 0, 375, h);
    widget_set_prop_int(strip, "_tab", 0);
    widget_on(strip, EVT_POINTER_DOWN, tab_touch, strip);
    return strip;
}

/* The open tab (0 Apps, 1 Games) and each tab's updates, for the strip's next paint. */
void store_tabs_set(void *strip, int tab, const int *updates) {
    if (!strip) return;
    widget_set_prop_int(strip, "_tab", tab);
    widget_set_prop_int(strip, "_b0", updates[0]);
    widget_set_prop_int(strip, "_b1", updates[1]);
    widget_invalidate_force(strip, 0);
}

/* page_row_detail's item for the app with id and name: its two lines moved past the tile and clear
 * of the state's box, the tile's colour and letters kept for store_paint. */
void store_row(void *item, const char *id, const char *name) {
    const theme_t *T = theme(); /* page_row_detail's boxes for a row with a detail */
    widget_move_resize(widget_get_child(item, 0), TEXT_X, T->page_cap_y, PILL_X - 8 - TEXT_X,
                       T->page_cap_h);
    widget_move_resize(widget_get_child(item, 1), TEXT_X, T->page_det_y, PILL_X - 8 - TEXT_X,
                       T->page_det_h);
    char letters[8];
    /* the first character, or two digits ("20" for 2048) */
    unsigned n = 1;
    while (name[n] && ((unsigned char)name[n] & 0xc0) == 0x80 && n < 4) ++n;
    if (name[0] >= '0' && name[0] <= '9' && name[1] >= '0' && name[1] <= '9') n = 2;
    snprintf(letters, sizeof letters, "%.*s", (int)n, name);
    widget_set_prop_str(item, "_tile", letters);
    widget_set_prop_int(item, "_hue",
                        (int)(hash_bytes(FNV_SEED, (const unsigned char *)id, strlen(id)) %
                              (sizeof hues / sizeof *hues)));
    widget_set_prop_int(item, "_state", -1);
}

/* The row's state for its box: 0 Install, 1 Update, 2 Installed, -1 none (its second line says
 * what is happening). */
void store_state(void *item, int state) {
    if (widget_get_prop_int(item, "_state", -1) == state) return;
    widget_set_prop_int(item, "_state", state);
    widget_invalidate_force(item, 0);
}

/* Text on the accent or a coloured band is final white (or the scheme's sel_text); everything
 * else is given in the dark half's colours, which Light Mode flips. */
static unsigned on_accent(void) {
    const scheme_t *S = scheme();
    return S->sel_text ? S->sel_text : 0xffffff;
}

/* The selected row's ink, as ringnav_style_color gives its labels: on_accent's, final (*raw), on a
 * coloured band (st.sel_accent); otherwise near-black on Minimal's light cards, else white. */
static unsigned lit_ink(int *raw) {
    *raw = st.sel_accent != 0;
    return *raw ? on_accent() : scheme()->flags & SCHEME_CARD ? 0x171717 : 0xffffff;
}

static void raw_text(void *canvas, const char *s, int x, int y, int h, unsigned px, unsigned c,
                     int align, int raw) {
    if (raw) ui_ink_raw(1);
    text_at(canvas, s, x, y, h, px, rgba(c, 255), align);
    if (raw) ui_ink_raw(0);
}

static void badge(void *canvas, int x, int cy, int n, int open) {
    char d[12];
    snprintf(d, sizeof d, "%d", n);
    rect_t r = { x, cy - 8, n > 9 ? 22 : 16, 16 };
    ui_ink_raw(1);
    fill_box(canvas, &r, rgba(open ? on_accent() : BADGE, 255), 8);
    caption(canvas, d, r.x, r.y, r.w, r.h, 11, rgba(open ? accent_tone(0) : 0xffffff, 255));
    ui_ink_raw(0);
}

static void paint_strip(void *w, void *canvas) {
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
    /* Classic and Modern: the switch, Classic's rim grey under a hairline, Modern's the accent */
    int classic = T->painter[PAINT_SELECTION] == SEL_STOPS, y = (h - SEG_H) / 2,
        half = (375 - 2 * SEG_X) / 2;
    unsigned accent = accent_tone(0);
    rect_t box = { SEG_X, y, 375 - 2 * SEG_X, SEG_H },
           in = { SEG_X + 1, y + 1, box.w - 2, SEG_H - 2 },
           open = { tab ? SEG_X + half : SEG_X + 1, y + 1, half - 1 + tab * (box.w - 2 * half),
                    SEG_H - 2 };
    if (classic) {
        canvas_set_fill_color(canvas, rgba(0x2f2f2f, 255));
        canvas_fill_rect(canvas, 0, h - 1, 375, 1);
    }
    ui_ink_raw(!classic);
    fill_box(canvas, &box, rgba(classic ? RIM : accent, 255), 7);
    ui_ink_raw(0);
    fill_box(canvas, &in, rgba(0, 255), 6);
    ui_ink_raw(1);
    fill_box(canvas, &open, rgba(accent, 255), 6);
    canvas_set_fill_color(canvas, rgba(accent, 255)); /* fill_box's vgcanvas leaves it as it was */
    canvas_fill_rect(canvas, tab ? open.x : open.x + open.w - 8, open.y, 8,
                     open.h); /* square inside */
    ui_ink_raw(0);
    for (int i = 0; i < 2; ++i) {
        int cx = SEG_X + half * i + half / 2;
        unsigned c = i == tab ? on_accent() : classic ? 0xffffff : accent;
        if (i == tab || !classic) ui_ink_raw(1);
        rect_t r = { cx - half / 2, y, half, SEG_H };
        caption(canvas, names[i], r.x, r.y, r.w, r.h, classic ? 17 : 16, rgba(c, 255));
        ui_ink_raw(0);
        if (n[i]) badge(canvas, cx + 26, y + SEG_H / 2, n[i], i == tab);
    }
}

/* An app's row: the tile and the state, after stock drew its labels. On the selected row the
 * state takes the row's ink (lit_ink), and Modern's box the band's colours. */
static void paint_row(void *w, void *canvas, const char *letters) {
    const theme_t *T = theme();
    int h = I(w, W_H), state = widget_get_prop_int(w, "_state", -1),
        sel = T->painter[PAINT_SELECTION],
        lit = st.sel_w == P(w, W_PARENT) && st.sel_row == I(w, W_Y) / h, raw = 0;
    rect_t tile = { 20, (h - TILE) / 2, TILE, TILE };
    if (T->icons_mono) { /* Minimal's grey tiles */
        fill_box(canvas, &tile, rgba(0x2a2a2a, 255), 9);
        caption(canvas, letters, tile.x, tile.y, TILE, TILE, 21, rgba(0xd8d8d8, 255));
    } else {
        ui_ink_raw(1);
        fill_box(canvas, &tile, rgba(hues[widget_get_prop_int(w, "_hue", 0)], 255), 9);
        caption(canvas, letters, tile.x, tile.y, TILE, TILE, 21, rgba(0xffffff, 255));
        ui_ink_raw(0);
    }
    if (state < 0) return;
    static const char *const words[3] = { "INSTALL", "UPDATE", "INSTALLED" };
    unsigned accent = accent_tone(0), ink = lit ? lit_ink(&raw) : 0xffffff;
    rect_t box = { PILL_X, (h - PILL_H) / 2, PILL_W, PILL_H },
           in = { box.x + 1, box.y + 1, PILL_W - 2, PILL_H - 2 };
    int mid = box.x + PILL_W / 2;
    if (sel == SEL_CARD || state == 2) { /* Minimal's word alone, and Installed in every theme */
        raw_text(canvas, words[state], sel == SEL_CARD ? 375 - 16 : 375 - 20, 0, h, 12,
                 lit                             ? ink
                 : state == 1 && sel == SEL_CARD ? 0xffffff
                                                 : SUDO_MUTED,
                 RIGHT, raw);
        return;
    }
    if (sel == SEL_STOPS) { /* Classic: a grey rim, white on the band */
        ui_ink_raw(raw);
        fill_box(canvas, &box, rgba(lit ? ink : RIM, 255), 5);
        ui_ink_raw(0);
        if (!lit) fill_box(canvas, &in, rgba(0, 255), 4);
        raw_text(canvas, words[state], mid, 0, h, 12, lit ? accent : 0xffffff, MID, raw);
        return;
    }
    /* Modern: the accent's rim round the paper, Update filled with the band's ink on it; on the
     * band, the row's ink round the band's colour, the band's own when Light Mode flips it */
    int rim_raw = !lit || raw;
    unsigned rim = lit ? ink : accent;
    ui_ink_raw(rim_raw);
    fill_box(canvas, &box, rgba(rim, 255), PILL_H / 2);
    ui_ink_raw(raw);
    if (state == 0) fill_box(canvas, &in, rgba(lit ? accent : 0, 255), PILL_H / 2 - 1);
    ui_ink_raw(0);
    if (state == 0)
        raw_text(canvas, words[state], mid, 0, h, 12, rim, MID, rim_raw);
    else
        raw_text(canvas, words[state], mid, 0, h, 12, lit ? accent : on_accent(), MID, !lit || raw);
}

/* ringnav_paint, every widget after stock painted it: the App Store's strip and its apps' rows. */
void store_paint(void *w, void *canvas) {
    if (!on_page || !P(canvas, CANVAS_LCD)) return;
    int h = I(w, W_H);
    if (h != strip_h() && h != page_h(64)) return; /* no prop lookups for other widgets */
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    const char *letters = widget_get_prop_str(w, "_tile", 0);
    if (letters)
        paint_row(w, canvas, letters);
    else if (widget_get_prop_int(w, "_tab", -1) >= 0)
        paint_strip(w, canvas);
    stock_canvas_set_fill_color(canvas, fill);
}
