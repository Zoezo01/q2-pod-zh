/* Clock and time: the time zone rows, the status bar's clock, and Classic's Set Date and Set Time
 * form with its clock face. */
#include "navigation.h"
extern int stock_timezone_trampoline(void);
extern void stock_timezone_row_trampoline(widget_t *, int);

static const char *const tz_names[] = TIMEZONE_NAMES;
#define TZ_COUNT (sizeof tz_names / sizeof *tz_names)

static void clock_zone(void);
int ringnav_timezone(void) {
    unsigned n = g_time_zone;
    int result = n < TZ_COUNT ? toolsSetTimeZone(tz_names[n]) : stock_timezone_trampoline();
    tzset(); /* localtime_r does not reload TZ itself */
    clock_zone();
    return result;
}

/* Past 2038, 28 years back for stock's 32-bit localtime: the same calendar until 2100. */
long clock_back(long long wall) {
    unsigned back = (unsigned)wall; /* 32-bit: the payload has no 64-bit division */
    while (back > 0x7fffffff) back -= 883612800;
    return (long)back;
}

/* Each zone's offset from UTC now, in seconds, daylight saving included: localtime's, with each
 * zone's TZ set in turn; then the saved zone is set again. rows gets the zones west to east by it,
 * ties in TIMEZONE_ORDER (standard offset, then name). ponytail: another thread's localtime in that
 * moment may read one of these zones. */
static long tz_offset[TZ_COUNT];
static void tz_now(unsigned char *rows) {
    static const unsigned char order[] = TIMEZONE_ORDER;
    long now = clock_back(wall_time());
    for (unsigned i = 0; i < TZ_COUNT; ++i) {
        toolsSetTimeZone(tz_names[i]);
        tzset();
        int buf[11];
        const int *tm = localtime_r(&now, buf); /* struct tm: tm_gmtoff @36 */
        tz_offset[i] = tm ? tm[9] : 0;
    }
    ringnav_timezone();
    for (unsigned i = 0; i < TZ_COUNT; ++i) {
        unsigned j = i;
        for (; j && tz_offset[rows[j - 1]] > tz_offset[order[i]]; --j) rows[j] = rows[j - 1];
        rows[j] = order[i];
    }
}

/* Stock's Time Zone caption, between rows 0 and 1, sits 20 px down its item and over row 1: Classic
 * gives it the item, the set's row. */
static void zone_caption(widget_t *item) {
    if (!classic_pages()) return;
    widget_t *list = P(item, W_PARENT); /* item is its last child, the caption's the one before */
    int n = list ? widget_count_children(list) : 0;
    widget_t *cap = n > 1 ? widget_get_child(list, n - 2) : (void *)0;
    cap = cap && widget_count_children(cap) ? widget_get_child(cap, 0) : (void *)0;
    if (cap && !tk_strcmp(widget_get_type(cap), "hscroll_label")) {
        widget_move_resize(cap, 0, 0, 375, geom_val(V_SET_ROW));
        theme_font(cap, ROLE_INFO, FONT_ROW);
    }
}
/* Row i > 0 shows zone rows[i - 1] as "GMT-4 New York", the offset it has now, so a zone on
 * daylight saving reads as its clocks do; stock names, ticks and clicks by the index it is given.
 * Focus-only, a label too long for the row keeps its ellipsis instead of scrolling once as the
 * page opens. */
