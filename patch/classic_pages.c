/* Classic's own pages: the T9 keyboard, Search, the playlist name box, the slider pages and the
 * message screens. */
#include "navigation.h"

/* Classic's message screens (msg_open; st has their windows): stock's label_msg in the scan's
 * dialog, our line, btn_no, the songs before the scan, the bar's part in px, stock's text as last
 * read and the poll following it. msg_counting: 1 msg_count runs, 3 + n it has answered n (-1
 * failed): one word, so the UI thread can't see the flag before the value. search_px:
 * search_edit's size in its disabled state, 0 until set. */
static struct {
    void *msg_src, *msg_line, *msg_btn;
    int msg_total;
    volatile int msg_counting;
    short msg_part;
    unsigned msg_timer;
    char msg_seen[128];
    int search_px;
} cp __attribute__((section(".scratch")));

/* Classic's T9 keyboard (stock's kb_default_t9, shared with the other themes, so nothing here
 * changes the asset): a ramp down its window, from screen row 60. Keys paint in the background hook
 * in place of stock's fill, and stock's text clears (the style hook). All colours are final. */
static unsigned kb_ramp(int y) {
    static const short rows[] = { 60, 62, 130, 190, 250, 320 };
    static const unsigned rgb[] = { 0x8b93a0, 0x8b93a0, 0x7d8590, 0x6b7581, 0x535d69, 0x444e5a };
    int k = 0;
    while (k < 4 && y >= rows[k + 1]) ++k;
    return mix(rgb[k], rgb[k + 1], y - rows[k], rows[k + 1] - rows[k]);
}
/* The ramp's screen rows y0 up to y1, where the canvas starts at screen row top. */
void paint_ramp(void *canvas, int y0, int y1, int top) {
    for (int y = y0; y < y1; ++y) bar_rect(canvas, RGBA(kb_ramp(y)), 0, y - top, 375, 1);
}
/* A button of the keyboard's: its panel's page's row's. */
int kb_key(void *w) {
    if (!st.kb_win || !w) return 0;
    for (int i = 0; i < 4 && w; ++i) w = P(w, W_PARENT);
    return w == st.kb_win;
}
void kb_paint_key(void *w, void *canvas) {
    static const stops_t plain = { 5,
                                   { 0, 6, 45, 85, 100 },
                                   { 0xfbfbfb, 0xf4f4f4, 0xeeeef0, 0xe3e4e6, 0xdadbdd } },
                         function = { 4,
                                      { 0, 8, 50, 100 },
                                      { 0x9aa1a9, 0x8d939f, 0x7e8693, 0x6a7384 } },
                         enter = { 3, { 0, 50, 100 }, { 0x7cb0ec, 0x3f7fdf, 0x1f5fc8 } };
    static const unsigned done[] = { 'D', 'o', 'n', 'e', 0 },
                          space_text[] = { 's', 'p', 'a', 'c', 'e', 0 };
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    const char *name = widget_get_prop_str(w, "name", "");
    char c = name ? *name : 0; /* opt:, page:, key:, action, space, or the character itself */
    int action = c == 'a', space = c == 's', fn = c == 'p' || c == 'k', white = action || fn;
    int kw = I(w, W_W), kh = I(w, W_H);
    rect_t r = { 0, 0, kw, kh }, ring = { -1, -1, kw + 2, kh + 2 }, down = { 0, 2, kw, kh };
    round_ring(canvas, &ring, 7, 0x1f000000u);
    /* the foot: the key's own last rows, two lower */
    for (int i = 0; i < 2; ++i) round_row(canvas, &down, 6, kh - 2 + i, 0xcc282b30u);
    paint_round_rows(canvas, &r, action ? &enter : fn ? &function : &plain, 6);
    const unsigned *s = action ? done : space ? space_text : widget_get_text(w);
    if (s && *s) {
        unsigned n = wide_len(s);
        int px = white || space ? 24 : 27, fpx = px;
        const char *face = theme_face(s, (int)n, &fpx);
        rect_t under = { 0, white ? -1 : 1, kw, kh };
        final_text(canvas, face, s, &under, (unsigned)fpx, white ? 0x77000000u : 0xffffffffu);
        final_text(canvas, face, s, &r, (unsigned)fpx,
                   white   ? 0xffffffffu
                   : space ? 0xff555555u
                           : 0xff111111u);
    } else {
        paint_art(w, canvas, c == 'k' ? "classic_light_kb_del" : "classic_light_kb_shift",
                  (kw - 22) / 2, (kh - 18) / 2);
    }
    stock_canvas_set_fill_color(canvas, fill);
}
/* The field's image: 15 px caps round a stretched column, the row under it included. */
static void paint_field_plain(void *w, void *canvas, int x, int y, int fw) {
    unsigned bitmap[64]; /* bitmap_t */
    if (widget_load_image(w, "classic_light_field", bitmap)) return;
    rect_t left = { 0, 0, 15, 31 }, mid = { 15, 0, 2, 31 }, right = { 17, 0, 15, 31 };
    rect_t to_left = { x, y, 15, 31 }, to_mid = { x + 15, y, fw - 30, 31 },
           to_right = { x + fw - 15, y, 15, 31 };
    canvas_draw_image(canvas, bitmap, &left, &to_left);
    canvas_draw_image(canvas, bitmap, &mid, &to_mid);
    canvas_draw_image(canvas, bitmap, &right, &to_right);
}
/* ... with the glass in it. */
void paint_field(void *w, void *canvas, int x, int y, int fw) {
    paint_field_plain(w, canvas, x, y, fw);
    paint_art(w, canvas, "classic_light_field_glass", x + 10, y + 7);
}
/* Stock's search_edit is disabled, so it paints in that state, hint and query alike: the hint's
 * size (search_layout's) only while it's empty. Written once per change: this is on the paint. */
