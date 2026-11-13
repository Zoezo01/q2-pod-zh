/* Battery History: the level's falls since the charger was last unplugged, kept on the flash, and
 * Power's page that graphs and lists them and exports them to the card. */
#include "navigation.h"

/* Battery History (docs/internals.md#battery-history): charge_poll hands battery_tick the level it
 * reads every CHARGE_POLL_MS. With the cable out, a level under the last row's is a row, the time
 * and the level; the first row is the start: the first poll after the cable comes out, or after a
 * boot with none in. A level over the last row's by more than BH_REBOUND starts again too: the Q2
 * charged while powered off, where demo saw no cable. Each row is one write of the whole record,
 * temp and rename (blob_io), so the flash is written once a percent at most. Within a history the
 * level only falls, from 100 at most to 1 (0 never reads: demo powers off first), so BH_ROWS rows
 * always hold one. ponytail: a charge of BH_REBOUND or less while powered off reads as the same
 * history, its rows below the last one's level again. */
#define BH_FILE PEQ_ROOT "/mnt/data/q2-battery"
#define BH_LOG PEQ_ROOT "/mnt/mmc/battery-log.txt"
#define BH_MAGIC 0x31484251u /* "QBH1" */
#define BH_ROWS 100
#define BH_REBOUND 3
static struct {
    struct {
        unsigned magic, n;
        struct {
            unsigned at, pct; /* wall_time's seconds, the level */
        } row[BH_ROWS];
    } rec;
    int read, cable; /* cable: in at the last tick */
    int stale;       /* a row the open page doesn't show yet: it came while the screen was off */
    void *page, *graph, *view;
} bh __attribute__((section(".scratch")));

/* The record from the flash, once; one that isn't whole, isn't this layout or whose levels don't
 * fall row by row is dropped, and the next tick starts again. */
static void bh_load(void) {
    if (bh.read) return;
    bh.read = 1;
    int ok = BLOB_IO(BH_FILE, bh.rec, 0) && bh.rec.magic == BH_MAGIC && bh.rec.n <= BH_ROWS;
    for (unsigned i = 0; ok && i < bh.rec.n; ++i)
        ok = bh.rec.row[i].pct &&
             (i ? bh.rec.row[i].pct < bh.rec.row[i - 1].pct : bh.rec.row[i].pct <= 100);
    if (!ok) memset(&bh.rec, 0, sizeof bh.rec);
}

static void bh_fill(void);
void battery_tick(int level, int cable) {
    if (bh.stale && bh.page && g_backlight_status) bh_fill();
    if (level <= 0 || level > 100) return; /* not read yet */
    bh_load();
    unsigned n = bh.rec.n;
    int unplugged = bh.cable && !cable;
    bh.cable = cable;
    if (cable) return;
    if (unplugged || !n || level > (int)bh.rec.row[n - 1].pct + BH_REBOUND)
        n = 0;
    else if (level >= (int)bh.rec.row[n - 1].pct)
        return;
    long long wall = wall_time();
    bh.rec.magic = BH_MAGIC;
    bh.rec.row[n].at = wall == -1 ? 0 : (unsigned)wall;
    bh.rec.row[n].pct = (unsigned)level;
    bh.rec.n = n + 1;
    BLOB_IO(BH_FILE, bh.rec, 1);
    bh.stale = 1;
    if (bh.page && g_backlight_status) bh_fill();
}

/* Row i's local time, or 0 for a clock never set. */
static const int *bh_tm(unsigned i, int *buf) {
    long t = clock_back(bh.rec.row[i].at);
    return bh.rec.row[i].at >= CLOCK_SET ? localtime_r(&t, buf) : (void *)0;
}
/* Row i as "99% | 12:00": the clock's time, 12-hour as the status bar's unless 24-hour is on, or
 * "+1:23", the hours and minutes since the start, for a clock never set. */