void ringnav_timezone_row(widget_t *item, int i) {
    static const char *const cities[] = TIMEZONE_CITIES;
    static unsigned char rows[TZ_COUNT];
    if (i == 1) tz_now(rows); /* stock builds rows 0, 1, 2, ... */
    int zone = i > 0 && i <= (int)TZ_COUNT ? rows[i - 1] : -1;
    stock_timezone_row_trampoline(item, zone < 0 ? i : zone + 1);
    if (i == 1) zone_caption(item);
    widget_t *row = widget_get_child(item, widget_count_children(item) - 1); /* stock's button */
    for (unsigned c = 0; row && c < widget_count_children(row); ++c) {
        widget_t *label = widget_get_child(row, c);
        if (tk_strcmp(widget_get_type(label), "hscroll_label")) continue;
        hscroll_label_set_only_focus(label, 1);
        if (zone < 0) continue;
        long off = tz_offset[zone];
        int m = (int)((off < 0 ? -off : off) / 60);
        char s[32];
        if (m % 60)
            tk_snprintf(s, sizeof s, "GMT%s%d:%02d %s", off < 0 ? "-" : "+", m / 60, m % 60,
                        cities[zone]);
        else
            tk_snprintf(s, sizeof s, "GMT%s%d %s", off < 0 ? "-" : "+", m / 60, cities[zone]);
        widget_set_text_utf8(label, s);
    }
}

/* The status bar's centred label follows stock's 24-hour setting (18:14 or 6:14 PM), without
 * seconds, or --:-- when the time cannot be read. clock_show reads the time once a minute, from a
 * timer due at the next minute, writes the label when the minute shown changes and applies Auto
 * Light Mode's and Night Shift's hour; nothing reads the time in between. It runs at once for a new
 * zone (ringnav_timezone), a time set by hand (time_click) and a wake (ringnav_sleep), and on the
 * pass after the bar first paints or paints with a new 24-hour setting (ringnav_paint_bg).
 * ponytail: a network time sync while lit shows at the next minute. */
int clock_due(const void *info) {
    (void)info;
    st.clock_timer = 0;
    clock_show();
    return 0;
}
void clock_show(void) {
    stop_timer(&st.clock_timer);
    int buf[11];
    const int *tm = clock_now(buf);
    int ok = tm && tm[0] >= 0 && tm[0] < 61 && tm[1] >= 0 && tm[1] < 60 && tm[2] >= 0 && tm[2] < 24;
    int full = st.clock_full = g_time24h_flag != 0;
    accent(); /* reads Light Mode */
    light_apply(ok ? tm : (void *)0);
    night_clock(ok && !light_day(tm));
    st.clock_timer = timer_add(clock_due, (void *)0, ok ? (60u - (unsigned)tm[0]) * 1000u : 60000u);
    unsigned key = ok ? ((unsigned)(tm[2] * 60 + tm[1]) + 1) | (unsigned)full << 11 |
                            (unsigned)!!st.bar_title << 12 /* it changes the 12-hour form below */
                      : ~0u;                               /* 0 before the first */
    widget_t *bar = *(void *const *)system_bar;
    widget_t *label = key == st.clock_key || !bar || st.bar_titled
                          ? (void *)0
                          : widget_lookup(bar, "label_clock", 1);
    if (!label) return;
    st.clock_key = key;
    char s[16] = "--:--";
    if (ok && full)
        tk_snprintf(s, sizeof s, "%02d:%02d", tm[2], tm[1]);
    else if (ok && st.bar_title) /* beside the bar's title: AM or PM would reach it */
        tk_snprintf(s, sizeof s, "%d:%02d", (tm[2] + 11) % 12 + 1, tm[1]);
    else if (ok)
        tk_snprintf(s, sizeof s, "%d:%02d %s", (tm[2] + 11) % 12 + 1, tm[1],
                    tm[2] < 12 ? "上午" : "下午");
    widget_set_text_utf8(label, s);
    /* The clock's own 16 px, in every state the bar's children read (disable's style says 20),
     * and against stock V1.36's 21 px page-title writes: the time reads like the word beside it. */
    if (!st.bar_title)
        for (unsigned i = 0; i < 3; ++i)
            widget_set_prop_int(label,
                                i ? (i == 1 ? "style:disable:font_size" : "style:focused:font_size")
                                  : "style:normal:font_size",
                                16);
}
static void clock_zone(void) {
    if (st.clock_timer) clock_show(); /* the bar runs; boot's call comes before it */
}

/* Minutes since midnight now, or -1 when the clock can't be read. */
int clock_minutes(void) {
    int buf[11];
    const int *tm = clock_now(buf);
    return tm ? tm[2] * 60 + tm[1] : -1;
}

