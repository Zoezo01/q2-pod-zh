/* Quick Settings, stock's pull-down: its holds (Wi-Fi's, Bluetooth's and the EQ's settings), the
 * key lock's button, the wheel's choice of brightness or volume, a stuck pull-down's release, and
 * the own pages' page, buttons and brightness slider: Classic's, and the flat look's. */
#include "navigation.h"
extern int stock_quicksettings_trampoline(void *, void *);

static void quick_lock_text(void *win) {
    widget_set_text_utf8(widget_lookup(win, "label_lock", 1), lock_names[lock_choice()]);
    image_base_set_image(widget_lookup(win, "img_lock", 1),
                         g_keylock_flag ? "drop_keylockopen" : "drop_keylock");
}
int ringnav_quick_lock(void *win, void *event) {
    lock_set((lock_choice() + 1) % 4);
    quick_lock_text(win);
    return 0;
}
#define QUICK_HOLD "_quick_hold"
#define SLIDE_PX 10 /* the brightness slider's sideways move before it takes the touch */
static struct {
    void *win, *icon, *slider, *pressed; /* pressed: Classic's button under the finger */
    unsigned timer;
    int x, y, sx, sy, slide; /* slide: the slider's touch, 0 undecided, 1 its own, -1 the swipe's */
    int wheel;               /* the wheel: 1 + Q2POD QSVOLUME once read (quick_volume) */
} quick __attribute__((section(".scratch")));

static int quick_cancel(void *ctx, void *event) {
    stop_timer(&quick.timer);
    quick.win = quick.icon = quick.slider = 0;
    if (ctx) quick.pressed = 0; /* the window's going */
    return 0;
}
static int quick_long(const void *unused) {
    quick.timer = 0;
    void *win = quick.win, *icon = quick.icon;
    if (!win || window_manager_get_top_window(window_manager()) != win || !pointer_down()) return 0;
    int page = widget_get_prop_int(icon, QUICK_HOLD, 0);
    /* Abort the image's press before closing, so its release cannot click the icon. */
    prop(icon, QUICK_HOLD, -page);
    unsigned abort_event[12];
    pointer_event_init(abort_event, EVT_POINTER_ABORT, win, 0, 0);
    widget_dispatch_event_to_target_recursive(win, abort_event);
    quick_cancel(0, 0);
    dialog_quit(win, 0);
    /* The EQ page as the top window, under the dialog, is already the goal. One further down
     * (Return's hold opened Now Playing over it, #397) closes and the new one opens over the page
     * on top, as from any other page: a second would share peq_ui.c's one state. Dialogs and the
     * status bar are no windows. */
    if (page == 3) {
        void *wm = window_manager(), *eq = 0;
        int top = 1;
        for (unsigned i = widget_count_children(wm); !eq && i-- > 0;) {
            void *w = widget_get_child(wm, i);
            if (tk_strcmp(widget_get_type(w), "window")) continue;
            if (!tk_strcmp(widget_get_prop_str(w, "name", ""), "equalizer_page"))
                eq = w;
            else
                top = 0;
        }
        if (eq && top) return 0;
        if (eq) peq_page_replace(eq);
    }
    navigator_to(page == 1   ? "systemset/wifi_page"
                 : page == 2 ? "systemset/bluetooth_page"
                             : "playset/equalizer_page"); /* as Playback's EQ row (0x4bd114) */
    return 0;
}
static void quick_press(void *icon) {
    if (quick.pressed) widget_invalidate_force(quick.pressed, (void *)0);
    if (icon) widget_invalidate_force(icon, (void *)0);
    quick.pressed = icon;
}
static int quick_down(void *icon, void *event) {
    int page = widget_get_prop_int(icon, QUICK_HOLD, 0);
    if (own_pages()) quick_press(icon);
    if (!page) return 0; /* a button with no hold */
    prop(icon, QUICK_HOLD, page < 0 ? -page : page);
    quick.win = window_manager_get_top_window(window_manager());
    quick.icon = icon;
    quick.x = I(event, EVENT_X), quick.y = I(event, EVENT_Y);
    rearm(&quick.timer, quick_long, RATE_HOLD_MS);
    return 0;
}
/* The own pages' page (quick_page, quick_scrub), in the window's
 * coordinates: each icon at opacity 0 keeps stock's image name, tap and hold, and paints nothing.
 */