static void bh_text(char *s, unsigned size, unsigned i) {
    int buf[11];
    const int *tm = bh_tm(i, buf);
    unsigned at = bh.rec.row[i].at, from = bh.rec.row[0].at, m = (at - from) / 60,
             pct = bh.rec.row[i].pct;
    if (tm && g_time24h_flag)
        tk_snprintf(s, size, "%u%% | %02d:%02d", pct, tm[2], tm[1]);
    else if (tm)
        tk_snprintf(s, size, "%u%% | %d:%02d %s", pct, (tm[2] + 11) % 12 + 1, tm[1],
                    tm[2] < 12 ? "AM" : "PM");
    else if (at >= from)
        tk_snprintf(s, size, "%u%% | +%u:%02u", pct, m / 60, m % 60);
    else /* the clock lost after the start */
        tk_snprintf(s, size, "%u%% | --:--", pct);
}
/* Row i's date, "11 Nov 2026" (year) or "11 Nov", when it is the first row or a day after the row
 * before's; else "". */
static void bh_day(char *s, unsigned size, unsigned i, int year) {
    static const char months[12][4] = { "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" };
    int buf[11], was[11];
    const int *tm = bh_tm(i, buf), *before = i ? bh_tm(i - 1, was) : (void *)0;
    *s = 0;
    if (!tm || (unsigned)tm[4] > 11 ||
        (before && before[3] == tm[3] && before[4] == tm[4] && before[5] == tm[5]))
        return;
    if (year)
        tk_snprintf(s, size, "%d %s %d", tm[3], months[tm[4]], tm[5] + 1900);
    else
        tk_snprintf(s, size, "%d %s", tm[3], months[tm[4]]);
}

/* The log's text into out: each day's date over its rows, or, for a clock never set, a line that
 * says the times count from the start. Returns its length. */
unsigned battery_log(char *out, unsigned size) {
    unsigned len = 0;
    *out = 0;
    if (bh.rec.n && bh.rec.row[0].at < CLOCK_SET)
        tk_snprintf(out, size, "Clock not set: times since the start\n");
    for (unsigned i = 0; i < bh.rec.n && (len = strlen(out)) < size; ++i) {
        char day[32], row[48];
        bh_day(day, sizeof day, i, 1);
        bh_text(row, sizeof row, i);
        tk_snprintf(out + len, size - len, *day ? "%s%s\n%s\n" : "%s%s%s\n",
                    len && *day ? "\n" : "", day, row);
    }
    return strlen(out);
}

/* Export Log: the log written whole to the card's root, temp and rename; not while a computer has
 * the card (USB storage) or without a megabyte free. */
static int bh_export(void *ctx, void *event) {
    char out[BH_ROWS * 64 + 64];
    if (navigator_window_is_exist("usbmode_page"))
        toast("The card is in use over USB");
    else if (!card_mounted())
        toast("Storage unavailable");
    else if (!card_room(PEQ_ROOT "/mnt/mmc", 1, 0))
        toast("The card is full");
    else if (!blob_io(BH_LOG, BH_LOG ".tmp", out, battery_log(out, sizeof out), 1))
        unlink(BH_LOG ".tmp"), toast("Couldn't save");
    else
        toast("Saved to battery-log.txt");
    return 0;
}

static int bh_closed(void *ctx, void *event) {
    bh.page = bh.graph = bh.view = 0;
    return 0;
}
/* A history row's click: none, but ringnav's wheel and Centre reach only rows with one
 * (navigation.c clickable), and stock's widget_on keeps no null handler. */
static int bh_row(void *ctx, void *event) { return 0; }
/* The page's graph, two rows high, over the list: Export Log, then each row, a day's first with its
 * date. Built again for each new row while it is open (battery_tick; after the screen comes back
 * on, for one that came while it was off), the selected row and the scroll kept. */