/* target's click, to stock. Set date and time's OK sets the clock (stock) and stays; it goes back
 * to the Time page instead, as Return does, after a tap (ringnav_dispatch) or Centre's click
 * (edit_single). */
int time_click(widget_t *target, event_t *event) {
    widget_t *page = window_manager_get_top_window(window_manager());
    int ok = page && !tk_strcmp(widget_get_prop_str(page, "name", ""), "manualtime_page") &&
             !tk_strcmp(widget_get_prop_str(target, "name", ""), "btn_enter");
    int result = stock_dispatch_trampoline(target, event);
    if (ok && st.clock_timer) clock_show();
    if (ok && window_manager_get_top_window(window_manager()) == page) navigator_back();
    return result;
}

/* The own pages' Set Date and Set Time, either look's: no OK stop, and AM/PM a field in 12-hour. */
int time_form(widget_t *top) {
    return top && own_pages() &&
           !tk_strcmp(widget_get_prop_str(top, "name", ""), "manualtime_page");
}

/* Classic's Set Date and Set Time, on stock's page. Its selectors stay the truth, at opacity
 * 0 over the cells this paints, so a drag still changes them and stock still saves them; the hour's
 * always 0..23, and the 12-hour form only shows it otherwise. AM/PM has no widget of its own: it's
 * the hour's half. Each image reaches past its box by its TIME_*_PAD (tools/art.py). */
#define TIME_CELL_Y 107
#define TIME_CELL_H 42
#define TIME_TRAY_Y 104
#define TIME_TRAY_PAD 7
#define TIME_CELL_PAD 4
#define TIME_TILE_X 40
#define TIME_TILE_Y 78
#define TIME_TILE_W 118
#define TIME_TILE_PAD 6
#define TIME_YEAR_W 60
#define TIME_CELL_W 45
#define TIME_AMPM 5
/* the flat look's tile, and Save's pill: the prompts' button, on their row */
#define TIME_TILE_H 100
#define TIME_SAVE_X 127
#define TIME_SAVE_Y 224
#define TIME_SAVE_W 121
#define TIME_SAVE_H 36
typedef struct {
    const char *tray;
    short tray_x, sep_x; /* no separator at 0 */
    unsigned char first, n;
    short x[3];
} time_row_t;
static const time_row_t time_rows[] = {
    { "classic_light_tray_164", 183, 0, 0, 3, { 186, 250, 299 } },
    { "classic_light_tray_108", 211, 259, 3, 2, { 214, 271 } },
    { "classic_light_tray_165", 183, 231, 3, 3, { 186, 243, 300 } },
};
static const char time_months[12][4] = { "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" };
/* The open form's selectors, by field, AM/PM's none, and its slide view: found after stock's init,
 * so a paint needs no lookup. */
static struct {
    widget_t *sel[6], *slide;
    int was[5]; /* each selector's index as last seen: time_changed, a drag's change */
} tf;
/* What option 0 of each stock selector means. */
static int time_value(int f) {
    static const unsigned short first[] = { 2022, 1, 1, 0, 0 };
    return f <= 4 && tf.sel[f] ? I(tf.sel[f], SELECTOR_INDEX) + first[f] : 0;
}
static const time_row_t *time_row(int time) {
    return &time_rows[time ? 1 + !g_time24h_flag : 0]; /* 24 or 12-hour */
}
/* A field's x: its row's; on the date's row under a theme's gap (the flat look's date.gap) the day
 * stays and the month and year step left by it. */
static int field_x(const time_row_t *row, int i) {
    int gap = row == time_rows ? theme()->date_gap : 0;
    return !gap ? row->x[i] : row->x[2] - (2 - i) * gap - (i < 2) * TIME_CELL_W - !i * TIME_YEAR_W;
}
static const char *time_month(void) { return time_months[clamp_step(time_value(1) - 1, 11, 0)]; }