static const char *const quick_icons[] = { "img_wifiswitch", "img_btswitch", "img_lock",
                                           "img_gain",       "img_usbmode",  "img_po",
                                           "img_playset",    "img_sysset" },
                         *const quick_labels[] = { "label_wifi",    "label_bt",
                                                   "label_lock",    "label_gain",
                                                   "label_usbmode", "label_outputway",
                                                   "label_playset", "label_sysset" };
/* The knob, or the flat bar's ends, reach past the slider, whose own repaint would leave them
 * behind. */
static int quick_moved(void *view, void *event) {
    widget_invalidate_force(view, (void *)0);
    return 0;
}
/* The flat look's brightness bar, in the window: its box, which the slider takes, its own 48 rows
 * about it. */
#define QS_BAR_X 33
#define QS_BAR_Y 239
#define QS_BAR_W 309
#define QS_BAR_H 36
const rect_t qs_bar_card = { 18, 226, 339, 62 }; /* the bar's card, round it */
static void quick_open(void *win) {
    int flat = flat_pages();
    void *c = widget_lookup(win, "view_menu", 1);
    /* Classic's to the slider's foot; the flat look's the buttons' card and the gap under it */
    if (c) widget_move_resize(c, 0, 0, I(c, W_W), flat ? 226 : I(c, W_Y) + I(c, W_H));
    /* [flat]: Classic's 60 px buttons 24 apart; the flat look's 54 px ones 31 apart, 15 in from
     * the card's sides */
    static const short column[2][4] = { { 31, 115, 199, 283 }, { 33, 118, 203, 288 } },
                       row[2][2] = { { 20, 138 }, { 34, 122 } }, side[2] = { 60, 54 };
    for (unsigned i = 0; i < sizeof quick_icons / sizeof *quick_icons; ++i) {
        if (!(c = widget_lookup(win, quick_icons[i], 1))) continue;
        widget_move_resize(c, column[flat][i % 4], row[flat][i >= 4], side[flat], side[flat]);
        widget_set_opacity(c, 0);
        if (!widget_get_prop_int(c, QUICK_HOLD, 0)) widget_on(c, EVT_POINTER_DOWN, quick_down, c);
    }
    if ((c = widget_lookup(win, "image0", 1))) widget_set_visible(c, 0, 0);
    if ((c = widget_lookup(win, "image1", 1))) widget_set_visible(c, 0, 0);
    if ((c = widget_lookup(win, "slider_backlight", 1))) {
        void *view = P(c, W_PARENT);
        if (flat) {
            widget_move_resize(view, 0, QS_BAR_Y + QS_BAR_H / 2 - 24, I(view, W_W), 48);
            widget_move_resize(c, QS_BAR_X, 0, QS_BAR_W, 48);
        }
        widget_on(c, EVT_VALUE_CHANGING, quick_moved, view);
        widget_on(c, EVT_VALUE_CHANGED, quick_moved, view);
    }
}
int ringnav_quick_settings(void *win, void *ctx) {
    int result = stock_quicksettings_trampoline(win, ctx);
    if (win) {
        quick_lock_text(win);
        /* held: 1 Wi-Fi's settings, 2 Bluetooth's, 3 the EQ page (Playback's) */
        static const char *const names[] = { "img_wifiswitch", "img_btswitch", "img_playset" };
        for (int i = 0; i < 3; ++i) {
            void *icon = widget_lookup(win, names[i], 1);
            if (!icon) continue;
            prop(icon, QUICK_HOLD, i + 1);
            widget_on(icon, EVT_POINTER_DOWN, quick_down, icon);
        }
        if (own_pages()) quick_open(win);
        widget_on(win, EVT_DESTROY, quick_cancel, win);
        widget_on(win, EVT_WINDOW_CLOSE, quick_cancel, win);
        widget_on(win, EVT_WINDOW_BACKGROUND, quick_cancel, win);
    }
    return result;
}