static void search_hint(void *win) {
    void *edit = widget_lookup(win, "search_edit", 1);
    const unsigned *text = edit ? widget_get_text(edit) : (void *)0;
    int px = text && *text ? 26 : st.kb_win ? 14 : 19;
    char key[STYLE_KEY];
    if (!edit || px == cp.search_px) return;
    cp.search_px = px;
    widget_set_prop_int(edit, style_key(key, "disable", "font_size"), px);
}
/* The search field's box and its children's: over the keyboard with the keyboard up, in the strip
 * with it down, where stock's toolbar hides and only the Return key goes back. The toolbar's return
 * is the chevron's tap. */
static void search_layout(void *win, int keyboard) {
    cp.search_px = 0; /* search_hint's size follows the field */
    void *nav = widget_lookup(win, "view_navbar", 1),
         *item = widget_lookup(win, "list_item_search", 1),
         *edit = widget_lookup(win, "search_edit", 1), *glass = widget_lookup(win, "img_search", 1);
    int fw = keyboard ? 244 : 327;
    if (nav) {
        widget_set_visible(nav, keyboard, 0);
        widget_set_enable(nav, keyboard);
    }
    if (item) widget_move_resize(item, keyboard ? 72 : 24, keyboard ? 15 : 33, fw, 30);
    if (edit) {
        /* Stock's hint, wider than either field at the typed text's 26: an empty field's own size,
         * for its 38 characters in stock's face, 14.8 px across per px, in the fw - 32 left. */
        static const char *const empty[] = { "empty", "empty_focus", "empty_over" };
        char key[STYLE_KEY];
        widget_move_resize(edit, 0, 0, fw, 30);
        for (unsigned k = 0; k < sizeof empty / sizeof *empty; ++k)
            widget_set_prop_int(edit, style_key(key, empty[k], "font_size"), keyboard ? 14 : 19);
    }
    if (glass) widget_move_resize(glass, 0, 0, 32, 30);
    widget_invalidate_force(win, (void *)0);
}
static int kb_gone(void *win, void *event) {
    if (win != st.kb_win) return 0;
    st.kb_win = (void *)0;
    if (st.search_win) search_layout(st.search_win, 0);
    return 0;
}
void kb_open(void *win) {
    static const char *const state[] = { "normal", "disable", "focused" };
    char key[STYLE_KEY];
    void *cand = widget_lookup(win, "candidates", 1);
    for (unsigned i = 0; cand && i < sizeof state / sizeof *state; ++i)
        widget_set_prop_int(cand, style_key(key, state[i], "bg_color"), 0);
    st.kb_win = win;
    widget_on(win, EVT_DESTROY, kb_gone, win);
    if (st.search_win) search_layout(st.search_win, 1);
}
static int search_gone(void *win, void *event) {
    if (win == st.search_win) st.search_win = st.search_tabs = (void *)0;
    return 0;
}
/* An edit clear, in the field's dark text, over paint_field's image, its text margin in from it. */
static void search_field_margin(void *edit, int margin) {
    static const char *const edit_state[] = { "normal", "focused",    "disable",
                                              "error",  "empty",      "empty_focus",
                                              "over",   "empty_over", "changed" };
    char key[STYLE_KEY];
    for (unsigned k = 0; k < sizeof edit_state / sizeof *edit_state; ++k) {
        widget_set_prop_int(edit, style_key(key, edit_state[k], "bg_color"), 0);
        widget_set_prop_int(edit, style_key(key, edit_state[k], "round_radius"), 0);
        widget_set_prop_int(edit, style_key(key, edit_state[k], "margin_left"), margin);
        widget_set_prop_int(edit, style_key(key, edit_state[k], "margin_top"), 0);
        widget_set_prop_int(edit, style_key(key, edit_state[k], "margin_bottom"), 0);
        widget_set_prop_int(edit, style_key(key, edit_state[k], "font_size"), 26);
    }
    text_ink(edit, 0xff111111u, INK_EDIT);
}
static void search_field_style(void *edit) { search_field_margin(edit, 32); }
/* A hint too wide for the room at the typed text's 26 takes a size of its own; none under 12, the
 * smallest worth reading, however long a playlist's name.
 * ponytail: stock's face averages 0.39 px a character per px; a hint of wide letters can still cut.
 */