/* The bar's title reads the page's own label (bar_title_sync). */
static void time_title(widget_t *win) {
    widget_t *label = (void *)0;
    char s[16];
    bar_title_of(win, &label);
    if (!label) return;
    tk_snprintf(s, sizeof s, "%s %d, %d", time_month(), time_value(2), time_value(0));
    widget_set_text_utf8(label, s);
}
static int time_changed(void *win, event_t *event) {
    (void)event;
    int first = -1;
    for (int f = 4; f >= 0; --f) {
        int now = tf.sel[f] ? I(tf.sel[f], SELECTOR_INDEX) : 0;
        if (now == tf.was[f]) continue;
        int crossed = f == 3 && fields(win) == 6 && now / 12 != tf.was[3] / 12;
        if (crossed) now = tf.was[3] / 12 * 12 + now % 12; /* its half, as the wheel keeps it */
        tf.was[f] = now;
        if (crossed) text_selector_set_selected_index(tf.sel[3], (unsigned)now);
        first = f;
    }
    /* a drag's cell takes the selection; stock clamping the day under a month leaves it the month
     */
    if (first >= 0) prop(win, "_edit_field", first);
    time_title(win);
    widget_invalidate_force(win, (void *)0);
    return 0;
}

/* The wheel on field f, or a tap on AM/PM. A 12-hour hour stays in its half; AM/PM crosses it. */
void time_wheel(widget_t *top, int f, int dir) {
    int half = f == TIME_AMPM || (f == 3 && fields(top) == 6);
    widget_t *sel = field_widget(top, half ? 3 : f);
    if (!sel) return;
    int index = I(sel, SELECTOR_INDEX), next;
    if (f == TIME_AMPM)
        next = index < 12 ? index + 12 : index - 12;
    else if (half)
        next = index / 12 * 12 + (index % 12 + dir + 12) % 12;
    else {
        unsigned options = text_selector_count_options(sel);
        if (!options || options > 0x7fffffff) return;
        next = clamp_step(index, (int)options - 1, dir);
    }
    if (next == index) return;
    tf.was[half ? 3 : f] = next;
    text_selector_set_selected_index(sel, (unsigned)next);
    time_title(top);
    field_set(top, f);
}
static int time_ampm(void *win, event_t *event) {
    (void)event;
    time_wheel(win, TIME_AMPM, 1);
    return 0;
}

/* Stock's window as it loads, before its init, so that init's own setting of the selectors places
 * them at this size. */
void time_open(widget_t *win) {
    static const char *const hide[] = { "img_dot", "slide_indicator" };
    widget_t *c, *ok = field_widget(win, fields(win));
    memset(&tf, 0, sizeof tf);
    for (unsigned i = 0; i < sizeof hide / sizeof *hide; ++i)
        if ((c = widget_lookup(win, hide[i], 1))) widget_set_visible(c, 0, 0);
    for (int t = 0; t < 2; ++t) {
        const time_row_t *row = time_row(t);
        for (int i = 0; i < row->n; ++i) {
            c = field_widget(win, row->first + i);
            if (!c) continue;
            widget_move_resize(c, field_x(row, i), TIME_CELL_Y,
                               row->first + i ? TIME_CELL_W : TIME_YEAR_W, TIME_CELL_H);
            widget_set_opacity(c, 0);
            /* a drag steps a row, TIME_CELL_H / 3; stock's release adds its speed times this */
            widget_set_prop_int(c, "yspeed_scale", 0);
        }
        widget_t *page = field_widget(win, row->first);
        page = page ? P(page, W_PARENT) : (void *)0;
        if (page && (c = widget_lookup(page, "view_back", 0))) widget_set_visible(c, 0, 0);
        if (t && page && row->n == 3) { /* AM/PM's cell takes a tap */
            c = image_create(page, row->x[2], TIME_CELL_Y, TIME_CELL_W, TIME_CELL_H);
            widget_set_prop_str(c, "clickable", "true");
            widget_on(c, EVT_CLICK, time_ampm, win);
        }
    }
    if (ok) { /* Save, past the page's stock height: painted, its box the touch */
        widget_move_resize(P(ok, W_PARENT), 0, 0, 375, 294);
        if (flat_pages())
            widget_move_resize(ok, TIME_SAVE_X, TIME_SAVE_Y, TIME_SAVE_W, TIME_SAVE_H);
        else
            widget_move_resize(ok, 0, 244, 375, 26);
        widget_set_text_utf8(ok, "保存");
        widget_set_opacity(ok, 0);
    }
    st.slider_win = win;
    st.slider_w = (void *)0; /* slider_gone clears the window; this page has no slider */
    widget_on(win, EVT_DESTROY, slider_gone, win);
}