/* Quick Settings' brightness slider fills as Now Playing's bar does (np_fill), set before its
 * paint; QFILL holds the fill it last got, so a new accent or Light Mode reaches it. */
#define QFILL "_ringnav_fill"
static int quick_slider(void *w) {
    void *win = w && !tk_strcmp(widget_get_prop_str(w, "name", ""), "slider_backlight")
                    ? window_of(w)
                    : (void *)0;
    return win && !tk_strcmp(widget_get_prop_str(win, "name", ""), "statusbar_dialog");
}
void quick_fill(void *w) {
    if (!quick_slider(w)) return;
    /* the own pages' bar is quick_scrub's: stock's goes clear, and 1 says it went */
    int clear = own_pages();
    int color = clear ? 1 : (int)RGBA(accent_tone(TONE_LIGHT));
    if (widget_get_prop_int(w, QFILL, 0) == color) return;
    widget_set_prop_int(w, QFILL, color);
    slider_fill(w, clear ? 0 : (unsigned)color);
    if (clear) {
        static const char *const track[] = { "style:normal:bg_color", "style:pressed:bg_color",
                                             "style:over:bg_color", "style:focused:bg_color" };
        for (unsigned i = 0; i < sizeof track / sizeof *track; ++i)
            widget_set_prop_int(w, track[i], 0);
    }
}
#define QS_TEXT_W 80
#define QS_TEXT_PX 17
#define QS_TEXT_LINE 14 /* a line's pitch, tighter than a label's font and its 2 px spacer */
#define QS_TEXT_BOX 18  /* each line's box, holding the font */
/* One line of a label, white in x, y's box. */
static void quick_line(void *canvas, const unsigned *s, unsigned n, int x, int y) {
    rect_t r = { x, y, QS_TEXT_W, QS_TEXT_BOX };
    draw_centred(canvas, s, n, &r, QS_TEXT_PX, 0xffffffffu);
}
/* A label under its icon, x, y its first line's box. The page draws its lines: stock's label keeps
 * the text and, for its repaint, a box round them, with its own ink clear. */
static void quick_label(void *canvas, void *label, int x, int y) {
    const unsigned *s = widget_get_text(label);
    unsigned n = wide_len(s), cut = 0, k = 1, raw = st.raw_ink;
    if (!n) return;
    int px = QS_TEXT_PX;
    const char *face = theme_face(s, (int)n, &px);
    canvas_set_font(canvas, face, px);
    int wrap = canvas_measure_text(canvas, s, n) > QS_TEXT_W, h = QS_TEXT_BOX + wrap * QS_TEXT_LINE;
    /* a face is one of theme_face's literals: the payload's .rodata fits the low 24 bits */
    int key = h << 24 | (int)(long)face;
    if (widget_get_prop_int(label, "_ringnav_box", 0) != key) {
        widget_set_prop_int(label, "_ringnav_box", key);
        slider_label(label, x, y, QS_TEXT_W, h, px, face ? face : "default");
        widget_set_prop_int(label, "style:normal:text_color", 0);
    }
    for (; wrap && k < n && canvas_measure_text(canvas, s, k) <= QS_TEXT_W; ++k)
        if (s[k] == ' ') cut = k;
    if (!wrap) cut = n;
    if (!cut) cut = k > 1 ? k - 1 : 1;
    st.raw_ink = 1;
    quick_line(canvas, s, cut, x, y);
    if (wrap) {
        unsigned skip = cut + (s[cut] == ' ');
        quick_line(canvas, s + skip, n - skip, x, y + QS_TEXT_LINE);
    }
    st.raw_ink = raw;
}
/* Quick Settings' buttons under Classic, in final colours, on Home's pane in the half: each a 60 px
 * tile, corners of 12, a 1 px border round a fill: off in light a 2-stop vertical silver, lighter
 * at the top, in dark a flat #333333 (the dark bar's hairline) with no ring; on (QS_LIT,
 * tools/art.py) flat, Classic Blue's blue, or another accent's red tone as Minimal's lit discs
 * take it, its border a quarter darker in light.
 * [on][light]: the fill's top and foot, the border. */