static void field_tips(void *edit, const char *tips, int room) {
    static const char *const empty[] = { "empty", "empty_focus", "empty_over" };
    char key[STYLE_KEY];
    int fit = room * 100 / (39 * (int)strlen(tips) + 1);
    if (fit < 12) fit = 12;
    widget_set_prop_str(edit, "tips", tips);
    for (unsigned k = 0; fit < 26 && k < sizeof empty / sizeof *empty; ++k)
        widget_set_prop_int(edit, style_key(key, empty[k], "font_size"), fit);
}
static int t9_gone(void *ctx, void *event) {
    st.t9_page = st.t9_back = (void *)0;
    return 0;
}
/* A search box alone on a page (peq.h edit_create), its keyboard about to open, with its hint;
 * under Classic Light, the keyboard's look. The page stays under the bar, unlike Search's
 * full-screen dialog, so the dark bar fills the strip between them and the bar says Search. The
 * box's foot clears T9's top, page y 30, so the keyboard doesn't move the page. Its hint has the
 * 212 px past the glass, as Search's does.
 */
void t9_box(void *page, void *edit, const char *tips) {
    widget_set_prop_str(edit, "tips", tips);
    if (!classic_pages()) return;
    void *title = text(page, 0, 0, 375, 28);
    widget_set_text_utf8(title, "Search");
    widget_set_name(title, PAGE_TITLE);
    widget_set_visible(title, 0, 0);
    st.t9_back = image_create(page, 30, 0, 40, 30); /* ringnav_touch takes its press */
    widget_move_resize(edit, 72, 2, 244, 27);
    search_field_style(edit);
    field_tips(edit, tips, 212);
    widget_on(edit, EVT_DESTROY, t9_gone, 0);
    st.t9_page = page;
}
/* New Playlist and Rename, stock's dialogs as they load under Classic: the keyboard's page under
 * the status bar, which says what's asked, the white chevron over stock's Return, a field with no
 * glass, its hint a page's muted. Every name stock looks up stays. */
static int pl_box_gone(void *win, void *event) {
    if (win == st.pl_box) st.pl_box = (void *)0;
    return 0;
}
/* Next pass: stock's init, which writes its own hint, has returned; the bar title stays until the
 * dialog goes, as bar_title_sync doesn't name a dialog. */
static int pl_box_due(const void *info) {
    (void)info;
    static const char *const empty[] = { "empty", "empty_focus", "empty_over" };
    char key[STYLE_KEY];
    void *win = st.pl_box, *lists = P(p_deque_playlist, 0);
    if (!win) return 0;
    void *edit = widget_lookup(win, "ssedit", 1), *r = (void *)0;
    const char *title = "New Playlist", *tips = "Playlist Name";
    if (!edit) {
        edit = widget_lookup(win, "edit", 1);
        title = "Rename";
        if (lists && (unsigned)I(PL_RENAME_ROW, 0) < deque_size(lists))
            r = deque_at(lists, (unsigned)I(PL_RENAME_ROW, 0));
        tips = r ? P(r, REC_TITLE) ?: title : title;
    }
    if (edit) {
        field_tips(edit, tips, 214);
        for (unsigned k = 0; k < sizeof empty / sizeof *empty; ++k)
            widget_set_prop_int(edit, style_key(key, empty[k], "tips_text_color"),
                                (int)RGBA(flip_if(ipod_light(), 0x6b6d6b)));
    }
    if (st.bar_title) {
        widget_set_text_utf8(st.bar_title, title);
        theme_font(st.bar_title, ROLE_BAR, FONT_ROW);
        widget_invalidate_force(st.bar_title, (void *)0);
    }
    return 0;
}
void pl_box_open(void *win) {
    static const char *const state[] = { "normal", "pressed", "over", "disable", "focused" };
    static const char *const prop_name[] = { "bg_color", "border_color", "round_radius" };
    char key[STYLE_KEY];
    void *item =
             widget_lookup(win, "list_item_ssedit", 1) ?: widget_lookup(win, "list_item_edit", 1),
         *edit = widget_lookup(win, "ssedit", 1) ?: widget_lookup(win, "edit", 1),
         *back = widget_lookup(win, "img_return", 1);
    if (!item || !edit || !back) return;
    for (unsigned s = 0; s < sizeof state / sizeof *state; ++s)
        for (unsigned k = 0; k < sizeof prop_name / sizeof *prop_name; ++k)
            widget_set_prop_int(item, style_key(key, state[s], prop_name[k]), 0);
    widget_move_resize(item, 72, 28, 244, 30);
    widget_move_resize(edit, 0, 0, 244, 30);
    search_field_margin(edit, 15);
    widget_use_style(back, "default");
    widget_move_resize(back, 30, 26, 42, 34);
    st.pl_box = win;
    widget_on(win, EVT_DESTROY, pl_box_gone, win);
    timer_add(pl_box_due, (void *)0, 0);
}
/* The dialog's own paint, under its children; the bar goes after the page, as Search's. */
void pl_box_paint(void *w, void *canvas) {
    void *lcd = P(canvas, CANVAS_LCD), *bar = *(void *const *)system_bar;
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    for (int y = 0; y < 60; ++y)
        bar_rect(canvas, RGBA(mix(0xb4bdc9, 0x8b93a0, y, 60)), 0, y, 375, 1);
    paint_ramp(canvas, 60, 320, 0);
    paint_art(w, canvas, "classic_light_kb_back", 43, 33);
    paint_field_plain(w, canvas, 72, 28, 244);
    if (bar) widget_paint(bar, canvas);
    st.paint_w = w;
    stock_canvas_set_fill_color(canvas, fill);
}
/* The history's top under Classic: the field (33 to 64), the strip's 7 rows above it and below. */
#define SEARCH_HISTORY_Y 71
/* The dialog as it loads, before its init: every name stock looks up stays. It covers the bar, so
 * it paints the bar (search_paint); it's at 0, 0, so these boxes are the screen's. */