/* After stock's init: it rebuilds the label the title goes in, and its own handlers, first in line,
 * have clamped the day by the time these read it. */
extern int stock_manualtime_trampoline(widget_t *win, void *ctx);
int ringnav_manualtime(widget_t *win, void *ctx) {
    int result = stock_manualtime_trampoline(win, ctx);
    if (time_form(win)) {
        for (int f = 0; f < 5; ++f) {
            widget_t *sel = tf.sel[f] = field_widget(win, f);
            if (sel) widget_on(sel, EVT_VALUE_CHANGED, time_changed, win);
            tf.was[f] = sel ? I(sel, SELECTOR_INDEX) : 0;
        }
        tf.slide = widget_lookup(win, "slide_view", 1);
        if (tf.slide) widget_on(tf.slide, EVT_VALUE_CHANGED, time_changed, win);
        time_title(win);
    }
    return result;
}

/* sin in 1/4096 at whole degrees, from a table of a quarter turn in 1/60 turn steps. */
static int clock_sin(int deg) {
    static const short quarter[16] = { 0,    428,  852,  1266, 1666, 2048, 2408, 2741,
                                       3044, 3314, 3547, 3742, 3896, 4006, 4074, 4096 };
    deg = (deg % 360 + 360) % 360;
    int a = deg % 90, s, t, v;
    if (deg / 90 & 1) a = 90 - a;
    s = a / 6, t = a % 6;
    v = quarter[s] + (t ? (quarter[s + 1] - quarter[s]) * t / 6 : 0);
    return deg >= 180 ? -v : v;
}
/* A convex polygon (x, y pairs in 1/64 px), each edge walked once over the sub-rows it crosses:
 * each row two end spans of single pixels by their coverage and a run between them. */
#define CLOCK_SUB 4
#define CLOCK_ROWS 64 /* the tallest shape, and its cut */
static void clock_fill(canvas_t *canvas, const int *p, int n, unsigned rgb) {
    int top = p[1], foot = p[1], los[CLOCK_ROWS * CLOCK_SUB], his[CLOCK_ROWS * CLOCK_SUB];
    unsigned color = RGBA(rgb);
    for (int i = 1; i < n; ++i) {
        top = p[2 * i + 1] < top ? p[2 * i + 1] : top;
        foot = p[2 * i + 1] > foot ? p[2 * i + 1] : foot;
    }
    int y0 = top >> 6, base = (y0 << 6) + 8;
    for (int s = 0; s < CLOCK_ROWS * CLOCK_SUB; ++s) los[s] = 1 << 24, his[s] = -(1 << 24);
    for (int i = 0; i < n; ++i) {
        const int *a = p + 2 * i, *b = p + 2 * ((i + 1) % n);
        int low = a[1] < b[1] ? a[1] : b[1], high = a[1] < b[1] ? b[1] : a[1];
        for (int s = (low - base + 15) >> 4; s < CLOCK_ROWS * CLOCK_SUB; ++s) {
            int yy = base + 16 * s;
            if (yy >= high) break;
            int x = a[0] + (yy - a[1]) * (b[0] - a[0]) / (b[1] - a[1]);
            los[s] = x < los[s] ? x : los[s];
            his[s] = x > his[s] ? x : his[s];
        }
    }
    for (int y = y0; y << 6 < foot && y - y0 < CLOCK_ROWS; ++y) {
        const int *lo = los + (y - y0) * CLOCK_SUB, *hi = his + (y - y0) * CLOCK_SUB;
        int left = 1 << 24, right = -(1 << 24), in = -(1 << 24), out = 1 << 24;
        for (int s = 0; s < CLOCK_SUB; ++s) {
            left = lo[s] < left ? lo[s] : left;
            right = hi[s] > right ? hi[s] : right;
            in = lo[s] > in ? lo[s] : in; /* a sub-row that missed leaves no run */
            out = hi[s] < out ? hi[s] : out;
        }
        in = (in + 63) >> 6, out >>= 6;
        for (int x = left >> 6; x << 6 < right; ++x) {
            if (x >= in && x < out) {
                bar_rect(canvas, color, x, y, out - x, 1);
                x = out - 1;
                continue;
            }
            int cov = 0;
            for (int s = 0; s < CLOCK_SUB; ++s) {
                int a = lo[s] > x << 6 ? lo[s] : x << 6,
                    b = hi[s] < (x + 1) << 6 ? hi[s] : (x + 1) << 6;
                if (b > a) cov += b - a;
            }
            if (cov)
                bar_rect(canvas,
                         (color & 0xffffff) | (unsigned)(cov * 255 / (64 * CLOCK_SUB)) << 24, x, y,
                         1, 1);
        }
    }
}
/* The clock art at x, y and size px square with its parts over it, each at its hand's angle for
 * h:m (peq.h clock_part_t). The fill is restored. */