static const unsigned qs_tile[2][2][3] = {
    { { 0x333333, 0x333333, 0x333333 }, { 0xe9ebee, 0xd3d6dc, 0x9ba2aa } },
    { { 0x207cde, 0x207cde, 0x207cde }, { 0x207cde, 0x207cde, 0x125cb0 } },
};
/* The page, in its window's coordinates: the ground, then each button by the image stock last gave
 * its icon, a sixth darker while pressed, its glyph in the tile's ink (white on, _l on a bright
 * accent or off in light, _d off in dark); and its label. */
static void quick_page_flat(void *win, void *canvas);
void quick_page(void *win, void *canvas) {
    if (flat_pages()) {
        quick_page_flat(win, canvas);
        return;
    }
    int light = ipod_light();
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    paint_stops(canvas, 0, 0, I(win, W_W), I(win, W_H), &theme()->home_pane[light]);
    for (unsigned i = 0; i < sizeof quick_icons / sizeof *quick_icons; ++i) {
        void *icon = widget_lookup(win, quick_icons[i], 1);
        void *label = widget_lookup(win, quick_labels[i], 1);
        if (!icon) continue;
        char name[40];
        const char *image = widget_get_prop_str(icon, "image", "");
        int on = named_in(QS_LIT, image);
        unsigned c[3] = { qs_tile[on][light][0], qs_tile[on][light][1], qs_tile[on][light][2] };
        if (on && !theme()->schemes[accent()].label) { /* labelled: the theme's own, Classic Blue */
            c[0] = c[1] = flip_if(light, accent_tone(TONE_RED));
            c[2] = light ? mix(c[0], 0, 1, 4) : c[0];
        }
        const char *ink = on ? bright_tone(c[0]) ? "_l" : "" : light ? "_l" : "_d";
        for (int k = 0; k < 3; ++k)
            if (icon == quick.pressed) c[k] = mix(c[k], 0, 1, 6);
        rect_t r = { I(icon, W_X), I(icon, W_Y), 60, 60 }, in = { r.x + 1, r.y + 1, 58, 58 };
        stops_t line = { 2, { 0, 100 }, { c[2], c[2] } }, face = { 2, { 0, 100 }, { c[0], c[1] } };
        paint_round_rows(canvas, &r, &line, 12);
        paint_round_rows(canvas, &in, &face, 11);
        tk_snprintf(name, sizeof name, "classic_light_%s%s", image, ink);
        paint_art(win, canvas, name, r.x, r.y);
        if (label) quick_label(canvas, label, r.x - 10, r.y + 64);
    }
    stock_canvas_set_fill_color(canvas, fill);
}
/* ringnav_paint, after stock's slider, whose bar quick_fill cleared: the suns, the track, the fill
 * (Classic Blue's own stops, another accent's light tone flat, as Minimal's fill) and the knob, in
 * the window's coordinates less the slider's. */