void search_open(void *win) {
    static const char *const tab_state[] = { "normal",
                                             "pressed",
                                             "over",
                                             "disable",
                                             "focused",
                                             "normal_of_active",
                                             "pressed_of_active",
                                             "over_of_active",
                                             "disable_of_active",
                                             "focused_of_active" },
                             *const tabs[] = { "btn_track", "btn_artist", "btn_album",
                                               "btn_genre" },
                             *const lists[] = { "scroll_view_track", "scroll_view_artist",
                                                "scroll_view_album", "scroll_view_genre" },
                             *const messages[] = { "label_track_msg", "label_artist_msg",
                                                   "label_album_msg", "label_genre_msg",
                                                   "label_tip" },
                             *const label_state[] = { "normal", "disable", "focused" },
                             *const full[] = { "view_album", "view_genre" };
    char key[STYLE_KEY];
    void *c, *control = widget_lookup(win, "tab_control", 1),
             *group = widget_lookup(win, "tab_button_group", 1),
             *pages = widget_lookup(win, "pages", 1);
    if (!control || !group || !pages) return;
    /* tab_control stays under the field, whose taps stock binds */
    widget_move_resize(control, 0, 64, 375, 256);
    widget_move_resize(group, 24, 7, 327, 30);
    widget_set_self_layout_params(pages, "0", "44", "375", "212");
    widget_move_resize(pages, 0, 44, 375, 212);
    for (unsigned i = 0; i < sizeof full / sizeof *full; ++i)
        if ((c = widget_lookup(win, full[i], 1))) widget_move_resize(c, 0, 0, 375, 212);
    for (unsigned i = 0; i < sizeof tabs / sizeof *tabs; ++i) {
        if (!(c = widget_lookup(win, tabs[i], 1))) continue;
        widget_move_resize(c, (int)i * 327 / 4, 0, (int)(i + 1) * 327 / 4 - (int)i * 327 / 4, 30);
        for (unsigned k = 0; k < sizeof tab_state / sizeof *tab_state; ++k) {
            widget_set_prop_int(c, style_key(key, tab_state[k], "bg_color"), 0);
            widget_set_prop_int(c, style_key(key, tab_state[k], "font_size"), 21);
            widget_set_prop_str(c, style_key(key, tab_state[k], "font_name"), "classic-light");
        }
        text_ink(c, 0xffffffffu, INK_TAB);
    }
    if ((c = widget_lookup(win, "search_edit", 1))) search_field_style(c);
    if ((c = widget_lookup(win, "img_search", 1))) image_base_set_image(c, "");
    if ((c = widget_lookup(win, "img_return", 1))) { /* the chevron's tap: the ramp draws it */
        widget_use_style(c, "default");
        widget_move_resize(c, 30, 10, 40, 40);
    }
    for (unsigned i = 0; i < sizeof lists / sizeof *lists; ++i)
        if ((c = widget_lookup(win, lists[i], 1)))
            widget_set_prop_int(P(c, W_PARENT), "default_item_height", 52);
    for (unsigned i = 0; i < sizeof messages / sizeof *messages; ++i)
        if ((c = widget_lookup(win, messages[i], 1)))
            for (unsigned k = 0; k < sizeof label_state / sizeof *label_state; ++k)
                widget_set_prop_int(c, style_key(key, label_state[k], "font_size"), 15);
    /* The history (its header, then the list where the rows go) and its tip show only while the
     * tabs don't (search_results), so they sit in the tabs' place, right under the field's strip.
     * Stock never moves them, and neither has a self_layout to put them back. */
    if ((c = widget_lookup(win, "view", 1))) {
        void *list = widget_lookup(c, "list_view", 1);
        widget_move_resize(c, 0, SEARCH_HISTORY_Y, 375, 320 - SEARCH_HISTORY_Y);
        if (list) widget_resize(list, 375, 320 - SEARCH_HISTORY_Y - 50);
    }
    if ((c = widget_lookup(win, "view_tip", 1)))
        widget_move_resize(c, 0, SEARCH_HISTORY_Y, 375, 320 - SEARCH_HISTORY_Y);
    st.search_win = win;
    st.search_tabs = group;
    widget_on(win, EVT_DESTROY, search_gone, win);
    search_layout(win, 0);
}
/* Stock shows the tabs (tab_control, the group's parent) with results, else the history or its
 * tip (0x49cf18, 0x49ee2c). The strip, the field's and the tabs' with results, ends at 108; with
 * the history, the field's alone, SEARCH_HISTORY_Y, as far under the field as it starts above. */