void paint_clock(widget_t *w, canvas_t *canvas, int x, int y, int size, const char *art, int h,
                 int m, const clock_part_t *parts, int n) {
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    paint_art(w, canvas, art, x, y);
    int cx = (x << 6) + (size << 5), cy = (y << 6) + (size << 5);
    for (int i = 0; i < n; ++i) {
        const clock_part_t *c = &parts[i];
        int pts[2 * 24], k = c->r ? 24 : c->n, r = c->r * size * 64 / 1000;
        int deg = c->hand == 1   ? h % 12 * 30 + m / 2
                  : c->hand == 2 ? m * 6
                                 : 0,
            co = clock_sin(deg + 90), si = clock_sin(deg);
        for (int j = 0; j < k; ++j) {
            int px = c->pts[j % 5][0] * size * 64 / 1000, py = c->pts[j % 5][1] * size * 64 / 1000;
            pts[2 * j] = cx + (r ? r * clock_sin(j * 15 + 90) : px * co - py * si) / 4096;
            pts[2 * j + 1] = cy + (r ? r * clock_sin(j * 15) : px * si + py * co) / 4096;
        }
        clock_fill(canvas, pts, k, c->rgb);
    }
    stock_canvas_set_fill_color(canvas, fill);
}
static const clock_part_t time_clock[] = {
    { 0x161b22, 0, 5, 1, { { -12, 0 }, { -21, -60 }, { 0, -190 }, { 21, -60 }, { 12, 0 } } },
    { 0x161b22, 0, 5, 2, { { -10, 0 }, { -18, -70 }, { 0, -280 }, { 18, -70 }, { 10, 0 } } },
    { 0x161b22, 37, 0, 0, { { 0 } } }, /* r 3, stroked 1.3 */
    { 0xffffff, 24, 0, 0, { { 0 } } },
};

/* Text in final colours, as Light Mode's text setter flips what it's given. */
static void time_text(canvas_t *canvas, const unsigned *s, unsigned n, const rect_t *r, unsigned px,
                      unsigned rgb, unsigned alpha) {
    draw_centred(canvas, s, n, r, px, alpha << 24 | (RGBA(flip_if(ipod_light(), rgb)) & 0xffffff));
}
/* A cell's or the tile's number: its text, widened. */
/* s's first 5 code points (UTF-8: 上午 and 下午 are one each, not their bytes). */
static unsigned time_chars(unsigned *to, const char *s) {
    unsigned n = 0;
    const unsigned char *p = (const unsigned char *)s;
    while (*p && n < 5) {
        unsigned c = *p++;
        if (c >= 0xc0) {
            int k = c >= 0xf0 ? 3 : c >= 0xe0 ? 2 : 1;
            for (c &= 0x3f >> k; k-- && (*p & 0xc0) == 0x80;) c = c << 6 | (*p++ & 0x3f);
        }
        to[n++] = c;
    }
    return n;
}
/* The flat look's Set Time hands in the half's ink, [0] dark and [1] light (flat_ink), and the
 * pivot's dot in its face's grey (flat_rest). */