static void quick_scrub_flat(void *w, void *canvas);
void quick_scrub(void *w, void *canvas) {
    static const stops_t track = { 2, { 0, 100 }, { 0x0a0a0a, 0x3a3c40 } };
    static const stops_t blue = { 3, { 0, 50, 100 }, { 0x8cc4f8, 0x2f85df, 0x165fc0 } };
    if (!own_pages() || !quick_slider(w) || !P(canvas, CANVAS_LCD)) return;
    if (flat_pages()) {
        quick_scrub_flat(w, canvas);
        return;
    }
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    void *view = P(w, W_PARENT);
    int ox = -I(view, W_X) - I(w, W_X), oy = -I(view, W_Y) - I(w, W_Y);
    int min = widget_get_prop_int(w, "min", 0), max = widget_get_prop_int(w, "max", 100);
    int part =
        max > min
            ? 239 * clamp_step(0, max - min, widget_get_prop_int(w, "value", 0) - min) / (max - min)
            : 0;
    rect_t r = { 68 + ox, 275 + oy, 239, 11 };
    paint_art(w, canvas, "classic_light_sun_small", 35 + ox, 272 + oy);
    paint_art(w, canvas, "classic_light_sun_big", 321 + ox, 269 + oy);
    paint_round_rows(canvas, &r, &track, 6);
    paint_art(w, canvas, "classic_light_drop_track", r.x, r.y);
    r.w = part;
    stops_t lit = blue;
    if (!theme()->schemes[accent()].label) { /* labelled: the theme's own, Classic Blue */
        unsigned c = flip_if(ipod_light(), accent_tone(TONE_LIGHT));
        lit = (stops_t){ 2, { 0, 100 }, { c, c } };
    }
    if (part >= 12) paint_round_rows(canvas, &r, &lit, 6); /* under that the knob covers it */
    paint_art(w, canvas, "classic_light_drop_knob", r.x + part - 11 - 3, 269 - 3 + oy);
    stock_canvas_set_fill_color(canvas, fill);
}

/* Quick Settings in the flat look, in final colours, [0] dark, [1] light: the two cards on the
 * page's ground, and a button or the bar at rest on a card, a step lighter in the dark half than
 * flat_rest, the pages' rest on the bare paper, so it stands off the card's grey. */
static const unsigned qs_card[2] = { 0x1c1c1e, 0xffffff }, qs_rest[2] = { 0x3a3a3c, 0xe9e9ec };
#define QS_FLAT_W 86
#define QS_FLAT_PX 14
#define QS_FLAT_LINE 14 /* a line's pitch */
#define QS_FLAT_BOX 16  /* each line's box, holding the font */
/* One line of a label, the first n of s, quiet in x, y's box, in the system's face. final_text
 * takes a nul-ended string, hence the copy; its 31 characters are more than the 86 px box holds. */
static void quick_line_flat(void *canvas, const unsigned *s, unsigned n, int x, int y) {
    unsigned line[32];
    rect_t r = { x, y, QS_FLAT_W, QS_FLAT_BOX };
    if (n > sizeof line / sizeof *line - 1) n = sizeof line / sizeof *line - 1;
    for (unsigned i = 0; i < n; ++i) line[i] = s[i];
    line[n] = 0;
    final_text(canvas, (void *)0, line, &r, QS_FLAT_PX, 0xff000000u | flat_quiet[ipod_light()]);
}
/* quick_label's, flat: a label under its button, x, y its first line's box, a second line when it
 * is wider than the box. */
static void quick_label_flat(void *canvas, void *label, int x, int y) {
    const unsigned *s = widget_get_text(label);
    unsigned n = wide_len(s), cut = 0, k = 1;
    if (!n) return;
    canvas_set_font(canvas, (void *)0, QS_FLAT_PX);
    int wrap = canvas_measure_text(canvas, s, n) > QS_FLAT_W, h = QS_FLAT_BOX + wrap * QS_FLAT_LINE;
    if (widget_get_prop_int(label, "_ringnav_box", 0) != h) {
        widget_set_prop_int(label, "_ringnav_box", h);
        slider_label(label, x, y, QS_FLAT_W, h, QS_FLAT_PX, "default");
        widget_set_prop_int(label, "style:normal:text_color", 0);
    }
    for (; wrap && k < n && canvas_measure_text(canvas, s, k) <= QS_FLAT_W; ++k)
        if (s[k] == ' ') cut = k;
    if (!wrap) cut = n;
    if (!cut) cut = k > 1 ? k - 1 : 1;
    quick_line_flat(canvas, s, cut, x, y);
    if (wrap) {
        unsigned skip = cut + (s[cut] == ' ');
        quick_line_flat(canvas, s + skip, n - skip, x, y + QS_FLAT_LINE);
    }
}
/* The page, flat, in its window's coordinates: the ground and its two cards, then each button by
 * the image stock last gave its icon: a 54 px disc, at rest or on (QS_LIT, tools/art.py), a sixth
 * darker while pressed, its glyph in the disc's ink (white, or _l at rest in the light half and on
 * a bright fill), and its label. */