static int search_strip_end(void) {
    return st.search_tabs && widget_get_visible(P(st.search_tabs, W_PARENT)) ? 108
                                                                             : SEARCH_HISTORY_Y;
}
/* The dialog's own paint, under its children: the dark bar's rows behind the field. The bar goes
 * first: the window manager paints a dialog over it. With the keyboard up there's no bar. */
void search_paint(void *w, void *canvas) {
    void *lcd = P(canvas, CANVAS_LCD), *bar = *(void *const *)system_bar;
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    search_hint(w);
    if (st.kb_win) {
        dark_bar_rows(canvas, 0, 375, 60);
        paint_art(w, canvas, "classic_light_kb_back", 42, 18);
        paint_field(w, canvas, 72, 15, 244);
    } else {
        dark_bar_rows(canvas, 26, 375, search_strip_end() - 26);
        paint_field(w, canvas, 24, 33, 327);
        if (bar) widget_paint(bar, canvas);
        st.paint_w = w;
    }
    stock_canvas_set_fill_color(canvas, fill);
}
/* Stock's init names the tabs from its translations (its widget_foreach's visitor, 0x49c26c:
 * search_track, songer, album, genre), which other pages' words share; the bin's words go back on
 * under the theme's dialog. */
extern int stock_search_init_trampoline(void *win, void *ctx);
int ringnav_search_init(void *win, void *ctx) {
    static const __WCHAR_TYPE__ names[4][8] = { L"Songs", L"Artists", L"Albums", L"Genres" };
    int result = stock_search_init_trampoline(win, ctx);
    if (win == st.search_win)
        for (unsigned i = 0, n = widget_count_children(st.search_tabs); i < n && i < 4; ++i)
            widget_set_text(widget_get_child(st.search_tabs, i), (const unsigned *)names[i]);
    return result;
}
/* The history's delete buttons, stock's: each row's X (s_btn_cancel_navbar) and the header's trash
 * (s_btn_delete_navbar). Their styles' opaque black border, unseen on stock's black, turns white in
 * Light Mode, a frame broken at its round corners over the selection: neither paints its border
 * (ringnav_paint). 1 the X, 2 the trash, else 0. */
int search_del(void *w) {
    if (!w || !st.search_win || window_of(w) != st.search_win) return 0;
    const char *s = widget_get_prop_str(w, "style", "");
    return !tk_strcmp(s, "s_btn_cancel_navbar") ? 1 : !tk_strcmp(s, "s_btn_delete_navbar") ? 2 : 0;
}
/* The X in stock's image's place, the theme's own mark as paint_tick's: 3 px runs, 15 x 13, about a
 * row label's cap height, in the selection's text on the selected row, else the ink. */
void search_x(void *w, void *canvas) {
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd) return;
    const scheme_t *S = scheme();
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    int x = I(w, W_W) / 2 - 7, y = I(w, W_H) / 2 - 6;
    stock_canvas_set_fill_color(canvas, RGBA(painting_in(st.sel_accent) ? S->sel_text : S->ink));
    for (int r = 0; r < 13; ++r) {
        canvas_fill_rect(canvas, x + r, y + r, 3, 1);
        if (r != 6) canvas_fill_rect(canvas, x + 12 - r, y + r, 3, 1);
    }
    stock_canvas_set_fill_color(canvas, fill);
}
/* The strip's row below it, over the rows. */
void search_over(void *w, void *canvas) {
    if (!w || w != st.search_win || st.kb_win || !P(canvas, CANVAS_LCD)) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    scrub_row(canvas, 0x55000000u, 0, search_strip_end(), 375);
    stock_canvas_set_fill_color(canvas, fill);
}
/* The tabs, in the group's coordinates: the choice control's track, the lit tab, the labels'
 * shadows under stock's labels. */