#define TIME_HANDS(ink, face)                                                                      \
    {                                                                                              \
        { ink, 0, 5, 1, { { -12, 0 }, { -21, -60 }, { 0, -190 }, { 21, -60 }, { 12, 0 } } },       \
        { ink, 0, 5, 2, { { -10, 0 }, { -18, -70 }, { 0, -280 }, { 18, -70 }, { 10, 0 } } },       \
        { ink, 37, 0, 0, { { 0 } } },                                                              \
        { face, 24, 0, 0, { { 0 } } },                                                             \
    }
static const clock_part_t time_clock_flat[2][4] = { TIME_HANDS(0xffffff, 0x2c2c2e),
                                                    TIME_HANDS(0x1c1c1e, 0xe9e9ec) };
/* time_paint's, flat: grey boxes at rest, the cell that holds in what is on, the clock's flat face
 * or the date's tile with its month in what is on, and Save's pill. */
static void time_paint_flat(widget_t *w, canvas_t *canvas) {
    static const unsigned colon = ':', save[] = { 0x4fdd, 0x5b58, 0 };
    rect_t tile = { TIME_TILE_X, TIME_TILE_Y, TIME_TILE_W, TIME_TILE_H },
           pill = { TIME_SAVE_X, TIME_SAVE_Y, TIME_SAVE_W, TIME_SAVE_H };
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), s[5], n;
    int time = tf.slide && widget_get_prop_int(tf.slide, "value", 0) == 1;
    int at = field_on_page(widget_get_prop_int(w, "_edit_field", 0), time);
    int hour = time_value(3), minute = time_value(4), light = ipod_light();
    unsigned on = flat_on(), ink = flat_ink[light], rest = flat_rest[light],
             over = bright_tone(on) ? flat_ink[1] : 0xffffff;
    const time_row_t *row = time_row(time);
    char b[8]; /* 下午 is 6 bytes and its NUL: 6 cut it to 下 and half a lead byte */
    bar_rect(canvas, RGBA(flat_page()), 0, 0, 375, 294);
    if (time) {
        paint_clock(w, canvas, 46, 73, 110,
                    light ? FLAT_ART "clock_face_l" : FLAT_ART "clock_face_d", hour, minute,
                    time_clock_flat[light], 4);
    } else { /* the month in what is on, over the day */
        rect_t head = { TIME_TILE_X, TIME_TILE_Y + 6, TIME_TILE_W, 26 },
               day = { TIME_TILE_X, TIME_TILE_Y + 26, TIME_TILE_W, 70 };
        flat_box(canvas, &tile, RGBA(rest), 13);
        n = time_chars(s, time_month());
        time_text(canvas, s, n, &head, 21, on, 255);
        tk_snprintf(b, sizeof b, "%02d", time_value(2));
        n = time_chars(s, b);
        time_text(canvas, s, n, &day, 64, ink, 255);
    }
    for (int i = 0; i < row->n; ++i) {
        int f = row->first + i;
        rect_t r = { field_x(row, i), TIME_CELL_Y, f ? TIME_CELL_W : TIME_YEAR_W, TIME_CELL_H };
        flat_box(canvas, &r, RGBA(f == at ? on : rest), 10);
        if (f == TIME_AMPM)
            tk_snprintf(b, sizeof b, "%s", hour < 12 ? "上午" : "下午");
        else if (f == 3 && row->n == 3)
            tk_snprintf(b, sizeof b, "%d", hour % 12 ?: 12);
        else
            tk_snprintf(b, sizeof b, f ? "%02d" : "%d", time_value(f));
        n = time_chars(s, b);
        time_text(canvas, s, n, &r, f == TIME_AMPM ? 23 : 27, f == at ? over : ink, 255);
    }
    if (row->sep_x) {
        rect_t r = { row->sep_x, TIME_CELL_Y, 12, TIME_CELL_H };
        time_text(canvas, &colon, 1, &r, 27, ink, 255);
    }
    flat_box(canvas, &pill, RGBA(rest), 18);
    time_text(canvas, save, 2, &pill, CONFIRM_FLAT_PX, ink, 255);
    stock_canvas_set_fill_color(canvas, fill);
}
/* The page, in the window's coordinates, under its children. */
void time_paint(widget_t *w, canvas_t *canvas) {
    static const unsigned colon = ':', save[] = { 0x4fdd, 0x5b58, 0 };
    if (flat_pages()) {
        time_paint_flat(w, canvas);
        return;
    }
    static const char *const cells[2][2] = {
        { "classic_light_cell_45", "classic_light_cell_lit_45" },
        { "classic_light_cell_60", "classic_light_cell_lit_60" },
    };
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), s[5], n;
    int time = tf.slide && widget_get_prop_int(tf.slide, "value", 0) == 1;
    int at = field_on_page(widget_get_prop_int(w, "_edit_field", 0), time);
    int hour = time_value(3), minute = time_value(4);
    const time_row_t *row = time_row(time);
    char b[8]; /* 下午 is 6 bytes and its NUL: 6 cut it to 下 and half a lead byte */
    paint_page(w, canvas, 0);
    if (time) {
        paint_art(w, canvas, "classic_light_clock_shadow", 58, 166);
        paint_clock(w, canvas, 46, 73, 110, "classic_light_clock_chrome", hour, minute, time_clock,
                    sizeof time_clock / sizeof *time_clock);
    } else {
        /* each box holds its font, reaching past the header and the page as CSS's line boxes do */
        rect_t head = { TIME_TILE_X, TIME_TILE_Y - 2, TIME_TILE_W, 30 },
               day = { TIME_TILE_X, TIME_TILE_Y + 20, TIME_TILE_W, 82 };
        paint_art(w, canvas, "classic_light_tile", TIME_TILE_X - TIME_TILE_PAD,
                  TIME_TILE_Y - TIME_TILE_PAD);
        n = time_chars(s, time_month());
        time_text(canvas, s, n, &head, 28, 0xffffff, 255);
        tk_snprintf(b, sizeof b, "%02d", time_value(2));
        n = time_chars(s, b);
        time_text(canvas, s, n, &day, 79, 0x1b2030, 255);
    }
    paint_art(w, canvas, row->tray, row->tray_x - TIME_TRAY_PAD, TIME_TRAY_Y - TIME_TRAY_PAD);
    for (int i = 0; i < row->n; ++i) {
        int f = row->first + i, cw = f ? TIME_CELL_W : TIME_YEAR_W;
        rect_t r = { row->x[i], TIME_CELL_Y, cw, TIME_CELL_H };
        paint_art(w, canvas, cells[!f][f == at], r.x - TIME_CELL_PAD, r.y - TIME_CELL_PAD);
        if (f == TIME_AMPM)
            tk_snprintf(b, sizeof b, "%s", hour < 12 ? "上午" : "下午");
        else if (f == 3 && row->n == 3)
            tk_snprintf(b, sizeof b, "%d", hour % 12 ?: 12);
        else
            tk_snprintf(b, sizeof b, f ? "%02d" : "%d", time_value(f));
        n = time_chars(s, b);
        time_text(canvas, s, n, &r, f == TIME_AMPM ? 30 : 35, 0xffffff, 255);
    }
    if (row->sep_x) {
        rect_t r = { row->sep_x, TIME_CELL_Y, 12, TIME_CELL_H };
        time_text(canvas, &colon, 1, &r, 32, 0xffffff, 255);
    }
    rect_t label = { 0, 244, 375, 26 }, under = { 0, 245, 375, 26 };
    time_text(canvas, save, 2, &under, 24, 0, 0x59);
    time_text(canvas, save, 2, &label, 24, 0xffffff, 0xb8);
    stock_canvas_set_fill_color(canvas, fill);
}