static void quick_page_flat(void *win, void *canvas) {
    int light = ipod_light();
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), on = flat_on();
    rect_t buttons = { 18, 20, 339, 196 }, bar = qs_bar_card;
    stops_t card = { 2, { 0, 100 }, { qs_card[light], qs_card[light] } };
    bar_rect(canvas, RGBA(flat_page()), 0, 0, I(win, W_W), I(win, W_H));
    paint_round_rows(canvas, &buttons, &card, 26);
    paint_round_rows(canvas, &bar, &card, 26);
    for (unsigned i = 0; i < sizeof quick_icons / sizeof *quick_icons; ++i) {
        void *icon = widget_lookup(win, quick_icons[i], 1);
        void *label = widget_lookup(win, quick_labels[i], 1);
        if (!icon) continue;
        char name[40];
        const char *image = widget_get_prop_str(icon, "image", "");
        int lit = named_in(QS_LIT, image);
        unsigned c = lit ? on : qs_rest[light];
        const char *ink = (lit ? bright_tone(c) : light) ? "_l" : "";
        if (icon == quick.pressed) c = mix(c, 0, 1, 6);
        rect_t r = { I(icon, W_X), I(icon, W_Y), 54, 54 };
        stops_t face = { 2, { 0, 100 }, { c, c } };
        paint_round_rows(canvas, &r, &face, 27);
        tk_snprintf(name, sizeof name, FLAT_ART "%s%s", image, ink);
        paint_art(win, canvas, name, r.x, r.y);
        if (label) quick_label_flat(canvas, label, r.x - (QS_FLAT_W - 54) / 2, r.y + 57);
    }
    stock_canvas_set_fill_color(canvas, fill);
}
/* quick_scrub's, flat: the bar, its fill never less than its own round end, the suns in it; in the
 * window's coordinates less the slider's. */
static void quick_scrub_flat(void *w, void *canvas) {
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    void *view = P(w, W_PARENT);
    int ox = -I(view, W_X) - I(w, W_X), oy = -I(view, W_Y) - I(w, W_Y);
    int min = widget_get_prop_int(w, "min", 0), max = widget_get_prop_int(w, "max", 100);
    int part = QS_BAR_H;
    if (max > min)
        part += (QS_BAR_W - QS_BAR_H) *
                clamp_step(0, max - min, widget_get_prop_int(w, "value", 0) - min) / (max - min);
    rect_t r = { QS_BAR_X + ox, QS_BAR_Y + oy, QS_BAR_W, QS_BAR_H };
    flat_bar(w, canvas, &r, part, qs_rest[ipod_light()], BAR_SUNS);
    stock_canvas_set_fill_color(canvas, fill);
}
/* The volume over the flat page (vol_paint, the wheel on stock's volume), dy down the screen from
 * w: the bar's card, and the volume in the bar's place between the speakers, so nothing of the page
 * is half covered. */
void quick_vol_flat(void *w, void *canvas, int dy, int level, int max) {
    rect_t card = qs_bar_card, r = { QS_BAR_X, QS_BAR_Y + dy, QS_BAR_W, QS_BAR_H };
    stops_t face = { 2, { 0, 100 }, { qs_card[ipod_light()], qs_card[ipod_light()] } };
    card.y += dy;
    paint_round_rows(canvas, &card, &face, 26);
    flat_bar(w, canvas, &r,
             QS_BAR_H + (max > 0 ? (QS_BAR_W - QS_BAR_H) * clamp_step(0, max, level) / max : 0),
             qs_rest[ipod_light()], BAR_SPEAKERS);
}