void search_tabs_paint(void *group, void *canvas) {
    static const stops_t light = { 4,
                                   { 0, 35, 70, 100 },
                                   { 0x7b90a9, 0x8597ae, 0x8a9bb1, 0x8e9fb4 } };
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    int paper = ipod_light();
    rect_t track = { 0, 0, 327, 30 };
    row_below(canvas, &track, 6, paper ? 0xb0ffffffu : 0x29ffffffu);
    paint_round_rows(canvas, &track, paper ? &light : &confirm_dark, 6);
    if (paper)
        round_row(canvas, &track, 6, 0, 0xff647ca0u);
    else
        inset_top(canvas, &track, 6, 0xaa);
    round_ring(canvas, &track, 6, paper ? 0x1a000000u : 0x88000000u);
    for (unsigned i = 0, n = widget_count_children(group); i < n && i < 4; ++i) {
        void *tab = widget_get_child(group, i);
        int x = (int)i * 327 / 4, w = (int)(i + 1) * 327 / 4 - x, fpx = 21;
        const unsigned *s = widget_get_text(tab);
        /* the lit tab fills its slot: rounded as the track at the track's ends, square beside
         * the other tabs, so no track shows between them, and over the light row below the
         * track, which under it read as a gap */
        if (widget_get_prop_int(tab, "value", 0))
            lit_rows(canvas, &(rect_t){ x, 0, w, 31 },
                     (i ? 0 : ROUND_LEFT) | (i + 1 < n && i < 3 ? 0 : ROUND_RIGHT));
        rect_t under = { x, -1, w, 30 };
        text_shadow(canvas, theme_face(s, (int)wide_len(s), &fpx), s, &under, (unsigned)fpx, 0x4d);
    }
    stock_canvas_set_fill_color(canvas, fill);
}

/* Classic's slider pages, one shape over stock's four windows: a dark bar (dark_bar_rows),
 * the page image, a band holding the slider between its ends, the caption. Only the painter reads
 * these tables. */
static const stops_t slider_band = { 8,
                                     { 0, 8, 22, 38, 49, 50, 92, 100 },
                                     { 0x6a707a, 0x565c66, 0x464b54, 0x343840, 0x23262d, 0x0e1014,
                                       0x0b0d11, 0x181b21 } },
                     slider_track = { 4,
                                      { 0, 40, 80, 100 },
                                      { 0x2f4052, 0x42566a, 0x3b4d60, 0x26333f } },
                     slider_lit = { 3, { 0, 45, 100 }, { 0xf7ffff, 0xeceeef, 0xc6cbcf } };
/* The page, in the window's coordinates, under its children. */
void slider_paint(void *w, void *canvas) {
    void *lcd = P(canvas, CANVAS_LCD), *s = st.slider_w, *sw = st.slider_sw;
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    int maxvol = st.slider_kind == SLIDER_MAX, px;
    void *number = widget_lookup(w, "label_balance", 1); /* stock writes its text after the load */
    if (number && number_face(number, &px) != st.number_face) slider_number(number);
    if (!sw && st.slider_kind == SLIDER_BOOT)
        sw = st.slider_sw = widget_lookup(w, "img_bootswitch", 1);
    paint_page(w, canvas, 0);
    if (sw && widget_get_visible(sw)) slider_switch(w, canvas, sw);
    if (widget_get_visible(
            P(s, W_PARENT))) { /* stock hides it while Default Volume's switch is off */
        rect_t band = { 28, 129, 322, 46 };
        paint_art(w, canvas, "classic_light_band_shadow", 21, 124);
        paint_round_rows(canvas, &band, &slider_band, 8);
        paint_art(w, canvas, "classic_light_band_top", 28, 129);
        paint_art(w, canvas, "classic_light_band_foot", 28, 167);
        if (maxvol || (st.slider_kind == SLIDER_BOOT && !xfade_page(w))) {
            paint_art(w, canvas, "classic_light_spk_quiet_w", 50, maxvol ? 139 : 145);
            paint_art(w, canvas, "classic_light_spk_loud_w", 311, maxvol ? 139 : 145);
        } else if (st.slider_kind == SLIDER_LIGHT) {
            paint_art(w, canvas, "classic_light_sun_small", 48, 144);
            paint_art(w, canvas, "classic_light_sun_big", 311, 141);
        }
        for (int i = 0; maxvol && i < 6; ++i) { /* a triangle at the fill's end, tip up */
            int half = i ? i : 1;
            bar_rect(canvas, 0xffffffffu, 81 + slider_part(s, 204) - half, 155 + i, 2 * half, 1);
        }
    }
    text_shadow(canvas, "classic-light", (const unsigned *)st.slider_hint,
                &(rect_t){ 0, 229, 375, 32 }, 31, 0x59);
    stock_canvas_set_fill_color(canvas, fill);
}
/* ringnav_paint, after stock's slider: the bar's 8 rows over its own, inset a pixel at each end on
 * the first and last. */
void slider_scrub(void *w, void *canvas) {
    if (!w || w != st.slider_w || !P(canvas, CANVAS_LCD)) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    int ww = I(w, W_W), part = slider_part(w, ww);
    for (int j = 0; j < 8; ++j) {
        int in = !j || j == 7;
        bar_rect(canvas, RGBA(stops_row(&slider_track, j, 8)), in, 31 + j, ww - 2 * in, 1);
    }
    for (int j = 0; part > 2 && j < 8; ++j) {
        int in = !j || j == 7;
        bar_rect(canvas, RGBA(stops_row(&slider_lit, j, 8)), in, 31 + j, part - 2 * in, 1);
    }
    stock_canvas_set_fill_color(canvas, fill);
}

