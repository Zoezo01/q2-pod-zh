/* Logical menu selection is independent of native touch focus. Stock code owns gestures. */
#include "navigation.h"
#include "playlist_art.h"
extern int stock_sorted_query_trampoline(const char *, const char *, void *, int);
extern int stock_seek_time_trampoline(int);
extern int stock_dbquery_trampoline(const char *, const char *, void *, int),
    stock_content_query_trampoline(const char *, const char *, void *, void *, int);
extern int stock_quicksettings_trampoline(void *, void *);
extern int stock_timezone_trampoline(void);
extern void stock_timezone_row_trampoline(void *, int);

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
static void zone_caption(void *item) {
    if (!classic_pages()) return;
    void *list = P(item, W_PARENT); /* item is its last child, the caption's the one before */
    int n = list ? widget_count_children(list) : 0;
    void *cap = n > 1 ? widget_get_child(list, n - 2) : (void *)0;
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
void ringnav_timezone_row(void *item, int i) {
    static const char *const cities[] = TIMEZONE_CITIES;
    static unsigned char rows[TZ_COUNT];
    if (i == 1) tz_now(rows); /* stock builds rows 0, 1, 2, ... */
    int zone = i > 0 && i <= (int)TZ_COUNT ? rows[i - 1] : -1;
    stock_timezone_row_trampoline(item, zone < 0 ? i : zone + 1);
    if (i == 1) zone_caption(item);
    void *row = widget_get_child(item, widget_count_children(item) - 1); /* stock's button */
    for (unsigned c = 0; row && c < widget_count_children(row); ++c) {
        void *label = widget_get_child(row, c);
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
static int library_music __attribute__((section(".scratch")));
static int library_first __attribute__((section(".scratch")));

/* Failed exec in a forked child must not flush demo's copied stdio or run its exit handlers. */
__asm__(".globl child_exit\nchild_exit:\nli $v0, 4001\nsyscall\n");
/* Geometry sets (theme_boot): demo imports neither, so the o32 syscalls, as spot_shut. 0, errno */
int theme_mprotect(void *addr, unsigned len, int prot);
int theme_cacheflush(void *addr, int len, int cache);
__asm__(".set push\n.set noreorder\n"
        ".globl theme_mprotect\ntheme_mprotect:\nli $v0,4125\nsyscall\njr $ra\nnop\n"
        ".globl theme_cacheflush\ntheme_cacheflush:\nli $v0,4147\nsyscall\njr $ra\nnop\n"
        ".set pop\n");
#define THEME_PAGE 4096u /* the Q2's kernel page: mprotect's rounding unit */
/* Out of memory, the kernel kills the fattest process, which was demo (#169): every child demo
 * forks, whose one job can need tens of MB, makes itself the first to go, between fork and exec. */
int open(const char *, int, ...);
void child_oom_first(void) {
    int fd = open("/proc/self/oom_score_adj", 1); /* O_WRONLY */
    if (fd >= 0) write(fd, "1000", 4), close(fd);
}

/* Stock's album-artist queries (Artists, an artist's albums and songs, search) take a song without
 * an album artist by its artist, so an untagged album stays under its artist, not Unknown.
 * Coverflow's grouping (ifnull(albumsonger,'')) keeps the tag. Then restrict the Library's category
 * queries, including Unknown and split albums, before grouping. Coverflow and folder queries keep
 * their own scope. */
#define ALBUM_ARTIST_OR_ARTIST "ifnull(nullif(albumsonger,''),songer)"
/* toolsQueryDbTable, or (content) toolsQueryContentDbTable, which an artist's albums tab
 * (getAlbumByAlbumArtist) runs with its row's arg and the name sort on the stack. */
static int library_query(int content, const char *db, const char *sql, void *row, void *arg,
                         int sort) {
    char aa[2048], filtered[2048];
    const char *s = sql ? strstr(sql, "albumsonger") : 0, *p = sql;
    if (s && !strstr(sql, "ifnull(albumsonger")) {
        unsigned n = 0;
        for (; s && n < sizeof aa; p = s + 11, s = strstr(p, "albumsonger"))
            n += (unsigned)tk_snprintf(aa + n, sizeof aa - n, "%.*s" ALBUM_ARTIST_OR_ARTIST,
                                       (int)(s - p), p);
        if (n >= sizeof aa ||
            n + (unsigned)tk_snprintf(aa + n, sizeof aa - n, "%s", p) >= sizeof aa)
            return -1;
        sql = aa;
    }
    const char *from = library_music && sql ? strstr(sql, " from songtable") : 0;
    if (from) { /* library_first: the Unknown row's check, which one song answers */
        int n = tk_snprintf(filtered, sizeof filtered,
                            "%.*s from (select * from songtable where %s)%s%s", (int)(from - sql),
                            sql, MUSIC_WHERE, from + 15, library_first ? " limit 1" : "");
        if (n < 0 || (unsigned)n >= sizeof filtered) return -1;
        sql = filtered;
    }
    if (sql && strstr(sql, "select * from playlist where id > 100 and cd_num=") == sql &&
        !strstr(sql, "order by")) {
        int n = tk_snprintf(filtered, sizeof filtered, "%s order by id asc", sql);
        if (n < 0 || (unsigned)n >= sizeof filtered) return -1;
        sql = filtered;
    }
    /* getAllPlayList's playlist headers: by name, not creation (stock Rename sets name too) */
    if (sql && !strcmp(sql, "select * from playlist where id <= 100")) {
        int n = tk_snprintf(filtered, sizeof filtered, "%s order by name collate nocase, id", sql);
        if (n < 0 || (unsigned)n >= sizeof filtered) return -1;
        sql = filtered;
    }
    /* Time added (ascending) would walk songtable_time_create backwards and reverse one second's
     * songs (a CUE image's tracks): id keeps stock's order. aa is free: this SQL has no
     * albumsonger. */
    const char *asc = sql ? strstr(sql, " ORDER BY time_create asc;") : 0;
    if (asc) {
        int n =
            tk_snprintf(aa, sizeof aa, "%.*s ORDER BY time_create asc, id", (int)(asc - sql), sql);
        if (n < 0 || (unsigned)n >= sizeof aa) return -1;
        sql = aa;
    }
    /* no memory to expand it: the token alone is SQL's 1, the whole library (library_scope.c) */
    char *expanded = sql && strstr(sql, LIBRARY_SCOPE_TOKEN) ? library_scope_query(sql) : 0;
    const char *query = expanded ? expanded : sql;
    int result = content == 2 ? stock_sorted_query_trampoline(db, query, row, (int)arg)
                 : content    ? stock_content_query_trampoline(db, query, row, arg, sort)
                              : stock_dbquery_trampoline(db, query, row, (int)arg);
    if (expanded) free(expanded);
    return result;
}
int ringnav_library_query(const char *db, const char *sql, void *row, int sort) {
    return library_query(0, db, sql, row, (void *)sort, 0);
}
int ringnav_sorted_query(const char *db, const char *sql, void *row, int sort) {
    return library_query(2, db, sql, row, (void *)sort, 0);
}
int ringnav_content_query(const char *db, const char *sql, void *row, void *arg, int sort) {
    return library_query(1, db, sql, row, arg, sort);
}
extern void *coverflow_tracks(void *page);
extern int coverflow_jump(void *w, int from, int dir, unsigned *letter), coverflow_bounded(void *w),
    coverflow_slide(void *w);
extern void coverflow_lost_touch(void), coverflow_wheel_land(void);
extern int coverflow_wheel_live(void), coverflow_wheel_poll(int usable, int gated),
    coverflow_wheel_on(void *w);
extern void *coverflow_album(void *page);
extern int coverflow_turning(void);
extern int coverflow_goto_album(void *r), coverflow_shows_album(void *r);
extern unsigned coverflow_scope(void *page);
extern void coverflow_home_art(void *top);
extern void coverflow_home_layout(void);
extern int ringnav_reboot(void);
extern void coverflow_home_clip(void *w, void *canvas, int begin);
extern void coverflow_slant(void *w, void *canvas, unsigned paper), coverflow_slant_close(void);
extern int coverflow_home_back(void *top), coverflow_home_owns(void *w);
extern void coverflow_home_top(void);
extern const char *coverflow_home_title(void);
extern void coverflow_home_row(void *surface, void *row, int id, int painting);
extern void lockscreen_paint(void *w, void *canvas);
extern void coverflow_paint(void *w, void *canvas), photos_paint(void *w, void *canvas),
    photos_open(const char *root), books_paint(void *w, void *canvas), books_open(const char *root),
    video_poll(void);
extern int books_key(void *top, unsigned key), video_on(void), books_video(const char *path);
extern void games_paint(void *w, void *canvas);
extern void spot_poll(void), spot_paint(void *w, void *canvas), *spot_art(void);
extern void up_next(int (*keyup)(void *, void *));
extern int un_hold(void *top);
extern void radio_poll(void);
extern void xfade_row(void *label), wheel_row(void *label), resume_row(void *label),
    mono_row(void *label), swap_row(void *label), night_row(void *label);
extern int mono_click(void *ctx, void *event), swap_click(void *ctx, void *event);
extern int xfade_open(void *ctx, void *event), xfade_key(void *top, unsigned key), resume_min(void);
extern int eq_key(void *top, unsigned key); /* peq_ui.c */
extern const char *radio_hold(void *top);
extern void visualizer_paint(void *w, void *canvas), visualizer_attach(void *win);
extern int visualizer_live(void), games_live(void);
extern unsigned char now_cover(void *r, const char **file);
extern int scrobble_append(const char *line, unsigned n);
extern int staged_into(void *into, int (*query)(void *), void *arg);
/* Scope music detail loads too: spoken tracks can share all their tags with music. */
void *staged_music(int (*query)(void *), void *arg, int *count) {
    int saved = library_music;
    library_music = 1;
    void *out = staged(query, arg, count);
    library_music = saved;
    return out;
}

extern int album_only(void *, void *, int), coverflow_split(void *);
#define SCROLL_MARGIN 12
#define HOME_FAST_WINDOW_MS 200
#define WHEEL_RUN_MS 140
#define WHEEL_RAMP_MS 100 /* scrub and the pixel fallback: one step for this long, then */
#define WHEEL_MAX_STEP 8  /* one more per this much spin, up to this many */
#define LIST_FIRST_MS 300 /* iPod row lists: one row per step for this long, */
#define LIST_RAMP_MS 200  /* then one row more per this much spin */
#define CF_FIRST_MS 700   /* Coverflow: one cover per step this long before it jumps by letter */
#define SHORT_LIST_MAX 16
scratch_t st __attribute__((section(".scratch")));

/* Single shared view: no entry point keeps a menu live across a nested load(). */
menu_t g_menu __attribute__((section(".scratch")));

/* Audited top windows, each tagged with its content-identity class and whether its row list is
 * one of the audited local row lists that carry over at the ends. The index is remembered
 * instead of the name pointer: AWTK owns and frees the window's name string. */
enum { CTX_DYNAMIC, CTX_FIXED, CTX_FOLDER, CTX_LOCAL };
enum { RING = 1, DRILL = 2, BUTTONS = 4, CHOICE = 8 };
typedef struct {
    const char *name;
    unsigned char kind, flags;
} context_t;
static const context_t contexts[] = {
#include "contexts.inc"
};

static int context_id(const char *name) {
    if (!name) return -1;
    for (unsigned i = 0; i < sizeof(contexts) / sizeof(*contexts); ++i)
        if (!tk_strcmp(name, contexts[i].name)) return (int)i;
    return -1;
}

/* The audited local row lists built from the compact assets: the file and music views. Grids
 * (album_page, but its Albums list below), other dynamic pages and the home carousel
 * keep hard ends; iPod Home's lists wrap at once (home_menu). */
/* By the top window's name, not m->ctx: a dynamic page (the hold menu) has no memory context.
 * album_page also holds an album's songs (0xff10), which keep hard ends. */
static int ring_list(const menu_t *m) {
    void *top = window_manager_get_top_window(window_manager());
    const char *name = top ? widget_get_prop_str(top, "name", (void *)0) : (void *)0;
    int ctx = context_id(name), ring = ctx >= 0 && (contexts[ctx].flags & RING);
    if (ctx >= 0 && !tk_strcmp(name, "album_page")) ring = I(g_class_type, 0) == CLASS_ALBUMS;
    return m->w && ring && m->rows >= 2;
}

/* The view kinds load() actually navigates: a vertical scroll view, a table client, a slide
 * menu or (iPod) a BUTTONS dialog, which does not scroll. A horizontal or page-snapping scroll
 * view is not a candidate, so it cannot make a page look like it has two panes. */
static int kind(void *w) {
    const char *t = widget_get_type(w);
    if (!tk_strcmp(t, "slide_menu")) return 3;
    if (!tk_strcmp(t, "table_client")) return 2;
    if (!tk_strcmp(t, "dialog") || !tk_strcmp(t, "window")) {
        int ctx = context_id(widget_get_prop_str(w, "name", (void *)0));
        return ctx >= 0 && (contexts[ctx].flags & BUTTONS) ? 4 : 0;
    }
    return !tk_strcmp(t, "scroll_view") && B(w, VIEW_VERTICAL) && !B(w, VIEW_HORIZONTAL) &&
                   !B(w, VIEW_SNAP)
               ? 1
               : 0;
}

/* Collect up to two candidate panes. ponytail: bounded tree walk per event; a persistent cache
 * would go stale because the tree changes without notification, so it waits for a measured
 * stutter. */
static void find_surface(void *w, void **found, int *count, int *aborted, int depth, int *budget) {
    if (!w || !widget_get_visible(w) || !widget_get_prop_bool(w, "enable", 1)) return;
    if (depth == 16 || --*budget < 0) {
        *aborted = 1;
        return;
    }
    if (kind(w)) {
        if (*count < 2) found[*count] = w;
        ++*count;
        return;
    }
    if (!tk_strcmp(widget_get_type(w), "pages")) {
        int active = widget_get_prop_int(w, "active", -1);
        if (active >= 0)
            find_surface(widget_get_child(w, active), found, count, aborted, depth + 1, budget);
        return;
    }
    unsigned n = widget_count_children(w);
    for (unsigned i = 0; i < n && *count < 2; ++i)
        find_surface(widget_get_child(w, i), found, count, aborted, depth + 1, budget);
}

int clamp_step(int offset, int maximum, int delta) {
    if (maximum < 0) maximum = 0;
    offset = offset < 0 ? 0 : offset > maximum ? maximum : offset;
    if (delta > maximum - offset) return maximum;
    if (delta < -offset) return 0;
    return offset + delta;
}

/* One-row steps until a sustained run of accepted same-direction ticks has spun for `first` ms;
 * the step is then two and gains one per `more` ms, capped at WHEEL_MAX_STEP. A pause longer than
 * WHEEL_RUN_MS, a reversal or a change of menu resets the run, so stopping and reversing stay
 * precise. */
static int ramp(void *top, void *surface, unsigned scope, int ctx, int dir, unsigned now, int first,
                int more) {
    int same = st.wheel_dir == dir && st.wheel_top == top && st.wheel_surface == surface &&
               st.wheel_scope == scope && st.wheel_ctx == ctx;
    unsigned gap = now - st.last_wheel;
    if (same && st.wheel_run && gap <= WHEEL_RUN_MS)
        st.wheel_run =
            (unsigned)clamp_step(st.wheel_run, first + (WHEEL_MAX_STEP - 2) * more + 1, gap);
    else
        st.wheel_run = 1;
    st.last_wheel = now;
    st.wheel_dir = dir;
    st.wheel_top = top;
    st.wheel_surface = surface;
    st.wheel_scope = scope;
    st.wheel_ctx = ctx;
    int spin = (int)st.wheel_run - 1;
    return spin < first ? 1 : 2 + (spin - first) / more;
}

/* A tap target has an EVT_CLICK handler. V1.36 widget emitter @0x60; emitter_on_with_tag items are
 * {ctx, id, type @8, handler, tag, working @0x14, pending_remove @0x15, next @0x20}. */
static int clickable(void *w) {
    void *emitter = P(w, W_EMITTER);
    for (void *item = emitter ? P(emitter, 0) : (void *)0; item; item = P(item, EMIT_NEXT))
        if (I(item, EMIT_TYPE) == EVT_CLICK && !B(item, EMIT_PENDING_REMOVE)) return 1;
    return 0;
}

typedef struct {
    void **at;
    int n, cap, budget;
} entries_t;

/* Visible, enabled tap targets in pre-order; a target's descendants belong to it.
 * ponytail: capped walk; raise MAX_ENTRIES if a real list outgrows 512. */
static void collect(void *w, entries_t *s, int depth) {
    if (!w || !widget_get_visible(w) || !widget_get_prop_bool(w, "enable", 1)) return;
    if (depth == 16 || s->n == s->cap || --s->budget < 0) return;
    if (clickable(w)) {
        s->at[s->n++] = w;
        return;
    }
    /* A pages widget exposes only its active child; inactive tabs must not look tappable. */
    if (!tk_strcmp(widget_get_type(w), "pages")) {
        int active = widget_get_prop_int(w, "active", -1);
        if (active >= 0) collect(widget_get_child(w, active), s, depth + 1);
        return;
    }
    unsigned n = widget_count_children(w);
    for (unsigned i = 0; i < n; ++i) collect(widget_get_child(w, i), s, depth + 1);
}

/* Widget-owned properties die with the surface; never retain recycled row pointers. */
#define TOUCH "_ringnav_touch"
#define SCOPE "_ringnav_scope"
#define FX "_ringnav_fx"

/* UI timers, shared with coverflow.c; a failed add leaves the timer 0. */
void stop_timer(unsigned *timer) {
    if (*timer) timer_remove(*timer);
    *timer = 0;
}

void rearm(unsigned *timer, int (*fn)(const void *), unsigned ms) {
    stop_timer(timer);
    *timer = timer_add(fn, (void *)0, ms);
}

/* A page's keyup (Books, Photos, Radio): Return runs its leave from the next loop pass. */
int return_key(void *event, unsigned *timer, int (*leave)(const void *)) {
    if (I(event, EVENT_KEY) != KEY_RETURN) return 0;
    rearm(timer, leave, 0);
    return 11; /* RET_STOP */
}

/* A centre press on a page telling single from double presses (Now Playing, Spotify's): 1 the
 * first, whose single runs DOUBLE_CLICK_MS on unless a second comes; 2 that second; 0 a press
 * after an overdue first, whose single runs now, and stock takes this one. Scrubbing, a press is
 * 2 at once, and one within DOUBLE_CLICK_MS after it 1 with no single, so ending a scrub with one
 * press or two never turns the screen off. */
int center_press(unsigned *timer, unsigned *at, int (*single)(const void *), int scrubbing) {
    unsigned now = (unsigned)time_now_ms();
    if (scrubbing) {
        *at = now;
        return 2;
    }
    if (!*timer) {
        if (now - *at < DOUBLE_CLICK_MS) return 1;
        *at = now;
        *timer = timer_add(single, (void *)0, DOUBLE_CLICK_MS);
        return 1;
    }
    stop_timer(timer);
    if (now - *at < DOUBLE_CLICK_MS) return 2;
    single((void *)0);
    return 0;
}

static void cancel_center(void) {
    stop_timer(&st.center_timer);
    st.center_top = st.center_surface = (void *)0;
}

/* The list's spin ramp starts over, and the next tick is the first of its run. */
static void drop_wheel(void) { st.wheel_run = 0; }

/* A wheel or centre step supersedes any run: drop the spin ramp and home slide. */
static void drop_spin(void) {
    drop_wheel();
    st.home_surface = (void *)0;
}

/* Input that is not navigation here: no pending confirmation and no run survive it. */
void drop_input(void) {
    cancel_center();
    drop_spin();
}

/* Home and Coverflow step their slide_menu with one retargeted animator (home_step): stock
 * scroll_to (0x606290) starts a new animator per call and orphans the running one, so fast
 * ticks leave several fighting over the offset and committing the index twice. */
static int carousel_page(void *top) {
    const char *name = top ? widget_get_prop_str(top, "name", "") : "";
    return !tk_strcmp(name, "home_page") || !tk_strcmp(name, "coverflow_page") ||
           !tk_strcmp(name, "photos_page"); /* its viewer */
}

static int is_home(void *top, void *w) { return w && kind(w) == 3 && carousel_page(top); }

/* iPod Home's lists (the main menu and its Settings list): their ends wrap at once, and Return
 * goes back to the first row. */
static int home_menu(void *top) {
    return top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "home_page");
}

/* A list row: a list_item or table_row. */
static int is_row(void *w) {
    const char *t = w ? widget_get_type(w) : "";
    return !tk_strcmp(t, "list_item") || !tk_strcmp(t, "table_row");
}

/* A widget's own window: the top-level widget it sits in. */
void *window_of(void *w) {
    void *wm = window_manager();
    while (w && P(w, W_PARENT) != wm) w = P(w, W_PARENT);
    return w;
}

/* A page that opens and closes with stock's slide (tools/ipod.py SLIDE). Stock paints it and
 * the page under it into the animator's snapshots while it is the top window and before
 * window_manager_is_animating is set; nothing else paints the page under it. */
static int slides(void *win) {
    /* Now Playing with a hint holds it as close_anim_hint, which this doesn't read. */
    if (win && win == st.np_win && theme()->motion[MOTION_PLAYING].hint) return 1;
    const char *hint = win ? widget_get_prop_str(win, "anim_hint", (void *)0) : (void *)0;
    return hint && *hint;
}

/* win's anim_hint becomes the theme's for kind, at load or open only. A theme with no hint for kind
 * reads and writes nothing. Now Playing takes it as close_anim_hint, its open stays immediate. No
 * kind reaches a dialog: a clear one's snapshot would show black under its animation. */
static void motion_hint(void *win, int kind) {
    const char *hint = theme()->motion[kind].hint;
    if (!hint) return;
    const char *prop = kind == MOTION_PLAYING ? "close_anim_hint" : "anim_hint";
    if (tk_strcmp(widget_get_prop_str(win, prop, ""), hint)) widget_set_prop_str(win, prop, hint);
}
/* A window as it loads. Themes with no window hints read nothing. */
static void motion_load(void *root, int np) {
    const motion_t *M = theme()->motion;
    if (!M[MOTION_PAGE].hint && !M[MOTION_PLAYING].hint) return;
    if (np)
        motion_hint(root, MOTION_PLAYING);
    else if (slides(root))
        motion_hint(root, MOTION_PAGE);
}
/* page_open's page: Cover Flow's takes the theme's hint, if it has one. */
void motion_open(void *page, const char *name) {
    if (!tk_strcmp(name, "coverflow_page")) motion_hint(page, MOTION_COVERFLOW);
}

/* The animator's destination is the intended icon, even before stock commits its index.
 * Keep this widget-owned: touch and page recreation cannot leave a dangling animator here. */
static int slide_index(void *w) {
    void *a = P(w, SLIDE_ANIMATOR);
    int n = (int)widget_count_children(w);
    if (!n) return -1;
    int id = I(w, SLIDE_INDEX);
    int stride = slide_menu_item_width(w) + I(w, SLIDE_SPACER);
    if (a && stride > 0) id -= I(a, ANIM_X_TO) / stride;
    id %= n;
    return id < 0 ? id + n : id;
}

static int home_done(void *w, void *event) {
    slide_menu_on_scroll_done(w, event);
    /* Stock skips focus restoration when a reversal returns to the original index. */
    widget_set_focused(widget_get_child(w, I(w, SLIDE_INDEX)), 1);
    return 7; /* RET_REMOVE: same one-shot lifetime as stock completion */
}

static void *surface(void *target, void **other);

static int cf_letter_expire(const void *info) {
    st.cf_letter_timer = st.cf_letter = 0;
    void *w = surface(0, 0);
    if (w && w == st.cf_letter_surface) widget_invalidate_force(w, 0);
    return 0;
}

extern float coverflow_follow(float k);
extern int coverflow_follow_aim(int d, int tau, int elapsed);
/* w's one animator, the wheel's and Coverflow's touch's, from the live offset to goal in ms with
 * easing (AWTK's easing_type_t), made on first use; 0 if it cannot be, the move committed at once.
 */
int carousel_glide(void *w, int goal, unsigned ms, int easing) {
    void *a = P(w, SLIDE_ANIMATOR);
    if (a && !ms) { /* ms 0 is the immediate move, also over a glide still running */
        widget_animator_pause(a);
        widget_animator_destroy(a);
        a = (void *)0;
    }
    if (!a) {
        a = ms ? widget_animator_scroll_create(w, ms, 0, easing == EASE_FOLLOW ? 0 : easing)
               : (void *)0;
        if (a && !widget_animator_on(a, EVT_ANIM_END, home_done, w)) {
            widget_animator_destroy(a);
            a = (void *)0;
        }
        P(w, SLIDE_ANIMATOR) = a;
        if (!a) {
            int n = (int)widget_count_children(w),
                stride = slide_menu_item_width(w) + I(w, SLIDE_SPACER);
            int id = n && stride > 0 ? (I(w, SLIDE_INDEX) - goal / stride) % n : 0;
            I(w, SLIDE_OFFSET) = 0;
            slide_menu_set_value(w, id < 0 ? id + n : id);
            widget_set_focused(widget_get_child(w, I(w, SLIDE_INDEX)), 1);
            return 0;
        }
        widget_set_focused(widget_get_child(w, I(w, SLIDE_INDEX)), 0);
    }
    widget_animator_pause(a);
    widget_animator_scroll_set_params(a, I(w, SLIDE_OFFSET), 0, goal, 0);
    if (easing == EASE_FOLLOW) /* ms is its time constant; the distance gives the run's length */
        ms = (unsigned)coverflow_follow_aim(
            goal - I(w, SLIDE_OFFSET), (int)ms,
            P(a, ANIM_EASING) == (void *)coverflow_follow ? I(a, ANIM_ELAPSED) : -1);
    I(a, ANIM_ELAPSED) = I(a, ANIM_START_TIME) = 0;
    I(a, ANIM_DURATION) = ms;
    /* scroll_create's own, so a reused one takes this run's */
    P(a, ANIM_EASING) = easing == EASE_FOLLOW ? (void *)coverflow_follow : easing_get(easing);
    widget_animator_start(a);
    return 1;
}

/* Cover Flow's big letter over w for LETTER_MS, or none: the wheel's jumps and the thumb's. */
void coverflow_letter(void *w, unsigned letter, unsigned now) {
    st.cf_letter = letter;
    st.cf_letter_at = now;
    st.cf_letter_surface = w;
    st.cf_letter_held = 0;
    if (letter)
        rearm(&st.cf_letter_timer, cf_letter_expire, LETTER_MS);
    else
        stop_timer(&st.cf_letter_timer);
}

/* The thumb's letter over w with no LETTER_MS: its letters last until it lifts, which clears it
 * (coverflow_letter(w, 0, now)). */
void coverflow_letter_hold(void *w, unsigned letter) {
    coverflow_letter(w, 0, 0);
    st.cf_letter = letter, st.cf_letter_held = 1;
}

static void home_step(void *w, int dir, unsigned now, int jump) {
    int n = (int)widget_count_children(w);
    int stride = slide_menu_item_width(w) + I(w, SLIDE_SPACER);
    int fast =
        st.home_surface == w && st.home_dir == dir && now - st.last_home <= HOME_FAST_WINDOW_MS;
    void *a = P(w, SLIDE_ANIMATOR);
    int live = I(w, SLIDE_OFFSET);
    int goal = a ? I(a, ANIM_X_TO) : 0;
    /* Reversal discards the unfinished destination and heads to the adjacent card
     * behind the live position. Same-direction ticks extend the intended destination. */
    if (a && (goal - live) * dir > 0) {
        goal = (live / stride) * stride;
        if (dir > 0 ? goal >= live : goal <= live) goal -= dir * stride;
    } else
        goal -= dir * stride;
    unsigned letter = 0;
    int from = (I(w, SLIDE_INDEX) - goal / stride - dir) % n;
    if (from < 0) from += n;
    int steps = jump ? coverflow_jump(w, from, dir, &letter) : 1;
    goal -= dir * stride * (steps - 1);
    int to = I(w, SLIDE_INDEX) - goal / stride;
    if (coverflow_bounded(w) && (to < 0 || to >= n)) {
        goal += dir * stride * steps;
        if (a && (goal - live) * dir < 0) return; /* still arriving at the end */
        I(w, SLIDE_OFFSET) = live = goal - dir * stride / CF_BOUNCE;
    }
    coverflow_letter(w, letter, now);
    const motion_t *glide =
        &theme()->motion[(coverflow_slide(w) ? MOTION_COVERFLOW_STEP : MOTION_STEP) + fast];
    if (!carousel_glide(w, goal, glide->ms, glide->easing)) {
        st.home_surface = (void *)0;
        return;
    }
    st.home_surface = w;
    st.last_home = now;
    st.home_dir = dir;
}

int usable(void) {
    if (!g_backlight_status || g_lockscreen_pageflag || g_testmode_flag || g_guideflag ||
        g_poweroff_state > 1)
        return 0;
    void *top = window_manager_get_top_window(window_manager());
    int confirm = top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "confirminfo_dialog");
    /* A held power key sets g_poweroff_state to 1 while its Confirm Shutdown prompt is up
     * (on_wm_keylong_fun); 2 is the power-off page itself. */
    if (g_poweroff_state) return confirm;
    if (g_usblink_status != 2) return 1;
    /* USB mode's exit asks whether to scan in a confirm dialog before it leaves the mode: once the
     * cable is out, that prompt takes the wheel and Centre. */
    return !g_usbdet_value && confirm;
}

static int usb_prompt(void) {
    void *top = window_manager_get_top_window(window_manager());
    return g_usblink_status == 2 && !g_usbdet_value && usable() && top &&
           !tk_strcmp(widget_get_prop_str(top, "name", ""), "confirminfo_dialog");
}

static int allowed_top(void *top) {
    return top && context_id(widget_get_prop_str(top, "name", (void *)0)) >= 0;
}
int pointer_down(void) { return window_manager_get_pointer_pressed(window_manager()); }

/* A click identifies its pane by ancestry. On the first wheel turn, an unowned pair starts
 * with the first pane in UI order; painting and touch never choose one implicitly. */
static void *surface_under(void *top, void *target, void **other, int wheel) {
    void *found[2] = { (void *)0, (void *)0 };
    int count = 0, aborted = 0, budget = 512;
    find_surface(top, found, &count, &aborted, 0, &budget);
    if (aborted || count == 0) return (void *)0;
    if (count == 1) return found[0];
    if (target) {
        for (int depth = 0; target && depth < 32; ++depth) {
            for (int i = 0; i < count; ++i)
                if (target == found[i]) {
                    if (other) *other = found[1 - i];
                    return found[i];
                }
            if (target == top) break;
            target = P(target, W_PARENT);
        }
        return (void *)0;
    }
    int first = widget_get_prop_int(found[0], SEL, -1) >= 0;
    int second = widget_get_prop_int(found[1], SEL, -1) >= 0;
    if (wheel && !first && !second) return found[0];
    return first == second ? (void *)0 : (first ? found[0] : found[1]);
}

static void *surface(void *target, void **other) {
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    void *w = usable() && !window_manager_is_animating(wm) && allowed_top(top)
                  ? surface_under(top, target, other, 0)
                  : (void *)0;
    if (st.center_timer && (top != st.center_top || w != st.center_surface)) cancel_center();
    if (!is_home(top, w) || w != st.home_surface) st.home_surface = (void *)0;
    return w;
}

void prop(void *w, const char *name, int value) {
    if (widget_get_prop_int(w, name, -1) != value) widget_set_prop_int(w, name, value);
}

/* The bump timer repaints at 120 ms; the second-detent arm survives independently. */
static void fx_cancel(void) {
    stop_timer(&st.fx_timer);
    st.fx_surface = (void *)0;
    st.bump_dir = 0;
}

static int fx_live(void *w) {
    return w == st.fx_surface && widget_get_prop_int(w, FX, 0) == st.fx_token;
}

static int fx_expire(const void *info) {
    st.fx_timer = 0;
    void *w = surface((void *)0, (void *)0);
    st.bump_dir = 0;
    if (w && fx_live(w)) widget_invalidate_force(w, (void *)0);
    return 0;
}

static void fx_arm(void *w, int dir) {
    rearm(&st.fx_timer, fx_expire, BUMP_MS);
    st.fx_token = st.fx_token == 0x7fffffff ? 1 : st.fx_token + 1;
    st.fx_surface = w;
    st.bump_dir = st.fx_timer ? dir : 0;
    prop(w, FX, st.fx_token);
}

/* A boundary detent arms only while the selection stays on that row; leaving it or reversing
 * makes the next boundary detent bump again. Every stopped detent re-arms, so a continuing spin
 * hard-stops at the end; only a detent after a pause of EDGE_PAUSE_MS carries over. */
static int edge_wraps(const menu_t *m, int id, int dir, unsigned now) {
    return id >= 0 && fx_live(m->w) && st.edge_id == id && st.edge_dir == dir &&
           now - st.edge_time >= EDGE_PAUSE_MS;
}

/* FNV-1a of the first two non-empty text properties in a small row subtree: the item's own name
 * and an optional subtitle, in pre-order. Each stays 0 while its text has not been seen. No stock
 * list row carries a stable id: emitter tags and pointer props are unused by the app rows (only
 * ROW_INDEX, which a re-sort rewrites), so these hashes are the identity available to restore. */
typedef struct {
    unsigned one, two;
    const unsigned *title; /* the first text itself, for the iPod fast-scroll letter */
} row_id_t;

/* The library browsing state both position memory and the queue menu key on. */
static unsigned local_hash(unsigned h) {
    h = hash_bytes(h, g_class_type, 4);
    h = hash_bytes(h, g_local_classinfo_save, 912);
    return hash_bytes(h, g_artist_type, 4);
}

static void row_hash_text(const unsigned *s, unsigned *h) {
    unsigned n = 0;
    while (s[n]) ++n;
    /* Stock wchar_t is UTF-32; hash all four bytes, zero bytes inside a character included. */
    *h = hash_bytes(FNV_SEED, (const unsigned char *)s, 4 * n);
    if (!*h) *h = 1; /* zero denotes missing text */
}

static void row_id_walk(void *w, int depth, int *budget, row_id_t *id) {
    if (!w || depth == 4 || id->two || --*budget < 0) return;
    const unsigned *s = widget_get_text(w);
    if (s && *s) {
        if (!id->one) {
            row_hash_text(s, &id->one);
            id->title = s;
        } else {
            row_hash_text(s, &id->two);
            return;
        }
    }
    unsigned n = widget_count_children(w);
    for (unsigned i = 0; i < n; ++i) row_id_walk(widget_get_child(w, i), depth + 1, budget, id);
}

static row_id_t row_id(void *w) {
    row_id_t id = { 0, 0, (void *)0 };
    int budget = 32;
    row_id_walk(w, 0, &budget, &id);
    return id;
}

/* The local list loaders use these browsing globals: folder_enter/back maintain g_folder_path;
 * load_localclass_list/load_album_detaillist use the class, saved query and artist/album modes.
 * Hash the bounded query object, including its flags, rather than a title or a freed pointer. */
static int context_now(unsigned *scope) {
    void *top = window_manager_get_top_window(window_manager());
    const char *name = top ? widget_get_prop_str(top, "name", (void *)0) : (void *)0;
    *scope = 0;
    if (!name) return -1;
    int ctx = context_id(name);
    if (ctx < 0) return -1;
    void *found[2];
    int count = 0, aborted = 0, budget = 512;
    find_surface(top, found, &count, &aborted, 0, &budget);
    if (contexts[ctx].kind == CTX_FOLDER) {
        unsigned n = 0;
        while (n < 1024 && g_folder_path[n]) ++n;
        if (!n || n == 1024) return -1;
        *scope = hash_bytes(FNV_SEED, g_folder_path, n);
    } else if (!tk_strcmp(name, "playlist_page")) {
        /* The list is the same whatever opening a row saves in the browsing state, so back keeps
         * the row; the Add to Playlist picker keeps its own. */
        *scope = (unsigned)widget_get_prop_int(top, PL_MODE, 0) + 1;
    } else if (contexts[ctx].kind == CTX_LOCAL) {
        unsigned h = local_hash(FNV_SEED);
        int cls = I(g_class_type, 0);
        /* Root collections ignore the saved detail query, which opening an artist overwrites. */
        if (!tk_strcmp(name, "localclass_page") && cls >= CLASS_ALBUMS && cls <= 0xf006) {
            h = hash_bytes(FNV_SEED, g_class_type, 4);
            h = hash_bytes(h, g_artist_type, 4);
        }
        *scope = hash_bytes(h, album_modetype, 4);
    } else if (contexts[ctx].kind == CTX_FIXED) {
        *scope = 1;
    } else if (!tk_strcmp(name, "coverflow_page")) {
        *scope = coverflow_scope(top);
        if (!*scope) return -1;
    } else {
        if (!tk_strcmp(name, "search_dialog") || !tk_strcmp(name, "tidal_search_dialog")) {
            void *edit = widget_lookup(top, "search_edit", 1);
            row_id_t query = row_id(edit);
            *scope = query.one;
        }
        /* Network/detail pages without an audited content key keep widget-owned selection, but
         * never import another page's row. */
        return -1;
    }
    if (!*scope) *scope = 1;
    return !aborted && count == 1 ? ctx : -1; /* no cross-pane position memory */
}

static int index_of(menu_t *m, int id);

/* Bounded recency order avoids a timestamp that could wrap during a long session. */
static int position(menu_t *m) {
    if (m->ctx >= 0)
        for (int i = 0; i < POS_MEM; ++i)
            if (st.pos[i].id && st.pos[i].ctx == m->ctx && st.pos[i].scope == m->scope) return i;
    return -1;
}

/* Remember where the user was. Widget props die with a recreated page; this survives it.
 * Non-virtual lists also remember the row's first two text values, so a reordered list restores
 * the same item and duplicate names can be told apart by their second line. */
static void select(menu_t *m, int id) {
    if (st.center_timer && (m->w != st.center_surface || id != st.center_id)) cancel_center();
    if (st.reveal_surface == m->w) st.reveal_surface = (void *)0;
    unsigned hash = 0, hash2 = 0;
    if (id >= 0) {
        int ctx = m->ctx;
        if (m->kind == 1) {
            int i = index_of(m, id);
            if (i >= 0) {
                row_id_t r = row_id(m->at[i]);
                hash = r.one;
                hash2 = r.two;
            }
        }
        if (ctx >= 0) {
            int p = position(m);
            if (p < 0) p = POS_MEM - 1;
            for (; p > 0; --p) st.pos[p] = st.pos[p - 1];
            st.pos[0] = (position_t){ ctx, id + 1, m->scope, hash, hash2 };
        }
    }
    prop(m->w, SEL, id);
}

int load_rows(menu_t *m, void *w) {
    m->w = w;
    m->n = 0;
    m->kind = kind(w);
    m->height = I(w, W_H);
    if (!m->kind || m->height <= 0) return 0;
    m->row = m->kind == 2 ? I(w, ROW_HEIGHT) : 0;
    m->rows = m->kind == 2 ? I(w, TABLE_ROWS) : 0;
    if (m->kind == 2 && (m->row <= 0 || m->rows < 0 || m->rows > 0x7fffffff / m->row)) return 0;
    unsigned n = widget_count_children(w);
    if (m->kind == 1 && I(w, VIEW_CONTENT_H) < 0) return 0;
    if (m->kind == 1 || m->kind == 4) {
        entries_t s = { m->at, 0, MAX_ENTRIES, 4096 };
        for (unsigned i = 0; i < n && s.n < s.cap && s.budget >= 0; ++i)
            collect(widget_get_child(w, i), &s, 1);
        m->n = s.n;
        m->rows = s.n;
        for (int i = 0; i < m->n; ++i) m->id[i] = i;
    } else {
        if (m->kind == 3) m->rows = (int)n;
        for (unsigned i = 0; i < n && m->n < MAX_ENTRIES; ++i) {
            void *r = widget_get_child(w, i), *e = (void *)0;
            entries_t s = { &e, 0, 1, 256 };
            collect(r, &s, 0);
            int id = m->kind == 2 ? I(r, ROW_INDEX) : (int)i;
            if (e && id >= 0 && id < m->rows) {
                m->at[m->n] = e;
                m->id[m->n++] = id;
            }
        }
    }
    return 1;
}

/* The Theme picker's rows, while it shows. ponytail: compared, never dereferenced; the picker is
 * its only caller in use. */
static void *theme_view __attribute__((section(".scratch")));

/* A row holding stock's "select" check image: a CHOICE picker's current option. */
static int checked(void *w, int depth, int *budget) {
    if (!w || depth == 16 || --*budget < 0 || !widget_get_visible(w)) return 0;
    if (!tk_strcmp(widget_get_prop_str(w, "image", ""), "select")) return 1;
    unsigned n = widget_count_children(w);
    for (unsigned i = 0; i < n; ++i)
        if (checked(widget_get_child(w, i), depth + 1, budget)) return 1;
    return 0;
}

static int checked_row(menu_t *m) {
    for (int i = 0; i < m->n; ++i) {
        int budget = 256;
        if (checked(m->at[i], 0, &budget)) return m->id[i];
    }
    return -1;
}

static int load(menu_t *m, void *w, int recall) {
    if (!load_rows(m, w)) return 0;
    m->ctx = context_now(&m->scope);
    int count = widget_get_prop_int(w, COUNT, -1);
    unsigned scope = (unsigned)widget_get_prop_int(w, SCOPE, 0);
    if (scope != m->scope) {
        count = -1;
        prop(w, SCOPE, (int)m->scope);
        if (st.reveal_surface == w) st.reveal_surface = (void *)0;
    }
    if (st.fx_surface && (st.fx_surface != w || scope != m->scope || count != m->rows)) fx_cancel();
    if (count != m->rows) {
        if (st.wheel_surface == w) drop_wheel();
        prop(w, SEL, -1);
        prop(w, COUNT, m->rows);
    }
    /* A recreated page has never seen this surface (COUNT unset) and lost its selection: put the
     * user back where they left off. A live page whose count changed resets the selection but
     * keeps its viewport, so it does not re-read the table. A non-virtual list prefers the
     * remembered first text, breaks ties by the second text and then by the remembered index,
     * and falls back to the index when no text matches. */
    if (m->kind != 3 && count < 0 && (recall || theme()->set_text || w == theme_view)) {
        /* A picker opens on its current option rather than the row last chosen; the Theme picker
         * (and a set_text theme's others) also while a tap that opened it is still down, or a
         * Centre takes its first row. */
        void *top = window_manager_get_top_window(window_manager());
        int ctx = top ? context_id(widget_get_prop_str(top, "name", (void *)0)) : -1;
        int choice =
            (ctx >= 0 && (contexts[ctx].flags & CHOICE)) || w == theme_view ? checked_row(m) : -1;
        int p = choice >= 0 || !recall ? -1 : position(m);
        int id = choice >= 0 ? choice : p < 0 ? -1 : st.pos[p].id - 1;
        unsigned hash = p < 0 ? 0 : st.pos[p].hash;
        unsigned hash2 = p < 0 ? 0 : st.pos[p].hash2;
        if (id >= 0 && m->kind == 1 && hash) {
            /* A secondary-text match outranks proximity; equal ranks keep the earlier row. */
            int best = -1, best_dist = 0, best_second = 0;
            for (int i = 0; i < m->n; ++i) {
                row_id_t r = row_id(m->at[i]);
                if (r.one != hash) continue;
                int dist = m->id[i] - id;
                if (dist < 0) dist = -dist;
                int second = hash2 && r.two == hash2;
                if (best < 0 || second > best_second ||
                    (second == best_second && dist < best_dist)) {
                    best = i;
                    best_dist = dist;
                    best_second = second;
                }
            }
            if (best >= 0) id = m->id[best];
        }
        if (id >= 0 && id < m->rows) {
            prop(w, SEL, id);
            /* A sliding page's first paint is its snapshot: arrive in place, not mid-glide. */
            reveal(m, id, slides(window_manager_get_top_window(window_manager())));
            st.reveal_surface = m->w;
            st.reveal_id = id;
            /* A synchronous table rebind changes the row pool; discard the pre-scroll snapshot. */
            if (m->kind == 2) return load_rows(m, w);
        }
    }
    return 1;
}

/* Live viewport offset. reveal glides are relative, so every delta is computed against the
 * position the widget is actually at, never an intended one. */
static int view_top(menu_t *m) {
    return m->kind == 2 ? I(m->w, TABLE_TOP) : m->kind == 1 ? I(m->w, SCROLL_Y) : 0;
}

/* The height a selected row scrolls in: the surface's, cut at the set's list_end, a row of the
 * client under the bar (W_Y summed up to the window), where the rows below draw on to the glass but
 * a selected one's text would not show. */
static int sel_height(menu_t *m) {
    int y = 0, end = geom_val(V_LIST_END);
    if (!end || m->kind == 3 || m->kind == 4) return m->height;
    void *wm = window_manager();
    for (void *p = m->w; p && P(p, W_PARENT) != wm; p = P(p, W_PARENT)) y += I(p, W_Y);
    return end - y < m->height ? end - y : m->height;
}

/* Largest viewport top that still shows content; the clamp bound for every glide. */
static int scroll_end(menu_t *m, int height) {
    if (m->kind == 2) return m->rows * m->row - height;
    return m->kind == 1 ? I(m->w, VIEW_CONTENT_H) - height : 0;
}
static int max_top(menu_t *m) { return scroll_end(m, sel_height(m)); }

static int speed_stop(void *e) {
    return !tk_strcmp(widget_get_type(e), "image") &&
           !tk_strcmp(widget_get_prop_str(P(e, W_PARENT), "style", ""), "s_btn_forwardspeed");
}
static rect_t bounds(menu_t *m, int i) {
    void *e = m->at[i];
    rect_t r = { 0, 0, I(e, W_W), I(e, W_H) };
    /* Speed images have 61px hit targets, but their visible buttons are only 24px high. */
    if (speed_stop(e) && r.h > 24) r.h = 24;
    for (void *p = e; p && p != m->w; p = P(p, W_PARENT)) {
        r.x += I(p, W_X);
        r.y += I(p, W_Y);
    }
    if (m->kind != 3) r.y -= view_top(m);
    if (m->kind == 1) r.x -= I(m->w, SCROLL_X);
    return r;
}

static int index_of(menu_t *m, int id) {
    for (int i = 0; i < m->n; ++i)
        if (m->id[i] == id) return i;
    return -1;
}

static int moving(menu_t *m) {
    return m->kind == 1   ? P(m->w, VIEW_ANIMATOR) != 0
           : m->kind == 2 ? P(m->w, TABLE_ANIMATOR) != 0
                          : 0;
}

static void stop_scroll(menu_t *m) {
    if (m->kind == 2)
        table_client_stop_animator_scroll(m->w);
    else if (m->kind == 1 && P(m->w, VIEW_ANIMATOR)) {
        /* Same pause/destroy/null sequence as stock table_client_stop_animator_scroll. */
        void *a = P(m->w, VIEW_ANIMATOR);
        widget_animator_pause(a);
        widget_animator_destroy(a);
        P(m->w, VIEW_ANIMATOR) = (void *)0;
    }
}

/* View w's scroll animation stopped, for a touch claimed sideways (addon.c swipe, as pull_event).
 */
void list_stop_scroll(void *w) {
    if (load_rows(&g_menu, w)) stop_scroll(&g_menu);
}

/* Wheel offsets are synchronous; table setters can replace the recycled row pool. Scroll end
 * loads the covers in view, as after a touch scroll. */
static void wheel_offset(menu_t *m, int top) {
    stop_scroll(m);
    if (m->kind == 2) {
        table_client_set_yoffset(m->w, top);
        load_rows(m, m->w);
    } else if (m->kind == 1) {
        scroll_view_set_offset(m->w, I(m->w, SCROLL_X), top);
        /* The immediate setter skips the on_scroll callback that touch scrolling runs. */
        int (*on_scroll)(void *, int, int) = P(m->w, VIEW_ON_SCROLL);
        if (on_scroll) on_scroll(m->w, I(m->w, SCROLL_X), I(m->w, SCROLL_Y));
        widget_dispatch_simple_event(m->w, EVT_SCROLL);
    } else
        return;
    widget_dispatch_simple_event(m->w, EVT_SCROLL_END);
}

/* Least viewport move that reveals logical row id with a small reading margin.
 * Remembered-position restoration keeps its glide; wheel steps are immediate. */
void reveal(menu_t *m, int id, int immediate) {
    int top = view_top(m);
    int y, h;
    if (m->kind == 2) {
        y = id * m->row;
        h = m->row;
    } else {
        int i = index_of(m, id);
        if (i < 0) return;
        rect_t r = bounds(m, i);
        y = r.y + top;
        h = r.h;
    }
    /* A tall row cannot fit: show its title consistently instead of alternating edges. */
    int height = sel_height(m), margin = clamp_step((height - h) / 2, SCROLL_MARGIN, 0);
    int want = h > height || y - top < margin  ? y - margin
               : y - top > height - h - margin ? y - (height - h - margin)
                                               : top;
    /* The first row brings in whatever heads the list above it (the EQ page's graph) when both
     * fit, rather than stopping a margin into it. Kind 1's ids are its row order. */
    if (m->kind == 1 && id == 0 && y + h + margin <= height) want = 0;
    want = clamp_step(want, max_top(m), 0);
    const theme_t *T = theme();
    if (immediate || !T->recall_ms) {
        wheel_offset(m, want);
        return;
    }
    if (want == top) return;
    /* Stock scroll views cannot retarget an animator whose old goal is a boundary. */
    stop_scroll(m);
    if (m->kind == 2) {
        table_client_scroll_to(m->w, want);
    } else
        scroll_view_scroll_delta_to(m->w, 0, want - top, T->recall_ms);
}

/* A payload page's own row: establish its scope and viewport before paint can recall another. */
void ringnav_select(void *w, int id, int rows) {
    /* A list under another window (a card app's pages still arriving) keeps the props alone: its
     * load would take g_menu and the top list's glide and big letter from under the wheel. */
    void *win = window_of(w);
    int loaded =
        (!win || win == window_manager_get_top_window(window_manager())) && load(&g_menu, w, 0);
    prop(w, COUNT, rows);
    prop(w, SEL, id);
    if (loaded) reveal(&g_menu, id, 1);
}

/* Keep selection during native momentum and recall glides. Once settled, repair an offscreen
 * selection with the visible row nearest the viewport centre (ties go to the earlier row), so a
 * swipe never leaves the highlight pinned to the top edge. An interrupted recall glide is
 * retried once instead: the remembered row must not be silently replaced by a visible one. */
static int reconcile(menu_t *m, int settle) {
    int id = m->kind == 3 ? slide_index(m->w) : widget_get_prop_int(m->w, SEL, -1);
    int cur = index_of(m, id);
    if (m->kind == 3) return cur;
    if (cur >= 0) {
        rect_t r = bounds(m, cur);
        if (r.y < m->height && r.y + r.h > 0) {
            st.reveal_surface = (void *)0; /* the glide arrived, or the user brought it back */
            return cur;
        }
        if (!settle) return cur;
    } else if (id >= 0 && id < m->rows && !settle)
        return -1;
    if (st.reveal_surface == m->w && st.reveal_id == id) {
        st.reveal_surface = (void *)0;
        reveal(m, id, 0);
        if (m->kind == 2) return load_rows(m, m->w) ? index_of(m, id) : -1;
        return cur;
    }
    int best = -1, best_dist = 0, partial = -1, partial_dist = 0, had = id >= 0;
    for (int i = 0; i < m->n; ++i) {
        rect_t r = bounds(m, i);
        if (r.y >= m->height || r.y + r.h <= 0) continue;
        int dist = r.y + r.h / 2 - m->height / 2;
        if (dist < 0) dist = -dist;
        if (r.y >= 0 && r.y + r.h <= m->height) {
            if (best < 0 || (had && dist < best_dist)) {
                best = i;
                best_dist = dist;
            }
            if (!had) break; /* a fresh page starts on the first visible row */
        } else if (partial < 0 || (had && dist < partial_dist)) {
            partial = i;
            partial_dist = dist;
        }
    }
    cur = best >= 0 ? best : partial;
    if (!had && cur >= 0) cur = pl_first(m, cur);
    st.reveal_surface = (void *)0;
    select(m, cur >= 0 ? m->id[cur] : -1);
    return cur;
}

/* Resolve only live rows. Widget-owned tokens reject a recreated window/surface even when
 * the allocator reuses its address; text hashes reject a rebound item at the same index. */
#define CONFIRM "_ringnav_confirm"
static int pending_matches(void *top, menu_t *m) {
    int id = m->kind == 3 ? slide_index(m->w) : widget_get_prop_int(m->w, SEL, -1);
    int i = index_of(m, id);
    if (top != st.center_top || m->w != st.center_surface || m->scope != st.center_scope ||
        m->ctx != st.center_ctx || m->rows != st.center_rows || id != st.center_id || i < 0 ||
        (unsigned)widget_get_prop_int(top, CONFIRM, 0) != st.center_token ||
        (unsigned)widget_get_prop_int(m->w, CONFIRM, 0) != st.center_token)
        return 0;
    /* Ordinary rows must still be the armed widget, even with duplicate/missing text.
     * Virtual tables instead resolve the logical row across recycled pool widgets. */
    if (m->kind != 2 && (unsigned)widget_get_prop_int(m->at[i], CONFIRM, 0) != st.center_token)
        return 0;
    row_id_t r = row_id(m->at[i]);
    return r.one == st.center_hash && r.two == st.center_hash2;
}

static int confirm_center(const void *info) {
    void *w = surface((void *)0, (void *)0);
    if (!st.center_timer) return 0;
    /* Stock removes this one-shot after return; never repeat (RET_REPEAT=8). */
    st.center_timer = 0;
    void *top = window_manager_get_top_window(window_manager());
    int valid = w && !pointer_down() && !g_power_longkey && !g_ingore_bootkey_flag &&
                !*(volatile unsigned char *)BOOT_KEY_GUARD && load_rows(&g_menu, w);
    if (valid) {
        /* Validation must not recall/rebind a changed scope or consume its pending recall. */
        g_menu.ctx = context_now(&g_menu.scope);
        valid = pending_matches(top, &g_menu);
    }
    cancel_center(); /* Clear before any app callback can destroy or navigate the page. */
    if (valid) {
        /* Stock's wheel lockout after the centre key (up to 800 ms) has guarded the press; the
         * page it opens scrolls at once. */
        *(volatile unsigned char *)KEY_LOCKOUT = 0;
        void *target = g_menu.at[index_of(&g_menu, st.center_id)];
        char click[0x30];
        stock_dispatch_trampoline(target, pointer_event_init(click, EVT_CLICK, target, 0, 0));
    }
    return 0;
}

/* Native scroll_to with the bar's current value only wakes its opacity lifecycle.
 * Stock setters retain ownership of thumb position. A transparent bar is still a bar.
 * The native same-value path shows immediately, waits 300 ms and fades over 500 ms. */
static void native_scrollbar(menu_t *m) {
    if (m->kind == 3 || max_top(m) <= 0) return;
    void *parent = P(m->w, W_PARENT);
    if (!parent) return;
    const char *type = widget_get_type(parent);
    if (tk_strcmp(type, "list_view") && tk_strcmp(type, "table_view")) return;
    unsigned n = widget_count_children(parent);
    for (unsigned i = 0; i < n; ++i) {
        void *bar = widget_get_child(parent, i);
        if (!tk_strcmp(widget_get_type(bar), "scroll_bar_m")) {
            scroll_bar_scroll_to(bar, I(bar, BAR_VALUE), 500);
            return;
        }
    }
}

#define PULL_BOUND "_pull_bound"
#define PULL_SUPPRESS "_pull_suppress"
#define PULL_PROMPT "_pull_prompt"

static void pull_cancel(void) {
    void *page = st.pull_page;
    st.pull_page = st.pull_surface = (void *)0;
    st.pull_claimed = 0;
    /* Stock widget_set_visible rejects a NULL widget; an absent prompt needs no local guard. */
    if (page) widget_set_visible(widget_lookup(page, PULL_PROMPT, 1), 0, 0);
}

static int pull_live(void) {
    unsigned scope;
    if (!st.pull_page) return 0;
    void *wm = window_manager();
    if (!usable() || window_manager_is_animating(wm) ||
        window_manager_get_top_window(wm) != st.pull_page)
        return 0;
    context_now(&scope);
    return scope == st.pull_scope;
}

/* A white-on-black line across page, floating, that touches and keys pass through; 0 if it can't be
 * made. */
static void *band_create(void *page, const char *name, int y, int h) {
    void *band = label_create(page, 0, y, I(page, W_W), h);
    if (band) {
        widget_set_name(band, name);
        widget_use_style(band, "s_label_white18c");
        widget_set_enable(band, 0);
        widget_set_sensitive(band, 0);
        prop(band, "floating", 1);
        prop(band, "style:disable:bg_color", (int)0xff000000u);
        prop(band, "style:disable:text_color", (int)0xffffffffu);
    }
    return band;
}

static int pull_event(void *page, void *event) {
    if (page != st.pull_page) return 0;
    unsigned type = I(event, EVENT_TYPE);
    if (type == EVT_DESTROY || type == EVT_WINDOW_BACKGROUND || type == EVT_WINDOW_CLOSE ||
        type == EVT_POINTER_ABORT || !pull_live()) {
        pull_cancel();
        return 0;
    }
    int dx = I(event, EVENT_X) - st.pull_x;
    int dy = I(event, EVENT_Y) - st.pull_y;
    if (dx < 0) dx = -dx;
    int release = type == EVT_POINTER_UP_BEFORE;
    if (!st.pull_claimed) {
        if (release || (dx >= PULL_CLAIM_PX && dx >= dy) || dy <= -PULL_CLAIM_PX) {
            pull_cancel();
            return 0;
        }
        if (dy < PULL_CLAIM_PX) return 0;
        st.pull_claimed = 1;
        prop(st.pull_surface, PULL_SUPPRESS, 1);
        /* Abort the native pressed targets before swallowing move/up; native widgets clear
         * their pressed state and grabs. A canceled search must not turn into a row click. */
        unsigned abort_event[12];
        pointer_event_init(abort_event, EVT_POINTER_ABORT, page, st.pull_x, st.pull_y);
        widget_dispatch_event_to_target_recursive(page, abort_event);
        if (load_rows(&g_menu, st.pull_surface)) stop_scroll(&g_menu);
    }
    int ready = dy >= PULL_SEARCH_PX && dy > dx;
    if (release) {
        pull_cancel(); /* clear before stock search can navigate or destroy anything */
        if (ready) {
            if (!tk_strcmp(widget_get_prop_str(page, "name", ""), "radio_page"))
                radio_search();
            else
                stock_search(page, event);
        }
        return STOP;
    }
    void *prompt = widget_lookup(page, PULL_PROMPT, 1);
    if (!prompt) prompt = band_create(page, PULL_PROMPT, 0, 44);
    if (prompt) {
        widget_set_text_utf8(prompt, ready ? "Release to search" : "Pull to search");
        widget_set_visible(prompt, dy > 0, 0);
    }
    return STOP;
}

static void pull_begin(void *event) {
    pull_cancel();
    if (!event || I(event, EVENT_Y) < QUICK_EDGE_PX) return;
    void *page = window_manager_get_top_window(window_manager());
    if (!page) return;
    /* Local Songs' is stock's search; Internet Radio's is its own. A set with home_lib shows the
     * Library on Home's window, in Split, whose own lists take no pull. */
    const char *name = widget_get_prop_str(page, "name", "");
    int home = !tk_strcmp(name, "home_page");
    if (!home && tk_strcmp(name, "radio_page") && tk_strcmp(name, "localmusic_page")) return;
    void *w = surface((void *)0, (void *)0);
    if (!w) return;
    if (home && tk_strcmp(widget_get_prop_str(w, "name", ""), "scroll_view_localmusic")) return;
    prop(w, PULL_SUPPRESS, 0);
    int xy[2] = { I(event, EVENT_X), I(event, EVENT_Y) };
    widget_to_local(w, xy);
    if (xy[0] < 0 || xy[1] < 0 || xy[0] >= I(w, W_W) || xy[1] >= I(w, W_H) ||
        I(w, kind(w) == 2 ? TABLE_TOP : SCROLL_Y) > 0)
        return;
    if (!widget_get_prop_int(page, PULL_BOUND, 0)) {
        const unsigned types[] = {
            EVT_DESTROY,       EVT_WINDOW_BACKGROUND,   EVT_WINDOW_CLOSE,
            EVT_POINTER_ABORT, EVT_POINTER_MOVE_BEFORE, EVT_POINTER_UP_BEFORE
        };
        for (unsigned i = 0; i < sizeof types / sizeof *types; ++i)
            if (!widget_on(page, types[i], pull_event, page)) {
                /* Retry must not duplicate the callbacks already installed. */
                while (i) widget_off_by_func(page, types[--i], pull_event, page);
                return;
            }
        prop(page, PULL_BOUND, 1);
    }
    if (!widget_get_prop_int(w, PULL_BOUND, 0)) {
        if (!widget_on(w, EVT_DESTROY, pull_event, page)) return;
        prop(w, PULL_BOUND, 1);
    }
    st.pull_page = page;
    st.pull_surface = w;
    st.pull_x = I(event, EVENT_X);
    st.pull_y = I(event, EVENT_Y);
    context_now(&st.pull_scope);
}

/* A pill's end radius on a list row (h tall, its card inset px from the row's left): h / 2, less
 * where the round end would cut across a leading cover's square corner (search results' art, which
 * sits closer to the edge than the Library's), so the cover stays inside the card.
 * ponytail: only the row's direct, non-centred image children on the left; nested or right-hand
 * art is not checked. */
static int pill_radius(void *row, int h, int inset) {
    int r = h / 2;
    for (unsigned i = 0; row && i < widget_count_children(row); ++i) {
        void *c = widget_get_child(row, i);
        /* icons: discs; hidden: Folder's multi-select check, full height at its left */
        if (tk_strcmp(widget_get_type(c), "image") || !widget_get_visible(c) ||
            widget_get_prop_int(c, "draw_type", 0) == IMAGE_DRAW_CENTER)
            continue;
        int x = I(c, W_X) - inset, y = I(c, W_Y), ch = I(c, W_H);
        if (I(c, W_W) <= 0 || ch <= 0 || x < 0 || x >= r) continue;
        if (h - y - ch < y) y = h - y - ch; /* the nearer of its top and bottom corners */
        while (r > 2 && y >= 0 && y < r && (r - x) * (r - x) + (r - y) * (r - y) > r * r) --r;
    }
    return r;
}

/* A vertical gradient in one-pixel bands, then a one-pixel top highlight; equal ends give a solid
 * fill. r.h is at least 2. A pill (radius, at most r.h / 2) insets the bands at either end to that
 * radius's circle; 0 is square. Plain fills keep this off the stock gradient_t ABI, which is not
 * audited. */
static void gradient(void *canvas, rect_t r, unsigned top, unsigned bottom, unsigned hi,
                     int radius) {
    for (int j = 0; j < r.h; ++j) {
        int out = 0, D = 2 * radius, d = 2 * j + 1 - r.h;
        d = (d < 0 ? -d : d) - (r.h - D); /* half pixels past the end circle's centre */
        if (radius && d > 0) {            /* 1/16-pixel chord, sampled at the band's centre. */
            int v = (D * D - d * d) * 64, k = 0;
            int end = 8 * D;
            while (k < end) {
                int mid = (k + end + 1) / 2;
                if (mid * mid <= v)
                    k = mid;
                else
                    end = mid - 1;
            }
            out = 8 * D - k;
        }
        int in = (out + 15) / 16;
        unsigned color = RGBA(j ? mix(top, bottom, j, r.h - 1) : hi);
        canvas_set_fill_color(canvas, color);
        canvas_fill_rect(canvas, r.x + in, r.y + j, r.w - 2 * in, 1);
        if (out & 15) {
            canvas_set_fill_color(canvas,
                                  (color & 0xffffff) | (unsigned)(16 - (out & 15)) * 17 << 24);
            canvas_fill_rect(canvas, r.x + in - 1, r.y + j, 1, 1);
            canvas_fill_rect(canvas, r.x + r.w - in, r.y + j, 1, 1);
        }
    }
}

/* Row j of h of the stops s, 0xRRGGBB: one stop a row when there are h of them. */
unsigned stops_row(const stops_t *s, int j, int h) {
    if (s->n == h) return s->rgb[j];
    int p = h > 1 ? j * 25600 / (h - 1) : 0, k = 0; /* 1/256 % */
    while (k + 2 < s->n && p > s->pos[k + 1] * 256) ++k;
    int lo = s->pos[k] * 256;
    return mix(s->rgb[k], s->rgb[k + 1], p - lo, s->pos[k + 1] * 256 - lo);
}
/* An N-stop gradient s over x, y, w, h in one-pixel bands; pixel row j sits j / (h - 1) of the way
 * down, mixed between the stops around it. A screen-colour painter's: its colours are final, never
 * flipped. As many stops as rows: each row its own. Fewer than two stops draw nothing. */
void paint_stops(void *canvas, int x, int y, int w, int h, const stops_t *s) {
    if (s->n < 2) return;
    for (int j = 0; j < h; ++j) {
        stock_canvas_set_fill_color(canvas, RGBA(stops_row(s, j, h)));
        canvas_fill_rect(canvas, x, y + j, w, 1);
    }
}
/* How far a rounded box's row i starts in from its side: a hard corner. */
int round_inset(int R, int i, int h) {
    return i < R ? arc_out(R, i) >> 4 : i >= h - R ? arc_out(R, h - 1 - i) >> 4 : 0;
}
/* R's corner pixels' coverage out of 255, by radius (tools/art.py CORNERS); a radius outside the
 * table takes a hard corner. */
static const unsigned char *const round_cover[] = ROUND_COVER;
static const unsigned char *cover_of(int R) {
    return (unsigned)R < sizeof round_cover / sizeof *round_cover ? round_cover[R]
                                                                  : (const unsigned char *)0;
}
/* The corner's row r's row i falls in, -1 between the corners. */
static int corner_row(const rect_t *r, int R, int i) {
    return i < R ? i : i >= r->h - R ? r->h - 1 - i : -1;
}
/* Row i of r, its corners rounded by R and antialiased, in a final 0xAARRGGBB: each corner pixel
 * the colour at its coverage of the colour's alpha. Only the ends named are rounded (ROUND_LEFT,
 * ROUND_RIGHT); the other side is square. */
static void round_row_ends(void *canvas, const rect_t *r, int R, int i, unsigned c, int ends) {
    const unsigned char *cover = cover_of(R);
    int row = corner_row(r, R, i), n = cover ? 0 : round_inset(R, i, r->h);
    for (; cover && row >= 0 && n < R && cover[row * R + n] < 255; ++n) {
        unsigned a = cover[row * R + n] * (c >> 24) / 255;
        if (!a) continue;
        if (ends & ROUND_LEFT) scrub_row(canvas, a << 24 | (c & 0xffffff), r->x + n, r->y + i, 1);
        if (ends & ROUND_RIGHT)
            scrub_row(canvas, a << 24 | (c & 0xffffff), r->x + r->w - 1 - n, r->y + i, 1);
    }
    int l = ends & ROUND_LEFT ? n : 0, rr = ends & ROUND_RIGHT ? n : 0;
    if (r->w > l + rr) scrub_row(canvas, c, r->x + l, r->y + i, r->w - l - rr);
}
void round_row(void *canvas, const rect_t *r, int R, int i, unsigned c) {
    round_row_ends(canvas, r, R, i, c, ROUND_LEFT | ROUND_RIGHT);
}
/* The gradient s over r, one row at a time, its corners rounded by R and antialiased. */
void paint_round_rows(void *canvas, const rect_t *r, const stops_t *s, int R) {
    for (int i = 0; s->n >= 2 && i < r->h; ++i)
        round_row(canvas, r, R, i, 0xff000000u | stops_row(s, i, r->h));
}
/* A ring a pixel wide just inside r: what R's corners cover and R - 1's, a pixel in, don't. With
 * either out of the table, hard corners. */
void round_ring(void *canvas, const rect_t *r, int R, unsigned c) {
    const unsigned char *out = cover_of(R), *in = cover_of(R - 1);
    for (int i = 0; i < r->h; ++i) {
        int row = corner_row(r, R, i);
        if (!i || i == r->h - 1) {
            round_row(canvas, r, R, i, c);
        } else if (row < 0 || !out || !in) {
            int n = round_inset(R, i, r->h);
            scrub_row(canvas, c, r->x + n, r->y + i, 1);
            scrub_row(canvas, c, r->x + r->w - 1 - n, r->y + i, 1);
        } else {
            for (int n = 0; n < R; ++n) {
                int a = out[row * R + n] - (n ? in[(row - 1) * (R - 1) + n - 1] : 0);
                if (a <= 0) continue;
                unsigned ink = (unsigned)a * (c >> 24) / 255 << 24 | (c & 0xffffff);
                scrub_row(canvas, ink, r->x + n, r->y + i, 1);
                scrub_row(canvas, ink, r->x + r->w - 1 - n, r->y + i, 1);
            }
        }
    }
}
void paint_art(void *w, void *canvas, const char *name, int x, int y);
/* Classic's panel (volume, rating) over r: the scheme's bar gloss, or (flat) its top colour alone,
 * LETTER_RADIUS corners, in a 1 px ring of its line, on its soft shadow, an image reaching
 * PANEL_PAD past the shadow's box, which sits PANEL_DROP below the panel (tools/art.py SHADOWS).
 * Restores the fill. */
void paint_panel(void *w, void *canvas, const rect_t *r, const char *shadow, int flat) {
    const scheme_t *S = scheme();
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    paint_art(w, canvas, shadow, r->x - PANEL_PAD, r->y - PANEL_PAD + PANEL_DROP);
    stops_t ring = { 2, { 0, 100 }, { S->bar_line, S->bar_line } };
    rect_t out = { r->x - 1, r->y - 1, r->w + 2, r->h + 2 };
    paint_round_rows(canvas, &out, &ring, LETTER_RADIUS + 1);
    stops_t even = { 2, { 0, 100 }, { S->bar.rgb[0], S->bar.rgb[0] } };
    paint_round_rows(canvas, r, flat ? &even : &S->bar, LETTER_RADIUS);
    stock_canvas_set_fill_color(canvas, fill);
}
/* A theme image at x, y at its size. */
void paint_art(void *w, void *canvas, const char *name, int x, int y) {
    unsigned bitmap[64]; /* bitmap_t: width @0, height @4 */
    if (widget_load_image(w, name, bitmap)) return;
    rect_t src = { 0, 0, (int)bitmap[0], (int)bitmap[1] }, dst = { x, y, src.w, src.h };
    canvas_draw_image(canvas, bitmap, &src, &dst);
}

/* Narrow the canvas clip to the surface's viewport, keeping the old clip; false when none shows. */
static int clip_surface(void *canvas, menu_t *m, rect_t *old) {
    int clip[4];
    if (!clip_within(canvas, &old->x, clip, I(canvas, CANVAS_X), I(canvas, CANVAS_Y), I(m->w, W_W),
                     m->height))
        return 0;
    canvas_set_clip_rect(canvas, clip);
    return 1;
}

/* Library sorting ignores a leading "The ", "A " or "An " (Apple's rule) when a name follows it:
 * the length to skip, else 0. */
static unsigned article(const char *s) {
    for (const char *a = "the a an "; *a;) {
        unsigned n = 0;
        while (a[n] != ' ' && (s[n] | 32) == a[n]) ++n;
        if (a[n] == ' ' && s[n] == ' ' && (unsigned char)s[n + 1] > ' ') return n + 1;
        while (*a++ != ' ') {}
    }
    return 0;
}

/* Text centred in r in the default font at px, in color; text color and alignment are restored,
 * the font is not (stock sets it before its own text). Shared with peq_ui.c and visualizer.c. */
static void draw_in_face(void *canvas, const char *face, const unsigned *s, unsigned n,
                         const void *r, unsigned px, unsigned color) {
    unsigned text = (unsigned)I(P(canvas, CANVAS_LCD), LCD_TEXT_COLOR);
    int align_v = I(canvas, CANVAS_ALIGN_V), align_h = I(canvas, CANVAS_ALIGN_H);
    canvas_set_font(canvas, face, px);
    canvas_set_text_color(canvas, color);
    I(canvas, CANVAS_ALIGN_V) = I(canvas, CANVAS_ALIGN_H) = 1;
    canvas_draw_text_in_rect(canvas, s, n, r);
    I(canvas, CANVAS_ALIGN_V) = align_v;
    I(canvas, CANVAS_ALIGN_H) = align_h;
    stock_canvas_set_text_color(canvas, text);
}
void draw_centred(void *canvas, const unsigned *s, unsigned n, const void *r, unsigned px,
                  unsigned color) {
    int fpx = (int)px;
    /* the theme's face, or the system default font */
    const char *face = theme_face(s, (int)n, &fpx);
    draw_in_face(canvas, face, s, n, r, (unsigned)fpx, color);
}
/* A label's shadow, which AWTK hasn't: its text, from under it in a paint hook, in black at alpha,
 * in r, its box moved. Black in either half (Light Mode's text setter flips this back). */
void text_shadow(void *canvas, const char *face, const unsigned *s, const void *r, unsigned px,
                 unsigned alpha) {
    unsigned n = wide_len(s);
    if (n)
        draw_in_face(canvas, face, s, n, r, px,
                     alpha << 24 | (RGBA(flip_if(ipod_light(), 0)) & 0xffffff));
}

/* A rounded box in color, or a square one when the canvas declines to round it (no vgcanvas, or a
 * radius under 3, which stock zeroes and then fails). */
void fill_box(void *canvas, rect_t *r, unsigned color, unsigned radius) {
    if (canvas_fill_rounded_rect(canvas, r, (void *)0, &color, radius)) {
        canvas_set_fill_color(canvas, color);
        canvas_fill_rect(canvas, r->x, r->y, r->w, r->h);
    }
}

/* A two-pixel frame in color, two concentric one-pixel rounded strokes, or a square one when the
 * box is too small to round or the canvas declines to (no vgcanvas). Leaves the stroke color set.
 */
static void frame_box(void *canvas, const rect_t *r, unsigned color, unsigned radius) {
    rect_t in = { r->x + 1, r->y + 1, r->w - 2, r->h - 2 };
    if (r->w > 2 * (int)radius && r->h > 2 * (int)radius &&
        !canvas_stroke_rounded_rect(canvas, r, (void *)0, &color, radius, 1) &&
        !canvas_stroke_rounded_rect(canvas, &in, (void *)0, &color, radius - 1, 1))
        return;
    canvas_set_stroke_color(canvas, color);
    canvas_stroke_rect(canvas, r->x, r->y, r->w, r->h);
    canvas_stroke_rect(canvas, in.x, in.y, in.w, in.h);
}

/* Stock names a playlist row's trailing More dots "img_more%d". */
static void *more_image(void *w) {
    for (unsigned i = 0, n = w ? widget_count_children(w) : 0; i < n; ++i) {
        void *c = widget_get_child(w, i);
        if (widget_get_visible(c) &&
            tk_str_start_with(widget_get_prop_str(c, "name", ""), "img_more"))
            return c;
    }
    return (void *)0;
}

/* A widget in a DRILL window (contexts.inc). Its own window decides, not the top one, so a window
 * painted during a transition keeps its own rows. */
static int drill(void *w) {
    /* stock's More hit test owns the trailing area, but for a selected-only chevron, left of it */
    if (!theme()->chevron_sel && more_image(w)) return 0;
    if (!tk_strcmp(widget_get_prop_str(w, "name", ""), PL_ACTION)) return 0;
    w = window_of(w);
    int ctx = w ? context_id(widget_get_prop_str(w, "name", (void *)0)) : -1;
    return ctx >= 0 && (contexts[ctx].flags & DRILL);
}

/* row_into's image, hidden, w's last child (geometry only: no string on the paint path). */
static int into_mark(void *w) {
    unsigned n = widget_count_children(w);
    void *c = n ? widget_get_child(w, n - 1) : (void *)0;
    return c && !widget_get_visible(c) && I(c, W_X) == 302 && I(c, W_W) == 50;
}
/* A drill row under a selected-only chevron: a page_list row's own mark, or on the list item of a
 * stock button (stock_more_into), whose own children are set_row's. */
static int page_into(void *row) { return into_mark(row) || into_mark(P(row, W_PARENT)); }
/* Folder's row i under a selected-only chevron, drawn with the list: by its record, as stock's
 * row does (all but a song's), since its EVT_WINDOW_FOREGROUND rebuilds the table's rows. */
static int folder_into(const menu_t *m, int i) {
    void *list = P(p_deque_showlist, 0);
    return !tk_strcmp(widget_get_prop_str(window_of(m->w), "name", ""), "folder_page") && list &&
           (unsigned)m->id[i] < deque_size(list) &&
           I(deque_at(list, (unsigned)m->id[i]), REC_TYPE) != 8;
}

/* The iPod `>` of each drill row, where stock rows place img_into, as stock hides its own
 * list_into: not in multi-select, and not on grid tiles. Home has none but a chevron_home theme's:
 * Modern's rides its selection bar alone, nudge included, Classic's sits at home_chevron_x. A
 * chevron_sel theme (Classic) draws only the selected row's, white. The image manager caches the
 * bitmap. */
static void paint_chevrons(void *w, void *canvas) {
    unsigned bitmap[64], white[64]; /* bitmap_t */
    rect_t old;
    void *win = window_of(w);
    const theme_t *T = theme();
    int home = home_menu(win), sel_only = T->chevron_sel,
        box = home && ipod_home() != 1 ? T->home_chevron_x : T->chevron_x;
    /* a picker's tap adds and closes: no `>` */
    if (g_navbar_status || !kind(w) || !P(canvas, CANVAS_LCD) || !(drill(w) || T->chevron_sel) ||
        (sel_only && widget_get_prop_int(win, PL_MODE, 0)) ||
        (home && (!T->chevron_home || st.sel_w != w)) ||
        (T->chevron_sel && st.sel_w != w && !drill(w)) || !load_rows(&g_menu, w) ||
        (T->chevron_sel && !drill(w) &&
         (st.sel_row >= g_menu.n ||
          (!page_into(g_menu.at[st.sel_row]) && !folder_into(&g_menu, st.sel_row)))) ||
        widget_load_image(w, "list_into", bitmap) || !clip_surface(canvas, &g_menu, &old))
        return;
    /* Light Mode: the row on a coloured bar or card keeps stock's white `>`, as an iPod's blue bar
     * does; by path, the image hook leaves it unflipped. */
    int lit = (sel_only || (ipod_light() && st.sel_accent)) && st.sel_w == w &&
              !widget_load_image(w, box ? "classic_light_chevron" : LIST_INTO_WHITE, white);
    for (int i = 0; i < g_menu.n; ++i) {
        rect_t r = bounds(&g_menu, i);
        /* the row's own tests last: an unmarked row under a selected-only chevron pays nothing */
        if (2 * r.w >= I(w, W_W) && (!home || i == st.sel_row) &&
            (!sel_only || (lit && i == st.sel_row)) &&
            (drill(g_menu.at[i]) ||
             (T->chevron_sel && (page_into(g_menu.at[i]) || folder_into(&g_menu, i)))))
            canvas_draw_icon(
                canvas, lit && i == st.sel_row ? white : bitmap,
                box ? r.x + box + (int)(lit && i == st.sel_row ? white : bitmap)[0] / 2
                    : r.x + r.w - CHEVRON_W + (int)bitmap[0] / 2 +
                          (home ? (ipod_home() == 1 ? HOME_FULL_CHEVRON_SHIFT : HOME_CHEVRON_SHIFT)
                                : 0),
                home ? st.sel_y : r.y + r.h / 2); /* bitmap_t width @0 */
    }
    canvas_set_clip_rect(canvas, &old);
}

/* Writes v (under 1000) in decimal to s; returns the digits written. */
unsigned put_num(unsigned *s, unsigned v) {
    unsigned n = 0;
    if (v >= 100) s[n++] = '0' + v / 100 % 10;
    if (v >= 10) s[n++] = '0' + v / 10 % 10;
    s[n++] = '0' + v % 10;
    return n;
}

static int letter_expire(const void *info) {
    st.letter_timer = 0;
    void *w = surface((void *)0, (void *)0);
    if (w && w == st.wheel_surface) widget_invalidate_force(w, (void *)0);
    return 0;
}

/* The fast-scroll letter's box and its n characters, on the theme's own scale; a letter_raw
 * theme's colours are final in both halves. */
static void draw_letter(void *canvas, const unsigned *c, unsigned n, rect_t *box) {
    const theme_t *T = theme();
    unsigned raw = st.raw_ink;
    st.raw_ink = T->letter_raw;
    /* unsigned: 0xd9 << 24 is past INT_MAX, undefined for an int; the Q2's clang wrapped it */
    fill_box(canvas, box, ((unsigned)LETTER_ALPHA << 24) | FILL_RGB, LETTER_RADIUS);
    draw_centred(canvas, c, n, box, T->letter_px ? T->letter_px : LETTER_PX, 0xffffffff);
    st.raw_ink = raw;
}

/* A page whose list is in an order of its own, where a row's first letter says nothing of how far
 * a spin has got: an album's songs (disc and track, comparator 0x5063f0), Up Next (play order),
 * Chapters (the file's), Edit Playlist's songs as added (ple_listing; its Add Songs picker is A to
 * Z). In Library's song list the class decides (internals.md, fast-scroll letter): Recent, Frequent
 * and Recently Added (0xf007-0xf009), Songs under Time added (getAllMusic 3 and 4) and a playlist's
 * songs (0xff13) under its Sort's Time added (g_playlistsort_flag not 0, getMusicByPlayList
 * 0x50b6c8) are in their own order. */
static int own_order(const char *page) {
    if (!tk_strcmp(page, "allmusic_page")) {
        int cls = I(g_class_type, 0), sort = I(g_sort_flag, 0);
        return (cls >= 0xf007 && cls <= 0xf009) || (cls == 0xff13 && I(g_playlistsort_flag, 0)) ||
               (cls == 0xf001 && (sort == 3 || sort == 4));
    }
    return !tk_strcmp(page, "albuminfo_page") || !tk_strcmp(page, "playerjumpinfo_page") ||
           !tk_strcmp(page, "upnext_page") || !tk_strcmp(page, "chapters_page") ||
           (!tk_strcmp(page, "playlistedit_page") && ple_listing());
}

/* iPod fast scroll: while the wheel ramp moves more than one row per step, the selected row's
 * first character sits in a dark translucent square over the list until LETTER_MS after the last
 * such step. A virtual table resolves the logical row in its recycled pool; an offscreen or
 * textless row shows nothing. The fill color and clip are restored. */
static void paint_letter(void *w, void *canvas) {
    rect_t old;
    if (!st.letter_timer || w != st.wheel_surface || st.wheel_run <= LIST_FIRST_MS ||
        st.touch_mode || !P(canvas, CANVAS_LCD) || !load_rows(&g_menu, w) ||
        (g_menu.ctx >= 0 && contexts[g_menu.ctx].kind == CTX_FIXED) || /* menus: not alphabetical */
        /* nor Library's categories beside Home's pane */
        !tk_strcmp(widget_get_prop_str(w, "name", ""), "scroll_view_localmusic") ||
        /* nor an album's tracks in Cover Flow, which are in album order (disc and track).
         * A page whose list is in an order of its own never arms it (the wheel handler, own_order).
         * ponytail: the lists own_order doesn't know keep it (internals.md) */
        coverflow_tracks(window_of(w)))
        return;
    int i = index_of(&g_menu, widget_get_prop_int(w, SEL, -1));
    const unsigned *s = i < 0 ? (void *)0 : row_id(g_menu.at[i]).title;
    while (s && *s == ' ') ++s;
    if (!s || !*s || !clip_surface(canvas, &g_menu, &old)) return;
    if (g_menu.ctx >= 0 && contexts[g_menu.ctx].kind == CTX_LOCAL) { /* the letter it sorts under */
        char head[8];
        unsigned k = 0;
        for (; k < 7 && s[k]; ++k) head[k] = (char)(s[k] < 128 ? s[k] : 127);
        head[k] = 0;
        s += article(head);
    }
    unsigned c = *s >= 'a' && *s <= 'z' ? *s - 32 : *s;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    rect_t box = { (I(w, W_W) - LETTER_BOX) / 2, (g_menu.height - LETTER_BOX) / 2, LETTER_BOX,
                   LETTER_BOX };
    draw_letter(canvas, &c, 1, &box);
    stock_canvas_set_fill_color(canvas, fill);
    canvas_set_clip_rect(canvas, &old);
}

/* Cover Flow's big letter centred over w, or the Year sort's year (CF_YEAR): its four digits in a
 * box as tall as the letter's and as wide as they need with the letter's padding, an en dash in
 * the letter's square when undated. */
static void cf_label(void *w, void *canvas) {
    unsigned c = st.cf_letter, y = c & ~CF_YEAR, s[4] = { c & CF_YEAR ? 0x2013 : c }, n = 1;
    int bw = LETTER_BOX;
    if (c & CF_YEAR && y) {
        for (int k = 3; k >= 0; --k, y /= 10) s[k] = '0' + y % 10;
        n = 4;
        const theme_t *T = theme();
        int px = T->letter_px ? T->letter_px : LETTER_PX;
        canvas_set_font(canvas, theme_face(s, (int)n, &px), (unsigned)px);
        int need = (int)canvas_measure_text(canvas, s, n) + LETTER_BOX - px;
        bw = need > bw ? need : bw;
    }
    rect_t box = { (I(w, W_W) - bw) / 2, (I(w, W_H) - LETTER_BOX) / 2, bw, LETTER_BOX };
    draw_letter(canvas, s, n, &box);
}

/* An art_square theme (Classic, which has no Corners; Modern under Square): an iPod's square art on
 * Now Playing, the full-screen art, Cover Flow (coverflow_render) and list thumbnails, their disc
 * placeholders squared too (square_disc); Home's art and Home: Carousel's cards keep their own
 * corners. */
int ipod_square_art(void) { return theme()->art_square && !setting(SETTING_ROUNDED); }

/* Playing art is rounded after scaling, so opposite corners share the same pixel grid.
 * NP_ART_RADIUS at 166px, painted over it in the page's
 * black: each corner row outside the arc, then its edge pixel at the alpha of its uncovered part
 * (1/16 px). */
static void paint_cover(void *w, void *canvas) {
    if (!w || !P(canvas, CANVAS_LCD)) return;
    if ((w == st.np_cover || w == st.stream_art) &&
        I(w, W_H) > I(w, W_W)) { /* Classic's room for a reflection */
        coverflow_slant(w, canvas, scheme()->paper & 255);
        return;
    }
    if (ipod_square_art()) return;
    int x = 0, y0 = 0;
    if (w == st.bc[2]) {
        w = widget_lookup(w, "img_bigcover", 1);
        if (!w) return;
        x = I(w, W_X), y0 = I(w, W_Y); /* after the title's shade, in the page's canvas */
    } else if (w != st.np_cover && w != spot_art())
        return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    int ww = I(w, W_W), h = I(w, W_H);
    int r = (ww < h ? ww : h) * NP_ART_RADIUS / 166;
    for (int i = 0; i < r; ++i) {
        int out = arc_out(r, i), n = out >> 4;
        for (int c = 0; c < 4; ++c) {
            int y = c & 1 ? h - 1 - i : i, left = !(c & 2);
            canvas_set_fill_color(canvas, RGBA(0));
            canvas_fill_rect(canvas, x + (left ? 0 : ww - n), y0 + y, n, 1);
            canvas_set_fill_color(canvas, (unsigned)(out & 15) * 17 << 24);
            canvas_fill_rect(canvas, x + (left ? n : ww - n - 1), y0 + y, 1, 1);
        }
    }
    stock_canvas_set_fill_color(canvas, fill);
}

/* Minimal's small capitals (Sudo's): s from x, y (the text's top) in the default font at px,
 * upper-cased (ASCII and Latin-1), SUDO_TRACK px apart, cut off at x + max; returns the whole
 * text's width, a trailing SUDO_TRACK included. The text color is restored, the font is not. */
static int draw_spaced(void *canvas, const unsigned *s, int x, int y, unsigned px, unsigned color,
                       int max) {
    unsigned text = (unsigned)I(P(canvas, CANVAS_LCD), LCD_TEXT_COLOR);
    canvas_set_font(canvas, (void *)0, px);
    canvas_set_text_color(canvas, color);
    int at = 0;
    for (; s && *s; ++s) {
        unsigned c = *s;
        if ((c >= 'a' && c <= 'z') || (c >= 0xe0 && c <= 0xfe && c != 0xf7)) c -= 32;
        int cw = (int)(canvas_measure_text(canvas, &c, 1) + 0.5f);
        if (at + cw > max)
            max = -1; /* cut off: the rest is only measured */
        else
            canvas_draw_text(canvas, &c, 1, x + at, y);
        at += cw + SUDO_TRACK;
    }
    stock_canvas_set_text_color(canvas, text);
    return at;
}

/* Minimal's album marquee: MARQUEE_HOLD_MS at the start, MARQUEE_PX every MARQUEE_MS until the
 * end shows, MARQUEE_HOLD_MS there, then back to the start. paint_caps measures the overflow and
 * starts it; it stops once the album fits or the page goes. */
#define MARQUEE_MS 50
#define MARQUEE_PX 2
static int np_marquee(const void *info) {
    st.np_marquee = 0;
    /* Covered or dark, paint_caps restarts it on Now Playing's next paint. */
    if (!st.np_album || st.np_over <= 0 || !g_backlight_status ||
        window_manager_get_top_window(window_manager()) != st.np_win)
        return 0;
    int end = st.np_off >= st.np_over;
    st.np_off = end ? 0 : st.np_off + MARQUEE_PX > st.np_over ? st.np_over : st.np_off + MARQUEE_PX;
    widget_invalidate_force(st.np_album, (void *)0);
    st.np_marquee = timer_add(np_marquee, (void *)0,
                              end || st.np_off >= st.np_over ? MARQUEE_HOLD_MS : MARQUEE_MS);
    return 0;
}

/* Minimal's Home labels are HOME_CAPS_PX small capitals over their transparent stock ink
 * (ringnav_style_color), white on the selected row, else SUDO_MUTED; the status bar's idle "Q2" is
 * the 1.0.1 wordmark's MARK_PX grey capitals; Now Playing's album (docs/ipod.md#now-playing) is
 * NP_CAPS_PX, painted on its page (view_album) over its transparent label, scrolled by np_marquee
 * when wider than it. ponytail: other long labels are cut off, not scrolled. */
static void paint_caps(void *w, void *canvas) {
    if (!w || !P(canvas, CANVAS_LCD)) return;
    const theme_t *T = theme();
    if (w == st.bar_idle) {
        if (T->caps_mark)
            draw_spaced(canvas, widget_get_text(w), 0, (I(w, W_H) - MARK_PX) / 2, MARK_PX,
                        RGBA(0xaaaaaa), 375);
        return;
    }
    if (st.np_album && w == P(st.np_album, W_PARENT)) {
        if (!T->caps_np) return;
        void *a = st.np_album;
        int old[4], clip[4];
        if (!clip_within(canvas, old, clip, I(canvas, CANVAS_X) + I(a, W_X),
                         I(canvas, CANVAS_Y) + I(a, W_Y), I(a, W_W), I(a, W_H)))
            return;
        canvas_set_clip_rect(canvas, clip);
        int room = I(a, W_W) - NP_CAPS_INSET;
        st.np_over = draw_spaced(canvas, widget_get_text(a), I(a, W_X) + NP_CAPS_INSET - st.np_off,
                                 I(a, W_Y) + (I(a, W_H) - NP_CAPS_PX) / 2, NP_CAPS_PX,
                                 RGBA(NP_ALBUM_RGB), room + st.np_off) -
                     SUDO_TRACK - room;
        canvas_set_clip_rect(canvas, old);
        if (st.np_over > 0 && !st.np_marquee)
            st.np_marquee = timer_add(np_marquee, (void *)0, MARQUEE_HOLD_MS);
        return;
    }
    if (!T->caps_home || tk_strcmp(widget_get_type(w), "hscroll_label") || !coverflow_home_owns(w))
        return;
    unsigned color = RGBA(SUDO_MUTED);
    for (void *p = w; p; p = P(p, W_PARENT))
        if (p == st.sel_widget) color = 0xffffffff;
    draw_spaced(canvas, widget_get_text(w), 0, (I(w, W_H) - HOME_CAPS_PX) / 2, HOME_CAPS_PX, color,
                I(w, W_W));
}

/* The selected row's long title scrolls, as an iPod's does, MARQUEE_PASSES times as soon as the
 * selection settles, through stock's scroller with every label's holds and pace
 * (ringnav_hscroll_start); every other row keeps its ellipsis. Only a title stock would scroll on
 * focus alone (only_focus; no row is focused): stock's timer stops such a label on its first tick
 * unless it is focused (0x5fb7ac), so only_focus is off while this one runs. It loops, so each pass
 * ends on its lull (ringnav_hscroll_lull), which counts it and stops it after the last, or once the
 * row isn't the selected one on a lit, usable screen: a dark screen or a covered page costs at most
 * the rest of one pass. A row is its widget and its id, as a table's pool can hold the selection in
 * one widget (paint_selection; a paint would drop hscroll_label_stop's invalidate, so marq_moved
 * stops it from a timer). */
static int marq_gone(void *ctx, void *event) {
    (void)ctx, (void)event;
    st.marq_label = (void *)0;
    return 0;
}
static void marq_stop(void) {
    void *label = st.marq_label;
    if (!label) return;
    st.marq_label = (void *)0;
    widget_off_by_func(label, EVT_DESTROY, marq_gone, (void *)0);
    hscroll_label_stop(label);
    hscroll_label_set_only_focus(label, 1);
}
static void *row_scroller(void *w, int depth) {
    unsigned n = widget_count_children(w);
    for (unsigned i = 0; i < n; ++i) {
        void *c = widget_get_child(w, i);
        if (!widget_get_visible(c)) continue;
        if (!tk_strcmp(widget_get_type(c), "hscroll_label")) return c;
        void *in = depth ? row_scroller(c, depth - 1) : (void *)0;
        if (in) return in;
    }
    return (void *)0;
}
/* The marquee's row while it is the selected one on a usable screen; else 0. */
static void *marq_row(void) {
    void *w = usable() ? surface((void *)0, (void *)0) : (void *)0;
    int i = st.sel_row;
    void *row = w && w == st.sel_w && load_rows(&g_menu, w) && i >= 0 && i < g_menu.n &&
                        g_menu.id[i] == st.marq_id
                    ? g_menu.at[i]
                    : (void *)0;
    return row == st.marq_row ? row : (void *)0;
}
/* The row's title was rewritten in place (addon.c relabel): if it is the marquee's, the next paint
 * re-arms it for the new text's width. */
void marq_restart(void *row) {
    if (row == st.marq_row) st.marq_row = (void *)0;
}
static int marq_moved(const void *info) {
    (void)info;
    st.marq_timer = 0;
    marq_stop();
    st.marq_passes = 0;
    void *row = marq_row();
    if (!row) {
        st.marq_row = (void *)0; /* the next paint with a selection starts over */
        return 0;
    }
    void *label = row_scroller(row, 2);
    /* stock's two margins (2 px each by default, 0x5fb23c) count towards its ellipsis */
    int over = label ? I(label, HSCROLL_TEXT_W) + 4 - I(label, W_W) : 0;
    if (over <= 0 || !B(label, HSCROLL_ONLY_FOCUS)) return 0; /* until it moves */
    B(label, HSCROLL_LOOP) = 1;
    st.marq_label = label;
    widget_on(label, EVT_DESTROY, marq_gone, (void *)0);
    hscroll_label_set_only_focus(label, 0);
    hscroll_label_start(label);
    return 0;
}

/* Every hscroll_label's holds and pace. Stock's start (hscroll_label_start, which its focus, its
 * paint on a new text, its lull and the payload all call) waits the label's delay, 0 unless set,
 * before the first step, and a looping label ends a pass on its lull (2000 or 3000 ms) with its end
 * in view, then starts again: most labels raced off their start at once and parked on their end.
 * Every start now waits MARQUEE_HOLD_MS, showing where the text is (its start, or a yoyo label's
 * end), and runs at HSCROLL_PACE at most (or at it, for a label with no speed, which would cover
 * any length in its duration). Stock's start does nothing for a running label. */
int ringnav_hscroll_start(void *w) {
    unsigned speed = (unsigned)I(w, HSCROLL_SPEED);
    I(w, HSCROLL_DELAY) = MARQUEE_HOLD_MS;
    if (!speed || speed > HSCROLL_PACE) I(w, HSCROLL_SPEED) = HSCROLL_PACE;
    return stock_hscroll_start_trampoline(w);
}
/* Stock's Albums rows since V1.36 (ALBUM_LABELS in tools/build.py): the list row's artist line and
 * a grid cell's title take stock's attributes here, then the focus flag V1.34 gave them, which
 * stock now sets in Menu Mode alone. No row's parent has the focus under ringnav, so they stay
 * still. */
void ringnav_album_label(void *label) {
    set_hscroll_label_attribute(label);
    hscroll_label_set_only_parent_focus(label, 1);
}
/* The end-of-pass widget_add_timer comes here (HSCROLL_LULL in tools/build.py): the lull holds the
 * end MARQUEE_HOLD_MS, then puts the start back before stock's callback restarts the label, so its
 * delay holds there. A looping yoyo label scrolls back from its end, which that delay holds: its
 * lull is 0. A label that doesn't loop comes here too (HSCROLL_ONCE): its lull's end stops it, back
 * on its start, as stock's lull callback ends (the timer id cleared, so hscroll_label_stop leaves
 * this firing timer to RET_REMOVE). An only_focus label stock won't restart (unfocused) rests on
 * its start, not its end. */
static int (*hscroll_lulled)(const void *);
static int hscroll_lull_end(const void *info) {
    void *w = P(info, TIMER_CTX);
    if (!B(w, HSCROLL_LOOP)) {
        I(w, HSCROLL_TIMER) = 0;
        hscroll_label_stop(w);
        return 7; /* RET_REMOVE */
    }
    if (!B(w, HSCROLL_YOYO)) hscroll_label_set_xoffset(w, 0);
    int ret = hscroll_lulled(info);
    if (w == st.marq_label && !marq_row())
        st.marq_row = (void *)0, marq_stop(); /* the next paint with a selection starts over */
    else if (w == st.marq_label && ++st.marq_passes >= MARQUEE_PASSES)
        marq_stop(); /* passes spent: nothing until the selection moves */
    return ret;
}
unsigned ringnav_hscroll_lull(void *w, int (*lulled)(const void *), unsigned ms) {
    (void)ms;
    hscroll_lulled = lulled;
    return widget_add_timer(w, hscroll_lull_end,
                            B(w, HSCROLL_YOYO) && B(w, HSCROLL_LOOP) ? 0 : MARQUEE_HOLD_MS);
}

/* Classic's dialogs (CONFIRM_CLASSIC_LIGHT), in screen pixels and final colours. A dialog
 * is full-screen (0, 0, 375, 320) and its background clear, so the bar and the page show through.
 */
#define BAR_ROWS geom_val(V_BAR)
enum { FORM_NONE, FORM_PAGE, FORM_TRAY };
static int dialogs_painted(void) { return theme()->painter[PAINT_CONFIRM] != CONFIRM_STOCK; }
/* The confirm's form: the tray for the prompt confirm_over asked for, the page for any other. The
 * first confirm painted after the call takes the flag, so a prompt stock opens over it (a held
 * power key) stays a question. */
static int confirm_form(void *win) {
    if (!dialogs_painted() || P(win, W_PARENT) != window_manager() ||
        tk_strcmp(widget_get_prop_str(win, "name", ""), "confirminfo_dialog"))
        return FORM_NONE;
    if (st.confirm_verb && !st.confirm_win) st.confirm_win = win;
    return st.confirm_verb && win == st.confirm_win ? FORM_TRAY : FORM_PAGE;
}
/* An inset shadow from above (0, 2, blur 2): of its alpha a, what a gaussian leaves on each row. */
void inset_top(void *canvas, const rect_t *r, int R, unsigned a) {
    static const unsigned char part[] = { 239, 177, 79, 17 };
    for (int i = 0; i < 4; ++i) round_row(canvas, r, R, i, (a * part[i] >> 8) << 24);
}
/* A box's one-pixel light below it. */
void row_below(void *canvas, const rect_t *r, int R, unsigned c) {
    rect_t down = { r->x, r->y + 1, r->w, r->h };
    round_row(canvas, &down, R, r->h - 1, c);
}
/* Form A's track, its 30 rows on the light half */
static const unsigned confirm_light[30] = { 0x7389a5, 0x7b90a9, 0x8094ac, 0x8598af, 0x8a9cb2,
                                            0x8fa0b5, 0x94a4b9, 0x98a8bc, 0x9dacbf, 0xa2b0c1,
                                            0xa5b2c3, 0xa8b5c5, 0xaab7c7, 0xadb9c9, 0xb0bccb,
                                            0xb3becd, 0xb5c0ce, 0xb7c1cf, 0xb8c2d0, 0xb9c3d1,
                                            0xbbc4d2, 0xbcc5d3, 0xbec7d4, 0xbfc8d4, 0xbfc8d4,
                                            0xc0c8d5, 0xc0c8d5, 0xbfc8d5, 0xbfc7d4, 0xbec7d4 };
const stops_t confirm_dark = { 4, { 0, 30, 85, 100 }, { 0x14171c, 0x1d2127, 0x2a2f36, 0x191c21 } },
              confirm_tray = { 8,
                               { 0, 8, 20, 34, 44, 49, 92, 100 },
                               { 0x6a707a, 0x565c66, 0x464b54, 0x343840, 0x22252c, 0x121419,
                                 0x111216, 0x1b1f26 } },
              confirm_well = { 5,
                               { 0, 25, 70, 90, 100 },
                               { 0x07090d, 0x0e1015, 0x15181e, 0x1c1f26, 0x0b0d12 } };
unsigned wide_len(const unsigned *s) {
    unsigned n = 0;
    while (s && s[n]) ++n;
    return n;
}
/* w's text in the theme's face where it has the characters, centred; px of that face. */
static void face_label(void *w, int px) {
    const char *face = theme_face(widget_get_text(w), -1, &px);
    widget_set_prop_str(w, "style:normal:font_name", face ? face : "default");
    widget_set_prop_int(w, "style:normal:font_size", px);
    widget_set_prop_str(w, "style:normal:text_align_h", "center");
    widget_set_prop_str(w, "style:normal:text_align_v", "middle");
}
/* A prompt line wider than its 327 px box stops at the last space or comma that fits; the rest, a
 * label of its own under it. A line with nowhere to break keeps its marquee. */
static void *confirm_wrap(void *w, void *line, void *canvas) {
    enum { CHARS = 96 };
    unsigned head[CHARS];
    const unsigned *s = widget_get_text(line);
    int n = (int)wide_len(s), px = 28, cut = 0;
    if (!n || n >= CHARS || !P(canvas, CANVAS_LCD)) return (void *)0;
    canvas_set_font(canvas, theme_face(s, n, &px), px);
    if (canvas_measure_text(canvas, s, n) <= 327) return (void *)0;
    for (int k = 1; k < n && canvas_measure_text(canvas, s, k) <= 327; ++k)
        if (s[k] == ' ' || s[k - 1] == ',') cut = k;
    if (!cut) return (void *)0;
    void *rest = hscroll_label_create(w, 0, 0, 327, 30);
    widget_use_style(rest, widget_get_prop_str(line, "style", ""));
    widget_set_text(rest, s + cut + (s[cut] == ' '));
    memcpy(head, s, (unsigned)cut * sizeof *head);
    head[cut] = 0;
    widget_set_text(line, head);
    return rest;
}
/* The confirm's first paint, before its background: its background clear, the prompt placed or (the
 * tray: the selected row says what it's for) hidden, msg_delcheckbutton left where it is, the
 * buttons moved onto their cells, each max(76, text + 36) wide. Stock binds them by name. Their ink
 * goes: confirm_labels draws it over its shadow. Once: the cells' height says it is done. Stock's
 * asset holds the two buttons, and dialog_confirminfo_dialog_init adds only hscroll_labels, the
 * prompt's lines, and msg_delcheckbutton, so whatever else is here is a line. */
static void confirm_dress(void *w, void *canvas, int form) {
    static const char *const clear[] = { "style:normal:text_color", "style:pressed:text_color",
                                         "style:over:text_color", "style:focused:text_color",
                                         "style:pressed:bg_color" };
    void *cell[2] = { widget_lookup(w, "img_cancel", 0), widget_lookup(w, "img_enter", 0) },
         *check = widget_lookup(w, "msg_delcheckbutton", 0);
    if (!cell[0] || !cell[1] || I(cell[0], W_H) == 30) return;
    widget_set_prop_int(w, "style:normal:bg_color", 0);
    if (form == FORM_TRAY) widget_set_text_utf8(cell[1], st.confirm_verb);
    int line = 0, width[2];
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) {
        void *c = widget_get_child(w, i);
        if (c == check || !tk_strcmp(widget_get_type(c), "button")) continue;
        if (form == FORM_TRAY) {
            widget_set_visible(c, 0, 0);
            continue;
        }
        widget_move_resize(c, 24, 61 + 30 * line++, 327, 30);
        face_label(c, 28);
        void *rest = confirm_wrap(w, c, canvas);
        if (rest) {
            widget_move_resize(rest, 24, 61 + 30 * line++, 327, 30);
            face_label(rest, 28);
        }
    }
    for (int k = 0; k < 2; ++k) {
        const unsigned *s = widget_get_text(cell[k]);
        int px = CONFIRM_CELL_PX, n = (int)wide_len(s);
        canvas_set_font(canvas, theme_face(s, n, &px), px);
        width[k] = (int)(canvas_measure_text(canvas, s, n) + 36.5f);
        if (width[k] < 76) width[k] = 76;
        for (unsigned j = 0; j < sizeof clear / sizeof *clear; ++j)
            widget_set_prop_int(cell[k], clear[j], 0);
    }
    int x = (375 - width[0] - width[1]) / 2, y = form == FORM_TRAY ? 265 : 258;
    widget_move_resize(cell[0], x, y, width[0], 30);
    widget_move_resize(cell[1], x + width[0], y, width[1], 30);
}
/* Classic's tick for a checked row, 13 x 8 from x, y: dy, x, w of each 1 px line. */
static void paint_tick(void *canvas, int x, int y) {
    static const unsigned char tick[][3] = { { 0, 11, 2 }, { 1, 10, 2 }, { 2, 9, 2 }, { 3, 8, 2 },
                                             { 4, 0, 2 },  { 4, 7, 2 },  { 5, 1, 2 }, { 5, 6, 2 },
                                             { 6, 2, 2 },  { 6, 5, 2 },  { 7, 3, 3 } };
    for (unsigned i = 0; i < sizeof tick / sizeof *tick; ++i)
        canvas_fill_rect(canvas, x + tick[i][1], y + tick[i][0], tick[i][2], 1);
}
/* Delete File's check_button under a set_text theme: its icons are stock's rings, emptied
 * (ringnav_image_add), so the box is drawn where the 26 px icon goes. Stock centres icon, its 10 px
 * spacer and text together (s_msg_checkbutton_style: icon_at 5). Under the button, whose icon is
 * clear; the button's own invalidate repaints this. */
static void confirm_check(void *w, void *canvas) {
    void *check = theme()->set_text ? widget_lookup(w, "msg_delcheckbutton", 0) : (void *)0;
    void *lcd = check ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!lcd) return;
    const unsigned *s = widget_get_text(check);
    canvas_set_font(canvas, (void *)0, 20);
    int text = (int)(canvas_measure_text(canvas, s, (int)wide_len(s)) + 0.5f);
    int x = I(check, W_X) + (I(check, W_W) - 26 - 10 - text) / 2 + 3,
        y = I(check, W_Y) + I(check, W_H) / 2 - 10;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), ink = RGBA(scheme()->ink);
    rect_t box = { x, y, 20, 20 };
    round_ring(canvas, &box, 6, ink);
    stock_canvas_set_fill_color(canvas, ink);
    if (widget_get_prop_int(check, "value", 0)) paint_tick(canvas, x + 4, y + 6);
    stock_canvas_set_fill_color(canvas, fill);
}
/* Under the selection: Form A's paper, or the tray with its shadow, then the track. */
static void confirm_track(void *w, void *canvas, int form) {
    void *lcd = P(canvas, CANVAS_LCD), *cancel = widget_lookup(w, "img_cancel", 0),
         *enter = widget_lookup(w, "img_enter", 0);
    if (!lcd || !cancel || !enter) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    rect_t t = { I(cancel, W_X), I(cancel, W_Y), I(cancel, W_W) + I(enter, W_W), I(cancel, W_H) };
    int light = ipod_light();
    if (form == FORM_TRAY) {
        rect_t ring = { 37, 253, 301, 54 }, tray = { 38, 254, 299, 52 };
        paint_art(w, canvas, "classic_light_confirm_shadow", 30, 248);
        round_ring(canvas, &ring, 10, 0x66000000u);
        paint_round_rows(canvas, &tray, &confirm_tray, 9);
        paint_round_rows(canvas, &t, &confirm_well, 6);
        inset_top(canvas, &t, 6, 0xdd);
    } else {
        bar_rect(canvas, RGBA(scheme()->paper), 0, BAR_ROWS, 375, 320 - BAR_ROWS);
        void *bar = *(void *const *)system_bar; /* over Quick Settings nothing else draws it */
        if (bar) widget_paint(bar, canvas);
        st.paint_w = w;
        if (light) {
            /* an inset shadow from the right (-3, 0, blur 3, spread -2) */
            static const unsigned char right[] = { 0x02, 0x06, 0x0e, 0x18 };
            for (int i = 0; i < t.h; ++i) {
                int n = round_inset(6, i, t.h);
                round_row(canvas, &t, 6, i, 0xff000000u | confirm_light[i]);
                for (int c = 0; c < 4; ++c)
                    scrub_row(canvas, (unsigned)right[c] << 24, t.x + t.w - n - 4 + c, t.y + i, 1);
            }
        } else {
            paint_round_rows(canvas, &t, &confirm_dark, 6);
            inset_top(canvas, &t, 6, 0xaa);
        }
    }
    round_ring(canvas, &t, 6, form == FORM_TRAY || !light ? 0x88000000u : 0x1a000000u);
    row_below(canvas, &t, 6, form == FORM_PAGE && light ? 0xb0ffffffu : 0x29ffffffu);
    stock_canvas_set_fill_color(canvas, fill);
}
/* The lit cell, in the scheme's selection: its light top row and dark foot row, rounded at the
 * ends named (round_row_ends). */
void lit_rows(void *canvas, const rect_t *c, int ends) {
    for (int i = 0; i < c->h; ++i)
        round_row_ends(canvas, c, 6, i, 0xff000000u | stops_row(&scheme()->sel, i, c->h), ends);
    round_row_ends(canvas, c, 6, 0, 0x73ffffffu, ends);
    round_row_ends(canvas, c, 6, c->h - 1, 0x59000000u, ends);
}
/* The pale form's lit cell, 30 rows: an inset light right edge, what its shape leaves a pixel left
 * of itself, and, with a cell to its right, a 4 px shade over that one, fading down the rows. Kept
 * a function of its own with one caller: test/patch.py calls it. */
__attribute__((noinline)) static void pale_lit(void *canvas, const rect_t *c, int more) {
    static const unsigned char fade[30] = { 255, 255, 255, 255, 255, 242, 230, 217, 204, 191,
                                            178, 166, 153, 140, 127, 115, 102, 92,  83,  73,
                                            63,  53,  44,  34,  24,  15,  5,   0,   0,   0 },
                               part[4] = { 7, 5, 3, 1 };
    const unsigned char *cover = cover_of(6);
    int right = c->x + c->w - 1;
    lit_rows(canvas, c, ROUND_LEFT | ROUND_RIGHT);
    for (int i = 0; i < c->h; ++i) {
        int row = corner_row(c, 6, i);
        if (row < 0) scrub_row(canvas, 0x4dffffffu, right, c->y + i, 1);
        for (int n = 0; row >= 0 && n < 6; ++n) {
            unsigned a = (unsigned)(cover[row * 6 + n] - (n ? cover[row * 6 + n - 1] : 0));
            if (a) scrub_row(canvas, (a * 0x4d / 255) << 24 | 0xffffffu, right - n, c->y + i, 1);
        }
        for (int k = 0; more && k < 4; ++k) { /* 0x36 at the cell, 0 four pixels on */
            unsigned a = i < (int)sizeof fade ? 0x36u * part[k] / 8 * fade[i] / 255 : 0;
            if (a) scrub_row(canvas, a << 24, right + 1 + k, c->y + i, 1);
        }
    }
}
/* The tray's lit cell: an outer ring. */
static void tray_lit(void *canvas, const rect_t *c) {
    rect_t ring = { c->x - 1, c->y - 1, c->w + 2, c->h + 2 };
    round_ring(canvas, &ring, 7, 0x77000000u);
    lit_rows(canvas, c, ROUND_LEFT | ROUND_RIGHT);
}
/* Over the selection: each cell's text over its shadow a row up. White, but for the dark half's
 * unlit cell. */
static void confirm_labels(void *w, void *canvas) {
    void *cell[2] = { widget_lookup(w, "img_cancel", 0), widget_lookup(w, "img_enter", 0) };
    int light = ipod_light(), lit = st.sel_w == w ? st.sel_row : -1;
    if (!cell[0] || !cell[1] || !P(canvas, CANVAS_LCD)) return;
    for (int k = 0; k < 2; ++k) {
        const unsigned *s = widget_get_text(cell[k]);
        unsigned n = wide_len(s);
        rect_t r = { I(cell[k], W_X), I(cell[k], W_Y), I(cell[k], W_W), I(cell[k], W_H) },
               up = { r.x, r.y - 1, r.w, r.h };
        draw_centred(canvas, s, n, &up, CONFIRM_CELL_PX, flip_if(light, 0x4d000000u));
        draw_centred(canvas, s, n, &r, CONFIRM_CELL_PX,
                     RGBA(light                                      ? flip(0xffffff)
                          : k == lit || confirm_form(w) == FORM_TRAY ? 0xffffff
                                                                     : 0xcfd6dc));
    }
}
/* The hold menu's panel, Cover Flow's flip panel (coverflow_panel_paint): the navbar its header,
 * the list its rows. over: 0 before the dialog's children, 1 after them. */
static void hold_paint(void *w, void *canvas, int over) {
    void *nav = w && w == st.qm_dialog && dialogs_painted() ? widget_lookup(w, "view_navbar", 1)
                                                            : (void *)0,
         *list = nav ? widget_lookup(w, "list_view", 1) : (void *)0;
    if (!list || !P(canvas, CANVAS_LCD)) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    coverflow_panel_paint(canvas, I(nav, W_X), I(nav, W_Y), I(nav, W_H), I(nav, W_H) + I(list, W_H),
                          over);
    stock_canvas_set_fill_color(canvas, fill);
}
/* The toast (msginfo_dialog) and the update's dialog: stock's full-screen dialog, clear, under a
 * box sized to its lines, whichever half. The serial's QR takes the box's colour on its own rect.
 * Since V1.34 the plain toast (kind 1) is one word-wrapping label, sized here to its wrapped lines;
 * V1.32's and its kinds 2 and 4 are hscroll_labels, a line each.
 */
#define TOAST_RGBA 0xee2b2b2bu
#define TOAST_LABEL_W 331
#define TOAST_LINE 24
#define TOAST_PX 22
static void toast_paint(void *w, void *canvas) {
    if (!w || P(w, W_PARENT) != window_manager() || !dialogs_painted()) return;
    const char *name = widget_get_prop_str(w, "name", "");
    int qr = !tk_strcmp(name, "showsn_dialog");
    if (!qr && tk_strcmp(name, "msginfo_dialog") && tk_strcmp(name, "checkfw_dialog")) return;
    int light = ipod_light();
    if (!widget_get_prop_int(w, "_toast", 0)) {
        widget_set_prop_int(w, "_toast", 1);
        widget_set_prop_int(w, "style:normal:bg_color", qr ? (int)flip_if(light, TOAST_RGBA) : 0);
        widget_set_prop_int(w, "style:normal:border_color", 0);
        if (qr) widget_set_prop_int(w, "style:normal:round_radius", 12);
    }
    if (qr || !P(canvas, CANVAS_LCD)) return;
    int high = 0, widest = 0;
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) {
        void *c = widget_get_child(w, i);
        int wrap = !tk_strcmp(widget_get_type(c), "label");
        if (!wrap && tk_strcmp(widget_get_type(c), "hscroll_label")) continue;
        if (I(c, W_W) != TOAST_LABEL_W) {
            widget_move_resize(c, 22, 145 + high, TOAST_LABEL_W, TOAST_LINE);
            face_label(c, TOAST_PX);
            text_ink(c, 0xffffffffu, INK_LABEL);
            if (wrap) /* stock's 120 px: five lines */
                label_resize_to_content(c, TOAST_LABEL_W, TOAST_LABEL_W, TOAST_LINE, 120);
        }
        const unsigned *s = widget_get_text(c);
        int px = TOAST_PX, chars = (int)wide_len(s);
        canvas_set_font(canvas, theme_face(s, chars, &px), px);
        int text = (int)(canvas_measure_text(canvas, s, chars) + 0.5f);
        if (text > widest) widest = text;
        high += I(c, W_H);
    }
    if (!high) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    /* a line too long scrolls or wraps inside its label: never a box wider than that */
    int bw = widest + 44 < TOAST_LABEL_W ? widest + 44 : TOAST_LABEL_W;
    rect_t box = { (375 - bw) / 2, 138, bw, high + 15 };
    fill_box(canvas, &box, flip_if(light, TOAST_RGBA), 12);
    stock_canvas_set_fill_color(canvas, fill);
}

/* Load and settle the painted surface's selection, then draw it behind the rows: Minimal's card
 * (a dot on Home), Modern's full-width accent bar (SEL_BAR) or Classic's stops band (SEL_STOPS). */
static void paint_selection(void *w, void *canvas) {
    if (kind(w)) st.sel_w = st.sel_widget = st.sel_accent = (void *)0;
    if (!w || !canvas || !kind(w)) return;
    void *top = window_manager_get_top_window(window_manager());
    int i, shown = !st.touch_mode;
    void *own = window_of(w);
    if (own && own != top && slides(top)) {
        /* The page under a sliding one, painted into the animator's snapshot: draw the row it
         * holds. Loading, recall and settling read the top page's state, so none of it runs.
         * A pending untouch is a Return by button, which shows the row of the page behind. Home's
         * carousel shows its selected card, as below. */
        if (!usable() || !load_rows(&g_menu, w) || g_menu.kind == 3) return;
        i = index_of(&g_menu, widget_get_prop_int(w, SEL, -1));
        top = own;
        if (st.untouch_timer) shown = 1;
    } else {
        if (surface((void *)0, (void *)0) != w) return;
        if (!load(&g_menu, w, !pointer_down())) {
            cancel_center();
            return;
        }
        if (st.center_timer && !pending_matches(top, &g_menu)) cancel_center();
        if (g_menu.kind == 3) return; /* Home shows its selected card. */
        i = reconcile(&g_menu, !moving(&g_menu) && !pointer_down());
    }
    /* Touch hides the selection until the wheel or a button, except on Home (iPod's list) and a
     * pop-up's buttons, which open on their first (Cancel). */
    int home = home_menu(top);
    if (home && i >= 0) coverflow_home_row(w, g_menu.at[i], g_menu.id[i], 1);
    if (i < 0 || (!shown && !home && g_menu.kind != 4)) return;
    rect_t r = bounds(&g_menu, i), old;
    /* A boundary detent nudges the selection against the end until it springs back. */
    if (fx_live(w) && st.bump_dir) r.y -= st.bump_dir * theme()->bump_px;
    if (r.w < 5 || r.h < 5 || !P(canvas, CANVAS_LCD)) return;
    void *lcd = P(canvas, CANVAS_LCD);
    unsigned fill_color = (unsigned)I(lcd, LCD_FILL_COLOR);
    unsigned stroke_color = (unsigned)I(lcd, LCD_STROKE_COLOR);
    if (!clip_surface(canvas, &g_menu, &old)) return;
    int form = g_menu.kind == 4 ? confirm_form(g_menu.w) : FORM_NONE;
    if (form) { /* the cell itself, not nudged */
        void *e = g_menu.at[i];
        rect_t cell = { I(e, W_X), I(e, W_Y), I(e, W_W), I(e, W_H) };
        if (form == FORM_TRAY)
            tray_lit(canvas, &cell);
        else
            pale_lit(canvas, &cell, !i);
        st.sel_w = w;
        st.sel_row = i;
        st.sel_y = cell.y + cell.h / 2;
        stock_canvas_set_fill_color(canvas, fill_color);
        stock_canvas_set_stroke_color(canvas, stroke_color);
        canvas_set_clip_rect(canvas, &old);
        return;
    }
    /* A list row gets the full surface width; a grid tile keeps its own rect. */
    int tile = 2 * r.w < I(g_menu.w, W_W);
    /* More's Speed row under the stops band: one band, the row's whole height, its cells on the
     * paper so their marks read, the wheel's framed. */
    void *speed = (void *)0, *speed_cell = g_menu.at[i];
    rect_t cell = r;
    if (theme()->painter[PAINT_SELECTION] == SEL_STOPS && speed_stop(speed_cell)) {
        void *button = P(speed_cell, W_PARENT);
        speed = P(button, W_PARENT);
        r.y -= I(speed_cell, W_Y) + I(button, W_Y);
        r.h = I(speed, W_H);
        tile = 0;
    }
    if (!tile) {
        r.x = 0;
        r.w = I(g_menu.w, W_W);
        /* The Library's row is a button over the top BODY of its pitch: the bar takes the whole
         * row, or the last row's leaves a strip at the screen's bottom. */
        void *row = P(g_menu.at[i], W_PARENT);
        if (!I(g_menu.at[i], W_Y) && is_row(row) && I(row, W_H) > r.h) r.h = I(row, W_H);
    }
    /* Classic: its stops band, Modern: the accent's bar, under the rows' own ink. Minimal: its card
     * (sel_widget), off-white with dark ink under its own accent, else the accent's under the rows'
     * own ink. */
    int painter = theme()->painter[PAINT_SELECTION], band = painter != SEL_CARD,
        corners = setting(SETTING_ROUNDED);
    const scheme_t *S = scheme();
    st.sel_widget = band ? (void *)0 : home ? P(g_menu.at[i], W_PARENT) : g_menu.at[i];
    st.sel_home = home && !band;
    /* Light Mode: ink on a coloured bar or card stays white (ringnav_style_color); so does the
     * stops band's, or its sel_text. */
    st.sel_accent =
        (painter == SEL_STOPS || (!neutral(S->tones[0]) && !(S->flags & SCHEME_CARD))) &&
                !st.sel_home
            ? (home ? P(g_menu.at[i], W_PARENT) : g_menu.at[i])
            : (void *)0;
    if (band && (!tile || g_menu.kind == 4)) {
        if (speed) st.sel_accent = speed;
        if (painter == SEL_STOPS) { /* Classic's band: square and full width, the glass clips it */
            int top = !!S->sel_top, tall = S->sel_tall_edge && r.h > geom(G_PITCH),
                edge = tall ? 2 : !!S->sel_edge;
            if (tall) paint_stops(canvas, r.x, r.y, r.w, r.h, &S->sel); /* its lines go over them */
            if (top) {
                stock_canvas_set_fill_color(canvas, RGBA(S->sel_top));
                canvas_fill_rect(canvas, r.x, r.y, r.w, 1);
            }
            if (tall && S->sel_tall_top) {
                stock_canvas_set_fill_color(canvas, RGBA(S->sel_tall_top));
                canvas_fill_rect(canvas, r.x, r.y + top, r.w, 1);
            }
            if (!tall) paint_stops(canvas, r.x, r.y + top, r.w, r.h - top - edge, &S->sel);
            if (tall) {
                stock_canvas_set_fill_color(canvas, RGBA(S->sel_tall_edge));
                canvas_fill_rect(canvas, r.x, r.y + r.h - 2, r.w, 1);
                stock_canvas_set_fill_color(canvas, RGBA(S->sel_tall_line));
                canvas_fill_rect(canvas, r.x, r.y + r.h - 1, r.w, 1);
            } else if (edge) {
                stock_canvas_set_fill_color(canvas, RGBA(S->sel_edge));
                canvas_fill_rect(canvas, r.x, r.y + r.h - 1, r.w, 1);
            }
        } else {
            /* Modern's bar, a list row's a pill under Rounded Corners, inset like Minimal's card */
            rect_t bar = r;
            int pill = !tile && corners ? pill_radius(g_menu.at[i], r.h, SEL_INSET) : 0;
            if (pill) bar.x += SEL_INSET, bar.w -= 2 * SEL_INSET;
            gradient(canvas, bar, S->tones[0], S->tones[1], S->tones[4], pill);
        }
    } else if (home) {
        rect_t dot = { HOME_DOT_X, r.y + (r.h - HOME_DOT) / 2, HOME_DOT, HOME_DOT };
        fill_box(canvas, &dot, 0xffffffff, HOME_DOT / 2);
    } else { /* Sudo's card: inset from the glass's edges, its corners rounded */
        int grid = tile && g_menu.kind != 4; /* pop-up buttons keep their row inset */
        int square = !tile && !corners;      /* Corners: Square, full width */
        int in = grid ? TILE_INSET : square ? 0 : SEL_INSET, vertical = grid ? TILE_INSET : 0;
        rect_t card = { r.x + in, r.y + vertical, r.w - 2 * in, r.h - 2 * vertical };
        int radius = !corners ? 0 : tile ? SEL_RADIUS : pill_radius(g_menu.at[i], card.h, in);
        fill_box(canvas, &card, RGBA(S->flags & SCHEME_CARD ? S->fill : S->tones[0]), radius);
    }
    if (speed) {
        void *button = P(speed_cell, W_PARENT);
        for (unsigned c = 0, n = widget_count_children(button); c < n; ++c) {
            void *e = widget_get_child(button, c);
            if (!speed_stop(e) || !widget_get_visible(e)) continue;
            rect_t plate = { cell.x + I(e, W_X) - I(speed_cell, W_X), cell.y, I(e, W_W), cell.h };
            fill_box(canvas, &plate, RGBA(0), SEL_RADIUS); /* black in stock's terms: the paper */
        }
        frame_box(canvas, &cell, 0xffffffff, SEL_RADIUS);
    }
    st.sel_w = w;
    st.sel_row = i;
    st.sel_y = r.y + r.h / 2;
    if (g_menu.at[i] != st.marq_row || g_menu.id[i] != st.marq_id) {
        st.marq_row = g_menu.at[i];
        st.marq_id = g_menu.id[i];
        rearm(&st.marq_timer, marq_moved, 0);
    }
    /* a pop-up button's tile: framed white on any accent; Classic's band has its own top line and
     * edge, as an iPod's */
    if (g_menu.kind == 4 && r.w < I(g_menu.w, W_W) && painter != SEL_STOPS)
        frame_box(canvas, &r, 0xffffffff, SEL_RADIUS);
    /* Save/restore explicitly: this firmware's canvas_save/restore cover neither clip nor
     * either color, and the global alpha is deliberately never touched. */
    stock_canvas_set_fill_color(canvas, fill_color);
    stock_canvas_set_stroke_color(canvas, stroke_color);
    canvas_set_clip_rect(canvas, &old);
}

/* A theme's separators, behind the rows: the last rows of each two-line row but the selected one's,
 * across the list. A row's two_lines is a few words of its children, on a pool of a few rows. A
 * page_list's rows are the view's own children, each page_detail high and named so, and every stock
 * scroll view comes here too: no walk, the screenful from the top's index, a name read only at that
 * height. Minimal stops at its empty sep. */
static int two_lines(void *row);
static void paint_separators(void *w, void *canvas) {
    rect_t old;
    int clip[4];
    const theme_t *T = theme();
    int k = w && T->sep[0].n ? kind(w) : 0;
    if (!(k == 1 || (k == 2 && I(w, ROW_HEIGHT) > geom(G_PITCH))) || !P(canvas, CANVAS_LCD) ||
        (k == 2 && !load_rows(&g_menu, w)) ||
        !clip_within(canvas, &old.x, clip, I(canvas, CANVAS_X), I(canvas, CANVAS_Y), I(w, W_W),
                     I(w, W_H)))
        return;
    canvas_set_clip_rect(canvas, clip);
    const stops_t *lines = &T->sep[ipod_light()];
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    /* st.sel_row counts load_rows' clickable entries, which a view's children needn't all be */
    int sel = st.sel_w == w ? st.sel_row : -1;
    void *sel_child = st.sel_w == w ? st.sel_accent : (void *)0;
    if (k == 2) {
        for (int i = 0; i < g_menu.n; ++i)
            if (i != sel && two_lines(g_menu.at[i])) {
                rect_t r = bounds(&g_menu, i);
                paint_stops(canvas, 0, r.y + r.h - lines->n, I(w, W_W), lines->n, lines);
            }
    } else {
        int top = I(w, SCROLL_Y), h = T->page_detail, n = (int)widget_count_children(w);
        for (int i = top > 0 ? top / h : 0; i < n; ++i) {
            void *row = widget_get_child(w, i);
            int y = I(row, W_Y) - top, rh = I(row, W_H);
            if (y >= I(w, W_H)) break;
            /* the selected one may be the row's button (Classic's Playlists) */
            if (row != sel_child && (rh == h || rh == geom(G_PITCH)) &&
                (!sel_child || P(sel_child, W_PARENT) != row) &&
                !tk_strcmp(widget_get_prop_str(row, "name", ""), PAGE_TALL))
                paint_stops(canvas, 0, y + rh - lines->n, I(w, W_W), lines->n, lines);
        }
    }
    stock_canvas_set_fill_color(canvas, fill);
    canvas_set_clip_rect(canvas, &old);
}

static void edit_paint(void *w, void *canvas);

/* Minimal's own accent: stock's white radio
 * images (select, unselect: a 26 px ring, a 12 px dot when checked) vanish on the selected row's
 * off-white card, so they are drawn again over it in the card's ink. So does the Wi-Fi, Bluetooth
 * and Tidal rows' navbar_delete, drawn again as list_delete, its copy in list_into's grey;
 * navbar_delete itself stays white for the playlist page's Delete. A set_text theme (Classic)
 * has neither: its rings are emptied and a checked row gets a tick.
 */
static void paint_radio(void *w, void *canvas) {
    if (!w) return;
    if (theme()->set_text) {
        const scheme_t *S = scheme();
        /* Classic: a checked row's tick (dy, x, w of each 1 px line), the theme's own mark,
         * in the selection's text or the ink; stock's ring images are empty (ringnav_image_add). */
        const char *image = widget_get_prop_str(w, "image", "");
        if (!P(canvas, CANVAS_LCD)) return;
        unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
        int x = I(w, W_W) / 2 - 6, y = I(w, W_H) / 2 - 4;
        /* A playlist's quiet More: stock's dots, emptied as a ring (pl_row), in the second line's
         * grey: 4 px dots, 7 px apart. The band's chevron takes their place (pl_fit). */
        if (!tk_strcmp(image, "unselect") &&
            tk_str_start_with(widget_get_prop_str(w, "name", ""), "img_more")) {
            for (int i = 0; i < 3 && !painting_in(st.sel_accent); ++i) {
                rect_t dot = { x - 3 + i * 7, y + 2, 4, 4 };
                fill_box(canvas, &dot, ui_ink(RGBA(S->muted)), 2);
            }
            stock_canvas_set_fill_color(canvas, fill);
            return;
        }
        if (tk_strcmp(image, "select")) return;
        stock_canvas_set_fill_color(canvas,
                                    RGBA(painting_in(st.sel_accent) ? S->sel_text : S->ink));
        paint_tick(canvas, x, y);
        stock_canvas_set_fill_color(canvas, fill);
        return;
    }
    if (!st.sel_widget || st.sel_home) return;
    const scheme_t *S = scheme();
    if (!(S->flags & SCHEME_CARD) || !P(canvas, CANVAS_LCD)) return;
    const char *image = widget_get_prop_str(w, "image", "");
    int more = tk_str_start_with(widget_get_prop_str(w, "name", ""), "img_more");
    int dot = !tk_strcmp(image, "select"), del = !tk_strcmp(image, "navbar_delete");
    if (!more && !dot && !del && tk_strcmp(image, "unselect")) return;
    void *row = P(w, W_PARENT);
    for (int depth = 0; row && row != st.sel_widget && depth < 8; ++depth) row = P(row, W_PARENT);
    if (row != st.sel_widget) return;
    int x = I(w, W_W) / 2, y = I(w, W_H) / 2;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    if (del) {
        unsigned bitmap[64]; /* bitmap_t: width @0, height @4 */
        if (widget_load_image(w, "list_delete", bitmap)) return;
        rect_t clear = { x - (int)bitmap[0] / 2, y - (int)bitmap[1] / 2, (int)bitmap[0],
                         (int)bitmap[1] };
        fill_box(canvas, &clear, RGBA(S->fill), 0);
        canvas_draw_icon(canvas, bitmap, x, y);
        stock_canvas_set_fill_color(canvas, fill);
        return;
    }
    if (more) {
        /* Stock's 4px dots, 7px apart, centred in the 50px image. */
        rect_t clear = { x - 13, y - 4, 26, 8 };
        fill_box(canvas, &clear, RGBA(S->fill), 0);
        for (int i = -1; i <= 1; ++i) {
            rect_t dot = { x - 11 + (i + 1) * 7, y - 2, 4, 4 };
            fill_box(canvas, &dot, RGBA(0x171717), 2);
        }
        stock_canvas_set_fill_color(canvas, fill);
        return;
    }
    rect_t ring = { x - 13, y - 13, 26, 26 }, hole = { x - 11, y - 11, 22, 22 },
           in = { x - 6, y - 6, 12, 12 };
    fill_box(canvas, &ring, RGBA(0x171717), 13);
    fill_box(canvas, &hole, RGBA(S->fill), 11);
    if (dot) fill_box(canvas, &in, RGBA(0x171717), 6);
    stock_canvas_set_fill_color(canvas, fill);
}

/* Light Mode: stock's own `>` (a list_into image) on the coloured bar or card's row, drawn again
 * white over the selection's colour, as paint_chevrons draws the payload's. A chevron_sel theme's
 * stock image is empty (ringnav_image_add), so this is its only one, in either half, over nothing
 * cleared: the band shows. */
static void paint_into(void *w, void *canvas) {
    unsigned bitmap[64];                         /* bitmap_t: width @0, height @4 */
    int sel_only = w ? theme()->chevron_sel : 0; /* in either half: stock's own is empty */
    int box = w ? theme()->chevron_x : 0;
    if (!w || !(ipod_light() || sel_only) || !st.sel_accent || !P(canvas, CANVAS_LCD) ||
        tk_strcmp(widget_get_prop_str(w, "image", ""), "list_into") ||
        !painting_in(st.sel_accent) ||
        (sel_only && !tk_strcmp(widget_get_prop_str(window_of(w), "name", ""), "folder_page")) ||
        widget_load_image(w, box ? "classic_light_chevron" : LIST_INTO_WHITE, bitmap))
        return;
    /* Only the middle half is cleared, where the glyph's ink is (rows 16-33 of 50): the whole
     * 50 px image would cross a 48 px row's top, over the bar's highlight and the row above. */
    int x = box ? box + (int)bitmap[0] / 2 - I(canvas, CANVAS_X) : I(w, W_W) / 2, y = I(w, W_H) / 2;
    rect_t clear = { x - (int)bitmap[0] / 2, y - (int)bitmap[1] / 4, (int)bitmap[0],
                     (int)bitmap[1] / 2 };
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    if (!sel_only) fill_box(canvas, &clear, RGBA(tones()[0]), 0);
    canvas_draw_icon(canvas, bitmap, x, y);
    stock_canvas_set_fill_color(canvas, fill);
}

/* The theme's scrollbar (theme_t.sb_*), a thumb alone: stock's mobile bar fades, so it is drawn
 * from the list's border paint, after its children, in a final colour. list_bar is the scroll_bar_m
 * of a list that scrolls: stock's max is at least the bar's own h, which can pass a short list's,
 * so the content is read off its sibling. A table's virtual height also stands in for that max
 * (*table): stock sets its bar only from its client's scroll, so before the first scroll the max is
 * none, or an asset's 1000. ponytail: a type compare per list paint under the theme only; cache the
 * bar per list if paints get heavy. */
static void *list_bar(void *w, int *table) {
    const char *type = widget_get_type(w);
    if (tk_strcmp(type, "list_view") && tk_strcmp(type, "table_view")) return (void *)0;
    void *bar = (void *)0;
    int scrolls = 0;
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) {
        void *c = widget_get_child(w, i);
        const char *t = widget_get_type(c);
        if (!tk_strcmp(t, "scroll_bar_m"))
            bar = c;
        else if (!tk_strcmp(t, "scroll_view"))
            scrolls |= I(c, VIEW_CONTENT_H) > I(c, W_H);
        else if (!tk_strcmp(t, "table_client"))
            scrolls |= (*table = table_client_get_virtual_h(c)) > I(c, W_H);
    }
    return scrolls ? bar : (void *)0;
}
static void paint_scrollbar(void *canvas, void *bar, int table) {
    const theme_t *T = theme();
    void *list = P(bar, W_PARENT), *lcd = P(canvas, CANVAS_LCD);
    int max = table ? table : I(bar, BAR_MAX), value = I(bar, BAR_VALUE);
    int y0 = I(canvas, CANVAS_Y), h = I(list, W_H);
    int top = (y0 > T->sb_top ? y0 : T->sb_top) - y0,
        bottom = (y0 + h < T->sb_bottom ? y0 + h : T->sb_bottom) - y0, track = bottom - top;
    if (!lcd || max <= 0 || track <= 0) return;
    int len = track * h / max;
    if (len < T->sb_min) len = T->sb_min;
    if (len > track) len = track;
    value = value < 0 ? 0 : value > max ? max : value;
    while (max >> 22) max >>= 1, value >>= 1; /* (track - len) * value stays in an int */
    rect_t r = { I(list, W_W) - T->sb_right - T->sb_w, top + (track - len) * value / max, T->sb_w,
                 len };
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), c = 0xff000000u | T->sb_thumb[ipod_light()];
    for (int i = 0; i < len; ++i) { /* radius 2: two round rows at each end, one fill between */
        if (i != 2 || len <= 4) {
            round_row(canvas, &r, 2, i, c);
            continue;
        }
        stock_canvas_set_fill_color(canvas, (RGBA(c & 0xffffffu) & 0xffffffu) | 0xff000000u);
        canvas_fill_rect(canvas, r.x, r.y + 2, r.w, len - 4);
        i = len - 3;
    }
    stock_canvas_set_fill_color(canvas, fill);
}

/* Stock paints children first and calls this with the surface's canvas origin restored. */
static void quick_scrub(void *w, void *canvas);
int ringnav_paint(void *w, void *canvas) {
    if (st.pull_page && !pull_live()) pull_cancel();
    int result = search_del(w) ? 0 : stock_paint_trampoline(w, canvas);
    night_paint(canvas);
    coverflow_paint(w, canvas);
    lockscreen_paint(w, canvas);
    if (w == st.cf_letter_surface && st.cf_letter &&
        (st.cf_letter_held || (unsigned)time_now_ms() - st.cf_letter_at < LETTER_MS) &&
        P(canvas, CANVAS_LCD)) {
        unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
        cf_label(w, canvas);
        stock_canvas_set_fill_color(canvas, fill);
    }
    edit_paint(w, canvas);
    photos_paint(w, canvas);
    books_paint(w, canvas);
    spot_paint(w, canvas);
    if (theme()->sb_w) {
        int table = 0;
        void *bar = w ? list_bar(w, &table) : (void *)0;
        if (bar) paint_scrollbar(canvas, bar, table);
    }
    addon_paint(w, canvas);
    peq_paint(w, canvas);
    visualizer_paint(w, canvas);
    games_paint(w, canvas);
    /* Even a page with no navigable pane must end pending input when it is painted. */
    if (st.center_timer || st.home_surface) {
        void *wm = window_manager(), *top = window_manager_get_top_window(wm);
        if (!usable() || window_manager_is_animating(wm) || top != st.center_top) cancel_center();
        if (!carousel_page(top)) st.home_surface = (void *)0;
    }
    paint_chevrons(w, canvas);
    paint_radio(w, canvas);
    paint_into(w, canvas);
    paint_letter(w, canvas);
    paint_cover(w, canvas);
    paint_scrubber(w, canvas);
    slider_scrub(w, canvas);
    quick_scrub(w, canvas);
    search_over(w, canvas);
    if (w && st.np_lrc && P(w, W_PARENT) == st.np_lrc) lyrics_paint(w, canvas);
    paint_caps(w, canvas);
    hold_paint(w, canvas, 1);
    coverflow_home_clip(w, canvas, 0);
    st.paint_w = w ? P(w, W_PARENT) : (void *)0;
    return result;
}

/* A s_listitem_black list_item in view with a 335x70 s_btn_listitem button; returns the button. */
static void *list_button(void *view) {
    void *item = list_item_create(view, 0, 0, 0, 0);
    widget_use_style(item, "s_listitem_black");
    void *button = button_create(item, 20, 0, 335, 70);
    widget_use_style(button, "s_btn_listitem");
    return button;
}

/* A stock settings-style row (0x4c6b94): a list_button with a 52px icon and a 24px label at x 72
 * (no icon: at x 10, as the Bluetooth page's rows), into adding stock's list_into trailing image
 * with the label narrowed clear of it, as the stock Local Music categories place it. Returns the
 * label. */
void *list_row_core(void *view, const char *icon, int (*click)(void *, void *), void *ctx, int w,
                    int into) {
    /* The Library constructor's live words (tools/ipod.py, theme_boot): lib_body and lib_inset. */
    int compact = !tk_strcmp(widget_get_prop_str(view, "name", ""), "scroll_view_localmusic");
    int h = compact ? *(const volatile unsigned short *)LIBRARY_ROW_BODY : 70;
    void *button = list_button(view);
    if (compact) widget_resize(button, 335, h);
    if (click) widget_on(button, EVT_CLICK, click, ctx);
    if (icon) { /* centred, as stock's rows (0x5356ac) */
        void *image = image_create(
            button, compact ? *(const volatile unsigned short *)LIBRARY_ROW_ICON_X : 10, 0,
            SET_STOCK_ICON, h);
        image_base_set_image(image, icon);
        image_set_draw_type(image, IMAGE_DRAW_CENTER);
    }
    void *label = hscroll_label_create(button, icon ? 72 : 10, 0, w, h);
    widget_use_style(label, "s_scrlabel_white24l");
    set_hscroll_label_attribute(label);
    if (into) image_base_set_image(image_create(button, 282, 0, 50, h), "list_into");
    return label;
}

void *list_row(void *view, const char *icon, int (*click)(void *, void *), void *ctx) {
    return list_row_core(view, icon, click, ctx, 260, 0);
}

/* A settings row that opens a page, its label clear of the chevron. */
static void *list_row_into(void *view, const char *icon, int (*click)(void *, void *), void *ctx) {
    return list_row_core(view, icon, click, ctx, 202, 1);
}

/* Use the same trailing switch images and hit target as Shanling's settings rows: at screen x 302
 * whether the label sits in a settings button (x 20) or a full-width page row (page_row_detail,
 * peq_ui.c's). */
void list_toggle(void *label, const char *title, int on) {
    if (theme()->set_text) {
        list_value(label, title, on ? "On" : "Off");
        return;
    }
    void *button = P(label, W_PARENT);
    void *image = widget_lookup(button, "_setting_switch", 0);
    if (!image) {
        int x = 302 - I(button, W_X);
        image = image_create(button, x, 0, 50, I(button, W_H));
        widget_set_name(image, "_setting_switch");
        image_set_draw_type(image, IMAGE_DRAW_CENTER);
        widget_move_resize(label, I(label, W_X), 0, x - I(label, W_X) - 8, I(button, W_H));
    }
    widget_set_text_utf8(label, title);
    image_base_set_image(image, on ? "switch_on" : "switch_off");
}

/* Classic's settings rows (set_text): the title left and the value right-aligned in the
 * trailing column, the title ending short of it. Switches read On or Off as values. */
void list_value(void *label, const char *title, const char *value) {
    void *button = P(label, W_PARENT);
    void *text = widget_lookup(button, "_setting_value", 0);
    if (!text) {
        int x = 302 - I(button, W_X) - 80;
        text = hscroll_label_create(button, x, 0, 134, I(button, W_H));
        widget_set_name(text, "_setting_value");
        widget_use_style(text, "s_scrlabel_white20r");
        hscroll_label_set_only_focus(text, 1);
        hscroll_label_set_ellipses(text, 1);
        widget_move_resize(label, I(label, W_X), 0, x - 8 - I(label, W_X), I(button, W_H));
    }
    widget_set_text_utf8(label, title);
    widget_set_text_utf8(text, value);
    theme_font(text, ROLE_TITLE, FONT_ROW);
}

/* Under a set without stock's toolbars (toolbar 0) a page's switch is a row: stock's img_switch
 * stays, hidden with its toolbar, for stock's callbacks to find by name, and the row's click is
 * its click. A click rebuilds Lock Screen's list, this row with it, so it waits for the next loop
 * pass. Under Lock Screen the row is first in the list; the Sleep Timer's list-less page has it
 * over its fields, tools/ipod.py moving them down. The row reads what the image shows. */
#define SWITCH_ROW "_switch_row"
static void switch_text(void *win, void *label) {
    void *sw = widget_lookup(win, "img_switch", 1);
    list_toggle(label, widget_lookup(win, "view_sleep", 1) ? "Sleep Timer" : "Lock Screen",
                sw && !tk_strcmp(widget_get_prop_str(sw, "image", ""), "switch_on"));
}
static int switch_due(const void *info) {
    (void)info;
    void *win = window_manager_get_top_window(window_manager());
    void *sw = win ? widget_lookup(win, "img_switch", 1) : (void *)0;
    char click[0x30];
    if (!sw) return 0;
    stock_dispatch_trampoline(sw, pointer_event_init(click, EVT_CLICK, sw, 0, 0));
    void *label = widget_lookup(win, SWITCH_ROW, 1); /* Lock Screen's is new, and right */
    if (label) switch_text(win, label);
    return 0;
}
static int switch_click(void *ctx, void *event) {
    (void)ctx, (void)event;
    timer_add(switch_due, (void *)0, 0);
    return 0;
}
void switch_row(void *win, void *view) {
    if (geom_val(V_TOOLBAR)) return;
    void *label = list_row(view, (const char *)0, switch_click, (void *)0);
    widget_set_name(label, SWITCH_ROW);
    switch_text(win, label);
    widget_restack(P(P(label, W_PARENT), W_PARENT), 0);
}
extern int stock_sleeptimer_trampoline(void *win, void *ctx);
int ringnav_sleeptimer(void *win, void *ctx) {
    int result = stock_sleeptimer_trampoline(win, ctx);
    if (win && !geom_val(V_TOOLBAR)) {
        int h = geom_val(V_SET_ROW);
        void *button = button_create(win, 0, 0, 375, h);
        widget_use_style(button, "s_btn_listitem");
        widget_on(button, EVT_CLICK, switch_click, (void *)0);
        void *label = hscroll_label_create(button, geom_val(V_TEXT_X), 0, 200, h);
        widget_use_style(label, "s_scrlabel_white24l");
        set_hscroll_label_attribute(label);
        widget_set_name(label, SWITCH_ROW);
        switch_text(win, label);
        theme_font(label, ROLE_TITLE, FONT_ROW);
    }
    return result;
}

/* A settings row's "Title: value" text; a set_text theme shows it as list_value, its bare text
 * (Accent's name) under Accent. */
static void row_label(void *label, const char *text) {
    if (!theme()->set_text) {
        widget_set_text_utf8(label, text);
        return;
    }
    const char *value = text;
    while (*value && !(value[0] == ':' && value[1] == ' ')) ++value;
    char title[40] = "Accent";
    unsigned n = (unsigned)(value - text);
    if (*value) {
        if (n >= sizeof title) n = sizeof title - 1;
        memcpy(title, text, n);
        title[n] = 0;
    }
    list_value(label, title, *value ? value + 2 : text);
}

/* item's place in list, comma separated, or 0; an empty item is never in it. Shared with
 * spotify.c. */
char *csv_find(char *list, const char *item) {
    unsigned n = strlen(item);
    for (char *p = list, *e; n && *p; p = e + !!*e) {
        for (e = p; *e && *e != ','; ++e) { /* demo imports no strchr */
        }
        if ((unsigned)(e - p) == n && !memcmp(p, item, n)) return p;
    }
    return 0;
}

/* item taken out of list, or added within size bytes: 1 added, 0 taken out (or empty, left
 * out), -1 no room. */
int csv_toggle(char *list, unsigned size, const char *item) {
    char *p = csv_find(list, item), *from;
    unsigned n = strlen(item), k = strlen(list);
    if (!n) return 0;
    if (p) {
        from = p + n + (p[n] == ',');
        if (!p[n] && p != list) --p; /* the last: its comma goes with it */
        while ((*p++ = *from++)) {}
        return 0;
    }
    if (k + !!k + n >= size) return -1;
    if (k) list[k++] = ',';
    memcpy(list + k, item, n + 1);
    return 1;
}

/* Stock modes, only with the screen off: 1 locks wheel volume, 2 the buttons, 3 both.
 * KEY_LOCKOUT is the separate, temporary debounce after a button press. */
static const char *const lock_names[] = { "Off", "Everything", "Buttons only", "Volume only" };
static int lock_choice(void) {
    if (g_keylock_flag && (g_keylock_mode < 1 || g_keylock_mode > 3)) {
        g_keylock_mode = 3; /* an unknown mode locks Everything */
        write_int_config(3, "SYSSET", "LOCKMODE");
    }
    return g_keylock_flag ? 4 - g_keylock_mode : 0;
}
static void lock_set(int choice) {
    g_keylock_mode = choice ? 4 - choice : 3;
    g_keylock_flag = choice != 0;
    write_int_config(g_keylock_mode, "SYSSET", "LOCKMODE");
    write_int_config(g_keylock_flag, "SYSSET", "LOCKFLAG");
}
static int lock_pick(void *ctx, void *event) {
    lock_set((int)(long)ctx);
    void *win = window_manager_get_top_window(window_manager());
    void *view = widget_lookup(win, "scroll_view_keylock", 1);
    int choice = lock_choice();
    for (unsigned i = 0; view && i < widget_count_children(view); ++i) {
        void *button = widget_get_child(widget_get_child(view, i), 0);
        image_base_set_image(widget_get_child(button, 0), (int)i == choice ? "select" : "unselect");
    }
    return 0;
}
int ringnav_keylock_rows(void *win) {
    void *view = win ? widget_lookup(win, "scroll_view_keylock", 1) : 0;
    if (!view) return 0;
    widget_destroy_children(view);
    int choice = lock_choice();
    for (int i = 0; i < 4; ++i) {
        void *label =
            list_row(view, i == choice ? "select" : "unselect", lock_pick, (void *)(long)i);
        widget_set_text_utf8(label, lock_names[i]);
    }
    return 0;
}
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
    /* An open EQ page is already the goal: a second would share peq_ui.c's one state. */
    if (page == 3 && navigator_window_is_exist("equalizer_page")) return 0;
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
    if (classic_pages()) quick_press(icon);
    if (!page) return 0; /* a button with no hold */
    prop(icon, QUICK_HOLD, page < 0 ? -page : page);
    quick.win = window_manager_get_top_window(window_manager());
    quick.icon = icon;
    quick.x = I(event, EVENT_X), quick.y = I(event, EVENT_Y);
    rearm(&quick.timer, quick_long, RATE_HOLD_MS);
    return 0;
}
/* Classic's page (quick_page, quick_scrub), in the window's
 * coordinates: each icon at opacity 0 keeps stock's image name, tap and hold, and paints nothing.
 */
static const char *const quick_icons[] = { "img_wifiswitch", "img_btswitch", "img_lock",
                                           "img_gain",       "img_usbmode",  "img_po",
                                           "img_playset",    "img_sysset" },
                         *const quick_labels[] = { "label_wifi",    "label_bt",
                                                   "label_lock",    "label_gain",
                                                   "label_usbmode", "label_outputway",
                                                   "label_playset", "label_sysset" };
/* The knob reaches past the slider, whose own repaint would leave it behind. */
static int quick_moved(void *view, void *event) {
    widget_invalidate_force(view, (void *)0);
    return 0;
}
static void quick_open(void *win) {
    void *c = widget_lookup(win, "view_menu", 1);
    if (c) widget_move_resize(c, 0, 0, I(c, W_W), I(c, W_Y) + I(c, W_H));
    static const short column[] = { 31, 115, 199, 283 }; /* 24 apart */
    for (unsigned i = 0; i < sizeof quick_icons / sizeof *quick_icons; ++i) {
        if (!(c = widget_lookup(win, quick_icons[i], 1))) continue;
        widget_move_resize(c, column[i % 4], i < 4 ? 20 : 138, 60, 60);
        widget_set_opacity(c, 0);
        if (!widget_get_prop_int(c, QUICK_HOLD, 0)) widget_on(c, EVT_POINTER_DOWN, quick_down, c);
    }
    if ((c = widget_lookup(win, "image0", 1))) widget_set_visible(c, 0, 0);
    if ((c = widget_lookup(win, "image1", 1))) widget_set_visible(c, 0, 0);
    if ((c = widget_lookup(win, "slider_backlight", 1))) {
        widget_on(c, EVT_VALUE_CHANGING, quick_moved, P(c, W_PARENT));
        widget_on(c, EVT_VALUE_CHANGED, quick_moved, P(c, W_PARENT));
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
        if (classic_pages()) quick_open(win);
        widget_on(win, EVT_DESTROY, quick_cancel, win);
        widget_on(win, EVT_WINDOW_CLOSE, quick_cancel, win);
        widget_on(win, EVT_WINDOW_BACKGROUND, quick_cancel, win);
    }
    return result;
}

/* Power's Charge limit and Wake (docs/internals.md#charge-limit, #wake), Q2POD CHARGELIMIT and
 * WAKEDOUBLE in config.ini, Display's Artist albums
 * (ARTISTCF), Playback's Year order (YEAROLD, coverflow.c's year ranks) and Artists, which
 * is stock's own PLAYSET ARTISTTYPE (artist_type, the artist page's switch;
 * docs/internals.md#album-artists), and Bluetooth's Resume on connect (RESUME, so a missing key
 * holds playback; docs/internals.md#resume-on-connect), and the Lock screen's Clock (LOCKCLOCK,
 * lockscreen.c; docs/internals.md#lock-screen), and Playback's Artist album order (ARTISTAZ), and
 * Power's Volume guard (VOLGUARD, playback.c's wheel_guard; docs/internals.md#volume-guard), and
 * Display's Hold Return (HOLDRETURN, compact_now_playing; docs/ipod.md#hold-return), and Power's
 * Standby limit (STANDBYLIMIT, ringnav_standby; docs/internals.md#standby-limit), and Bluetooth's
 * Control Device Volume (BTVOLLINK, bt_audio_ready; docs/internals.md#headset-controls-and-volume),
 * and Power's Smart Wi-Fi (SMARTWIFI, smart_poll; docs/internals.md#smart-wi-fi), and Display's
 * Large Library Mode (BIGLIBRARY, Cover Flow's letters; docs/internals.md#coverflow-thumb) and
 * Playlist View (PLAYLISTCF).
 */
/* Bluetooth's Control Device Volume on: the Q2's volume is the device's own (bt_audio_ready). */
static int bt_vol_link __attribute__((section(".scratch")));
#define STR_(x) #x
#define STR(x) STR_(x)
static int *const pod_values[] = { &st.charge_limit,
                                   &st.wake_double,
                                   &st.artist_cf,
                                   &st.year_old,
                                   0,
                                   &st.resume,
                                   &st.lock_clock,
                                   &st.artist_az,
                                   &wheel_guard,
                                   &st.hold_home,
                                   &st.standby_limit,
                                   &bt_vol_link,
                                   0,
                                   0,
                                   &st.eq_output,
                                   0,
                                   0,
                                   &st.smart_wifi,
                                   0,
                                   0,
                                   &st.big_library,
                                   &st.playlist_cf };
_Static_assert(POD_LOCKCLOCK == 6, "lockscreen.c passes pod_rows 6");
_Static_assert(sizeof pod_values / sizeof *pod_values == POD_COUNT, "a pod_values per row");
static const char *const pod_keys[] = { "CHARGELIMIT",
                                        "WAKEDOUBLE",
                                        "ARTISTCF",
                                        "YEAROLD",
                                        0,
                                        "RESUME",
                                        "LOCKCLOCK",
                                        "ARTISTAZ",
                                        "VOLGUARD",
                                        "HOLDRETURN",
                                        "STANDBYLIMIT",
                                        "BTVOLLINK",
                                        0,
                                        0,
                                        "EQOUTPUT",
                                        0,
                                        0,
                                        "SMARTWIFI",
                                        0,
                                        0,
                                        "BIGLIBRARY",
                                        "PLAYLISTCF" };
_Static_assert(sizeof pod_keys / sizeof *pod_keys == POD_COUNT, "a pod_keys entry per row");
/* a row's choices: Artist albums three, Standby limit four, every other row two */
static int pod_choices(unsigned i) { return i == POD_ARTIST_CF ? 3 : i == POD_STANDBY ? 4 : 2; }
void pod_settings(void) {
    if (st.pod_read) return;
    for (unsigned i = 0; i < sizeof pod_keys / sizeof *pod_keys; ++i)
        if (pod_keys[i]) /* Artist albums defaults to Coverflow */
            *pod_values[i] = config_value("Q2POD", pod_keys[i], pod_choices(i), i == POD_ARTIST_CF);
    st.pod_read = 1;
}
int ringnav_year_old(void) {
    pod_settings();
    return st.year_old;
}
int ringnav_artist_az(void) {
    pod_settings();
    return st.artist_az;
}
int ringnav_artist_view(void) {
    pod_settings();
    return st.artist_cf;
}
int ringnav_playlist_coverflow(void) {
    pod_settings();
    return st.playlist_cf;
}
int ringnav_lock_clock(void) {
    pod_settings();
    return st.lock_clock;
}
/* Large Library Mode: Cover Flow's letters and their half copies (coverflow_thumb.c, lettered,
 * coverflow_halves); off unless the key is 1. */
int ringnav_big_library(void) {
    pod_settings();
    return st.big_library;
}
int ringnav_eq_output(void) {
    pod_settings();
    return st.eq_output;
}
/* Advanced EQ's row. Turned off, what plays stays. */
void ringnav_eq_output_toggle(void) {
    pod_settings();
    st.eq_output = !st.eq_output;
    write_int_config(st.eq_output, "Q2POD", "EQOUTPUT");
    if (!st.eq_output) eq_output_forget();
}
int ringnav_bt_resume(void) {
    pod_settings();
    return st.resume;
}
static int bt_link(void) {
    pod_settings();
    return bt_vol_link;
}
/* read_artistype_value (a Library list's or artist page's init) and boot (playback.c
 * ringnav_bootmode): Artists as stock reads it, PLAYSET ARTISTTYPE, but Album Artist when no choice
 * is saved; stock writes the key only when Artists or the artist page's switch toggles. */
void ringnav_read_artistype(void) {
    I(artist_type, 0) = config_value("PLAYSET", "ARTISTTYPE", 2, 1);
}
/* checkBluetoothStatus' player_save_start(0) once a headset's A2DP comes up (tools/build.py
 * BT_CONNECT_START): player_save_stop stopped the local or Tidal queue, and stock restarts it
 * unpaused, so a paused queue plays, and a stopped one starts over whatever owns playback
 * (mclStartPlayer's hook yields Spotify and Internet Radio). Resume on connect off (the default):
 * a queue that was playing goes on, a paused one comes back paused, and nothing else starts. A
 * card game with the sound (addon.c) keeps it either way. */
int ringnav_bt_connect_start(int pause) {
    if (addon_game_audio()) return 0;
    if (ringnav_bt_resume()) return player_save_start(pause);
    int saved = media_owner() == 1 ? mclGetSavePlayStatus() : 0; /* 2 playing, 3 paused */
    return saved == 2 || saved == 3 ? player_save_start(saved == 3) : 0;
}
static void pod_text(int i) {
    static const char *const names[][4] = {
        { "Charge Limit: Off", "Charge Limit: " STR(CHARGE_STOP) "%" },
        { "Wake: Single Press", "Wake: Double Press" },
        { "Artist View: Grid", "Artist View: Cover Flow", "Artist View: List" },
        { "Year Order: Newest First", "Year Order: Oldest First" },
        { "Artists: Artist", "Artists: Album Artist" },
        { "Resume on Connect: Off", "Resume on Connect: On" },
        { "Clock: Standard", "Clock: Large" },
        { "Artist Album Order: Year", "Artist Album Order: A–Z" },
        { "Volume Guard: Off", "Volume Guard: On" },
        { "Hold Return: Recent", "Hold Return: Home" },
        { "Standby Limit: Off", "Standby Limit: " STR(STANDBY_LIMIT_1) " h",
          "Standby Limit: " STR(STANDBY_LIMIT_2) " h",
          "Standby Limit: " STR(STANDBY_LIMIT_3) " h" },
        { 0 }, /* Control Device Volume: a switch */
        { 0 }, /* Coverflow Sort's from coverflow.c */
        { "Refresh Cover Flow" },
        { 0 }, /* EQ per Output: a switch */
        { 0 }, /* Key Tone: a switch */
        { 0 }, /* In-Vehicle Mode: a switch */
        { 0 }, /* Smart Wi-Fi: a switch */
        { 0 }, /* Enlarged Album Art: a switch */
        { 0 }, /* Folder Album Cover: a switch */
        { 0 }, /* Large Library Mode: a switch */
        { "Playlist View: List", "Playlist View: Cover Flow" },
    };
    _Static_assert(sizeof names / sizeof *names == POD_COUNT, "a names entry per row");
    if (i == POD_CF_SORT) {
        char sort[48];
        tk_snprintf(sort, sizeof sort, "Cover Flow %s", coverflow_sort_name());
        widget_set_text_utf8(st.pod_label[i], sort);
        return;
    }
    int v = i == POD_ARTISTS       ? I(artist_type, 0) == 1
            : i == POD_KEYTONE     ? g_keytone_flag != 0
            : i == POD_CARMODE     ? g_carmode != 0
            : i == POD_COVERMODE   ? g_bigcovermode == 1
            : i == POD_FOLDERCOVER ? g_foldercover_flag != 0
            : pod_values[i]        ? *pod_values[i]
                                   : 0;
    const char *toggle = i == POD_CHARGE        ? "Charge Limit (" STR(CHARGE_STOP) "%)"
                         : i == POD_RESUME      ? "Resume on Connect"
                         : i == POD_VOL_GUARD   ? "Volume Guard"
                         : i == POD_BT_LINK     ? "Control Device Volume"
                         : i == POD_KEYTONE     ? "Key Tone"
                         : i == POD_CARMODE     ? "In-Vehicle Mode"
                         : i == POD_SMART_WIFI  ? "Smart Wi-Fi"
                         : i == POD_COVERMODE   ? "Enlarged Album Art"
                         : i == POD_FOLDERCOVER ? "Folder Album Cover"
                         : i == POD_BIG_LIB     ? "Large Library Mode"
                                                : 0;
    if (toggle)
        list_toggle(st.pod_label[i], toggle, v);
    else
        widget_set_text_utf8(st.pod_label[i], names[i][v]);
    /* Stock's rows, so stock's translations */
    if (i == POD_KEYTONE) widget_set_tr_text(st.pod_label[i], "system_keytone");
    if (i == POD_CARMODE) widget_set_tr_text(st.pod_label[i], "system_carmode");
    if (i == POD_COVERMODE) widget_set_tr_text(st.pod_label[i], "playset_covermode");
    if (i == POD_FOLDERCOVER) widget_set_tr_text(st.pod_label[i], "playset_foldercover");
}
/* Centre or tap toggles and saves. Charge limit takes effect on the next UI loop pass
 * (power_poll), Smart Wi-Fi within SMART_POLL_MS; Artists on the next load of an artist list, as
 * the artist page's switch does; Large Library Mode's letters at the next spin, its half copies at
 * Cover Flow's next idle pass; Standby limit at the next Standby; Coverflow Sort and Refresh
 * Coverflow at Coverflow's next open (coverflow.c), Refresh with a toast, as it changes nothing in
 * view. */
static int pod_click(void *ctx, void *event) {
    int i = (int)(long)ctx;
    pod_settings();
    if (i == POD_CF_SORT)
        coverflow_sort_step();
    else if (i == POD_CF_REFRESH) {
        coverflow_refresh_art();
        toast("Cover Flow rebuilds artwork next time");
    } else if (i == POD_ARTISTS) {
        int v = I(artist_type, 0) != 1;
        I(artist_type, 0) = v;
        write_int_config(v, "PLAYSET", "ARTISTTYPE");
    } else if (i == POD_KEYTONE) {
        g_keytone_flag = !g_keytone_flag;
        write_int_config(g_keytone_flag, "SYSSET", "KEYTONE");
        /* Click With Headphones, built just after this row on Controls, shows while it is on. */
        void *item = P(P(st.setting_label[9], W_PARENT), W_PARENT);
        widget_set_visible(item, g_keytone_flag != 0, 0);
        widget_layout(P(item, W_PARENT));
    } else if (i == POD_CARMODE) {
        g_carmode = !g_carmode;
        write_int_config(g_carmode, "SYSSET", "CARMODE");
        g_carmode_state = 0;         /* as stock's row (0x4d3530) */
    } else if (i == POD_COVERMODE) { /* these two as stock's rows (0x4c0714) */
        g_bigcovermode = g_bigcovermode != 1;
        write_int_config(g_bigcovermode, "PLAYSET", "COVERMODE");
    } else if (i == POD_FOLDERCOVER) {
        g_foldercover_flag = !g_foldercover_flag;
        write_int_config(g_foldercover_flag, "PLAYSET", "FOLDERCOVER");
    } else {
        int *value = pod_values[i];
        *value = (*value + 1) % pod_choices(i);
        write_int_config(*value, "Q2POD", pod_keys[i]);
        st.charge_at = 0;
        /* Control Device Volume: off, stock's gain back at once; on, bt_volume_poll's next pass
         * sends the Q2's level to the device. */
        if (i == POD_BT_LINK && mclGetOutputWay() == 1)
            headset_volume_reset(), device_set_volume(g_volume, 0);
    }
    pod_text(i);
    return 0;
}
void pod_rows(void *win, const char *view_name, int first, int n, const char *const *icons) {
    void *view = win ? widget_lookup(win, view_name, 1) : (void *)0;
    pod_settings();
    for (int i = 0; view && i < n; ++i) {
        st.pod_label[first + i] = list_row(view, icons[i], pod_click, (void *)(long)(first + i));
        pod_text(first + i);
    }
}

/* systemset_powermanager_page_init and playset_playset_page_init: stock builds its rows, then
 * these follow in the same widgets and styles (list_row), with the value in the label. */
static void boot_row(void *view);
int ringnav_powermanager(void *win, void *ctx) {
    static const char *const icons[] = { "usb_chargeswitch", "system_powermanager",
                                         "system_carmode", "wifiset_wifi" };
    int result = stock_power_trampoline(win, ctx);
    pod_rows(win, "scroll_view_powermanager", POD_STANDBY, 1,
             icons + 1); /* beside stock's Standby */
    pod_rows(win, "scroll_view_powermanager", POD_CHARGE, 1, icons);
    pod_rows(win, "scroll_view_powermanager", POD_SMART_WIFI, 1, icons + 3);
    pod_rows(win, "scroll_view_powermanager", POD_CARMODE, 1, icons + 2);
    boot_row(win ? widget_lookup(win, "scroll_view_powermanager", 1) : (void *)0);
    return result;
}
/* The Bluetooth page's list (stock's local builder, btmenu_rows), rebuilt whole on every search and
 * link change: its rows, five with Bluetooth on (one, the switch, off), then the device lists.
 * With Bluetooth on, Resume on connect and Control Device Volume follow the fourth, Bluetooth
 * Headset Volume Control (bt_adjvol), each time, iconless and 78 high as stock's rows. */
int ringnav_btrows(void *win, void *ctx) {
    static const char *const icons[] = { 0 };
    int result = stock_btrows_trampoline(win, ctx);
    if (!g_bluetoothflag) return result;
    pod_rows(win, "scroll_view_bluetooth", POD_RESUME, 1, icons);
    pod_rows(win, "scroll_view_bluetooth", POD_BT_LINK, 1, icons);
    void *view = win ? widget_lookup(win, "scroll_view_bluetooth", 1) : (void *)0;
    unsigned n = view ? widget_count_children(view) : 0;
    if (n < 2) return result;
    for (unsigned k = 0; k < 2; ++k) {
        void *row = widget_get_child(view, n - 2 + k);
        widget_resize(row, I(row, W_W), 78);
        widget_restack(row, 4 + k);
    }
    return result;
}
/* A settings list's rows, stock's (in its table's order, each button named by its place there,
 * which its click looks up, so order is free) and then ours, restacked into order; any other count
 * is left as built. */
static void reorder(void *view, const unsigned char *order, unsigned n) {
    void *rows[32];
    if (!view || n > sizeof rows / sizeof *rows || widget_count_children(view) != n) return;
    for (unsigned i = 0; i < n; i++) rows[i] = widget_get_child(view, i);
    for (unsigned i = 0; i < n; i++) widget_restack(rows[order[i]], i);
}

/* Everyday listening, what plays next, level, channels, then specialist output controls; the ring
 * puts Library & Folders one detent from the top. Stock's rows 0-10 (ringnav_audioset drops its
 * Volume and Album Display and Folder skip), then ours as built. */
static const unsigned char playset_order[] = {
    3 /* Play Mode */,
    2 /* EQ */,
    7 /* Gapless */,
    11 /* Crossfade */,
    6 /* Memory play */,
    12 /* Resume Songs */,
    8 /* Headphones required */,
    0 /* Gain */,
    9 /* ReplayGain */,
    16 /* Max Volume */,
    17 /* Default Vol */,
    10 /* Balance */,
    13 /* Mono Audio */,
    14 /* Swap L/R */,
    18 /* Enlarged art */,
    1 /* Filters */,
    4 /* DSD Output */,
    5 /* USB Audio */,
    15 /* Library & Folders */,
};
/* Library & Folders: stock's Folder skip (0), then ours as built. */
static const unsigned char library_order[] = {
    2 /* Artists */,     3 /* Artist album order */, 1 /* Year order */,
    4 /* Folder Root */, 0 /* Folder skip */,        5 /* Folder art */,
};
/* Connections, personalisation and daily controls, then device maintenance; About stays last.
 * Stock's Wheel Function, In-Vehicle, General and System stay hidden, its page left whole: the
 * wheel is Q2 Pod's (tools/build.py WHEEL_MODE), Power and Controls have In-Vehicle's, Key tone's
 * and Buttons lock's rows (pod_click, keylock_open), and the rows stock moved to System are here,
 * as they were (ringnav_systemset). */
static const unsigned char sysset_order[] = {
    1 /* Wireless */,
    2 /* Network */,
    3 /* Display */,
    10 /* Controls */,
    4 /* Power */,
    11 /* Date and time */,
    0 /* Language */,
    9 /* App Store */,
    12 /* Update */,
    13 /* Reset */,
    5 /* Wheel Function, hidden */,
    6 /* In-Vehicle, hidden */,
    7 /* General, hidden */,
    8 /* System, hidden */,
    14 /* About */,
};

/* Folder Root (Q2POD FOLDERROOT): the card's top-level folder Home's Folder opens in, "" the card
 * itself; name holds 256. toolsReadConfig leaves name alone for a missing key and empty default.
 * ponytail: names over ROOT_MAX bytes or with an edge space are not offered: stock's config.ini
 * line (toolsWriteConfig's 257-byte sprintf, toolsReadConfig's 255-byte fgets, then trimmed) can't
 * keep them. */
#define ROOT_MAX 200
static void root_read(char *name) {
    name[0] = 0;
    toolsReadConfig("/mnt/data/config.ini", "Q2POD", "FOLDERROOT", name, "");
}
/* The card's next top-level folder after name, in name order (case aside, as FAT and exFAT hold
 * no two names differing only so; no dot-folders): the first after "", "" (the card) after the
 * last. d_type 0: as media_find. */
static void root_next(char *name) {
    char next[256] = "";
    void *dir = opendir("/mnt/mmc");
    for (struct dirent *e; dir && (e = readdir(dir));) {
        const char *d = e->d_name;
        unsigned n = strlen(d);
        if ((e->d_type != 4 && e->d_type) || d[0] == '.' || d[0] == ' ' || d[n - 1] == ' ' ||
            n > ROOT_MAX)
            continue;
        if (strcasecmp(d, name) > 0 && (!next[0] || strcasecmp(d, next) < 0))
            memcpy(next, d, n + 1);
    }
    if (dir) closedir(dir);
    memcpy(name, next, 256);
}
/* The saved root's folder into path (265 bytes), 0 when there is none to open. */
static int root_dir(char *path) {
    char name[256];
    unsigned info[36];
    root_read(name);
    tk_snprintf(path, 265, "/mnt/mmc/%s", name);
    return name[0] && name[0] != '.' && !strrchr(name, '/') && !__xstat(3, path, info) &&
           (info[5] & 0170000) == 0040000;
}
static void root_text(const char *name) {
    char t[280];
    tk_snprintf(t, sizeof t, "Folder Root: %s", name[0] ? name : "Card");
    widget_set_text_utf8(st.root_label, t);
}
/* Centre or tap: the next root, saved; Folder opens there from its next visit. */
static int root_click(void *ctx, void *event) {
    char name[256];
    root_read(name);
    root_next(name);
    toolsWriteConfig("/mnt/data/config.ini", "Q2POD", "FOLDERROOT", name);
    root_text(name);
    return 0;
}

/* A row that opens the stock page ctx names, as stock's own rows do. */
static int to_page(void *ctx, void *event) {
    navigator_to((const char *)ctx);
    return 0;
}
/* Playback's Library & Folders opens stock's page again (as Display's sub-pages do) with only the
 * library rows: stock's Folder skip (12) kept, its others dropped. The main page drops it instead,
 * and stock's Volume and Album Display (11): that page's four rows are here as they were, Max
 * Volume, Default Volume and Enlarged Album Art on Playback and Folder Album Cover on Library &
 * Folders, opening stock's pages and flipping stock's switches (pod_click). Each page builds only
 * its own rows, so a row's kept label is its page's. */
static int playset_sub __attribute__((section(".scratch")));
static int library_open(void *ctx, void *event) {
    playset_sub = 1;
    navigator_to("playset/playset_page");
    playset_sub = 0;
    return 0;
}
int ringnav_audioset(void *win, void *ctx) {
    static const char *const icons[] = { "playset_covermode", "playset_folderjump" },
                             *const cover_icons[] = { "playset_covermode", "playset_foldercover" };
    int sub = playset_sub, result = stock_audioset_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_playset", 1) : (void *)0;
    if (!view) return result;
    for (int i = (int)widget_count_children(view); i-- > 0;)
        if (sub ? i != 12 : i >= 11) widget_destroy(widget_get_child(view, (unsigned)i));
    if (sub) {
        void *nav = widget_lookup(win, "view_navbar", 1),
             *title = nav ? widget_get_child(nav, 0) : (void *)0;
        if (title) widget_set_text_utf8(title, "Library & Folders");
        pod_rows(win, "scroll_view_playset", POD_YEAR_OLD, 2, icons);
        pod_rows(win, "scroll_view_playset", POD_ARTIST_AZ, 1, icons);
        char name[256];
        root_read(name);
        st.root_label = list_row(view, "playset_folderjump", root_click, 0);
        root_text(name);
        pod_rows(win, "scroll_view_playset", POD_FOLDERCOVER, 1, cover_icons + 1);
        reorder(view, library_order, sizeof library_order);
        return result;
    }
    xfade_row(list_row_into(view, "playset_gapless", xfade_open, (void *)0));  /* crossfade.c */
    resume_row(list_row_into(view, "playset_memplay", xfade_open, (void *)2)); /* crossfade.c */
    mono_row(list_row(view, "playset_balance", mono_click, 0));
    swap_row(list_row(view, "playset_balance", swap_click, 0));
    widget_set_text_utf8(list_row_into(view, "playset_folderjump", library_open, 0),
                         "Library & Folders");
    widget_set_tr_text(
        list_row_into(view, "playset_maxvol", to_page, (void *)"playset/maxvol_page"),
        "playset_maxvol");
    widget_set_tr_text(
        list_row_into(view, "playset_bootvol", to_page, (void *)"playset/bootvol_page"),
        "playset_bootvol");
    pod_rows(win, "scroll_view_playset", POD_COVERMODE, 1, cover_icons);
    reorder(view, playset_order, sizeof playset_order);
    return result;
}

/* Nearest collected ancestor of a tap: a clickable child selects the row that owns it. */
static int selects(menu_t *m, void *target) {
    for (int depth = 0; target && depth < 32; ++depth) {
        for (int i = 0; i < m->n; ++i)
            if (m->at[i] == target) return i;
        if (target == m->w) break;
        target = P(target, W_PARENT);
    }
    return -1;
}

/* Classic: a row the list cuts comes whole when it's pressed, as the wheel's does: a list under a
 * page stays as left, so it would still be cut on the way back. ponytail: not a table's, whose
 * setter rebinds the row under a click; a swipe with no press stays where it was put. */
static void whole_row(menu_t *m, int i) {
    rect_t r = bounds(m, i);
    if (m->kind == 1 && classic_pages() && (r.y < 0 || r.y + r.h > sel_height(m)))
        reveal(m, m->id[i], 1);
}

/* target's row takes the selection, painted: a tap's, or Theme's after its relayout. */
static void keep_selected(void *target) {
    void *other = (void *)0, *w = surface(target, &other);
    int i = w && load(&g_menu, w, 0) ? selects(&g_menu, target) : -1;
    if (i < 0) return;
    stop_scroll(&g_menu); /* an explicit tap replaces any pending recall glide */
    if (other) {
        prop(other, SEL, -1);
        widget_invalidate_force(other, (void *)0);
    }
    select(&g_menu, g_menu.id[i]);
    whole_row(&g_menu, i);
    widget_invalidate_force(w, (void *)0);
}

/* scroll_lrc's rows are 20 px, or 25 a line when wrapped (0x53b838), and its 178 px are no whole
 * number of them, so the view's end (content height - view height), where stock's follow and the
 * wheel stop, cut the top row. The content grows to the first row top at or past that end plus the
 * view: under a row of blank space below the last. A relayout (scroll_view's, 0x604638) puts the
 * height back and clamps the offset; the next follow, tick or glide step pads it again. ponytail:
 * the wheel and a drag stop anywhere, so a row can be cut at the top until the page follows. iPod's
 * lyrics page (lyrics.c) ends with its last line in the middle. */
/* The offset that centres row i of lrc in its view. */
static int lyric_centre(void *lrc, int i) {
    void *row = widget_get_child(lrc, i);
    return I(row, W_Y) + (I(row, W_H) - I(lrc, W_H)) / 2;
}
static void lyric_pad(void *lrc) {
    if (lyrics_on(lrc)) {
        I(lrc, VIEW_CONTENT_H) = lyric_centre(lrc, widget_count_children(lrc) - 1) + I(lrc, W_H);
        return;
    }
    int h = I(lrc, W_H), end = I(lrc, VIEW_CONTENT_H) - h, top = 1 << 30;
    for (int i = widget_count_children(lrc); i-- > 0;) {
        int t = I(widget_get_child(lrc, i), W_Y);
        if (t >= end && t < top) top = t;
    }
    if (end > 0 && top < 1 << 30) I(lrc, VIEW_CONTENT_H) = top + h;
}

static unsigned np_track(void);

/* The lyrics page in front: Now Playing on top, lit, not sliding and untouched, on the lyrics. */
static int lyrics_shown(void) {
    void *wm = window_manager();
    return st.np_lrc && st.np_slide && usable() && window_manager_get_top_window(wm) == st.np_win &&
           !window_manager_is_animating(wm) && !pointer_down() &&
           widget_get_prop_int(st.np_slide, "value", 0) == 1;
}

/* Stock's scroll animator on the lyrics stops, a drag's fling among them (stock
 * table_client_stop_animator_scroll's sequence, as stop_scroll). */
static void lyric_still(void *lrc) {
    void *a = P(lrc, VIEW_ANIMATOR);
    if (!a) return;
    widget_animator_pause(a);
    widget_animator_destroy(a);
    P(lrc, VIEW_ANIMATOR) = (void *)0;
}

/* Another song: its lyrics at the top, nothing held or gliding, and stock's animator, which would
 * carry them on to where the last song's follow sent them, stopped. */
static void lyric_top(void *lrc) {
    st.lyric_held = st.lyric_spun = st.lyric_glide = st.lyric_to = st.lyric_push = 0;
    lyric_still(lrc);
    scroll_view_set_offset(lrc, I(lrc, SCROLL_X), 0);
}

/* The lyrics glide to `to` (1/16 px) within their content; lyric_step moves them, every UI pass. */
static void lyric_glide(void *lrc, int to) {
    lyric_still(lrc);
    st.lyric_to = clamp_step(to, (I(lrc, VIEW_CONTENT_H) - I(lrc, W_H)) * 16, 0);
    if (!st.lyric_glide) st.lyric_at = (unsigned)time_now_ms();
    st.lyric_glide = 1;
}

/* The Now Playing timer's follow (LYRIC_FOLLOW in tools/build.py): stock scrolls to the row three
 * lines above the current one, which past the end clamps to it. iPod: lyrics the wheel or a finger
 * moved stay (lyric_held) until Now Playing goes or another song starts, and a finger on the
 * screen keeps them too; on the page, the follow glides. The lyrics page follows its own playing
 * line (lyric_follow). */
int ringnav_lyric_follow(void *lrc, int x, int y, int ms) {
    lyric_pad(lrc);
    if (lrc == st.np_lrc) {
        if (st.lyric_held || st.lyric_spun == 2 || lyrics_on(lrc) || pointer_down()) return 0;
        if (lyrics_shown()) {
            lyric_glide(lrc, y * 16);
            return 0;
        }
        st.lyric_glide = 0;
    }
    return scroll_view_scroll_to(lrc, x, y, ms);
}

/* The page's refresh rebuilds scroll_lrc's rows (0x53b67c, LYRIC_ROWS in tools/build.py) whenever
 * g_forcerefresh_flag is set, as Play/Pause (playpause_quick_click) and the page's return to the
 * front do, and the rebuild ends at offset 0: the lyrics flicked to the top until the next follow.
 * The offset stays; the follow moves it if the line did. iPod: rows for another song than the last
 * rows' start at the top (lyric_top), whatever moved the old ones since the song changed, and they
 * become the lyrics page's (lyrics_rows). */
int ringnav_lyric_rows(void *win) {
    void *lrc = widget_lookup(win, "scroll_lrc", 1);
    int y = lrc ? I(lrc, SCROLL_Y) : 0, ret = ((int (*)(void *))0x53b67c)(win);
    if (lrc && lrc == st.np_lrc && lyrics_rows(lrc)) lyric_pad(lrc);
    unsigned song = lrc && lrc == st.np_lrc ? np_track() : st.lyric_rows_song;
    if (song != st.lyric_rows_song) {
        st.lyric_rows_song = st.lyric_song = song;
        lyric_top(lrc);
        return ret;
    }
    if (y) scroll_view_set_offset(lrc, I(lrc, SCROLL_X), y);
    return ret;
}

/* The playing track: its queue position and path. */
static unsigned np_track(void) {
    unsigned at, n;
    void *r = queue_now(&at, &n);
    return r ? fnv(hash_bytes(FNV_SEED, (const unsigned char *)&at, sizeof at), P(r, REC_PATH)) : 0;
}

/* Stock's seek resumes playback; a paused seek must wait for Play instead. */
static int seek_position(int to) {
    unsigned at, n;
    void *r = mclGetPlayStatus() == 3 ? queue_now(&at, &n) : 0;
    if (r) {
        int sec = -1, total;
        player_playtime_and_length(&sec, &total);
        mclSetSeek(to + I(r, REC_CUE_START));
        return sec + 1;
    }
    player_seek_time(to);
    return 0;
}

/* The status bar's centred label follows stock's 24-hour setting (18:14 or 6:14 PM), without
 * seconds, or --:-- when the time cannot be read. clock_show reads the time once a minute, from a
 * timer due at the next minute, writes the label when the minute shown changes and applies Auto
 * Light Mode's and Night Shift's hour; nothing reads the time in between. It runs at once for a new
 * zone (ringnav_timezone), a time set by hand (time_click) and a wake (ringnav_sleep), and on the
 * pass after the bar first paints or paints with a new 24-hour setting (ringnav_paint_bg).
 * ponytail: a network time sync while lit shows at the next minute. */
static void clock_show(void);
int clock_due(const void *info) {
    (void)info;
    st.clock_timer = 0;
    clock_show();
    return 0;
}
static void clock_show(void) {
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
    void *bar = *(void *const *)system_bar;
    void *label = key == st.clock_key || !bar || st.bar_titled
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
                    tm[2] < 12 ? "AM" : "PM");
    widget_set_text_utf8(label, s);
}
static void clock_zone(void) {
    if (st.clock_timer) clock_show(); /* the bar runs; boot's call comes before it */
}

/* systembar_showface sets img_bt's image each second: a codec badge (BT_CODECS) while stock shows
 * the codec, else bar_bt or BT_GLYPH. A new badge shows CODEC_MS, then codec_fade fades it out and
 * BT_GLYPH in, and bar_sync keeps putting BT_GLYPH back over stock's badge before img_bt paints. */
static const char *const codecs[] = BT_CODECS;
static const int codec_reach[] = BT_CODEC_REACH;
_Static_assert(sizeof codecs / sizeof *codecs == sizeof codec_reach / sizeof *codec_reach,
               "one reach per codec");

static int codec_fade(const void *info) {
    int n = ++st.codec_step, d = CODEC_STEPS - n;
    if (n == 1)
        st.codec_timer = timer_add(codec_fade, (void *)0, CODEC_STEP_MS); /* this one ends */
    if (n == CODEC_STEPS) image_base_set_image(st.bar_bt, BT_GLYPH);
    widget_set_opacity(st.bar_bt, 255u * (unsigned)(d < 0 ? -d : d) / CODEC_STEPS);
    if (n < 2 * CODEC_STEPS) return n == 1 ? 0 : 8; /* RET_REPEAT */
    st.codec_timer = 0;
    return 0;
}

/* The percentage's width at BATT_PX: digits are tabular; BATT_PCT_W until the first paint measures.
 */
static int pct_width(unsigned level) {
    if (!st.pct_digit) return BATT_PCT_W;
    return (level >= 100 ? 3 : level >= 10 ? 2 : 1) * st.pct_digit + st.pct_sign;
}

/* What the playing source reports: 2 playing, 3 paused. */
int bar_status(void) {
    if (media_owner() == 1) return mclGetPlayStatus();
    return video_status() ?: radio_status() ?: spot_status();
}
/* img_state follows whichever player plays, stock's or Spotify's, radio's or a video's, and
 * label_idle shows when neither it nor label_eq does. Returns whether a state image changed. */
static int bar_state_sync(void *bar) {
    int changed = 0;
    if (!st.bar_bt && bar) {
        st.bar_bt = widget_lookup(bar, "img_bt", 1);
        st.bar_wifi = widget_lookup(bar, "img_wifi", 1);
        st.bar_pct = widget_lookup(bar, "label_battery", 1);
        st.bar_slot = widget_lookup(bar, "view_battery", 1);
        st.bar_icon = widget_lookup(bar, "img_battery", 1);
        st.bar_idle = widget_lookup(bar, "label_idle", 1);
        st.bar_state = widget_lookup(bar, "img_state", 1);
        st.bar_eq = widget_lookup(bar, "label_eq", 1);
        st.bar_title = widget_lookup(bar, "label_title", 1);
    }
    int on = 0;
    if (st.bar_state) {
        int status = bar_status();
        on = status == 2 || status == 3;
        if (on != !!widget_get_visible(st.bar_state)) {
            widget_set_visible(st.bar_state, on, 0);
            changed = 1;
        }
        if (on) {
            /* BATT_CLASSIC_LIGHT's play state is its own art (tools/art.py), by state and half */
            static const char *const classic_light_icon[2][2] = {
                { "classic_light_play_d", "classic_light_pause_d" },
                { "classic_light_play_l", "classic_light_pause_l" }
            };
            const char *icon = st.bar_dark
                                   ? status == 2 ? "classic_light_play_w" : "classic_light_pause_w"
                               : bar_theme()->painter[PAINT_BATTERY] == BATT_CLASSIC_LIGHT
                                   ? classic_light_icon[ipod_light()][status != 2]
                               : status == 2 ? "bar_play"
                                             : "bar_pause";
            if (tk_strcmp(widget_get_prop_str(st.bar_state, "image", ""), icon)) {
                image_base_set_image(st.bar_state, icon);
                changed = 1;
            }
        }
    }
    if (st.bar_idle && st.bar_state && st.bar_eq)
        widget_set_visible(st.bar_idle,
                           !widget_get_visible(st.bar_state) && !widget_get_visible(st.bar_eq), 0);
    return changed;
}

/* An invalidate made while another window paints is dropped with that frame's dirty rects, and
 * img_state keeps its dirty flag, so stock's own invalidates skip it until something else repaints
 * the bar: Now Playing, painting every 250 ms, sees a Play/Pause first and left a stale icon
 * (#116). Repaint the bar once, after the frame. */
int bar_repaint(const void *info) {
    (void)info;
    st.bar_timer = 0;
    widget_invalidate_force(*(void *const *)system_bar, (void *)0);
    return 0;
}

/* The status bar's codec badge, then the Battery setting (docs/ipod.md#status-bar-and-clock):
 * view_battery paints the charge-level icon, the percentage, both, or the percentage inside, sized
 * to its ink so every gap is STATUS_GAP, and the icon wherever the group's ink would reach more
 * than BATT_ROOM (a wide badge while it shows). widget_set_visible does nothing for an unchanged
 * state and relayouts the view for a new one. */
static int slider_page(void *top);
static int time_form(void *top);
/* The pages under Classic's dark bar. */
static int dark_bar(void *top) {
    return slider_page(top) || time_form(top) ||
           (top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "usbmode_page"));
}
/* The real bar's title's row, set for bar_px, so a dark bar's title sits on the same rows. */
int dark_title_y(void) { return st.bar_title ? I(st.bar_title, W_Y) : 0; }
/* Their title at the bar's size, in the face its text has (none: the system's). */
static void dark_title(void *t) {
    const unsigned *s = widget_get_text(t);
    int px = theme()->font_px[ROLE_BAR];
    const char *face = theme_face(s, (int)wide_len(s), &px);
    slider_label(t, 0, dark_title_y(), 375, 25, px, face ? face : "default");
}
/* w at x, y on the screen, unless it's there. */
void bar_place(void *w, int x, int y, int ww, int h) {
    void *p = P(w, W_PARENT);
    x -= I(p, W_X), y -= I(p, W_Y);
    if (I(w, W_X) != x || I(w, W_Y) != y || I(w, W_W) != ww || I(w, W_H) != h)
        widget_move_resize(w, x, y, ww, h);
}
/* w left of the screen, where a relayout finds it. */
static void bar_off(void *w) {
    if (I(w, W_X) != -400) widget_move_resize(w, -400, I(w, W_Y), I(w, W_W), I(w, W_H));
}
/* On reaching a dark page or leaving one: its title across the bar in white in either half and the
 * clock gone, and back as its style and box were. bar_sync places the rest. */
static void bar_dark_sync(void *bar) {
    void *t = st.bar_title;
    if (!t || !st.bar_slot || !st.bar_state || !classic_pages()) return;
    int dark = dark_bar(window_manager_get_top_window(window_manager()));
    if (dark == st.bar_dark) return;
    void *clock = widget_lookup(bar, "label_clock", 1), *style = P(t, W_ASTYLE);
    st.bar_dark = (unsigned char)dark;
    if (clock) widget_set_visible(clock, !dark, 0);
    if (dark) {
        st.title_x = (short)I(t, W_X), st.title_y = (short)I(t, W_Y), st.title_w = (short)I(t, W_W);
        st.title_align = (unsigned char)style_get_int(style, "text_align_h", ALIGN_H_LEFT);
        st.title_color = style_get_int(style, "text_color", (int)RGBA(0xffffff));
        dark_title(t);
    } else {
        widget_move_resize(t, st.title_x, st.title_y, st.title_w, I(t, W_H));
        widget_set_prop_int(t, "style:normal:text_align_h", st.title_align);
        widget_set_prop_int(t, "style:normal:text_color", st.title_color);
        widget_set_prop_int(t, "_final_n", 0);
        widget_set_prop_int(t, "_font", -1);
        theme_font(t, ROLE_BAR, FONT_ROW);
        widget_layout_children(P(st.bar_slot, W_PARENT));
        st.clock_key = 0;
    }
    /* this paint may have drawn the bar already, and an invalidate mid-paint is lost */
    if (!st.bar_timer) st.bar_timer = timer_add(bar_repaint, (void *)0, 0);
}
/* The battery view_battery shows: the level from label_battery's "88%" (stock zeroes
 * progress_battery while charging), then under Classic full on power (get_battery_capacity's
 * status 2 and stock's cable flag, memory both, as stock shows the plain icon then), charging
 * (bar_charge) and low (bar_lowcharge), as stock picks the icon. Reads stock's widgets, no I/O. */
static unsigned battery_now(int classic_light) {
    const char *icon = widget_get_prop_str(st.bar_icon, "image", "");
    const unsigned *t = widget_get_text(st.bar_pct);
    int level = 0;
    while (t && *t >= '0' && *t <= '9') level = level * 10 + (int)(*t++ - '0');
    return (unsigned)(level > 100 ? 100 : level) << 3 |
           (unsigned)(classic_light && I(g_power_chargestate, 0) == 2 && g_usbdet_value) << 2 |
           (unsigned)(icon && !tk_strcmp(icon, "bar_charge")) << 1 |
           (unsigned)(icon && !tk_strcmp(icon, "bar_lowcharge"));
}
static void bar_sync(void *bar) {
    bar_dark_sync(bar);
    if (bar_state_sync(bar) && !st.bar_timer) st.bar_timer = timer_add(bar_repaint, (void *)0, 0);
    if (!st.bar_bt || !st.bar_wifi || !st.bar_pct || !st.bar_slot || !st.bar_icon) return;
    int shown = widget_get_visible(st.bar_bt), c = 0;
    const char *image = shown ? widget_get_prop_str(st.bar_bt, "image", "") : "";
    for (int i = 0; image && i < (int)(sizeof codecs / sizeof *codecs); ++i)
        if (!tk_strcmp(image, codecs[i])) c = i + 1;
    /* A new badge starts over; BT_GLYPH with a badge is the faded badge itself. */
    if (c != st.codec && (c || !image || tk_strcmp(image, BT_GLYPH))) {
        stop_timer(&st.codec_timer);
        st.codec = c;
        st.codec_step = 0;
        widget_set_opacity(st.bar_bt, 255);
        if (c) st.codec_timer = timer_add(codec_fade, (void *)0, CODEC_MS);
    } else if (c && st.codec_step >= CODEC_STEPS)
        image_base_set_image(st.bar_bt, BT_GLYPH);
    accent(); /* reads the settings */
    if (st.home_rockbox != rockbox_available() ||
        (shortcut() == SHORTCUT_GROOVEBOX && st.home_groovebox != groovebox_available()))
        coverflow_home_layout();
    /* Classic's battery: the set's body, which Percent Inside keeps */
    const theme_t *T = theme(), *B = bar_theme();
    /* The look's rows off the row layout's, y 0 in every set's row. A relayout puts them back
     * there; this pass corrects them before the frame draws them. */
    if (I(st.bar_bt, W_Y) != B->bar_bt_dy)
        widget_move_resize(st.bar_bt, I(st.bar_bt, W_X), B->bar_bt_dy, I(st.bar_bt, W_W),
                           I(st.bar_bt, W_H));
    if (I(st.bar_wifi, W_Y) != B->bar_wifi_dy)
        widget_move_resize(st.bar_wifi, I(st.bar_wifi, W_X), B->bar_wifi_dy, I(st.bar_wifi, W_W),
                           I(st.bar_wifi, W_H));
    int classic_row = T->painter[PAINT_BATTERY] == BATT_CLASSIC_LIGHT, /* the row's layout */
        classic_light = B->painter[PAINT_BATTERY] == BATT_CLASSIC_LIGHT,
        body =
            classic_light ? bar_geom_val(V_BATT_W) + 1 : BATT_BODY_W, /* its soft edge's column */
        inside = classic_light ? body : BATT_INSIDE_W;
    int mode = st.battery;
    if (mode) {
        int reach = mode == 1   ? BATT_PCT_W
                    : mode == 3 ? inside + BATT_NUB_W
                                : body + BATT_NUB_W + BATT_GAP + BATT_PCT_W;
        if (widget_get_visible(st.bar_wifi)) reach += I(st.bar_wifi, W_W) + STATUS_GAP;
        if (shown)
            reach +=
                STATUS_GAP +
                (st.codec && st.codec_step < CODEC_STEPS ? codec_reach[st.codec - 1] : BT_REACH);
        /* Classic: the play state shares the group */
        if (classic_row && st.bar_state && widget_get_visible(st.bar_state))
            reach += I(st.bar_state, W_W) + STATUS_GAP;
        /* Classic's Percent Inside is the icon's own width: the icon would win no room */
        if (reach > BATT_ROOM && !(classic_light && mode == 3)) mode = 0;
    }
    widget_set_visible(st.bar_icon, 0, 0);
    widget_set_visible(st.bar_pct, 0, 0);
    widget_set_visible(st.bar_slot, 1, 0);

    unsigned key = battery_now(classic_light);
    if (st.bar_dark) { /* each paint: a relayout puts them back */
        bar_off(st.bar_bt);
        bar_off(st.bar_wifi);
        /* draw_type center, in the box it keeps: the 10 x 11 glyph lands at 278, 7 */
        bar_place(st.bar_state, 278 - (I(st.bar_state, W_W) - 10) / 2,
                  7 - (I(st.bar_state, W_H) - 11) / 2, I(st.bar_state, W_W), I(st.bar_state, W_H));
        bar_place(st.bar_slot, 285, 1, 37, 21);
        if (key != st.batt_key && !st.bar_timer)
            st.bar_timer = timer_add(bar_repaint, (void *)0, 0);
        st.batt_key = key;
        return;
    }
    int pct = pct_width(key >> 3);
    int width = mode == 1
                    ? pct
                    : (mode == 3 ? inside : body) + BATT_NUB_W + (mode == 2 ? BATT_GAP + pct : 0);
    int resized = I(st.bar_slot, W_W) != width;
    widget_resize(st.bar_slot, width, geom_val(V_BAR));
    /* Resize only relayouts the slot's own children; the right-aligned row must
     * position it again even when Icon + Percent -> Icon keeps it visible. */
    if (resized) widget_layout_children(P(st.bar_slot, W_PARENT));
    if (key == st.batt_key && mode == st.batt_mode && !resized) return;
    st.batt_mode = mode;
    st.batt_key = key;
    /* bar_sync runs mid-paint: an invalidate now is lost, and this frame painted the slot only
     * where stock dirtied the bar (label_battery's new digit), so charging's colour reached one
     * digit until a wake. The whole bar again after this frame. */
    if (!st.bar_timer) st.bar_timer = timer_add(bar_repaint, (void *)0, 0);
}

/* systembar_showface, each second: stock hides img_state whenever its own player is stopped, so
 * with Spotify, radio or a video playing it went off here and back on at the next paint, two
 * relayouts a second and a frame with EQ drawn in the play icon's place. Correct it before paint,
 * dark or not, so a wake never shows stock's hidden icon; the badge waits for paint. Stock writes
 * the battery into label_battery and img_battery, which bar_sync hides, so their writes repaint
 * nothing and a new level showed only when something else painted the bar: a new level or charge
 * repaints the bar here, outside a paint, while lit (a wake paints it all). */
int ringnav_showface(void *bar) {
    int result = stock_showface_trampoline(bar);
    bar_state_sync(bar); /* no I/O, and a relayout waits for a paint */
    if (st.bar_pct && st.bar_icon && g_backlight_status) {
        unsigned key = battery_now(bar_theme()->painter[PAINT_BATTERY] == BATT_CLASSIC_LIGHT);
        if (key != st.batt_key) {
            st.batt_key = key; /* what this repaint shows: bar_sync has nothing more to repaint */
            widget_invalidate_force(bar, (void *)0);
        }
    }
    return result;
}

void bar_rect(void *canvas, unsigned color, int x, int y, int w, int h) {
    stock_canvas_set_fill_color(canvas, color);
    canvas_fill_rect(canvas, x, y, w, h);
}
/* Classic's battery (BATT_CLASSIC_LIGHT), the body's top-left at x, y, the set's V_BATT_W x
 * V_BATT_H with the nub outside: the batt_line outline, its corners half covered, batt_edge along
 * the bottom and a hollow nub, the batt_empty rows inside, over them the level in the batt stops (a
 * batt_low sliver when low, full while charging), the last column shaded and, outside Percent
 * Inside, the classic_light_bolt image charging or classic_light_plug full on power. In the light
 * half a soft edge outside the body and an emboss under it. Final colours, never flipped; the
 * caller restores the fill. Returns the level's width. */
static int classic_light_battery(void *w, void *canvas, int x, int y, unsigned key) {
    const theme_t *T = bar_theme();
    unsigned bitmap[64]; /* bitmap_t: width @0, height @4 */
    int L = ipod_light(), bw = bar_geom_val(V_BATT_W), bh = bar_geom_val(V_BATT_H), nh = BATT_NUB_H,
        iw = bw - 2, ih = bh - 2, r = x + bw - 1, ny = y + (bh - nh) / 2,
        f = key & 1 ? T->batt_low_w : iw * (int)(key & 2 ? 100 : key >> 3) / 100;
    unsigned line = RGBA(T->batt_line[L]);
    if (L) {
        bar_rect(canvas, (unsigned)T->batt_soft_a << 24, x - 1, y, 1, bh);
        bar_rect(canvas, (unsigned)T->batt_soft_top_a << 24, x, y - 1, bw, 1);
    }
    bar_rect(canvas, line, x + 1, y, iw, 1);
    canvas_fill_rect(canvas, x, y + 1, 1, ih);
    canvas_fill_rect(canvas, r, y + 1, 1, ih);
    canvas_fill_rect(canvas, x + bw, ny, BATT_NUB_W - 1, 1);
    canvas_fill_rect(canvas, x + bw, ny + nh - 1, BATT_NUB_W - 1, 1);
    canvas_fill_rect(canvas, x + bw + BATT_NUB_W - 1, ny + 1, 1, nh - 2);
    stock_canvas_set_fill_color(canvas, (line & 0xffffff) | (unsigned)T->batt_corner_a << 24);
    canvas_fill_rect(canvas, x, y, 1, 1);
    canvas_fill_rect(canvas, r, y, 1, 1);
    canvas_fill_rect(canvas, x + bw + BATT_NUB_W - 1, ny, 1, 1);
    canvas_fill_rect(canvas, x + bw + BATT_NUB_W - 1, ny + nh - 1, 1, 1);
    stock_canvas_set_fill_color(canvas, (line & 0xffffff) | (unsigned)T->batt_foot_a << 24);
    canvas_fill_rect(canvas, x, y + bh - 1, 1, 1);
    canvas_fill_rect(canvas, r, y + bh - 1, 1, 1);
    bar_rect(canvas, RGBA(T->batt_edge), x + 1, y + bh - 1, iw, 1);
    paint_stops(canvas, x + 1, y + 1, iw, ih, &T->batt_empty);
    if (key & 1) /* low */
        bar_rect(canvas, RGBA(T->batt_low), x + 1, y + 1, f, ih);
    else
        paint_stops(canvas, x + 1, y + 1, f, ih, &T->batt);
    bar_rect(canvas, (unsigned)T->batt_shade_a << 24, r - 1, y + 1, 1, ih);
    if (L) {
        bar_rect(canvas, 0xffffffu | (unsigned)T->batt_emboss_a << 24, x, y + bh + 1, bw - 1, 1);
        bar_rect(canvas, 0xffffffu | (unsigned)T->batt_emboss_nub_a << 24, x + bw, ny + nh,
                 BATT_NUB_W, 2);
    }
    if (key & 6 && st.batt_mode != 3 &&
        !widget_load_image(w, key & 2 ? "classic_light_bolt" : "classic_light_plug", bitmap)) {
        rect_t src = { 0, 0, (int)bitmap[0], (int)bitmap[1] },
               dst = { x + 1, y + 1, (int)bitmap[0], (int)bitmap[1] };
        canvas_draw_image(canvas, bitmap, &src, &dst);
    }
    return f;
}
/* The part of the digits in r that falls in columns x to x + w, in color (draw_centred's, which
 * Light Mode flips). */
static void classic_light_digits(void *canvas, const unsigned *s, unsigned n, const rect_t *r,
                                 int x, int w, unsigned color) {
    int old[4], clip[4];
    if (!clip_within(canvas, old, clip, I(canvas, CANVAS_X) + x, I(canvas, CANVAS_Y) + r->y, w,
                     r->h))
        return;
    canvas_set_clip_rect(canvas, clip);
    draw_centred(canvas, s, n, r, (unsigned)r->h, color);
    canvas_set_clip_rect(canvas, old);
}

/* The dark bar's battery, 26 x 13 at 294, 7 and its nub, in its slot at 285, 1, which is at x, y:
 * the level from the left, full while charging. Final colours; the caller restores the fill. */
static void dark_battery(void *canvas, int x, int y, unsigned key) {
    static const stops_t level = { 6,
                                   { 0, 20, 42, 62, 80, 100 },
                                   { 0xe2e2e2, 0xd7d7d7, 0xd3d3d3, 0xd4d4d4, 0xd7d7d7, 0xdedede } };
    bar_rect(canvas, RGBA(0xf5f5f5), x + 9, y + 6, 26, 13);
    bar_rect(canvas, RGBA(0x050505), x + 10, y + 7, 24, 11);
    bar_rect(canvas, RGBA(0xf5f5f5), x + 35, y + 10, 2, 5);
    paint_stops(canvas, x + 11, y + 8, 22 * (int)(key & 2 ? 100 : key >> 3) / 100, 9, &level);
}
/* A battery body bh rows high, either look's: its top in the slot w. Centred on the row's height,
 * as the icons beside it are, not on w's, which is the bar's (Classic's row is shorter than its
 * bar). Beside the percentage it rises BATT_PCT_DY, to the digits' ink. */
static int batt_top(void *w, int bh) {
    return (I(P(w, W_PARENT), W_H) + 1 - bh) / 2 - BATT_PCT_DY * (st.batt_mode == 2);
}
/* Mode 1 is the percentage alone; mode 3 uses a compact solid battery with dark digits; mode 2
 * puts the percentage beside the charge-level icon. Under Classic the battery is
 * classic_light_battery's, its digits in the bar's size. Restore canvas state. */
static void paint_battery(void *w, void *canvas) {
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd) return;
    const theme_t *T = bar_theme();
    int classic_light = T->painter[PAINT_BATTERY] == BATT_CLASSIC_LIGHT,
        bar_px = theme()->font_px[ROLE_BAR], /* the row's, as the room bar_sync gave it */
        px = classic_light && bar_px ? bar_px : BATT_PX;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    unsigned key = st.batt_key, level = key >> 3, s[4], n = put_num(s, level);
    int inside = st.batt_mode == 3;
    if (!inside) s[n++] = '%';
    unsigned color = RGBA(key & 2 ? BATT_CHARGE_RGB : key & 1 ? BATT_LOW_RGB : 0xffffff);
    if (st.bar_dark) {
        dark_battery(canvas, 0, 0, key);
        stock_canvas_set_fill_color(canvas, fill);
        return;
    }
    if (!inside && !st.pct_digit) {
        unsigned c = '0';
        canvas_set_font(canvas, theme_face(&c, 1, 0), px); /* draw_centred's face */
        st.pct_digit = (int)(canvas_measure_text(canvas, &c, 1) + 0.5f);
        c = '%';
        st.pct_sign = (int)(canvas_measure_text(canvas, &c, 1) + 0.5f);
    }
    if (st.batt_mode == 1) {
        rect_t r = { 0, 0, I(w, W_W), I(w, W_H) };
        draw_centred(canvas, s, n, &r, px, color);
        return;
    }
    if (classic_light) { /* the body's soft edge at 0; on Classic's own row batt_top is the middle
                            of the bar's rows above its two lines (tools/ipod.py sizes the row) */
        int bw = bar_geom_val(V_BATT_W), bh = bar_geom_val(V_BATT_H), y = batt_top(w, bh);
        int f = classic_light_battery(w, canvas, 1, y, key);
        if (st.batt_mode == 2) {
            int x = 1 + bw + BATT_NUB_W + BATT_GAP;
            rect_t r = { x, 0, I(w, W_W) - x, I(w, W_H) };
            draw_centred(canvas, s, n, &r, px, color);
        } else if (inside) {
            /* dark on the level, whatever its colour; past it white, on the dark rows. White flips
             * to the light half's black and black to its white, so that half passes the other. */
            rect_t r = { 2, y + 1, bw - 2, bh - 2 };
            classic_light_digits(canvas, s, n, &r, r.x, f, RGBA(ipod_light() ? 0xffffffu : 0));
            classic_light_digits(canvas, s, n, &r, r.x + f, r.w - f,
                                 RGBA(ipod_light() ? 0 : 0xffffffu));
        }
        stock_canvas_set_fill_color(canvas, fill);
        return;
    }
    const int bw = inside ? BATT_INSIDE_W : BATT_BODY_W, bh = inside ? BATT_INSIDE_H : BATT_BODY_H,
              nh = BATT_NUB_H, y = batt_top(w, bh);
    canvas_set_fill_color(canvas, color);
    if (inside) {
        rect_t body = { 0, y, bw, bh };
        fill_box(canvas, &body, color, 3);
        canvas_set_fill_color(canvas, color);
        canvas_fill_rect(canvas, bw, y + (bh - nh) / 2, BATT_NUB_W, nh);
        /* Digits have no descenders: lower their ink within the font's line box. */
        rect_t r = { 2, y + BATT_INSIDE_TEXT_DY, bw - 4, bh };
        draw_centred(canvas, s, n, &r, BATT_INSIDE_PX, RGBA(0));
        stock_canvas_set_fill_color(canvas, fill);
        return;
    }
    canvas_fill_rect(canvas, 1, y, bw - 2, 1);
    canvas_fill_rect(canvas, 1, y + bh - 1, bw - 2, 1);
    canvas_fill_rect(canvas, 0, y + 1, 1, bh - 2);
    canvas_fill_rect(canvas, bw - 1, y + 1, 1, bh - 2);
    canvas_fill_rect(canvas, bw, y + (bh - nh) / 2, BATT_NUB_W, nh);
    canvas_fill_rect(canvas, 2, y + 2, (bw - 4) * (key & 2 ? 100 : level) / 100, bh - 4);
    if (st.batt_mode == 2) {
        int x = bw + BATT_NUB_W + BATT_GAP;
        rect_t r = { x, 0, I(w, W_W) - x, I(w, W_H) };
        draw_centred(canvas, s, n, &r, BATT_PX, color);
    }
    stock_canvas_set_fill_color(canvas, fill);
}

/* Seconds as stock writes label_playtime, after a minus when negative is 1. */
void clock_text(void *label, int negative, int t) {
    char s[16] = "-";
    toolsTimeItoa(s + negative, t);
    widget_set_text_utf8(label, s);
}

void label_font(void *w, int bold) {
    static const char *const font[] = { "style:normal:font_name", "style:focused:font_name",
                                        "style:disable:font_name" };
    for (unsigned i = 0; i < sizeof font / sizeof *font; ++i)
        widget_set_prop_str(w, font[i], bold ? "bold" : "default");
}
static const unsigned short bold_ranges[] = BOLD_RANGES,
                            classic_light_ranges[] = CLASSIC_LIGHT_RANGES;

/* 1 iff every character of c (n < 0: up to the NUL; NULL has none) is in face's cmap. */
static int face_has(int face, const unsigned *c, int n) {
    const unsigned short *r = face == FONT_CLASSIC_LIGHT ? classic_light_ranges : bold_ranges;
    unsigned k = face == FONT_CLASSIC_LIGHT
                     ? sizeof classic_light_ranges / sizeof *classic_light_ranges
                     : sizeof bold_ranges / sizeof *bold_ranges;
    if (face == FONT_DEFAULT) return 1;
    if (!c) return 0;
    for (; n < 0 ? *c : n; ++c, --n) {
        unsigned i = 0;
        while (i < k && (*c < r[i] || *c > r[i + 1])) i += 2;
        if (i >= k) return 0;
    }
    return 1;
}

/* The theme's font for role on w: its face (bold.ttf, classic-light.ttf: tools/build.py) when that
 * face has every character of w's text (BOLD_RANGES, CLASSIC_LIGHT_RANGES), else the next bold one
 * that does (classic-light, then bold, at FACE_FALLBACK_PCT of the px), else stock's, which has
 * CJK; and its px, if the theme sets one. Written in every state the labels style, only when the
 * answer (face | px << 2) differs from was; returns it. A code-built row passes FONT_ROW: the
 * answer lives in its own "_font" prop with the role above it (row_faces reads it), and a role the
 * theme leaves to the style writes nothing. */
static const char *const font_faces[] = { "default", "bold", "classic-light" }; /* [FONT_*] */
/* bold.ttf standing in for classic-light.ttf: that much of the px gives the same capitals, their
 * cap heights' ratio (test_ipod). Only the fallback takes it: Classic's bold stays at its px. */
#define FACE_FALLBACK_PCT 83
static int fallback_px(int px) { return (px * FACE_FALLBACK_PCT + 50) / 100; }
int theme_font(void *w, int role, int was) {
    static const char *const names[] = { "style:normal:font_name", "style:focused:font_name",
                                         "style:disable:font_name" },
                             *const sizes[] = { "style:normal:font_size", "style:focused:font_size",
                                                "style:disable:font_size" };
    const theme_t *T = theme();
    int face = w ? T->font[role] : FONT_DEFAULT, px = T->font_px[role], row = was == FONT_ROW;
    if (row) {
        if (!face && !px) return 0;
        was = widget_get_prop_int(w, "_font", -1);
    }
    const unsigned *text = face ? widget_get_text(w) : (void *)0, none[1] = { 0 };
    if (face && !text) text = none; /* no text yet: keeps the face, as bold_label did */
    int fell = face == FONT_CLASSIC_LIGHT && !face_has(FONT_CLASSIC_LIGHT, text, -1);
    if (fell) face = FONT_BOLD; /* Cyrillic: still bold */
    if (face == FONT_BOLD && !face_has(FONT_BOLD, text, -1))
        face = FONT_DEFAULT; /* CJK */
    else if (fell)
        px = fallback_px(px);
    int answer = face | px << 2, kept = row ? answer | role << 16 : answer;
    if (!w || kept == was) return answer;
    for (unsigned i = 0; i < sizeof names / sizeof *names; ++i) {
        widget_set_prop_str(w, names[i], font_faces[face]);
        if (px) widget_set_prop_int(w, sizes[i], px);
    }
    if (row) widget_set_prop_int(w, "_font", kept);
    return answer;
}

/* Each label under w that theme_font gave a face finds it again for its text, in its own role. */
static void row_faces(void *w) {
    int kept = widget_get_prop_int(w, "_font", -1);
    if (kept >= 0) theme_font(w, kept >> 16, FONT_ROW);
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i)
        row_faces(widget_get_child(w, i));
}

/* A virtual table's bind of a pooled row (table_client's load loop, TABLE_LOAD_CALL): stock's
 * on_load_data writes the row's text with no relayout, so the faces its layout picked would stay
 * with the last text: a CJK name in Classic's face, which has none of it, drew blank (#217). */
int ringnav_table_load(void *ctx, unsigned index, void *row) {
    int ret =
        ((int (*)(void *, unsigned, void *))P(P(row, W_PARENT), TABLE_LOAD_DATA))(ctx, index, row);
    row_faces(row);
    return ret;
}

/* Canvas text in the theme's face where it has the characters, else bold.ttf where it has them
 * (still bold), else the system font (draw_centred, lyrics.c). A theme without a face of its own
 * draws the system font, as before. */
const char *theme_face(const unsigned *s, int n, int *px) {
    if (theme()->font[ROLE_TITLE] != FONT_CLASSIC_LIGHT) return (const char *)0;
    if (face_has(FONT_CLASSIC_LIGHT, s, n)) return "classic-light";
    if (!face_has(FONT_BOLD, s, n)) return (const char *)0;
    if (px) *px = fallback_px(*px); /* bold here is always the fallback */
    return "bold";
}

/* Paused, the seek waits for Play (scrub_end) and the player's second stands still until then,
 * and the page's timer would put the bar back on it. The bar keeps the
 * target until the player reports it, the track changes or stops, or SETTLE_PLAY_MS of playing
 * pass without it; then the page's timer comes back. */
#define SETTLE_MS 250
#define SETTLE_PLAY_MS 2000
static int settle_poll(const void *info) {
    int sec = -1, total, status = mclGetPlayStatus();
    player_playtime_and_length(&sec, &total);
    if (status == 2) st.settle_played += SETTLE_MS;
    if (st.np_win && np_track() == st.scrub_track &&
        (sec < st.scrub_to - 1 || sec > st.scrub_to + 1) &&
        (status == 3 || (status == 2 && st.settle_played < SETTLE_PLAY_MS)))
        return 8; /* RET_REPEAT: paused, or not there yet */
    st.settle_timer = 0;
    if (st.np_win) playing_timer_start(st.np_win);
    return 0;
}

/* Commits a target the wheel moved, unless the playing track changed since the scrub began; the
 * fill comes back, and the page's timer once the player is there (settle_poll), unless the page is
 * gone. */
static void scrub_end(void) {
    if (!st.scrub) return;
    st.scrub = 0;
    stop_timer(&st.scrub_timer);
    int seek = st.scrub_moved && np_track() == st.scrub_track;
    if (seek) st.held_sec = seek_position(st.scrub_to);
    st.scrub_moved = 0;
    if (!st.np_win) return;
    np_fill(0);
    st.settle_played = 0;
    if (seek)
        rearm(&st.settle_timer, settle_poll, SETTLE_MS);
    else
        playing_timer_start(st.np_win);
}

static int scrub_expire(const void *info) {
    st.scrub_timer = 0;
    scrub_end();
    return 0;
}

/* The lyrics: stock's 250 ms timer scrolls scroll_lrc to the current line whenever it is elsewhere.
 * Once the wheel or a finger moves them they stay (ringnav_lyric_follow), while the timer keeps the
 * bar and the highlight going, until Now Playing goes or another song starts, a swipe to another
 * page and back included. A scrub under way ends first. */
static void lyric_hold(void) {
    scrub_end();
    st.lyric_held = 1;
}

/* The glide's step, every UI pass: a dt / (dt + LYRIC_EASE_MS) share of the way left, at least a
 * pixel, so it eases in whatever the loop's pace, and quick wheel travel adds up. */
#define LYRIC_EASE_MS 50
static void lyric_step(void *lrc) {
    unsigned now = (unsigned)time_now_ms();
    int dt = (int)(now - st.lyric_at);
    st.lyric_at = now;
    lyric_pad(lrc);
    int y = I(lrc, SCROLL_Y),
        to = (clamp_step(st.lyric_to, (I(lrc, VIEW_CONTENT_H) - I(lrc, W_H)) * 16, 0) + 8) >> 4;
    if (to == y) {
        st.lyric_glide = 0;
        return;
    }
    if (dt > LYRIC_EASE_MS) dt = LYRIC_EASE_MS; /* a late pass: half the way, no leap */
    int step = (to - y) * dt / (dt + LYRIC_EASE_MS);
    scroll_view_set_offset(lrc, I(lrc, SCROLL_X), y + (step ? step : to > y ? 1 : -1));
}

/* The wheel let go: the glide ends on the row top nearest its target, so no row is cut at the top.
 * The end is one (lyric_pad). */
static void lyric_snap(void *lrc) {
    lyric_pad(lrc);
    int at = st.lyric_glide ? st.lyric_to : I(lrc, SCROLL_Y) * 16, to = 0;
    for (int i = 0, n = widget_count_children(lrc); i < n; ++i) {
        int top = I(widget_get_child(lrc, i), W_Y) * 16;
        if ((top > at ? top - at : at - top) < (to > at ? to - at : at - to)) to = top;
    }
    lyric_glide(lrc, to);
}

/* Every UI loop pass (ringnav_sleep). On the lyrics page in front, while the track has lyrics, the
 * wheel posts no ticks (wheel_raw) and its raw travel, 200 units a turn, glides them a pixel at a
 * time: a tick's worth of travel at the Wheel Sensitivity moves them LYRIC_ROW_PX, stock's row, as
 * far as a tick did. No key event comes, so the travel times the loop's idle and the screen's
 * timeout itself. While a finger stays on the wheel they stay where it put them; let go, they glide
 * to the nearest row top (lyric_snap). A finger that came down on the lyrics and dragged them holds
 * them as the wheel does; stock's scroll animation stops as it lands, so only the finger moves
 * them, and a swipe that brings the page in holds nothing. A swipe to another page and back keeps
 * the hold; leaving Now Playing drops it (np_gone). Another song starts at the top, its hold
 * gone. The lyrics page's rows are its lines, a tick's worth a line. */
#define LYRIC_ROW_PX 20

/* Now Playing's lyrics are the lyrics page's (lyrics.c): the rows full width after a blank
 * LYRIC_SLOT. Stock's init built the rows before ringnav_playing knew the page; they become the
 * page's, or, with none or no memory for their table, stay stock's. */
static void lyric_init(void) {
    void *lrc = st.np_lrc;
    if (lrc && lyrics_rows(lrc)) lyric_pad(lrc);
}

/* The lyrics page, every pass in front, else every LYRIC_FOLLOW_MS: its playing line centred,
 * gliding there while the page shows, else jumping, unless the wheel or a finger holds them or a
 * finger is on the screen. Its rows repaint while it is in front (front) and lit. */
#define LYRIC_FOLLOW_MS 250
static void lyric_follow(void *lrc, int ms, int front, int shown) {
    int line = lyrics_line(lrc, ms, front), to = lyric_centre(lrc, line > 0 ? line : 0);
    if (to < 0) to = 0;
    if (st.lyric_held || st.lyric_spun == 2 || pointer_down())
        return; /* lyric_spun 2: a finger on the wheel holds the nudge */
    lyric_pad(lrc);
    if (shown) {
        if (to * 16 != st.lyric_to || (!st.lyric_glide && I(lrc, SCROLL_Y) != to))
            lyric_glide(lrc, to * 16);
    } else if (I(lrc, SCROLL_Y) != to) {
        lyric_still(lrc);
        scroll_view_set_offset(lrc, I(lrc, SCROLL_X), to);
    }
}

/* Smart Awake (#57): Now Playing lit in front, a song playing, on its lyrics, info or
 * visualizer page, or its full-screen art in front, holds the screen on as the lyrics wheel does;
 * paused, it times out as usual. */
static void keep_lit_poll(void) {
    unsigned now = (unsigned)time_now_ms();
    if (!st.np_slide || now - st.keep_lit_at < 1000) return;
    st.keep_lit_at = now;
    accent(); /* reads the settings */
    void *top = window_manager_get_top_window(window_manager());
    if (st.keep_lit && g_backlight_status && mclGetPlayStatus() == 2 &&
        ((top == st.np_win && widget_get_prop_int(st.np_slide, "value", 0)) ||
         (top && top == st.bc[2])))
        reset_poweroptions_timer(1, 1, 1);
}

/* Smart Awake's setting (accent's, kept current by its switch): addon.c holds a card game open
 * and lit under it */
int ringnav_smart_awake(void) {
    accent();
    return st.keep_lit;
}

/* Full-screen lyrics (np_lyric_touch): the column (slide_view_view) goes over the page's other
 * widgets, black and the window's width, its slide_view down to the page dots, and the lines wrap
 * at the new width (lyrics_fit), back on the playing line; off, its place, index and slide_view's
 * height before (np_was) again. Centred's column (tools/ipod.py PLAYING_STOCK) keeps its y and
 * height, its lyrics ending 20 px above its foot. The iPod page's, between the top row and the
 * bar, takes the whole window: its dots NP_FULL_FOOT above the foot, as Centred's, and its
 * view_lrc, whose self_layout fixes its place, from the top down to NP_FULL_FOOT above them; off,
 * both self_layouts are their old place again. A page without a slide_view has none of this. */
#define NP_FULL_FOOT 20
static void np_full(int on) {
    if (!st.np_slide || !on == !st.np_full) return; /* without stock's slide_view: no column */
    void *col = P(st.np_slide, W_PARENT), *dots = widget_lookup(col, "slide_indicator1", 1),
         *view = P(st.np_lrc, W_PARENT);
    /* off, the layout it started in: Centred's setting changes only the next Now Playing's */
    int *was = st.np_was, ipod = on ? np_layout() != NP_CENTRED && dots && view : st.np_full == 2;
    if (on) {
        int now[] = { I(col, W_X), I(col, W_Y),          I(col, W_W),
                      I(col, W_H), widget_index_of(col), I(st.np_slide, W_H) };
        memcpy(was, now, sizeof now);
        if (ipod) was[6] = I(view, W_Y), was[7] = I(view, W_H), was[8] = I(dots, W_Y);
    }
    st.np_full = on ? 1 + ipod : 0;
    widget_restack(col, on ? widget_count_children(st.np_win) : was[4]);
    widget_set_prop_str(col, "style:normal:bg_color", on ? "#000000" : "#00000000");
    int h = I(st.np_win, W_H), foot = on ? h - NP_FULL_FOOT : was[8];
    if (!on)
        widget_move_resize(col, was[0], was[1], was[2], was[3]);
    else
        widget_move_resize(col, 0, ipod ? 0 : was[1], I(st.np_win, W_W), ipod ? h : was[3]);
    if (ipod) {
        char y[12], vy[12], vh[12];
        tk_snprintf(y, sizeof y, "%d", foot);
        tk_snprintf(vy, sizeof vy, "%d", on ? 0 : was[6]);
        tk_snprintf(vh, sizeof vh, "%d", on ? -NP_FULL_FOOT : was[7]);
        widget_set_self_layout_params(dots, "0", y, "100%", "10");
        widget_set_self_layout_params(view, "0", vy, "100%", vh);
    }
    widget_resize(st.np_slide, I(col, W_W), on && dots ? (ipod ? foot : I(dots, W_Y)) : was[5]);
    widget_layout(col);
    if (lyrics_fit(st.np_lrc)) lyric_pad(st.np_lrc);
    /* the follow takes them; the offset the layout left is no drag of the touch (lyric_poll) */
    st.lyric_held = 0, st.lyric_drag_y = I(st.np_lrc, SCROLL_Y);
}

static void lyric_poll(void) {
    void *lrc = st.np_lrc, *wm = window_manager();
    unsigned song = lrc ? np_track() : st.lyric_song;
    if (song != st.lyric_song) {
        st.lyric_song = song;
        lyric_top(lrc);
    }
    int page = lrc && st.np_slide && widget_get_prop_int(st.np_slide, "value", 0) == 1;
    if (st.np_full && !page) np_full(0); /* full-screen lyrics end off their page */
    int touch = lrc && window_manager_get_top_window(wm) == st.np_win && pointer_down();
    int shown = page && lyrics_shown(),
        on = shown && widget_count_children(lrc) && mclGetLyricSize() > 0;
    if (touch && !st.lyric_touch) {
        st.lyric_touch = page ? 1 : 2;
        st.lyric_drag_y = page ? I(lrc, SCROLL_Y) : 0;
        if (page) lyric_still(lrc);
    } else if (!touch && st.lyric_touch) {
        if (st.lyric_touch == 1 && page && I(lrc, SCROLL_Y) != st.lyric_drag_y) lyric_hold();
        st.lyric_touch = 0;
    }
    if (on != st.lyric_raw) {
        __sync_lock_test_and_set(&wheel_travel, 0);
        st.lyric_raw = on;
        if (on || wheel_raw == WHEEL_RAW) wheel_raw = on; /* never the thumb's, handed over */
    }
    int travel = on ? __sync_lock_test_and_set(&wheel_travel, 0) : 0;
    /* Following, the wheel meets the resistance of a list's end: travel short of
     * LYRIC_PUSH_DETENTS within LYRIC_PUSH_MS only nudges the lyrics BUMP_PX from the follow's
     * place, and they spring back when it is let go, still following; a stray touch keeps the
     * follow. More takes over. */
    if (travel && !st.lyric_held) {
        unsigned at = (unsigned)time_now_ms();
        if (!st.lyric_push || at - st.lyric_push_at > LYRIC_PUSH_MS) {
            lyric_pad(lrc);
            st.lyric_push = 0; /* a nudge still held keeps its home: no walking off tick by tick */
            if (st.lyric_spun != 2)
                st.lyric_home = st.lyric_glide ? st.lyric_to : I(lrc, SCROLL_Y) * 16;
        }
        st.lyric_push_at = at;
        st.lyric_push += travel;
        /* the clicks Wheel Sensitivity makes: fewer raw units each as it rises */
        int push = LYRIC_PUSH_DETENTS * WHEEL_TICK_UNITS * 50 / wheel_pct();
        if (st.lyric_push > -push && st.lyric_push < push) {
            st.last_input = at;
            reset_poweroptions_timer(1, 1, 1);
            /* x rises anticlockwise: back up the lyrics */
            lyric_glide(lrc,
                        st.lyric_home - ((st.lyric_push > 0) - (st.lyric_push < 0)) * BUMP_PX * 16);
            st.lyric_spun = 2;
            travel = 0;
        }
    }
    if (travel) {
        st.last_input = time_now_ms();
        reset_poweroptions_timer(1, 1, 1);
        lyric_hold();
        st.lyric_push = 0;
        lyric_pad(lrc);
        int y = I(lrc, SCROLL_Y),
            from = st.lyric_glide || (st.lyric_to + 8) >> 4 == y ? st.lyric_to : y * 16;
        /* x rises anticlockwise: back up the lyrics */
        int row = lyrics_on(lrc) ? LYRIC_SLOT : LYRIC_ROW_PX;
        lyric_glide(lrc, from - travel * row * 16 * wheel_pct() / (WHEEL_TICK_UNITS * 50));
        st.lyric_spun = 1;
    }
    if (!page)
        st.lyric_spun = st.lyric_push = 0;
    else if (st.lyric_spun == 2 && !wheel_held) {
        /* a nudge springs back, shown or not, unless a tick took them meanwhile; the follow moves
         * them on if the line changed. The next touch starts its push afresh. */
        if (shown && !st.lyric_held) lyric_glide(lrc, st.lyric_home);
        st.lyric_spun = st.lyric_push = 0;
    } else if (shown && st.lyric_spun && !wheel_held) {
        st.lyric_spun = 0;
        lyric_snap(lrc);
    }
    /* The page in front and usable (lit): the clock and the follow every pass (it narrows on each
     * second's change, lyrics_now). Else the follow, which only jumps, at stock's timer's pace on
     * whole seconds (lyrics_sec), so a dark or covered page reads the clock only that often. */
    int front = page && usable() && window_manager_get_top_window(wm) == st.np_win;
    unsigned now = (unsigned)time_now_ms();
    if (lyrics_on(lrc) && (front || now - st.lyric_follow_at >= LYRIC_FOLLOW_MS)) {
        st.lyric_follow_at = now;
        lyric_follow(lrc, front ? lyrics_now() : lyrics_sec(), front, shown);
    }
    if (!shown)
        st.lyric_glide = 0;
    else if (st.lyric_glide)
        lyric_step(lrc);
}

/* A tick on the lyrics page while the track has lyrics, which comes only before lyric_poll takes
 * the wheel raw, glides them to the next or previous row's top, ahead of scrub and the volume. */
static int np_lyrics(unsigned key) {
    void *lrc = st.np_lrc;
    if (!st.np_slide || !lrc || widget_get_prop_int(st.np_slide, "value", 0) != 1 ||
        !widget_count_children(lrc) || mclGetLyricSize() <= 0)
        return 0;
    lyric_hold();
    lyric_pad(lrc);
    int y = st.lyric_glide ? (st.lyric_to + 8) >> 4 : I(lrc, SCROLL_Y),
        max = I(lrc, VIEW_CONTENT_H) - I(lrc, W_H), to = key == KEY_NEXT ? max : 0;
    for (int i = 0, n = widget_count_children(lrc); i < n; ++i) {
        int top = I(widget_get_child(lrc, i), W_Y);
        if (key == KEY_NEXT ? top > y && top < to : top < y && top > to) to = top;
    }
    lyric_glide(lrc, clamp_step(to, max, 0) * 16);
    return 1;
}

static void np_cancel(void) {
    unsigned settle = st.settle_timer; /* an earlier scrub's wait; the one scrub_end arms stays */
    stop_timer(&st.np_press);
    scrub_end();
    if (!settle) return;
    stop_timer(&st.settle_timer);
    if (st.np_win) playing_timer_start(st.np_win);
}

/* A single centre press, DOUBLE_CLICK_MS on, never while scrubbing (with Single Press Scrub, a
 * double press): it replays the release to stock on_wm_keyup_fun, which turns the screen off (it
 * reads only the event's key). */
static int np_single(const void *info) {
    static const unsigned release[EVENT_KEY / 4 + 1] = { [EVENT_KEY / 4] = KEY_CENTER };
    st.np_press = 0;
    if (g_backlight_status) on_wm_keyup_fun((void *)0, (void *)release);
    return 0;
}

/* The top window if it is stock's volume dialog (vol_paint), else 0. */
static void *vol_top(void) {
    void *top = window_manager_get_top_window(window_manager());
    if (!top || tk_strcmp(widget_get_prop_str(top, "name", ""), "volume_dialog")) return (void *)0;
    return top;
}

/* The volume dialog on top with Now Playing directly under it (where vol_paint draws its band,
 * given the slider and times), else 0. */
static void *vol_band(void *wm) {
    void *vol = st.np_win ? vol_top() : (void *)0;
    unsigned n = vol ? widget_count_children(wm) : 0;
    return n > 1 && widget_get_child(wm, n - 2) == st.np_win ? vol : (void *)0;
}

/* Scrub starts from the bar's second; the wheel moves the target until scrub_end. A volume
 * band over the page closes first, as a tap beside its bar does, so the wheel reaches the scrub
 * and the bar shows. The close runs the page's foreground handler, which sets the bar to the
 * player's second and restarts stock's timer: the target is read, and the timer cleared, after
 * it. */
static void scrub_start(void) {
    if (st.scrub || !st.np_slider) return;
    void *vol = vol_band(window_manager());
    if (vol) window_close(vol);
    st.scrub = 1;
    st.scrub_moved = 0;
    st.scrub_to = widget_get_prop_int(st.np_slider, "value", 0);
    st.scrub_track = np_track();
    stop_timer(&st.settle_timer);
    playing_timer_clear(st.np_win);
    np_fill(1);
    rearm(&st.scrub_timer, scrub_expire, SCRUB_MS);
}

/* Single Press Scrub (IPOD SCRUBSINGLE)'s single press, DOUBLE_CLICK_MS on: scrub starts. */
static int np_scrub(const void *info) {
    st.np_press = 0;
    if (g_backlight_status) scrub_start();
    return 0;
}

/* Centre and, while scrubbing, the wheel on the top Now Playing page. A centre press waits
 * DOUBLE_CLICK_MS: a second one starts scrub, else it turns the screen off as anywhere else; with
 * Single Press Scrub the two swap, so a double press turns the screen off as on the lists. While
 * scrubbing one press ends it at once (center_press). The wheel moves the target SCRUB_STEP
 * seconds times the WHEEL_RAMP_MS ramp, within the track, and only previews it: the seek waits
 * for the scrub to end. Neither the volume nor its dialog sees the wheel. */
static int np_key(void *top, unsigned key) {
    unsigned now = (unsigned)time_now_ms();
    if (key == KEY_CENTER) {
        int one = (accent(), st.scrub_single);
        int press =
            center_press(&st.np_press, &st.np_press_at, one ? np_scrub : np_single, st.scrub);
        if (one && !press) /* an overdue single press just started a scrub: this one ends it */
            press = 2, st.np_press_at = now;
        if (press != 2) return press ? STOP : 0;
        if (st.scrub) /* one press ends a scrub */
            scrub_end();
        else if (one)
            np_single((void *)0);
        else
            scrub_start();
        return STOP;
    }
    if (np_track() != st.scrub_track) { /* a new track: scrub_end drops the last one's target */
        scrub_end();
        return STOP;
    }
    int dir = key == KEY_NEXT ? 1 : -1;
    /* the step grows while the wheel spins: every tick counts, on the ramp lists had before */
    int step = SCRUB_STEP * ramp(top, st.np_slider, 0, -1, dir, now, WHEEL_RAMP_MS, WHEEL_RAMP_MS);
    int to = clamp_step(st.scrub_to, widget_get_prop_int(st.np_slider, "max", 0), dir * step);
    if (to != st.scrub_to) st.scrub_moved = 1;
    st.scrub_to = to;
    widget_set_prop_int(st.np_slider, "value", to);
    clock_text(st.np_elapsed, 0, to);
    np_sync(top);
    rearm(&st.scrub_timer, scrub_expire, SCRUB_MS);
    return STOP;
}

static int np_gone(void *win, void *event) {
    if (win == st.np_win) {
        st.np_win = (void *)0;
        st.np_hash = 0;
        st.np_pos = st.np_album = st.np_remain = st.np_slider = st.np_elapsed = st.np_cover =
            st.np_slide = st.np_lrc = st.np_title = (void *)0;
        st.scrub_moved = 0; /* the page is going: no seek */
        st.np_off = st.np_full = st.lyric_held = 0;
        stop_timer(&st.np_marquee);
        stop_timer(&st.np_remain_timer);
        stop_timer(&st.np_fit_timer);
        stop_timer(&st.genre_timer);
        st.genre_label = (void *)0;
        np_cancel();
        coverflow_slant_close();
        lyrics_gone(); /* the next page's rows may take its rows' place */
    }
    return 0;
}

/* Now Playing's art (either page) follows the player's cover. Stock reloads img_cover only on
 * one-shot flags its 250 ms timer finds set: a song change, a forced refresh, the cover thread's
 * finish. Enlarged Art's timer clears all three while it is open (0x52dac0), so a change it took
 * left the page on the last track's art. Each lit pass, the playing track and the player's file it
 * can use (once g_lastcover_url is this track's: 1 embedded, 2 folder image, 4 downloaded, 3 none:
 * stock's placeholder) are compared with the last; a change reloads the art as stock does, the
 * cached bitmap dropped first (the player writes every track's cover to the same file). Anything
 * else (parsing, Tidal) keeps stock's image.
 * ponytail: one more decode per track change while the page is up, beside stock's own reload. */
static unsigned np_art(const char **file) {
    unsigned at, n;
    void *r = queue_now(&at, &n);
    unsigned char type = now_cover(r, file);
    if (type == 3) *file = "play_defaultcover"; /* none: stock's placeholder */
    return *file ? fnv(hash_bytes(FNV_SEED, &type, 1), (const unsigned char *)P(r, REC_PATH)) : 0;
}
static void np_art_poll(void) {
    const char *file;
    if (!st.np_cover || !g_backlight_status) return;
    unsigned key = np_art(&file);
    if (key == st.np_art) return;
    st.np_art = key;
    if (!file) return;
    image_manager_unload_bitmap_by_name(image_manager(), file);
    image_base_set_image(st.np_cover, file);
    coverflow_slant_close(); /* the same file, another cover: Classic's art drawn again */
    widget_invalidate_force(st.np_cover, (void *)0);
}

/* Classic's Spotify or Internet Radio Now Playing closing: its art's slanted frame goes with it.
 * Only the latest page's art: an old page destroyed after a new one set its own leaves that be. */
static int stream_gone(void *ctx, void *event) {
    if (ctx != st.stream_art) return 0;
    st.stream_art = st.stream_bar = (void *)0;
    coverflow_slant_close();
    return 0;
}
/* Classic's Spotify and Internet Radio Now Playing (spotify.c, radio.c) take the local page's
 * places (NP_IPOD_*): the art, its box taller than wide, so paint_cover slants the image it shows
 * over its reflection (coverflow_slant, whose one frame is keyed by the image's url and pixels, so
 * the local page and this one never show each other's); the title's, artist's, album's and "3 of
 * 12"'s rows (text, any 0 left out); the times in "-9:59"'s boxes beside the bar, in Classic's
 * face, until the page fits them to its track (ipod_times); the bar, which the page paints with
 * ipod_bar, or with none a bar of its own, last so over the art's box, that paint_scrubber paints
 * always full and that takes no touch; the heart. 0 under the other themes, whose pages keep their
 * own layout. */
int stream_ipod(void *art, void *const text[4], void *elapsed, void *remain, void *bar,
                void *heart) {
    static const unsigned char dy[] = { 0, 34, 62, 90 }, h[] = { 28, 22, 22, 20 };
    if (theme()->np_track[0].n < 2) return 0;
    int reflect = (NP_IPOD_ART * SLANT_REFLECT_PCT + 99) / 100, bw = 375 - 2 * NP_IPOD_BAR_X;
    widget_move_resize(art, SPOT_ART_X, NP_IPOD_ART_Y, NP_IPOD_ART, NP_IPOD_ART + reflect);
    st.stream_art = art, st.stream_bar = (void *)0;
    widget_on(art, EVT_DESTROY, stream_gone, art);
    for (unsigned i = 0; i < 4; ++i)
        if (text[i])
            widget_move_resize(text[i], NP_IPOD_TEXT_X, NP_IPOD_TEXT_Y + dy[i],
                               375 - SPOT_ART_X - NP_IPOD_TEXT_X, h[i]);
    if (text[3]) widget_set_prop_int(text[3], "style:normal:font_size", 16); /* "3 of 12"'s */
    void *times[] = { elapsed, remain };
    for (unsigned i = 0; i < 2; ++i) {
        if (!times[i]) continue;
        widget_move_resize(times[i],
                           i ? NP_IPOD_BAR_X + bw + NP_IPOD_TIME_GAP
                             : NP_IPOD_BAR_X - NP_IPOD_TIME_GAP - NP_IPOD_TIME_W1,
                           NP_IPOD_BAR_Y - (NP_IPOD_TIME_H - NP_BAR_IPOD + 1) / 2, NP_IPOD_TIME_W1,
                           NP_IPOD_TIME_H);
        widget_use_style(times[i], i ? "s_scrlabel_white20l" : "s_scrlabel_white20r");
        widget_set_prop_str(times[i], "style:normal:font_name", font_faces[FONT_CLASSIC_LIGHT]);
        widget_set_prop_int(times[i], "style:normal:font_size", NP_IPOD_TIME_PX);
        widget_set_prop_int(times[i], "style:normal:text_color", (int)0xffffffffu);
    }
    if (!bar) {
        bar = st.stream_bar =
            widget_factory_create_widget(widget_factory(), "view", P(art, W_PARENT), 0, 0, 0, 0);
        widget_set_sensitive(bar, 0);
    }
    widget_move_resize(bar, NP_IPOD_BAR_X, NP_IPOD_BAR_Y, bw, NP_BAR_IPOD + SCRUB_REFLECT_ROWS);
    if (heart) widget_move_resize(heart, NP_IPOD_FAV_X, NP_IPOD_FAV_Y, 50, 50); /* local NP's */
    return 1;
}

/* Now Playing's lyrics page (ringnav_playing): a touch that lifts within TAP_PX of where it came
 * down, so neither a swipe nor a drag of the lines, switches them to full screen and back
 * (np_full), once there are lyrics to show. The slide_view or scroll_lrc taking the touch (its
 * abort) ends full screen at once, so the pages slide in their column. Returns 0: a STOP would
 * keep the slide_view's grab (rate_fav_click). */
#define TAP_PX 10 /* AWTK's drag threshold */
static int np_lyric_touch(void *ctx, void *event) {
    int type = I(event, EVENT_TYPE), x = I(event, EVENT_X), y = I(event, EVENT_Y);
    int tap = (unsigned)(x - st.np_tap_x + TAP_PX) <= 2 * TAP_PX &&
              (unsigned)(y - st.np_tap_y + TAP_PX) <= 2 * TAP_PX;
    if (type == EVT_POINTER_DOWN) {
        st.np_tap_x = x, st.np_tap_y = y;
        return 0;
    }
    st.np_tap_x = 1 << 20; /* used or taken for a swipe: a later lift is no tap */
    if (type == EVT_POINTER_ABORT) {
        if (st.np_full) np_full(0);
    } else if (tap && (st.np_full || lyrics_on(st.np_lrc)))
        np_full(!st.np_full);
    return 0;
}

/* playing_page_init: stock builds the page and starts its 250 ms timer, then the iPod labels bind.
 * Headphones required with none to play to: stock toasts, closes the window it was given and
 * returns STOP before it builds anything (0x53e4b4), so nothing binds. */
int ringnav_playing(void *win, void *ctx) {
    if (st.np_full)
        np_full(0); /* another Now Playing: the last one's lyrics back in their column */
    /* and their place: np_gone leaves a page this one replaces alone */
    st.lyric_held = 0;
    int result = stock_playing_trampoline(win, ctx);
    if (!win || result == STOP) return result;
    rate_bind(win);
    /* np_gone leaves a replaced page's state alone: its Genre line is that page's. */
    stop_timer(&st.genre_timer);
    st.genre_label = (void *)0;
    st.np_win = win;
    st.np_pos = widget_lookup(win, "label_ipod_pos", 1);
    st.np_album = widget_lookup(win, "label_ipod_album", 1);
    st.np_slider = widget_lookup(win, "slider_play", 1);
    st.np_remain = widget_lookup(win, "label_ipod_remain", 1);
    st.np_elapsed = widget_lookup(win, "label_playtime", 1);
    st.np_cover = widget_lookup(win, "img_cover", 1);
    st.np_title = widget_lookup(win, "scrlabel_title", 1);
    st.np_bold = -1;
    st.np_slide = widget_lookup(win, "slide_view", 1);
    st.np_lrc = widget_lookup(win, "scroll_lrc", 1);
    st.lyric_song = st.lyric_rows_song = np_track(); /* no song change at the start */
    lyric_init();                                    /* the rows stock's init built */
    void *lyric = widget_lookup(win, "view_lyric", 1);
    static const unsigned touch[] = { EVT_POINTER_DOWN, EVT_POINTER_UP_BEFORE, EVT_POINTER_ABORT };
    for (unsigned i = 0; lyric && i < sizeof touch / sizeof *touch; ++i)
        widget_on(lyric, touch[i], np_lyric_touch, (void *)0);
    st.np_hash = 0;
    const char *file;
    st.np_art = np_art(&file); /* stock's init loaded it */
    st.np_left = -1;
    np_fill(0);
    np_theme();
    widget_on(win, EVT_DESTROY, np_gone, win);
    int sec = -1, total = 0;
    player_playtime_and_length(&sec, &total);
    /* Stock's first update sets the value before the max, so the asset's max clamps it and the
     * bar sits short until the next tick: set them again, max first. */
    if (st.np_slider && total > 0) {
        widget_set_prop_int(st.np_slider, "max", total);
        widget_set_prop_int(st.np_slider, "value", sec);
    }
    /* A paused seek still waiting for Play, the player's second unmoved: the bar shows its target
     * again, held as scrub_end holds it (settle_poll). Resumed or another track drops it. */
    if (st.held_sec && st.held_sec == sec + 1 && np_track() == st.scrub_track &&
        mclGetPlayStatus() == 3 && st.np_slider) {
        playing_timer_clear(win);
        widget_set_prop_int(st.np_slider, "value", st.scrub_to);
        clock_text(st.np_elapsed, 0, st.scrub_to);
        st.settle_played = 0;
        rearm(&st.settle_timer, settle_poll, SETTLE_MS);
    } else
        st.held_sec = 0;
    /* The page opens fitted when the length is known; a track just picked reports 0 until stock's
     * status poll (mclSyncPlayerStatus) has it, so it opens on the build's widest bar and np_sync
     * narrows it for a long track.
     * ponytail: inferred, not traced on the device; a 10:00+ track picked from a list still narrows
     * once after the open. Fit from the file's own length at open if that shows. */
    np_fit_due((void *)0);
    np_sync(win);
    visualizer_attach(win);
    return result;
}

/* The song name and its shade fade out together, BC_STEPS steps BC_STEP_MS apart. */
static int bc_fade(const void *info) {
    int n = ++st.bc_step;
    if (n == 1) st.bc_timer = timer_add(bc_fade, (void *)0, BC_STEP_MS); /* this one ends */
    for (int i = 0; i < 2; ++i)
        if (st.bc[i]) widget_set_opacity(st.bc[i], 255u * (unsigned)(BC_STEPS - n) / BC_STEPS);
    if (n < BC_STEPS) return n == 1 ? 0 : 8; /* RET_REPEAT */
    st.bc_timer = 0;
    return 0;
}

static int bc_gone(void *win, void *event) {
    stop_timer(&st.bc_timer);
    stop_timer(&st.bc_repaint);
    st.bc[0] = st.bc[1] = st.bc[2] = (void *)0;
    return 0;
}

/* Shows the song name and its shade again for BC_MS, then fades them out. */
static void bc_show(void) {
    stop_timer(&st.bc_timer);
    for (int i = 0; i < 2; ++i)
        if (st.bc[i]) widget_set_opacity(st.bc[i], 255);
    st.bc_step = 0;
    st.bc_track = np_track();
    st.bc_timer = timer_add(bc_fade, (void *)0, BC_MS);
}

static int bc_repaint(const void *info) {
    st.bc_repaint = 0;
    if (st.bc[2]) widget_invalidate_force(st.bc[2], (void *)0);
    return 0; /* RET_REMOVE */
}

/* The full-screen art takes its cover's shape: the largest w:h box centred on the window whose
 * corners clear the glass by GLASS_SLACK, as tools/ipod.py sizes the square BC_ART, with the shade
 * and song name on its foot. Checked as the window paints; the image is decoded for its shape only
 * when the track, stock's cover of it or the image changes, and the box is sized again when that
 * shape or the box (a layout back to the square) changes. Stock names the player's one file per
 * kind by g_playcover_type (0x52d828), so one counts only once it is the type's and g_lastcover_url
 * is this track's, as Home's art; until then the art keeps the shape of g_lastcover_url's cover, or
 * the square once that changes. The file is decoded past the image manager's cached copy, which may
 * be the last track's. Stock's default_bigcover placeholder is the square. */
static void bc_fit(void *win) {
    void *img = widget_lookup(win, "img_bigcover", 1), *shade = st.bc[1], *title = st.bc[0];
    const char *name = img ? widget_get_prop_str(img, "image", "") : "";
    unsigned at, n;
    const char *cover;
    unsigned type = now_cover(queue_now(&at, &n), &cover);
    unsigned box = img ? (unsigned)(I(img, W_W) << 16 | I(img, W_H)) : 0;
    unsigned key = fnv(np_track() ^ type, (const unsigned char *)name);
    if (!img || !shade || !title || (key == st.bc_key && box == st.bc_box)) return;
    int player = tk_str_end_with(name, "/tmp/coverpic.jpg") ||
                 tk_str_end_with(name, "/tmp/externpic.jpg") ||
                 tk_str_end_with(name, "/tmp/album_tidal.jpg");
    const char *own = type == 1                ? "/tmp/coverpic.jpg"
                      : type == 2 || type == 4 ? "/tmp/externpic.jpg"
                                               : "";
    unsigned bitmap[64], url = fnv(0, (const unsigned char *)g_lastcover_url);
    if (key != st.bc_key && player && !(*own && tk_str_end_with(name, own))) {
        if (url != st.bc_url) st.bc_shape = 1 << 16 | 1;
    } else if (key != st.bc_key) {
        st.bc_shape = 1 << 16 | 1,
        st.bc_url = url; /* the square when it won't load or is past 4:1 */
        if (tk_strcmp(name, "default_bigcover")) {
            image_manager_unload_bitmap_by_name(image_manager(), name);
            if (!widget_load_image(img, name, bitmap)) {
                if (bitmap[0] && bitmap[1] && bitmap[0] < 1 << 16 && bitmap[1] < 1 << 16 &&
                    bitmap[0] <= 4 * bitmap[1] && bitmap[1] <= 4 * bitmap[0])
                    st.bc_shape = bitmap[0] << 16 | bitmap[1];
                widget_unload_image(img, bitmap);
            }
        }
    }
    st.bc_key = key, st.bc_box = box;
    unsigned w = st.bc_shape >> 16, h = st.bc_shape & 0xffff;
    for (int s = 374; s > 2; s -= 2) {
        int bw = w >= h ? s : (int)(s * w / h), bh = w >= h ? (int)(s * h / w) : s;
        int x = (375 - bw) / 2, y = (320 - bh) / 2, dx = GLASS_R + GLASS_SLACK - x,
            dy = GLASS_R - y;
        if (bh > 320 || (dx > 0 && dy > 0 && dx * dx + dy * dy > GLASS_R * GLASS_R)) continue;
        if (I(img, W_X) == x && I(img, W_Y) == y && I(img, W_W) == bw && I(img, W_H) == bh) return;
        int sh = I(shade, W_H), foot = y + bh - sh;
        widget_move_resize(title, x + BC_TITLE_PAD, foot + I(title, W_Y) - I(shade, W_Y),
                           bw - 2 * BC_TITLE_PAD, I(title, W_H));
        widget_move_resize(shade, x, foot, bw, sh);
        widget_move_resize(img, x, y, bw, bh);
        st.bc_box = (unsigned)(bw << 16 | bh);
        rearm(&st.bc_repaint, bc_repaint, 0); /* AWTK drops what a paint invalidates */
        return;
    }
}

/* Geometry sets: tools/ipod.py bakes SET0 into the stock row immediates (ipod.json). Another set
 * rewrites, once, only the low halfwords that differ. Stock text is R-X, so every page holding one
 * goes RWX first, lowest first; only if all did are they written, flushed and put back to R-X. A
 * failed mprotect puts back the pages already opened and writes nothing, so the code stays SET0's
 * and st.geom, the set the code holds, stays 0. SET0 makes no syscall and writes nothing. */
static int boot_differs(int i, const volatile unsigned short *v) {
    return (*(unsigned *)boot_sites[i].addr & 0xffff) != (unsigned)v[boot_sites[i].group];
}
static unsigned boot_page(const volatile unsigned short *v, unsigned from) { /* ~0u: none */
    unsigned page = ~0u;
    for (int i = 0; i < BOOT_SITES; ++i) {
        unsigned p = boot_sites[i].addr & ~(THEME_PAGE - 1u);
        if (p >= from && p < page && boot_differs(i, v)) page = p;
    }
    return page;
}
/* The set T boots with setting at v and every other option as stored; with chosen, as a switch to T
 * leaves them: an option's preset unless it has none or the stored value is its keep
 * (theme_preset's test). Its own option with sets selects the set (tools/theme.py allows one);
 * without one, T->geom. */
static int geom_with(const theme_t *T, int setting, int v, int chosen) {
    for (int k = 0; k < T->n_opts; ++k) {
        const option_t *o = &T->opts[k];
        if (o->setting < SETTING_OWN || !theme_keys[o->setting - SETTING_OWN].sets) continue;
        int s = o->setting - SETTING_OWN, w = o->setting == setting ? v : st.own[s];
        if (chosen && o->preset >= 0 && w != o->keep) w = o->preset;
        return theme_keys[s].sets[w];
    }
    return T->geom;
}
int theme_geom(const theme_t *T) { return geom_with(T, -1, 0, 0); }
static void theme_boot(void) {
    unsigned g = theme_geom(theme()), p, q;
    const volatile unsigned short *v = boot_values[g];
    for (p = boot_page(v, 0); p != ~0u; p = boot_page(v, p + THEME_PAGE))
        if (theme_mprotect((void *)p, THEME_PAGE, 7)) { /* RWX */
            for (q = boot_page(v, 0); q != p; q = boot_page(v, q + THEME_PAGE))
                theme_mprotect((void *)q, THEME_PAGE, 5);
            /* A theme only ever runs on its own set, so a set this boot can't write reads as
             * Minimal until the next boot; THEME stays as stored. Its Appearance rows are never
             * built on another theme's, whose rows a switch would redraw through unset labels. */
            st.theme = 0;
            return;
        }
    for (p = boot_page(v, 0); p != ~0u; p = boot_page(v, p + THEME_PAGE)) {
        for (int i = 0; i < BOOT_SITES; ++i)
            if ((boot_sites[i].addr & ~(THEME_PAGE - 1u)) == p && boot_differs(i, v)) {
                unsigned *w = (unsigned *)boot_sites[i].addr;
                *w = (*w & 0xffff0000) | v[boot_sites[i].group];
            }
        theme_cacheflush((void *)p, THEME_PAGE, 3); /* BCACHE */
        theme_mprotect((void *)p, THEME_PAGE, 5);   /* PROT_READ|PROT_EXEC */
    }
    st.geom = g;
}

/* The set theme_boot wrote's own copy of a window, <set>_<name>.bin in name's folder
 * (tools/build.py), written into buf (the caller's 64 bytes), else name. */
static const char *ui_redirect(const char *name, char *buf) {
    unsigned g = st.geom;
    for (const volatile ui_redirect_t *r = ui_redirects; r->name; ++r)
        if (r->sets >> g & 1 && !tk_strcmp(r->name, name)) {
            const char *slash = strrchr(name, '/'), *base = slash ? slash + 1 : name;
            tk_snprintf(buf, 64, "%.*s%s_%s", (int)(base - name), name, geom_names[g], base);
            return buf;
        }
    return name;
}

/* The value of group (G_PITCH, G_BODY, ...) in the set theme_boot wrote (st.geom). */
int geom(int group) { return boot_values[st.geom][group]; }

/* An A-B marker's x for point t (stock 0x53b9d8, from its three calls in the markers' placement:
 * AB_X_CALLS in tools/build.py), on the boot set's bar (the np_bar_x and np_bar_w immediates):
 * Classic's bar fitted to the track (ipod_times) takes it along, at the same share of it. 0, no
 * point to mark, stays. */
int ringnav_ab_x(int t) {
    int x = ab_x(t), x0 = geom(G_NP_BAR_X), w0 = geom(G_NP_BAR_W);
    if (!st.np_slider || x < x0 || w0 <= 0) return x;
    return I(st.np_slider, W_X) + (x - x0) * I(st.np_slider, W_W) / w0;
}

/* ui_loader_load_widget_with_parent, every window's asset (window_open's): the theme's layout
 * (np_layout(); Centred Now Playing forces NP_CENTRED) picks the page. Stock's own page
 * (tools/ipod.py PLAYING_STOCK) is named playing_page too, which ringnav_playing binds as the iPod
 * one; any opener comes through here. */
extern void *stock_ui_load_trampoline(const char *name, void *parent);
/* key: STYLE_KEY bytes. */
const char *style_key(char *key, const char *state, const char *name) {
    tk_snprintf(key, STYLE_KEY, "style:%s:%s", state, name);
    return key;
}
/* The slider's value as a part of w px. */
int slider_part(void *s, int w) {
    int min = widget_get_prop_int(s, "min", 0), max = widget_get_prop_int(s, "max", 100);
    return max > min ? w * (widget_get_prop_int(s, "value", 0) - min) / (max - min) : 0;
}
static int slider_gone(void *win, void *event) {
    if (win == st.slider_win) st.slider_win = st.slider_w = st.slider_sw = (void *)0;
    return 0;
}
/* Crossfade's switch is hidden, so its image change repaints nothing: the tray, for crossfade.c. */
void slider_tray_dirty(void *page) {
    rect_t tray = { 125, 28, 125, 44 };
    if (page && page == st.slider_win) widget_invalidate_force(page, &tray);
}
/* Max Volume's triangle moves past the slider's rect, inside the band. */
static int slider_moved(void *win, void *event) {
    rect_t band = { 28, 129, 322, 46 };
    widget_invalidate_force(win, &band);
    return 0;
}
/* The switch's parent is hidden, so it can't be hit: this does it, for stock's or crossfade.c's
 * handler. */
static int slider_switch_hit(void *win, void *event) {
    void *sw = widget_lookup(win, "img_bootswitch", 1);
    char click[0x30];
    if (!sw || !widget_get_visible(sw)) return 0;
    stock_dispatch_trampoline(sw, pointer_event_init(click, EVT_CLICK, sw, 0, 0));
    widget_invalidate_force(win, (void *)0); /* stock may have hidden the band's view */
    return 0;
}
/* A label's final ink, 0xAARRGGBB in its first n states, stored as what Light Mode's style hook
 * turns into it, so stored for this half. text_inks writes it again when the half changes. */
static const char *const ink_state[] = { "normal",
                                         "pressed",
                                         "over",
                                         "disable",
                                         "focused",
                                         "normal_of_active",
                                         "pressed_of_active",
                                         "over_of_active",
                                         "disable_of_active",
                                         "focused_of_active",
                                         "error",
                                         "empty",
                                         "empty_focus",
                                         "empty_over",
                                         "changed" };
void text_ink(void *w, unsigned color, int n) {
    char key[STYLE_KEY];
    unsigned ink = RGBA(flip_if(ipod_light(), color & 0xffffffu)) & 0xffffffu;
    ink |= color & 0xff000000u;
    widget_set_prop_int(w, "_final", (int)color);
    widget_set_prop_int(w, "_final_n", n);
    for (int i = 0; i < n; ++i)
        widget_set_prop_int(w, style_key(key, ink_state[i], "text_color"), (int)ink);
}
void text_inks(void *w) {
    int n = widget_get_prop_int(w, "_final_n", 0);
    if (n) text_ink(w, (unsigned)widget_get_prop_int(w, "_final", 0), n);
    for (int i = widget_count_children(w) - 1; i >= 0; --i) text_inks(widget_get_child(w, i));
}
/* A page's label: box, face and white, in either half (Light Mode's style hook flips it back). */
void slider_label(void *w, int x, int y, int ww, int h, int px, const char *face) {
    widget_move_resize(w, x, y, ww, h);
    widget_set_prop_str(w, "style:normal:font_name", face);
    widget_set_prop_int(w, "style:normal:font_size", px);
    widget_set_prop_str(w, "style:normal:text_align_h", "center");
    text_ink(w, 0xffffffffu, INK_LABEL);
}
/* The value's label, on the pages that show one, in the face its text has. Shared with crossfade.c.
 */
const char *number_face(void *label, int *px) {
    *px = 32;
    const char *face = theme_face(widget_get_text(label), -1, px);
    return face ? face : "default";
}
void slider_number(void *label) {
    if (!classic_pages()) return;
    int px;
    widget_set_visible(label, 1, 0);
    st.number_face = number_face(label, &px);
    slider_label(label, 0, 85, 375, 34, px, st.number_face);
}
static void time_open(void *win);
static void time_paint(void *w, void *canvas);
/* Stock's window as it loads, before its init. Every look-up it makes by name still finds its
 * widget: edit_key clicks img_dec and img_add, hidden here. */
static const __WCHAR_TYPE__ slider_caption[] = L"Press Centre When Finished";
/* Crossfade's and Night Shift's Centre stays on the page (crossfade.c): what it does there. */
void slider_centre_hint(void *page, int night) {
    void *c = page && page == st.slider_win ? widget_lookup(page, "caption", 1) : (void *)0;
    if (!c) return;
    st.slider_hint = night ? L"Press Centre to Change Mode" : L"Press Centre to Turn On or Off";
    widget_set_text(c, (const unsigned *)st.slider_hint);
}
static void slider_open(void *win) {
    static const char *const sliders[] = { "slider_backlight", "slider_maxvol", "slider_balance",
                                           "slider_bootvol" }, /* [SLIDER_*] */
        *const hide[] = { "img_dec",     "img_add",      "img_left",        "img_right",
                          "img_volleft", "img_volright", "label_backlight", "label_vol" },
                             *const states[] = { "normal", "pressed", "over", "disable",
                                                 "focused" };
    char key[32];
    void *s = (void *)0, *c;
    unsigned kind = 0;
    for (; !s && kind < sizeof sliders / sizeof *sliders; ++kind)
        s = widget_lookup(win, sliders[kind], 1);
    if (!s) return;
    slider_knobless(s); /* in every theme: the page's slider is a plain one */
    if (!classic_pages()) return;
    --kind;
    for (unsigned i = 0; i < sizeof hide / sizeof *hide; ++i)
        if ((c = widget_lookup(win, hide[i], 1))) widget_set_visible(c, 0, 0);
    int maxvol = kind == SLIDER_MAX;
    widget_move_resize(P(s, W_PARENT), 0, 0, 375, 294);
    widget_move_resize(s, 81, maxvol ? 111 : 117, 204,
                       70); /* its bar's rows 31..38 of a wide touch */
    /* stock's slider fills its knob in this colour, then draws the theme's icon on it, and has no
     * knob with neither (slider_knobless) */
    for (unsigned i = 0; i < sizeof states / sizeof *states; ++i)
        widget_set_prop_int(s, style_key(key, states[i], "border_color"), 0);
    st.slider_win = win;
    st.slider_w = s;
    st.slider_kind = (unsigned char)kind;
    st.slider_sw = (void *)0; /* stock's init makes it, after this: slider_paint finds it */
    st.slider_hint = slider_caption;
    widget_on(win, EVT_DESTROY, slider_gone, win);
    if (maxvol) { /* the triangle reaches past the slider's rect */
        widget_on(s, EVT_VALUE_CHANGING, slider_moved, win);
        widget_on(s, EVT_VALUE_CHANGED, slider_moved, win);
    }
    c = text(win, 0, 228, 375, 32);
    widget_set_name(c, "caption");
    widget_set_text(c, (const unsigned *)slider_caption);
    slider_label(c, 0, 228, 375, 32, 31, "classic-light");
    if ((c = widget_lookup(win, "label_balance", 1))) {
        slider_number(c);
        c = text(win, 50, 139, 20, 26);
        widget_set_text_utf8(c, "L");
        slider_label(c, 50, 139, 20, 26, 24, "classic-light");
        c = text(win, 306, 139, 20, 26);
        widget_set_text_utf8(c, "R");
        slider_label(c, 306, 139, 20, 26, 24, "classic-light");
    }
    if (kind != SLIDER_BOOT) return;
    /* the toolbar stays for img_bootswitch (tools/ipod.py TOOLBAR_SWITCHES); the bar has the title
     */
    if ((c = widget_lookup(win, "view_navbar", 1))) {
        widget_set_visible(c, 0, 0);
        widget_set_enable(c, 0);
    }
    c = image_create(win, 125, 28, 125, 44);
    widget_set_name(c, "switch_hit");
    widget_set_prop_str(c, "clickable", "true");
    widget_on(c, EVT_CLICK, slider_switch_hit, win);
}
/* The Off and On tray on its shadow, the recessed track, the lit cell in the scheme's selection. */
void slider_switch(void *w, void *canvas, void *sw) {
    static const unsigned off[] = { 'O', 'f', 'f' }, on[] = { 'O', 'n' };
    rect_t tray = { 125, 28, 125, 44 }, track = { 138, 37, 100, 26 };
    rect_t left = { 138, 37, 50, 26 }, right = { 188, 37, 50, 26 };
    const char *image = widget_get_prop_str(sw, "image", "");
    /* stock's two names, switch_on and switch_off, first differ at [8] */
    const rect_t *cell = image[0] && image[8] == 'n' ? &right : &left;
    unsigned white = RGBA(flip_if(ipod_light(), 0xffffff));
    paint_art(w, canvas, "classic_light_tray_shadow", 117, 22);
    paint_round_rows(canvas, &tray, &confirm_tray, 9);
    paint_round_rows(canvas, &track, &confirm_well, 6);
    paint_art(w, canvas, "classic_light_track_inset", 138, 37);
    paint_round_rows(canvas, cell, &scheme()->sel, 6);
    paint_art(w, canvas, "classic_light_lit_edge", cell->x - 1, 36);
    draw_centred(canvas, off, 3, &left, 21, white);
    draw_centred(canvas, on, 2, &right, 21, white);
}
/* The page at (0, y, 375, 294): flat in the half's Home pane colour (theme home_pane), final. */
void paint_page(void *w, void *canvas, int y) {
    (void)w;
    bar_rect(canvas, RGBA(theme()->home_pane[ipod_light()].rgb[0]), 0, y, 375, 294);
}
/* A nul-ended string centred in r in face (0: the system font) at px, in a final 0xAARRGGBB, never
 * flipped. Text colour and alignment are restored, the font is not. */
void final_text(void *canvas, const char *face, const unsigned *s, const void *r, unsigned px,
                unsigned color) {
    unsigned n = wide_len(s), raw = st.raw_ink;
    st.raw_ink = 1;
    if (n)
        draw_in_face(canvas, face, s, n, r, px,
                     (RGBA(color & 0xffffffu) & 0xffffffu) | (color & 0xff000000u));
    st.raw_ink = raw;
}
/* A key for paint_dark_bar: a battery label's "88%", charging as the status bar's icon says. */
unsigned dark_bar_key(const unsigned *t) {
    const char *icon = st.bar_icon ? widget_get_prop_str(st.bar_icon, "image", "") : "";
    unsigned level = 0;
    while (t && *t >= '0' && *t <= '9') level = level * 10 + (*t++ - '0');
    return (level > 100 ? 100 : level) << 3 | (unsigned)(icon && !tk_strcmp(icon, "bar_charge"))
                                                  << 1;
}
/* The dark bar's rows and its line, the last of h rows from y: the scheme's dark half in either
 * half, over the bar's own line, with no second one; a flat Status Bar's plain black. Final
 * colours. */
void dark_bar_rows(void *canvas, int y, int w, int h) {
    const theme_t *T = bar_theme();
    const scheme_t *S = &T->schemes[*T->accent_slot];
    if (T->painter[PAINT_BAR] != BAR_GLOSS) {
        bar_rect(canvas, RGBA(BAR_COLOR), 0, y, w, h);
        return;
    }
    paint_stops(canvas, 0, y, w, h - 1, &S->bar);
    bar_rect(canvas, RGBA(S->bar_line), 0, y + h - 1, w, 1);
}
/* The dark bar on a full-screen window, which the system bar doesn't paint over: its rows and line,
 * a title if it has one, the play state and the battery by dark_bar_key's key. Final colours; the
 * fill is restored. */
void paint_dark_bar(void *w, void *canvas, const unsigned *title, unsigned key) {
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    int status = bar_status();
    dark_bar_rows(canvas, 0, 375, 26);
    if (title) { /* as the real bar's dark_title */
        int px = theme()->font_px[ROLE_BAR];
        const char *face = theme_face(title, -1, &px);
        draw_in_face(canvas, face, title, wide_len(title), &(rect_t){ 0, dark_title_y(), 375, 25 },
                     (unsigned)px, RGBA(flip_if(ipod_light(), 0xffffff)));
    }
    dark_battery(canvas, 285, 1, key);
    if (status == 2 || status == 3)
        paint_art(w, canvas, status == 2 ? "classic_light_play_w" : "classic_light_pause_w", 278,
                  7);
    stock_canvas_set_fill_color(canvas, fill);
}
/* The info screen's dark tray: its rows, top light, ring and the light row under the ring. */
void paint_tray(void *canvas, int x, int y, int w, int h) {
    static const stops_t rows = { 5,
                                  { 0, 7, 49, 50, 100 },
                                  { 0x474e58, 0x2b3038, 0x15181d, 0x090b0e, 0x14161b } };
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    rect_t r = { x, y, w, h }, ring = { x - 1, y - 1, w + 2, h + 2 };
    round_ring(canvas, &ring, 10, 0xa6000000u);
    row_below(canvas, &ring, 10, 0x26ffffffu);
    paint_round_rows(canvas, &r, &rows, 9);
    round_row(canvas, &r, 9, 0, 0x47ffffffu);
    stock_canvas_set_fill_color(canvas, fill);
}
/* Minutes since midnight now, or -1 when the clock can't be read. */
int clock_minutes(void) {
    int buf[11];
    const int *tm = clock_now(buf);
    return tm ? tm[2] * 60 + tm[1] : -1;
}

/* USB Storage's page, every theme: the computer's eject (SCSI START STOP UNIT, LoEj) closes the
 * card's LUN in the kernel's f_mass_storage, and its configfs file then reads empty. Once it has
 * read open, empty is the eject: the heading (Classic) or stock's line (scrlabel_info, which
 * stock's timer rewrites "USB Connected" every 500 ms, so a label of its style covers it) says OK
 * to Disconnect. No file (the gadget not built yet, or torn down) is no eject. The LUN stays closed
 * until stock rebuilds the gadget, so the poll ends there.
 * ponytail: only a host that sends LoEj counts (macOS, Linux, Explorer's Eject); Windows' Safely
 * Remove Hardware may only stop the device, leaving the page on Connected. The upgrade is
 * telling that apart from a cable still in (the gadget's suspend state, which configfs doesn't
 * show). */
#define USB_LUN "/sys/kernel/config/usb_gadget/storage_demo/functions/mass_storage.msg/lun.0/file"
static int usb_poll(const void *info) {
    void *win = P(info, TIMER_CTX), *c;
    char path[2];
    void *f = fopen(USB_LUN, "rb");
    int n = f ? (int)fread(path, 1, sizeof path, f) : -1;
    if (f) fclose(f);
    if (n > 0) st.usb_lun = 1;
    if (n || !st.usb_lun) return 8; /* RET_REPEAT */
    if ((c = widget_lookup(win, "msg_heading", 1))) {
        widget_set_text_utf8(c, "OK to Disconnect");
        widget_set_text_utf8(widget_lookup(win, "msg_line", 1), "");
    } else if ((c = widget_lookup(win, "scrlabel_info", 1))) {
        void *ok = hscroll_label_create(P(c, W_PARENT), I(c, W_X), I(c, W_Y), I(c, W_W), I(c, W_H));
        widget_use_style(ok, "s_scrlabel_white24c");
        widget_set_text_utf8(ok, "OK to Disconnect");
        widget_set_visible(c, 0, 0);
    }
    widget_invalidate_force(win, (void *)0);
    return 7; /* RET_REMOVE */
}

void *ringnav_ui_load(const char *name, void *parent) {
    char path[64]; /* this call's own: a window stock loads inside this one keeps its name */
    /* application_init's first window; accent() makes theme() final */
    if (name && !st.theme_booted && !tk_strcmp(name, "system_bar")) {
        st.theme_booted = 1;
        accent();
        theme_boot();
    }
    int np = name && !tk_strcmp(name, "playing_page");
    if (np) name = np_pages[np_layout()];
    void *root = stock_ui_load_trampoline(ui_redirect(name, path), parent);
    /* the two small dialogs stock leaves dimming what's under them; its others clear it themselves
     */
    if (root && dialogs_painted() &&
        (!tk_strcmp(widget_get_prop_str(root, "name", ""), "showsn_dialog") ||
         !tk_strcmp(widget_get_prop_str(root, "name", ""), "checkfw_dialog")))
        widget_set_prop_str(root, "highlight", "");
    if (root && slider_page(root)) slider_open(root); /* every theme's: no knob; Classic's page */
    if (root && classic_pages()) {
        const char *loaded = widget_get_prop_str(root, "name", "");
        msg_open(root);
        if (!tk_strcmp(loaded, "search_dialog")) search_open(root);
        if (!tk_strcmp(loaded, "kb_default_t9")) kb_open(root);
        if (!tk_strcmp(loaded, "addplaylist_dialog") || !tk_strcmp(loaded, "renameplaylist_dialog"))
            pl_box_open(root);
        if (time_form(root)) time_open(root);
    }
    if (root && g_usbmode != 1 && !tk_strcmp(widget_get_prop_str(root, "name", ""), "usbmode_page"))
        st.usb_lun = 0, widget_add_timer(root, usb_poll, 500);
    if (root) motion_load(root, np);
    return root;
}

/* bigcover_page_init: stock's full-screen art (a tap on Now Playing's opens it, any tap closes it)
 * shows the song name BC_MS, then bc_fade fades it out. Stock's own timer writes the next song's
 * name; ringnav_sleep shows it again when the track changes. Enlarged Album Art's countdown opens
 * it too; off the art page (lyrics, info, visualizer) there is no art to enlarge, so ringnav_sleep
 * holds the countdown full there (#57). Closing it after the open flashed it every 10 s (#153). */
int ringnav_bigcover(void *win, void *ctx) {
    int result = stock_bigcover_trampoline(win, ctx);
    if (!win) return result;
    st.bc[0] = widget_lookup(win, "scrlabel_title", 1);
    st.bc[1] = widget_lookup(win, "view_shade", 1);
    st.bc[2] = win;
    st.bc_key = 0, st.bc_shape = 1 << 16 | 1;
    bc_show();
    widget_on(win, EVT_DESTROY, bc_gone, win);
    return result;
}

/* Both native album constructors use 110px square covers, with playing overlays either
 * beside the cover or nested inside it. Keep their local origins together when padding the tile.
 * Every theme insets the cover, so the selection card frames it rather than hiding under it. */
static void album_children(void *w, int nested) {
    int pad = TILE_INSET + TILE_CLEARANCE, size = 110 - 2 * pad;
    for (unsigned i = 0; i < widget_count_children(w); ++i) {
        void *c = widget_get_child(w, i);
        int x = I(c, W_X), y = I(c, W_Y), cw = I(c, W_W), h = I(c, W_H);
        if ((cw == 110 || cw == size) && cw == h) {
            album_children(c, 1);
            x = y = nested ? 0 : pad;
            cw = h = size;
            if (!tk_strcmp(widget_get_type(c), "image") &&
                widget_get_prop_int(c, "draw_type", 0) != IMAGE_DRAW_SCALE_DOWN)
                image_set_draw_type(c, IMAGE_DRAW_SCALE_DOWN);
        } else if (!nested && !tk_strcmp(widget_get_type(c), "hscroll_label")) {
            if (x != pad) y -= TILE_INSET;
            x = pad;
            cw = size;
        } else
            continue;
        if (x != I(c, W_X) || y != I(c, W_Y) || cw != I(c, W_W) || h != I(c, W_H))
            widget_move_resize(c, x, y, cw, h);
    }
}

static void album_tile(void *w) {
    if (!w || I(w, W_W) != 110 || I(w, W_H) != 160 || tk_strcmp(widget_get_type(w), "button"))
        return;
    void *win = window_of(w);
    const char *name = win ? widget_get_prop_str(win, "name", "") : "";
    if (!tk_strcmp(name, "album_page") || !tk_strcmp(name, "artistinfo_page")) album_children(w, 0);
}

/* 1 if name is in names, NUL-separated and ended by an empty name. */
static int named_in(const char *names, const char *name) {
    for (const char *n = names; *n;) {
        if (!tk_strcmp(name, n)) return 1;
        while (*n++) {}
    }
    return 0;
}
/* 1 if an active control's white glyph turns CONFIRM_SURFACE on tone, 0xRRGGBB: perceived
 * brightness over GLYPH_LIGHT_MAX (Graphite's silver). */
static int bright_tone(unsigned tone) {
    return ((tone >> 16) * 299 + (tone >> 8 & 255) * 587 + (tone & 255) * 114) / 1000 >
           GLYPH_LIGHT_MAX;
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
static void quick_fill(void *w) {
    if (!quick_slider(w)) return;
    /* Classic's bar is quick_scrub's: stock's goes clear, and 1 says it went */
    int clear = classic_pages();
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
static void quick_page(void *win, void *canvas) {
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
static void quick_scrub(void *w, void *canvas) {
    static const stops_t track = { 2, { 0, 100 }, { 0x0a0a0a, 0x3a3c40 } };
    static const stops_t blue = { 3, { 0, 50, 100 }, { 0x8cc4f8, 0x2f85df, 0x165fc0 } };
    if (!classic_pages() || !quick_slider(w) || !P(canvas, CANVAS_LCD)) return;
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
/* The confirm pop-up's prompt (stock's own child, "Shut Down?"): CONFIRM_PROMPT_PX, centred in the
 * space above its rows, or above Delete File's "Also delete source file" (stock's check_button
 * msg_delcheckbutton at y 124, centred 20px), which keeps its stock place and style.
 * ponytail: by position, any other non-button child above CONFIRM_TOP. */
static void confirm_prompt(void *w, void *canvas) {
    if (tk_strcmp(widget_get_prop_str(w, "name", ""), "confirminfo_dialog")) return;
    int form = confirm_form(w);
    if (form) {
        confirm_dress(w, canvas, form);
        return;
    }
    void *check = widget_lookup(w, "msg_delcheckbutton", 0);
    int bottom = check ? I(check, W_Y) : CONFIRM_TOP;
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) {
        void *c = widget_get_child(w, i);
        if (c == check || !tk_strcmp(widget_get_type(c), "button") ||
            I(c, W_Y) + I(c, W_H) > CONFIRM_TOP ||
            (!I(c, W_X) && !I(c, W_Y) && I(c, W_W) == I(w, W_W)))
            continue;
        widget_move_resize(c, 0, 0, I(w, W_W), bottom);
        widget_set_prop_int(c, "style:normal:font_size", CONFIRM_PROMPT_PX);
        widget_set_prop_str(c, "style:normal:text_align_h", "center");
        widget_set_prop_str(c, "style:normal:text_align_v", "middle");
    }
}
/* A list row's touch target: its own (page rows), else a button over its top (the Library's,
 * Settings', the More menu's); pressed with its grey (ipod.json's pressed #333333) or none. */
static int pressed_row(void *b) {
    const char *s = widget_get_prop_str(b, "style", "");
    return !tk_strcmp(widget_get_prop_str(b, "state_for_style", ""), "pressed") &&
           (!tk_strcmp(s, "s_btn_listitem") || !tk_strcmp(s, "s_btn_forwardspeed") ||
            !tk_strcmp(s, "s_listitem_black"));
}
static void *touch_target(void *row) {
    void *b = widget_count_children(row) ? widget_get_child(row, 0) : (void *)0;
    return b && !tk_strcmp(widget_get_type(b), "button") && !I(b, W_Y) ? b : row;
}
/* A button under half its row's width is a tile (the album grid's, the playlist page's pair), as
 * paint_selection tells a tile from a row. */
static int tile_in(void *b, void *row) { return 2 * I(b, W_W) < I(row, W_W); }
static int press_repaint(const void *info) {
    st.press_timer = 0;
    void *top = window_manager_get_top_window(window_manager());
    if (top) widget_invalidate_force(top, (void *)0);
    return 0;
}

/* A touched list row's grey is the selection's shape: the whole row, as Modern's bar (the
 * Library's button leaves x 0-20, 355-375 and the pitch's last 4 px), or under Rounded Corners the
 * inset pill. Drawn as the row's background; the button's own box is skipped (1). The press
 * invalidates the whole surface (ringnav_touch), its release only the button, so the row's edges
 * repaint once after it (press_repaint). A tile's grey is its selection card, inside the tile. */
static int pressed_pill(void *w, void *canvas) {
    if (!w) return 0;
    if (!is_row(w)) {
        void *row = P(w, W_PARENT);
        if (!is_row(row) || !pressed_row(w)) return 0;
        if (!tile_in(w, row)) /* the row drew it: skip its button's box */
            return touch_target(row) == w && I(row, W_W) > 2 * SEL_INSET + I(row, W_H);
        rect_t card = { TILE_INSET, TILE_INSET, I(w, W_W) - 2 * TILE_INSET,
                        I(w, W_H) - 2 * TILE_INSET };
        fill_box(canvas, &card, PH_GREY, setting(SETTING_ROUNDED) ? SEL_RADIUS : 0);
        return 1;
    }
    void *b = touch_target(w);
    /* childless: not a row but a page, such as Now Playing's visualizer; a tile draws its own */
    if (!pressed_row(b) || tile_in(b, w) || !widget_count_children(w) ||
        I(w, W_W) <= 2 * SEL_INSET + I(w, W_H)) {
        if (w == st.press_row) {
            st.press_row = (void *)0;
            rearm(&st.press_timer, press_repaint, 0);
        }
        return 0;
    }
    st.press_row = w;
    int in = setting(SETTING_ROUNDED) ? SEL_INSET : 0;
    rect_t pill = { in, 0, I(w, W_W) - 2 * in, I(w, W_H) };
    fill_box(canvas, &pill, PH_GREY, in ? pill_radius(b, pill.h, SEL_INSET) : 0);
    return b == w; /* a page row's own (clear) background */
}

/* A payload page's caption, 48 px or page_title's: a child of win, or the first in one of its
 * children (Games' list comes after its sheet). The one on show, else the first: a hidden caption
 * still names the page, and Games keeps its name over a game. */
static void *page_caption(void *win) {
    void *first = 0;
    for (unsigned i = 0, n = widget_count_children(win); i < n; ++i) {
        void *c = widget_get_child(win, i);
        int shown = widget_get_visible(c);
        if (widget_count_children(c) && tk_strcmp(widget_get_type(c), "hscroll_label"))
            c = widget_get_child(c, 0);
        if (tk_strcmp(widget_get_type(c), "hscroll_label") ||
            (I(c, W_H) != 48 && tk_strcmp(widget_get_prop_str(c, "name", ""), PAGE_TITLE)))
            continue;
        if (shown) return c;
        if (!first) first = c;
    }
    return first;
}

/* The bar title for window win: Now Playing, Coverflow and EQ by name; else 0, and *label is
 * a stock page's (hidden) navbar title, else a payload page's 48 px caption row (page_title). */
static const char *bar_title_of(void *win, void **label) {
    const char *name = widget_get_prop_str(win, "name", "");
    if (tk_str_start_with(name, "playing_page")) return "Now Playing";
    if (!tk_strcmp(name, "coverflow_page")) return "Cover Flow";
    if (!tk_strcmp(name, "equalizer_page")) return "EQ"; /* its 48 px row is the tabs' own label */
    if (!tk_strcmp(name, "spotify_page")) return "Spotify"; /* they hide their own name's label */
    if (!tk_strcmp(name, "radionp_page")) return "Internet Radio";
    if (!tk_strcmp(name, "usbmode_page")) return "Q2 Pod";
    if (!tk_strcmp(name, "search_dialog")) return "Search"; /* stock's toolbar has no label */
    void *nav = widget_lookup(win, "view_navbar", 1);
    if (nav) {
        for (int i = 0, k = widget_count_children(nav); i < k && !*label; ++i) {
            void *c = widget_get_child(nav, i);
            const char *t = widget_get_type(c);
            if (!tk_strcmp(t, "hscroll_label") || !tk_strcmp(t, "label")) *label = c;
        }
    } else {
        *label = page_caption(win);
    }
    return 0;
}

/* Either of st.title_win and st.title_lbl going (page_list rebuilds a caption under a live window)
 * drops both, so the next paint finds them again. */
int title_gone(void *w, void *event) {
    if (st.title_win) widget_off_by_func(st.title_win, EVT_DESTROY, title_gone, st.title_win);
    if (st.title_lbl) widget_off_by_func(st.title_lbl, EVT_DESTROY, title_gone, st.title_lbl);
    st.title_win = st.title_lbl = st.action_view = (void *)0;
    return 0;
}

/* Songs' action bar as a plain row (a set's action_row, its view tools/ipod.py sizes). Stock
 * rebuilds its children on each refresh (0x4a9e78): the label first, then Play All's image and
 * maybe Sort's, each bound as it is made. They stay as hit areas: the label at the list's text x in
 * the row's face and size, Play All's image over the row's left without its icon, Sort's three bars
 * (tools/art.py) over the right. Their first child's height says they are done. */
static void action_row(void *view) {
    int h = geom_val(V_ACTION_ROW), n = widget_count_children(view);
    if (n < 2 || I(widget_get_child(view, 0), W_H) == h) return;
    void *label = widget_get_child(view, 0), *play = widget_get_child(view, 1);
    widget_move_resize(label, geom_val(V_TEXT_X), 0, 280, h);
    theme_font(label, ROLE_TITLE, FONT_ROW);
    widget_use_style(play, "default");
    widget_move_resize(play, 0, 0, 300, h);
    if (n < 3) return; /* Recently Added and the like have no Sort */
    void *sort = widget_get_child(view, 2);
    widget_use_style(sort, "default");
    image_base_set_image(sort, "classic_light_sort_bars");
    image_set_draw_type(sort, IMAGE_DRAW_CENTER);
    widget_move_resize(sort, 301, 0, 48, h);
}

static int title_due(const void *info) {
    (void)info;
    widget_invalidate_force(st.bar_title, (void *)0);
    return 0;
}

/* Classic's bar title (label_title, only its set's bar has one) names the top window win,
 * Home by the list it shows; a dialog keeps the window's title. The title's source is found once
 * per top window; after that a paint compares only that label's text with the title's (none for a
 * title by name), and the title is written only when it changes, with the face that has its
 * characters (stock draws a character its face lacks as nothing). */
void bar_title_sync(void *win) {
    /* a page under the top window that writes its caption would flip the bar with each paint */
    if (!st.bar_title || !win || win != window_manager_get_top_window(window_manager())) return;
    static const unsigned none = 0;
    char s[256], now[256]; /* stock writes nothing unless all of it fits */
    const char *t = home_menu(win) ? coverflow_home_title() : (const char *)0;
    if (t)
        ;
    else if (win != st.title_win) {
        title_gone(0, 0);
        st.title_win = win;
        widget_on(win, EVT_DESTROY, title_gone, win);
        if (tk_strcmp(widget_get_type(win), "window") && win != st.search_win) return;
        t = bar_title_of(win, &st.title_lbl);
        /* a page may have Songs' action bar: only a set with action_row looks */
        if (geom_val(V_ACTION_ROW)) st.action_view = widget_lookup(win, "view_navbar_allplay", 0);
        if (st.title_lbl) widget_on(st.title_lbl, EVT_DESTROY, title_gone, st.title_lbl);
    } else {
        if (!st.title_lbl) return;
        const unsigned *a = widget_get_text(st.title_lbl) ?: &none,
                       *b = widget_get_text(st.bar_title) ?: &none;
        while (*a && *a == *b) ++a, ++b;
        if (*a == *b) return;
    }
    if (!t) {
        *s = 0;
        if (st.title_lbl) widget_get_text_utf8(st.title_lbl, s, sizeof s);
        t = !tk_strcmp(s, "Channel Balance") ? "Balance" /* English's only */
            : !tk_strcmp(s, "(null)")        ? ""        /* stock's "%s" of a NULL name: no title */
                                             : s;
    }
    if (widget_get_text_utf8(st.bar_title, now, sizeof now) || tk_strcmp(now, t)) {
        widget_set_text_utf8(st.bar_title, t);
        if (st.bar_dark)
            dark_title(st.bar_title);
        else
            theme_font(st.bar_title, ROLE_BAR, FONT_ROW);
        /* this runs mid-paint, where an invalidate is lost and the bar then redraws only the
         * clock's rect: a page's new caption under the same window would wait for a window's change
         */
        timer_add(title_due, (void *)0, 0);
    }
}

/* A page_title's caption was an empty page's message, and the bar holds captions: the message stays
 * the first line, and the bar reads name, the page's, from the hidden label. */
void page_empty(void *body, const char *name) {
    if (theme()->page_title_h) return;
    char message[64];
    void *caption = widget_get_child(body, 0);
    if (widget_get_text_utf8(caption, message, sizeof message)) return;
    widget_set_name(page_title(body, message, 0), "");
    widget_set_text_utf8(caption, name);
    widget_set_visible(caption, 0, 0);
    bar_title_sync(window_manager_get_top_window(window_manager()));
}

/* Whether label's text fits the bar's title box: measured on the bar's canvas in the face and size
 * the title would take, against the box less hscroll_label's two 2 px margins. A bar not found yet
 * fits nothing. */
int bar_title_fits(void *label) {
    void *canvas = st.bar_title ? widget_get_canvas(st.bar_title) : (void *)0;
    const unsigned *s = widget_get_text(label);
    unsigned n = wide_len(s);
    if (!canvas) return 0;
    int px = theme()->font_px[ROLE_BAR];
    const char *face = theme_face(s, (int)n, &px);
    canvas_set_font(canvas, face, px);
    return canvas_measure_text(canvas, s, n) <= I(st.bar_title, W_W) - 4;
}

/* Stock paints a widget's background before its children, so the bar sits behind the rows.
 * The selection work for the surface happens here, once per frame, instead of in the border hook;
 * a BUTTONS dialog is itself top-level. Home's art is clipped to its panel until the border hook.
 * Other top-level widgets are the status bar, which gets its solid fill, and the windows. Painting
 * the top window or the bar keeps the bar's clock, Home's art and Now Playing's labels current;
 * Home's art is also checked when Home is painted under another window. */
int ringnav_paint_bg(void *w, void *canvas) {
    st.paint_w = w;
    quick_fill(w);
    confirm_prompt(w, canvas);
    toast_paint(w, canvas);
    album_tile(w);
    int result = 0;
    if (kb_key(w))
        kb_paint_key(w, canvas);
    else if (search_del(w) == 1)
        search_x(w, canvas);
    else if (!pressed_pill(w, canvas))
        result = stock_paint_bg_trampoline(w, canvas);
    void *wm = window_manager(), *bar = *(void *const *)system_bar;
    int form = w ? confirm_form(w) : FORM_NONE;
    if (form) {
        confirm_track(w, canvas, form);
        confirm_check(w, canvas);
    }
    hold_paint(w, canvas, 0);
    if (w && w == st.search_tabs) search_tabs_paint(w, canvas);
    paint_selection(w, canvas);
    if (form) confirm_labels(w, canvas);
    if (w && w == st.rate_box)
        paint_panel(w, canvas, &(rect_t){ PANEL_PAD, PANEL_PAD - PANEL_DROP, 304, 110 },
                    "classic_light_rate_shadow", 1);
    paint_separators(w, canvas);
    coverflow_home_clip(w, canvas, 1);
    if (w && w == st.bar_slot) paint_battery(w, canvas);
    if (w && w == st.action_view && P(canvas, CANVAS_LCD)) { /* Play All's row: the same lines */
        const stops_t *lines = &theme()->sep[ipod_light()];
        unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
        paint_stops(canvas, 0, I(w, W_H) - lines->n, I(w, W_W), lines->n, lines);
        stock_canvas_set_fill_color(canvas, fill);
    }
    if (!w || P(w, W_PARENT) != wm) return result;
    if (w == st.slider_win) { /* one sliding out too */
        if (st.slider_w)
            slider_paint(w, canvas);
        else
            time_paint(w, canvas);
    }
    if (classic_pages() && P(canvas, CANVAS_LCD) &&
        !tk_strcmp(widget_get_prop_str(w, "name", ""), "statusbar_dialog"))
        quick_page(w, canvas);
    if (w == st.msg_win || w == st.usb_win) msg_paint(w, canvas);
    if (w == st.search_win) search_paint(w, canvas);
    if (w && w == st.pl_box) pl_box_paint(w, canvas);
    if (w == st.t9_page && P(canvas, CANVAS_LCD)) {
        unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
        dark_bar_rows(canvas, 0, 375, 34);
        paint_art(w, canvas, "classic_light_kb_back", 42, 4);
        paint_field(w, canvas, 72, 1, 244);
        stock_canvas_set_fill_color(canvas, fill);
    }
    if (w == st.kb_win && P(canvas, CANVAS_LCD)) {
        unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
        paint_ramp(canvas, 60, 320, 60);
        stock_canvas_set_fill_color(canvas, fill);
    }
    if (w == bar && P(canvas, CANVAS_LCD)) {
        unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
        const theme_t *T = bar_theme();
        const scheme_t *S = bar_scheme();
        if (st.bar_dark) {
            dark_bar_rows(canvas, 0, I(w, W_W), 26);
        } else if (T->painter[PAINT_BAR] ==
                   BAR_GLOSS) { /* Classic: the gloss rows over the line, final */
            int h = I(w, W_H) - 1;
            paint_stops(canvas, 0, 0, I(w, W_W), h, &S->bar);
            if (S->bar_line2 && ipod_light()) {
                stock_canvas_set_fill_color(canvas, RGBA(S->bar_line2));
                canvas_fill_rect(canvas, 0, h - 1, I(w, W_W), 1);
            }
            stock_canvas_set_fill_color(canvas, RGBA(S->bar_line));
            canvas_fill_rect(canvas, 0, h, I(w, W_W), 1);
        } else {
            /* Modern in Light Mode: an iPod's grey bar (its scheme's bar_light), flat (final
             * colour) */
            int grey = S->bar_light && ipod_light();
            stock_canvas_set_fill_color(canvas,
                                        grey ? RGBA(S->bar_light) : ui_ink(RGBA(BAR_COLOR)));
            canvas_fill_rect(canvas, 0, 0, I(w, W_W), I(w, W_H));
        }
        stock_canvas_set_fill_color(canvas, fill);
    }
    void *top = window_manager_get_top_window(wm);
    /* Any painted window, not only the top one: Home slides back in from a snapshot. */
    coverflow_home_art(w == bar ? top : w);
    if (w == st.bc[2]) bc_fit(w);
    if (bar && (w == bar || w == top)) {
        if (!st.clock_timer || (g_time24h_flag != 0) != st.clock_full) { /* not mid-paint */
            st.clock_full = g_time24h_flag != 0;
            rearm(&st.clock_timer, clock_due, 0);
        }
        bar_sync(bar);
        bar_title_sync(top);
        bar_title(bar, top);
        if (st.action_view) action_row(st.action_view);
        if (np_sync(top) && np_beside()) rearm(&st.np_remain_timer, remain_due, 0); /* mid-paint */
        if (w == top) vol_paint(top, canvas);
        /* Boot may paint Home before the screen is usable, so nothing chose or drew its first
         * row. Once, when the list is first reachable, repaint it. */
        void *list = st.greeted ? (void *)0 : surface((void *)0, (void *)0);
        if (list) {
            st.greeted = 1;
            widget_invalidate_force(list, (void *)0);
        }
    }
    return result;
}

/* Live accent (docs/internals.md#accent). Colors are mapped as style_get_color returns them: text
 * (text_color, highlight_text_color) to the red tone, fills and borders to the light tone. The name
 * is only compared for a color that maps. */
/* Resolve ancestry during painting: rebinding a pooled row never leaves a color override behind. */
int painting_in(void *row) {
    for (void *w = st.paint_w; row && w; w = P(w, W_PARENT)) {
        if (w == row) return 1;
        if (w == st.sel_w) break;
    }
    return 0;
}
static int selected_ink(void) { return painting_in(st.sel_widget); }

unsigned *ringnav_style_color(unsigned *color, void *style, const char *name, unsigned fallback) {
    /* A mutable style (inline style:*) answers from its default style through style_get_color
     * again: only the outermost call maps, as Light Mode's flip is not idempotent. */
    ++st.style_depth;
    stock_color_trampoline(color, style, name, fallback);
    if (--st.style_depth) return color;
    /* Text inputs' selected text (an edit's T9 pending letter) is stock near-black on their light
     * blue selected_bg_color, which Light Mode keeps: it stays, not flipped to paper. */
    if (name && !tk_strcmp(name, "selected_text_color")) return color;
    /* Classic: an edit's hint with no colour of its own is stock's default, its text colour,
     * already mapped (Minimal and Modern keep main's second mapping) */
    if (name && *color == fallback && !tk_strcmp(name, "tips_text_color") && classic_pages())
        return color;
    const scheme_t *S = scheme();
    const theme_t *T = theme();
    if (T->sb_w && st.paint_w && name &&
        (tk_str_end_with(name, "fg_color") || tk_str_end_with(name, "bg_color")) &&
        !tk_strcmp(widget_get_type(st.paint_w), "scroll_bar_m")) {
        *color &= 0xffffff; /* paint_scrollbar draws it */
        return color;
    }
    if (st.paint_w && name && tk_str_end_with(name, "text_color")) {
        if (kb_key(st.paint_w)) { /* kb_paint_key draws it */
            *color &= 0xffffff;
            return color;
        }
        if ((T->caps_mark && st.paint_w == st.bar_idle) ||
            (T->caps_home && coverflow_home_owns(st.paint_w))) {
            *color &= 0xffffff; /* paint_caps draws it */
            return color;
        }
        const char *label = widget_get_prop_str(st.paint_w, "name", "");
        if (!tk_strcmp(label, "label_del") || !tk_strcmp(label, "label_rename")) {
            int enabled = 1;
            for (void *w = P(st.paint_w, W_PARENT); w; w = P(w, W_PARENT))
                if (!widget_get_prop_bool(w, "enable", 1)) enabled = 0;
            *color = (*color & 0xff000000u) | (enabled ? 0xffffff : 0xaaaaaa);
        }
        if ((ipod_light() || S->sel_text) && painting_in(st.sel_accent)) {
            if (S->sel_text) *color = (*color & 0xff000000u) | (RGBA(S->sel_text) & 0xffffff);
            return color;
        }
        int selected = selected_ink();
        if (selected && (st.sel_home || S->flags & SCHEME_CARD)) {
            unsigned alpha = *color & 0xff000000u;
            *color =
                alpha |
                (st.sel_home ? 0xffffff : ((*color & 0xffffff) == 0xffffff ? 0x171717 : 0x484848));
            *color = ui_ink(*color);
            return color;
        }
        if (st.sel_home && st.sel_w) {
            for (void *w = st.paint_w; w; w = P(w, W_PARENT))
                if (w == st.sel_w) {
                    *color = ui_ink((*color & 0xff000000u) | 0xaaaaaa);
                    return color;
                }
        }
        /* Minimal's secondary text is Sudo's quieter grey, but stock's on an accent's card. */
        if (S->muted && !selected && (*color & 0xffffff) == 0xaaaaaa)
            *color = (*color & 0xff000000u) | S->muted;
    }
    int a = accent();
    if (S->flags & SCHEME_RAW_RED && !ipod_light()) return color;
    unsigned c = accent_map(*color, a, TONE_LIGHT);
    if (c != *color && name && tk_str_end_with(name, "text_color"))
        c = accent_map(*color, a, TONE_RED);
    *color = c;
    return color;
}

/* Backgrounds come as gradients: the whole stock leaf (null style or vtable: none), then the stops
 * of the caller's gradient_t (nr @8, 8-byte {color, offset} stops @0xc, at most 8). style_get_color
 * asks here first for every color, text included, so its own call stays unmapped and the color
 * hook picks the tone. */
void *ringnav_style_gradient(void *style, const char *name, void *out) {
    void *vt = style ? P(style, 0) : (void *)0;
    void *(*get)(void *, const char *, void *) =
        vt ? (void *(*)(void *, const char *, void *))P(vt, 0x18) : (void *)0;
    ++st.style_depth;
    void *g = get ? get(style, name, out) : (void *)0;
    int own = --st.style_depth || __builtin_return_address(0) == (void *)STYLE_COLOR_GRADIENT_RET;
    int selected_bg = name && !tk_strcmp(name, "bg_color") && selected_ink();
    for (int i = 0; !own && g && g == out && i < I(g, 8) && i < 8; ++i)
        I(g, 0xc + 8 * i) =
            selected_bg ? 0 : (int)accent_map((unsigned)I(g, 0xc + 8 * i), accent(), TONE_LIGHT);
    return g;
}

/* 1 if name is one of the settings rows' category icons (SETTINGS_ICON_NAMES in stock.h, from
 * ipod.json): their red is a category colour, like the purple and orange ones, not an accent. */
static int settings_icon(const char *name) { return named_in(SETTINGS_ICON_NAMES, name); }

/* Decoded theme images are mapped once, before the image manager caches them. Only a plain asset
 * name is the theme's: covers by path or URL (a '/' or ':') never are. Minimal also greys built-in
 * navigation/control icons; category discs keep white glyphs, warning batteries keep their colours.
 * The confirm pop-up's discs (CONFIRM_IMAGE*) take the dark CONFIRM_SURFACE under every accent,
 * Crimson included, so their white glyphs stay legible. The quick settings' active controls
 * (DROPDOWN_IMAGE*, not the brightness suns) take the accent's red tone like everything else red,
 * but their white glyph turns CONFIRM_SURFACE on a tone brighter than GLYPH_LIGHT_MAX (Graphite's
 * silver), so an active disc stands apart from the grey inactive ones and its glyph stays legible.
 * Any other red under white (a switch's knob, a disc's glyph, a BUTTON_IMAGE*'s label) is a surface
 * and takes the light tone, which keeps the white legible; red marks on their own keep the red
 * tone. bitmap_t: w @0, h @4, format @0xe; the 32-bit formats 1-4 hold r, g, b at these byte
 * offsets. */
/* Clamp straight alpha to the arc coverage: reusing an already rounded bitmap is harmless. */
static void round_corners(unsigned char *px, int w, int h, int stride, int alpha) {
    int r = (w < h ? w : h) * NP_ART_RADIUS / 166;
    for (int y = 0; y < r; ++y) {
        int out = arc_out(r, y);
        for (int x = 0; x <= out / 16; ++x) {
            unsigned coverage = x < out / 16 ? 0 : (unsigned)(16 - (out & 15)) * 255 / 16;
            for (int c = 0; c < 4; ++c) {
                unsigned char *a =
                    px + (c & 1 ? h - 1 - y : y) * stride + (c & 2 ? w - 1 - x : x) * 4 + alpha;
                if (*a > coverage) *a = (unsigned char)coverage;
            }
        }
    }
}

/* Stock's disc placeholders where the art follows Corners (ipod_square_art): Now Playing's, the
 * full-screen art's, Cover Flow's and Spotify's (PLACEHOLDER), the album grids' and rows'. Under
 * square art their transparent rounded corners are filled with their own top edge's grey, so
 * they are square as the real covers there; the opaque flag set, as nothing shows through. */
static void square_disc(const char *name, void *bitmap, const unsigned char at[4]) {
    static const char *const discs[] = { "play_defaultcover", "default_bigcover", PLACEHOLDER,
                                         "default_album_big", "default_album_small" };
    unsigned i = 0;
    while (i < sizeof discs / sizeof *discs && tk_strcmp(name, discs[i])) ++i;
    int w = I(bitmap, 0), h = I(bitmap, 4), stride = bitmap_get_line_length(bitmap);
    unsigned char *px = i < sizeof discs / sizeof *discs && w > 0 && h > 0 && stride >= w * 4
                            ? bitmap_lock_buffer_for_write(bitmap)
                            : (void *)0;
    if (!px) return;
    const unsigned char *edge = px + 4 * (w / 2);
    if (edge[at[3]] == 255) {
        for (int y = 0; y < h; ++y)
            for (unsigned char *p = px + y * stride, *end = p + 4 * w; p < end; p += 4) {
                unsigned a = p[at[3]];
                for (int c = 0; c < 3; ++c)
                    p[at[c]] =
                        (unsigned char)((p[at[c]] * a + edge[at[c]] * (255 - a) + 127) / 255);
                p[at[3]] = 255;
            }
        *(unsigned short *)((char *)bitmap + 0xc) |= 1;
    }
    bitmap_unlock_buffer(bitmap);
}

/* Zeroes every pixel of a decoded 32-bit bitmap (bitmap_t w @0, h @4), alpha included, row padding
 * kept, and clears its opaque flag (@0xc bit 0) as round_corners' callers do: it draws nothing. */
static void clear_bitmap(void *bitmap) {
    unsigned char *px = bitmap_lock_buffer_for_write(bitmap);
    if (!px) return;
    for (int y = 0; y < I(bitmap, 4); ++y)
        memset(px + y * bitmap_get_line_length(bitmap), 0, 4 * (unsigned)I(bitmap, 0));
    *(unsigned short *)((char *)bitmap + 0xc) &= ~1u;
    bitmap_unlock_buffer(bitmap);
}

/* Scales a decoded 32-bit bitmap (at most 64 px square; alpha at byte a of each pixel) into a to x
 * to square at its middle, each pixel the alpha-weighted mean of the pixels it covers (a straight
 * or a premultiplied colour alike), the rest cleared, and clears its opaque flag. */
static void shrink_centred(void *bitmap, int to, int a) {
    static unsigned char src[64 * 64 * 4]; /* the UI thread's */
    int w = I(bitmap, 0), h = I(bitmap, 4), stride = bitmap_get_line_length(bitmap);
    int premul = *(unsigned short *)((char *)bitmap + 0xc) & BITMAP_PREMULTI;
    unsigned char *px = w <= 64 && h <= 64 && 0 < to && to < w && to < h
                            ? bitmap_lock_buffer_for_write(bitmap)
                            : (void *)0;
    if (!px) return;
    for (int y = 0; y < h; ++y) memcpy(src + 4 * w * y, px + stride * y, 4u * (unsigned)w);
    for (int y = 0; y < h; ++y)
        for (int x = 0; x < w; ++x) {
            unsigned sum[4] = { 0 }, n = 0;
            int sx = x - (w - to) / 2, sy = y - (h - to) / 2;
            if (sx >= 0 && sy >= 0 && sx < to && sy < to)
                for (int v = sy * h / to; v < (sy + 1) * h / to; ++v)
                    for (int u = sx * w / to; u < (sx + 1) * w / to; ++u, ++n)
                        for (int c = 0; c < 4; ++c) {
                            const unsigned char *p = src + 4 * (w * v + u);
                            sum[c] += c == a || premul ? p[c] : p[c] * p[a];
                        }
            unsigned char *d = px + stride * y + 4 * x;
            for (int c = 0; c < 4; ++c)
                d[c] = (unsigned char)(!n                 ? 0
                                       : c == a || premul ? sum[c] / n
                                       : sum[a]           ? sum[c] / sum[a]
                                                          : 0);
        }
    *(unsigned short *)((char *)bitmap + 0xc) &= ~1u;
    bitmap_unlock_buffer(bitmap);
}

int ringnav_image_add(void *manager, const char *name, void *bitmap) {
    static const unsigned char at[4][4] = BITMAP_RGBA_AT;
    unsigned format = bitmap ? *(unsigned short *)((char *)bitmap + 0xe) - 1u : 4,
             preset = accent();
    const theme_t *T = theme();
    const scheme_t *sc = scheme(); /* scheme preset (accent()) in the half Light Mode picks */
    unsigned char *data = (void *)0;
    /* Before the manager copies bitmap_t: cached flags must agree with the new alpha. These
     * paths are stock's album/artist thumbnails, Folder view's (.downpic/fd*, its cover
     * thread's) and a playlist's row art (playlist_art.c, pl_ and plc_, the _s.jpg ones: Cover
     * Flow insets its covers' own corners); playing covers are rounded after scaling. */
    if (format < 4 && name && !ipod_square_art() &&
        (tk_str_start_with(name, "file:///mnt/mmc/.sldp/") ||
         tk_str_start_with(name, "file:///mnt/mmc/.downpic/fd") ||
         (tk_str_start_with(name, "file:///mnt/mmc/.coverflow/pl") &&
          tk_str_end_with(name, "_s.jpg")))) {
        int w = I(bitmap, 0), h = I(bitmap, 4), stride = bitmap_get_line_length(bitmap);
        if (w > 0 && h > 0 && w <= 4096 && h <= 4096 && stride >= w * 4) {
            unsigned char *px = bitmap_lock_buffer_for_write(bitmap);
            if (px) {
                round_corners(px, w, h, stride, at[format][3]);
                *(unsigned short *)((char *)bitmap + 0xc) &= ~1u;
                bitmap_unlock_buffer(bitmap);
            }
        }
    }
    if (format < 4 && name && ipod_square_art()) square_disc(name, bitmap, at[format]);
    const char *s = name;
    int dark = s && tk_str_start_with(s, CONFIRM_IMAGE);
    int control = s && tk_str_start_with(s, DROPDOWN_IMAGE) && !tk_str_start_with(s, DROPDOWN_SUN);
    int paper = ipod_light(); /* Light Mode: tone_of's tones, flipped as drawn */
    unsigned tone = dark ? CONFIRM_SURFACE : flip_if(paper, tone_of(tones_of(preset), TONE_RED)),
             glyph = 0xffffff, light = flip_if(paper, tone_of(tones_of(preset), TONE_LIGHT));
    if (control && bright_tone(tone)) glyph = CONFIRM_SURFACE;
    while (s && *s && *s != '/' && *s != ':') ++s;
    /* Home: Carousel's cards keep stock's colours in every Theme, accent and Light Mode; its white
     * arrows (menu_left, menu_right) still flip in Light Mode, or they vanish on the paper */
    if (s && !*s && tk_str_start_with(name, "menu_") && tk_strcmp(name, "menu_left") &&
        tk_strcmp(name, "menu_right"))
        s = (void *)0;
    /* art_final, Classic Light's (tools/theme.py refuses it for another theme), and so on its
     * Status Bar under any theme: its own images (tools/art.py) are final, but for Sort's bars and
     * the volume's speakers: marks on the paper, in the half's ink */
    if (s && !*s && (T->art_final || bar_theme()->art_final) &&
        tk_str_start_with(name, "classic_light_") && tk_strcmp(name, "classic_light_sort_bars") &&
        !tk_str_start_with(name, "classic_light_vol_"))
        s = (void *)0;
    /* Minimal: the codec badges (img_flac, img_mp3, ...: red with the name cut out) take the
     * secondary text's grey, legible on the black list and on the selected row's off-white card. */
    if (s && !*s && tk_str_start_with(name, "img_") && tk_strcmp(name, "img_delete") &&
        tk_strcmp(name, "img_edit") && tk_strcmp(name, "img_morefolder") &&
        tk_strcmp(name, "img_path") && !dark && T->icons_badge_muted)
        tone = sc->muted;
    /* Minimal: the settings icons' coloured discs all turn MINIMAL_CHEVRON, like list_into's ink
     * and stock's grey list discs. The Bluetooth rows' white marks (device, refresh) and
     * list_delete (paint_radio's) take list_into's ink too under Minimal's own accent, or they
     * vanish on the selected row's off-white card; another accent's card keeps them white. A
     * pixel's saturation (max - min) against the disc's (the icon's most saturated) says how far it
     * blends towards the white glyph, so edges stay smooth; alpha kept. */
    int into = s && !*s && sc->flags & SCHEME_CARD &&
               (!tk_strcmp(name, "list_into") || !tk_strcmp(name, "bt_lefticon") ||
                !tk_strcmp(name, "bt_refresh") || !tk_strcmp(name, "list_delete"));
    int icon = s && !*s &&
               (settings_icon(name) || tk_str_start_with(name, "local_") ||
                (tk_str_start_with(name, "list_") && !into) || tk_str_start_with(name, "small_") ||
                tk_str_start_with(name, "player_"));
    int mono = s && !*s && T->icons_mono &&
               (tk_str_start_with(name, "navbar_") || tk_str_start_with(name, "home_") ||
                tk_str_start_with(name, "img_") || tk_str_start_with(name, "drop_") ||
                tk_str_start_with(name, "play_") || tk_str_start_with(name, "playlist_") ||
                tk_str_start_with(name, "stream_") || tk_str_start_with(name, "tidal_") ||
                tk_str_start_with(name, "btn_") || tk_str_start_with(name, "bt_") ||
                tk_str_start_with(name, "singer_") || tk_str_start_with(name, "song_") ||
                tk_str_start_with(name, "album_") || tk_str_start_with(name, "search") ||
                !tk_strcmp(name, "airplay") || !tk_strcmp(name, "dlna") ||
                !tk_strcmp(name, "wifitransport")) &&
               tk_strcmp(name, "play_defaultcover") && tk_strcmp(name, "playlist_default") &&
               !tk_str_start_with(name, "tidal_shan") && tk_strcmp(name, "tidal_user");
    /* Baidu's is a white square, the only list icon without a disc: cut to one, its edge a pixel
     * soft. Minimal greys it inverted below (a grey disc, the logo white), as its neighbours. */
    int square = s && !*s && !tk_strcmp(name, "list_baidu");
    unsigned char *cut = square && format < 4 ? bitmap_lock_buffer_for_write(bitmap) : (void *)0;
    if (cut) {
        int w = I(bitmap, 0), h = I(bitmap, 4), r = (w < h ? w : h) - 2; /* radius x2 */
        for (int y = 0; y < h; ++y)
            for (int x = 0; x < w; ++x) {
                unsigned char *a = cut + y * bitmap_get_line_length(bitmap) + 4 * x + at[format][3];
                int dx = 2 * x + 1 - w, dy = 2 * y + 1 - h, d = dx * dx + dy * dy;
                int q = r - 2; /* sqrt(d), in the two-pixel band only */
                while (q < r + 2 && (q + 1) * (q + 1) <= d) ++q;
                *a = (unsigned char)(*a * (r + 2 - q) / 4);
            }
        bitmap_unlock_buffer(bitmap);
    }
    unsigned char *px = (icon || into) && format < 4 && T->icons_mono
                            ? bitmap_lock_buffer_for_write(bitmap)
                            : (void *)0;
    if (px) {
        const unsigned char *o = at[format];
        int disc = 0;
        for (int pass = 0; pass < 2; ++pass)
            for (int y = 0; y < I(bitmap, 4); ++y)
                for (unsigned char *p = px + y * bitmap_get_line_length(bitmap),
                                   *end = p + 4 * I(bitmap, 0);
                     p < end; p += 4) {
                    int r = p[o[0]], g = p[o[1]], b = p[o[2]];
                    int sat = (r > g ? (r > b ? r : b) : (g > b ? g : b)) -
                              (r < g ? (r < b ? r : b) : (g < b ? g : b));
                    if (!pass) {
                        if (sat > disc) disc = sat;
                        continue;
                    }
                    if (sat > disc) sat = disc;
                    if (icon && !disc) continue; /* already grey: keep the disc/glyph contrast */
                    int ink = disc ? (255 - (sc->chevron & 255)) * sat / disc : 0;
                    p[o[0]] = p[o[1]] = p[o[2]] =
                        (unsigned char)(!icon    ? sc->chevron & 255
                                        : square ? (sc->chevron & 255) + ink
                                                 : 255 - ink);
                }
        bitmap_unlock_buffer(bitmap);
    }
    int mapped = 0; /* the accent changed a pixel: its colours are final */
    if ((!(sc->flags & SCHEME_RAW_RED) || dark || paper) && format < 4 && s && !*s &&
        !settings_icon(name) && (!T->icons_mono || (!icon && !into)))
        data = bitmap_lock_buffer_for_write(bitmap);
    if (data) {
        const unsigned char *o = at[format];
        unsigned stride = bitmap_get_line_length(bitmap);
        /* Only an active control holds red; an inactive one keeps its white glyph. */
        int red = 0, white = tk_str_start_with(name, BUTTON_IMAGE);
        int scan = control ? glyph != 0xffffff : !dark && light != tone;
        for (int y = 0; scan && !(red && white) && y < I(bitmap, 4); ++y)
            for (unsigned char *p = data + y * stride, *end = p + 4 * I(bitmap, 0); p < end;
                 p += 4) {
                unsigned c = p[o[0]] | p[o[1]] << 8 | p[o[2]] << 16;
                if (red_map(c, tone, 0xffffff, 0) != c)
                    red = 1;
                else if ((c == 0xffffff || c == 0x7f7f7f) &&
                         p[o[3]] == 255) /* or a pressed disc's */
                    white = 1;
            }
        if (!red)
            glyph = 0xffffff;
        else if (!control && white)
            tone = light;
        for (int y = 0; y < I(bitmap, 4); ++y)
            for (unsigned char *p = data + y * stride, *end = p + 4 * I(bitmap, 0); p < end;
                 p += 4) {
                unsigned c0 = p[o[0]] | p[o[1]] << 8 | p[o[2]] << 16,
                         c = red_map(c0, tone, glyph, 0);
                mapped |= c != c0;
                if (mono) {
                    unsigned grey =
                        ((c & 255) * 299 + (c >> 8 & 255) * 587 + (c >> 16) * 114) / 1000;
                    c = grey * 0x010101;
                }
                p[o[0]] = c;
                p[o[1]] = c >> 8;
                p[o[2]] = c >> 16;
            }
        bitmap_unlock_buffer(bitmap);
    }
    /* Minimal: a card app's Streaming icon (addon.c, a file:// image) greys as Streaming's own
     * stream_ icons do (mono, above), so its row matches theirs; Classic keeps its colours. */
    unsigned char *app = format < 4 && T->icons_addon_mono && addon_is_icon(name) &&
                                 I(bitmap, 0) > 0 && I(bitmap, 0) <= 4096 && I(bitmap, 4) <= 4096 &&
                                 (int)bitmap_get_line_length(bitmap) >= 4 * I(bitmap, 0)
                             ? bitmap_lock_buffer_for_write(bitmap)
                             : (void *)0;
    if (app) {
        const unsigned char *o = at[format];
        for (int y = 0; y < I(bitmap, 4); ++y)
            for (unsigned char *p = app + y * bitmap_get_line_length(bitmap),
                               *end = p + 4 * I(bitmap, 0);
                 p < end; p += 4)
                p[o[0]] = p[o[1]] = p[o[2]] =
                    (unsigned char)((p[o[0]] * 299 + p[o[1]] * 587 + p[o[2]] * 114) / 1000);
        bitmap_unlock_buffer(bitmap);
    }
    /* An icons_badge_ink theme's codec badges (Classic's monochrome marks) in the half's ink:
     * every pixel that colour, alpha kept, final. */
    int mark = -1;
    if (format < 4 && s && !*s && T->icons_badge_ink)
        for (unsigned i = 0; i < sizeof codecs / sizeof *codecs; ++i)
            if (!tk_strcmp(name, codecs[i])) mark = (int)sc->ink;
    unsigned char *one = mark >= 0 ? bitmap_lock_buffer_for_write(bitmap) : (void *)0;
    if (one) {
        const unsigned char *o = at[format];
        int premul = *(unsigned short *)((char *)bitmap + 0xc) & BITMAP_PREMULTI;
        for (int y = 0; y < I(bitmap, 4); ++y)
            for (unsigned char *p = one + y * bitmap_get_line_length(bitmap),
                               *end = p + 4 * I(bitmap, 0);
                 p < end; p += 4)
                for (int c = 0; c < 3; ++c) {
                    unsigned v = (unsigned)mark >> (16 - 8 * c) & 255;
                    p[o[c]] = (unsigned char)(premul ? v * p[o[3]] / 255 : v);
                }
        bitmap_unlock_buffer(bitmap);
        mapped = 1;
    }
    /* A chevron_sel theme (Classic): stock's grey `>` on every row is emptied; paint_into
     * draws the selected row's, white. */
    if (T->chevron_sel && s && !*s && !tk_strcmp(name, "list_into") && format < 4) {
        clear_bitmap(bitmap);
        mapped = 1;
    }
    /* A set_text theme (Classic): stock's radio rings are emptied; paint_radio draws the
     * tick. */
    if (T->set_text && s && !*s && (!tk_strcmp(name, "select") || !tk_strcmp(name, "unselect")) &&
        format < 4) {
        clear_bitmap(bitmap);
        mapped = 1;
    }
    /* Light Mode: a theme image without colour that is mostly glyph (marks, chevrons, spinners)
     * flips as the colours do. One with a grey or dark disc or plate (LIGHT_SURFACE) keeps its
     * white glyph, an image the accent mapped is final, and switch_off keeps its white knob as
     * switch_on does. Quick Settings' inactive discs (DROPDOWN_IMAGE, unmapped) flip whole: a
     * light disc under a dark glyph, as the active ones stand out in the accent. Premultiplied
     * pixels (bitmap_t flags @0xc) flip their straight colour. The lock screen's hint lies on a
     * picture or a dark page in a theme's own light half, so it keeps its white there; so does
     * Gain's switch, whose thumb stays white as the other switches' do. */
    unsigned char *ink = paper && !mapped && format < 4 && s && !*s &&
                                 tk_strcmp(name, "switch_off") &&
                                 (T->light == T->schemes ||
                                  (tk_strcmp(name, "scrsaver_slide") &&
                                   tk_strcmp(name, "gain_low") && tk_strcmp(name, "gain_high")))
                             ? bitmap_lock_buffer_for_write(bitmap)
                             : (void *)0;
    if (ink) {
        const unsigned char *o = at[format];
        unsigned fp = sc->paper & 255, fi = sc->ink & 255;
        int w = I(bitmap, 0), h = I(bitmap, 4), stride = bitmap_get_line_length(bitmap);
        int premul = *(unsigned short *)((char *)bitmap + 0xc) & BITMAP_PREMULTI, solid = 0,
            hue = 0;
        for (int y = 0; !hue && y < h; ++y)
            for (unsigned char *p = ink + y * stride, *end = p + 4 * w; p < end; p += 4) {
                int a = p[o[3]], r = p[o[0]], g = p[o[1]], b = p[o[2]];
                if (a < 128) continue;
                int hi = r > g ? (r > b ? r : b) : (g > b ? g : b),
                    lo = r < g ? (r < b ? r : b) : (g < b ? g : b);
                if (premul) hi = hi * 255 / a, lo = lo * 255 / a;
                hue |= hi - lo > LIGHT_CHROMA;
                solid += (premul ? (r * 299 + g * 587 + b * 114) / a * 255 / 1000
                                 : (r * 299 + g * 587 + b * 114) / 1000) < LIGHT_GLYPH;
            }
        for (int y = 0; !hue && (control || solid * LIGHT_SURFACE < w * h) && y < h; ++y)
            for (unsigned char *p = ink + y * stride, *end = p + 4 * w; p < end; p += 4)
                for (int c = 0, a = p[o[3]]; c < 3 && a; ++c)
                    p[o[c]] = (unsigned char)(premul ? flip_v(p[o[c]] * 255u / a, fp, fi) * a / 255
                                                     : flip_v(p[o[c]], fp, fi));
        bitmap_unlock_buffer(bitmap);
    }
    /* A theme's art placeholder size (Classic's 38): stock's grey tiles, as tall as the row (Folder
     * view's folders and songs; list_song is Album info's row image too), drawn at that size in
     * its middle, so they have room above and below; a real cover (a file path) keeps the row's
     * size. */
    static const char *const tiles[] = { "list_folder", "list_song" };
    for (unsigned k = 0;
         T->art_placeholder && s && !*s && format < 4 && k < sizeof tiles / sizeof *tiles; ++k)
        if (!tk_strcmp(name, tiles[k])) shrink_centred(bitmap, T->art_placeholder, at[format][3]);
    return stock_image_trampoline(manager, name, bitmap);
}

static void setting_text(int i) {
    static const char *const home[] = { "Home: Split", "Home: Full", "Home: Carousel" },
                             *const battery[] = { "Battery: Icon", "Battery: Percent",
                                                  "Battery: Icon + Percent",
                                                  "Battery: Percent Inside" },
                             *const light[] = { "Light Mode: Off", "Light Mode: On",
                                                "Light Mode: Auto" };
    int a = accent();
    const scheme_t *S = scheme();
    const char *const names[] = { S->label ? (const char *)0 : accent_names[a],
                                  home[st.home],
                                  battery[st.battery],
                                  (const char *)0,
                                  (const char *)0,
                                  (const char *)0,
                                  light[st.light],
                                  (const char *)0,
                                  (const char *)0 };
    char text[64];
    if (i == 0 && S->label) {
        tk_snprintf(text, sizeof text, "Accent: %s", S->label);
        row_label(st.setting_label[0], text);
        return;
    }
    if (i == 3 || i == 4 || i >= SETTING_OWN) {
        if (i == 3)
            tk_snprintf(text, sizeof text, "Theme: %s", theme()->name);
        else if (i == 4)
            tk_snprintf(text, sizeof text, "Shortcut: %s", ipod_home_shortcut());
        else {
            const theme_key_t *K = &theme_keys[i - SETTING_OWN];
            if (theme()->set_text) { /* the label and the value's name, unsplit */
                list_value(st.setting_label[i], K->label, K->names[st.own[i - SETTING_OWN]]);
                return;
            }
            tk_snprintf(text, sizeof text, "%s: %s", K->label, K->names[st.own[i - SETTING_OWN]]);
        }
        row_label(st.setting_label[i], text);
        return;
    }
    static const char *const toggles[] = { "Rounded Corners",       0,
                                           "Smart Awake",           "Centred Now Playing",
                                           "Click With Headphones", "Single Press Scrub",
                                           "Time in Title" };
    if (i == 5 || i >= 7) {
        list_toggle(st.setting_label[i], toggles[i - 5], *setting_values[i]);
        return;
    }
    row_label(st.setting_label[i], names[i]);
}

static int settings_list(void *w) {
    return w && !tk_strcmp(widget_get_type(w), "list_view") && !I(w, ROW_HEIGHT) &&
           I(w, LIST_DEFAULT_ITEM_HEIGHT) == geom_val(V_SET_ROW);
}

/* Cached pages behind Display must take the new inset before their return transition. */
static void settings_layout(void *w) {
    if (!w) return;
    if (settings_list(w)) {
        int y = theme()->set_top;
        if (I(w, W_Y) != y) widget_move_resize(w, I(w, W_X), y, I(w, W_W), I(w, W_H));
        return;
    }
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i)
        settings_layout(widget_get_child(w, i));
}

/* Centre or tap cycles the row's value and saves it. A new accent or theme reaches the payload's
 * drawing on the next paint, and the theme's colors and images once every cached image is dropped
 * and the screen repaints; Home takes its new layout and Streaming label at once (never
 * recreated), and an open Now Playing its progress bar. */
/* A Theme's preset (its theme.xml's), applied as it is chosen: Classic light, Classic Blue, Time
 * in Title and its own switches on; Modern light, Cobalt, square, Time in Title off; Minimal dark,
 * Graphite, rounded, Time in Title on; all Split. Light Mode's Auto, the battery and Centred Now
 * Playing are kept. Only a change is saved. */
static void setting_put(int *value, int v, const char *key) {
    if (*value != v) write_int_config(*value = v, "IPOD", key);
}
/* live 0 saves the preset alone (restart_confirm: the running state keeps the booted theme). */
static void theme_preset(const theme_t *T, int live) {
    int buf[11];
    if (live) st.light_day = light_day(clock_now(buf));
    for (int k = 0; k < T->n_opts; ++k) {
        const option_t *o = &T->opts[k];
        int *v = option_slot(T, o->setting), was = *v;
        if (o->preset >= 0 && *v != o->keep)
            setting_put(live ? v : &was, o->preset, option_key(T, o->setting));
    }
}

void restart_confirm(int setting, int value);
static int setting_click(void *ctx, void *event) {
    int i = (int)(long)ctx;
    const theme_t *T = theme();
    int *value = option_slot(T, i), was = *value;
    const char *key = option_key(T, i);
    /* Rockbox, Favorites, Shuffle, Rated Albums, Playlists, Artists, Albums, Games, Podcasts,
     * Audiobooks, Groovebox (skipped unless the card has it; Rockbox is always offered) */
    static const int shortcut_next[] = { 3, 2, 6, 5, 7, 4, 8, 1, 9, SHORTCUT_GROOVEBOX, 0 };
    _Static_assert(sizeof shortcut_next / sizeof *shortcut_next == SETTING_N_SHORTCUT,
                   "one per shortcut");
    /* Graphite, ..., Plum, Green, then Ivory (masks skip it: Modern's to Graphite, Classic's to its
     * own Classic Blue) */
    static const int accent_next[] = { 1, 2, 3, 4, 5, 6, 8, 0, MINIMAL_ACCENT };
    _Static_assert(sizeof accent_next / sizeof *accent_next == ACCENT_N, "one per ACCENTS row");
    if (i == 4) {
        *value = shortcut_next[shortcut()];
        if (*value == SHORTCUT_GROOVEBOX && !groovebox_available())
            *value = shortcut_next[SHORTCUT_GROOVEBOX];
    } else if (i) {
        const option_t *o = option_of(T, i);
        unsigned mask = o && o->values ? o->values : ~0u;
        do
            *value = (*value + 1) %
                     (i >= SETTING_OWN ? theme_keys[i - SETTING_OWN].n : setting_counts[i]);
        while (!(mask >> *value & 1));
    } else { /* Graphite, ..., Green, Ivory (Minimal); a theme's own schemes after Green */
        const option_t *o = option_of(T, SETTING_ACCENT);
        unsigned mask = o && o->values ? o->values : (1u << ACCENT_N) - 1;
        do {
            int v = *value >= ACCENT_N ? *value + 1 : accent_next[*value];
            if (!v && T->n_schemes > ACCENT_N && *value < ACCENT_N) v = ACCENT_N;
            *value = v < T->n_schemes ? v : 0;
        } while (!(mask >> *value & 1));
    }
    if (i >= SETTING_OWN && geom_with(T, i, *value, 0) != st.geom) {
        int v = *value; /* an own option's value that boots another set */
        *value = was;
        restart_confirm(i, v);
        return 0;
    }
    write_int_config(*value, "IPOD", key);
    if (i == 6) { /* Auto applies from this click */
        int buf[11];
        st.light_day = light_day(clock_now(buf));
    }
    if (i == SETTING_OWN + OWN_STATUSBAR)
        st.pct_digit = st.pct_sign = 0; /* the battery's digits, measured again at its size */
    if (i == 2 || i == SETTING_OWN + OWN_STATUSBAR)
        widget_invalidate_force(*(void *const *)system_bar, (void *)0); /* bar_sync applies it */
    else if (i == 1 || i == 4)
        coverflow_home_layout();
    else if (i == 5) {
        if (theme()->art_square)
            image_manager_unload_all(image_manager()); /* its thumbnails' corners */
        widget_invalidate_force(window_manager(), (void *)0);
    } else if (i < 7) { /* the switches from Smart Awake on only need their toggle drawn, Centred
                         * Now Playing the next open (ringnav_ui_load) */
        np_fill(0);
        image_manager_unload_all(image_manager());
        widget_invalidate_force(window_manager(), (void *)0);
    }
    setting_text(i);
    return 0;
}

/* The info page's Genre line: stock's centred label would clip a long value at both ends, and
 * stock's np_info rewrites it every 250 ms, which would reset an animation on it. So it stays
 * hidden as the value source, and a private hscroll_label shows the value, its text set only when
 * that changes. It scrolls as Minimal's album does (np_marquee), in front and lit only: covered,
 * dark or off the page the chain ends, and np_info's next fill restarts it. Stock measures text_w
 * on the first paint, so a new label starts moving on a fill after that.
 * ponytail: not in np_animating, so once idle its 50 ms step waits for the loop's slower passes and
 * it runs a little slower than the album's; add it there if that looks bad, at the cost of the
 * loop's fast pace while it moves. */
#define GENRE_SCROLL "ringnav_genre_scroll"
static int genre_over(void) {
    void *s = st.genre_label;
    if (!s || !g_backlight_status || window_manager_get_top_window(window_manager()) != st.np_win ||
        widget_get_prop_int(st.np_slide, "value", 0) != 2) /* view_info */
        return 0;
    return I(s, HSCROLL_TEXT_W) + 4 - I(s, W_W); /* stock's two 2 px margins, as marq_moved */
}
static int genre_step(const void *info) {
    (void)info;
    st.genre_timer = 0;
    int over = genre_over();
    if (over <= 0) return 0;
    int end = st.genre_off >= over;
    st.genre_off = end ? 0 : st.genre_off + MARQUEE_PX > over ? over : st.genre_off + MARQUEE_PX;
    hscroll_label_set_xoffset(st.genre_label, st.genre_off);
    st.genre_timer = timer_add(genre_step, (void *)0,
                               end || st.genre_off >= over ? MARQUEE_HOLD_MS : MARQUEE_MS);
    return 0;
}
static void genre_fill(void *win, void *label, const char *text) {
    void *s = widget_lookup(win, GENRE_SCROLL, 1);
    if (!s) {
        s = hscroll_label_create(P(label, W_PARENT), I(label, W_X), I(label, W_Y), I(label, W_W),
                                 I(label, W_H));
        if (!s) return;
        widget_set_name(s, GENRE_SCROLL);
        widget_use_style(s, "s_scrlabel_white16c");
        hscroll_label_set_only_focus(s, 1); /* no stock timer: genre_step moves it */
        widget_set_visible(label, 0, 0);
    }
    char old[1024 + 64 + 8]; /* ringnav_np_info's out */
    int same = !widget_get_text_utf8(s, old, sizeof old) && !strcmp(old, text);
    if (!same) {
        widget_set_text_utf8(s, text);
        st.genre_off = 0;
        hscroll_label_set_xoffset(s, 0);
    }
    if (win != st.np_win) return;
    st.genre_label = s;
    if (!same) stop_timer(&st.genre_timer); /* the new text holds at its start */
    if (!st.genre_timer && genre_over() > 0)
        st.genre_timer = timer_add(genre_step, (void *)0, MARQUEE_HOLD_MS);
}
/* A switch to theme t that keeps the set: THEME, then t's preset, Home and the settings inset
 * relaid, the Appearance rows (Theme and t's options) redrawn, the per-theme caches dropped and
 * everything repainted. */
void theme_select(int t) {
    setting_put(&st.theme, t, "THEME");
    theme_preset(theme(), 1);
    coverflow_home_layout();
    settings_layout(window_manager());
    np_theme();
    st.pct_digit = st.pct_sign = 0; /* the battery's digits, measured again in t's font */
    const theme_t *T = theme();
    setting_text(SETTING_THEME);
    for (int k = 0; k < T->n_opts; ++k) setting_text(T->opts[k].setting);
    np_fill(0);
    image_manager_unload_all(image_manager());
    widget_invalidate_force(window_manager(), (void *)0);
}
int theme_restart(int t) { return geom_with(&themes[t], -1, 0, 1) != st.geom; }
/* A choice that needs another geometry set: confirmed, the config takes value (a theme its presets
 * too, its own options' among them) and the Q2 restarts into it, ringnav_reboot saving the queue
 * first; the running state stays the booted set's theme until then, as theme_boot requires.
 * Cancelled, nothing is written and nothing changes. */
void restart_confirm(int setting, int value) {
    struct {
        int kind, ms;
        char text[0x400];
    } prompt = { 1, 0, "Restart to apply?" };
    if (navigator_to_with_context("dialog/confirminfo_dialog", &prompt) != 10) return;
    const theme_t *T = theme();
    int was = *option_slot(T, setting);
    setting_put(&was, value, option_key(T, setting));
    if (setting == SETTING_THEME) theme_preset(&themes[value], 0);
    ringnav_reboot();
}
static int theme_pick(void *ctx, void *event) {
    int t = (int)(long)ctx;
    accent();
    if (t == st.theme) return 0;
    if (theme_restart(t)) {
        restart_confirm(SETTING_THEME, t);
        return 0;
    }
    theme_select(t);
    void *win = window_manager_get_top_window(window_manager());
    void *view = win ? widget_lookup(win, "scroll_view_display", 1) : (void *)0;
    for (unsigned i = 0;
         view && view == theme_view && i < THEME_N && i < widget_count_children(view); ++i)
        image_base_set_image(widget_get_child(widget_get_child(widget_get_child(view, i), 0), 0),
                             theme_rows[i] == t ? "select" : "unselect");
    return 0;
}

/* Now Playing's info page: stock (0x53bc78, from its one call, NP_INFO_CALL in tools/build.py)
 * writes "Label:value" lines and the folder alone. Each line gets ": ", a number its unit's space
 * ("35.92 MB", "16-bit", "950 kbps", "44.1 kHz"), the folder its Location label, and an unknown
 * value, en_US's first player_unknown ("Unknown Artist"), reads "Unknown". Classic's page has
 * rows (tools/ipod.py np_ipod, #204's): stock's label keeps the name and its <name>_value the
 * value, in the info role's face, the value in the theme's grey (written through the flip, its own
 * inverse on the half's paper and ink), the genre with them, not genre_fill's marquee. */
int ringnav_np_info(void *win) {
    static const char *const names[] = { "label_album", "label_genre",  "label_size",
                                         "label_year",  "label_encode", "label_bits",
                                         "label_kbps",  "label_sample", "label_location" },
                             *const units[] = {
                                 "", "", " MB", "", "", "-bit", " kbps", " kHz", ""
                             };
    int result = np_info(win), rows = win && widget_lookup(win, "label_album_value", 1);
    char where[64] = "";
    getFormatString("player_location", where, sizeof where);
    const char *loc =
        *where && *where != '(' ? where : "Location"; /* a missing string reads "(key)" */
    for (unsigned i = 0; i < sizeof names / sizeof *names; ++i) {
        void *label = win ? widget_lookup(win, names[i], 1) : (void *)0;
        char s[1024], out[1024 + sizeof where + 8];
        if (!label || widget_get_text_utf8(label, s, sizeof s) || !*s) continue;
        const char *name = s, *v = s, *colon = s;
        while (*colon && *colon != ':') ++colon;
        int n = (int)(colon - s);
        if (i == 8 && !tk_str_start_with(s, loc)) /* the bare folder */
            name = loc, n = (int)strlen(loc);
        else if (*colon)
            v = colon + 1;
        else
            continue;
        while (*v == ' ') ++v;
        if (!strcmp(v, "Unknown Artist")) v = "Unknown";
        const char *unit = *units[i] ? units[i] + 1 : units[i];
        int len = (int)strlen(v), u = (int)strlen(unit);
        int split = u && len > u && !strcmp(v + len - u, unit) && v[len - u - 1] >= '0' &&
                    v[len - u - 1] <= '9';
        if (rows) {
            char key[24];
            tk_snprintf(key, sizeof key, "%s_value", names[i]);
            void *value = widget_lookup(win, key, 1);
            tk_snprintf(out, sizeof out, "%.*s", n, name);
            if (strcmp(out, s)) widget_set_text_utf8(label, out);
            theme_font(label, ROLE_INFO, FONT_ROW);
            if (!value) continue;
            tk_snprintf(out, sizeof out, "%.*s%s", split ? len - u : len, v, split ? units[i] : "");
            if (widget_get_text_utf8(value, s, sizeof s) || strcmp(s, out))
                widget_set_text_utf8(value, out);
            theme_font(value, ROLE_INFO, FONT_ROW);
            int ink = (int)ui_ink(RGBA(theme()->np_info_value[ipod_light()]));
            if (widget_get_prop_int(value, "_ink", 0) != ink) {
                widget_set_prop_int(value, "_ink", ink);
                widget_set_prop_int(value, "style:normal:text_color", ink);
            }
            continue;
        }
        tk_snprintf(out, sizeof out, "%.*s: %.*s%s", n, name, split ? len - u : len, v,
                    split ? units[i] : "");
        if (i == 1) {
            genre_fill(win, label, out);
            continue;
        }
        if (strcmp(out, s)) widget_set_text_utf8(label, out);
    }
    return result;
}

/* The Display sub-page the next display_page is to be (DISPLAY_*), 0 for Display itself. */
enum { DISPLAY_APPEARANCE = 1, DISPLAY_COVERFLOW, DISPLAY_CONTROLS, DISPLAY_THEMES };
static int keylock_open(void *ctx, void *event) {
    navigator_to("systemset/keylock_page");
    return 0;
}
static int display_sub __attribute__((section(".scratch")));
static int display_open(void *ctx, void *event) {
    display_sub = (int)(long)ctx;
    navigator_to("systemset/display_page");
    display_sub = 0;
    return 0;
}

static void setting_row(void *view, int i) {
    static const char *const icons[] = {
        "system_display",    "playset_covermode", "system_powermanager", "system_display",
        "playset_covermode", "system_display",    "system_display",      "system_display",
        "playset_covermode", "system_keytone",    "playset_covermode",   "system_display"
    };
    const char *icon = i < SETTING_OWN ? icons[i] : "system_display";
    st.setting_label[i] = list_row(view, icon, i == SETTING_THEME ? display_open : setting_click,
                                   (void *)(long)(i == SETTING_THEME ? DISPLAY_THEMES : i));
    setting_text(i);
}

/* systemset_display_page_init: stock builds its four rows (0x4c6b94: a s_listitem_black list_item
 * holding a 335x70 s_btn_listitem button with a 52px icon, a 24px label at x 72 and list_into).
 * Its fourth, Show Navigation Bar, goes: the themes own the bar and each list's box, so stock's
 * switch is held on (tools/build.py NAV_SHOWN). Smart Awake and Night Shift (the brightness icon,
 * opening its slider page, crossfade.c) join them, then Appearance and Coverflow, each opening
 * stock's page again (display_open) with its stock rows dropped and its own instead: Theme, Light
 * Mode, Accent, Corners, Home, Shortcut, Battery, Time in Title, Centred Now Playing; Artist View,
 * Coverflow Sort and Refresh Coverflow. Controls is System settings' row (ringnav_systemset) on the
 * same page: the wheel, the buttons and what the buttons do. The value rows show it in the label
 * and have no chevron, since they change in place. ponytail: stock's page opened over itself; its
 * own rows' state is never touched on a sub-page. */
int ringnav_display(void *win, void *ctx) {
    static const char *const titles[] = { 0, "Appearance", "Cover Flow", "Controls", "Theme" },
                             *const sub_icons[] = { 0, "system_display", "playset_covermode",
                                                    "playset_folderjump" };
    static const char *const cf_icons[] = { "playset_covermode", "playset_covermode",
                                            "system_update", "playset_covermode" };
    static const char *const hold_icon[] = { "playset_folderjump" },
                             *const tone_icon[] = { "system_keytone" },
                             *const lock_icon[] = { "system_keylock" };
    static const unsigned char main_order[] = { 0, 1, 3, 4, 2, 5, 6 };
    int sub = display_sub, result = stock_display_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_display", 1) : (void *)0;
    if (!view) return result;
    if (!sub) {
        widget_destroy(widget_get_child(view, 3)); /* Show Navigation Bar */
        setting_row(view, 7);
        night_row(list_row_into(view, "display_backlight", xfade_open, (void *)3));
        for (int i = DISPLAY_APPEARANCE; i <= DISPLAY_COVERFLOW; ++i)
            widget_set_text_utf8(list_row_into(view, sub_icons[i], display_open, (void *)(long)i),
                                 titles[i]);
        reorder(view, main_order, sizeof main_order);
        return result;
    }
    widget_destroy_children(view);
    void *nav = widget_lookup(win, "view_navbar", 1), *title = nav ? widget_get_child(nav, 0) : 0;
    if (title) widget_set_text_utf8(title, titles[sub]);
    if (sub == DISPLAY_THEMES) { /* the built-ins */
        accent();
        for (int i = 0; i < THEME_N; ++i) {
            int t = theme_rows[i];
            widget_set_text_utf8(
                list_row(view, t == st.theme ? "select" : "unselect", theme_pick, (void *)(long)t),
                themes[t].name);
        }
        theme_view = view;
        return result;
    }
    if (sub == DISPLAY_COVERFLOW) {
        pod_rows(win, "scroll_view_display", POD_ARTIST_CF, 1, cf_icons);
        pod_rows(win, "scroll_view_display", POD_PL_CF, 1, cf_icons);
        pod_rows(win, "scroll_view_display", POD_CF_SORT, 2, cf_icons + 1);
        pod_rows(win, "scroll_view_display", POD_BIG_LIB, 1, cf_icons + 3);
        return result;
    }
    if (sub == DISPLAY_APPEARANCE) {
        /* Theme, then the active theme's options in order */
        const theme_t *T = theme();
        setting_row(view, SETTING_THEME);
        for (int k = 0; k < T->n_opts; ++k) setting_row(view, T->opts[k].setting);
        return result;
    }
    wheel_row(list_row_into(view, "system_display", xfade_open, (void *)1));
    pod_rows(win, "scroll_view_display", POD_HOLD_HOME, 1, hold_icon);
    setting_row(view, 10);
    pod_rows(win, "scroll_view_display", POD_KEYTONE, 1, tone_icon);
    setting_row(view, 9); /* Click With Headphones shows while Key Tone is on */
    widget_set_visible(P(P(st.setting_label[9], W_PARENT), W_PARENT), g_keytone_flag != 0, 0);
    widget_set_tr_text(list_row_into(view, "system_keylock", keylock_open, 0), "system_keylock");
    pod_rows(win, "scroll_view_display", POD_WAKE, 1, lock_icon);
    pod_rows(win, "scroll_view_display", POD_VOL_GUARD, 1, lock_icon);
    return result;
}

#define BOOT_TARGET "/mnt/data/boot-target"
static int boot_rockbox __attribute__((section(".scratch")));
static void boot_text(void *label) {
    widget_set_text_utf8(label, boot_rockbox ? "Boot to: Rockbox" : "Boot to: Q2 Pod");
}
static int boot_click(void *label, void *event) {
    if (!rockbox_available()) {
        toast("Rockbox is not on the card");
        return 0;
    }
    const char *value = boot_rockbox ? "q2pod" : "rockbox";
    if (!blob_io(BOOT_TARGET, BOOT_TARGET ".tmp", (void *)value, strlen(value), 1)) {
        unlink(BOOT_TARGET ".tmp");
        toast("Could not save boot choice");
        return 0;
    }
    boot_rockbox = !boot_rockbox;
    boot_text(label);
    return 0;
}
static void boot_row(void *view) {
    if (!view || !rockbox_available()) return;
    char value[16];
    void *f = fopen(BOOT_TARGET, "rb");
    boot_rockbox = 1;
    if (f) {
        unsigned n = fread(value, 1, 5, f);
        int q2pod = n == 5 && !memcmp(value, "q2pod", 5);
        /* Shell command substitution strips all trailing newlines. */
        while (q2pod && (n = fread(value, 1, sizeof value, f)))
            for (unsigned i = 0; i < n; ++i)
                if (value[i] != '\n') q2pod = 0;
        boot_rockbox = !q2pod || ferror(f);
        fclose(f);
    }
    void *label = list_row(view, "system_powermanager", 0, 0);
    void *button = P(label, W_PARENT);
    widget_on(button, EVT_CLICK, boot_click, label);
    boot_text(label);
}
int ringnav_systemset(void *win, void *ctx) {
    int result = stock_systemset_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_sysset", 1) : (void *)0;
    if (view) {
        /* Stock's System page's rows and Date & Time, which V1.34 took off this list */
        static const char *const moved[][2] = { { "system_time", "systemset/datetime_page" },
                                                { "system_update", "systemset/fwupdate_page" },
                                                { "system_reset", "systemset/reset_page" },
                                                { "system_about", "systemset/about_page" } };
        widget_set_text_utf8(list_row_into(view, "system_update", shelf_open, 0), "App Store");
        widget_set_text_utf8(
            list_row_into(view, "playset_folderjump", display_open, (void *)DISPLAY_CONTROLS),
            "Controls");
        for (unsigned i = 0; i < 4; ++i)
            widget_set_tr_text(list_row_into(view, moved[i][0], to_page, (void *)moved[i][1]),
                               moved[i][0]);
        for (unsigned i = 5; i <= 8; ++i) widget_set_visible(widget_get_child(view, i), 0, 0);
    }
    reorder(view, sysset_order, sizeof sysset_order);
    return result;
}

static int untouch(const void *info) {
    st.untouch_timer = 0;
    st.touch_mode = 0;
    void *top = window_manager_get_top_window(window_manager());
    if (top) widget_invalidate_force(top, (void *)0);
    return 0;
}

static void hide_outline(void) {
    if (st.touch_mode && !st.untouch_timer) return; /* already hidden: each touch move comes here */
    stop_timer(&st.untouch_timer);
    st.touch_mode = 1;
    void *top = window_manager_get_top_window(window_manager());
    if (top) widget_invalidate_force(top, (void *)0);
}

int ringnav_touch(void *ctx, void *event) {
    /* T9's chevron goes back, as Return, before the press blurs the box: that blur searches. */
    if (st.t9_back && event && I(event, EVENT_TYPE) == EVT_POINTER_DOWN) {
        int xy[2] = { I(event, EVENT_X), I(event, EVENT_Y) };
        widget_to_local(st.t9_back, xy);
        if (xy[0] >= 0 && xy[0] < I(st.t9_back, W_W) && xy[1] >= 0 && xy[1] < I(st.t9_back, W_H))
            search_key(KEY_RETURN);
    }
    /* settle_timer while paused on the scrubbed track: a paused seek still waiting for Play. */
    int hold =
        st.settle_timer && st.np_slider && np_track() == st.scrub_track && mclGetPlayStatus() == 3;
    if (hold && event) {
        int xy[2] = { I(event, EVENT_X), I(event, EVENT_Y) };
        widget_to_local(st.np_slider, xy);
        if (xy[0] >= 0 && xy[0] < I(st.np_slider, W_W) && xy[1] >= 0 &&
            xy[1] < I(st.np_slider, W_H))
            hold = 0;
    }
    if (hold)
        stop_timer(&st.np_press); /* Swiping pages keeps the paused seek's preview. */
    else
        np_cancel(); /* before the slider sees the touch, so a drag seeks the stock way */
    hide_outline();
    int result = stock_touch_trampoline(ctx, event);
    /* A tap is a fresh interaction: it cancels a pending screen-toggle pair and any spin. */
    drop_input();
    fx_cancel();
    /* Stock move-before forwards to this down-before entry too. Only a real down starts
     * a new pull; the page's native before-children callbacks observe subsequent motion. */
    if (!event || I(event, EVENT_TYPE) == EVT_POINTER_DOWN) pull_begin(event);
    void *w = surface((void *)0, (void *)0);
    /* Pointer-down must not recall/rebind the row that native touch is about to hit. */
    if (!result && w && load_rows(&g_menu, w)) {
        stop_scroll(&g_menu);
        prop(w, TOUCH, 1);
        widget_invalidate_force(w, (void *)0);
    }
    return result; /* The very same touch continues through the stock tap/drag handlers. */
}

/* target's click, to stock. Set date and time's OK sets the clock (stock) and stays; it goes back
 * to the Time page instead, as Return does, after a tap (ringnav_dispatch) or Centre's click
 * (edit_single). */
static int time_click(void *target, void *event) {
    void *page = window_manager_get_top_window(window_manager());
    int ok = page && !tk_strcmp(widget_get_prop_str(page, "name", ""), "manualtime_page") &&
             !tk_strcmp(widget_get_prop_str(target, "name", ""), "btn_enter");
    int result = stock_dispatch_trampoline(target, event);
    if (ok && st.clock_timer) clock_show();
    if (ok && window_manager_get_top_window(window_manager()) == page) navigator_back();
    return result;
}

/* Quick Settings' swipe up under Classic: a touch from QS_SWIPE_Y that has gone SLIDE_PX up,
 * and isn't the slider's by then, counts for stock's handler as one from its line. */
static void quick_swipe(void *target, void *event) {
    int from = I(g_pressdown_y, 0);
    if (from < QS_SWIPE_Y || from >= QS_SWIPE_STOCK || from - I(event, EVENT_Y) < SLIDE_PX ||
        I(g_dropdown_menu_state, 0) != 3 || (quick.slider && quick.slide > 0) || !classic_pages() ||
        tk_strcmp(widget_get_prop_str(target, "name", ""), "statusbar_dialog"))
        return;
    I(g_pressdown_y, 0) = QS_SWIPE_STOCK;
}

/* Observe actual clicks BEFORE app callbacks can navigate or destroy/rebind their widgets.
 * Do not turn pointer-down into selection: a swipe is not a tap. */
int ringnav_dispatch(void *target, void *event) {
    if (event && target != window_manager() &&
        ((I(event, EVENT_TYPE) >= EVT_KEY_DOWN && I(event, EVENT_TYPE) <= EVT_KEY_UP_BEFORE) ||
         I(event, EVENT_TYPE) == EVT_KEY_LONG) &&
        ple_key((unsigned)I(event, EVENT_KEY), 3))
        return STOP;
    if (event) {
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
        if (target && type == EVT_CLICK && widget_get_prop_int(target, QUICK_HOLD, 0) < 0)
            return STOP;
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
                    stock_dispatch_trampoline(
                        target, pointer_event_init(ev, EVT_POINTER_ABORT, target, 0, 0));
                quick.slide = -1;
            } else if (!quick.slide && (up || (dx >= SLIDE_PX && dx > dy))) {
                quick.slide = 1;
                stock_dispatch_trampoline(target, pointer_event_init(ev, EVT_POINTER_DOWN - 1,
                                                                     target, quick.sx, quick.sy));
            } else if (!quick.slide && dy >= SLIDE_PX)
                quick.slide = -1;
            if (up) quick.slider = 0;
            if (quick.slide <= 0) return 0;
        } else if (up) /* a release the slider doesn't see, its press never given */
            quick.slider = 0;
    }
    /* Native key delivery can retain the covered window's focused widget: the Artists list under
     * an artist's page (wheel turns queued while it loads) or under its Coverflow. */
    if (target && event && I(event, EVENT_TYPE) >= EVT_KEY_DOWN &&
        I(event, EVENT_TYPE) <= EVT_KEY_UP_BEFORE &&
        (I(event, EVENT_KEY) == KEY_PREV || I(event, EVENT_KEY) == KEY_NEXT)) {
        void *wm = window_manager(), *top = window_manager_get_top_window(wm);
        const char *name = top ? widget_get_prop_str(top, "name", "") : "";
        if (target != wm && top &&
            (!tk_strcmp(name, "coverflow_page") || !tk_strcmp(name, "artistinfo_page"))) {
            void *w = target;
            while (w && w != top) w = P(w, W_PARENT);
            if (!w) return STOP;
        }
    }
    if (st.pull_page && (!event || !pull_live() || I(event, EVENT_TYPE) == EVT_KEY_DOWN_BEFORE))
        pull_cancel();
    if (target && event && I(event, EVENT_TYPE) == EVT_CLICK) {
        for (void *w = target; w; w = P(w, W_PARENT))
            if (widget_get_prop_int(w, PULL_SUPPRESS, 0)) return STOP;
        pull_cancel();
    }
    if (target && event && I(event, EVENT_TYPE) == EVT_CLICK) {
        hide_outline();
        cancel_center(); /* A native activation supersedes confirmation, even without touch. */
        drop_spin();
        fx_cancel();
        keep_selected(
            target); /* a tap owns its live row: recall could scroll/rebind it before delivery */
        return time_click(target, event);
    }
    return stock_dispatch_trampoline(target, event);
}

/* Songs' bind found no artist on the record (SONG_UNKNOWN_CALL, tools/build.py). Stock's "Unknown
 * Artist" shows only where a set keeps stock's row; under a set without text-row artwork the
 * two-line row says only what the record says, so the second line stays empty. */
void ringnav_song_unknown(const char *key, char *text, int size) {
    if (geom(G_TEXT_ART)) getFormatString(key, text, size);
}

/* Under a set without text-row artwork (Classic's), a row no taller than the pitch is one
 * line: its leading children (artwork, the playing gif; stock's choice box stays) and the text's
 * second line hide, and the title, at the set's centred y, takes their width. Stock re-showing one
 * relayouts the row, which hides it again. */
static int one_line(void *row) { return !geom(G_TEXT_ART) && I(row, W_H) <= geom(G_PITCH); }
/* Under such a set, a taller row whose leading image stock gave no width (text_art 0, as Songs') is
 * two lines of text: the same children hide, its second line stays. */
static int two_lines(void *row) {
    if (geom(G_TEXT_ART) || I(row, W_H) <= geom(G_PITCH)) return 0;
    for (unsigned i = 0; i < widget_count_children(row); ++i) {
        void *child = widget_get_child(row, i);
        if (!tk_str_start_with(widget_get_prop_str(child, "name", ""), "img_choice"))
            return !I(child, W_W);
    }
    return 0;
}

/* A row's text after its title, however deep stock nests it (view_artist): the meta role. A theme
 * with its own face there also takes it to stock's secondary grey, the theme's muted (a style's
 * white would read as black ink in Light Mode). */
void row_meta(void *w) {
    const theme_t *T = theme();
    if (tk_strcmp(widget_get_type(w), "hscroll_label")) {
        for (unsigned i = 0;
             (T->font[ROLE_META] || T->font_px[ROLE_META]) && i < widget_count_children(w); ++i)
            row_meta(widget_get_child(w, i));
        return;
    }
    int fresh =
        (T->font[ROLE_META] || T->font_px[ROLE_META]) && widget_get_prop_int(w, "_font", -1) < 0;
    theme_font(w, ROLE_META, FONT_ROW);
    if (fresh) widget_set_prop_int(w, "style:normal:text_color", (int)SPOT_GREY);
}

/* Only audited ordinary row constructors install this per-instance layouter. Stock still
 * positions every child, skips hidden controls, and owns clone/destruction and text overflow. */
static int compact_row_layout(void *layout, void *row) {
    void *text = (void *)0, *title = (void *)0;
    /* Classic's playlist rows hold their children in their own places: stock's layout has none */
    if (widget_get_prop_int(P(row, W_PARENT), PL_ROW, 0)) {
        pl_fit(row);
        return 0;
    }
    unsigned count = widget_count_children(row);
    const theme_t *T = theme();
    int tall = T->art_x && I(row, W_H) > geom(G_PITCH), plain = two_lines(row), choice = 0;
    /* Names differ (view_info, viewinfo_N, ...); all seven constructors put the title
     * first, either directly in the row or one level inside its text container. */
    for (unsigned i = 0; i < count && !text; ++i) {
        void *child = widget_get_child(row, i);
        if (!tk_strcmp(widget_get_type(child), "hscroll_label")) text = title = child;
        for (unsigned j = 0; j < widget_count_children(child) && !text; ++j) {
            void *label = widget_get_child(child, j);
            if (!tk_strcmp(widget_get_type(label), "hscroll_label")) {
                text = child;
                title = label;
            }
        }
    }
    if (text && (plain || one_line(row))) {
        for (unsigned i = 0; i < count; ++i) {
            void *child = widget_get_child(row, i);
            if (child == text) break;
            if (!tk_str_start_with(widget_get_prop_str(child, "name", ""), "img_choice"))
                widget_set_visible(child, 0, 0);
            else if (widget_get_visible(child))
                choice = 1;
        }
        for (unsigned j = 0; !plain && text != title && j < widget_count_children(text); ++j)
            if (widget_get_child(text, j) != title)
                widget_set_visible(widget_get_child(text, j), 0, 0);
    }
    if (text && widget_get_visible(text)) {
        int width = I(row, W_W) - 2 * B(layout, DEFAULT_LAYOUT_X_MARGIN);
        if (drill(row)) /* as if a stock img_into were the last child */
            width -=
                CHEVRON_W - B(layout, DEFAULT_LAYOUT_X_MARGIN) + B(layout, DEFAULT_LAYOUT_SPACING);
        for (unsigned i = 0; i < count; ++i) {
            void *child = widget_get_child(row, i);
            if (child != text && widget_get_visible(child))
                width -= I(child, W_W) + B(layout, DEFAULT_LAYOUT_SPACING);
        }
        /* A theme's art row: its text's box on the theme's columns, hscroll_label's margin each
         * side (the move's below, after stock's layout); a two-line row's from the set's text
         * inset, unless stock's choice box shows. */
        if (tall && !plain) width = T->art_text_end - T->art_text_x + 4;
        if (plain && !choice) {
            /* stock's chevron last, as Search's Artists: the text ends where an art row's does */
            void *last = widget_get_child(row, count - 1);
            int into = last != text && widget_get_visible(last) &&
                       !tk_strcmp(widget_get_prop_str(last, "image", ""), "list_into");
            width = (into ? T->art_text_end : T->row_text_end) - geom_val(V_TEXT_X) + 2;
        }
        if (width < 0) width = 0;
        widget_resize(text, width, I(text, W_H));
        /* Stock's rows scroll a long title once as the page opens, every row at a time, its
         * glyphs smeared over the moving clip; focus-only (no row takes AWTK focus) keeps the
         * ellipsis, as the selected row shows it. */
        hscroll_label_set_only_focus(title, 1);
        theme_font(title, tall ? ROLE_TALL : ROLE_TITLE, FONT_ROW);
        if (title != text) {
            int available = width - I(title, W_X);
            widget_resize(title, available > 0 ? available : 0, I(title, W_H));
            for (unsigned j = 0, after = 0; j < widget_count_children(text); ++j) {
                void *label = widget_get_child(text, j);
                if (label == title)
                    after = 1;
                else if (after) {
                    row_meta(label);
                    if (tall) { /* stock's second line is 151 px: it takes the box, as the title */
                        widget_resize(label, available > 0 ? available : 0, I(label, W_H));
                        for (unsigned k = 0; k < widget_count_children(label); ++k)
                            widget_resize(widget_get_child(label, k), available > 0 ? available : 0,
                                          I(widget_get_child(label, k), W_H));
                    }
                }
            }
        }
    }
    int ret = ((int (*)(void *, void *))((const unsigned *)DEFAULT_LAYOUT_VTABLE)[2])(layout, row);
    if (tall && text && widget_get_visible(text)) { /* stock's choice box keeps stock's places */
        void *art = 0;
        for (unsigned i = 0; i < count && widget_get_child(row, i) != text; ++i) {
            void *child = widget_get_child(row, i);
            if (!widget_get_visible(child)) continue;
            if (tk_str_start_with(widget_get_prop_str(child, "name", ""), "img_choice")) {
                art = 0;
                break;
            }
            art = art ?: child;
            widget_move_resize(child, T->art_x - I(row, W_X), I(child, W_Y), I(child, W_W),
                               I(child, W_H));
        }
        if (art)
            widget_move_resize(text, T->art_text_x - 2 - I(row, W_X), I(text, W_Y), I(text, W_W),
                               I(text, W_H));
    }
    /* The set's text inset (Classic's text_x, on screen, clear of the glass; the row may sit
     * in from it): text leading the row moves there, its right edge kept. Text after art or stock's
     * choice box keeps its place. */
    int tx = geom_val(V_TEXT_X);
    if (tx && text && I(text, W_X) <= B(layout, DEFAULT_LAYOUT_X_MARGIN)) {
        tx = tx > I(row, W_X) ? tx - I(row, W_X) : 0;
        widget_move_resize(text, tx, I(text, W_Y),
                           plain && !choice ? I(text, W_W) : I(text, W_W) - (tx - I(text, W_X)),
                           I(text, W_H));
    }
    return ret;
}

int compact_set_row_layout(void *row, const char *params) {
    static unsigned vtable[8] __attribute__((section(".scratch")));
    int ret = widget_set_children_layout(row, params);
    if (ret || !row) return ret;
    void *layout = P(row, W_CHILDREN_LAYOUT);
    if (layout) {
        if (!vtable[0]) {
            memcpy(vtable, (const void *)DEFAULT_LAYOUT_VTABLE, sizeof(vtable));
            vtable[2] = (unsigned)compact_row_layout;
        }
        P(layout, CHILDREN_LAYOUT_VTABLE) = vtable;
    }
    return ret;
}

/* Stock's own table rows (ipod.json stock_row_layout_calls), under a set that re-pitches them. */
int stock_set_row_layout(void *row, const char *params) {
    return geom_val(V_STOCK_ROW) < SET_STOCK_ROW ? compact_set_row_layout(row, params)
                                                 : widget_set_children_layout(row, params);
}

/* ... whose titles are bound with no relayout (stock_row_text_calls), so the face that has the
 * text's characters is found here. Queue's label, alone in its button at x 10, goes to the set's
 * text inset. */
int stock_row_text(void *label, const char *text) {
    int ret = widget_set_text_utf8(label, text);
    if (geom_val(V_STOCK_ROW) < SET_STOCK_ROW) {
        theme_font(label, ROLE_TITLE, FONT_ROW);
        if (I(label, W_X) == 10) {
            int x = geom_val(V_TEXT_X) - SET_STOCK_X;
            widget_move_resize(label, x, 0, I(label, W_W) + 10 - x, I(label, W_H));
        }
    }
    return ret;
}

/* ... whose style stock then picks, after the title on each bind: the playing row's red, which the
 * build turns white for every theme (ipod.json), so a play mark goes before its title: every face
 * a row takes has one (the build draws Classic's: tools/build.py play_mark), so the title keeps the
 * face stock_row_text gave it. It keeps its colour under a set that re-pitches the rows; the style
 * hook takes it from there, as any stock red. Inline, as the row's font is, and written on each
 * bind, as the rows are reused. */
int stock_row_style(void *label, const char *style) {
    int ret = widget_use_style(label, style), playing = !tk_strcmp(style, "s_scrlabel_red24l");
    if (playing) {
        char text[512] = "\xe2\x96\xb6 "; /* U+25B6; stock binds at most this much title */
        widget_get_text_utf8(label, text + 4, sizeof text - 4);
        widget_set_text_utf8(label, text);
    }
    if (geom_val(V_STOCK_ROW) < SET_STOCK_ROW)
        widget_set_prop_int(label, "style:normal:text_color",
                            (int)RGBA(playing ? STOCK_RED : 0xffffff));
    return ret;
}

/* More's speed row: its four 42 x 61 buttons can't take the pitch, so it stays stock's height,
 * over the set's rows. Its icon goes and its labels take the row's fonts, each box holding its
 * font and ending before the first button. */
void stock_more_speed(void *win) {
    void *first =
        geom_val(V_STOCK_ROW) < SET_STOCK_ROW ? widget_lookup(win, "img_forward_0", 1) : (void *)0;
    if (!first) return;
    void *button = P(first, W_PARENT), *row = P(button, W_PARENT);
    int x = geom_val(V_TEXT_X) - I(button, W_X), labels = 0;
    widget_resize(row, I(row, W_W), SET_STOCK_ROW);
    for (unsigned i = 0, n = widget_count_children(button); i < n; ++i) {
        void *c = widget_get_child(button, i);
        if (I(c, W_W) == SET_STOCK_ICON) widget_set_visible(c, 0, 0);
        if (tk_strcmp(widget_get_type(c), "hscroll_label")) continue;
        if (!labels++) {
            widget_move_resize(c, x, 8, I(first, W_X) - x - 2, 28);
            theme_font(c, ROLE_META,
                       FONT_ROW); /* the row's 27 px wouldn't fit before the buttons */
        } else {
            widget_move_resize(c, x, 43, I(c, W_W), 18);
            theme_font(c, ROLE_META, FONT_ROW);
        }
    }
}
/* More's rows that open a screen, under a selected-only chevron: page_into's mark on the list item.
 * Their kind is read here, once, so no paint compares a string. */
void stock_more_into(void *win) {
    void *view = theme()->chevron_sel ? widget_lookup(win, "scroll_view_more", 1) : (void *)0;
    if (!view || widget_count_children(view) != PLAYMORE_COUNT) return;
    for (unsigned i = 0; i < PLAYMORE_COUNT; ++i) {
        int kind = ((const int *)PLAYMORE_ROWS)[i];
        if (kind >= PLAYMORE_INTO_FIRST && kind <= PLAYMORE_INTO_LAST)
            widget_set_visible(image_create(widget_get_child(view, i), 302, 0, 50, 0), 0, 0);
    }
}

/* Settings rows (docs/ipod.md#settings). One stock child in row coordinates: full-height children
 * fill the row and shorter ones keep their centre; the icon shrinks to the set's icon size, or
 * hides at 0; text starts at the corner-safe column; children from SET_RIGHT_SIDE, and wide ones'
 * right edges, move with the trailing image by `shift`, never past the text margin. */
static void set_child(void *c, int row_w, int row_h, int shift) {
    int x = I(c, W_X), y = I(c, W_Y), w = I(c, W_W), h = I(c, W_H), right = x + w;
    /* Streaming's rows, taller than the set's, have an icon in a set with none */
    int stream = row_h == geom_val(V_STREAM_ROW) && row_h != geom_val(V_SET_ROW),
        icon = stream ? SET_ICON : geom_val(V_SET_ICON),
        art_x = stream ? theme()->art_x : 0; /* the theme's art columns are Streaming's alone */
    const theme_t *T = theme();
    /* A set without settings icons: any image leading the row goes, whatever its width (a paired
     * device's 50 px one), and text after one starts at the text column. A payload picker's mark
     * (Theme, Key Lock) stays, in the column of stock's own marks. */
    if (!icon && x < SET_RIGHT_SIDE && !tk_strcmp(widget_get_type(c), "image")) {
        const char *image = widget_get_prop_str(c, "image", "");
        if (!tk_strcmp(image, "select") || !tk_strcmp(image, "unselect"))
            widget_move_resize(c, row_w - SET_EDGE - 50, 0, 50, row_h);
        else
            widget_set_visible(c, 0, 0);
        return;
    }
    if (h >= SET_STOCK_BODY) {
        y = 0;
        h = row_h;
    } else
        y -= (SET_STOCK_BODY - row_h) / 2;
    if (w == SET_STOCK_ICON && !tk_strcmp(widget_get_type(c), "image")) {
        if (!icon) {
            widget_set_visible(c, 0, 0);
            return;
        }
        /* the build pre-sizes the audited icons to SET_ICON, so they draw 1:1; any other one scales
         * down */
        image_set_draw_type(c, IMAGE_DRAW_SCALE_DOWN);
        /* a theme's art rows' columns: its cover's, and its text's less hscroll_label's margin */
        widget_move_resize(c, art_x ? art_x : SET_ICON_X, (row_h - icon) / 2, icon, icon);
        return;
    }
    int left = x >= SET_RIGHT_SIDE ? SET_STOCK_X + x + shift
               : x > SET_STOCK_ICON / 2 + 10 /* after a stock icon at x 10 */
                   ? (!icon   ? SET_TEXT_X
                      : art_x ? T->art_text_x - 2
                              : SET_ICON_X + icon + SET_GAP) +
                         x - (SET_STOCK_ICON + 20)
                   : SET_TEXT_X + x - 10;
    if (!icon && left < SET_TEXT_X) left = SET_TEXT_X;
    right = right >= SET_RIGHT_SIDE ? SET_STOCK_X + right + shift : right + left - x;
    if (right > row_w - SET_TEXT_X) right = row_w - SET_TEXT_X;
    widget_move_resize(c, left, y, right > left ? right - left : 0, h);
    if (!tk_strcmp(widget_get_type(c), "hscroll_label")) theme_font(c, ROLE_TITLE, FONT_ROW);
}

/* The row's stock settings button (SET_STOCK_X, SET_STOCK_W), or none: a row already mapped, or one
 * another builder made, which keeps its own height and geometry. */
static void *set_button(void *item) {
    if (tk_strcmp(widget_get_type(item), "list_item")) return (void *)0;
    for (unsigned i = 0; i < widget_count_children(item); ++i) {
        void *b = widget_get_child(item, i);
        if (I(b, W_X) == SET_STOCK_X && I(b, W_W) == SET_STOCK_W &&
            !tk_strcmp(widget_get_type(b), "button"))
            return widget_lookup(b, "img_forward_0", 0) ? (void *)0 : b; /* stock_more_speed */
    }
    return (void *)0;
}

/* The stock button spans the row; its children follow. Once mapped it no longer matches, so a later
 * layout leaves it alone; the builders give its children no self_layout, so nothing lays them out
 * again. */
static void set_row(void *item) {
    int row_w = I(item, W_W), row_h = I(item, W_H);
    void *b = set_button(item);
    if (b) {
        int trail = SET_STOCK_TRAIL; /* the rightmost trailing image sets the right column */
        unsigned n = widget_count_children(b);
        for (unsigned j = 0; j < n; ++j) {
            void *c = widget_get_child(b, j);
            if (I(c, W_W) == 50 && I(c, W_X) >= SET_RIGHT_SIDE && I(c, W_X) > trail &&
                !tk_strcmp(widget_get_type(c), "image"))
                trail = I(c, W_X);
        }
        widget_move_resize(b, 0, 0, row_w, row_h);
        for (unsigned j = 0; j < n; ++j)
            set_child(widget_get_child(b, j), row_w, row_h,
                      row_w - SET_EDGE - 50 - SET_STOCK_X - trail);
        /* a marked row's text ends before the chevron's box, as row_into's; stock's box has no x */
        int chevron_x = theme()->chevron_x;
        if (chevron_x && into_mark(item))
            for (unsigned j = 0; j < n; ++j) {
                void *c = widget_get_child(b, j);
                if (!tk_strcmp(widget_get_type(c), "hscroll_label"))
                    widget_resize(c, chevron_x - 1 - I(c, W_X), I(c, W_H));
            }
    }
}

/* A settings row's height: h, or two of them where it is too short for a stock row with a detail
 * line under its title (Bluetooth's Quality), which would run into the next row. */
static int set_row_h(void *button, int h) {
    if (2 * h > SET_STOCK_BODY) return h;
    for (unsigned i = 0, n = widget_count_children(button); i < n; ++i) {
        void *c = widget_get_child(button, i);
        if (I(c, W_Y) >= SET_STOCK_BODY / 2 && !tk_strcmp(widget_get_type(c), "hscroll_label"))
            return 2 * h;
    }
    return h;
}

/* Streaming's list: settings rows at the set's stream_row (52 under the classic-light set), matched
 * by name, at layout time only. Its list on Home's window matches too, but its buttons are Home's
 * by then (coverflow.c home_fill), so set_button leaves them alone. */
static int stream_list(void *view) {
    return view && !tk_strcmp(widget_get_prop_str(view, "name", ""), "scroll_view_streamsset");
}

/* The list_view layouter's vtable slot. A list whose asset default_item_height is the set's
 * settings row (only the iPod settings pages) gets settings rows: items stock made SET_STOCK_ROW
 * high take it before the stock layout stacks them and sizes the scroll view, then each row is
 * mapped. Settings code never moves, resizes or scrolls its rows afterwards
 * (docs/ipod.md#settings). A set whose pitch is its settings row (Classic) gives every re-pitched
 * list an item_height (tools/ipod.py), which stock forces on each row: a grid's rows (the artist
 * page's albums, 160 px list_items of 110 px tiles) keep their own height, or each would be one
 * pitch high, and stock's list painter, which culls a row by its own box, would skip the tiles'
 * covers and names wherever a repaint misses that box, under the selection card it paints. */
int ipod_list_layout(void *layout, void *view) {
    void *list = view ? P(view, W_PARENT) : (void *)0;
    int settings = settings_list(list);
    int row = stream_list(view) ? geom_val(V_STREAM_ROW) : settings ? geom_val(V_SET_ROW) : 0;
    if (settings) settings_layout(list);
    unsigned n = row ? widget_count_children(view) : 0;
    for (unsigned i = 0; i < n; ++i) {
        void *item = widget_get_child(view, i);
        void *button = I(item, W_H) == SET_STOCK_ROW ? set_button(item) : (void *)0;
        if (button) I(item, W_H) = set_row_h(button, row);
    }
    int forced = list ? I(list, ROW_HEIGHT) : 0, grid = 0; /* list_view's item_height */
    for (unsigned i = 0, k = forced ? widget_count_children(view) : 0; i < k && !grid; ++i) {
        void *item = widget_get_child(view, i),
             *b = widget_count_children(item) ? widget_get_child(item, 0) : (void *)0;
        grid = b && !tk_strcmp(widget_get_type(b), "button") && tile_in(b, list);
    }
    if (grid) I(list, ROW_HEIGHT) = 0; /* this layout only: settings_list reads it */
    int ret = ((int (*)(void *, void *))LIST_VIEW_LAYOUT)(layout, view);
    if (grid) I(list, ROW_HEIGHT) = forced;
    /* Stock neither places nor sizes a hidden row (no keep_invisible): mapped at 0x0 its button
     * would stay empty once shown (Display's Accent), so it is mapped after its first layout. */
    for (unsigned i = 0; i < n; ++i) {
        void *item = widget_get_child(view, i);
        if (widget_get_visible(item)) set_row(item);
    }
    return ret;
}

/* Replaces home_page_init's boot resume, navigator_to_with_context("playing_page", ctx) with ctx
 * {queue, index, class, mode}. Car mode (mode 2, plays) keeps that call; otherwise this is the
 * paused start playing_page_init would make, without the page. */
void ringnav_boot(const char *page, const int *ctx) {
    if (ctx[3] == 2)
        navigator_to_with_context(page, ctx);
    else if (ctx[2] != 0xff)
        player_start((void *)ctx[0], ctx[1], ctx[2], ctx[3]);
}

/* Called by stock long Return and short Return on Home, after their gates.
 * Elsewhere it opens Now Playing over the page, remembered as np_origin; on Now Playing it goes
 * back to that page while it lies right under it, else Home. With Hold Return: Home it goes Home
 * from any page but Home itself, where it still opens Now Playing.
 * Over Quick Settings the hold closes it, as Return does (ringnav), and acts on the page under it
 * once it has gone (quick_poll): Now Playing opened over the dialog left it open underneath, and
 * stock's pull-down starts none while one is (g_dropdown_menu_state), so Now Playing had no Quick
 * Settings until Return had gone back to the one left open and closed it (#303). Over a prompt it
 * cancels it.
 * ponytail: every way leaves the latch to the stock release filter, as stock's own Home call here
 * did: the hold's key-up, where it reaches the filter, is swallowed there; clearing it let that
 * release act on Home, and since V1.34 go straight back from the Now Playing it had opened (#303).
 * A release that never arrives leaves it for chord_gate's next press to clear. */
int compact_now_playing(void) {
    pull_cancel();
    drop_input();
    void *wm = window_manager();
    void *top = window_manager_get_top_window(wm);
    fx_cancel();
    if (usable() && top && !window_manager_is_animating(wm)) {
        const char *name = widget_get_prop_str(top, "name", "");
        if (!tk_strcmp(name, "statusbar_dialog")) {
            if (!dialog_quit(top, 0)) st.qs_hold = (unsigned)time_now_ms(); /* not one refused */
            return 0;
        }
        /* A prompt (Shut Down? and ours) is usable, but Now Playing opened under its modal loop
         * and the prompt came back over it: a second Shut Down? (#303). The hold cancels it, as
         * the prompt's own Return does (dialog_quit 4, 0x499bdc). */
        if (!tk_strcmp(name, "confirminfo_dialog")) {
            dialog_quit(top, 4);
            return 0;
        }
        pod_settings();
        /* Classic's hold leaves a list open on Home for Home's main list */
        int classic = classic_pages();
        if (st.hold_home && classic && coverflow_home_back(top)) {
            /* a list open on Home: its main list */
        } else if (st.hold_home && tk_strcmp(name, "home_page")) {
            st.np_origin = (void *)0;
            if (classic) coverflow_home_top();
            navigator_back_to_home();
        } else if (!tk_strcmp(name, "playing_page")) {
            /* ponytail: np_origin is only compared, never followed; a reused address goes Back
             * instead of Home */
            unsigned n = st.np_origin ? widget_count_children(wm) : 0;
            int back = n >= 2 && widget_get_child(wm, n - 1) == top &&
                       widget_get_child(wm, n - 2) == st.np_origin;
            st.np_origin = (void *)0;
            if (back) {
                navigator_back();
            } else {
                if (classic) coverflow_home_top();
                navigator_back_to_home();
            }
        } else {
            static const int context[4] = { 0, 0, 0xff, 2 };
            st.np_origin = top;
            navigator_switch_to_with_context("playing_page", context, 0);
        }
    }
    return 0;
}

/* Play/Pause hold queue menu (docs/internals.md). Stock long press fires once per press, so a hold
 * on a local song, album, artist/composer/genre, folder or playlist row opens the stock sortselect
 * dialog rebuilt as that row's menu. */
enum {
    QM_SONG = 1,
    QM_ALBUM,
    QM_GROUP,
    QM_FOLDER,
    QM_COVERFLOW,
    QM_COVERALBUM,
    QM_PLAYING,
    QM_PLAYLIST
};
enum {
    QA_NEXT = 1,
    QA_ADD,
    QA_SHUFFLE,
    QA_FAV,
    QA_UNFAV,
    QA_PLAYLIST,
    QA_ALBUM,
    QA_ARTIST,
    QA_ADV_SHUFFLE,
    QA_REPEAT,
    QA_GROUP,
    QA_NEXT_GROUP,
    QA_PREV_GROUP,
    QA_RATE,
    QA_RESUME,
    QA_QUEUE,
    QA_FOLDER,
    QA_CHAPTERS,
    QA_MORE,
    QA_NEW_PLAYLIST,
    QA_EDIT_PLAYLISTS
};

static char *play_key(void) {
    char *s = window_manager_get_input_device_status(window_manager());
    for (int i = 0; s && i < INPUT_KEY_COUNT; ++i)
        if (I(s, INPUT_KEYS + i * INPUT_KEY_SIZE) == KEY_PLAY)
            return s + INPUT_KEYS + i * INPUT_KEY_SIZE;
    return (char *)0;
}

/* Any Play/Pause release ends the latch; only the held press itself is swallowed. */
static int hold_released(void) {
    char *k = play_key();
    int held = k && st.qm_press && *(unsigned long long *)(k + INPUT_KEY_TIME) == st.qm_press;
    st.qm_press = 0;
    return held;
}

/* A record's fields from REC_NAME to last, and its CUE range: to REC_PATH its identity, to
 * REC_ARTIST its tags too. */
static unsigned rec_hash_to(void *r, int last) {
    unsigned h = FNV_SEED;
    for (int o = REC_NAME; o <= last; o += 4) h = fnv(h, P(r, o));
    return hash_bytes(h, (const unsigned char *)r + REC_CUE_START, 8);
}
/* The row a hold menu is on: an album row (a cover's) or a playlist's is told apart by its tags. */
static unsigned rec_hash(void *r) { return rec_hash_to(r, REC_ARTIST); }

/* Everything a row's tracks are resolved from; a change while the menu is open cancels it. */
static unsigned browse_hash(void) { return local_hash(hash_bytes(FNV_SEED, g_folder_path, 1024)); }

/* Coverflow's or Most Played's tracks, when page is theirs. */
static void *page_tracks(void *page) {
    return !page                ? (void *)0
           : page == st.mp_page ? st.mp_list
           : page == st.rt_page ? st.rt_list
                                : coverflow_tracks(page);
}

/* A queue's identity: its records' names, paths and CUE ranges, not their tags, which card_tags
 * fills as a song starts (the queue is not changed by that). */
unsigned queue_hash(void *q) {
    unsigned h = FNV_SEED;
    for (unsigned i = 0, n = deque_size(q); i < n; ++i) {
        unsigned r = rec_hash_to(deque_at(q, i), REC_PATH);
        h = hash_bytes(h, (const unsigned char *)&r, sizeof r);
    }
    return h;
}

static void *qm_list(void) {
    if (st.qm_kind == QM_PLAYING) return P(mcl_pdeqplaylist, 0);
    if (st.qm_kind == QM_PLAYLIST) return P(p_deque_playlist, 0);
    return st.qm_kind == QM_COVERFLOW ? page_tracks(window_manager_get_top_window(window_manager()))
                                      : P(p_deque_showlist, 0);
}

void *qm_record(void) {
    if (st.qm_kind == QM_PLAYING) {
        unsigned at, n;
        void *r = queue_now(&at, &n), *q = P(mcl_pdeqplaylist, 0);
        unsigned type = (unsigned)MCL(MCL_TYPE);
        return r && q == st.qm_queue && at == st.qm_idx && n == st.qm_rows &&
                       (type == 1 || (type & 0xf000) == 0xf000) && airplayGetFlag() != 2 &&
                       rec_hash(r) == st.qm_hash && queue_hash(q) == st.qm_queue_hash
                   ? r
                   : 0;
    }
    if (st.qm_kind == QM_COVERALBUM) {
        void *r = coverflow_album(window_manager_get_top_window(window_manager()));
        return r && rec_hash(r) == st.qm_hash ? r : 0;
    }
    void *list = qm_list();
    if (!list || deque_size(list) != st.qm_rows || st.qm_idx >= st.qm_rows ||
        (st.qm_kind < QM_COVERFLOW && browse_hash() != st.qm_browse))
        return (void *)0;
    void *r = deque_at(list, st.qm_idx);
    return r && rec_hash(r) == st.qm_hash ? r : (void *)0;
}

/* The queue menu's album's album_id, 0 when not an album it rates: a Coverflow cover's, a Library
 * Albums row's (split as Coverflow's), an artist's albums tab row's (ringnav_artist_list). */
unsigned qm_album_id(void *r) {
    if (st.qm_kind == QM_COVERALBUM || (st.qm_kind == QM_ALBUM && st.qm_cls == CLASS_ALBUMS))
        return album_id(r);
    return st.qm_kind == QM_ALBUM && st.qm_cls == CLASS_ARTIST_ALBUMS && st.art_ids &&
                   st.art_n == st.qm_rows && st.qm_idx < st.art_n
               ? st.art_ids[st.qm_idx]
               : 0;
}

/* Now Playing's More… (img_more, on both layouts), while that page is open; else 0. */
static void *np_more(void) {
    if (st.np_win) return widget_lookup(st.np_win, "img_more", 1);
    return (void *)0;
}

static void qm_close(void) {
    void *dialog = st.qm_dialog;
    st.qm_dialog = (void *)0;
    if (dialog) window_close(dialog);
}

/* Return and the title bar arrow dismiss without the stock sort-change flag. */
static int qm_back(void *dialog, void *event) {
    if (I(event, EVENT_TYPE) == EVT_KEY_UP && I(event, EVENT_KEY) != KEY_RETURN) return 0;
    qm_close();
    return STOP;
}

static int qm_gone(void *dialog, void *event) {
    if (st.qm_dialog == dialog) st.qm_dialog = (void *)0;
    return 0;
}

/* An album, artist/composer/genre or folder row: the query stock would run for it, as
 * batch_add_file (0x4ff344) expands each class. Artist 0xf004/0xff01 rows name the artist at +0x18,
 * composer 0xf005/0xff02 at +0x20, genre 0xf006/0xff03 at +0x1c; classinfo +0xa.. flags Unknown. */
static const unsigned char qm_by[] = { REC_ARTIST, 0x20, 0x1c };
static int class_query(unsigned cls, void *r) {
    unsigned g = (cls & 0xf) - (cls < 0xff00 ? 4 : 1);
    int artist = I(g_artist_type, 0) == 1, id = I(r, REC_ID);
    if (cls == CLASS_ALBUMS) return getMusicByAlbum(id == -1 ? (const char *)0 : P(r, REC_ALBUM));
    if (cls < 0xff00)
        return (g == 2   ? getMusicByGenre
                : g      ? getMusicByComposer
                : artist ? getMusicByAlbumArtist
                         : getMusicBySonger)(id == -1 ? (const char *)0 : P(r, qm_by[g]));
    /* load_album_detaillist 0xff01 -> load_localclass_list 0xff11, and its composer/genre kin */
    return (g == 2   ? getMusicByAlbumAndGenre
            : g      ? getMusicByAlbumAndComposer
            : artist ? getMusicByAlbumAndAlbumSonger
                     : getMusicByAlbumAndSonger)(
        P(r, REC_ALBUM), g_local_classinfo_save[0xa + g] ? (const char *)0 : P(r, qm_by[g]),
        id == -2);
}

/* A playlist's songs, as its row opens them (cd_num, as ple_open). */
static int qm_query(void *r) {
    return st.qm_kind == QM_PLAYLIST ? getMusicByPlayList(I(r, REC_DISC))
                                     : class_query(st.qm_cls, r);
}

/* An Albums row (load_album_detaillist) or Go to album opens its name's songs, 0xff10 with
 * classinfo +0xd the name and +0x10d the artist: r is kept, a copy, to keep its album alone. */
static void album_open(void *r) {
    if (st.album) deque_destroy(st.album);
    st.album = 0;
    if (!r || I(r, REC_ID) < 0) return;
    st.album = _create_deque("stSongInfo");
    deque_init(st.album);
    _deque_push_back(st.album, r);
}
int ringnav_album_detail(int cls, void *r) {
    album_open(cls == CLASS_ALBUMS ? r : 0);
    return stock_detail_trampoline(cls, r);
}
/* The opened album, when the classinfo 0xff10 loads is still its name and artist. */
static void *album_opened(void) {
    const char *info = (const char *)g_local_classinfo_save;
    void *a = st.album ? deque_at(st.album, 0) : 0;
    const char *name = a ? P(a, REC_ALBUM) : 0, *artist = a ? P(a, REC_ARTIST) : 0;
    int same = name && artist && !info[9] && !strcasecmp(info + 0xd, name);
    return same && !strcmp(info + 0x10d, artist) ? a : 0;
}

/* Stock's album art cache, /mnt/mmc/.sldp/<md5 of the name>0.jpg (1.jpg big), keys by name, so
 * split Albums rows of a name shared a cover: theirs adds the album artist, else the album's
 * folder. A name of one album keeps its key and cached art. buf holds 256 bytes, as stock's. */
static int twin(void *o, void *r) {
    const char *x = P(o, REC_ALBUM), *y = P(r, REC_ALBUM);
    return x && !strcasecmp(x, y) && album_cmp(o, r);
}
static const char *art_key(char *buf, void *list, unsigned i) {
    void *r = deque_at(list, i);
    const char *name = P(r, REC_ALBUM), *aa = P(r, REC_ALBUM_ARTIST), *path = P(r, REC_PATH);
    if (!name || !((i && twin(deque_at(list, i - 1), r)) ||
                   (i + 1 < deque_size(list) && twin(deque_at(list, i + 1), r))))
        return name;
    if (aa && *aa)
        snprintf(buf, 256, "%s\x1f%s", name, aa);
    else
        snprintf(buf, 256, "%s\x1f%.*s", name, path ? (int)album_dir(path, 1) : 0,
                 path ? path : "");
    return buf;
}
/* The Albums row renderer's snprintf(buf, 0x100, "%s", album) (0x4a52bc), a2 the row. */
int ringnav_art_name(char *buf, unsigned size, unsigned row, const char *album) {
    char key[256];
    void *list = P(p_deque_showlist, 0);
    return snprintf(buf, size, "%s", row < deque_size(list) ? art_key(key, list, row) : album);
}
/* albumcoverinfo_init's and foldercoverinfo_init's _deque_push_back of each row's cover task
 * (0x4f79c4, 0x4f7b6c): the list's copy (0x5c639c) duplicates the task's name and path, which stock
 * malloc'd for it and never freed. */
void ringnav_cover_push(void *covers, void *task) {
    _deque_push_back(covers, task);
    free(P(task, 4)), free(P(task, 8));
}
/* A list's albumcoverinfo_init: the art thread hashes each cover task's copy of its row's name
 * (+4, read under aclist_mutex): key renames a row's, or returns 0 to keep it. */
static int rename_covers(void *list, const char *(*key)(char *, void *, unsigned)) {
    int result = albumcoverinfo_init(list);
    void *covers = P(pdeq_albumcoverlist, 0);
    char buf[256], *s;
    const char *k;
    pthread_mutex_lock((void *)aclist_mutex);
    for (unsigned i = 0; i < deque_size(list) && i < deque_size(covers); ++i) {
        void *r = deque_at(covers, i);
        char **name = (char **)((char *)r + 4);
        if ((k = key(buf, list, i)) && (s = strdup(k))) {
            if (strcmp(*name, s)) ((unsigned char *)r)[0] = ((unsigned char *)r)[1] = 0;
            free(*name), *name = s;
        }
    }
    pthread_mutex_unlock((void *)aclist_mutex);
    return result;
}
static const char *split_key(char *buf, void *list, unsigned i) {
    return art_key(buf, list, i) == buf ? buf : 0;
}
/* The Albums page's albumcoverinfo_init calls. */
int ringnav_albumcovers(void *list) { return rename_covers(list, split_key); }

/* A job can finish after navigation rebuilt the list: its row number now belongs to another
 * album. Compare the key copied by the worker before setting either cover-ready flag. */
void ringnav_cover_done(int row, int big, const char *key) {
    pthread_mutex_lock((void *)aclist_mutex);
    void *covers = P(pdeq_albumcoverlist, 0);
    if ((unsigned)row < deque_size(covers) && (big == 0 || big == 1)) {
        void *r = deque_at(covers, (unsigned)row);
        const char *name = P(r, 4);
        if (name && key && !strcmp(name, key)) ((unsigned char *)r)[big] = 1;
    }
    pthread_mutex_unlock((void *)aclist_mutex);
}

/* An artist's albums (load_localartist_list 1) group by name too, and a name there is usually one
 * row though another artist's album shares it, so each row takes its album's art_key in the split
 * Albums order: the same-named album in its folder, else by its album artist. ponytail: an album
 * across folders whose album artist isn't the row's keeps stock's key; look up its track's
 * albumsonger by fileurl if that shows. */
static void artist_keys_free(void) {
    for (unsigned i = 0; i < st.art_n; ++i) free(st.art_keys[i]);
    free(st.art_keys);
    free(st.art_ids);
    st.art_keys = 0, st.art_ids = 0, st.art_n = 0;
}
static int same_dir(void *a, void *b) {
    const char *x = P(a, REC_PATH), *y = P(b, REC_PATH);
    unsigned n = x ? album_dir(x, 1) : 0;
    return n && y && album_dir(y, 1) == n && !memcmp(x, y, n);
}
/* r's album among all's rows of its name: by folder, else album artist as r's artist; or -1. */
static int artist_album(void *all, void *r) {
    const char *name = P(r, REC_ALBUM), *artist = P(r, REC_ARTIST);
    for (int by_dir = 1; by_dir >= 0; --by_dir)
        for (unsigned j = 0; name && j < deque_size(all); ++j) {
            void *a = deque_at(all, j);
            const char *x = P(a, REC_ALBUM), *aa = P(a, REC_ALBUM_ARTIST);
            if (x && !strcasecmp(x, name) &&
                (by_dir ? same_dir(a, r) : aa && artist && !strcasecmp(aa, artist)))
                return (int)j;
        }
    return -1;
}
extern int coverflow_artist(void *albums);
extern void coverflow_by_year(void *albums);
static int artist_songs(void *unused) {
    return (I(artist_type, 0) == 1 ? getMusicByAlbumArtist : getMusicBySonger)(
        g_local_classinfo_save[0xa] ? (const char *)0
                                    : (const char *)g_local_classinfo_save + 0x10d);
}
/* Split artwork keys use the whole library; the layout uses only this artist's albums. */
static void *artist_albums(void *all) {
    int n;
    void *songs = staged_music(artist_songs, 0, &n), *out = _create_deque("stSongInfo");
    deque_init(out);
    for (unsigned i = 0; i < deque_size(all); ++i) {
        void *a = deque_at(all, i);
        for (unsigned j = 0; j < deque_size(songs); ++j) {
            void *s = deque_at(songs, j);
            const char *name = P(s, REC_ALBUM);
            if (I(a, REC_ID) < 0 ? !name || !*name : !album_cmp(a, s)) {
                _deque_push_back(out, a);
                break;
            }
        }
    }
    deque_destroy(songs);
    return out;
}
/* The artist page's grid rows in mine's order (artist_albums, by year), as artist_album finds
 * them; rows it misses keep their order after. */
static void artist_order(void *list, void *mine) {
    unsigned n = deque_size(list), m = deque_size(mine);
    int *at = calloc(n + 1, sizeof *at);
    if (!at) return;
    for (unsigned i = 0; i < n; ++i) at[i] = artist_album(mine, deque_at(list, i));
    void *out = _create_deque("stSongInfo");
    deque_init(out);
    for (unsigned k = 0; k <= m; ++k) /* k == m: the unmatched */
        for (unsigned i = 0; i < n; ++i)
            if (k < m ? at[i] == (int)k : at[i] < 0) _deque_push_back(out, deque_at(list, i));
    free(at);
    deque_clear(list);
    deque_assign(list, out);
    deque_destroy(out);
}
static int artist_cf_gone(void *win, void *event) {
    if (win == st.art_cf_win) st.art_cf_win = (void *)0;
    if (win == st.art_cf_veil) st.art_cf_veil = (void *)0;
    return 0;
}
/* The page Coverflow is about to cover (an artist's, or Playlists from playlists.c) opens unseen
 * and without its slide, so the list cuts straight to the covers, as Home's Coverflow card does;
 * once they are up it is shown again under them, its slide back for its own Return. */
void cf_veil(void *win, int on) {
    if (on) {
        tk_snprintf(st.art_cf_hint, sizeof st.art_cf_hint, "%s",
                    widget_get_prop_str(win, "anim_hint", ""));
        widget_set_prop_str(win, "anim_hint", "");
    } else
        widget_set_prop_str(win, "anim_hint", st.art_cf_hint);
    widget_set_opacity(win, on ? 0 : 0xff);
}
static int artist_cf_open(const void *unused) {
    void *top = window_manager_get_top_window(window_manager()), *all = st.art_cf_list,
         *veiled = st.art_cf_veil;
    st.art_cf_timer = 0;
    st.art_cf_list = st.art_cf_veil = (void *)0;
    if (veiled && top == veiled && coverflow_artist(all))
        st.art_cf_win = top;
    else
        deque_destroy(all);
    if (veiled) cf_veil(veiled, 0);
    return 0;
}
/* Artist albums: Grid gives the artist page its stock toolbar back, for its multi-select
 * (s_img_mulsel), which iPod hides on the local pages; the tabs move down under it. A toolbar
 * already showing (a reload) is left alone. */
static void artist_bar(void) {
    void *top = window_manager_get_top_window(window_manager()),
         *nav = top ? widget_lookup(top, "view_navbar", 0) : 0,
         *tab = top ? widget_lookup(top, "tab_control", 0) : 0,
         *pages = tab ? widget_lookup(tab, "pages_artistinfo", 0) : 0, *album;
    char h[12], y[12];
    pod_settings();
    if (st.artist_cf || !nav || !pages || widget_get_visible(nav) ||
        tk_strcmp(widget_get_prop_str(top, "name", ""), "artistinfo_page"))
        return;
    int height = I(tab, W_H) - I(nav, W_H), inner = height - I(pages, W_Y);
    widget_set_visible(nav, 1, 0);
    widget_set_enable(nav, 1);
    widget_move_resize(tab, I(tab, W_X), I(nav, W_H), I(tab, W_W), height);
    tk_snprintf(y, sizeof y, "%d", I(pages, W_Y));
    tk_snprintf(h, sizeof h, "%d", inner);
    widget_set_self_layout_params(pages, "0", y, "100%", h);
    if ((album = widget_lookup(pages, "list_view_album", 1))) /* its height is fixed, not 100% */
        widget_move_resize(album, I(album, W_X), I(album, W_Y), I(album, W_W), inner);
    widget_layout(tab);
}
static int artist_list(int tab) {
    int n = stock_artist_trampoline(tab);
    void *list = P(p_deque_showlist, 0), *all;
    /* An artist with no name: its title (the navbar's, the bar's) is the language's "Unknown
     * Artist", as Songs' rows say, not glibc's "(null)" */
    if (!strcmp((const char *)ARTIST_TITLE, "(null)"))
        getFormatString("player_unknown", (char *)ARTIST_TITLE, 0x200);
    artist_bar();
    artist_keys_free();
    if (tab != 1 || n <= 0 || !(st.art_keys = calloc((unsigned)n, sizeof(char *)))) return n;
    st.art_ids = calloc((unsigned)n, sizeof *st.art_ids);
    st.art_n = (unsigned)n;
    all = _create_deque("stSongInfo");
    deque_init_copy(all, list);
    coverflow_split(all);
    /* The artist's albums by year (Playback settings' Year order) or A-Z, the grid's rows in
     * theirs. */
    void *mine = artist_albums(all);
    coverflow_by_year(mine);
    artist_order(list, mine);
    for (unsigned i = 0; i < st.art_n && i < deque_size(list); ++i) {
        void *r = deque_at(list, i);
        int j = artist_album(all, r);
        const char *k;
        char buf[256];
        if (j >= 0 && (k = split_key(buf, all, (unsigned)j))) st.art_keys[i] = strdup(k);
        if (j >= 0 && st.art_ids) st.art_ids[i] = album_id(deque_at(all, (unsigned)j));
    }
    /* Artist albums: Coverflow: the albums go to Coverflow once the page is up, once per page,
     * so coming back from it shows the stock grid. */
    pod_settings();
    void *top = window_manager_get_top_window(window_manager());
    if (st.artist_cf && !st.art_cf_list && top && top != st.art_cf_win &&
        !tk_strcmp(widget_get_prop_str(top, "name", ""), "artistinfo_page")) {
        st.art_cf_list = mine, mine = 0;
        st.art_cf_veil = top;
        cf_veil(top, 1);
        widget_on(top, EVT_DESTROY, artist_cf_gone, top);
        rearm(&st.art_cf_timer, artist_cf_open, 0);
    }
    if (mine) deque_destroy(mine);
    deque_destroy(all);
    return n;
}
int ringnav_artist_list(int tab) {
    int saved = library_music;
    library_music = 1;
    int n = artist_list(tab);
    library_music = saved;
    return n;
}
static const char *artist_key(char *buf, void *list, unsigned i) {
    return st.art_keys && list == P(p_deque_showlist, 0) && st.art_n == deque_size(list) &&
                   i < st.art_n
               ? st.art_keys[i]
               : 0;
}
/* The artist page's albums renderer's snprintf of the name (0x4adb4c), a2 the row. */
int ringnav_artist_art_name(char *buf, unsigned size, unsigned row, const char *album) {
    const char *k = artist_key(0, P(p_deque_showlist, 0), row);
    return snprintf(buf, size, "%s", k ? k : album);
}
/* The artist page's albumcoverinfo_init calls. */
int ringnav_artist_covers(void *list) {
    return st.art_keys ? rename_covers(list, artist_key) : albumcoverinfo_init(list);
}
/* The album page's big cover, snprintf of classinfo +0xd (0x4a910c): its Albums row's cover task
 * name, as the thread hashed it, when that is this album's key. */
int ringnav_art_header(char *buf, unsigned size, const char *fmt, const char *album) {
    void *covers = P(pdeq_albumcoverlist, 0);
    unsigned i = (unsigned)I(ALBUMINFO_ROW, 0), n = strlen(album);
    const char *key = i < deque_size(covers) ? P(deque_at(covers, i), 4) : 0;
    return snprintf(buf, size, fmt,
                    key && !strncasecmp(key, album, n) && key[n] == 0x1f ? key : album);
}

/* An empty Library list (Albums, Artists, Composers, Genres): once stock marks its page opened
 * (the "stage" property the window manager sets after the open animation), the page is closed
 * again and the toast says so over the list it came from. Stock opened a black page, and an empty
 * Albums grid's Return then did nothing until a restart (Hold Return: Home, back_to_home, still
 * left it); the cause was not found in the window manager's close path, so the page goes before a
 * Return can reach it, with the forced close that path uses. The page is its class's
 * (g_class_type), on top, whichever of the two list pages shows it, polled every EMPTY_POLL_MS
 * until it opens or stops being on top. */
static int empty_toast(const void *unused) {
    static const char *const none[] = { "No albums", "No artists", "No composers", "No genres" };
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    const char *name = top ? widget_get_prop_str(top, "name", "") : "";
    int mine = (!tk_strcmp(name, "album_page") || !tk_strcmp(name, "localclass_page")) &&
               I(g_class_type, 0) == CLASS_ALBUMS + st.empty_kind;
    if (mine && widget_get_prop_int(top, "stage", 0) != WINDOW_STAGE_OPENED) {
        timer_add(empty_toast, (void *)0, EMPTY_POLL_MS);
        return 0;
    }
    if (mine) window_manager_close_window_force(wm, top);
    toast(none[st.empty_kind]);
    return 0;
}

/* Library Artists rows have no head picture, so the name gets its width (compact_row_layout skips
 * hidden siblings). Composers and Genres share the rows, which are rebound, so they show theirs
 * again, and Classic's one-line rows show none. */
int ringnav_class_icon(void *row, int i) {
    int artists = I(g_class_type, 0) == 0xf004;
    int shown = !artists && geom(G_TEXT_ART); /* no class icon on a one-line row */
    widget_set_visible(widget_lookup(row, "img_icon", 1), shown, 0);
    return shown ? class_icon(row, i) : 0;
}

/* load_localclass_list: stock fills p_deque_showlist and returns its size (Playlists and Baidu:
 * their own deque's). getAllAlbum,
 * getAllArtist (and its album-artist twin), getAllComposer and getAllGenre end with an Unknown row
 * (id -1) whenever the library has songs; it goes when the query its press runs finds none. Stock
 * clears the staging deque before returning, and so does this. Stock never sets g_artist_type,
 * which picks the album-artist queries for Artists and an artist's albums, so those lists always
 * went by the Artist tag; it follows artist_type, the setting the artist page itself uses.
 * getAllAlbum groups by name alone: Albums is split as Coverflow is (album artist, else folder),
 * and the name's songs an album row opens (0xff10) keep only that album (album_open). An empty
 * list toasts what it lacks. */
/* list_get_img_pic: a song row's format badge (img_flac, img_mp3, ...) by its extension. "null" is
 * stock's no badge, which hides it, as an iPod's rows carry none. */
int ringnav_badge(const char *path, char *out, int size) {
    tk_snprintf(out, (unsigned)size, "null");
    return 0;
}
/* A deque's songs by path and CUE start (listen_key), open addressing at most half full: slot j + 1
 * for record j, so a key's first record in deque order is met first. calloc'd, 0 if that fails. */
unsigned *rec_index(void *songs, unsigned *mask) {
    unsigned n = deque_size(songs), size = 2, *slot;
    while (size < 2 * n) size <<= 1;
    *mask = size - 1;
    if (!(slot = calloc(size, sizeof *slot))) return 0;
    for (unsigned j = 0; j < n; ++j) {
        void *s = deque_at(songs, j);
        if (!P(s, REC_PATH)) continue;
        unsigned h = listen_key(s);
        while (slot[h & *mask]) ++h;
        slot[h & *mask] = j + 1;
    }
    return slot;
}
/* The first of rec_index's songs with r's path and CUE start, else 0. */
void *rec_find(void *songs, const unsigned *slot, unsigned mask, void *r) {
    const char *path = P(r, REC_PATH);
    for (unsigned h = path ? listen_key(r) : 0; path && slot[h & mask]; ++h) {
        void *s = deque_at(songs, slot[h & mask] - 1);
        if (!strcmp(P(s, REC_PATH), path) && I(s, REC_CUE_START) == I(r, REC_CUE_START)) return s;
    }
    return 0;
}
/* My Fav's, Frequent's and Recent's rows are stock's copies of the songs as added or played, some
 * with an empty title though the library has the tag (a song played from Folder): an empty one
 * takes the library's, by path, so it reads as every other list's instead of falling back to its
 * file name and track number.
 * ponytail: the library is staged and indexed once per list load (O(library)) when any row is
 * untitled; keep an index across loads if that shows. */
static void fav_titles(void *list, int n) {
    void *songs = 0;
    unsigned mask = 0, *slot = 0;
    for (int i = 0; i < n; ++i) {
        void *r = deque_at(list, (unsigned)i), *s;
        char **title = (char **)((char *)r + REC_TITLE), *t;
        if (I(r, REC_TYPE) != 8 || (*title && **title) || !P(r, REC_PATH)) continue;
        int k;
        if (!songs && (songs = staged(songs_unsorted, 0, &k))) slot = rec_index(songs, &mask);
        if (!slot || !(s = rec_find(songs, slot, mask, r))) continue;
        const char *lib = P(s, REC_TITLE);
        if (lib && *lib && (t = strdup(lib))) free(*title), *title = t;
    }
    free(slot);
    if (songs) deque_destroy(songs);
}
/* Playlists bypass songtable; filter their displayed copies, never their stored rows (Cover
 * Flow's turned playlist too). */
int music_records(void *list, int discovery) {
    void *music = _create_deque("stSongInfo");
    deque_init(music);
    for (unsigned i = 0; i < deque_size(list); ++i) {
        void *r = deque_at(list, i);
        const char *path = P(r, REC_PATH);
        int hidden = discovery ? library_scope_hidden(path) : spoken(path);
        if (I(r, REC_TYPE) != 8 || !hidden) _deque_push_back(music, r);
    }
    deque_clear(list);
    deque_assign(list, music);
    deque_destroy(music);
    return (int)deque_size(list);
}
/* Library -> Songs (load_localclass_list's default): stock reads into the staging deque and copies
 * that to the showlist, the library held twice; this reads straight into the showlist. */
static int songs_sorted(void *unused) { return getAllMusic(I(g_sort_flag, 0)); }
static int songs_load(void) {
    void *list = P(p_deque_showlist, 0);
    deque_clear(list);
    staged_into(list, songs_sorted, 0);
    deque_clear(P(tools_pdeq_directory, 0)); /* as stock leaves it */
    music_nagvation_title(0xf001);
    return (int)deque_size(list);
}

int ringnav_localclass(int cls) {
    I(g_artist_type, 0) = I(artist_type, 0) == 1;
    int saved = library_music;
    library_music = 1;
    int n = cls == 0xf001 ? songs_load() : stock_localclass_trampoline(cls);
    void *list = P(p_deque_showlist, 0), *dir = P(tools_pdeq_directory, 0), *a = album_opened();
    if (n > 0 && cls == 0xff20) n = music_records(list, 0);
    /* These stored lists bypass songtable too. Keep Discovery out of their displayed copies,
     * without removing favourites/history or changing explicit playlist and folder queues. */
    if (n > 0 && (cls == 0xf00a || cls == 0xf008 || cls == 0xf007)) n = music_records(list, 1);
    if (n > 0 && cls == 0xff10 && a) album_only(list, a, 0), n = (int)deque_size(list);
    if (n > 0 && cls == CLASS_ALBUMS) n = coverflow_split(list);
    if (n > 0 && (cls == 0xf00a || cls == 0xf008 || cls == 0xf007)) /* My Fav, Frequent, Recent */
        fav_titles(list, n);
    /* an untagged song's row reads its file name without the extension (track_name). Bounded by
     * list's own size: Playlists (0xf00b) counts p_deque_playlist's rows, Baidu's (0xff26) its own.
     */
    for (int i = 0, k = (int)deque_size(list); i < n && i < k; ++i) {
        void *r = deque_at(list, (unsigned)i);
        char buf[512], **title = (char **)((char *)r + REC_TITLE), *t;
        if (I(r, REC_TYPE) != 8 || (*title && **title) ||
            !(t = strdup(track_name(buf, sizeof buf, r) ?: "")))
            continue;
        free(*title), *title = t;
    }
    if (cls < CLASS_ALBUMS || cls > 0xf006) {
        library_music = saved;
        return n;
    }
    if (n > 0 && I(deque_at(list, (unsigned)n - 1), REC_ID) == -1) {
        library_first = 1; /* a song is enough: its query stops at the first (library_query) */
        class_query((unsigned)cls, deque_at(list, (unsigned)n - 1));
        library_first = 0;
        int empty = !deque_size(dir);
        deque_clear(dir);
        if (empty) deque_pop_back(list), n--;
    }
    library_music = saved;
    if (n <= 0) { /* say so, not a black page; once the page that loads it is up */
        st.empty_kind = cls - CLASS_ALBUMS;
        timer_add(empty_toast, (void *)0, 0);
    }
    return n;
}

static int dir_query(void *path) { return toolsLoadDirectory((const char *)path); }
/* A folder's songs and, in stock's listing order, its subfolders' (dot folders such as .tidal
 * left out), so a folder of albums queues them all.
 * ponytail: one toolsLoadDirectory per folder on the UI thread; a big tree takes a while. */
static void folder_tracks(const char *path, void *add, int depth) {
    int n;
    void *rows = staged(dir_query, (void *)path, &n);
    for (unsigned i = 0; i < deque_size(rows); ++i) {
        void *t = deque_at(rows, i);
        const char *name = P(t, REC_NAME);
        if (I(t, REC_TYPE) == 8 && !playback_video(t))
            _deque_push_back(add, t);
        else if (I(t, REC_TYPE) == 4 && depth < 8 && name && *name != '.') {
            char sub[1024];
            if ((unsigned)tk_snprintf(sub, sizeof sub, "%s/%s", path, name) < sizeof sub)
                folder_tracks(sub, add, depth + 1);
        }
    }
    deque_destroy(rows);
}

/* The tracks stock would play for that row, in its order; a folder's take in its subfolders. */
static void qm_tracks(void *r, void *add) {
    int n;
    if (st.qm_kind == QM_FOLDER) {
        char path[1024];
        tk_snprintf(path, sizeof(path), "%s/%s", (const char *)g_folder_path,
                    (const char *)P(r, REC_NAME));
        ringnav_wdt_long();
        folder_tracks(path, add, 0);
        return;
    }
    void *rows = staged_music(qm_query, r, &n);
    if (st.qm_kind == QM_PLAYLIST) music_records(rows, 0); /* as its opened list (0xff20) */
    for (unsigned i = 0; i < deque_size(rows); ++i) {
        void *t = deque_at(rows, i);
        if (I(t, REC_TYPE) == 8) _deque_push_back(add, t);
    }
    deque_destroy(rows);
}

/* The row's tracks: the song itself, or what stock would play for the row; an Albums row's own
 * album alone (getMusicByAlbum finds every album of the name), in album order, as Coverflow's. */
void *qm_collect(void *r) {
    if (st.qm_kind == QM_COVERALBUM || (st.qm_kind == QM_ALBUM && st.qm_cls == CLASS_ALBUMS))
        return coverflow_album_tracks(r);
    void *add = _create_deque("stSongInfo");
    deque_init(add);
    if (st.qm_kind == QM_SONG || st.qm_kind == QM_COVERFLOW || st.qm_kind == QM_PLAYING) {
        if (!playback_video(r)) _deque_push_back(add, r);
    } else
        qm_tracks(r, add);
    return add;
}

/* Insert at pos+1 or append; playback_queue_insert keeps every queue index right. Shuffle picks at
 * random, so the first inserted track is forced once (ringnav_shuffle). */
static void qm_insert(void *queue, void *add, int next) {
    unsigned at = playback_queue_insert(queue, add, next);
    if (next) {
        /* stock Shuffle's only: advanced Shuffle Songs (also MCL_MODE 2) plays its own order */
        st.qm_forced = MCL(MCL_MODE) == 2 && !playback_option(0) ? at + 1 : 0;
        st.qm_forced_hash = rec_hash(deque_at(queue, at));
    }
}

static int qm_apply(void *add, int next) {
    void *queue = P(mcl_pdeqplaylist, 0);
    if (!queue || airplayGetFlag() == 2) return 0;
    unsigned size = deque_size(queue), type = (unsigned)MCL(MCL_TYPE), n = deque_size(add);
    /* Only a local (folder or library) queue grows; streams keep theirs. */
    if (size && type != 1 && (type & 0xf000) != 0xf000) return 0;
    int album = st.qm_kind == QM_COVERFLOW || st.qm_kind == QM_COVERALBUM;
    if (n && !size) { /* loads without starting playback; only a real folder is class 1 */
        if (album) album_memory(deque_at(add, 0));
        mclLoadPlayList(add, 0, st.qm_kind == QM_FOLDER ? 1 : album ? 0xff10 : (int)st.qm_cls);
    } else if (n)
        qm_insert(queue, add, next);
    return n != 0;
}

/* A 2 s modal dialog: its loop runs every ringnav_sleep poll again, so a poll clears or latches
 * what it toasts first (logo_poll). */
void toast(const char *text) {
    struct {
        int kind, ms;
        char text[0x400];
    } info = { 1, 2000, { 0 } };
    for (unsigned i = 0; text[i] && i < sizeof info.text - 1; ++i) info.text[i] = text[i];
    ++st.toasts; /* its loop runs the UI's polls, which may toast again */
    navigator_to_with_context("dialog/msginfo_dialog", &info);
    --st.toasts;
}

int all_songs(void *music) {
    int saved = library_music;
    library_music = music != 0;
    int n = getAllMusic(0);
    library_music = saved;
    return n;
}

/* Shuffle does not need the expensive pinyin title sort; groups order their tracks themselves.
 * Non-zero music leaves audiobooks and podcasts out, as the Library's Artists and Albums
 * (MUSIC_WHERE). */
int songs_unsorted(void *music) {
    int saved = library_music;
    library_music = music != 0;
    int n = getAllMusic(3);
    library_music = saved;
    return n;
}

/* Return for the payload's own pages; each passes page_open its own close. */
int page_keyup(void *ctx, void *event) {
    if (I(event, EVENT_KEY) != KEY_RETURN) return 0;
    navigator_back();
    return STOP;
}

/* The close of navigation.c's own pages: Chapters and Streaming's Configuration. */
static int page_closed(void *ctx, void *event) {
    if (ctx == st.ch_page) {
        st.ch_page = 0;
        if (st.ch && !st.ch->done)
            chapters_cancel(st.ch);
        else {
            free(st.ch);
            st.ch = 0;
        }
    }
    if (ctx == st.sc_page) st.sc_page = 0, st.sc_armed = 0;
    return 0;
}

#define RESUME_FILE "/mnt/data/ringnav-resume" /* coverflow.c's blob_io */
#define PLAYS_FILE "/mnt/data/ringnav-plays"

/* key's saved place, 0 for none. */
static int spot_at(unsigned key) {
    if (!st.spots_read) {
        st.spots_read = 1;
        if (!BLOB_IO(RESUME_FILE, st.spots, 0)) /* earlier releases kept 32 */
            blob_io(RESUME_FILE, RESUME_FILE, st.spots, 32 * sizeof *st.spots, 0);
    }
    for (int i = 0; i < RESUME_SLOTS; i++)
        if (st.spots[i].key == key) return st.spots[i].sec;
    return 0;
}

void plays_load(void) {
    if (!st.plays_read) {
        st.plays_read = 1;
        BLOB_IO(PLAYS_FILE, st.plays, 0);
    }
}

/* A play count's key: the path's hash, with a CUE track's start mixed in, since a CUE image's
 * tracks share one path. Never 0, which marks a free slot. */
unsigned listen_key(void *r) {
    unsigned key = fnv(FNV_SEED, P(r, REC_PATH));
    int cue = I(r, REC_CUE_START);
    if (cue) key = hash_bytes(key, (const unsigned char *)&cue, sizeof cue);
    return key | !key;
}

/* Chapters are read only while this snapshot is open: an M4B's, or a spoken file's in any format
 * ffmpeg reads them from, such as an MP3 book's ID3 chapters. A spoken CUE track other than an
 * M4B's has none, as before: its sheet lists its chapters. An M4B CUE track's rows are moved to
 * its own clock in chapter_poll. */
static int chapter_file(void *r) {
    const char *path = P(r, REC_PATH), *ext = path ? strrchr(path, '.') : 0;
    if (ext && !strcasecmp(ext, ".m4b")) return 1;
    return spoken(path) && !I(r, REC_CUE_START) && !I(r, REC_CUE_END);
}
static int chapter_pick(void *ctx, void *event) {
    unsigned i = (unsigned)(long)ctx;
    if (st.ch && st.ch->done && !st.ch->failed && i < (unsigned)st.ch->count &&
        np_track() == st.ch_track && (mclGetPlayStatus() == 2 || mclGetPlayStatus() == 3)) {
        /* Suppress even a resume poll that has not yet settled this track. */
        unsigned at, n;
        void *r = queue_now(&at, &n);
        st.ch_key = listen_key(r);
        st.rs_pending = 0;
        st.rs_go_key = 0;
        st.scrub_track = st.ch_track;
        st.scrub_to = st.ch->rows[i].sec;
        st.scrub = st.scrub_moved = 1;
        if (st.np_win) playing_timer_clear(st.np_win);
        if (st.np_slider) widget_set_prop_int(st.np_slider, "value", st.scrub_to);
        if (st.np_elapsed) clock_text(st.np_elapsed, 0, st.scrub_to);
        scrub_end();
    }
    navigator_back();
    return 0;
}
static int chapter_poll(const void *unused) {
    if (!chapters_poll(st.ch, time_now_ms())) return 8;
    st.ch_timer = 0;
    if (!st.ch_page) {
        free(st.ch);
        st.ch = 0;
        return 0;
    }
    /* ffmpeg's times are the file's; the player's clock and seek start at a CUE track's start. Rows
     * move to the track's clock: the chapter playing at its start shows at 0:00, earlier ones go,
     * and so do those from its end on. Untitled rows keep the file's numbering. */
    int n = 0;
    for (int i = 0; !st.ch->failed && i < st.ch->count; ++i) {
        chapter *c = &st.ch->rows[i];
        int sec = c->sec - st.ch_cue;
        if (st.ch_end && c->sec >= st.ch_end) break;
        if (!*c->title) tk_snprintf(c->title, sizeof c->title, "Chapter %d", i + 1);
        if (st.ch_cue && sec <= 0) {
            n = 0;
            sec = 0;
        }
        if (n != i) st.ch->rows[n] = *c;
        st.ch->rows[n++].sec = sec;
    }
    st.ch->count = n;
    void *view =
        page_list_or(st.ch_page, st.ch_page, "Chapters",
                     st.ch->failed ? "Could not read chapters" : "No embedded chapters", n, 64);
    int sec = 0, total, current = 0;
    player_playtime_and_length(&sec, &total);
    for (int i = 0; i < n; ++i) {
        char at[32];
        chapter *c = &st.ch->rows[i];
        tk_snprintf(at, sizeof at, "%d:%02d:%02d", c->sec / 3600, c->sec / 60 % 60, c->sec % 60);
        page_row_detail(view, i, c->title, at, chapter_pick);
        if (c->sec <= sec) current = i;
    }
    if (n) ringnav_select(view, current, n);
    return 0;
}
static void chapters_open(void *r) {
    if (st.ch || !chapter_file(r)) return;
    if (!mp4_fits(P(r, REC_PATH))) {
        toast("Too long to read chapters on the Q2");
        return;
    }
    st.ch_track = np_track();
    st.ch_cue = I(r, REC_CUE_START);
    st.ch_end = I(r, REC_CUE_END);
    st.ch = chapters_start(P(r, REC_PATH), time_now_ms());
    if (!st.ch) {
        toast("Could not read chapters");
        return;
    }
    st.ch_page = page_open("chapters_page", page_closed, page_keyup);
    st.ch_timer = timer_add(chapter_poll, 0, 50);
    if (!st.ch_page || !st.ch_timer) {
        /* No polling timer: kill and reap the child before releasing its storage. */
        chapters_cancel(st.ch);
        if (st.ch->pid > 0) {
            int status;
            while (waitpid(st.ch->pid, &status, 0) < 0 && *__errno_location() == 4) {}
        }
        free(st.ch);
        st.ch = 0;
        if (st.ch_page) navigator_back();
        stop_timer(&st.ch_timer);
        return;
    }
    page_list(st.ch_page, st.ch_page, 0, "Loading chapters…", 0, 64);
}

/* Streaming's Configuration (docs/internals.md#streaming-configuration): Tidal's and Spotify's
 * cache caps, 512 MB to 8 GB, each kept in its folder's .cache-size as "<MB>M", librespot's
 * --cache-size-limit form, which the card's Spotify launcher reads when it starts librespot. Files,
 * not config.ini: tidal.c's trim reads Tidal's on its download thread, and blob_io renames it into
 * place, so a half-written one is never read. And a row that stops every stream and add-on. */
static const char *const sc_dirs[] = { TIDAL_DIR, SPOT_DIR };

/* The choice i, the cap being 512 << i MB: 2 (2 GB) when the file is missing or holds another. */
int stream_cache(int spotify) {
    char path[48], s[12];
    tk_snprintf(path, sizeof path, "%s/.cache-size", sc_dirs[spotify]);
    state_read(path, s, sizeof s);
    unsigned mb = (unsigned)atoi(s + 1);
    for (int i = 0; i < 5; ++i)
        if (mb == 512u << i) return i;
    return 2;
}

/* Row i's caption: a cache row's size, or Erase Spotify Data's state. */
static void sc_text(int i) {
    static const char *const sizes[] = { "512 MB", "1 GB", "2 GB", "4 GB", "8 GB" };
    char s[40];
    if (i < 2)
        tk_snprintf(s, sizeof s, "%s Cache: %s", i ? "Spotify" : "Tidal", sizes[stream_cache(i)]);
    widget_set_text_utf8(widget_get_child(st.sc_row[i], 0),
                         i < 2            ? s
                         : shelf_erase(0) ? "Erasing…"
                         : st.sc_armed    ? "Press again to erase the login and cache"
                                          : "Erase Spotify Data");
}

/* netupdate.c's erase ended: .cache-size went with it, so Spotify's row reads 2 GB again. */
void stream_erased(void) {
    if (st.sc_page) sc_text(1), sc_text(2);
}

/* The services Streaming lists, each shown or hidden by a check row on its Configuration page:
 * 0 stock's Tidal, 1 Spotify, the card apps (addon.c's order), then Tidal Downloads, Internet
 * Radio and AirPlay. Q2POD STREAMHIDE in config.ini holds the hidden ones' keys, comma separated:
 * a fixed row's upper case one, a card app's id (lower case, so never one of those), so a hidden
 * app stays hidden whatever its place. Missing, every row shows. Configuration is never hidden. */
static const char *const svc_fixed[][2] = { { "TIDAL", "TIDAL" },
                                            { "SPOTIFY", "Spotify" },
                                            { "DOWNLOADS", "Tidal Downloads" },
                                            { "RADIO", "Internet Radio" },
                                            { "AIRPLAY", "AirPlay" } };
/* Service i's key (label 0) or row label (1), as Streaming last listed them. */
static const char *svc(int i, int label) {
    if (i >= 2 && i < 2 + st.svc_n) return label ? addon_name(i - 2) : addon_id(i - 2);
    return svc_fixed[i < 2 ? i : i - st.svc_n][label];
}
void svc_hidden(char *s) {
    s[0] = 0; /* as root_read: a missing key leaves s alone */
    toolsReadConfig("/mnt/data/config.ini", "Q2POD", "STREAMHIDE", s, "");
}
char *svc_find(char *hide, int i) {
    const char *key = svc(i, 0);
    return key ? csv_find(hide, key) : 0;
}
/* A switch row's Centre: the service hidden, or shown again, and saved; its switch and its row on
 * Streaming (the page under this one) follow at once. ponytail: an uninstalled app's key stays
 * in STREAMHIDE (hidden again if it comes back); a list full of them (ROOT_MAX) refuses more. */
static int svc_toggle(int i) {
    char hide[SVC_HIDE];
    const char *key = svc(i, 0);
    if (!key) return 0;
    svc_hidden(hide);
    /* ROOT_MAX, not sizeof hide: stock's config line (a 257-byte sprintf, a 255-byte fgets) */
    int hidden = csv_toggle(hide, ROOT_MAX, key);
    if (hidden < 0) {
        toast("Could not save setting");
        return 0;
    }
    toolsWriteConfig("/mnt/data/config.ini", "Q2POD", "STREAMHIDE", hide);
    list_toggle(st.sc_check[i], svc(i, 1), !hidden);
    /* The page's rows, and a set with home_lib's list on Home's window, by window: Home's comes
     * first and may be the empty one. */
    void *wm = window_manager();
    for (unsigned k = 0, windows = widget_count_children(wm); k < windows; ++k) {
        void *view = widget_lookup(widget_get_child(wm, k), "scroll_view_streamsset", 1);
        for (unsigned c = 0, rows = view ? widget_count_children(view) : 0; c < rows; ++c)
            if (widget_get_prop_int(widget_get_child(view, c), SVC_PROP, 0) == i + 1)
                widget_set_visible(widget_get_child(view, c), !hidden, 0);
        if (view) widget_layout(view);
    }
    return 0;
}

/* A cache row steps to the next size and saves it: Tidal's applies at the next download's trim,
 * Spotify's when librespot next starts. Erase Spotify Data wants a second press in a row, as the
 * App Store's Remove: any other row's press or leaving the page drops the first. Stop Streaming
 * Services ends librespot and its restart loop, a radio station, the card apps (addon.c) and a
 * stock stream (Tidal's) queue, so the keys and the output are local music's again; local music
 * already stopped is left as it is. */
static int sc_click(void *ctx, void *event) {
    int i = (int)(long)ctx;
    st.sc_armed = i == 2 && !st.sc_armed && !shelf_erase(0); /* erasing: says to wait */
    if (i == 2 && !st.sc_armed) shelf_erase(1);
    sc_text(2);
    if (i > 4) return svc_toggle(i - 5);
    if (i == 2) return 0;
    if (i == 3) {
        unsigned type = (unsigned)MCL(MCL_TYPE);
        spot_stop();
        radio_stop();
        addon_stop();
        if (type != 1 && (type & 0xf000) != 0xf000 && mclGetPlayStatus() != 1) player_stop();
        toast("Streaming stopped");
        return 0;
    }
    if (i == 1 && shelf_erase(0)) return toast("Wait for the one in progress"), 0; /* it rm -rfs */
    char path[48], tmp[52], s[8];
    int n = tk_snprintf(s, sizeof s, "%uM", 512u << (stream_cache(i) + 1) % 5);
    tk_snprintf(path, sizeof path, "%s/.cache-size", sc_dirs[i]);
    tk_snprintf(tmp, sizeof tmp, "%s.tmp", path);
    mkdir(sc_dirs[i], 0755);
    if (!blob_io(path, tmp, s, (unsigned)n, 1)) toast("Could not save setting");
    sc_text(i);
    return 0;
}

int stream_config(void *ctx, void *event) {
    if (st.sc_page || !(st.sc_page = page_open("streamconf_page", page_closed, page_keyup)))
        return 0;
    int n = SVC_FIXED + st.svc_n;
    void *view = page_list(st.sc_page, st.sc_page, 0, "Configuration", 5 + n, 48);
    for (int i = 0; i < 3; ++i)
        st.sc_row[i] = page_row_detail(view, i, "", 0, sc_click), sc_text(i);
    page_row_detail(view, 3, "Stop Streaming Services", 0, sc_click);
    /* a gap before the toggles, clear as the rows (the theme's default list_item fills) */
    int rh = page_h(48);
    widget_use_style(list_item_create(view, 0, 4 * rh, 375, rh), "s_listitem_black");
    char hide[SVC_HIDE];
    svc_hidden(hide);
    for (int i = 0; i < n; ++i) {
        st.sc_check[i] = widget_get_child(page_row_detail(view, 5 + i, "", 0, sc_click), 0);
        list_toggle(st.sc_check[i], svc(i, 1), !svc_find(hide, i));
    }
    return 0;
}

/* 1 + the MEDIA folder s names (up to its end or a '/'), 0 for none. MEDIA is letters only. */
static int media_kind(const char *s) {
    for (int k = 0; k < VIDEOS; k++) {
        unsigned n = strlen(MEDIA[k]);
        if (!strncasecmp(s, MEDIA[k], n) && (!s[n] || s[n] == '/')) return k + 1;
    }
    return 0;
}

/* Folder view lists, in the card's Videos folder, its videos (playback.c ringnav_audio_extension):
 * stock lists audio files only, so their rows never reached ringnav_folder_click. */
int folder_videos(void) {
    const char *p = (const char *)g_folder_path;
    return tk_str_start_with(p, "/mnt/mmc/") && media_kind(p + 9) == VIDEOS;
}

/* A track in Podcasts or Audiobooks: always resumed, never counted or scrobbled. Shared (peq.h). */
int spoken(const char *path) {
    const char *ext = path ? strrchr(path, '.') : 0;
    return (ext && !strcasecmp(ext, ".m4b")) ||
           (path && tk_str_start_with(path, "/mnt/mmc/") && media_kind(path + 9) - 1u < AUDIOBOOKS);
}

/* A spoken CUE track's book: the chapter of list (r's file's, the last starting at or before the
 * saved place) to resume, its index among list's songs (*at) and the seconds into it (*off); 0
 * without a saved place. */
static void *book_place(void *list, void *r, int *at, int *off) {
    if (!list || !r || !(I(r, REC_CUE_START) || I(r, REC_CUE_END)) || !spoken(P(r, REC_PATH)))
        return 0;
    unsigned key = fnv(FNV_SEED, P(r, REC_PATH));
    int place = spot_at(key), j = 0;
    void *best = 0;
    for (unsigned i = 0, n = deque_size(list); place && i < n; i++) {
        void *t = deque_at(list, i);
        if (I(t, REC_TYPE) != 8) continue;
        if (fnv(FNV_SEED, P(t, REC_PATH)) == key && I(t, REC_CUE_START) <= place &&
            (!best || I(t, REC_CUE_START) > I(best, REC_CUE_START)))
            best = t, *at = j;
        j++;
    }
    if (best) *off = place - I(best, REC_CUE_START);
    return best;
}

/* The card's folder for kind into path (sizeof st.media_root, which a MEDIA name always fits); 0
 * when there is none. */
int media_find(int kind, char *path) {
    void *dir = opendir("/mnt/mmc");
    int found = 0;
    for (struct dirent *e; dir && !found && (e = readdir(dir));)
        if ((e->d_type == 4 || !e->d_type) && media_kind(e->d_name) == kind)
            found = tk_snprintf(path, sizeof st.media_root, "/mnt/mmc/%s", e->d_name) > 0;
    if (dir) closedir(dir);
    return found;
}

/* A Local Music row's, or Home's Shortcut's (Podcasts, Audiobooks): the folder found again, as the
 * card may have changed since; without it, a toast. */
int media_click(void *ctx, void *event) {
    int kind = (int)(long)ctx;
    char root[sizeof st.media_root], t[48];
    if (!media_find(kind, root)) {
        tk_snprintf(t, sizeof t, "No %s folder on the card", MEDIA[kind - 1]);
        toast(t);
    } else if (kind == PHOTOS)
        photos_open(root);
    else if (kind == BOOKS)
        books_open(root);
    else {
        memcpy(st.media_root, root, sizeof root);
        st.media_open = 1;
        navigator_to("folder_page");
    }
    return 0;
}

/* Go to Folder: a card song's folder into path (sizeof st.go_folder) and its g_folder_layer, as
 * folder_enter counts (the card 2), so stock Back climbs to the card; 0 when off the card, too
 * long, holding a name stock's enter never builds ("", "." or "..") or no longer a directory. */
static int song_folder(void *r, char *path) {
    const char *p = P(r, REC_PATH);
    unsigned info[36], layer = 2;
    if (!p || strlen(p) <= 9 || memcmp(p, "/mnt/mmc/", 9)) return 0;
    unsigned n = (unsigned)(strrchr(p, '/') - p);
    if (n >= sizeof st.go_folder) return 0;
    memcpy(path, p, n);
    path[n] = 0;
    for (unsigned i = 8, j; i < n; i = j) { /* path[i] is the '/' before a name */
        for (j = i + 1; j < n && path[j] != '/'; ++j) {}
        unsigned len = j - i - 1;
        if (!len || (path[i + 1] == '.' && (len == 1 || (len == 2 && path[i + 2] == '.'))) ||
            ++layer > 255) /* g_folder_layer is a byte */
            return 0;
    }
    return !__xstat(3, path, info) && (info[5] & 0170000) == 0040000 ? (int)layer : 0;
}

/* folder_page_init lists the storage roots at g_folder_layer 1, a root's folders at 2 and deeper
 * folders at 3 on (folder_enter). Videos browse here too, folder by folder. A media root becomes a
 * layer-3 folder, so stock's reload, title (its name, folder_reinit_navbarname) and entering all
 * treat it as one. Each open starts a fresh visit (folder_reset_variable): folder_back pops the
 * enter history kept for each layer and leaves an M3U before stepping up, and a visit left without
 * Back (Home, playing) keeps both. */
int ringnav_folder(void *win, void *ctx) {
    int result = stock_folder_trampoline(win, ctx), layer = 3;
    char root[265];
    const char *path = st.media_root;
    if (!st.media_open || !win) {
        int card = win && !access("/tmp/mmc_add", 0);
        st.media_root[0] = 0;
        /* No drive and a card (stock's check_tfstatus): the card is the only root, so open it. */
        if (card && !g_otg_status) memcpy(st.media_root, "/mnt/mmc", 9), layer = 2;
        /* Folder Root: an ordinary folder below the card, so Back walks up to it, then as above. */
        if (card && root_dir(root)) path = root, layer = 3;
        /* Go to Folder, checked as it was picked (song_folder) */
        if (card && st.go_layer) path = st.go_folder, layer = st.go_layer;
    }
    if (path[0]) {
        folder_reset_variable();
        memcpy((char *)g_folder_path, path, strlen(path) + 1);
        g_folder_layer = layer;
        folder_reload_data();
        folder_reinit_navbarname();
        folder_refresh(win);
    }
    st.media_open = st.go_layer = 0;
    return result;
}

/* folder_load_top: stock lists SD and OTG whatever is plugged in, and OTG alone opens a full
 * page saying there is none; without a drive (g_otg_status) the card is the only root. */
void ringnav_folder_top(void) {
    stock_folder_top_trampoline();
    void *roots = P(p_deque_showlist, 0);
    if (!g_otg_status && deque_size(roots) == 2) deque_pop_back(roots);
}

/* A folder level's list, stock's table on the open page, pushes in as a page_list level does. */
static void folder_push(int dir) {
    if (!theme()->motion[MOTION_INPLACE].ms) return;
    void *table =
        widget_lookup(window_manager_get_top_window(window_manager()), "tableview_folder", 1);
    if (table) page_push(widget_get_child(table, 0), dir);
}

/* Back at a shortcut or bypassed SD root (media_root, each its own layer) leaves the page (stock's
 * layer 0). */
int ringnav_folder_back(void *yoffset, void *index) {
    if (st.media_root[0] && !tk_strcmp((const char *)g_folder_path, st.media_root)) {
        g_folder_layer = 1;
        st.media_root[0] = 0;
    }
    int layer = g_folder_layer, result = stock_folder_back_trampoline(yoffset, index);
    if (g_folder_layer < layer && g_folder_layer) folder_push(-1);
    return result;
}

/* folder_page's row click (stock's, 0x532414: the tapped or Centre-pressed row's index into
 * p_deque_showlist, then folder_enter, whose 8, a file, it queues as songs): a video plays as from
 * Library's Videos row (books_video). Select mode (g_navbar_status) and everything else is stock's.
 */
int ringnav_folder_click(void *ctx, void *event) {
    void *row = event && !g_navbar_status ? table_row_of(P(event, EVENT_TARGET)) : (void *)0;
    void *list = P(p_deque_showlist, 0);
    unsigned i = row ? (unsigned)I(row, ROW_INDEX) : ~0u;
    if (i < deque_size(list)) {
        void *r = deque_at(list, i);
        if (I(r, REC_TYPE) == 8 && books_video(P(r, REC_PATH))) return 0;
    }
    int layer = g_folder_layer, result = stock_folder_click_trampoline(ctx, event);
    if (g_folder_layer > layer) folder_push(1);
    return result;
}

/* usbmode_page_init, as a cable to a computer opens USB mode, before stock unmounts the card for
 * USB storage: the card apps, which write to it (downloads, uploads), end first, so nothing of
 * theirs keeps it busy (they run from /tmp: addon.c), as do Download Album Lyrics' job and
 * playlist art's worker. */
int ringnav_usbmode(void *a, void *b, void *c, void *d) {
    addon_card_away();
    album_lyrics_stop();
    playlist_art_stop();
    return stock_usbmode_trampoline(a, b, c, d);
}

/* mclStartPlayer, every local track's start: a playing librespot or radio station lets the DAC's
 * PCM go first. A track too long for RAM fails as stock's own checks do, with -1; so, silently,
 * does any start while a card game has the sound (addon.c): a jack or USB DAC change and a
 * Bluetooth drop come here through player_save_start, and the game keeps its PCM. */
int ringnav_start_player(void) {
    if (addon_game_audio()) return -1;
    if (!playback_start_fits()) return -1;
    headset_manual();
    spot_yield();
    radio_stop();
    playback_track_speed();
    return stock_start_player_trampoline();
}

/* systemset_about_page_init: stock's seven rows (0x4c0de4) and its thanks (about_more, a 100 px
 * list_item of wrapped text, the eighth child, dropped here: stock builds it and never reads it
 * again), each row a s_listitem_black list_item holding a 335x70 s_btn_listitem button, a 166px
 * s_scrlabel_white24l title at x 10 and a 149px s_scrlabel_white20r value at x 176, both focus-only
 * scrolling with ellipses. FW. Version (row 1) reads demo's version literal, which carries the
 * updater tag (tools/build.py TAG), so it shows the stock firmware's again; a CFW. Version row in
 * the same widgets follows it. Its button has no name, so stock's row click (atoi of the name,
 * 0x4c12f8) ignores it. */
static void about_label(void *button, int x, int w, const char *style, const char *text) {
    void *label = hscroll_label_create(button, x, 0, w, 70);
    widget_use_style(label, style);
    hscroll_label_set_only_focus(label, 1);
    hscroll_label_set_ellipses(label, 1);
    widget_set_text_utf8(label, text);
    theme_font(label, x < 100 ? ROLE_TITLE : ROLE_META, FONT_ROW);
}

int ringnav_about(void *win, void *ctx) {
    int result = stock_about_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_about", 1) : (void *)0;
    if (view && widget_count_children(view) == 8) {
        widget_destroy(widget_get_child(view, 7)); /* stock's thanks */
        widget_set_text_utf8(widget_get_child(widget_get_child(widget_get_child(view, 1), 0), 1),
                             STOCK_VERSION);
        void *button = list_button(view);
        about_label(button, 10, 166, "s_scrlabel_white24l", "CFW. Version");
        about_label(button, 176, 149, "s_scrlabel_white20r", Q2POD_VERSION);
        widget_restack(P(button, W_PARENT), 2);
    }
    return result;
}

/* Go to album/artist reuse g_local_classinfo_save, which the pages underneath reload from when
 * they come back, so it is put back as the page opened here closes. */
static int qm_restore(void *page, void *event) {
    memcpy((void *)g_local_classinfo_save, st.qm_classinfo, sizeof st.qm_classinfo);
    return 0;
}

/* Now Playing's More rows, for this record: Album info (0x5397cc) fills the album query and opens
 * playerjumpinfo_page; Artist info opens artistinfo_page with {class, record}. A song played in
 * Coverflow, or with Artist albums: Coverflow, goes to the album's tracks in Coverflow instead, as
 * Go to Artist reaches its covers. A card app's song goes to the app's page when it offers one
 * (addon.c addon_goto). */
static void qm_goto(void *r, int album) {
    unsigned char *info = (unsigned char *)g_local_classinfo_save;
    void *wm = window_manager(), *page = window_manager_get_top_window(wm);
    /* a card app's song (Qobuz's): the app's own page for its album or artist, when it says so */
    if (addon_goto(P(r, REC_PATH), P(r, album ? REC_ALBUM : REC_ARTIST), album)) return;
    if (album && (navigator_window_is_exist("coverflow_page") || ringnav_artist_view()) &&
        coverflow_goto_album(r))
        return;
    memcpy(st.qm_classinfo, info, sizeof st.qm_classinfo);
    if (album) {
        info[9] = 0;
        tk_snprintf((char *)info + 0xd, 0x100, "%s", (const char *)P(r, REC_ALBUM));
        tk_snprintf((char *)info + 0x10d, 0x100, "%s", (const char *)P(r, REC_ARTIST));
        *(int *)info = 0xff10;
        album_open(r);
        navigator_to("playerjumpinfo_page");
    } else {
        struct {
            int cls;
            void *r;
        } context = { (int)st.qm_cls, r };
        navigator_to_with_context("localmusic/artistinfo_page", &context);
    }
    void *top = window_manager_get_top_window(wm);
    if (top != page) widget_on(top, EVT_WINDOW_CLOSE, qm_restore, top);
}

/* Deferred so the dialog is never closed under its own click dispatch. */
static int qm_open(const void *unused);

/* New Playlist (edit 0) and Edit Playlists… run once the dialog is destroyed, so the Playlists page
 * is on top however its close animates (playlists.c's click acts on the top page only), and from
 * the pass after, so the covers never close inside the dialog's destroy. */
static int qm_edit_run(const void *info) {
    playlists_edit((int)(long)P(info, TIMER_CTX));
    return 0;
}
static int qm_edit_due(void *edit, void *event) {
    timer_add(qm_edit_run, edit, 0);
    return 0;
}

static int qm_run(const void *unused) {
    st.qm_timer = 0;
    if (!st.qm_dialog) return 0;
    if (st.qm_action == QA_NEW_PLAYLIST || st.qm_action == QA_EDIT_PLAYLISTS)
        widget_on(st.qm_dialog, EVT_DESTROY, qm_edit_due,
                  (void *)(long)(st.qm_action == QA_EDIT_PLAYLISTS));
    qm_close();
    void *r = qm_record();
    int a = st.qm_action, cls = st.qm_kind == QM_COVERFLOW ? 0xf001 : (int)st.qm_cls;
    unsigned aid = r ? qm_album_id(r) : 0; /* an album's own Favourites and Rate */
    if (r && a >= 100) {
        if (!playback_set(st.qm_choice - 1, a - 100)) toast("Queue unchanged");
        st.qm_choice = 0;
    } else if (r && a == QA_CHAPTERS) {
        chapters_open(r);
    } else if (r && a == QA_QUEUE) {
        /* A shuffle's play order is not the list's: ours (Up Next). A Play Next stock's Shuffle was
         * holding (ringnav_shuffle) goes first in it, as advanced play has no forced pick. */
        if (playback_listable()) {
            void *queue = P(mcl_pdeqplaylist, 0);
            unsigned at = st.qm_forced - 1;
            st.qm_forced = 0;
            if (queue && at < deque_size(queue) &&
                rec_hash(deque_at(queue, at)) == st.qm_forced_hash)
                playback_move(at);
            up_next(page_keyup);
        } else
            navigator_to("playerqueue_page");
    } else if (a == QA_MORE && st.qm_kind == QM_PLAYLIST) {
        if (r) navigator_to_with_context("localmusic/playlistsmore_page", (void *)(long)st.qm_idx);
    } else if (a == QA_NEW_PLAYLIST || a == QA_EDIT_PLAYLISTS) {
        /* the page's own, not the playlist's: qm_edit_due */
    } else if (a == QA_MORE) {
        void *more = np_more(); /* clicked as a tap would, so stock's own handler opens it */
        char click[0x30];
        if (more) stock_dispatch_trampoline(more, pointer_event_init(click, EVT_CLICK, more, 0, 0));
    } else if (r && (a == QA_NEXT_GROUP || a == QA_PREV_GROUP)) {
        if (!playback_group_skip(a == QA_NEXT_GROUP)) toast("No other group");
    } else if (aid && a == QA_RATE) {
        rate_show(window_manager_get_top_window(window_manager()), rate_kept(1, aid), aid);
    } else if (aid && (a == QA_FAV || a == QA_UNFAV)) {
        if (rate_saved(rate_keep(1, aid, a == QA_FAV ? 5 : 0))) rate_album_favs(r, a == QA_FAV);
    } else if (r && a == QA_RATE) {
        rate_open();
    } else if (r && a >= QA_ADV_SHUFFLE && a <= QA_GROUP) {
        st.qm_choice = a - QA_ADV_SHUFFLE + 1;
        qm_open(0);
    } else if (r && a == QA_FAV) { /* tags a folder file as batch-select's Add to My Fav does */
        qm_select();
        batch_add_file(cls, 0xf00a, qm_list(), P(p_vector_select_record, 0), 0);
        ratings_copy_due();
    } else if (r && a == QA_UNFAV) {
        deleteMusicFromFav(r);
        ratings_copy_due();
        if (cls == 0xf00a)
            g_delete_flag = 1;          /* My Fav reloads, as Now Playing's heart (0x53cafc) */
    } else if (r && a == QA_PLAYLIST) { /* the playlist page's add mode adds the selected row */
        qm_select();
        navigator_to_with_context(
            "localmusic/playlist_page",
            (void *)(long)((st.qm_kind == QM_PLAYING ? 0x20000 : 0x10000) | cls));
        if (st.qm_kind == QM_PLAYING) {
            void *top = window_manager_get_top_window(window_manager());
            if (!tk_strcmp(widget_get_prop_str(top, "name", ""), "playlist_page")) {
                st.qm_playlist = top;
                qm_playlist_bind(top, 0, qm_playlist_pick);
                widget_on(top, EVT_DESTROY, qm_playlist_gone, top);
            }
        }
    } else if (r && a == QA_RESUME) { /* the list's songs from the chapter; the poll seeks */
        int at = 0, off = 0;
        void *list = qm_list(), *t = book_place(list, r, &at, &off), *songs;
        if (t && (songs = _create_deque("stSongInfo"))) {
            deque_init(songs);
            for (unsigned i = 0; i < deque_size(list); i++)
                if (I(deque_at(list, i), REC_TYPE) == 8) _deque_push_back(songs, deque_at(list, i));
            st.rs_go_key = listen_key(t);
            st.rs_go = off;
            st.rs_go_at = (unsigned)time_now_ms();
            play_folder(songs, at, 0xf001);
            deque_destroy(songs);
        } else
            toast("Queue unchanged");
    } else if (r && a == QA_FOLDER) {
        st.go_layer = song_folder(r, st.go_folder);
        if (st.go_layer)
            navigator_to("folder_page"); /* its init, ringnav_folder, opens there */
        else
            toast("Folder not found");
        st.go_layer = 0; /* never left for a later Folder visit */
    } else if (r && a >= QA_ALBUM)
        qm_goto(r, a == QA_ALBUM);
    else {
        void *add = r ? qm_collect(r) : (void *)0;
        if (!add || !(a == QA_SHUFFLE ? shuffle_play(add) : qm_apply(add, a == QA_NEXT)))
            toast("Queue unchanged");
        if (add) deque_destroy(add);
    }
    return 0;
}

/* Touch and centre both arrive as the row's click; the first one wins. */
static int qm_pick(void *action, void *event) {
    if (st.qm_dialog && !st.qm_timer) {
        st.qm_action = (int)(long)action;
        st.qm_timer = timer_add(qm_run, (void *)0, 0);
    }
    return 0;
}

/* Deferred out of the long-press dispatch. The sortselect init is synchronous and non-modal
 * (it clears to_modal), so the dialog is the top window once navigator_to returns. */
/* Hold menu rows: stock sortselect's list is 210, so three whole rows and the reading margin
 * (SCROLL_MARGIN) show at every scroll, never a half row under the title. */
#define QM_ROW 66
/* Under Classic (dialogs_painted) the hold menu is Cover Flow's flip panel
 * (docs/ipod.md#cover-flow) centred under the bar, opaque: QM_HEAD rows of header holding the
 * navbar's title, then the theme's page rows, QM_ROWS at most (past them it scrolls), all clear of
 * the glass. No dim; hold_paint draws the panel. */
#define QM_HEAD 44
#define QM_ROWS 6
static void hold_open(void *dialog, int n) {
    void *nav = widget_lookup(dialog, "view_navbar", 1),
         *list = widget_lookup(dialog, "list_view", 1);
    if (!nav || !list) return;
    int row = theme()->page_row, rows = n < QM_ROWS ? n : QM_ROWS, h = QM_HEAD + rows * row,
        y = BAR_ROWS + (320 - BAR_ROWS - h) / 2;
    void *title = widget_lookup(nav, "scrlabel_title", 1);
    widget_set_prop_str(dialog, "highlight", "");
    widget_set_prop_int(dialog, "style:normal:bg_color", 0);
    widget_set_prop_int(nav, "style:normal:bg_color", 0);
    widget_move_resize(nav, PANEL_X, y, BACK_W, QM_HEAD);
    widget_set_visible(widget_lookup(nav, "img_return", 1), 0, 0);
    widget_move_resize(title, 12, (QM_HEAD - 33) / 2, BACK_W - 24, 33);
    panel_font(title, 31, 1);
    text_ink(title, 0xffffffffu, INK_LABEL); /* white on the header in either half */
    widget_set_prop_int(list, "style:normal:bg_color", 0);
    widget_move_resize(list, PANEL_X, y + QM_HEAD, BACK_W, rows * row);
    widget_set_prop_str(widget_lookup(list, "scroll_view", 1), "self_layout",
                        "default(x=0,y=0,w=100%,h=100%)");
}
/* A row's label as the flip panel's track: the theme's face, left; Light Mode flips its white. */
static void hold_label(void *item, void *label) {
    widget_set_prop_int(item, "style:normal:bg_color", 0);
    widget_move_resize(label, 12, 0, BACK_W - 24, theme()->page_row);
    panel_font(label, 25, 1);
}
/* The title loops, as Now Playing's; stock's sortselect title doesn't. Its holds at its start and
 * end are every label's (ringnav_hscroll_start, ringnav_hscroll_lull). */
static void qm_title(void *dialog, const char *s) {
    void *title = widget_lookup(dialog, "scrlabel_title", 1);
    widget_set_prop_int(title, "loop", 1);
    widget_set_text_utf8(title, s);
}
static int qm_open(const void *unused) {
    st.qm_timer = 0;
    void *wm = window_manager(), *page = window_manager_get_top_window(wm);
    void *r = usable() && !window_manager_is_animating(wm) ? qm_record() : (void *)0;
    if (!r) return 0;
    navigator_to("dialog/sortselect_dialog");
    void *dialog = window_manager_get_top_window(wm);
    if (dialog == page) return 0;
    if (tk_strcmp(widget_get_prop_str(dialog, "name", ""), "sortselect_dialog")) {
        window_close(dialog);
        return 0;
    }
    /* The Play hold is a button, as Centre and Return: it ends a click's touch hiding
     * (hide_outline). A choice opens from a row's click, which may be a tap. */
    if (!st.qm_choice) st.touch_mode = 0;
    void *back = widget_lookup(dialog, "img_return", 1),
         *view = widget_lookup(dialog, "scroll_view", 1);
    widget_off_by_func(dialog, EVT_KEY_UP, (void *)SORTSELECT_KEYUP, dialog);
    widget_off_by_func(back, EVT_CLICK, (void *)SORTSELECT_CLOSE, dialog);
    widget_on(dialog, EVT_KEY_UP, qm_back, dialog);
    widget_on(back, EVT_CLICK, qm_back, dialog);
    widget_on(dialog, EVT_DESTROY, qm_gone, dialog);
    if (st.qm_choice) {
        /* stock's play modes, its category named as Group by sets it (album or folder) */
        static const char *const shuffle[2][5] = {
            { "Off", "All Songs", "Songs in Album", "Albums", "Albums and Songs" },
            { "Off", "All Songs", "Songs in Folder", "Folders", "Folders and Songs" }
        };
        static const char *const repeat[2][6] = { { "Play One Song", "Play One Album", "Play All",
                                                    "Repeat Song", "Repeat Album", "Repeat All" },
                                                  { "Play One Song", "Play One Folder", "Play All",
                                                    "Repeat Song", "Repeat Folder",
                                                    "Repeat All" } };
        static const char *const groups[] = { "Album", "Folder" };
        int kind = st.qm_choice - 1,
            count = kind == 0   ? 5
                    : kind == 1 ? 6
                                : 2,
            folder = playback_option(2);
        const char *const *names = kind == 0   ? shuffle[folder]
                                   : kind == 1 ? repeat[folder]
                                               : groups;
        qm_title(dialog, kind == 0 ? "Shuffle" : kind == 1 ? "Repeat" : "Group by");
        widget_destroy_children(view);
        int painted = dialogs_painted(), row = painted ? theme()->page_row : QM_ROW,
            w = painted ? BACK_W : 375;
        for (int i = 0; i < count; ++i) {
            void *item = list_item_create(view, 0, i * row, w, row);
            widget_use_style(item, "s_listitem_black");
            void *label = hscroll_label_create(item, 30, 0, 310, row);
            widget_use_style(label, "s_scrlabel_white24l");
            widget_set_text_utf8(label, names[i]);
            if (painted) hold_label(item, label);
            if (i == playback_option(kind))
                image_base_set_image(image_create(item, w - 35, 0, 24, row), "select");
            widget_on(item, EVT_CLICK, qm_pick, (void *)(long)(100 + i));
        }
        widget_set_prop_int(view, "virtual_h", count * row);
        if (painted) hold_open(dialog, count);
        st.qm_dialog = dialog;
        ringnav_select(view, playback_option(kind), count);
        return 0;
    }
    unsigned cls = st.qm_cls;
    int song = st.qm_kind == QM_SONG || st.qm_kind == QM_COVERFLOW || st.qm_kind == QM_PLAYING;
    const char *album = P(r, REC_ALBUM), *artist = P(r, REC_ARTIST);
    /* a song by its title: the player's parse names a folder play's untagged record */
    char name[512];
    const char *tag = song ? now_tag(r, REC_TITLE) : (void *)0;
    const char *title = (st.qm_kind == QM_ALBUM || st.qm_kind == QM_COVERALBUM) ? album
                        : st.qm_kind == QM_GROUP ? P(r, qm_by[(cls & 0xf) - 4])
                        : tag && *tag            ? tag
                        : song                   ? track_name(name, sizeof name, r)
                                                 : P(r, REC_NAME);
    if (st.qm_kind == QM_PLAYLIST && *st.qm_name) title = st.qm_name;
    qm_title(dialog, title ? title : "");
    widget_destroy_children(view);
    /* Songs favourite, as stock's heart, and go to their album and artist; collections shuffle.
     * Go to is left out where its single-instance page is already open, or the album is the page.
     */
    unsigned char acts[20], n = 0;
    /* Shuffle Albums/Folders only: skipping the group comes first */
    int group_first = st.qm_kind == QM_PLAYING && playback_option(0) >= 3;
    /* Rate (the heart's hold) follows Next/Previous album */
    if (group_first) acts[n++] = QA_NEXT_GROUP, acts[n++] = QA_PREV_GROUP;
    if (group_first && st.rate_win) acts[n++] = QA_RATE;
    int at, off; /* a spoken CUE book's saved place, from any of its tracks */
    if (st.qm_kind == QM_SONG && book_place(qm_list(), r, &at, &off)) acts[n++] = QA_RESUME;
    acts[n++] = QA_NEXT;
    acts[n++] = QA_ADD;
    if (st.qm_kind == QM_PLAYING) {
        if (chapter_file(r)) acts[n++] = QA_CHAPTERS;
        acts[n++] = QA_QUEUE;
    }
    if (!song) acts[n++] = QA_SHUFFLE;
    if (song) acts[n++] = checkFavExist(r) ? QA_UNFAV : QA_FAV;
    /* an album's own hearts, 5 its favourite */
    unsigned aid = song ? 0 : qm_album_id(r);
    int album_hearts = aid ? rate_kept(1, aid) : 0;
    if (aid) acts[n++] = album_hearts == 5 ? QA_UNFAV : QA_FAV, acts[n++] = QA_RATE;
    if (st.qm_kind < QM_COVERFLOW || st.qm_kind == QM_PLAYING) acts[n++] = QA_PLAYLIST;
    /* a card app's song isn't in the Library: Go to only to the app's own page */
    int hidden = song && !addon_goes(P(r, REC_PATH));
    if ((st.qm_kind == QM_SONG || st.qm_kind == QM_PLAYING) && album && *album && !hidden &&
        (cls & 0xfff0) != 0xff10 && !navigator_window_is_exist("playerjumpinfo_page") &&
        !coverflow_shows_album(r))
        acts[n++] = QA_ALBUM;
    if ((song || cls == CLASS_ALBUMS) && artist && *artist && !hidden &&
        !navigator_window_is_exist("artistinfo_page"))
        acts[n++] = QA_ARTIST;
    /* ponytail: hidden while a folder_page is open (as Go to's single-instance pages), so a folder
     * play's Now Playing has none; Back reaches that folder_page instead. */
    if (song && !navigator_window_is_exist("folder_page") && song_folder(r, st.go_folder))
        acts[n++] = QA_FOLDER;
    if (st.qm_kind == QM_PLAYING) {
        acts[n++] = QA_ADV_SHUFFLE;
        acts[n++] = QA_REPEAT;
        acts[n++] = QA_GROUP;
        if (!group_first && st.rate_win) acts[n++] = QA_RATE;
        if (np_more()) acts[n++] = QA_MORE; /* last: Now Playing's own menu */
    }
    if (st.qm_kind == QM_PLAYLIST) acts[n++] = QA_MORE; /* its dots' page */
    /* the list's own rows, which its covers hide: Edit Playlists… is Classic's select mode; a
     * toolbar's page edits from its toolbar */
    if (st.qm_kind == QM_PLAYLIST && coverflow_playlist(page)) {
        acts[n++] = QA_NEW_PLAYLIST;
        if (!geom_val(V_TOOLBAR)) acts[n++] = QA_EDIT_PLAYLISTS;
    }
    static const char *const rows[] = { "Play Next",
                                        "Add to Queue",
                                        "Shuffle",
                                        "Add to Favourites",
                                        "Remove from Favourites",
                                        "Add to Playlist",
                                        "Go to Album",
                                        "Go to Artist",
                                        "Shuffle",
                                        "Repeat",
                                        "Group by",
                                        "Next Album",
                                        "Previous Album",
                                        "Rate",
                                        "Resume",
                                        "View Queue",
                                        "Go to Folder",
                                        "Chapters",
                                        "More…",
                                        "New Playlist",
                                        "Edit Playlists…" };
    char rated[32];
    /* stock sortselect row styles */
    int painted = dialogs_painted(), pitch = painted ? theme()->page_row : QM_ROW;
    for (int i = 0; i < n; ++i) {
        void *item = list_item_create(view, 0, i * pitch, painted ? BACK_W : 375, pitch);
        widget_use_style(item, "s_listitem_black");
        void *label = hscroll_label_create(item, 30, 0, 266, pitch);
        widget_use_style(label, "s_scrlabel_white24l");
        if (acts[i] == QA_RATE) {
            int h = aid ? album_hearts : checkFavExist(r) ? 5 : rate_kept(0, listen_key(r));
            const char *row = aid ? "Rate Album" : "Rate";
            char hs[16];
            tk_snprintf(rated, sizeof rated, h ? "%s  %s" : "%s", row, hearts_text(hs, h));
        }
        widget_set_text_utf8(label,
                             acts[i] == QA_RATE ? rated
                             : acts[i] == QA_GROUP
                                 ? (playback_option(2) ? "Group by: Folder" : "Group by: Album")
                             : acts[i] == QA_NEXT_GROUP && playback_option(2) ? "Next Folder"
                             : acts[i] == QA_PREV_GROUP && playback_option(2) ? "Previous Folder"
                                                                              : rows[acts[i] - 1]);
        if (painted) hold_label(item, label);
        widget_on(item, EVT_CLICK, qm_pick, (void *)(long)acts[i]);
    }
    widget_set_prop_int(view, "virtual_h", n * pitch);
    if (painted) hold_open(dialog, n);
    st.qm_dialog = dialog;
    return 0;
}

/* A playlist's hold, from its row or its cover: the list's own menu, at its index in the live list
 * (p_deque_playlist), which its More page takes. A cover's title is the name the covers show: the
 * live list keeps a renamed one's old name until its page is on top again. */
static int qm_list_hold(unsigned at, char *key, void *cover) {
    void *lists = P(p_deque_playlist, 0);
    void *r = lists && at < deque_size(lists) ? deque_at(lists, at) : 0;
    if (!key || !r || !(st.qm_timer = timer_add(qm_open, (void *)0, 0))) return 0;
    st.qm_kind = QM_PLAYLIST;
    const char *name = cover ? P(cover, REC_TITLE) : 0;
    tk_snprintf(st.qm_name, sizeof st.qm_name, "%s", name ? name : "");
    st.qm_cls = 0xf001; /* its songs load as a song list, as Shuffle's (play_folder) */
    st.qm_idx = at;
    st.qm_rows = deque_size(lists);
    st.qm_hash = rec_hash(r);
    st.qm_press = *(unsigned long long *)(key + INPUT_KEY_TIME);
    drop_input();
    return 1;
}

/* The hold: the same gates and row as a centre press, over the stock showlist or Coverflow's
 * own tracks (row count checked), or the album at the centre of Coverflow's covers. Everything
 * else stays stock. */
static int qm_hold(void) {
    st.qm_choice = 0;
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    const char *name = top ? widget_get_prop_str(top, "name", "") : "";
    int playing = !tk_strcmp(name, "playing_page");
    if (st.rate_on && top == st.rate_on) { /* the picker's: the hold and its release do nothing */
        char *key = play_key();
        if (key) st.qm_press = *(unsigned long long *)(key + INPUT_KEY_TIME);
        drop_input();
        return 1;
    }
    if (st.qm_dialog || st.qm_timer || st.qm_playlist || !usable() ||
        (!playing && !allowed_top(top)) || window_manager_is_animating(wm) || pointer_down() ||
        airplayGetFlag() == 2 || g_navbar_status)
        return 0;
    if (playing) {
        unsigned at, n, type = (unsigned)MCL(MCL_TYPE);
        void *r = queue_now(&at, &n), *q = P(mcl_pdeqplaylist, 0);
        char *key = play_key();
        if (!r || !key || (type != 1 && (type & 0xf000) != 0xf000) ||
            !(st.qm_timer = timer_add(qm_open, 0, 0)))
            return 0;
        st.qm_kind = QM_PLAYING;
        st.qm_cls = 0xf001;
        st.qm_queue = q;
        st.qm_idx = at;
        st.qm_rows = n;
        st.qm_hash = rec_hash(r);
        st.qm_queue_hash = queue_hash(q);
        st.qm_press = *(unsigned long long *)(key + INPUT_KEY_TIME);
        drop_input();
        return 1;
    }
    int kind = contexts[context_id(name)].kind;
    void *album = coverflow_album(top), *cover = coverflow_playlist(top);
    char *key = play_key();
    if (cover)
        return qm_list_hold(playlist_index(I(cover, REC_DISC)), key, cover); /* by its cd_num */
    if (album) {
        if (!key || !(st.qm_timer = timer_add(qm_open, (void *)0, 0))) return 0;
        st.qm_kind = QM_COVERALBUM;
        st.qm_cls = CLASS_ALBUMS;
        st.qm_hash = rec_hash(album);
        st.qm_press = *(unsigned long long *)((char *)key + INPUT_KEY_TIME);
        drop_input();
        return 1;
    }
    void *list = page_tracks(top);
    int coverflow = list != 0;
    void *w = surface_under(top, (void *)0, (void *)0, 0);
    if ((!coverflow && kind < CTX_FOLDER) || !w || !load(&g_menu, w, 1) || g_menu.ctx < 0 ||
        g_menu.kind == 3)
        return 0;
    int cur = reconcile(&g_menu, !moving(&g_menu));
    if (cur < 0) return 0;
    /* A playlist row's hold queues its songs; More… opens the page its trailing dots do. The Add
     * to Playlist picker shares playlist_page's name and keeps stock's hold: its add mode tells
     * the two apart, as the showlist under it is another page's. */
    if (!tk_strcmp(name, "playlist_page") && widget_get_prop_int(top, PL_MODE, 0)) return 0;
    if (!tk_strcmp(name, "playlist_page")) {
        /* The row's index counts Import and Export; its dots' name carries the playlist's. */
        void *more = more_image(g_menu.at[cur]);
        return qm_list_hold((unsigned)atoi(more ? widget_get_prop_str(more, "name", "") + 8 : "-1"),
                            key, 0);
    }
    if (!coverflow) list = P(p_deque_showlist, 0);
    if (!list || deque_size(list) != (unsigned)g_menu.rows) return 0;
    void *r = deque_at(list, g_menu.id[cur]);
    /* An artist's tabs reload the showlist with load_localartist_list, which sets classinfo +0
     * (0xff07 songs, 0xff01 albums) but not g_class_type. */
    unsigned cls = (unsigned)(tk_strcmp(name, "artistinfo_page") ? I(g_class_type, 0)
                                                                 : I(g_local_classinfo_save, 0));
    if (!r || !key || playback_video(r)) return 0;
    st.qm_kind = I(r, REC_TYPE) == 8 ? QM_SONG : 0;
    if (coverflow) {
        if (st.qm_kind) st.qm_kind = QM_COVERFLOW;
    } else if (kind == CTX_FOLDER) {
        if (I(r, REC_TYPE) == 4) st.qm_kind = QM_FOLDER;
    } else if (cls == CLASS_ALBUMS || (cls >= CLASS_ARTIST_ALBUMS && cls <= 0xff03))
        st.qm_kind = QM_ALBUM; /* all albums, and an artist's, composer's or genre's */
    else if (cls >= 0xf004 && cls <= 0xf006)
        st.qm_kind = QM_GROUP; /* artist, composer and genre lists */
    if (!st.qm_kind || !(st.qm_timer = timer_add(qm_open, (void *)0, 0))) return 0;
    st.qm_cls = cls;
    st.qm_idx = (unsigned)g_menu.id[cur];
    st.qm_rows = (unsigned)g_menu.rows;
    st.qm_hash = rec_hash(r);
    st.qm_browse = browse_hash();
    st.qm_press = *(unsigned long long *)((char *)key + INPUT_KEY_TIME);
    drop_input();
    return 1;
}

/* Quick Settings' wheel: 0 its brightness slider, 1 stock's volume (Q2POD QSVOLUME). A held
 * Play/Pause there switches it (ringnav_keylong) and says so in a band for QS_HINT_MS, not a toast:
 * its msginfo_dialog would cover the pull-down, taking the wheel and Centre for 2 s. The band sits
 * between the second row's first label line and the slider. */
#define QS_HINT "ringnav_qs_hint"
#define QS_HINT_MS 1500
static int quick_hint_end(const void *info) {
    widget_set_visible(P(info, TIMER_CTX), 0, 0);
    return 7; /* RET_REMOVE */
}
static void quick_hint(void *top, int volume) {
    void *old = widget_lookup(top, QS_HINT, 1);
    if (old) widget_destroy(old); /* its timer goes with it: a second hold gets its whole time */
    void *band = band_create(top, QS_HINT, 226, 28);
    if (!band) return;
    widget_set_text_utf8(band, volume ? "Wheel: Volume" : "Wheel: Brightness");
    widget_add_timer(band, quick_hint_end, QS_HINT_MS);
}
static int quick_volume(void) {
    if (!st.qs_wheel) st.qs_wheel = 1 + config_value("Q2POD", "QSVOLUME", 2, 0);
    return st.qs_wheel - 1;
}
static int quick_wheel_toggle(void *top) {
    if (!usable() || !top || tk_strcmp(widget_get_prop_str(top, "name", ""), "statusbar_dialog"))
        return 0;
    int volume = !quick_volume();
    st.qs_wheel = 1 + volume;
    write_int_config(volume, "Q2POD", "QSVOLUME");
    quick_hint(top, volume);
    return 1;
}
/* The window manager's pointer stays "pressed" when a touch's release never reaches it: every tick
 * and Centre press on a list is then dropped (ringnav's gate) and the selection stays hidden until
 * a tap; a stuck pull-down (quick_stuck) stays
 * too. The release went missing in the input queue (playback.c ringnav_queue_event, the found
 * way, now closed); this is the backstop for any other. A press with no pointer event reaching the
 * window manager (ringnav_input) within STALE_TOUCH_MS gets an abort, so a pressed row drops its
 * press and cannot click, then the release stock never got, at the touch's last place: only that
 * path clears the flag (input_device_status_on_input_event, 0x6b3514). A finger resting that long
 * is lifted too; its real lift then finds the flag clear and does nothing (0x6b33e0). ponytail:
 * STALE_TOUCH_MS is a guess, and that the flag is what window_manager_get_pointer_pressed reads
 * (vtable +0x44) and that widgets drop a press on the abort are AWTK's source, not traced in demo;
 * the time stamped here holds a wrong guess to one try per STALE_TOUCH_MS.
 * --dev builds only (Q2POD_DEV): each heal, STALE_NOTES a boot, goes to q2-wheel.log, which the
 * next boot appends to the card's (tools/build.py WHEEL_LOG_HOOK): the page, the pull-down's state
 * and slide timer, the touch thread's own down flag (LOOP_PRESSED) and the last pointer event. */
#define STALE_NOTES 8
static void stale_note(unsigned age) {
    if (st.stale_notes >= STALE_NOTES) return;
    st.stale_notes++;
    unsigned char *loop = main_loop();
    void *top = window_manager_get_top_window(window_manager());
    char s[160];
    tk_snprintf(s, sizeof s,
                "== wheel: stale press at %u s: top=%s pull-down=%d slide=%d thread=%d "
                "last=%x %u ms ago\n",
                (unsigned)time_now_ms() / 1000, top ? widget_get_prop_str(top, "name", "") : "-",
                I(g_dropdown_menu_state, 0), top ? widget_get_prop_int(top, "timer_idex", -1) : -1,
                loop ? loop[LOOP_PRESSED] : -1, st.touch_type, age);
    void *f = fopen("/mnt/data/q2-wheel.log", "ab");
    if (!f) return;
    fputs(s, f);
    fclose(f);
}
static void stale_heal(void) {
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    unsigned now = (unsigned)time_now_ms(), age = now - st.touch_at, abort_event[12], up[12];
    if (age < STALE_TOUCH_MS || !window_manager_get_pointer_pressed(wm)) return;
    if (Q2POD_DEV) stale_note(age);
    st.touch_at = now;
    if (top)
        widget_dispatch_event_to_target_recursive(
            top, pointer_event_init(abort_event, EVT_POINTER_ABORT, top, 0, 0));
    stock_input_trampoline(
        wm, pointer_event_init(up, EVT_POINTER_UP_BEFORE - 1, wm, st.touch_x, st.touch_y));
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
static void quick_poll(void) {
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
/* Stock long-key callback: everything except a taken Play/Pause hold runs the stock body. On an
 * Internet Radio station it is the favourite toggle, on a Spotify track Add to Queue (its reply
 * toasts), on a card app's row its hold op (addon_hold: a Deezer list downloaded whole), in Quick
 * Settings the wheel's brightness/volume switch, on View Queue's shuffle list
 * Play Next (un_hold), the release latched as the queue menu's. */
int ringnav_keylong(void *ctx, void *event) {
    if (event && ple_key((unsigned)I(event, EVENT_KEY), 3)) return STOP;
    if (!event || I(event, EVENT_KEY) != KEY_PLAY) return stock_keylong_trampoline(ctx, event);
    void *top = window_manager_get_top_window(window_manager());
    const char *fav = radio_hold(top);
    int taken = fav || quick_wheel_toggle(top) || spot_hold(top) || addon_hold(top) || un_hold(top);
    if (!taken && qm_hold()) return STOP;
    /* Stock has no Play/Pause hold, so its release would play or pause: Classic's does nothing. */
    if (taken || (usable() && classic_pages())) {
        char *key = play_key();
        if (key) st.qm_press = *(unsigned long long *)(key + INPUT_KEY_TIME);
        drop_input();
        if (fav) toast(fav);
        return STOP;
    }
    return stock_keylong_trampoline(ctx, event);
}

/* Hold time (HOLD_KEYS in tools/build.py; docs/internals.md#queue-menu): Play/Pause and Return fire
 * their long press after KEY_HOLD_MS, every other key stock's 1000 ms (500 for key 232, unused on
 * the Q2). ringnav_hold_timer replaces the key-down's timer_add, ringnav_hold_info the timer's
 * threshold lookup. */
#define KEY_HOLD_MS 700
static const int hold_info[][2] = { { KEY_PLAY, KEY_HOLD_MS }, { KEY_RETURN, KEY_HOLD_MS } };
const int *ringnav_hold_info(void *ids, int key) {
    for (unsigned i = 0; i < sizeof hold_info / sizeof *hold_info; i++)
        if (hold_info[i][0] == key) return hold_info[i];
    return ((const int *(*)(void *, int))0x6b384c)(
        ids, key); /* input_device_status_find_key_long_press_info */
}
unsigned ringnav_hold_timer(int (*fn)(const void *), void *ids, unsigned ms, int key) {
    unsigned id = timer_add(fn, ids, key == KEY_PLAY || key == KEY_RETURN ? KEY_HOLD_MS : ms);
    if (key == KEY_CENTER) st.centre_hold = id; /* chord_gate may end it */
    return id;
}

/* Replaces mclNextSong's shuffle pick call: stock picks and bookkeeps, then a pending Play next
 * becomes the next track if it is still at its index. */
int shuffle_forced(void) {
    void *queue = P(mcl_pdeqplaylist, 0);
    unsigned at = st.qm_forced - 1;
    return queue && at < deque_size(queue) && rec_hash(deque_at(queue, at)) == st.qm_forced_hash
               ? (int)at
               : -1;
}
int ringnav_shuffle(int forward) {
    unsigned was = (unsigned)MCL(MCL_POS);
    int back = playback_back(), result = mcl_shuffle_pick(forward), forced = shuffle_forced(),
        ahead = playback_peek(was); /* after the pick: Folder Skip's jump changes the queue */
    st.qm_forced = 0;
    if (forced >= 0)
        MCL(MCL_POS) = forced;
    else if (back >= 0)
        MCL(MCL_POS) = back; /* after Previous, Next returns to the track it left */
    else if (ahead >= 0)
        MCL(MCL_POS) = ahead;
    return result;
}

/* Replaces the name comparators' toolsTrimLeft calls (SORT_TRIMS): each trims its own copy of a
 * name, which then sorts without its article. */
void ringnav_sort_key(char *s) {
    toolsTrimLeft(s);
    unsigned n = article(s);
    if (n)
        for (char *d = s; (*d = d[n]); ++d) {}
}

/* Moves key's place to the front of the ring at sec, or forgets it near either end. */
static void spot_keep(unsigned key, int sec, int total) {
    int i = 0, keep = sec >= RESUME_EDGE_S && sec < total - RESUME_EDGE_S;
    while (i < RESUME_SLOTS - 1 && st.spots[i].key != key) i++;
    /* nothing to do: already first at sec, or nothing kept to forget */
    if (keep ? !i && st.spots[0].key == key && st.spots[0].sec == sec : st.spots[i].key != key)
        return;
    if (keep) {
        for (; i > 0; i--) st.spots[i] = st.spots[i - 1]; /* the oldest falls off the end */
        st.spots[0].key = key;
        st.spots[0].sec = sec;
    } else {
        for (; i < RESUME_SLOTS - 1; i++) st.spots[i] = st.spots[i + 1];
        st.spots[i].key = 0;
    }
    BLOB_IO(RESUME_FILE, st.spots, 1);
}

/* Counts key's play and moves it first, so among equal counts the least recently played is the
 * one replaced when no slot is free. */
static void play_count(void *r, unsigned key) {
    plays_load();
    int i = 0;
    for (int j = 0; j < PLAYS_SLOTS && st.plays[i].key != key; j++)
        if (st.plays[j].key == key || st.plays[j].n <= st.plays[i].n) i = j;
    unsigned evicted = st.plays[i].key;
    mp_counted(r, key, evicted != key ? evicted : 0);
    unsigned n = st.plays[i].key == key ? st.plays[i].n + 1 : 1;
    for (; i > 0; i--) st.plays[i] = st.plays[i - 1];
    st.plays[0].key = key;
    st.plays[0].n = n;
    BLOB_IO(PLAYS_FILE, st.plays, 1);
}

/* Scrobbling (docs/internals.md#scrobbling): a Rockbox-style AudioScrobbler 1.1 log at the card's
 * root, for any .scrobbler.log uploader or Upload Scrobbles (scrobble.c). Artist, album and title
 * are the player's parsed tags when it has parsed this track (now_tag), or with .scrobble.ini's
 * ALBUM_ARTIST the song's album artist when it has one (parsed too: folder queues' records have
 * none); untagged (artist-less) tracks are skipped, as scrobblers reject them. */
char *scrobble_tag(char *o, char *end, const char *s) {
    for (; s && *s && o < end - 1; s++) *o++ = *s == '\t' || *s == '\n' || *s == '\r' ? ' ' : *s;
    *o++ = '\t';
    return o;
}

static void scrobble(void *r, int total, int heard) {
    const char *artist = now_tag(r, REC_ARTIST), *album_artist = now_tag(r, REC_ALBUM_ARTIST);
    if (!album_artist || !*album_artist)
        album_artist = P(r, REC_ALBUM_ARTIST); /* CUE: none parsed */
    if (album_artist && *album_artist && scrobble_flag("ALBUM_ARTIST")) artist = album_artist;
    if (!artist || !*artist) return;
    long long now = wall_time();
    if (now < 1600000000) return; /* clock never set: Last.fm would reject the time */
    if (!addon_scrobbles(P(r, REC_PATH))) return; /* its card app's q2app says scrobble=0 */
    char line[800], name[512], *end = line + sizeof line - 48, *o = line; /* 48: the numbers */
    const char *title = now_tag(r, REC_TITLE);
    if (!title || !*title || !strcmp(title, "(null)")) title = track_name(name, sizeof name, r);
    o = scrobble_tag(o, end, artist);
    o = scrobble_tag(o, end, now_tag(r, REC_ALBUM));
    o = scrobble_tag(o, end, title);
    if (I(r, REC_TRACK) > 0) o += tk_snprintf(o, 12, "%d", I(r, REC_TRACK));
    o += tk_snprintf(o, (unsigned)(line + sizeof line - o), "\t%d\tL\t%u\t\n", total,
                     (unsigned)(now - heard));
    if (scrobble_append(line, (unsigned)(o - line))) scrobble_listened();
}

/* The polled track's place at sec, its file's seconds. A CUE track becomes the book's place only
 * past its own first RESUME_EDGE_S; only the last one (no CUE end) knows the file's end, where the
 * book is finished and forgotten. */
static void spot_save(int sec) {
    if (!(st.rs_cue || st.rs_end) || sec >= RESUME_EDGE_S)
        spot_keep(st.rs_file, st.rs_cue + sec, st.rs_end ? 0x7fffffff : st.rs_cue + st.rs_total);
}

/* Would starting the polled track again lose a place worth keeping? spot_keep keeps none near
 * either end, and spot_save leaves a CUE book's place alone at a chapter's start. */
int resume_held(void) {
    int sec, total;
    if (!st.rs_long || st.rs_cue || st.rs_end) return 0;
    if (player_playtime_and_length(&sec, &total) < 0) sec = st.rs_sec; /* stopped: where it was */
    return sec >= RESUME_EDGE_S && sec < st.rs_total - RESUME_EDGE_S;
}

/* A load of the file playing, from another list: back to this second once it starts over. */
void resume_return(void) {
    int sec, total;
    st.rs_go = player_playtime_and_length(&sec, &total) < 0 ? st.rs_sec : sec;
    st.rs_go_key = st.rs_key;
    st.rs_go_at = (unsigned)time_now_ms();
}

/* A confirmed restart: a stop before it doesn't send it back to the place. */
void resume_restart(void) { st.rs_stopped = 0; }

/* Once a second from the UI loop: a long track saves its place and, when it starts playing near
 * its beginning, jumps to the saved one. A spoken CUE track saves its file's place but jumps only
 * when a Resume row (book_place) started it. A track change waits a poll, so the previous track's
 * time never counts for the new one. */
static void resume_poll(void) {
    unsigned now = (unsigned)time_now_ms(), at, n;
    if (now - st.rs_at < 1000) return;
    st.rs_at = now;
    int sec = 0, total = 0;
    void *r = queue_now(&at, &n);
    if (!r || player_playtime_and_length(&sec, &total) < 0 || total <= 0) {
        if (st.rs_long && mclGetPlayStatus() == 1)
            st.rs_stopped = 1; /* not a load's or seek's -1 */
        return;
    }
    unsigned key = listen_key(r);                   /* a CUE image's tracks share one path */
    if (now - st.rs_go_at > 8000) st.rs_go_key = 0; /* a Resume row's chapter never came */
    if (key != st.rs_key) {
        if (st.rs_long && !st.rs_pending) spot_save(st.rs_sec);
        st.rs_key = key;
        st.rs_long = st.rs_stopped = 0;
        st.rs_settle = 1;
        return;
    }
    if (st.rs_settle) {
        st.rs_settle = 0;
        st.rs_spoken = spoken(P(r, REC_PATH));
        st.rs_file = fnv(FNV_SEED, P(r, REC_PATH));
        st.rs_cue = I(r, REC_CUE_START);
        st.rs_end = I(r, REC_CUE_END);
        int book = st.rs_cue || st.rs_end; /* music CUE images are left alone */
        int min = resume_min();            /* Q2POD RESUMEMIN, 0 for Off */
        st.rs_long = book ? st.rs_spoken : (min && total >= min * 60) || st.rs_spoken;
        int saved = st.rs_long ? spot_at(st.rs_file) : 0; /* reads the ring spot_keep writes */
        st.rs_pending = book ? 0 : saved;
        if (playback_resumed(r)) st.rs_pending = 0;
        st.rs_sec = st.rs_saved = sec;
        st.rs_still = 0;
    }
    /* Started again after a stop, as a list pick or Play does: back to the place it stopped at. */
    if (st.rs_stopped) {
        st.rs_stopped = 0;
        if (st.rs_long && !st.rs_cue && !st.rs_end && sec < RESUME_START_S &&
            st.rs_sec >= RESUME_EDGE_S && st.rs_sec < st.rs_total - RESUME_EDGE_S)
            st.rs_pending = st.rs_sec;
    }
    if (st.ch_key == key)
        st.rs_pending = 0;
    else
        st.ch_key = 0;
    /* A listen: half the track or LISTEN_MAX_S heard, seeks and pauses aside; a repeat starts over.
     * It scrobbles then too, or with [SCROBBLE] PERCENT once that share of it is heard (of all but
     * its last 2 s, which a poll a second apart may not see). */
    if (!st.rs_pending && !st.rs_spoken) {
        unsigned lk = listen_key(r);
        if (lk != st.ls_key || (sec < 2 && st.ls_sec > LISTEN_MIN_S)) {
            st.ls_key = lk;
            st.ls_sec = sec;
            st.ls_heard = st.ls_done = st.ls_sent = 0;
            int pct = scrobble_value("PERCENT");
            st.ls_pct = pct >= 1 && pct <= 100 ? pct : 0;
        }
        int d = sec - st.ls_sec;
        if (d > 0 && d <= 2) st.ls_heard += d;
        st.ls_sec = sec;
        int listen = total / 2 < LISTEN_MAX_S ? total / 2 : LISTEN_MAX_S;
        if (!st.ls_done && total > LISTEN_MIN_S && st.ls_heard >= listen) {
            st.ls_done = 1;
            play_count(r, lk);
        }
        if (!st.ls_sent && total > LISTEN_MIN_S &&
            st.ls_heard >= (st.ls_pct ? (total - 2) * st.ls_pct / 100 : listen)) {
            st.ls_sent = 1;
            scrobble(r, total, st.ls_heard);
        }
    }
    if (!st.rs_long) return;
    st.rs_total = total;
    if (key == st.rs_go_key) { /* a Resume row's chapter, the playing one too, once it starts */
        if (sec < 1 || sec >= RESUME_START_S) return; /* the old position until it does */
        st.rs_go_key = 0;
        st.rs_pending = st.rs_go;
    }
    if (st.rs_pending) {
        if (sec < 1) return; /* not playing yet: a paused boot resume waits for Play */
        if (sec < RESUME_START_S) {
            /* blocks the UI up to 2 s, as Scrub's commit; past the hook: nobody pressed, no wait */
            stock_seek_time_trampoline(st.rs_pending);
            sec = st.rs_pending;
        }
        st.rs_pending = 0;
        st.rs_sec = st.rs_saved = sec;
        return;
    }
    st.rs_still = sec == st.rs_sec ? st.rs_still + 1 : 0;
    st.rs_sec = sec;
    int moved = sec - st.rs_saved;
    if (moved && (st.rs_still >= 2 || moved >= RESUME_SAVE_S || moved <= -RESUME_SAVE_S)) {
        st.rs_saved = sec;
        spot_save(sec);
    }
}

/* Home's Rockbox row (docs/boot.md#rockbox-from-home), bound by coverflow_home: a click leaves
 * Q2 Pod for Rockbox. demo cannot start it itself: Rockbox needs the screen, the keys and ALSA. So
 * demo leaves S90play's flag and ends; S90play sees it, runs Rockbox and starts demo again when
 * Rockbox exits. Bluetooth's daemons are not demo's children and keep the headphones connected, so
 * Rockbox finds them and plays there (q2-rockbox's pcm-alsa). The queue is saved as power-off
 * saves it, charging is handed back as Rockbox expects it at boot, the
 * watchdog (checkappprocess.sh, which reboots once demo is gone) and hciplayer (which holds the
 * DAC's PCM) are stopped, and demo is killed outright, so no exit handler can hang it. */
int rockbox_open(void *ctx, void *event) {
    void *flag = !rockbox_available() ? (void *)0 : fopen(ROCKBOX_FLAG, "w");
    if (!flag) {
        toast("Rockbox is not on the card");
        return 0;
    }
    fclose(flag);
    playback_save();
    save_memoryplay_info();
    player_stop();
    radio_stop();
    addon_stop(); /* the card apps' loops run on past demo: each asked to end */
    if (st.charge_held) switch_charge_enable(1);
    /* Rockbox's volume is pcm-alsa's software mixer alone, so the headset's absolute volume, left
     * at the Q2's, would cap it: open it to the maximum volume limit. Demo syncs it back on return.
     */
    if (bt_link() && g_bluetoothflag && bt_linkstatus &&
        (unsigned)btctl_transport_get_volume() <= 127)
        btctl_transport_set_volume(((g_maxvolume < 100 ? g_maxvolume : 100) * 127 + 50) / 100);
    ringnav_clean_end(); /* the watchdog would outlive demo's kill; nothing to report */
    system("killall checkappprocess.sh; killall -9 hciplayer; " SPOT_KILL "; sync; kill -9 $PPID");
    return 0;
}

/* Now Playing on top. */
static int now_playing(void) {
    void *top = window_manager_get_top_window(window_manager());
    return top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "playing_page");
}

/* Now Playing's visualizer repainting, its album marquee moving or its lyrics gliding: their 40 and
 * 50 ms timers and the glide's steps would run late on an idle pass. Its progress and the lyrics'
 * follow tick every 250 ms and keep up. A game's 33 ms frame timer too: Vortex's raw wheel posts no
 * input to stamp, nor does a card game's, whose travel goes once a pass. */
static int np_animating(void) {
    return games_live() || addon_game_on() ||
           (now_playing() && (visualizer_live() || st.lyric_glide ||
                              (st.np_marquee && st.np_off > 0 && st.np_off < st.np_over)));
}

/* Keep stock VOLUME for wired/USB and boot; Bluetooth saves its own level. Smart Wi-Fi's own
 * toggles of the Wi-Fi switch save nothing (smart_config). */
int ringnav_write_config(int value, const char *section, const char *key) {
    if (smart_config(section, key)) return 0;
    if (!tk_strcmp(section, "PLAYSET") && !tk_strcmp(key, "VOLUME") && mclGetOutputWay() == 1)
        key = "BT_VOLUME";
    return stock_write_config_trampoline(value, section, key);
}

int ringnav_output(int way) {
    int old = mclGetOutputWay();
    int changed = (old == 1) != (way == 1);
    if (changed) {
        write_int_config(g_volume, "PLAYSET", "VOLUME");
        char s[256] = "";
        toolsReadConfig("/mnt/data/config.ini", "PLAYSET", way == 1 ? "BT_VOLUME" : "VOLUME", s,
                        "");
        int v = 0;
        unsigned i;
        for (i = 0; i < 3 && s[i] >= '0' && s[i] <= '9'; ++i) v = v * 10 + s[i] - '0';
        if (!i || s[i] || v > 100) v = g_volume;
        if (v > g_maxvolume) v = g_maxvolume;
        g_volume = (unsigned char)v;
        headset_volume_reset();
    }
    int result = stock_output_trampoline(way);
    if (changed) device_set_volume(g_volume, 1);
    return result;
}

/* AVRCP absolute volume, as in PR #3. libbtctl returns 0 on a successful write.
 * Keep stock software gain unless the headset accepted the requested level. */
static int bt_audio_ready(void) {
    return bt_link() && g_bluetoothflag && bt_linkstatus &&
           *(const volatile int *)bt_showcoding > 0 && !bt__recv_pageflag && mclGetOutputWay() == 1;
}

static struct {
    unsigned checked;
    int ready, last, failed; /* failed: the headset refused the last absolute volume */
} btvol __attribute__((section(".scratch")));

void headset_volume_reset(void) { btvol.ready = 0; }

static int bt_sync_volume(int left, int right) {
    unsigned cur = (unsigned)btctl_transport_get_volume();
    if (cur > 127) return btvol.failed = 1, 0; /* no absolute volume (yet): the slow pace */
    int v = left > right ? left : right;
    v = v < 0 ? 0 : v > 100 ? 100 : v;
    /* 128 headset steps onto 101: one that already reads as v stays, so the headset's own steps
     * hold */
    if ((int)(cur * 100 + 63) / 127 != v) {
        unsigned want = (v * 127 + 50) / 100;
        if (btctl_transport_set_volume(want)) return btvol.failed = 1, 0;
        cur = want;
    }
    btvol.last = cur;
    btvol.failed = 0;
    return 1;
}

int headset_gain(void) { return bt_audio_ready() && btvol.ready && g_volume ? 100 : g_volume; }

/* Absolute zero still needs software mute. Unsupported/failed writes retain stock gain. */
int ringnav_btvol(int left, int right) {
    if (bt_audio_ready()) {
        btvol.ready = bt_sync_volume(left, right);
        if (btvol.ready && (left > 0 || right > 0)) {
            int volume = left > right ? left : right;
            return stock_btvol_trampoline(left <= 0 ? 0 : left * 100 / volume,
                                          right <= 0 ? 0 : right * 100 / volume);
        }
    }
    return stock_btvol_trampoline(left, right);
}

/* Every 400 ms lit; dark, every BT_VOLUME_LIMIT_MS while a volume limit has to be held on the
 * headset's own keys, else BT_VOLUME_SLOW_MS, so a long dark session doesn't wake libbtctl 2.5
 * times a second for a volume nobody sees. A headset that refuses or can't report absolute volume
 * gets the slow pace lit too. ponytail: dark, the headset's keys can pass the limit for up to
 * BT_VOLUME_LIMIT_MS before it is pulled back. */
static void bt_volume_poll(void) {
    unsigned now = time_now_ms();
    unsigned gap = btvol.failed                       ? BT_VOLUME_SLOW_MS
                   : g_backlight_status               ? 400u
                   : btvol.ready && g_maxvolume < 100 ? BT_VOLUME_LIMIT_MS
                                                      : BT_VOLUME_SLOW_MS;
    if (now - btvol.checked < gap) return;
    btvol.checked = now;
    if (!bt_audio_ready()) {
        btvol.ready = btvol.failed = 0;
        return;
    }
    if (!btvol.ready) {
        device_set_volume(g_volume, 0); /* Reuse stock's Balance calculation on reconnect. */
        return;                         /* Initial connection preserves Q2 volume. */
    }
    int cur = btctl_transport_get_volume();
    if (cur < 0 || cur > 127) {
        btvol.ready = 0;
        device_set_volume(g_volume, 0);
        return;
    }
    if (cur == btvol.last) return;
    int v = (cur * 100 + 63) / 127;
    int limit = g_maxvolume < 100 ? g_maxvolume : 100;
    if (v > limit) v = limit;
    btvol.last = cur;
    g_volume = (unsigned char)v;
    *(volatile signed char *)0xa4cbb5u = (signed char)v; /* stock responseSetVolume's level */
    device_set_volume(v, 1);
    write_int_config(v, "PLAYSET", "VOLUME");
    void *top = vol_top();
    if (top) {
        void *slider = widget_lookup(top, "slider_vol", 1);
        if (slider) widget_set_prop_int(slider, "value", v);
    }
}

/* main_loop_sleep_default paces the UI loop at 8 ms (125 Hz), screen on or off. With the backlight
 * off it first idles SCREEN_OFF_SLEEP_MS (DARK_IDLE_SLEEP_MS once nothing plays and the DAC is
 * off, unless Wake's second press may be coming); stock then finds its 8 ms gone, sleeps 0 and
 * keeps its own bookkeeping. A dark wheel turn keeps stock's pace, DARK_TURN_MS on from each tick
 * (tick_gate): an idling pass dispatched every tick it had slept through at once, so a dark turn
 * clicked in bursts and stepped the volume in jumps (#303). ponytail: a turn's first ticks still
 * wait out one idle and arrive together, to one click (tone_dark). Ending the idle on a key
 * instead is select() on a datagram socket that ringnav_queue_event sends to, a send on every
 * input thread; build it if the first tick's wait ever shows. The first pass with it back on
 * repaints every window once, so nothing drawn while dark, or only partly, stays on screen until
 * the next input; after a centre press woke it, it also restarts Wake's window for a dropped press
 * (woke_at). With the screen on, a pass idles IDLE_SLEEP_MS once input and window animations have
 * been still for IDLE_MS, except while Now Playing animates or the volume row's push runs (it
 * leaves 5.3 s after the last key). */
static volatile int dark_quiet; /* read by ringnav_dark_usleep on stock's threads */
static int tone_dark;           /* this pass clicked with the screen off (ringnav_buzzer) */
int ringnav_dark_usleep(unsigned us) {
    return usleep(dark_quiet && us < DARK_POLL_US ? DARK_POLL_US : us);
}
/* The cover pollers feed only what is painted, so they slow down whenever the screen is off,
 * playing or not. ponytail: art parsed while dark can show up to DARK_POLL_US late after wake. */
int ringnav_dark_cover_usleep(unsigned us) {
    return usleep(!g_backlight_status && us < DARK_POLL_US ? DARK_POLL_US : us);
}
/* on_wm_idle_status ran player_mainscheduling every UI pass, and its mclAutoChange asks hciplayer
 * for its status and waits for the answer on the UI thread: 40 to 125 round trips a second, each
 * waking both players. It runs every MAINSCHEDULING_MS instead, DARK_POLL_US while dark and quiet:
 * a lyric line and the play-state notifications follow within that, as stock's 200 ms status sync
 * does. From a second before the playing track's end (player_playtime_and_length's cache, a CUE
 * track's own end) until MAINSCHEDULING_END_MS after, it runs every pass, so a change without
 * Gapless starts as soon as stock's did; paused, it stays paced. ponytail: a length that overstates
 * the audio (a VBR MP3 without a Xing header) ends outside the window, up to 100 ms late; follow
 * mclAutoChange's own end flag if that shows. */
#define MAINSCHEDULING_MS 100
#define MAINSCHEDULING_END_MS 2000
int ringnav_mainscheduling(void) {
    static unsigned last, end;
    int sec = -1, total = 0;
    if (mclGetPlayStatus() == 2) player_playtime_and_length(&sec, &total);
    unsigned now = (unsigned)time_now_ms(),
             gap = dark_quiet ? DARK_POLL_US / 1000 : MAINSCHEDULING_MS;
    if (total > 0 && total - sec <= 1) end = now | !now;
    if (last && now - last < gap && !(end && now - end < MAINSCHEDULING_END_MS)) return 0;
    last = now | !now; /* never 0 */
    return player_mainscheduling();
}

/* Cover Flow's thumb, every UI loop pass before lyric_poll, so a wheel handed between them ends up
 * the lyrics' or, rewritten next pass, the covers'. The gates ticks have, for raw travel: stock's
 * lockout after Centre or Return, as ringnav keeps it; Return's stop; no frame in WHEEL_FRESH_MS.
 * No key event comes, so a turn times the loop's idle and the screen's timeout itself. */
static void thumb_poll(void) {
    if (!coverflow_wheel_live()) return;
    unsigned now = (unsigned)time_now_ms();
    int gated = *(volatile signed char *)KEY_LOCKOUT > 8 - LOCKOUT_KEEP || wheel_mute ||
                (int)(now - wheel_frame_at) >= WHEEL_FRESH_MS;
    int r = coverflow_wheel_poll(usable(), gated);
    if (r) st.last_input = now;
    if (r & THUMB_TURNED) reset_poweroptions_timer(1, 1, 1);
    if (r & THUMB_CLICK) buzzeer_switch(1);
}

int ringnav_sleep(void *loop) {
    stale_heal();
    quick_poll();
    headset_poll();
    headset_eq_poll();
    bt_volume_poll();
    video_poll();
    spot_poll();
    radio_poll();
    addon_poll();
    playback_poll();
    resume_poll();
    scrobble_auto();
    power_poll();
    thumb_poll();
    lyric_poll();
    keep_lit_poll();
    info_poll();
    np_art_poll();
    if ((st.np_slide && widget_get_prop_int(st.np_slide, "value", 0)) || /* off the art page */
        (st.rate_on && classic_pages()))                                 /* over the picker */
        *(volatile signed char *)BC_IDLE = BC_IDLE_FULL;
    if (st.bc[0] && np_track() != st.bc_track) bc_show();
    if (!g_backlight_status) {
        st.dark |= 1;
        int quiet = I(g_dacoff_time, 0) < 0 && !media_playing() && !video_on() &&
                    airplayGetFlag() != 2 && (signed char)bt__recv_pageflag != 2 &&
                    time_now_ms() - st.wake_at >= WAKE_MS;
        dark_quiet = quiet;
        if ((unsigned)time_now_ms() - st.tick_at >= DARK_TURN_MS)
            sleep_ms(quiet ? DARK_IDLE_SLEEP_MS : SCREEN_OFF_SLEEP_MS);
    } else if (st.dark) {
        dark_quiet = 0;
        wheel_guard_reset();
        if (st.dark & 2) st.woke_at = (unsigned)time_now_ms();
        st.dark = 0;
        if (st.clock_timer) clock_show(); /* a time set while dark */
        widget_invalidate_force(window_manager(), (void *)0);
    } else if (time_now_ms() - st.last_input >= IDLE_MS &&
               !window_manager_is_animating(window_manager()) && !np_animating() &&
               !coverflow_turning())
        sleep_ms(IDLE_SLEEP_MS);
    tone_dark = 0; /* after the polls: a click of theirs is this pass's too */
    return stock_sleep_trampoline(loop);
}

/* With the screen off Return never reaches the UI, so a bump of Back leaves the page alone.
 * Wake: Double press. With the screen off, a lone centre press (the power key, which wakes it)
 * never reaches the UI, so a pocket bump stays dark; one within WAKE_MS after it passes, press and
 * release, so holding that second press still powers off. With Single press it passes at once.
 * Either way a passing press clears Standby's resume guard, so its release is not dropped, and
 * another centre press within WAKE_MS of it, or of the first UI loop pass with the screen lit, is
 * dropped with its release (wake_eat): turning the screen on (on_wm_keyup_fun) and that pass's
 * paint stall the loop about that long, so a blind second press is dispatched only after. A blind
 * double press only wakes, without clicking the selected row or turning the screen off again.
 * Repeated downs share their press's fate, including a dropped press's release. A passing hold
 * keeps stock's downstream long-press power-off. Other keys pass: the wheel and media work
 * dark. Returns 1 to drop the event. */
static void woke(unsigned now) { /* a passing dark press: not Standby's resume key */
    *(volatile unsigned char *)BOOT_KEY_GUARD = 0;
    st.woke_at = now;
    st.dark |= 2; /* ringnav_sleep restarts woke_at once the screen is lit */
}
static int wake_gate(void *e) {
    unsigned type = (unsigned)I(e, EVENT_TYPE), now = (unsigned)time_now_ms();
    if (type != EVT_KEY_DOWN && type != EVT_KEY_LONG && type != EVT_KEY_UP) return 0;
    unsigned key = (unsigned)I(e, EVENT_KEY);
    if (!g_backlight_status && key == KEY_RETURN) return 1;
    if (type == EVT_KEY_LONG) return 0; /* stock generates this downstream of the input hook */
    if (key != KEY_CENTER) return 0;
    /* Consume Standby's release guard once; our dropped press already drops its release. */
    if (type == EVT_KEY_DOWN && !g_backlight_status && *(volatile unsigned char *)BOOT_KEY_GUARD) {
        st.wake_down = 0;
        *(volatile unsigned char *)BOOT_KEY_GUARD = 0;
    }
    if (type == EVT_KEY_UP)
        st.wake_down = 0;
    else {
        if (st.wake_down) return st.wake_down == 2;
        st.wake_down = 1;
    }
    if (type == EVT_KEY_UP && st.wake_eat) {
        st.wake_eat = 0;
        return 1;
    }
    /* every press sets it, so one whose release never came (Standby while held) leaves none */
    if (type == EVT_KEY_DOWN && (st.wake_eat = now - st.woke_at < WAKE_MS)) {
        st.wake_down = 2;
        return 1;
    }
    if (!st.wake_double || g_backlight_status) { /* wake_pass is read by power_poll */
        if (!g_backlight_status && type == EVT_KEY_DOWN) woke(now);
        return st.wake_pass = 0;
    }
    if (type == EVT_KEY_UP) {
        int pass = st.wake_pass;
        st.wake_pass = 0;
        return !pass;
    }
    st.wake_pass = now - st.wake_at < WAKE_MS;
    st.wake_at = st.wake_pass ? 0 : now;
    if (st.wake_pass)
        woke(now);
    else
        st.wake_down = 2;
    return !st.wake_pass;
}

/* A loose wheel, or a thumb on its rim between two buttons, can close Centre with an edge button
 * (Return, Play/Pause, Previous, Next). A Centre press that starts while one is held, within
 * CHORD_MS of its press, is dropped whole (its repeats and release too), before the wake gate sees
 * it; the edge button keeps its own. Centre pressed first passes, but an edge press within
 * CHORD_MS of it ends Centre's hold timer (ringnav_hold_timer), so a rim press held near a corner
 * never reaches stock's Centre hold, the Shut Down dialog. Return's hold (Now Playing, Home) can
 * ghost a second press as it lets go: a Return press starting within RETURN_GHOST_MS of a hold's
 * release is dropped whole too. ponytail: CHORD_MS bounds a Return release that never came (Standby
 * while held); RETURN_GHOST_MS is the tuning knob, and only a ghost after the release is caught.
 * Returns 1 to drop the event. */
#define CHORD_MS 500
#define RETURN_GHOST_MS 135
static int chord_gate(void *e) {
    unsigned type = (unsigned)I(e, EVENT_TYPE), key = (unsigned)I(e, EVENT_KEY);
    unsigned now = (unsigned)time_now_ms();
    if (type != EVT_KEY_DOWN && type != EVT_KEY_LONG && type != EVT_KEY_UP) return 0;
    unsigned edge = key == KEY_RETURN     ? 1
                    : key == KEY_PLAY     ? 2
                    : key == KEY_BACK_BTN ? 4
                    : key == KEY_FWD_BTN  ? 8
                                          : 0;
    /* a repeat follows its press within 258 ms; a later down is a new press (a lost release) */
    if (edge && type == EVT_KEY_UP)
        st.edge_held &= ~edge;
    else if (edge &&
             (!(st.edge_held & edge) || (type == EVT_KEY_DOWN && now - st.edge_seen > 300))) {
        st.edge_held |= edge;
        st.edge_at = now;
        if (st.centre_at && (int)(now - st.centre_at) < CHORD_MS && st.centre_hold) {
            timer_remove(st.centre_hold); /* the rim's Centre: no hold */
            st.centre_hold = 0;
        }
    }
    if (edge && type != EVT_KEY_UP) st.edge_seen = now;
    if (key == KEY_RETURN) {
        /* a repeat follows its press within 258 ms; a later down is a new press */
        if (type == EVT_KEY_DOWN && !st.return_at &&
            (!st.return_eat || now - st.return_eat_at > 300))
            st.return_eat = st.return_up && (int)(now - st.return_up) < RETURN_GHOST_MS;
        if (st.return_eat) {
            st.return_eat_at = now;
            if (type == EVT_KEY_UP) st.return_eat = st.return_up = 0;
            return 1;
        }
        if (type == EVT_KEY_UP) {
            st.return_up = /* return_at may run a ms late, its | 1 */
                st.return_at && now - st.return_at + 1 >= KEY_HOLD_MS ? now | 1 : 0;
            st.return_at = 0;
        } else if (!st.return_at) {
            st.return_at = now | 1; /* never 0 while held */
            /* a hold's release can miss stock's filter as the windows close: its latch is stale */
            if (type == EVT_KEY_DOWN) *(volatile unsigned char *)RETURN_RELEASE_LATCH = 0;
        }
        return 0;
    }
    if (key != KEY_CENTER) return 0;
    /* a repeat follows its press within 258 ms; a later down is a new press (a lost release) */
    int fresh = type == EVT_KEY_DOWN && (!st.chord_eat || now - st.chord_at > 300);
    if (fresh) st.chord_eat = st.edge_held && now - st.edge_at < CHORD_MS ? 1 : -1;
    st.chord_at = now;
    int eat = st.chord_eat > 0;
    if (type == EVT_KEY_UP) st.chord_eat = st.centre_hold = 0;
    /* a passing press: its time, for an edge press after it; its release ends that */
    if (!eat)
        st.centre_at = type == EVT_KEY_UP ? 0 : st.centre_at && !fresh ? st.centre_at : now | 1;
    return eat;
}

/* main_loop_post_key_event: its one caller, on_wm_idle_status, posts SyncLink's (Eddict Player's)
 * play/pause, previous and next as a press and a release for the loop to dispatch later, stamped
 * with time_now_ms. Each post keeps its key and the time around it, so ringnav_input knows it. */
static struct {
    unsigned key, from, to;
} posted[4] __attribute__((section(".scratch")));
extern int stock_post_key_trampoline(void *, int, int);
int ringnav_post_key(void *loop, int pressed, int key) {
    static unsigned next __attribute__((section(".scratch")));
    unsigned from = time_now_ms();
    int ret = stock_post_key_trampoline(loop, pressed, key);
    next = (next + 1) % 4;
    posted[next].key = key & 255;
    posted[next].from = from;
    posted[next].to = time_now_ms();
    return ret;
}
static int remote_key(void *e) {
    unsigned key = (unsigned)I(e, EVENT_KEY), t = (unsigned)I(e, EVENT_TIME);
    for (int i = 0; i < 4; i++)
        if (posted[i].key == key && t - posted[i].from <= posted[i].to - posted[i].from)
            return posted[i].key = 0, 1;
    return 0;
}

/* window_manager_dispatch_input_event: while q2video plays (books.c) no key or touch reaches the
 * UI; a key's release goes to the player instead. A card game (addon.c) takes every event but the
 * music's keys. Every event times ringnav_sleep's idle. */
int ringnav(void *ctx, void *event);
/* A wheel tick needs a ring frame with pressure within WHEEL_FRESH_MS (wheel_sample stamps it
 * before it posts), and no stop from Return, so a tick with no finger behind it goes no further,
 * whatever posted it. Its release follows its press, so AWTK never keeps a press with no release.
 * replay_wheel calls ringnav itself and never passes here. Signed: a frame can stamp after the
 * clock is read. ponytail: ticks queued behind a UI stalled past WHEEL_FRESH_MS after the last
 * frame go too; a flick's tail, raise the knob if it is ever missed. */
static int tick_gate(void *e) {
    unsigned type = (unsigned)I(e, EVENT_TYPE), key = (unsigned)I(e, EVENT_KEY);
    if ((type != EVT_KEY_DOWN && type != EVT_KEY_UP) || (key != KEY_PREV && key != KEY_NEXT))
        return 0;
    unsigned bit = key == KEY_NEXT ? 2 : 1;
    if (type == EVT_KEY_UP) {
        int drop = (st.tick_drop & bit) != 0;
        st.tick_drop &= ~bit;
        return drop;
    }
    int muted = wheel_mute;
    unsigned now = (unsigned)time_now_ms();
    if (!muted && (int)(now - wheel_frame_at) < WHEEL_FRESH_MS) {
        st.tick_drop &= ~bit;
        st.tick_at = now;
        return 0;
    }
    st.tick_drop |= bit;
    if (Q2POD_DEV && !muted) wheel_gate_note();
    return 1;
}
/* A wheel tick's step of the volume, as stock's dialog makes it (volume_dialog_on_keyup): within 0
 * and the volume limit, set and saved. */
static void vol_step(unsigned key) {
    int value = g_volume + (key == KEY_NEXT ? 1 : -1);
    if (value < 0) value = 0;
    if (value > g_maxvolume) value = g_maxvolume;
    if (value > 100) value = 100;
    if (value == g_volume) return;
    g_volume = value;
    device_set_volume(value, 1);
    write_int_config(value, "PLAYSET", "VOLUME");
}
int ringnav_input(void *wm, void *e) {
    if (e && tick_gate(e)) return 0;
    if (e && (chord_gate(e) || wake_gate(e))) return 0;
    st.last_input = time_now_ms();
    unsigned type = e ? (unsigned)I(e, EVENT_TYPE) : 0;
    unsigned key = e ? (unsigned)I(e, EVENT_KEY) : 0;
    if (video_on()) {
        if (e && I(e, EVENT_TYPE) == EVT_KEY_UP) video_key((unsigned)I(e, EVENT_KEY));
        return 0;
    }
    if (addon_game_input(e)) return 0;
    if (type >= EVT_POINTER_FIRST && type <= EVT_POINTER_ABORT) { /* a finger (stale_heal) */
        st.touch_at = st.last_input;
        st.touch_type = (int)type;
        st.touch_x = I(e, EVENT_X);
        st.touch_y = I(e, EVENT_Y);
    }
    if ((type == EVT_KEY_DOWN || type == EVT_KEY_UP || type == EVT_KEY_LONG) &&
        (key == KEY_PREV || key == KEY_NEXT || key == KEY_CENTER) && usb_prompt()) {
        reset_poweroptions_timer(1, 1, 1);
        if (type == EVT_KEY_UP) ringnav(0, e);
        return 0;
    }
    void *top = window_manager_get_top_window(wm);
    if (type == EVT_KEY_UP && (key == KEY_PREV || key == KEY_NEXT) && usable() &&
        window_manager_is_animating(wm) && top &&
        !tk_strcmp(widget_get_prop_str(top, "name", ""), "playing_page")) {
        drop_input();
        st.held = 0; /* Queued list ticks never become volume ticks. */
        reset_poweroptions_timer(1, 1, 1);
        vol_step(key);
        return 0;
    }
    /* No Quick Settings over a prompt. Stock's pull-down (on_wm_tsmove_fun) stays out of its own
     * Add and Delete prompts by their flags, but not out of Shut Down? (g_poweroff_state 1, it
     * tests for 2) or a prompt of ours. Return there was stock's, and took the windows away under
     * the prompt's modal loop: Shut Down? came back with dead buttons, g_poweroff_state still 1,
     * until a restart (#303). A touch that lands with a prompt in front starts below the pull's
     * band, whatever stock recorded for it. */
    if (type == EVT_POINTER_FIRST) {
        int ret = stock_input_trampoline(wm, e);
        void *front = window_manager_get_top_window(wm);
        if (front && !tk_strcmp(widget_get_prop_str(front, "name", ""), "confirminfo_dialog") &&
            I(g_pressdown_y, 0) < QS_PULL_Y)
            I(g_pressdown_y, 0) = QS_PULL_Y;
        return ret;
    }
    if ((type != EVT_KEY_DOWN && type != EVT_KEY_UP) || !remote_key(e))
        return stock_input_trampoline(wm, e);
    /* Buttons lock is the buttons': stock's dark lock filters would drop SyncLink's keys (#113) */
    unsigned char lock = g_keylock_flag;
    g_keylock_flag = 0;
    int ret = stock_input_trampoline(wm, e);
    g_keylock_flag = lock;
    return ret;
}

/* window_manager_paint, a leaf (tools/build.py WM_PAINT_LEAF): the window manager's own paint
 * (vtable +0xc), except while q2video or a card game has the framebuffer (each poll repaints after
 * it) and while the screen is off, unless a window animates (AWTK steps it from the paint); the
 * first lit pass repaints the whole screen (ringnav_sleep; docs/internals.md Battery). */
int ringnav_wm_paint(void *wm) {
    if (wm && !g_backlight_status && !window_manager_is_animating(wm)) return 0; /* RET_OK */
    int away = video_on() || addon_game_on();
    void *vt = wm && !away ? P(wm, 0x94) : (void *)0;
    int (*paint)(void *) = vt ? (int (*)(void *))P(vt, 0xc) : (int (*)(void *))0;
    return paint ? paint(wm) : away ? 0 : 0x10; /* RET_BAD_PARAMS, as stock */
}

/* buzzeer_switch (0x4fe3f8), every Key Tone click, stock's and ringnav()'s: the MCU's buzzer
 * (system("cmd_mcu write_str buzzer") when g_keytone_flag is set). Silent while headphones (either
 * jack) or a Bluetooth or USB output listen, since the buzzer is the device's own speaker;
 * hciplayer holds their PCM, so the click has no other way out. Playback alone leaves it clicking.
 * Click With Headphones (IPOD KEYTONEHP, off) keeps the buzzer clicking there too, as stock does;
 * Key Tone off is still silent.
 * The command waits out the buzzer and the MCU's park (tools/mcu_pwm_clock.c), so it runs on a
 * worker, not the UI thread; a click while one sounds is dropped, as the buzzer is busy anyway.
 * With the screen off a pass clicks once (tone_dark; ringnav_sleep).
 */
static volatile int tone_busy;
static void *tone_worker(void *on) {
    stock_buzzer_trampoline((int)(long)on);
    tone_busy = 0;
    return 0;
}
int ringnav_buzzer(int on) {
    int listening = g_po_status || g_bal_status || mclGetOutputWay();
    if ((listening && (accent(), !st.keytone_hp)) || !g_keytone_flag || tone_busy || tone_dark)
        return 0;
    unsigned long tid;
    tone_busy = 1;
    if (pthread_create(&tid, 0, tone_worker, (void *)(long)on)) {
        tone_busy = 0;
    } else {
        pthread_detach(tid);
        tone_dark = !g_backlight_status;
    }
    return 0;
}

/* on_wm_keydown_before_fun. Stock clicks (buzzeer_switch, which reads g_keytone_flag) on the press,
 * before the release moves anything. A wheel press on a page whose release ringnav() takes as row
 * navigation runs the whole stock body with the flag off for that one synchronous call, so its
 * gates and latches still run, and the release clicks when the selection changes. The test reads
 * the tree only: nothing is loaded, selected or restored. Carousels, the pixel-scroll fallback,
 * scrub, the volume and every other key keep the stock click. */
int ringnav_keydown(void *ctx, void *event) {
    unsigned key = event ? (unsigned)I(event, EVENT_KEY) : 0;
    if (ple_key(key, 1)) return STOP;
    void *top = window_manager_get_top_window(window_manager());
    unsigned char tone = g_keytone_flag;
    void *row = (void *)0;
    entries_t s = { &row, 0, 1, 4096 };
    st.tone_key = 0;
    if ((key == KEY_PREV || key == KEY_NEXT) && usable() && allowed_top(top)) {
        void *w = surface_under(top, (void *)0, (void *)0, 1);
        unsigned n = w && kind(w) != 3 ? widget_count_children(w) : 0;
        for (unsigned i = 0; i < n && !s.n; ++i) collect(widget_get_child(w, i), &s, 1);
        if (s.n) st.tone_key = key;
    }
    if (st.tone_key) g_keytone_flag = 0;
    int result = stock_keydown_trampoline(ctx, event);
    if (st.tone_key) g_keytone_flag = tone;
    /* Coverflow moves on release; stop the press before it navigates the focused list below. */
    if (!result && (key == KEY_PREV || key == KEY_NEXT) && top &&
        !tk_strcmp(widget_get_prop_str(top, "name", ""), "coverflow_page"))
        return STOP;
    return result;
}

/* Stock's setting editors, which have no list. Brightness, maximum and startup volume and balance
 * step with the wheel through their own +/- (limits and saving stay stock's), and Centre goes back.
 * Quick Settings' brightness slider has no +/-: its value is set, firing stock's change callback.
 * Set date and time and the sleep timer edit one text_selector field at a time, outlined by
 * edit_paint: the wheel turns it, Centre moves to the next field and, after the last, to OK, which
 * Centre presses; a turn on OK goes back to the fields. A double Centre turns the screen off. */
static int slider_page(void *top) {
    const char *name = top ? widget_get_prop_str(top, "name", "") : "";
    return !tk_strcmp(name, "backlight_page") || !tk_strcmp(name, "maxvol_page") ||
           !tk_strcmp(name, "bootvol_page") || !tk_strcmp(name, "balance_page");
}

/* Classic's Set Date and Set Time: no OK stop, and AM/PM a field in 12-hour. */
static int time_form(void *top) {
    return top && classic_pages() &&
           !tk_strcmp(widget_get_prop_str(top, "name", ""), "manualtime_page");
}

static int fields(void *top) {
    const char *name = top ? widget_get_prop_str(top, "name", "") : "";
    return !tk_strcmp(name, "manualtime_page")      ? 5 + (time_form(top) && !g_time24h_flag)
           : !tk_strcmp(name, "sleepshutdown_page") ? 2
                                                    : 0;
}

static void *field_widget(void *top, int f) {
    static const char *const names[] = { "tselector_year", "tselector_month", "tselector_day",
                                         "tselector_hour", "tselector_min",   "btn_enter" };
    int n = fields(top);
    if (n == 6 && f == 5) return (void *)0; /* AM/PM */
    return n ? widget_lookup(top, names[f == n ? 5 : n == 2 ? f + 3 : f], 1) : (void *)0;
}

/* A swipe between the date and time pages moves the field to that page's first. */
static int field_on_page(int f, int time) { return time != (f >= 3) ? (time ? 3 : 0) : f; }
/* The field the wheel edits. */
static int field_at(void *top) {
    int n = fields(top), f = clamp_step(widget_get_prop_int(top, "_edit_field", 0), n, 0);
    void *slide = n >= 5 && f < n ? widget_lookup(top, "slide_view", 1) : (void *)0;
    return slide ? field_on_page(f, widget_get_prop_int(slide, "value", 0) == 1) : f;
}

static void field_set(void *top, int f) {
    prop(top, "_edit_field", f);
    int n = fields(top);
    void *slide = n >= 5 && f < n ? widget_lookup(top, "slide_view", 1) : (void *)0;
    if (slide) widget_set_prop_int(slide, "value", f >= 3);
    widget_invalidate_force(top, (void *)0);
}

/* The side keys move between the fields and OK, where there's one: Next on, Previous back, stopping
 * at the ends. */
static int field_key(void *top, unsigned key) {
    int n = fields(top);
    if (!n || (key != KEY_FWD_BTN && key != KEY_BACK_BTN)) return 0;
    /* a pending Centre would step (or press OK) again. ponytail: it's dropped, not run, so under
     * Classic it can be the switched-off Sleep Timer's flip */
    stop_timer(&st.edit_press);
    field_set(top, clamp_step(field_at(top), n - time_form(top), key == KEY_FWD_BTN ? 1 : -1));
    return 1;
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
    void *sel[6], *slide;
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
static const char *time_month(void) { return time_months[clamp_step(time_value(1) - 1, 11, 0)]; }

/* The bar's title reads the page's own label (bar_title_sync). */
static void time_title(void *win) {
    void *label = (void *)0;
    char s[16];
    bar_title_of(win, &label);
    if (!label) return;
    tk_snprintf(s, sizeof s, "%s %d, %d", time_month(), time_value(2), time_value(0));
    widget_set_text_utf8(label, s);
}
static int time_changed(void *win, void *event) {
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
static void time_wheel(void *top, int f, int dir) {
    int half = f == TIME_AMPM || (f == 3 && fields(top) == 6);
    void *sel = field_widget(top, half ? 3 : f);
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
static int time_ampm(void *win, void *event) {
    (void)event;
    time_wheel(win, TIME_AMPM, 1);
    return 0;
}

/* Stock's window as it loads, before its init, so that init's own setting of the selectors places
 * them at this size. */
static void time_open(void *win) {
    static const char *const hide[] = { "img_dot", "slide_indicator" };
    void *c, *ok = field_widget(win, fields(win));
    memset(&tf, 0, sizeof tf);
    for (unsigned i = 0; i < sizeof hide / sizeof *hide; ++i)
        if ((c = widget_lookup(win, hide[i], 1))) widget_set_visible(c, 0, 0);
    for (int t = 0; t < 2; ++t) {
        const time_row_t *row = time_row(t);
        for (int i = 0; i < row->n; ++i) {
            c = field_widget(win, row->first + i);
            if (!c) continue;
            widget_move_resize(c, row->x[i], TIME_CELL_Y,
                               row->first + i ? TIME_CELL_W : TIME_YEAR_W, TIME_CELL_H);
            widget_set_opacity(c, 0);
            /* a drag steps a row, TIME_CELL_H / 3; stock's release adds its speed times this */
            widget_set_prop_int(c, "yspeed_scale", 0);
        }
        void *page = field_widget(win, row->first);
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
        widget_move_resize(ok, 0, 244, 375, 26);
        widget_set_text_utf8(ok, "Save");
        widget_set_opacity(ok, 0);
    }
    st.slider_win = win;
    st.slider_w = (void *)0; /* slider_gone clears the window; this page has no slider */
    widget_on(win, EVT_DESTROY, slider_gone, win);
}

/* After stock's init: it rebuilds the label the title goes in, and its own handlers, first in line,
 * have clamped the day by the time these read it. */
extern int stock_manualtime_trampoline(void *win, void *ctx);
int ringnav_manualtime(void *win, void *ctx) {
    int result = stock_manualtime_trampoline(win, ctx);
    if (time_form(win)) {
        for (int f = 0; f < 5; ++f) {
            void *sel = tf.sel[f] = field_widget(win, f);
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
static void clock_fill(void *canvas, const int *p, int n, unsigned rgb) {
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
void paint_clock(void *w, void *canvas, int x, int y, int size, const char *art, int h, int m,
                 const clock_part_t *parts, int n) {
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
static void time_text(void *canvas, int light, const char *face, const unsigned *s, unsigned n,
                      const rect_t *r, unsigned px, unsigned rgb, unsigned alpha) {
    unsigned color = alpha << 24 | (RGBA(flip_if(light, rgb)) & 0xffffff);
    if (face)
        draw_in_face(canvas, face, s, n, r, px, color);
    else
        draw_centred(canvas, s, n, r, px, color);
}
/* A cell's or the tile's number: its text, widened. */
static unsigned time_chars(unsigned *to, const char *s) {
    unsigned n = 0;
    while (s[n] && n < 5) to[n] = (unsigned char)s[n], ++n;
    return n;
}
/* The page, in the window's coordinates, under its children. */
static void time_paint(void *w, void *canvas) {
    static const unsigned colon = ':', save[] = { 'S', 'a', 'v', 'e' };
    static const char *const cells[2][2] = {
        { "classic_light_cell_45", "classic_light_cell_lit_45" },
        { "classic_light_cell_60", "classic_light_cell_lit_60" },
    };
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), s[5], n;
    int time = tf.slide && widget_get_prop_int(tf.slide, "value", 0) == 1;
    int at = field_on_page(widget_get_prop_int(w, "_edit_field", 0), time);
    int hour = time_value(3), minute = time_value(4), L = ipod_light();
    const time_row_t *row = time_row(time);
    char b[6];
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
        time_text(canvas, L, (const char *)0, s, n, &head, 28, 0xffffff, 255);
        tk_snprintf(b, sizeof b, "%02d", time_value(2));
        n = time_chars(s, b);
        time_text(canvas, L, (const char *)0, s, n, &day, 79, 0x1b2030, 255);
    }
    paint_art(w, canvas, row->tray, row->tray_x - TIME_TRAY_PAD, TIME_TRAY_Y - TIME_TRAY_PAD);
    for (int i = 0; i < row->n; ++i) {
        int f = row->first + i, cw = f ? TIME_CELL_W : TIME_YEAR_W;
        rect_t r = { row->x[i], TIME_CELL_Y, cw, TIME_CELL_H };
        paint_art(w, canvas, cells[!f][f == at], r.x - TIME_CELL_PAD, r.y - TIME_CELL_PAD);
        if (f == TIME_AMPM)
            tk_snprintf(b, sizeof b, "%s", hour < 12 ? "AM" : "PM");
        else if (f == 3 && row->n == 3)
            tk_snprintf(b, sizeof b, "%d", hour % 12 ?: 12);
        else
            tk_snprintf(b, sizeof b, f ? "%02d" : "%d", time_value(f));
        n = time_chars(s, b);
        time_text(canvas, L, (const char *)0, s, n, &r, f == TIME_AMPM ? 30 : 35, 0xffffff, 255);
    }
    if (row->sep_x) {
        rect_t r = { row->sep_x, TIME_CELL_Y, 12, TIME_CELL_H };
        time_text(canvas, L, (const char *)0, &colon, 1, &r, 32, 0xffffff, 255);
    }
    rect_t label = { 0, 244, 375, 26 }, under = { 0, 245, 375, 26 };
    time_text(canvas, L, (const char *)0, save, 4, &under, 24, 0, 0x59);
    time_text(canvas, L, (const char *)0, save, 4, &label, 24, 0xffffff, 0xb8);
    stock_canvas_set_fill_color(canvas, fill);
}

static int edit_single(const void *info) {
    st.edit_press = 0;
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    if (!usable() || window_manager_is_animating(wm) || pointer_down()) return 0;
    *(volatile unsigned char *)KEY_LOCKOUT = 0; /* stock's wheel lockout after the centre key */
    int n = fields(top), f = field_at(top);
    void *hidden = n && !geom_val(V_TOOLBAR) ? widget_lookup(top, "view_sleep", 1) : (void *)0;
    if (hidden && !widget_get_visible(hidden)) /* the switch's off: only its row is showing */
        switch_click(0, 0);
    else if (!n) {
        if (slider_page(top)) navigator_back();
    } else if (f < n - time_form(top))
        field_set(top, f + 1);
    else {
        void *ok = field_widget(top, n);
        char click[0x30];
        if (ok) time_click(ok, pointer_event_init(click, EVT_CLICK, ok, 0, 0));
    }
    return 0;
}

static int edit_key(void *top, unsigned key) {
    static const unsigned release[EVENT_KEY / 4 + 1] = { [EVENT_KEY / 4] = KEY_CENTER };
    int quick = top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "statusbar_dialog");
    int n = fields(top);
    if (!quick && !n && !slider_page(top)) return 0;
    if (key == KEY_CENTER) {
        if (quick) return 0;
        int press = center_press(&st.edit_press, &st.edit_at, edit_single, 0);
        if (!press)
            press = center_press(&st.edit_press, &st.edit_at, edit_single,
                                 0); /* after an overdue one */
        if (press == 2 && g_backlight_status) on_wm_keyup_fun((void *)0, (void *)release);
        return 1;
    }
    if (key != KEY_PREV && key != KEY_NEXT) return 0;
    /* sliding open or shut (quick_stuck's states, or its slide timer): the wheel waits for it */
    if (quick && ((unsigned)(I(g_dropdown_menu_state, 0) - 1) < 2 ||
                  widget_get_prop_int(top, "timer_idex", -1) != -1))
        return 1;
    if (quick && quick_volume()) return 0; /* stock's wheel volume */
    void *wm = window_manager();
    if (window_manager_is_animating(wm) || pointer_down()) return 1;
    int dir = key == KEY_NEXT ? 1 : -1;
    if (quick) {
        void *slider = widget_lookup(top, "slider_backlight", 1);
        if (!slider || !widget_get_visible(slider)) return 1;
        int value = widget_get_prop_int(slider, "value", 0);
        int min = widget_get_prop_int(slider, "min", 0),
            max = widget_get_prop_int(slider, "max", 100);
        int next = min + clamp_step(value - min, max - min, dir);
        if (next != value) widget_set_prop_int(slider, "value", next);
    } else if (!n) {
        void *button = widget_lookup(top, dir > 0 ? "img_add" : "img_dec", 1);
        char click[0x30];
        if (button && widget_get_prop_bool(button, "enable", 1))
            stock_dispatch_trampoline(button, pointer_event_init(click, EVT_CLICK, button, 0, 0));
    } else {
        int f = field_at(top);
        void *sel = field_widget(top, f);
        unsigned options = f < n && sel ? text_selector_count_options(sel) : 0;
        if (time_form(top))
            time_wheel(top, f, dir);
        else if (f == n)
            field_set(top, dir > 0 ? 0 : n - 1);
        else if (options && options <= 0x7fffffff) {
            int index = I(sel, SELECTOR_INDEX), next = clamp_step(index, (int)options - 1, dir);
            if (next != index) text_selector_set_selected_index(sel, (unsigned)next);
            field_set(top, f);
        }
    }
    return 1;
}

/* ringnav_paint, after the children: the field the wheel edits, framed in white; OK, when it is
 * next, as a selected row: Minimal's off-white card with its label dark. */
static void edit_paint(void *w, void *canvas) {
    void *top = window_manager_get_top_window(window_manager());
    if (time_form(top)) return;
    int n = fields(top), f = n ? field_at(top) : 0;
    if (!w || !n || w != field_widget(top, f) || !P(canvas, CANVAS_LCD)) return;
    int ww = I(w, W_W), wh = I(w, W_H);
    if (ww < 5 || wh < 5) return;
    if (f == n) {
        unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), k = 0;
        rect_t r = { 0, 0, ww, wh };
        const unsigned *text = widget_get_text(w);
        while (text && k < 16 && text[k]) ++k;
        fill_box(canvas, &r, RGBA(MINIMAL_FILL), SEL_RADIUS);
        draw_centred(canvas, text, k, &r, EDIT_OK_PX, RGBA(0x171717));
        stock_canvas_set_fill_color(canvas, fill);
        return;
    }
    unsigned old = (unsigned)I(P(canvas, CANVAS_LCD), LCD_STROKE_COLOR);
    rect_t r = { 0, 0, ww, wh };
    frame_box(canvas, &r, 0xffffffff, SEL_RADIUS);
    stock_canvas_set_stroke_color(canvas, old);
}

static int replay_wheel(const void *info);
int ringnav(void *ctx, void *event);
/* A wheel tick the page cannot take yet: counted, its event kept for replay. Replay's own tick,
 * held again, always counts back in (replay_wheel takes it off); live events never equal
 * st.held_event, so the cap still holds at intake. */
static int hold_wheel(void *ctx, void *event, int dir) {
    unsigned now = (unsigned)time_now_ms();
    if (!st.held) st.held_at = now;
    if (st.held * dir < HOLD_MAX || event == st.held_event) st.held += dir;
    st.held_ctx = ctx;
    if (event != st.held_event) memcpy(st.held_event, event, sizeof st.held_event);
    if (!st.held_timer) st.held_timer = timer_add(replay_wheel, 0, HOLD_POLL_MS);
    return STOP;
}

static int replay_wheel(const void *info) {
    void *wm = window_manager();
    if (!st.held || (unsigned)time_now_ms() - st.held_at > HOLD_MS || !usable()) {
        st.held = 0;
        st.held_timer = 0;
        return 0; /* anything but RET_REPEAT removes the timer */
    }
    if (window_manager_is_animating(wm) || pointer_down()) return 8;
    int dir = st.held > 0 ? 1 : -1;
    unsigned key = dir > 0 ? KEY_NEXT : KEY_PREV;
    st.held_event[EVENT_KEY / 4] = key;
    st.tone_key = key; /* clicks on its row change, as a live tick whose press was silenced */
    ringnav(st.held_ctx, st.held_event); /* held again: hold_wheel counts it back in */
    st.held -= dir;
    if (st.held) return 8; /* RET_REPEAT */
    st.held_timer = 0;
    return 0;
}

/* The keyboard is its own top window: take the edit from the input method's owner. Close it
 * before stock's Return or the page's own can destroy the edit under it, which crashes demo; the
 * next Return goes back. Stock's search dialogs and the playlist editor's search (search_edit)
 * keep their text. Every other T9 edit of ours (EDIT_NAME, edit_create) is a cancel: emptied
 * first, so the blur's EVT_VALUE_CHANGED reads as no entry and its page backs out of the edit. */
int search_key(unsigned key) {
    if (key != KEY_RETURN) return 0;
    void *im = input_method(), *edit = im ? P(im, IM_WIDGET) : (void *)0;
    const char *name = edit ? widget_get_prop_str(edit, "name", "") : "";
    int ours = !tk_strcmp(name, EDIT_NAME);
    if (!edit || (!ours && tk_strcmp(name, "search_edit"))) return 0;
    if (ours) widget_set_text_utf8(edit, "");
    widget_set_focused(edit, 0);
    input_method_request(im, (void *)0); /* searchbox dialogs do not close on blur */
    return 1;
}

int ringnav(void *ctx, void *event) {
    pull_cancel();
    /* The stock filter dereferences the event before returning. */
    if (!event) {
        drop_input();
        return 0;
    }
    unsigned key = (unsigned)I(event, EVENT_KEY);
    if (ple_key(key, 2)) return STOP;
    /* This release's press was silenced by ringnav_keydown: a row change below clicks instead. */
    int owned = st.tone_key == key;
    st.tone_key = 0;
    if ((key == KEY_PREV || key == KEY_NEXT) &&
        *(volatile signed char *)KEY_LOCKOUT <= 8 - LOCKOUT_KEEP)
        *(volatile unsigned char *)KEY_LOCKOUT = 0;
    if (key == KEY_CENTER || key == KEY_RETURN) st.held = 0; /* a button ends a held turn */
    /* A tick the other way ends it too and is dropped with it, before or during its replay;
     * replay's own ticks go the held way. */
    if (st.held * (key == KEY_NEXT ? 1 : key == KEY_PREV ? -1 : 0) < 0) {
        st.held = 0;
        drop_input();
        return STOP;
    }
    void *front = window_manager_get_top_window(window_manager());
    if (usable() && field_key(front, key)) return STOP; /* before stock's track skip */
    /* The restart toast's msginfo_dialog stops Previous and Next itself (0x49b7c8), for its 2 s of
     * the 3 s: the same key goes on. ponytail: the toast stays up its time; another key is still
     * stopped. */
    if ((key == KEY_BACK_BTN || key == KEY_FWD_BTN) && restart_armed(key == KEY_FWD_BTN) && front &&
        !tk_strcmp(widget_get_type(front), "dialog")) {
        key == KEY_FWD_BTN ? next_quick_click() : prev_quick_click();
        return STOP;
    }
    /* The rating picker's keys, before the page's own (its Return would leave it) */
    if (usable() && rate_key(window_manager_get_top_window(window_manager()), key)) return STOP;
    /* A game's keys, before stock's lock filter, media keys and screen-off double Centre */
    if (usable() && games_key(window_manager_get_top_window(window_manager()), key)) return STOP;
    /* Cover Flow's flip: a Centre during it does nothing. Its Return is the page's (coverflow.c
     * keyup), the wheel waits at the page's no surface (hold_wheel). */
    if (key == KEY_CENTER && coverflow_turning() && !g_power_longkey) return STOP;
    /* Return or Centre closes quick settings, as its swipe up does (dialog_quit); stock leaves
     * Return to its Home button and Centre to turning the screen off. A held Return's release
     * goes to the stock filter, which consumes its latch (the hold closed it itself,
     * compact_now_playing). */
    if ((key == KEY_CENTER ||
         (key == KEY_RETURN && !*(volatile unsigned char *)RETURN_RELEASE_LATCH)) &&
        usable()) {
        void *top = window_manager_get_top_window(window_manager());
        if (top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "statusbar_dialog")) {
            dialog_quit(top, 0);
            return STOP;
        }
        /* The library scan's dialog, once its scan has ended: stock's Return and Cancel only stop
         * a scan (0x4a2574) and do nothing until its timer (0x4a2bac) has counted SCAN_DONE to 3
         * and slept 2 s (1 s until V1.34), ~2.4 s, then close it itself; so a press there seemed
         * lost. The dialog's init clears SCAN_DONE (0x4a2de4). dialog_quit is safe twice, so the
         * timer's own close after ours does nothing. */
        if (top && *(volatile int *)SCAN_DONE &&
            !tk_strcmp(widget_get_prop_str(top, "name", ""), "updatemusic_dialog")) {
            dialog_quit(top, 0);
            return STOP;
        }
    }
    if (search_key(key)) return STOP;
    int result = usb_prompt() ? 0 : stock_keyup_trampoline(ctx, event);
    if (key == KEY_PLAY && hold_released()) return STOP;
    /* Media controls follow the active source, including when local Now Playing is visible. */
    if (!result && (media_focused() || media_owner() != 1) &&
        (key == KEY_PLAY || key == KEY_FWD_BTN || key == KEY_BACK_BTN)) {
        media_key(key);
        return STOP;
    }
    if (result) {
        cancel_center();
        if (key == KEY_PREV || key == KEY_NEXT) drop_wheel();
        /* Keep the home interval independent of rejected list navigation. */
        if (key == KEY_CENTER || !usable()) drop_spin();
        return result;
    }
    /* A volume dialog just opened is the window in front but not yet the window manager's key
     * target: that comes with its open event, on the pass's idle or after its animation. A tick
     * dispatched before then went past it to on_wm_keyup_fun, which opened one more dialog over
     * it; a dark pass takes several ticks, so a dark turn left a stack, each dialog closing 3 s
     * after the last and showing the volume it opened at (#303). The tick steps the volume here,
     * and the dialog's slider with it. */
    void *vol = key == KEY_PREV || key == KEY_NEXT ? vol_top() : (void *)0;
    if (vol && P(window_manager(), W_KEY_TARGET) != vol) {
        vol_step(key);
        void *slider = widget_lookup(vol, "slider_vol", 1);
        if (slider) widget_set_prop_int(slider, "value", g_volume);
        return STOP;
    }
    if (key == KEY_RETURN && st.scrub) { /* ends the scrub without leaving the page */
        np_cancel();
        if (window_manager_get_top_window(window_manager()) == st.np_win) return STOP;
    }
    /* Full-screen lyrics: Return goes back to the column first, as a tap does */
    if (key == KEY_RETURN && st.np_full &&
        window_manager_get_top_window(window_manager()) == st.np_win) {
        np_full(0);
        return STOP;
    }
    /* Home's Settings list goes back to Home's list (coverflow_home_back). */
    if (key == KEY_RETURN && coverflow_home_back(window_manager_get_top_window(window_manager())))
        return STOP;
    /* Return on Home's main list selects Now Playing (its first row); on that row it opens Now
     * Playing with the current queue untouched. The carousel keeps stock's Return. */
    if (key == KEY_RETURN && usable()) {
        void *wm = window_manager(), *top = window_manager_get_top_window(wm);
        void *w = home_menu(top) && !window_manager_is_animating(wm)
                      ? surface_under(top, (void *)0, (void *)0, 1)
                      : (void *)0;
        if (w && load(&g_menu, w, 1) && g_menu.n && g_menu.kind != 3) {
            st.touch_mode = 0;
            if (widget_get_prop_int(w, SEL, -1) != 0) {
                select(&g_menu, 0);
                reveal(&g_menu, 0, 1);
                widget_invalidate_force(w, (void *)0);
            } else
                compact_now_playing();
            return STOP;
        }
    }
    /* Back by button: the page behind shows its row. A sliding page is painted once more, into its
     * closing snapshot, inside stock's handling of this release; it keeps hiding its own row until
     * that is done. */
    if (key == KEY_RETURN && st.touch_mode) {
        rearm(&st.untouch_timer, untouch, 0);
        if (!st.untouch_timer) st.touch_mode = 0;
    }
    if (key != KEY_CENTER && key != KEY_PREV && key != KEY_NEXT) return result;
    if (key == KEY_CENTER) {
        drop_spin();
        fx_cancel();
    } else
        cancel_center();
    if (st.center_timer && (unsigned)time_now_ms() - st.last_center >= DOUBLE_CLICK_MS) {
        unsigned timer = st.center_timer;
        confirm_center((void *)0);
        timer_remove(timer);
    }
    /* An overdue click may change power/lock state; inspect it after its callback. */
    if (!usable()) {
        drop_input();
        return result;
    }
    /* Match the stock power-key release exclusions, including release after long press. */
    if (key == KEY_CENTER &&
        (g_power_longkey || g_ingore_bootkey_flag || *(volatile unsigned char *)BOOT_KEY_GUARD)) {
        /* V1.36's confirm takes every Centre release itself (0x499b9c), so the one that ends the
         * hold that opened Shut Down? never reaches on_wm_keyup_fun, which cleared these: cleared
         * here, or each later Centre in the prompt is dropped as that release (#303). */
        void *held = window_manager_get_top_window(window_manager());
        if (held && !tk_strcmp(widget_get_prop_str(held, "name", ""), "confirminfo_dialog")) {
            g_power_longkey = g_ingore_bootkey_flag = 0;
            *(volatile unsigned char *)BOOT_KEY_GUARD = 0;
        }
        cancel_center();
        return result;
    }
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    if (spot_key(top, key)) return STOP; /* Spotify's page: Centre scrubs */
    if (xfade_key(top, key))
        return STOP; /* Crossfade's page: Centre toggles, the wheel sets the length */
    if (eq_key(top, key))
        return STOP; /* EQ's Simple tab, or its tabs: the wheel moves or adjusts, Centre acts */
    if (edit_key(top, key)) {
        drop_input();
        return STOP;
    }

    /* Outside a scrub the volume's band over Now Playing leaves the keys the page's: Centre's
     * presses as on it, the wheel the volume's, and a pending press survives the ticks */
    int np = top == st.np_win || (!st.scrub && vol_band(wm));
    if (!np || window_manager_is_animating(wm) || pointer_down())
        np_cancel();
    else if (key != KEY_CENTER && np_lyrics(key))
        return STOP;
    else if (key == KEY_CENTER || st.scrub)
        return np_key(st.np_win, key);
    if (!allowed_top(top) && !usb_prompt()) {
        drop_input();
        return result;
    }
    /* A double Centre turns the screen off on every listed page, also where the page takes Centre
     * itself (the Books reader's caption), has no row selected or is still sliding in. A list's
     * selected row times its own double press (center_timer). */
    if (key == KEY_CENTER && !st.center_timer) {
        unsigned now = (unsigned)time_now_ms();
        if (top == st.dc_top && now - st.dc_at < DOUBLE_CLICK_MS) {
            static const unsigned release[EVENT_KEY / 4 + 1] = { [EVENT_KEY / 4] = KEY_CENTER };
            st.dc_top = (void *)0;
            drop_input();
            if (g_backlight_status) on_wm_keyup_fun((void *)0, (void *)release);
            return STOP;
        }
        st.dc_top = top;
        st.dc_at = now;
    }
    int dir = key == KEY_NEXT ? 1 : key == KEY_PREV ? -1 : 0;
    if (window_manager_is_animating(wm) || pointer_down()) {
        drop_input();
        return dir && window_manager_is_animating(wm) ? hold_wheel(ctx, event, dir) : STOP;
    }
    coverflow_lost_touch();
    if (!dir) coverflow_wheel_land();     /* Centre opens the album the caption names */
    if (books_key(top, key)) return STOP; /* the reader's pages and caption */
    void *w = surface_under(top, (void *)0, (void *)0, dir != 0);
    if (!is_home(top, w) || w != st.home_surface) st.home_surface = (void *)0;
    if (!w || !load(&g_menu, w, 1)) {
        drop_input();
        return dir ? hold_wheel(ctx, event, dir) : result;
    }
    if (st.center_timer && !pending_matches(top, &g_menu)) cancel_center();
    int touch = widget_get_prop_int(w, TOUCH, 0);
    if (touch) {
        stop_scroll(&g_menu);
        prop(w, TOUCH, 0); /* Centre consumes the interrupted gesture too. */
    }
    int cur = reconcile(&g_menu, touch || !moving(&g_menu));
    unsigned now = (unsigned)time_now_ms();
    if (!dir) {
        if (st.center_timer && now - st.last_center < DOUBLE_CLICK_MS) {
            cancel_center();
            return result; /* Let the stock downstream short-press handler turn the screen off. */
        }
        cancel_center();
        if (cur < 0) return STOP;
        st.touch_mode = 0;
        select(&g_menu, g_menu.id[cur]);
        whole_row(&g_menu, cur);
        widget_invalidate_force(w, (void *)0);
        st.last_center = now;
        st.center_top = top;
        st.center_surface = w;
        st.center_scope = g_menu.scope;
        st.center_ctx = g_menu.ctx;
        st.center_rows = g_menu.rows;
        st.center_id = g_menu.id[cur];
        row_id_t r = row_id(g_menu.at[cur]);
        st.center_hash = r.one;
        st.center_hash2 = r.two;
        st.center_timer = timer_add(confirm_center, (void *)0, DOUBLE_CLICK_MS);
        st.center_token = st.center_timer;
        if (st.center_timer) {
            prop(top, CONFIRM, (int)st.center_token);
            prop(w, CONFIRM, (int)st.center_token);
            if (g_menu.kind != 2) prop(g_menu.at[cur], CONFIRM, (int)st.center_token);
        } else
            cancel_center(); /* Allocation failure consumes the press without a click. */
        return STOP;
    }
    st.touch_mode = 0;
    if (is_home(top, w)) {
        if (coverflow_wheel_on(w)) return STOP; /* a tick queued as the wheel went raw */
        int speed = ramp(top, w, g_menu.scope, g_menu.ctx, dir, now, CF_FIRST_MS, LIST_RAMP_MS);
        home_step(w, dir, now, speed > 1);
        widget_invalidate_force(w, (void *)0);
        return STOP;
    }
    /* Every accepted tick times the run and takes its place in it, short lists included. */
    int list = g_menu.n; /* row lists: the gentler ramp */
    int step = ramp(top, g_menu.w, g_menu.scope, g_menu.ctx, dir, now,
                    list ? LIST_FIRST_MS : WHEEL_RAMP_MS, list ? LIST_RAMP_MS : WHEEL_RAMP_MS);
    if (g_menu.rows <= SHORT_LIST_MAX) {
        st.wheel_run = 0;
        step = 1;
    }
    native_scrollbar(&g_menu);
    if (g_menu.kind == 3) {
        if (dir > 0)
            slide_menu_scroll_to_next(w);
        else
            slide_menu_scroll_to_prev(w);
    } else if (g_menu.n) {
        int id = widget_get_prop_int(w, SEL, -1);
        /* No letter over a list in an order of its own. Tested here, as at paint the top may
         * already be the page a Return brings back. */
        if (step > 1 && !own_order(widget_get_prop_str(top, "name", "")))
            rearm(&st.letter_timer, letter_expire, LETTER_MS);
        int next = clamp_step(id < 0 ? (cur >= 0 ? g_menu.id[cur] : 0) : id, g_menu.rows - 1,
                              id < 0 ? 0 : dir * step);
        if (next == 0 || next == g_menu.rows - 1) drop_wheel();
        if (next == id) {
            int home = home_menu(top) && g_menu.rows >= 2;
            if (!(home || ring_list(&g_menu)) || id < 0) {
                stop_scroll(&g_menu);
                widget_invalidate_force(w, (void *)0); /* a wheel wake still clears touch hiding */
                return STOP;
            }
            if (!edge_wraps(&g_menu, id, dir, now)) {
                st.edge_id = id;
                st.edge_dir = dir;
                st.edge_time = now;
                fx_arm(w, dir);
                stop_scroll(&g_menu);
                widget_invalidate_force(w, (void *)0);
                return STOP;
            }
            /* A detent after a pause at the bumped end: carry over to the other end of this list.
             */
            st.bump_dir = 0;
            next = dir > 0 ? 0 : g_menu.rows - 1;
        }
        st.edge_id = -1; /* left the end: the next boundary detent bumps again */
        select(&g_menu, next);
        /* Stop momentum even when the selected row already fits the viewport. */
        reveal(&g_menu, next, 1);
        /* Home's pane repaints in this frame, with the row. */
        if (home_menu(top) && index_of(&g_menu, next) >= 0)
            coverflow_home_row(w, g_menu.at[index_of(&g_menu, next)], next, 0);
        if (owned && id >= 0) buzzeer_switch(1); /* one click per row change, however far */
    } else {
        /* no row to select: the view's own end, not the selection's list_end */
        int top = view_top(&g_menu), end = scroll_end(&g_menu, g_menu.height);
        int next = clamp_step(top, end, dir * RING_STEP * step);
        if (next == 0 || next >= end) st.wheel_run = 0;
        wheel_offset(&g_menu, next);
    }
    widget_invalidate_force(w, (void *)0);
    return STOP;
}