static void bh_fill(void) {
    unsigned n = bh.rec.n;
    int sel = bh.view ? widget_get_prop_int(bh.view, "_ringnav_index", -1) : -1,
        offset = bh.view ? widget_get_prop_int(bh.view, "yoffset", 0) : 0;
    bh.stale = 0;
    bh.graph = 0;
    void *view = bh.view = page_list_or(bh.page, bh.page, "Battery History", "Unplug to start",
                                        n ? (int)n + 1 : 0, 48);
    if (!n) return;
    void *list = P(view, W_PARENT);
    int rh = page_h(48), head = 2 * rh, y = I(list, W_Y),
        room = widget_get_prop_int(bh.page, "h", client_h()) - y - head,
        h = I(list, W_H) < room ? I(list, W_H) : room;
    widget_move_resize(list, 0, y + head, 375, h);
    widget_resize(view, 375, h);
    bh.graph = widget_factory_create_widget(widget_factory(), "view", bh.page, 0, y, 375, head);
    page_row_detail(view, 0, "Export Log", 0, bh_export);
    for (unsigned i = 0; i < n; ++i) {
        char day[16], s[64];
        bh_text(s, sizeof s, i);
        bh_day(day, sizeof day, i, 0);
        if (*day) tk_snprintf(s + strlen(s), sizeof s - strlen(s), ", %s", day);
        page_row_detail(view, (int)i + 1, s, 0, bh_row);
    }
    if (sel >= 0) ringnav_select(view, sel, (int)n + 1); /* rows only ever join the end */
    scroll_view_set_offset(view, 0, offset);
}
/* Power's Battery History row. */
int battery_history(void *ctx, void *event) {
    if (bh.page || !(bh.page = page_open("battery_page", bh_closed, page_keyup))) return 0;
    bh_load();
    bh.view = 0;
    bh_fill();
    return 0;
}

/* Row i's x in the plot, from x0 to x0 + w: by its time from the start up to now (to the last row's
 * when now reads earlier, or on the other side of CLOCK_SET from the rows: the clock set or lost
 * since), or evenly by row when the times don't run forward on one clock (set or lost
 * mid-history). 32-bit: the payload has no 64-bit division. */
static int bh_x(unsigned i, int x0, int w, unsigned now) {
    unsigned n = bh.rec.n, from = bh.rec.row[0].at, end = bh.rec.row[n - 1].at;
    int set = from >= CLOCK_SET, forward = 1;
    for (unsigned k = 1; k < n; ++k)
        forward &=
            bh.rec.row[k].at >= bh.rec.row[k - 1].at && (bh.rec.row[k].at >= CLOCK_SET) == set;
    if (!forward) return x0 + (int)i * w / (int)n;
    if ((now >= CLOCK_SET) != set || now < end) now = end;
    unsigned span = now - from, at = i < n ? bh.rec.row[i].at - from : span;
    while (span > 0xffff) span >>= 1, at >>= 1;
    return x0 + (span ? (int)(at * (unsigned)w / span) : (int)i * w / (int)n);
}
/* The graph (ringnav_paint): faint lines at 100%, 50% and 0%, labelled in grey, and the level as
 * steps from the start to now, a 2px line in the accent's light tone over a faint fill, as
 * peq_ui.c's curve. */
void battery_paint(void *w, void *canvas) {
    if (!w || w != bh.graph || !P(canvas, CANVAS_LCD) || !bh.rec.n) return;
    static const char *const labels[] = { "100%", "50%", "0%" };
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR),
             tone = accent_tone(TONE_LIGHT);
    long long wall = wall_time();
    int x0 = page_x() + 40, plot = 375 - page_x() - 8 - x0, top = 10, h = I(w, W_H) - 20;
    for (int k = 0; k < 3; ++k) {
        int y = top + k * h / 2;
        canvas_set_fill_color(canvas, rgba(k == 2 ? 0x4a4a4a : 0x262626, 255));
        canvas_fill_rect(canvas, x0, y, plot, 1);
        caption(canvas, labels[k], page_x(), y - 7, 36, 14, 12, CF_GREY);
    }
    unsigned now = wall == -1 ? 0 : (unsigned)wall;
    for (unsigned i = 0; i < bh.rec.n; ++i) {
        int x = bh_x(i, x0, plot, now), next = bh_x(i + 1, x0, plot, now),
            y = top + h - (int)bh.rec.row[i].pct * h / 100;
        canvas_set_fill_color(canvas, rgba(tone, 0x40));
        canvas_fill_rect(canvas, x, y, next - x, top + h - y);
        canvas_set_fill_color(canvas, rgba(tone, 255));
        canvas_fill_rect(canvas, x, y - 1, next - x + 1, 2);
        if (i + 1 < bh.rec.n) /* the step down to the next row */
            canvas_fill_rect(canvas, next - 1, y - 1, 2,
                             top + h - (int)bh.rec.row[i + 1].pct * h / 100 - y + 2);
    }
    stock_canvas_set_fill_color(canvas, fill);
}