/* Classic's message screens, over stock's two windows: Update Local Music's dialog
 * (full-screen, so it paints its own bar) and USB mode's page. Screen rows; each window's own
 * origin is read when it paints. Only the painter reads these tables. */
#define MSG_BAR_X 72
#define MSG_BAR_Y 218
#define MSG_BAR_W 229
#define MSG_BAR_H 14
static const stops_t msg_fill = { 12,
                                  { 0, 8, 16, 25, 33, 42, 50, 58, 67, 75, 83, 100 },
                                  { 0xf7ffff, 0xfcfdff, 0xf4f4f4, 0xedebec, 0xeae8e9, 0xe2e1df,
                                    0xd5d4d2, 0xcbcac8, 0xc8c7c5, 0xc0c0c0, 0xbebfc1, 0xbdc1c4 } },
                     msg_cancel = { 3, { 0, 30, 100 }, { 0x232c33, 0x3a434a, 0x333c43 } };
static const unsigned char msg_track[MSG_BAR_H] = { 0x87, 0x5c, 0x4d, 0x3d, 0x3d, 0x3b, 0x3b,
                                                    0x3b, 0x3b, 0x3b, 0x3b, 0x3b, 0x54, 0x54 };
static int msg_gone(void *win, void *event) {
    if (win == st.usb_win) st.usb_win = (void *)0;
    if (win == st.msg_win) {
        stop_timer(&cp.msg_timer);
        st.msg_win = (void *)0;
    }
    return 0;
}
/* Follows stock's label_msg, which it writes from the running count in the language's format, one
 * number, and which is hidden here: "312 of 704 songs" and the bar's part, held under its end until
 * the dialog closes. With no total to measure against (the first scan, a scan past it, a folder's
 * scan, or until msg_count answers) the line is stock's text and cp.msg_part -1 hides the bar.
 * Whether anything changed. */
static int msg_sync(void) {
    char now[sizeof cp.msg_seen], line[48];
    *now = 0;
    int answer = cp.msg_counting;
    if (answer > 1) { /* the total: read stock's text against it */
        cp.msg_total = answer - 3, cp.msg_counting = 0, cp.msg_seen[0] = 0;
    }
    if (!cp.msg_seen[0]) { /* over stock's translation; runs in paints, so only if it differs */
        widget_get_text_utf8(cp.msg_btn, now, sizeof now);
        if (tk_strcmp(now, "Cancel")) widget_set_text_utf8(cp.msg_btn, "Cancel");
        *now = 0;
    }
    widget_get_text_utf8(cp.msg_src, now, sizeof now);
    if (!tk_strcmp(now, cp.msg_seen)) return 0;
    tk_snprintf(cp.msg_seen, sizeof cp.msg_seen, "%s", now);
    const char *p = now;
    while (*p && (*p < '0' || *p > '9')) ++p;
    int count = atoi(p), total = cp.msg_total;
    cp.msg_part = -1;
    if (*p && total && count <= total && *(volatile char *)SCAN_WHOLE == 1) {
        cp.msg_part = (short)(MSG_BAR_W * count / total);
        if (cp.msg_part > MSG_BAR_W * 99 / 100) cp.msg_part = MSG_BAR_W * 99 / 100;
        tk_snprintf(line, sizeof line, "%d of %d songs", count, total);
        widget_set_text_utf8(cp.msg_line, line);
    } else
        widget_set_text_utf8(cp.msg_line, now);
    return 1;
}
/* Stock's hidden label repaints nothing, so this does, at the rate stock writes it. */
static int msg_poll(const void *info) {
    (void)info;
    if (msg_sync()) widget_invalidate_force(st.msg_win, &(rect_t){ 16, 188, 343, 45 });
    return 8; /* RET_REPEAT */
}
int library_count(const char *sql);
/* The songs before the scan, off the UI thread as coverflow.c's count_worker: a count, as a
 * 47,000-song library's rows are too many to hold beside the scan. Detached, as nothing may wait
 * for it when the dialog closes; msg_sync takes the answer. */
static void *msg_count(void *unused) {
    cp.msg_counting = 3 + library_count("select count(*) from songtable");
    return 0;
}
int msg_counting(void) { return cp.msg_counting == 1; } /* coverflow.c's library_counting */
#define MSG_HEAD_PX 29
#define MSG_HEAD_H 32 /* holds the font */
#define MSG_LINE_PX 18
/* Stock's window as it loads, before its init. Every look-up stock makes by name still finds its
 * widget, and btn_no keeps its binding. A cable can open USB's page over the scan's dialog, so each
 * has its own st. */