/* Quick Settings' swipe up under the own pages: a touch from QS_SWIPE_Y (the flat look's bar's
 * card) that has gone SLIDE_PX up, and isn't the slider's by then, counts for stock's handler as
 * one from its line. */
static void quick_swipe(void *target, void *event) {
    int from = I(g_pressdown_y, 0);
    /* the flat look's from its bar's card, which its slider's touch fills from 233 */
    if (from < (flat_pages() ? qs_bar_card.y : QS_SWIPE_Y) || from >= QS_SWIPE_STOCK ||
        from - I(event, EVENT_Y) < SLIDE_PX || I(g_dropdown_menu_state, 0) != 3 ||
        (quick.slider && quick.slide > 0) || !own_pages() ||
        tk_strcmp(widget_get_prop_str(target, "name", ""), "statusbar_dialog"))
        return;
    I(g_pressdown_y, 0) = QS_SWIPE_STOCK;
}

/* ringnav_dispatch's Quick Settings part, for each event: a hold's timer and Classic's pressed
 * button, and the brightness slider's touch. What ringnav_dispatch returns, or -1 to go on. */
int quick_dispatch(void *target, void *event) {
    int type = I(event, EVENT_TYPE);
    if (quick.timer &&
        (type == EVT_POINTER_UP_BEFORE || type == EVT_POINTER_UP_BEFORE - 1 ||
         type == EVT_POINTER_ABORT || type == EVT_CLICK || type == EVT_KEY_DOWN_BEFORE ||
         ((type == EVT_POINTER_MOVE_BEFORE || type == EVT_POINTER_MOVE_BEFORE - 1) &&
          ((unsigned)(I(event, EVENT_X) - quick.x + 10) > 20 ||
           (unsigned)(I(event, EVENT_Y) - quick.y + 10) > 20))))
        quick_cancel(0, 0);
    if (quick.pressed && (type == EVT_POINTER_UP_BEFORE || type == EVT_POINTER_UP_BEFORE - 1 ||
                          type == EVT_POINTER_ABORT))
        quick_press(0);
    if (target && type == EVT_CLICK && widget_get_prop_int(target, QUICK_HOLD, 0) < 0) return STOP;
    /* Quick Settings' swipe up to close may start on its brightness slider. The slider gets
     * its press only once the touch has moved SLIDE_PX more sideways than up or down, or on a
     * tap's release; a touch that moves up or down first, or while stock slides the pull-down
     * (g_dropdown_menu_state 1, 2), never reaches it, and one it already has is aborted. A
     * withheld event returns 0, not STOP, which would end its parents' handling too: the
     * window manager's pointer-down keeps the touch's start (g_pressdown_y) for the swipe. */
    void *qs = target;
    if (type == EVT_POINTER_DOWN - 1 && target &&
        !tk_strcmp(widget_get_prop_str(target, "name", ""), "slider_backlight"))
        while (qs && tk_strcmp(widget_get_prop_str(qs, "name", ""), "statusbar_dialog"))
            qs = P(qs, W_PARENT); /* Quick Settings', not Settings' Brightness page */
    else
        qs = 0;
    if (qs) {
        quick.slider = target, quick.slide = 0;
        quick.sx = I(event, EVENT_X), quick.sy = I(event, EVENT_Y);
        return 0;
    }
    int move = type == EVT_POINTER_MOVE_BEFORE - 1, up = type == EVT_POINTER_UP_BEFORE - 1;
    if (move && target) quick_swipe(target, event);
    if (target && target == quick.slider && (move || up)) {
        int dx = I(event, EVENT_X) - quick.sx, dy = I(event, EVENT_Y) - quick.sy;
        dx = dx < 0 ? -dx : dx, dy = dy < 0 ? -dy : dy;
        unsigned ev[12];
        if ((unsigned)(I(g_dropdown_menu_state, 0) - 1) < 2) {
            if (quick.slide > 0)
                stock_dispatch_trampoline(target,
                                          pointer_event_init(ev, EVT_POINTER_ABORT, target, 0, 0));
            quick.slide = -1;
        } else if (!quick.slide && (up || (dx >= SLIDE_PX && dx > dy))) {
            quick.slide = 1;
            stock_dispatch_trampoline(
                target, pointer_event_init(ev, EVT_POINTER_DOWN - 1, target, quick.sx, quick.sy));
        } else if (!quick.slide && dy >= SLIDE_PX)
            quick.slide = -1;
        if (up) quick.slider = 0;
        if (quick.slide <= 0) return 0;
    } else if (up) /* a release the slider doesn't see, its press never given */
        quick.slider = 0;
    return -1;
}