void msg_open(void *win) {
    static const char *const hide[] = { "img_animation", "label_msg", "label_scanfolder",
                                        "img_usbmode", "scrlabel_info" },
                             *const state[] = { "normal", "pressed", "over", "disable", "focused" };
    const char *name = widget_get_prop_str(win, "name", "");
    char key[STYLE_KEY];
    int scan = !tk_strcmp(name, "updatemusic_dialog");
    void *c, *src = widget_lookup(win, "label_msg", 1), *btn = widget_lookup(win, "btn_no", 1);
    if (scan ? !src || !btn : tk_strcmp(name, "usbmode_page") != 0) return;
    for (unsigned i = 0; i < sizeof hide / sizeof *hide; ++i)
        if ((c = widget_lookup(win, hide[i], 1))) widget_set_visible(c, 0, 0);
    widget_on(win, EVT_DESTROY, msg_gone, win);
    c = text(win, 16, 0, 343, 24);
    widget_set_name(c, "msg_heading");
    widget_set_text_utf8(c, scan ? "Updating Local Music" : "Connected");
    slider_label(c, 16, 0, 343, MSG_HEAD_H, MSG_HEAD_PX, "classic-light");
    c = text(win, 16, 0, 343, 20);
    widget_set_name(c, "msg_line");
    slider_label(c, 16, 0, 343, 20, MSG_LINE_PX, "default");
    if (!scan) {
        st.usb_win = win;
        if (g_usbmode != 1) widget_set_text_utf8(c, "Eject before disconnecting");
        return;
    }
    st.msg_win = win, cp.msg_src = src, cp.msg_btn = btn, cp.msg_line = c;
    widget_move_resize(btn, 127, 248, 121, 34);
    for (unsigned i = 0; i < sizeof state / sizeof *state; ++i) {
        widget_set_prop_int(btn, style_key(key, state[i], "bg_color"), 0);
        widget_set_prop_str(btn, style_key(key, state[i], "font_name"), "classic-light");
        widget_set_prop_int(btn, style_key(key, state[i], "font_size"), CONFIRM_CELL_PX);
    }
    text_ink(btn, 0xffffffffu, INK_BUTTON);
    cp.msg_seen[0] = 0, cp.msg_part = -1, cp.msg_total = 0;
    if (cp.msg_counting != 1) { /* one running answers this dialog too */
        unsigned long thread;
        cp.msg_counting = 1; /* before it starts: it may answer before create returns */
        if (pthread_create(&thread, 0, msg_count, 0))
            cp.msg_counting = 0;
        else
            pthread_detach(thread);
    }
    cp.msg_timer = timer_add(msg_poll, (void *)0, 200);
}
/* Under the window's children. */
void msg_paint(void *w, void *canvas) {
    static const unsigned update[] = { 'U', 'p', 'd', 'a', 't', 'e', ' ', 'L',
                                       'i', 'b', 'r', 'a', 'r', 'y', 0 },
                          q2pod[] = { 'Q', '2', ' ', 'P', 'o', 'd', 0 };
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    int scan = w == st.msg_win, dy = I(w, W_Y), head = scan ? 159 : 228, line = scan ? 188 : 255;
    void *heading = widget_lookup(w, "msg_heading", 1), *below = widget_lookup(w, "msg_line", 1);
    if (scan) msg_sync();
    bar_place(heading, 16, head, 343, MSG_HEAD_H);
    bar_place(below, 16, line, 343, 20);
    paint_page(w, canvas, 26 - dy);
    if (dy < 26) paint_dark_bar(w, canvas, scan ? update : q2pod, st.batt_key);
    paint_art(w, canvas, scan ? "classic_light_orb_note" : "classic_light_orb_usb",
              scan ? 129 : 122, (scan ? 38 : 67) - dy);
    text_shadow(canvas, "classic-light", widget_get_text(heading),
                &(rect_t){ 16, head + 1 - dy, 343, MSG_HEAD_H }, MSG_HEAD_PX, 0x59);
    text_shadow(canvas, "default", widget_get_text(below), &(rect_t){ 16, line + 1 - dy, 343, 20 },
                MSG_LINE_PX, 0x59);
    if (scan) {
        rect_t cancel = { 127, 248, 121, 34 };
        /* the track's corners clip the fill, whose moving end is square */
        for (int i = 0; cp.msg_part >= 0 && i < MSG_BAR_H; ++i) {
            int n = round_inset(7, i, MSG_BAR_H), x = MSG_BAR_X + n, y = MSG_BAR_Y + i,
                end = MSG_BAR_X + MSG_BAR_W - n, to = MSG_BAR_X + cp.msg_part;
            scrub_row(canvas, (unsigned)msg_track[i] << 24, x, y, end - x);
            if (to > end) to = end;
            if (to > x) bar_rect(canvas, RGBA(stops_row(&msg_fill, i, MSG_BAR_H)), x, y, to - x, 1);
        }
        if (cp.msg_part >= 0)
            scrub_row(canvas, 0x14ffffffu, MSG_BAR_X + 7, MSG_BAR_Y + MSG_BAR_H, MSG_BAR_W - 14);
        paint_round_rows(canvas, &cancel, &msg_cancel, 7);
        row_below(canvas, &cancel, 7, 0x26ffffffu);
    }
    stock_canvas_set_fill_color(canvas, fill);
}