/* Quick Settings' wheel: 0 its brightness slider, 1 stock's volume (Q2POD QSVOLUME). A held
 * Play/Pause there switches it (ringnav_keylong) and says so in the theme's own toast, over the
 * pull-down as stock's own messages are.
 * ponytail: the toast is modal, so it takes the wheel and Centre for its 2 s; a floating band kept
 * them live (until 1.8.2) but had no theme's look. */
int quick_volume(void) {
    if (!quick.wheel) quick.wheel = 1 + config_value("Q2POD", "QSVOLUME", 2, 0);
    return quick.wheel - 1;
}
/* The words for ringnav_keylong's toast; 0 when the hold is not Quick Settings'. */
const char *quick_wheel_toggle(void *top) {
    if (!usable() || !top || tk_strcmp(widget_get_prop_str(top, "name", ""), "statusbar_dialog"))
        return 0;
    int volume = !quick_volume();
    quick.wheel = 1 + volume;
    write_int_config(volume, "Q2POD", "QSVOLUME");
    return volume ? "Wheel: Volume" : "Wheel: Brightness";
}

/* Stock's swipe down opens Quick Settings at g_dropdown_menu_state 1 and starts its slide only on
 * the touch's release reaching the dialog (its pointer-up handler adds the timer_idex timer). The
 * dialog is modal, opened inside the swipe's pointer move, and above the screen it catches a touch
 * only once opened (window_manager_find_target, stage 3), in an idle its nested loop runs after
 * draining the queue. So a release already queued, by a flick quicker than the dialog loads or a
 * swipe queued behind a track start, goes to the page under it: the dialog stays on top off screen
 * with that page sent to the background, Now Playing's timer stopped (#189). With no touch down
 * and no slide running, the next UI pass hands it the release through ringnav_dispatch: stock's
 * handler (0x4a1e6c, bound at dialog_statusbar_dialog_init) ignores the event's place and at states
 * 1 and 2 with timer_idex -1 always adds its slide timer (0x4a1f4c), which always ends, so one pass
 * does it. */
static int quick_stuck(void *top) {
    return usable() && top &&
           !tk_strcmp(widget_get_prop_str(top, "name", ""), "statusbar_dialog") &&
           (unsigned)(I(g_dropdown_menu_state, 0) - 1) < 2 && !pointer_down() &&
           widget_get_prop_int(top, "timer_idex", -1) == -1;
}
void quick_poll(void) {
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    unsigned up[12];
    if (quick_stuck(top))
        ringnav_dispatch(top, pointer_event_init(up, EVT_POINTER_UP_BEFORE - 1, top, 0, 0));
    /* Return's hold over Quick Settings (compact_now_playing), once the dialog has gone: its own
     * idle closes it, a pass or two on. One still up QS_HOLD_MS later was no hold's to wait for. */
    if (st.qs_hold && (unsigned)time_now_ms() - st.qs_hold >= QS_HOLD_MS) st.qs_hold = 0;
    if (st.qs_hold && top && !window_manager_is_animating(wm) &&
        tk_strcmp(widget_get_prop_str(top, "name", ""), "statusbar_dialog")) {
        st.qs_hold = 0;
        compact_now_playing();
    }
}
