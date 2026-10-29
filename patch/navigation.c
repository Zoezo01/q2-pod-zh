/* Logical menu selection is independent of native touch focus. Stock code owns gestures. */
#include "chapters.h"
#include "offsets.inc"
#include "peq.h" /* libc/libcstl imports (deque_*, send) and the shared helpers */
#include "playback.h"
#include "stock.h"
#undef select /* the UI selection helper below; headset.c uses libc select */
extern int stock_sorted_query_trampoline(const char *, const char *, void *, int);
extern int stock_dbquery_trampoline(const char *, const char *, void *, int),
    stock_content_query_trampoline(const char *, const char *, void *, void *, int);
extern int stock_quicksettings_trampoline(void *, void *);
extern int stock_timezone_trampoline(void);
extern void stock_timezone_row_trampoline(void *, int);

static const char *const tz_names[] = TIMEZONE_NAMES;
#define TZ_COUNT (sizeof tz_names / sizeof *tz_names)

#if IPOD
static void clock_zone(void);
#endif
int ringnav_timezone(void) {
    unsigned n = g_time_zone;
    int result = n < TZ_COUNT ? toolsSetTimeZone(tz_names[n]) : stock_timezone_trampoline();
    tzset(); /* localtime_r does not reload TZ itself */
#if IPOD
    clock_zone();
#endif
    return result;
}

/* Past 2038, 28 years back for stock's 32-bit localtime: the same calendar until 2100. */
static long clock_back(long long wall) {
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
extern int stock_music_info_trampoline(int, char *, const char *);
static int library_music __attribute__((section(".scratch")));
static int spoken(const char *path);

/* Failed exec in a forked child must not flush demo's copied stdio or run its exit handlers. */
__asm__(".globl child_exit\nchild_exit:\nli $v0, 4001\nsyscall\n");

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
    if (from) {
        int n =
            tk_snprintf(filtered, sizeof filtered, "%.*s from (select * from songtable where %s)%s",
                        (int)(from - sql), sql, MUSIC_WHERE, from + 15);
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
    if (content == 2) return stock_sorted_query_trampoline(db, sql, row, (int)arg);
    return content ? stock_content_query_trampoline(db, sql, row, arg, sort)
                   : stock_dbquery_trampoline(db, sql, row, (int)arg);
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
extern int stock_keyup_trampoline(void *, void *), stock_touch_trampoline(void *, void *),
    stock_output_trampoline(int), stock_write_config_trampoline(int, const char *, const char *),
    stock_paint_trampoline(void *, void *), stock_dispatch_trampoline(void *, void *),
    stock_keylong_trampoline(void *, void *), stock_paint_bg_trampoline(void *, void *),
    stock_showface_trampoline(void *), stock_playing_trampoline(void *, void *),
    stock_display_trampoline(void *, void *), stock_bigcover_trampoline(void *, void *),
    stock_localmusic_trampoline(void *, void *), stock_keydown_trampoline(void *, void *),
    stock_btvol_trampoline(int, int), stock_sleep_trampoline(void *),
    stock_color_trampoline(void *, void *, const char *, unsigned),
    stock_image_trampoline(void *, const char *, void *), stock_about_trampoline(void *, void *),
    stock_folder_trampoline(void *, void *), stock_folder_back_trampoline(void *, void *),
    stock_folder_top_trampoline(void), stock_folder_click_trampoline(void *, void *),
    stock_input_trampoline(void *, void *), stock_buzzer_trampoline(int),
    stock_localclass_trampoline(int), stock_detail_trampoline(int, void *),
    stock_artist_trampoline(int), stock_power_trampoline(void *, void *),
    stock_systemset_trampoline(void *, void *), stock_audioset_trampoline(void *, void *),
    stock_btrows_trampoline(void *, void *), stock_playlist_trampoline(void *),
    stock_stream_trampoline(void *, void *), stock_start_player_trampoline(void),
    stock_standby_trampoline(void), stock_usbmode_trampoline(void *, void *, void *, void *);
extern void *coverflow_tracks(void *page);
extern int coverflow_jump(void *w, int from, int dir, unsigned *letter), coverflow_bounded(void *w);
extern void *coverflow_album(void *page), *coverflow_album_tracks(void *r);
extern int coverflow_goto_album(void *r), coverflow_shows_album(void *r);
extern unsigned coverflow_scope(void *page);
extern void coverflow_home_art(void *top);
extern void coverflow_home_layout(void);
extern void coverflow_home_clip(void *w, void *canvas, int begin);
extern int coverflow_home_back(void *top), coverflow_home_owns(void *w);
extern void lockscreen_paint(void *w, void *canvas);
extern void coverflow_paint(void *w, void *canvas), photos_paint(void *w, void *canvas),
    photos_open(const char *root), books_paint(void *w, void *canvas), books_open(const char *root),
    video_poll(void), video_key(unsigned key);
extern int books_key(void *top, unsigned key), video_on(void), books_video(const char *path);
extern void games_paint(void *w, void *canvas);
extern int games_key(void *top, unsigned key), games_open(void *ctx, void *event);
extern void spot_poll(void), spot_yield(void), spot_paint(void *w, void *canvas), *spot_art(void);
extern int spot_media(unsigned key), spot_key(void *top, unsigned key),
    spot_open(void *ctx, void *event), spot_hold(void *top);
extern void up_next(int (*closed)(void *, void *), int (*keyup)(void *, void *)),
    upnext_closed(void *page);
extern int un_hold(void *top);
extern void radio_poll(void);
extern void xfade_row(void *label), wheel_row(void *label), resume_row(void *label),
    mono_row(void *label), swap_row(void *label), mqa_row(void *label);
extern int mono_click(void *ctx, void *event), swap_click(void *ctx, void *event),
    mqa_click(void *ctx, void *event);
extern int xfade_open(void *ctx, void *event), xfade_key(void *top, unsigned key), resume_min(void);
extern int eq_key(void *top, unsigned key); /* peq_ui.c */
extern int radio_media(unsigned key), radio_open(void *ctx, void *event);
extern const char *radio_hold(void *top);
extern void visualizer_paint(void *w, void *canvas), visualizer_attach(void *win);
extern int visualizer_live(void), games_live(void);
extern void *queue_now(unsigned *pos, unsigned *n);
extern const char *now_tag(void *r, int field);
extern int scrobble_ready(void), scrobble_start(void), scrobble_poll(int *sent),
    scrobble_album_artist(void);
extern void scrobble_append(const char *line, unsigned n);
extern void *staged(int (*query)(void *), void *arg, int *count);
/* Scope music detail loads too: spoken tracks can share all their tags with music. */
void *staged_music(int (*query)(void *), void *arg, int *count) {
    int saved = library_music;
    library_music = 1;
    void *out = staged(query, arg, count);
    library_music = saved;
    return out;
}

extern int album_only(void *, void *, int), coverflow_split(void *);
extern unsigned album_id(void *r);
extern unsigned album_dir(const char *, int);
extern volatile unsigned library_gen;
extern const char *track_name(char *buf, unsigned size, void *t);
static int ple_key(unsigned key, int phase);
#define STOP 11
#define GLIDE_MS 300
#define SCROLL_MARGIN 12
#define HOME_FAST_WINDOW_MS 200
#define HOME_SLIDE_MS 200
#define HOME_FAST_SLIDE_MS 120
#define CF_BOUNCE                                                                                  \
    5 /* an artist's last album: a step past kicks it 1/CF_BOUNCE of a stride, springing back */
#define WHEEL_RUN_MS 140
#define WHEEL_RAMP_MS 100 /* scrub, the pixel fallback and Stock: one step for this long, then */
#define WHEEL_MAX_STEP 8  /* one more per this much spin, up to this many */
#define LIST_FIRST_MS 300 /* iPod row lists: one row per step for this long, */
#define LIST_RAMP_MS 200  /* then one row more per this much spin */
#define CF_FIRST_MS 700   /* Coverflow: one cover per step this long before it jumps by letter */
#define SHORT_LIST_MAX 16
#define MAX_ENTRIES 512
#define POS_MEM 64
/* RADIUS, FILL_RGB, FILL_ALPHA, SHADE_ALPHA, OUTLINE_RGB and OUTLINE_ALPHA come from offsets.inc;
 * the colors pack their bytes at compile time (little-endian r,g,b,a). The outline is translucent
 * neutral white, never the playing red, seated on a dark shade of the stock surface so bright
 * album art cannot wash it out. */
#define FILL_COLOR ((FILL_ALPHA << 24) | FILL_RGB)
#define SHADE_COLOR ((SHADE_ALPHA << 24) | FILL_RGB)
#define OUTLINE_COLOR ((OUTLINE_ALPHA << 24) | OUTLINE_RGB)
#define B(p, o) (*(unsigned char *)((char *)(p) + (o)))

typedef struct {
    int ctx, id; /* logical row + 1; 0 = unused */
    unsigned scope, hash, hash2;
} position_t;

/* The settings rows pod_rows builds (pod_values and the Q2POD keys below). */
enum {
    POD_CHARGE,
    POD_LOW,
    POD_WAKE,
    POD_ARTIST_CF,
    POD_YEAR_OLD,
    POD_ARTISTS,
    POD_RESUME,
    POD_LOCKCLOCK,
    POD_ARTIST_AZ,
    POD_VOL_GUARD,
    POD_HOLD_HOME,
    POD_STANDBY,
    POD_BT_LINK,
    POD_CF_SORT,    /* Display's Coverflow Sort: coverflow.c's SORT_FILE, not config.ini */
    POD_CF_REFRESH, /* and Refresh Coverflow, an action */
    POD_WHEEL_QS,   /* Wheel QS, a swipe across the wheel (playback.c) */
    POD_COUNT
};

/* Writable state lives in the zero-filled page the builder maps past the payload text.
 * center_timer defers confirmation; wheel_* scale repeated fast detents; pos_* remember
 * the selected row per audited context across page recreation; reveal_* pin the recall glide so
 * an interruption cannot silently rewrite that memory. */
typedef struct {
    unsigned last_center; /* release time of the pending selection press */
    unsigned center_timer, center_token, center_scope, center_hash, center_hash2;
    unsigned edit_press, edit_at; /* a slider or time editor's pending Centre (edit_key) */
    int center_id, center_ctx, center_rows;
    unsigned cf_letter, cf_letter_at, cf_letter_timer;
    void *cf_letter_surface;
    unsigned last_home;
    int home_dir;
    void *home_surface;   /* non-null also marks a step accepted at time zero */
    void *center_top;     /* top window of that press */
    void *center_surface; /* navigation surface of that press */
    unsigned last_wheel;  /* time of the previous wheel detent */
    int wheel_dir;        /* direction of that detent */
    unsigned wheel_run;   /* continuous same-direction spin ms + 1; capped at full speed */
    int touch_mode;       /* session-wide drawing preference, independent of selection */
    void *wheel_top, *wheel_surface;
    unsigned wheel_scope;
    int wheel_ctx;
    /* iPod: a Return by button ends touch mode once stock has handled it */
    unsigned untouch_timer;
    /* iPod: the row pressed_pill last drew grey, and the repaint once it is released */
    void *press_row;
    unsigned press_timer;
    /* wheel ticks held while a page slides or loads (hold_wheel), replayed into it */
    int held;
    unsigned held_at, held_timer;
    void *held_ctx;
    unsigned held_event[12];
    position_t pos[POS_MEM]; /* most recently selected first; keyed by context and scope */
    void *reveal_surface;    /* surface of the interrupted recall glide, 0 when none */
    int reveal_id;           /* logical row that glide was bringing into view */
    /* Bump repaint and wrap arm; the widget token rejects recycled surfaces. */
    void *fx_surface;
    int fx_token, bump_dir, edge_dir, edge_id;
    unsigned fx_timer, edge_time;
    /* Resume: the saved places, file seconds by path hash, most recent first; the track polled
     * (rs_key, listen_key's, 0 none) and whether it is long, its last seconds, the seconds last
     * saved and how many polls they stood still; rs_pending is the place to restore once it plays,
     * rs_settle a track change one poll old. rs_file is its path's hash, rs_cue and rs_end its CUE
     * start and end, rs_total its seconds; rs_go is the seconds into rs_go_key's chapter a Resume
     * row asked for at rs_go_at. */
    struct {
        unsigned key;
        int sec;
    } spots[RESUME_SLOTS];
    unsigned spots_read, rs_at, rs_key, rs_file, rs_go_key, rs_go_at;
    int rs_long, rs_settle, rs_sec, rs_saved, rs_still, rs_pending, rs_cue, rs_end, rs_total, rs_go;
    int rs_spoken; /* under Podcasts or Audiobooks: resumed at any length, never counted */
    /* Play counts by path hash (plays_read once loaded); the playing track's last second, the
     * seconds of it heard and whether this play has counted. */
    struct {
        unsigned key, n;
    } plays[PLAYS_SLOTS];
    unsigned plays_read, ls_key;
    int ls_sec, ls_heard, ls_done;
    /* Most Played's page and its tracks: the last ranking plus the songs counted since, kept across
     * opens while the library is at mp_gen (~library_gen once stale). */
    void *mp_page, *mp_list, *td_page, *td_list;
    unsigned td_timer;
    int td_asking; /* Tidal Downloads shows Clear All Downloads' confirmation */
    unsigned mp_gen;
    void *sp_page; /* the Library Shuffle menu's page, kept across picks */
    void *ch_page;
    chapters *ch;
    unsigned ch_timer, ch_track, ch_key;
    void *sc_page, *sc_row[2]; /* Streaming's Configuration page and its two cache rows */
/* Streaming's services (svc): five fixed, and at most APPS_MAX card apps. */
#define SVC_FIXED 5
#define SVC_MAX (SVC_FIXED + APPS_MAX)
#define SVC_HIDE 260 /* STREAMHIDE, "key,key" */
#define SVC_PROP "ringnav_svc"
    void *sc_check[SVC_MAX]; /* the Configuration page's switch labels, by service */
    int svc_n;               /* the card apps Streaming listed at its last build */
    /* Ratings: the songs' 1-4 and albums' 1-5 heart tables (calloc'd on first use); Now Playing's
     * window, its heart and the heart's stock click context, the 1-4 badge, the window the picker
     * is up on (0 when down), the hearts picked, the heart's hold timer (rate_held once it fired),
     * and the album rated (album_id, 0 for the playing track); the Ratings menu
     * and the list it opened, with its tracks. */
    unsigned *rates, *album_rates;
    void *rate_win, *rate_fav, *rate_ctx, *rate_badge, *rate_on;
    int rate_shown; /* the badge's hearts plus 1, 0 for a new badge */
    int empty_kind; /* the empty Library list empty_toast names */
    int rate_pick, rate_held;
    unsigned rate_hold, rate_tick, rate_album;
    void *rt_menu, *rt_page, *rt_list;
    int dark; /* 1: the backlight was off at the last UI loop pass; 2: a centre press woke it */
    /* Power's Charge limit and Low power (read once from config.ini's Q2POD), the value
     * labels of those rows and Artists, and the poll: charging is held off at the limit; CPU1 is
     * offline (cpu_off), this boot's first offline is done (cpu_marked), and cpu_bad is 0 before
     * the check, 1 usable, 2 refused by an earlier boot's stall, 3 refused by the kernel;
     * last_input times the screen-on idle. year_old: Year order, oldest first. resume:
     * Bluetooth's Resume on connect on. hold_home: Hold Return goes Home. standby_limit: Standby
     * limit's choice; standby_off: its alarm ended a Standby, so the next pass powers off. */
    int pod_read, charge_limit, low_power, wake_double, wake_pass, wake_eat,
        wake_down, /* 1: the held Centre press passes; 2: it is dropped */
        charge_held, cpu_off, cpu_bad, cpu_marked, artist_cf, year_old, resume, lock_clock,
        artist_az, hold_home, standby_limit, standby_off, wheel_qs;
    void *pod_label[POD_COUNT];
    void *art_cf_win, *art_cf_list; /* the artist page given its Coverflow, the albums pending */
    void *art_cf_veil;              /* the artist page unseen until they show, and its anim_hint */
    char art_cf_hint[64];
    unsigned art_cf_timer;
    unsigned wake_at; /* Wake: Double press, the screen-off centre press a second one must follow */
    unsigned woke_at; /* the centre press that woke the screen, then its first lit loop pass */
    void *np_origin;  /* the page Hold Return opened Now Playing over */
    void *dc_top;     /* the page of the last centre press no list click took, and its time */
    unsigned dc_at;
    unsigned charge_at, last_input, cpu_retry;
    /* the boot logo's poll: last check, card seen mounted, its sync thread, its failure to toast */
    unsigned logo_at;
    int logo_mounted;
    volatile int logo_busy;
    const char *volatile logo_err;
    void *pull_page, *pull_surface;
    int pull_x, pull_y, pull_claimed;
    unsigned pull_scope;
#if IPOD
    void *sel_w; /* the surface whose selection was last drawn */
    int sel_row, sel_y;
    void *paint_w, *sel_widget; /* paint-local color mapping, never stored on recycled widgets */
    int sel_home;
    unsigned clock_key;   /* minute + 1 and format bit; 0 before the first, ~0 for --:-- */
    unsigned clock_timer; /* clock_due, at the next minute; 0 until the bar first paints */
    int clock_full;       /* g_time24h_flag as clock_show last read it */
    /* The status bar's Bluetooth, Wi-Fi and battery widgets, looked up once (the bar is never
     * destroyed); the codec badge shown (index + 1 in BT_CODECS, 0 none), its fade step (0 showing,
     * CODEC_STEPS the glyph fading in, 2 * CODEC_STEPS done) and timer; the battery slot's last
     * level, charge and low state. */
    void *bar_bt, *bar_wifi, *bar_pct, *bar_slot, *bar_icon, *bar_idle, *bar_state, *bar_eq;
    int codec, codec_step, batt_mode;
    unsigned codec_timer, batt_key, bar_timer;
    int pct_digit, pct_sign; /* BATT_PX advances of '0' and '%', measured on the first paint */
    unsigned letter_timer;   /* the fast-scroll letter shows while this runs */
    /* Now Playing's window and payload-filled widgets, and the sources they last showed. */
    void *np_win, *np_pos, *np_album, *np_slider, *np_remain, *np_elapsed, *np_cover;
    void *np_slide, *np_lrc; /* the art/lyrics/info pages and the lyric lines' scroll_view */
    /* The full-screen art's song name, its shade and its window, the fade's step and timer
     * (ringnav_bigcover) */
    void *bc[3];
    int bc_step;
    unsigned bc_timer, bc_track, bc_repaint;
    unsigned bc_key, bc_box, bc_shape,
        bc_url; /* the track, cover and image bc_fit sized for, the box
                 * it left, the image's w << 16 | h and its track */
    /* Volume: the dialog vol_paint last drew, its value and the redraw timer */
    void *vol_dialog;
    int vol_drawn;
    unsigned vol_timer;
    unsigned np_hash; /* of the album text, position and queue length shown, 0 to refresh */
    int np_left;
    /* Minimal's album marquee: np_off px scrolled of the np_over px the album overflows its label.
     */
    int np_off, np_over;
    unsigned np_marquee;
    /* Scrub: a centre press at np_press_at waits DOUBLE_CLICK_MS in np_press for a second one;
     * scrub_to is the target second, scrub_moved set once the wheel changed it. */
    unsigned np_press, np_press_at, scrub_timer, scrub_track, settle_timer;
    int scrub, scrub_to, scrub_moved, settle_played;
    int held_sec; /* a paused seek to scrub_to on scrub_track: the player's second then, +1; 0 none
                   */
    /* The lyrics (lyric_poll), of the song lyric_song, their rows built for lyric_rows_song:
     * lyric_raw while their page holds the wheel's raw travel; lyric_held once the wheel or a drag
     * moved them; lyric_spun once the wheel moved them since it was let go; lyric_touch while a
     * finger is on Now Playing, 1 if it came down on the lyrics, lyric_drag_y the offset then; the
     * glide's target lyric_to (1/16 px), its last step lyric_at, and lyric_glide while it moves;
     * lyric_follow_at the last follow. */
    int lyric_raw, lyric_held, lyric_spun, lyric_touch, lyric_drag_y, lyric_glide, lyric_to;
    unsigned lyric_at, lyric_follow_at, lyric_song, lyric_rows_song;
    /* The Display settings, read from config.ini on first use, and the display page's value labels.
     */
    int settings_read, accent, minimal_accent, home_full, battery, home_rockbox, classic, shortcut,
        rounded, light, light_day; /* light_day: Auto's answer for now (LIGHT=2) */
    void *sel_accent;              /* the row on a coloured selection, for Light Mode's ink */
    int style_depth;               /* nested style hooks (a mutable style's fallback) map nothing */
    void *setting_label[7];
    unsigned tone_key; /* the wheel key whose press ringnav_keydown silenced, 0 when none */
    int greeted;       /* the first reachable list got its boot repaint */
#endif
    /* Queue menu: the hold's AWTK press time marks its release; the target is a track/list row
     * checked by count, record and browsing-state hashes; qm_cls is the class its list holds;
     * qm_forced is a shuffle Play next. */
    unsigned long long qm_press;
    int qs_wheel; /* Quick Settings' wheel, 1 + Q2POD QSVOLUME once read (quick_volume) */
    unsigned qm_timer, qm_cls, qm_idx, qm_rows, qm_hash, qm_browse, qm_forced, qm_forced_hash;
    int qm_kind, qm_action, qm_choice;
    void *qm_dialog, *qm_queue, *qm_playlist;
    unsigned qm_queue_hash;
    unsigned char qm_classinfo[912]; /* g_local_classinfo_save before a Go to */
    /* Podcasts/Audiobooks: folder_page opens at media_root once media_open is set (else at the
     * card, /mnt/mmc, with a card and no drive; otherwise the storage selector stays); while the
     * page is there, Back at that root leaves it. */
    int media_open;
    char media_root[32];
    /* Go to Folder: once set, folder_page opens at go_folder at g_folder_layer go_layer. */
    int go_layer;
    char go_folder[1024]; /* g_folder_path's size */
    void *root_label;     /* Playback settings' Folder Root row */
    void *album;     /* what an Albums row or Go to album opened (album_open), a one-record deque */
    char **art_keys; /* an artist's albums tab's art keys by row, 0 for a name's own
                        (ringnav_artist_list) */
    unsigned *art_ids; /* and its rows' album_id, 0 for an album not found */
    unsigned art_n;
} scratch_t;
static scratch_t st __attribute__((section(".scratch")));
static void rate_bind(void *win); /* ratings: both builds' Now Playing heart */

typedef struct {
    int x, y, w, h;
} rect_t;
typedef struct {
    void *w, *at[MAX_ENTRIES];
    int id[MAX_ENTRIES], n, kind, rows, row, height, ctx;
    unsigned scope;
} menu_t;
/* Single shared view: no entry point keeps a menu live across a nested load(). */
static menu_t g_menu __attribute__((section(".scratch")));

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
 * (album_page, but its Albums list below), dynamic pages and the home carousel
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

static int clamp_step(int offset, int maximum, int delta) {
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

/* A tap target has an EVT_CLICK handler. V1.32 widget emitter @0x60; emitter_on_with_tag items are
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
#define SEL "_ringnav_index"
#define TOUCH "_ringnav_touch"
#define COUNT "_ringnav_count"
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
static void drop_input(void) {
    cancel_center();
    drop_spin();
}

/* Home and Coverflow step their slide_menu with one retargeted animator (home_step): stock
 * scroll_to (0x5f3400) starts a new animator per call and orphans the running one, so fast
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

#if IPOD
/* A list row: a list_item or table_row. */
static int is_row(void *w) {
    const char *t = w ? widget_get_type(w) : "";
    return !tk_strcmp(t, "list_item") || !tk_strcmp(t, "table_row");
}

/* A widget's own window: the top-level widget it sits in. */
static void *window_of(void *w) {
    void *wm = window_manager();
    while (w && P(w, W_PARENT) != wm) w = P(w, W_PARENT);
    return w;
}

/* A page that opens and closes with stock's slide (tools/ipod.py SLIDE). Stock paints it and
 * the page under it into the animator's snapshots while it is the top window and before
 * window_manager_is_animating is set; nothing else paints the page under it. */
static int slides(void *win) {
    const char *hint = win ? widget_get_prop_str(win, "anim_hint", (void *)0) : (void *)0;
    return hint && *hint;
}
#else
#define slides(win) 0
#endif

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
    st.cf_letter = letter;
    st.cf_letter_at = now;
    st.cf_letter_surface = w;
    if (letter)
        rearm(&st.cf_letter_timer, cf_letter_expire, LETTER_MS);
    else
        stop_timer(&st.cf_letter_timer);
    if (!a) {
        a = widget_animator_scroll_create(w, HOME_SLIDE_MS, 0, SLIDE_EASING);
        if (a && !widget_animator_on(a, EVT_ANIM_END, home_done, w)) {
            widget_animator_destroy(a);
            a = (void *)0;
        }
        P(w, SLIDE_ANIMATOR) = a;
        if (!a) {
            int id = (I(w, SLIDE_INDEX) - goal / stride) % n;
            I(w, SLIDE_OFFSET) = 0;
            slide_menu_set_value(w, id < 0 ? id + n : id);
            widget_set_focused(widget_get_child(w, I(w, SLIDE_INDEX)), 1);
            st.home_surface = (void *)0;
            return;
        }
        widget_set_focused(widget_get_child(w, I(w, SLIDE_INDEX)), 0);
    }
    widget_animator_pause(a);
    widget_animator_scroll_set_params(a, live, 0, goal, 0);
    I(a, ANIM_ELAPSED) = I(a, ANIM_START_TIME) = 0;
    I(a, ANIM_DURATION) = fast ? HOME_FAST_SLIDE_MS : HOME_SLIDE_MS;
    widget_animator_start(a);
    st.home_surface = w;
    st.last_home = now;
    st.home_dir = dir;
}

static int usable(void) {
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
    if (!IPOD && top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "confirminfo_dialog"))
        return usb_prompt();
    return top && context_id(widget_get_prop_str(top, "name", (void *)0)) >= 0;
}

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

static void prop(void *w, const char *name, int value) {
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
static void reveal(menu_t *m, int id, int immediate);

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

static int load_rows(menu_t *m, void *w) {
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
    if (recall && m->kind != 3 && count < 0) {
        /* A picker opens on its current option rather than the row last chosen. */
        void *top = window_manager_get_top_window(window_manager());
        int ctx = top ? context_id(widget_get_prop_str(top, "name", (void *)0)) : -1;
        int choice = ctx >= 0 && (contexts[ctx].flags & CHOICE) ? checked_row(m) : -1;
        int p = choice >= 0 ? -1 : position(m);
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

/* Largest viewport top that still shows content; the clamp bound for every glide. */
static int max_top(menu_t *m) {
    if (m->kind == 2) return m->rows * m->row - m->height;
    return m->kind == 1 ? I(m->w, VIEW_CONTENT_H) - m->height : 0;
}

static rect_t bounds(menu_t *m, int i) {
    void *e = m->at[i];
    rect_t r = { 0, 0, I(e, W_W), I(e, W_H) };
    /* Speed images have 61px hit targets, but their visible buttons are only 24px high. */
    if (!tk_strcmp(widget_get_type(e), "image") &&
        !tk_strcmp(widget_get_prop_str(P(e, W_PARENT), "style", ""), "s_btn_forwardspeed") &&
        r.h > 24)
        r.h = 24;
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
static void reveal(menu_t *m, int id, int immediate) {
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
    int margin = clamp_step((m->height - h) / 2, SCROLL_MARGIN, 0);
    int want = h > m->height || y - top < margin  ? y - margin
               : y - top > m->height - h - margin ? y - (m->height - h - margin)
                                                  : top;
    want = clamp_step(want, max_top(m), 0);
    if (immediate) {
        wheel_offset(m, want);
        return;
    }
    if (want == top) return;
    /* Stock scroll views cannot retarget an animator whose old goal is a boundary. */
    stop_scroll(m);
    if (m->kind == 2) {
        table_client_scroll_to(m->w, want);
    } else
        scroll_view_scroll_delta_to(m->w, 0, want - top, GLIDE_MS);
}

/* A payload page's own row: establish its scope and viewport before paint can recall another. */
void ringnav_select(void *w, int id, int rows) {
    int loaded = load(&g_menu, w, 0);
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
    int valid = w && !window_manager_get_pointer_pressed(window_manager()) && !g_power_longkey &&
                !g_ingore_bootkey_flag && !*(volatile unsigned char *)BOOT_KEY_GUARD &&
                load_rows(&g_menu, w);
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
            if (!IPOD || !tk_strcmp(widget_get_prop_str(page, "name", ""), "radio_page"))
                radio_search();
            else
                stock_search(page, event);
        }
        return STOP;
    }
    void *prompt = widget_lookup(page, PULL_PROMPT, 1);
    if (!prompt) {
        prompt = label_create(page, 0, 0, I(page, W_W), 44);
        if (prompt) {
            widget_set_name(prompt, PULL_PROMPT);
            widget_use_style(prompt, "s_label_white18c");
            widget_set_enable(prompt, 0);
            widget_set_sensitive(prompt, 0);
            prop(prompt, "floating", 1);
            prop(prompt, "style:disable:bg_color", (int)0xff000000u);
            prop(prompt, "style:disable:text_color", (int)0xffffffffu);
        }
    }
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
    /* Local Songs' stock search is iPod's; Internet Radio's search is its own, in both. */
    const char *name = widget_get_prop_str(page, "name", "");
    if (tk_strcmp(name, "radio_page") && (!IPOD || tk_strcmp(name, "localmusic_page"))) return;
    void *w = surface((void *)0, (void *)0);
    if (!w) return;
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

/* A one-digit setting under section in the stock config.ini, 0 to n - 1, else def (a missing key
 * too): toolsReadConfig(path, section, key, out, default) copies the value, or the default. */
int config_value(const char *section, const char *key, int n, int def) {
    char s[256] = "", d[2] = { (char)('0' + def), 0 };
    toolsReadConfig("/mnt/data/config.ini", section, key, s, d);
    return s[0] >= '0' && s[0] < '0' + n && !s[1] ? s[0] - '0' : def;
}

#define ROCKBOX_BIN "/mnt/mmc/.rockbox/rockbox"
/* glibc 2.28 MIPS o32 stat v3: st_mode at byte 20, as stock's fs stat reads it. */
int rockbox_available(void) {
    unsigned info[36];
    return !__xstat(3, ROCKBOX_BIN, info) && (info[5] & 0170000) == 0100000;
}

/* 0xRRGGBB to an opaque color_t, whose bytes are r, g, b, a. */
#define RGBA(c) (0xff000000u | ((c) & 255) << 16 | ((c) & 0xff00) | (c) >> 16)

#if IPOD
/* Light Mode (docs/ipod.md#light-mode): a neutral colour, its channels within LIGHT_SPREAD, flipped
 * per channel from black to LIGHT_PAPER and white to LIGHT_INK; any other hue, and the alpha byte,
 * as they are. Either byte order (color_t or 0xRRGGBB): the paper and ink are grey. */
static int neutral(unsigned c) {
    unsigned hi = 0, lo = 255;
    for (int s = 0; s < 24; s += 8) {
        unsigned v = c >> s & 255;
        hi = v > hi ? v : hi;
        lo = v < lo ? v : lo;
    }
    return hi - lo <= LIGHT_SPREAD;
}
static unsigned flip_v(unsigned v) { return LIGHT_PAPER - v * (LIGHT_PAPER - LIGHT_INK) / 255; }
static unsigned flip(unsigned c) {
    if (!neutral(c)) return c;
    unsigned out = c & 0xff000000u;
    for (int s = 0; s < 24; s += 8) out |= flip_v(c >> s & 255) << s;
    return out;
}
static unsigned flip_if(int on, unsigned c) { return on ? flip(c) : c; }

/* Every colour the payload draws passes here (peq.h wraps the canvas setters), as stock's pass
 * the style and image hooks: flipped in Light Mode. A clear colour stays 0: stock tests for it
 * (Now Playing's slider draws no dragger under a #00000000 border). */
unsigned ui_ink(unsigned c) { return ipod_light() && c >> 24 ? flip(c) : c; }
#else
unsigned ui_ink(unsigned c) { return c; }
#endif

#if IPOD

/* The 0xRRGGBB j/n of the way from one color to another, per channel. Shared with visualizer.c. */
unsigned mix(unsigned from, unsigned to, int j, int n) {
    unsigned c = 0;
    for (int s = 0; s < 24; s += 8)
        c |= (unsigned)(((int)(from >> s & 255) * (n - j) + (int)(to >> s & 255) * j) / n) << s;
    return c;
}

/* The Accent, Home, Battery and Theme settings; each theme keeps its own accent
 * (docs/ipod.md#display-settings). */
static const unsigned accents[][5] = { ACCENTS };
#define ACCENT_N (int)(sizeof accents / sizeof *accents)
static const char *const accent_names[] = {
    "Accent: Graphite",  "Accent: Crimson", "Accent: Tidal", "Accent: Champagne", "Accent: Cobalt",
    "Accent: Tangerine", "Accent: Plum",    "Accent: Ivory", "Accent: Green"
};
_Static_assert(sizeof accent_names / sizeof *accent_names == ACCENT_N, "one name per ACCENTS row");
/* The Display rows' settings, by ringnav_display's index: 0 Accent, 1 Home, 2 Battery, 3 Theme,
 * 4 Shortcut, 5 Corners, 6 Light Mode. */
static const char *const setting_keys[] = { "ACCENT",   "HOME",    "BATTERY", "THEME",
                                            "SHORTCUT", "ROUNDED", "LIGHT" };
static const int setting_counts[] = { ACCENT_N, 2, 4, 2, 7, 2, 3 };
static int *const setting_values[] = { &st.accent,   &st.home_full, &st.battery, &st.classic,
                                       &st.shortcut, &st.rounded,   &st.light };
/* The local time now into buf (struct tm: tm_sec @0, tm_min @4, tm_hour @8), or 0 when the clock
 * cannot be read. */
static const int *clock_now(int *buf) {
    long long wall = wall_time();
    long now = clock_back(wall);
    return wall == -1 ? (void *)0 : localtime_r(&now, buf);
}
/* Light Mode's Auto (LIGHT=2) is light from 7:00 to 19:00 by the clock. clock_show applies it each
 * minute: when Auto crosses either time it drops the cached images (ringnav_image_add bakes the
 * mode into them) and repaints, as setting_click does. */
#define LIGHT_FROM 7
#define LIGHT_UNTIL 19
static int light_day(const int *tm) { return tm && tm[2] >= LIGHT_FROM && tm[2] < LIGHT_UNTIL; }
static void np_fill(int scrub);
static void light_apply(const int *tm) {
    if (st.light != 2 || light_day(tm) == st.light_day) return;
    st.light_day = !st.light_day;
    np_fill(0);
    image_manager_unload_all(image_manager());
    widget_invalidate_force(window_manager(), (void *)0);
}
static int accent(void) {
    if (!st.settings_read) {
        for (int i = 0; i < 4; ++i)
            *setting_values[i] = config_value("IPOD", setting_keys[i], setting_counts[i], 0);
        if (st.accent == MINIMAL_ACCENT) /* Classic has no Ivory: Graphite */
            st.accent = 0;
        st.shortcut = config_value("IPOD", "SHORTCUT", 7, 7); /* 7: no key, see shortcut() */
        st.minimal_accent = config_value("IPOD", "MINIMAL_ACCENT", ACCENT_N, MINIMAL_ACCENT);
        st.rounded = config_value("IPOD", "ROUNDED", 2, 0); /* Square unless chosen */
        st.light = config_value("IPOD", "LIGHT", 3, 0);     /* dark unless chosen; 2 Auto */
        int buf[11];
        st.light_day = light_day(clock_now(buf));
        st.settings_read = 1;
    }
    return st.classic ? st.accent : st.minimal_accent;
}
/* 1 while Minimal draws its own accent: the off-white card with dark ink. */
static int minimal_card(void) { return !ipod_classic() && st.minimal_accent == MINIMAL_ACCENT; }
/* Shortcut with no key is Rockbox while the card has it, else Favorites: asked each time, as the
 * card may mount after the first read. */
static int shortcut(void) {
    accent();
    return st.shortcut < 7 ? st.shortcut : rockbox_available() ? 0 : 3;
}
int ipod_classic(void) {
    accent();
    return st.classic;
}
int ipod_home_full(void) {
    accent();
    return st.home_full;
}
int ipod_light(void) {
    accent();
    return st.light == 2 ? st.light_day : st.light;
}
int ipod_battery(void) {
    accent();
    return st.battery;
} /* lockscreen.c's Large clock */
/* 1 when Home shows its Rockbox row (coverflow_home_layout): the card has Rockbox. */
int ipod_home_rockbox(void) {
    st.home_rockbox = rockbox_available();
    return shortcut() || st.home_rockbox;
}
const char *ipod_home_shortcut(void) {
    static const char *const names[] = { "Rockbox",      "Artists", "Albums", "Favorites",
                                         "Rated Albums", "Shuffle", "Games" };
    return names[shortcut()];
}
extern int rockbox_open(void *, void *);
static int rated_albums(void *, void *), shuffle_menu(void *, void *);
int home_shortcut_open(void *ctx, void *event) {
    int s = shortcut();
    if (!s) return rockbox_open(ctx, event);
    if (s == 4) return rated_albums(ctx, event);
    if (s == 5) return shuffle_menu(ctx, event);
    if (s == 6) return games_open(ctx, event);
    if (s == 2) {
        struct {
            int cls;
            void *record;
        } album = { 0xf003, 0 };
        return navigator_to_with_context("localmusic/album_page", &album);
    }
    return navigator_to_with_context(s == 1 ? "localmusic/localclass_page"
                                            : "localmusic/allmusic_page",
                                     (void *)(long)(s == 1 ? 0xf004 : 0xf00a));
}
enum { TONE_LIGHT = 2, TONE_RED = 3 }; /* columns of ACCENTS */
/* Light Mode keeps a coloured accent's tones; a neutral one (Graphite, Ivory) takes its silver red
 * tone for both, which flips dark. Before flip. */
static unsigned tone_of(const unsigned *a, int tone) {
    return ipod_light() && neutral(a[0]) ? a[TONE_RED] : a[tone];
}
unsigned accent_tone(int tone) {
    return tone == TONE_LIGHT && minimal_card() ? MINIMAL_FILL : tone_of(accents[accent()], tone);
}

/* A color_t (bytes r, g, b, a) that is stock red blended with a neutral, t * red + k * white per
 * channel within RED_TOLERANCE, becomes the same blend of one of the preset's tones (TONE_RED for
 * text and images, TONE_LIGHT for every other color): flat reds,
 * pressed tints and anti-aliased edges follow the accent, alpha is kept, and greys and other hues
 * are unchanged. t comes from least squares against red with the mean removed, in 1/4096. The white
 * part (k) becomes that share of glyph: white keeps a blend's light part as it is. With another
 * glyph, neutral pixels (the glyph itself) map too, so pass it only for an image that holds red.
 * Crimson is the identity. Light Mode (light) puts the red share over paper instead: a tint over
 * black becomes the same tint of tone over LIGHT_PAPER, its grey part flipped. */
static unsigned red_map(unsigned c, unsigned tone, unsigned glyph, int light) {
    static const int red[3] = { STOCK_RED >> 16, STOCK_RED >> 8 & 255, STOCK_RED & 255 };
    const int sum = red[0] + red[1] + red[2];
    int ch[3] = { c & 255, c >> 8 & 255, c >> 16 & 255 }, s = ch[0] + ch[1] + ch[2], d = 0, dd = 0;
    for (int i = 0; i < 3; ++i) {
        d += (3 * ch[i] - s) * (3 * red[i] - sum);
        dd += (3 * red[i] - sum) * (3 * red[i] - sum);
    }
    int t = d * 4096 / dd;
    if (t < 256 && glyph != 0xffffff) t = 0; /* a neutral glyph pixel takes the glyph colour too */
    int k = (s * 4096 - t * sum) / (3 * 4096);
    if ((t && t < 256) || t > 4096 + 256 || k < -RED_TOLERANCE) return c;
    unsigned out = c & 0xff000000u;
    for (int i = 0; i < 3; ++i) {
        int miss = ch[i] - (t * red[i] / 4096 + k);
        if (miss > RED_TOLERANCE || miss < -RED_TOLERANCE) return c;
        int v = t * (int)(tone >> (16 - 8 * i) & 255) / 4096 +
                (light ? (t < 4096 ? 4096 - t : 0) * LIGHT_PAPER / 4096 -
                             k * (LIGHT_PAPER - LIGHT_INK) / 255
                       : k * (int)(glyph >> (16 - 8 * i) & 255) / 255);
        out |= (unsigned)(v < 0 ? 0 : v > 255 ? 255 : v) << 8 * i;
    }
    return out;
}
unsigned accent_map(unsigned c, int preset, int tone) {
    if (ipod_light())
        return !(c >> 24) || neutral(c)
                   ? ui_ink(c)
                   : red_map(c, flip(tone_of(accents[preset], tone)), 0xffffff, 1);
    return preset == CRIMSON ? c : red_map(c, accents[preset][tone], 0xffffff, 0);
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
        if (tk_strcmp(widget_get_type(c), "image") ||
            widget_get_prop_int(c, "draw_type", 0) == IMAGE_DRAW_CENTER) /* icons: discs */
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
#endif

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
void draw_centred(void *canvas, const unsigned *s, unsigned n, const void *r, unsigned px,
                  unsigned color) {
    unsigned text = (unsigned)I(P(canvas, CANVAS_LCD), LCD_TEXT_COLOR);
    int align_v = I(canvas, CANVAS_ALIGN_V), align_h = I(canvas, CANVAS_ALIGN_H);
    canvas_set_font(canvas, (void *)0, px); /* the system default font */
    canvas_set_text_color(canvas, color);
    I(canvas, CANVAS_ALIGN_V) = I(canvas, CANVAS_ALIGN_H) = 1;
    canvas_draw_text_in_rect(canvas, s, n, r);
    I(canvas, CANVAS_ALIGN_V) = align_v;
    I(canvas, CANVAS_ALIGN_H) = align_h;
    stock_canvas_set_text_color(canvas, text);
}

/* A rounded box in color, or a square one when the canvas declines to round it (no vgcanvas, or a
 * radius under 3, which stock zeroes and then fails). */
static void fill_box(void *canvas, rect_t *r, unsigned color, unsigned radius) {
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

#if IPOD
/* A widget in a DRILL window (contexts.inc). Its own window decides, not the top one, so a window
 * painted during a transition keeps its own rows. */
static int drill(void *w) {
    if (more_image(w)) return 0; /* stock's More hit test owns the trailing area */
    w = window_of(w);
    int ctx = w ? context_id(widget_get_prop_str(w, "name", (void *)0)) : -1;
    return ctx >= 0 && (contexts[ctx].flags & DRILL);
}

/* The iPod `>` of each drill row, where stock rows place img_into, as stock hides its own
 * list_into: not in multi-select, and not on grid tiles. Home has none, except Classic's, which
 * rides its selection bar alone, nudge included. The image manager caches the bitmap. */
static void paint_chevrons(void *w, void *canvas) {
    unsigned bitmap[64], white[64]; /* bitmap_t */
    rect_t old;
    void *win = window_of(w);
    int home = home_menu(win);
    if (g_navbar_status || !kind(w) || !P(canvas, CANVAS_LCD) || !drill(w) ||
        (home && (!ipod_classic() || st.sel_w != w)) || !load_rows(&g_menu, w) ||
        widget_load_image(w, "list_into", bitmap) || !clip_surface(canvas, &g_menu, &old))
        return;
    /* Light Mode: the row on a coloured bar or card keeps stock's white `>`, as an iPod's blue bar
     * does; by path, the image hook leaves it unflipped. */
    int lit = ipod_light() && st.sel_accent && st.sel_w == w &&
              !widget_load_image(w, LIST_INTO_WHITE, white);
    for (int i = 0; i < g_menu.n; ++i) {
        rect_t r = bounds(&g_menu, i);
        if (2 * r.w >= I(w, W_W) && drill(g_menu.at[i]) && (!home || i == st.sel_row))
            canvas_draw_icon(
                canvas, lit && i == st.sel_row ? white : bitmap,
                r.x + r.w - CHEVRON_W + (int)bitmap[0] / 2 +
                    (home ? (ipod_home_full() ? HOME_FULL_CHEVRON_SHIFT : HOME_CHEVRON_SHIFT) : 0),
                home ? st.sel_y : r.y + r.h / 2); /* bitmap_t width @0 */
    }
    canvas_set_clip_rect(canvas, &old);
}

/* Writes v (under 1000) in decimal to s; returns the digits written. */
static unsigned put_num(unsigned *s, unsigned v) {
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

/* iPod fast scroll: while the wheel ramp moves more than one row per step, the selected row's
 * first character sits in a dark translucent square over the list until LETTER_MS after the last
 * such step. A virtual table resolves the logical row in its recycled pool; an offscreen or
 * textless row shows nothing. The fill color and clip are restored. */
static void paint_letter(void *w, void *canvas) {
    rect_t old;
    if (!st.letter_timer || w != st.wheel_surface || st.wheel_run <= LIST_FIRST_MS ||
        st.touch_mode || !P(canvas, CANVAS_LCD) || !load_rows(&g_menu, w) ||
        (g_menu.ctx >= 0 &&
         contexts[g_menu.ctx].kind == CTX_FIXED)) /* menus are not alphabetical */
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
    fill_box(canvas, &box, (LETTER_ALPHA << 24) | FILL_RGB, LETTER_RADIUS);
    draw_centred(canvas, &c, 1, &box, LETTER_PX, 0xffffffff);
    stock_canvas_set_fill_color(canvas, fill);
    canvas_set_clip_rect(canvas, &old);
}

/* Playing art is rounded after scaling, so opposite corners share the same pixel grid.
 * NP_ART_RADIUS at 166px, painted over it in the page's
 * black: each corner row outside the arc, then its edge pixel at the alpha of its uncovered part
 * (1/16 px). */
static void paint_cover(void *w, void *canvas) {
    if (!w || !P(canvas, CANVAS_LCD)) return;
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
#endif

#if IPOD
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
#define MARQUEE_HOLD_MS 1500
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
    if (!w || ipod_classic() || !P(canvas, CANVAS_LCD)) return;
    if (w == st.bar_idle) {
        draw_spaced(canvas, widget_get_text(w), 0, (I(w, W_H) - MARK_PX) / 2, MARK_PX,
                    RGBA(0xaaaaaa), 375);
        return;
    }
    if (st.np_album && w == P(st.np_album, W_PARENT)) {
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
    if (tk_strcmp(widget_get_type(w), "hscroll_label") || !coverflow_home_owns(w)) return;
    unsigned color = RGBA(SUDO_MUTED);
    for (void *p = w; p; p = P(p, W_PARENT))
        if (p == st.sel_widget) color = 0xffffffff;
    draw_spaced(canvas, widget_get_text(w), 0, (I(w, W_H) - HOME_CAPS_PX) / 2, HOME_CAPS_PX, color,
                I(w, W_W));
}
#endif

/* Load and settle the painted surface's selection, then draw it: a neutral outline over the rows
 * in Stock, an off-white bar behind them in iPod (a dot on Home), or Classic's full-width accent
 * bar. The outline is one neutral white line seated on a dark shade line: the shade is the stock
 * dark surface at an alpha high enough to hold the white over bright album art, and being the same
 * color as the dark rows it vanishes on the stock theme. The translucent fill keeps the row
 * readable over artwork without borrowing the red "playing" language or the native focus flag.
 * Small rows and degenerate geometry keep the square fallback. */
static void paint_selection(void *w, void *canvas) {
#if IPOD
    if (kind(w)) st.sel_w = st.sel_widget = st.sel_accent = (void *)0;
#endif
    if (!w || !canvas || !kind(w)) return;
    void *top = window_manager_get_top_window(window_manager());
    int i, shown = !st.touch_mode;
#if IPOD
    void *own = window_of(w);
    if (own && own != top && slides(top)) {
        /* The page under a sliding one, painted into the animator's snapshot: draw the row it
         * holds. Loading, recall and settling read the top page's state, so none of it runs.
         * A pending untouch is a Return by button, which shows the row of the page behind. */
        if (!usable() || !load_rows(&g_menu, w)) return;
        i = index_of(&g_menu, widget_get_prop_int(w, SEL, -1));
        top = own;
        if (st.untouch_timer) shown = 1;
    } else
#endif
    {
        if (surface((void *)0, (void *)0) != w) return;
        if (!load(&g_menu, w, !window_manager_get_pointer_pressed(window_manager()))) {
            cancel_center();
            return;
        }
        if (st.center_timer && !pending_matches(top, &g_menu)) cancel_center();
        if (g_menu.kind == 3) return; /* Home shows its selected card. */
        i = reconcile(&g_menu,
                      !moving(&g_menu) && !window_manager_get_pointer_pressed(window_manager()));
    }
    /* Touch hides the selection until the wheel or a button, except on Home (iPod's list) and a
     * pop-up's buttons, which open on their first (Cancel). */
    int home = home_menu(top);
    if (i < 0 || (!shown && !home && g_menu.kind != 4)) return;
    rect_t r = bounds(&g_menu, i), old;
    /* A boundary detent nudges the selection against the end until it springs back. */
    if (fx_live(w) && st.bump_dir) r.y -= st.bump_dir * BUMP_PX;
    if (r.w < 5 || r.h < 5 || !P(canvas, CANVAS_LCD)) return;
    void *lcd = P(canvas, CANVAS_LCD);
    unsigned fill_color = (unsigned)I(lcd, LCD_FILL_COLOR);
    unsigned stroke_color = (unsigned)I(lcd, LCD_STROKE_COLOR);
    if (!clip_surface(canvas, &g_menu, &old)) return;
#if IPOD
    /* A list row gets the full surface width; a grid tile keeps its own rect. */
    int tile = 2 * r.w < I(g_menu.w, W_W);
    if (!tile) {
        r.x = 0;
        r.w = I(g_menu.w, W_W);
        /* The Library's row is a button over the top BODY of its pitch: the bar takes the whole
         * row, or the last row's leaves a strip at the screen's bottom. */
        void *row = P(g_menu.at[i], W_PARENT);
        if (!I(g_menu.at[i], W_Y) && is_row(row) && I(row, W_H) > r.h) r.h = I(row, W_H);
    }
    /* Classic: the accent's bar under the rows' own ink. Minimal: its card (sel_widget), off-white
     * with dark ink under its own accent, else the accent's under the rows' own ink. */
    int classic = ipod_classic();
    st.sel_widget = classic ? (void *)0 : home ? P(g_menu.at[i], W_PARENT) : g_menu.at[i];
    st.sel_home = home && !classic;
    /* Light Mode: ink on a coloured bar or card stays white (ringnav_style_color). */
    st.sel_accent = !neutral(accents[accent()][0]) && !st.sel_home && !minimal_card()
                        ? (home ? P(g_menu.at[i], W_PARENT) : g_menu.at[i])
                        : (void *)0;
    if (classic && (!tile || g_menu.kind == 4)) {
        const unsigned *a = accents[accent()];
        /* Corners: Rounded makes a list row's bar a pill, inset like Minimal's card */
        rect_t bar = r;
        int pill = !tile && st.rounded ? pill_radius(g_menu.at[i], r.h, SEL_INSET) : 0;
        if (pill) bar.x += SEL_INSET, bar.w -= 2 * SEL_INSET;
        gradient(canvas, bar, a[0], a[1], a[4], pill);
    } else if (home) {
        rect_t dot = { HOME_DOT_X, r.y + (r.h - HOME_DOT) / 2, HOME_DOT, HOME_DOT };
        fill_box(canvas, &dot, 0xffffffff, HOME_DOT / 2);
    } else { /* Sudo's card: inset from the glass's edges, its corners rounded */
        int grid = tile && g_menu.kind != 4; /* pop-up buttons keep their row inset */
        int square = !tile && !st.rounded;   /* Corners: Square, a full-width bar as Classic's */
        int in = grid ? TILE_INSET : square ? 0 : SEL_INSET, vertical = grid ? TILE_INSET : 0;
        rect_t card = { r.x + in, r.y + vertical, r.w - 2 * in, r.h - 2 * vertical };
        int radius = !st.rounded ? 0 : tile ? SEL_RADIUS : pill_radius(g_menu.at[i], card.h, in);
        fill_box(canvas, &card, RGBA(minimal_card() ? MINIMAL_FILL : accents[accent()][0]), radius);
    }
    st.sel_w = w;
    st.sel_row = i;
    st.sel_y = r.y + r.h / 2;
    if (g_menu.kind == 4 &&
        r.w < I(g_menu.w, W_W)) /* a pop-up button's tile: framed white on any accent */
        frame_box(canvas, &r, 0xffffffff, SEL_RADIUS);
#else
    rect_t outer = { r.x + 1, r.y + 1, r.w - 2, r.h - 2 };
    rect_t inner = { outer.x + 1, outer.y + 1, outer.w - 2, outer.h - 2 };
    int drawn = 0;
    /* Two concentric one-pixel rounded strokes, shade outside and white inside, not one
     * border_width=2 call: the effect then does not depend on how a canvas backend
     * interprets the width argument. Geometry is checked before the call, and radius 9/8
     * both stay above the stock "square at <= 2" cutoff. The stock rounded stroke returns
     * non-zero when its backend cannot draw (for example a canvas without a vgcanvas), and
     * the safe square fallback then keeps the outline visible. */
    if (outer.w > 2 * RADIUS && outer.h > 2 * RADIUS) {
        unsigned fill = FILL_COLOR;
        canvas_fill_rounded_rect(canvas, &outer, (void *)0, &fill, RADIUS);
        unsigned shade = SHADE_COLOR;
        unsigned white = OUTLINE_COLOR;
        drawn = canvas_stroke_rounded_rect(canvas, &outer, (void *)0, &shade, RADIUS, 1) == 0;
        if (drawn)
            drawn =
                canvas_stroke_rounded_rect(canvas, &inner, (void *)0, &white, RADIUS - 1, 1) == 0;
    }
    if (!drawn) {
        canvas_set_stroke_color(canvas, SHADE_COLOR);
        canvas_stroke_rect(canvas, outer.x, outer.y, outer.w, outer.h);
        canvas_set_stroke_color(canvas, OUTLINE_COLOR);
        canvas_stroke_rect(canvas, inner.x, inner.y, inner.w, inner.h);
    }
#endif
    /* Save/restore explicitly: this firmware's canvas_save/restore cover neither clip nor
     * either color, and the global alpha is deliberately never touched. */
    stock_canvas_set_fill_color(canvas, fill_color);
    stock_canvas_set_stroke_color(canvas, stroke_color);
    canvas_set_clip_rect(canvas, &old);
}

static void edit_paint(void *w, void *canvas);

#if IPOD
/* Minimal's own accent: stock's white radio images (select, unselect: a 26 px ring, a 12 px dot
 * when checked) vanish on the selected row's off-white card, so they are drawn again over it in the
 * card's ink. So does the Wi-Fi, Bluetooth and Tidal rows' navbar_delete, drawn again as
 * list_delete, its copy in list_into's grey; navbar_delete itself stays white for the playlist
 * page's Delete.
 */
static void paint_radio(void *w, void *canvas) {
    if (!w || !st.sel_widget || st.sel_home || !minimal_card() || !P(canvas, CANVAS_LCD)) return;
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
        fill_box(canvas, &clear, RGBA(MINIMAL_FILL), 0);
        canvas_draw_icon(canvas, bitmap, x, y);
        stock_canvas_set_fill_color(canvas, fill);
        return;
    }
    if (more) {
        /* Stock's 4px dots, 7px apart, centred in the 50px image. */
        rect_t clear = { x - 13, y - 4, 26, 8 };
        fill_box(canvas, &clear, RGBA(MINIMAL_FILL), 0);
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
    fill_box(canvas, &hole, RGBA(MINIMAL_FILL), 11);
    if (dot) fill_box(canvas, &in, RGBA(0x171717), 6);
    stock_canvas_set_fill_color(canvas, fill);
}

static int painting_in(void *row);
/* Light Mode: stock's own `>` (a list_into image) on the coloured bar or card's row, drawn again
 * white over the selection's colour, as paint_chevrons draws the payload's. */
static void paint_into(void *w, void *canvas) {
    unsigned bitmap[64]; /* bitmap_t: width @0, height @4 */
    if (!w || !ipod_light() || !st.sel_accent || !P(canvas, CANVAS_LCD) ||
        tk_strcmp(widget_get_prop_str(w, "image", ""), "list_into") ||
        !painting_in(st.sel_accent) || widget_load_image(w, LIST_INTO_WHITE, bitmap))
        return;
    /* Only the middle half is cleared, where the glyph's ink is (rows 16-33 of 50): the whole
     * 50 px image would cross a 48 px row's top, over the bar's highlight and the row above. */
    int x = I(w, W_W) / 2, y = I(w, W_H) / 2;
    rect_t clear = { x - (int)bitmap[0] / 2, y - (int)bitmap[1] / 4, (int)bitmap[0],
                     (int)bitmap[1] / 2 };
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    fill_box(canvas, &clear, RGBA(accents[accent()][0]), 0);
    canvas_draw_icon(canvas, bitmap, x, y);
    stock_canvas_set_fill_color(canvas, fill);
}
#endif

/* Stock paints children first and calls this with the surface's canvas origin restored. */
int ringnav_paint(void *w, void *canvas) {
    if (st.pull_page && !pull_live()) pull_cancel();
    int result = stock_paint_trampoline(w, canvas);
    coverflow_paint(w, canvas);
    lockscreen_paint(w, canvas);
    if (w == st.cf_letter_surface && st.cf_letter &&
        (unsigned)time_now_ms() - st.cf_letter_at < LETTER_MS && P(canvas, CANVAS_LCD)) {
        unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
        rect_t box = { (I(w, W_W) - LETTER_BOX) / 2, (I(w, W_H) - LETTER_BOX) / 2, LETTER_BOX,
                       LETTER_BOX };
        unsigned color = (LETTER_ALPHA << 24) | FILL_RGB;
        fill_box(canvas, &box, color, LETTER_RADIUS);
        draw_centred(canvas, &st.cf_letter, 1, &box, LETTER_PX, 0xffffffff);
        stock_canvas_set_fill_color(canvas, fill);
    }
    edit_paint(w, canvas);
    photos_paint(w, canvas);
    books_paint(w, canvas);
    spot_paint(w, canvas);
    addon_paint(w, canvas);
    peq_paint(w, canvas);
#if IPOD
    visualizer_paint(w, canvas);
    games_paint(w, canvas);
#endif
    /* Even a page with no navigable pane must end pending input when it is painted. */
    if (st.center_timer || st.home_surface) {
        void *wm = window_manager(), *top = window_manager_get_top_window(wm);
        if (!usable() || window_manager_is_animating(wm) || top != st.center_top) cancel_center();
        if (!carousel_page(top)) st.home_surface = (void *)0;
    }
#if IPOD
    paint_chevrons(w, canvas);
    paint_radio(w, canvas);
    paint_into(w, canvas);
    paint_letter(w, canvas);
    paint_cover(w, canvas);
    if (w && st.np_lrc && P(w, W_PARENT) == st.np_lrc) lyrics_paint(w, canvas);
    paint_caps(w, canvas);
    coverflow_home_clip(w, canvas, 0);
    st.paint_w = w ? P(w, W_PARENT) : (void *)0;
#else
    paint_selection(w, canvas);
#endif
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

/* A stock settings-style row (0x4c19bc): a list_button with a 52px icon and a 24px label at x 72
 * (no icon: at x 10, as the Bluetooth page's rows), into adding stock's list_into trailing image
 * with the label narrowed clear of it, as the stock Local Music categories place it. Returns the
 * label. */
static void *list_row_core(void *view, const char *icon, int (*click)(void *, void *), void *ctx,
                           int w, int into) {
    /* Match the patched Library constructor's body and art_inset (tools/ipod.py). */
    int compact =
        IPOD && !tk_strcmp(widget_get_prop_str(view, "name", ""), "scroll_view_localmusic");
    int h = compact ? 68 : 70;
    void *button = list_button(view);
    if (compact) widget_resize(button, 335, h);
    if (click) widget_on(button, EVT_CLICK, click, ctx);
    if (icon) { /* centred, as stock's rows (0x524978) */
        void *image = image_create(button, compact ? 8 : 10, 0, SET_STOCK_ICON, h);
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
    void *win, *icon, *slider;
    unsigned timer;
    int x, y, sx, sy, slide; /* slide: the slider's touch, 0 undecided, 1 its own, -1 the swipe's */
} quick __attribute__((section(".scratch")));

static int quick_cancel(void *ctx, void *event) {
    stop_timer(&quick.timer);
    quick.win = quick.icon = quick.slider = 0;
    return 0;
}
static int quick_long(const void *unused) {
    quick.timer = 0;
    void *win = quick.win, *icon = quick.icon;
    if (!win || window_manager_get_top_window(window_manager()) != win ||
        !window_manager_get_pointer_pressed(window_manager()))
        return 0;
    int page = widget_get_prop_int(icon, QUICK_HOLD, 0);
    /* Abort the image's press before closing, so its release cannot toggle the radio. */
    prop(icon, QUICK_HOLD, -page);
    unsigned abort_event[12];
    pointer_event_init(abort_event, EVT_POINTER_ABORT, win, 0, 0);
    widget_dispatch_event_to_target_recursive(win, abort_event);
    quick_cancel(0, 0);
    dialog_quit(win, 0);
    navigator_to(page == 1 ? "systemset/wifi_page" : "systemset/bluetooth_page");
    return 0;
}
static int quick_down(void *icon, void *event) {
    int page = widget_get_prop_int(icon, QUICK_HOLD, 0);
    prop(icon, QUICK_HOLD, page < 0 ? -page : page);
    quick.win = window_manager_get_top_window(window_manager());
    quick.icon = icon;
    quick.x = I(event, EVENT_X), quick.y = I(event, EVENT_Y);
    rearm(&quick.timer, quick_long, RATE_HOLD_MS);
    return 0;
}
int ringnav_quick_settings(void *win, void *ctx) {
    int result = stock_quicksettings_trampoline(win, ctx);
    if (win) {
        quick_lock_text(win);
        static const char *const names[] = { "img_wifiswitch", "img_btswitch" };
        for (int i = 0; i < 2; ++i) {
            void *icon = widget_lookup(win, names[i], 1);
            if (!icon) continue;
            prop(icon, QUICK_HOLD, i + 1);
            widget_on(icon, EVT_POINTER_DOWN, quick_down, icon);
        }
        widget_on(win, EVT_DESTROY, quick_cancel, win);
        widget_on(win, EVT_WINDOW_CLOSE, quick_cancel, win);
        widget_on(win, EVT_WINDOW_BACKGROUND, quick_cancel, win);
    }
    return result;
}

/* Power's Charge limit, Low power and Wake (docs/internals.md#charge-limit, #low-power,
 * #wake), Q2POD CHARGELIMIT, LOWPOWER and WAKEDOUBLE in config.ini, Display's Artist albums
 * (ARTISTCF), Audio settings' Year order (YEAROLD, coverflow.c's year ranks) and Artists, which
 * is stock's own PLAYSET ARTISTTYPE (artist_type, the artist page's switch;
 * docs/internals.md#album-artists), and Bluetooth's Resume on connect (RESUME, so a missing key
 * holds playback; docs/internals.md#resume-on-connect), and the Lock screen's Clock (LOCKCLOCK,
 * lockscreen.c; docs/internals.md#lock-screen), and Playback's Artist album order (ARTISTAZ), and
 * Power's Volume guard (VOLGUARD, playback.c's wheel_guard; docs/internals.md#volume-guard), and
 * Display's Hold Return (HOLDRETURN, compact_now_playing; docs/ipod.md#hold-return), and Power's
 * Standby limit (STANDBYLIMIT, ringnav_standby; docs/internals.md#standby-limit), and Bluetooth's
 * Control Device Volume (BTVOLLINK, bt_audio_ready; docs/internals.md#headset-controls-and-volume).
 */
/* Bluetooth's Control Device Volume on: the Q2's volume is the device's own (bt_audio_ready). */
static int bt_vol_link __attribute__((section(".scratch")));
#define STR_(x) #x
#define STR(x) STR_(x)
static int *const pod_values[] = { &st.charge_limit,
                                   &st.low_power,
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
                                   &st.wheel_qs };
_Static_assert(POD_LOCKCLOCK == 7, "lockscreen.c passes pod_rows 7");
_Static_assert(sizeof pod_values / sizeof *pod_values == POD_COUNT, "a pod_values per row");
static const char *const pod_keys[] = {
    "CHARGELIMIT", "LOWPOWER", "WAKEDOUBLE", "ARTISTCF",     "YEAROLD",   0, "RESUME", "LOCKCLOCK",
    "ARTISTAZ",    "VOLGUARD", "HOLDRETURN", "STANDBYLIMIT", "BTVOLLINK", 0, 0,        "WHEELQS"
};
_Static_assert(sizeof pod_keys / sizeof *pod_keys == POD_COUNT, "a pod_keys entry per row");
/* a row's choices: Artist albums three, Standby limit four, every other row off and on */
static int pod_choices(unsigned i) { return i == POD_ARTIST_CF ? 3 : i == POD_STANDBY ? 4 : 2; }
static void pod_settings(void) {
    if (st.pod_read) return;
    for (unsigned i = 0; i < sizeof pod_keys / sizeof *pod_keys; ++i)
        if (pod_keys[i]) /* Artist albums defaults to Coverflow */
            *pod_values[i] = config_value("Q2POD", pod_keys[i], pod_choices(i), i == POD_ARTIST_CF);
    st.pod_read = 1;
}
/* Wheel QS on (Q2POD WHEELQS, default off; playback.c's swipe). */
int ringnav_wheel_qs(void) {
    pod_settings();
    return st.wheel_qs;
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
int ringnav_lock_clock(void) {
    pod_settings();
    return st.lock_clock;
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
static unsigned le32(const unsigned char *p) {
    return p[0] | p[1] << 8 | p[2] << 16 | (unsigned)p[3] << 24;
}
/* A FLAC's "ALBUM ARTIST" Vorbis comment (foobar2000's spelling) into out (0x100 bytes); stock's
 * libtag reads only ALBUMARTIST. ponytail: FLAC only; Ogg/Opus would need page reassembly. */
static void flac_album_artist(const char *path, char *out) {
    const char *ext = strrchr(path, '.');
    if (!ext || strcasecmp(ext, ".flac")) return;
    void *f = fopen(path, "rb");
    unsigned char h[13];
    if (!f) return;
    if (fread(h, 4, 1, f) == 1 && !memcmp(h, "fLaC", 4))
        while (fread(h, 4, 1, f) == 1) {
            unsigned left = (unsigned)h[1] << 16 | h[2] << 8 | h[3], c;
            if ((h[0] & 0x7f) != 4) {
                if (h[0] & 0x80 || fseek(f, (long)left, 1)) break;
                continue;
            }
            /* vendor length and string, the comment count, then each comment's length and bytes,
             * read a field at a time: a cover kept in the comments can make the block megabytes */
            if (left < 8 || fread(h, 4, 1, f) != 1 || (c = le32(h)) > left - 8 ||
                fseek(f, (long)c, 1) || fread(h, 4, 1, f) != 1)
                break;
            left -= 8 + c;
            for (unsigned n = le32(h); n-- && left >= 4 && fread(h, 4, 1, f) == 1;) {
                c = le32(h);
                left -= 4;
                if (c > left) break;
                left -= c;
                if (c > 13) {
                    if (fread(h, 13, 1, f) != 1) break;
                    if (!strncasecmp((char *)h, "ALBUM ARTIST=", 13)) {
                        c = c - 13 > 0xff ? 0xff : c - 13;
                        out[fread(out, c, 1, f) == 1 ? c : 0] = 0;
                        break;
                    }
                    c -= 13;
                }
                if (fseek(f, (long)c, 1)) break;
            }
            break;
        }
    fclose(f);
}
/* toolsGetMusicInfo (the library scan and the player's tag reads): an empty album artist falls back
 * to flac_album_artist. */
int ringnav_music_info(int a, char *info, const char *path) {
    int r = stock_music_info_trampoline(a, info, path);
    if (path && !info[MUSIC_INFO_ALBUM_ARTIST])
        flac_album_artist(path, info + MUSIC_INFO_ALBUM_ARTIST);
    return r;
}
/* player_get_id3info's copy of the parsed path (ID3_PATH in tools/build.py), after the tags and
 * before Now Playing's lines. A CUE track's tags are its record's, the stock parser's TITLE and
 * PERFORMER (the track's, else the sheet's); a sheet without them takes the file's (issue #59).
 * ponytail: the playing track only; the database keeps the sheet's empty fields for Library. */
char *ringnav_id3_path(char *id3, const char *path) {
    static const int fields[] = { ID3_ALBUM, ID3_ARTIST, ID3_ALBUM_ARTIST };
    if (mclGetCueFlag() && (!id3[ID3_ALBUM] || !id3[ID3_ARTIST] || !id3[ID3_ALBUM_ARTIST])) {
        char info[0x698] = { 0 };
        if (toolsGetMusicInfo(0, info, path) == 1)
            for (unsigned i = 0; i < sizeof fields / sizeof *fields; i++) {
                char *f = id3 + fields[i];
                if (*f) continue;
                tk_snprintf(f, 0x100, "%s", info + fields[i] - ID3_ALBUM);
                toolsTrimLeft(f); /* as stock did to its fields before this copy */
            }
    }
    /* last: now_tag and marked_empty read a matching path as the tags being complete */
    tk_snprintf(id3, 0x400, "%s", path); /* +0x400: the CUE start */
    return id3;
}
/* The library scan's folder walk (SCAN_DIRS in tools/build.py): its first exclusion, strcmp(name,
 * "."), here skips every hidden folder, as Folder view's list does, so a card app's own
 * (q2-deezer's stream cache in .deezer/stream) never joins the Library. */
int ringnav_scan_dir(const char *name, const char *dot) {
    return name[0] == '.' ? 0 : strcmp(name, dot);
}

/* Whether s is UTF-8: every lead byte with its continuation bytes, nothing overlong, no surrogate,
 * nothing past U+10FFFF. */
static int utf8_text(const char *s) {
    for (const unsigned char *p = (const unsigned char *)s; *p;) {
        unsigned c = *p++, n = c < 0x80                ? 0
                               : c >= 0xc2 && c < 0xe0 ? 1
                               : c >= 0xe0 && c < 0xf0 ? 2
                               : c >= 0xf0 && c < 0xf5 ? 3
                                                       : 4;
        if (n == 4) return 0;
        unsigned v = c & (0x3f >> n);
        for (unsigned i = 0; i < n; ++i, ++p) {
            if ((*p & 0xc0) != 0x80) return 0;
            v = v << 6 | (*p & 63);
        }
        if ((n == 2 && (v < 0x800 || (v >= 0xd800 && v < 0xe000))) ||
            (n == 3 && (v < 0x10000 || v > 0x10ffff)))
            return 0;
    }
    return 1;
}

static int ascii_letter(unsigned c) { return (c | 32) >= 'a' && (c | 32) <= 'z'; }

/* Whether s reads as GBK rather than Latin-1 or Windows-1252: not UTF-8, and every byte from 0x80
 * a two-byte character's, lead 0x81-0xFE then trail 0x80-0xFE, none of them between two ASCII
 * letters. Western text has an accented letter beside an ASCII one ("mú-s-ica" is FA 73), or a
 * pair inside a word ("Can-çã-o", "Beyonc-é’-s"); Chinese text runs of high-byte pairs. ponytail: a
 * GBK character with an ASCII-range trail (0x40-0x7E, rarer GBK/3-4 ones), or one alone inside a
 * Latin word, reads as Latin-1, and accented pairs at a word's edge only ("ñó" alone) as GBK; a
 * real charset guesser if either shows up. */
static int gbk_text(const char *s) {
    int high = 0;
    for (const unsigned char *p = (const unsigned char *)s; *p; ++p) {
        if (*p < 0x80) continue;
        if (*p == 0x80 || *p == 0xff || p[1] < 0x80 || p[1] == 0xff ||
            (p > (const unsigned char *)s && ascii_letter(p[-1]) && ascii_letter(p[2])))
            return 0;
        high = 1, ++p;
    }
    return high && !utf8_text(s);
}

/* Tag text (TAG_TEXT in tools/build.py): stock toolsFixId3ErrorCode took a field TagLib found all
 * Latin-1 for GBK when its Latin-1 bytes (latin1) went to UTF-8 and back unchanged (round, the
 * strcmp this replaces), and "Lo que la música me enseñó" does. It is GBK now only if it also reads
 * as GBK; anything else (nonzero) keeps TagLib's UTF-8 copy. */
int ringnav_gbk_tag(const char *round, const char *latin1) {
    return strcmp(round, latin1) || !gbk_text(latin1);
}

/* The CUE sheet parser's fields (CUE_TEXT in tools/build.py), up to 500 bytes each in buffers of
 * 512 or more: stock took any field that wasn't Chinese UTF-8 for GBK ("Café" became Chinese).
 * UTF-8 stays as it is, GBK (gbk_text) goes through stock's conversion as before; anything else is
 * Windows-1252, the code page Western rippers write, re-encoded as UTF-8 in place and cut at a
 * whole character to 511 bytes. Stock's returned 1 for kept text; so does this. */
int ringnav_cue_text(char *s, const char *to, const char *from) {
    if (!s || utf8_text(s)) return 1;
    /* stock's conversion writes back over s unbounded: GBK grows by half as UTF-8, so 340 bytes at
     * most fill the 512; a longer field reads as Windows-1252 */
    if (strlen(s) <= 340 && gbk_text(s))
        return ((int (*)(char *, const char *, const char *))0x5bccac)(s, to, from);
    static const unsigned short cp1252[32] = {
        0x20ac, '?',    0x201a, 0x0192, 0x201e, 0x2026, 0x2020, 0x2021, 0x02c6, 0x2030, 0x0160,
        0x2039, 0x0152, '?',    0x017d, '?',    '?',    0x2018, 0x2019, 0x201c, 0x201d, 0x2022,
        0x2013, 0x2014, 0x02dc, 0x2122, 0x0161, 0x203a, 0x0153, '?',    0x017e, 0x0178
    };
    char out[512];
    unsigned n = 0;
    for (const unsigned char *p = (const unsigned char *)s; *p; ++p) {
        unsigned c = *p >= 0x80 && *p < 0xa0 ? cp1252[*p - 0x80] : *p, k = c < 0x80    ? 1
                                                                           : c < 0x800 ? 2
                                                                                       : 3;
        if (n + k >= sizeof out) break;
        if (k == 1)
            out[n++] = (char)c;
        else if (k == 2)
            out[n++] = (char)(0xc0 | c >> 6), out[n++] = (char)(0x80 | (c & 63));
        else
            out[n++] = (char)(0xe0 | c >> 12), out[n++] = (char)(0x80 | (c >> 6 & 63)),
            out[n++] = (char)(0x80 | (c & 63));
    }
    out[n] = 0;
    memcpy(s, out, n + 1);
    return 1;
}

/* Quote bounded scanner paths/tags into one UPDATE; never execute a truncated statement. */
static int scan_quote(char *sql, unsigned cap, unsigned *n, const char *s) {
    if (*n + 3 >= cap) return 0;
    sql[(*n)++] = '\'';
    for (; s && *s; ++s) {
        if (*n + 3 >= cap) return 0;
        if (*s == '\'') sql[(*n)++] = '\'';
        sql[(*n)++] = *s;
    }
    sql[(*n)++] = '\'';
    sql[*n] = 0;
    return 1;
}
/* Only toolsLoadAllFile's folder-scan duplicate check calls this. Keep the row's ID, CUE bounds,
 * creation time and library count, so saved queues/favourites still refer to the same song. */
int ringnav_scan_refresh(void *row, const char *table, int type) {
    int exists = toolsCheckItemExist(row, table, type);
    if (!table || strcmp(table, "songtable")) return exists;
    if (!exists) return toolsGetMusicNum() >= 65000;
    const char *path = P(row, REC_PATH), *name = P(row, REC_NAME);
    if (!path || !name) return exists;
    char info[0x698] = { 0 }, sql[8192];
    int cue = I(row, REC_CUE_START) || I(row, REC_CUE_END);
    if (!cue) {
        /* Stock returns success even when TagLib cannot open/read a tag; keep the old tags then. */
        void *file = taglib_file_new(path, 0);
        int valid = file && taglib_file_tag(file);
        if (file) taglib_file_free(file);
        /* Refresh tags without the audio-properties probe that can walk the whole file. */
        if (!valid || toolsGetMusicInfo(0, info, path) != 1) return exists;
    }
    static const char *const fields[] = { "album", "songer",   "style",
                                          "name",  "composer", "albumsonger" };
    static const unsigned offsets[] = { 0, 0x100, 0x200, 0x280, 0x480, 0x580 };
    static const unsigned records[] = { REC_ALBUM, REC_ARTIST, 0x1c,
                                        REC_TITLE, 0x20,       REC_ALBUM_ARTIST };
    unsigned n = tk_snprintf(sql, sizeof sql, "update songtable set ");
    for (unsigned i = 0; i < sizeof fields / sizeof *fields; ++i) {
        n += tk_snprintf(sql + n, sizeof sql - n, "%s%s=", i ? "," : "", fields[i]);
        if (n >= sizeof sql ||
            !scan_quote(sql, sizeof sql, &n, cue ? P(row, records[i]) : info + offsets[i]))
            return exists;
    }
    /* Stock indexes the trimmed title's pinyin initial, falling back to the filename. */
    char title[128] = "", key[128] = "";
    tk_snprintf(title, 31, "%s",
                cue && P(row, REC_TITLE) ? (char *)P(row, REC_TITLE) : info + 0x280);
    toolsTrimLeft(title);
    toolsTrimRight(title);
    if (!*title || !strcmp(title, "(null)")) tk_snprintf(title, 31, "%s", name);
    toolsHanziToPinyin(title, key);
    unsigned initial = (unsigned char)*key;
    if (initial >= 'a' && initial <= 'z') initial -= 'a' - 'A';
    initial = initial >= 'A' && initial <= 'Z'   ? initial
              : initial >= '0' && initial <= '9' ? '*'
                                                 : '#';
    n += tk_snprintf(sql + n, sizeof sql - n,
                     ",Initial=%u,year=%d,track=%d,cd_num=%d where filename=", initial,
                     cue ? I(row, 0x30) : I(info, 0x680), cue ? I(row, REC_TRACK) : I(info, 0x684),
                     cue ? I(row, REC_DISC) : I(info, 0x688));
    if (n >= sizeof sql || !scan_quote(sql, sizeof sql, &n, name)) return exists;
    n += tk_snprintf(sql + n, sizeof sql - n, " and fileurl=");
    if (n >= sizeof sql || !scan_quote(sql, sizeof sql, &n, path)) return exists;
    n += tk_snprintf(sql + n, sizeof sql - n, " and time=%d and time_end=%d", I(row, REC_CUE_START),
                     I(row, REC_CUE_END));
    if (n < sizeof sql) toolsUpdateDbTable("/mnt/data/database.db", sql);
    return exists;
}
/* checkBluetoothStatus' player_save_start(0) once a headset's A2DP comes up (tools/build.py
 * BT_CONNECT_START): player_save_stop stopped the local or Tidal queue, and stock restarts it
 * unpaused, so a paused queue plays, and a stopped one starts over whatever owns playback
 * (mclStartPlayer's hook yields Spotify and Internet Radio). Resume on connect off (the default):
 * a queue that was playing goes on, a paused one comes back paused, and nothing else starts. */
int ringnav_bt_connect_start(int pause) {
    if (ringnav_bt_resume()) return player_save_start(pause);
    int saved = media_owner() == 1 ? mclGetSavePlayStatus() : 0; /* 2 playing, 3 paused */
    return saved == 2 || saved == 3 ? player_save_start(saved == 3) : 0;
}
static void pod_text(int i) {
    static const char *const names[][4] = {
        { "Charge Limit: Off", "Charge Limit: " STR(CHARGE_STOP) "%" },
        { "Low Power: Off", "Low Power: On" },
        { "Wake: Single Press", "Wake: Double Press" },
        { "Artist Albums: Grid", "Artist Albums: Coverflow", "Artist Albums: List" },
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
        { "Refresh Coverflow" },
        { 0 }, /* Wheel QS: a switch */
    };
    _Static_assert(sizeof names / sizeof *names == POD_COUNT, "a names entry per row");
    if (i == POD_CF_SORT) {
        char sort[48];
        tk_snprintf(sort, sizeof sort, "Coverflow %s", coverflow_sort_name());
        widget_set_text_utf8(st.pod_label[i], sort);
        return;
    }
    int v = i == POD_ARTISTS ? I(artist_type, 0) == 1 : pod_values[i] ? *pod_values[i] : 0;
    const char *toggle = i == POD_CHARGE      ? "Charge Limit (" STR(CHARGE_STOP) "%)"
                         : i == POD_LOW       ? "Low Power"
                         : i == POD_RESUME    ? "Resume on Connect"
                         : i == POD_VOL_GUARD ? "Volume Guard"
                         : i == POD_BT_LINK   ? "Control Device Volume"
                         : i == POD_WHEEL_QS  ? "Wheel QS"
                                              : 0;
    if (toggle)
        list_toggle(st.pod_label[i], toggle, v);
    else
        widget_set_text_utf8(st.pod_label[i], names[i][v]);
}
/* Centre or tap toggles and saves. Charge limit and Low power take effect on the next UI loop pass
 * (power_poll); Artists on the next load of an artist list, as the artist page's switch does;
 * Standby limit at the next Standby; Coverflow Sort and Refresh Coverflow at Coverflow's next open
 * (coverflow.c), Refresh with a toast, as it changes nothing in view. */
static int pod_click(void *ctx, void *event) {
    int i = (int)(long)ctx;
    pod_settings();
    if (i == POD_CF_SORT)
        coverflow_sort_step();
    else if (i == POD_CF_REFRESH) {
        coverflow_refresh_art();
        toast("Coverflow rebuilds its artwork next time");
    } else if (i == POD_ARTISTS) {
        int v = I(artist_type, 0) != 1;
        I(artist_type, 0) = v;
        write_int_config(v, "PLAYSET", "ARTISTTYPE");
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
int ringnav_powermanager(void *win, void *ctx) {
    static const char *const icons[] = { "usb_chargeswitch", "system_powermanager",
                                         "system_keylock" };
    int result = stock_power_trampoline(win, ctx);
    pod_rows(win, "scroll_view_powermanager", POD_CHARGE, 3, icons);
    pod_rows(win, "scroll_view_powermanager", POD_VOL_GUARD, 1, icons + 2);
    pod_rows(win, "scroll_view_powermanager", POD_STANDBY, 1, icons + 1);
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
/* A settings list's rows, stock's (ids 0.. in its table order, each button named by its id, which
 * its click looks up, so order is free) and then ours, restacked into order; any other count is
 * left as built. */
static void reorder(void *view, const unsigned char *order, unsigned n) {
    void *rows[32];
    if (!view || n > sizeof rows / sizeof *rows || widget_count_children(view) != n) return;
    for (unsigned i = 0; i < n; i++) rows[i] = widget_get_child(view, i);
    for (unsigned i = 0; i < n; i++) widget_restack(rows[order[i]], i);
}

/* Everyday listening, volume, library behaviour, then specialist output controls. */
static const unsigned char playset_order[] = {
    3 /* Play Mode */,
    2 /* EQ */,
    9 /* Gapless */,
    18 /* Crossfade */,
    10 /* ReplayGain */,
    8 /* Memory play */,
    20 /* Resume Songs */,
    0 /* Gain */,
    4 /* Max Volume */,
    5 /* Default Vol */,
    11 /* Balance */,
    21 /* Mono Audio */,
    22 /* Swap L/R */,
    16 /* Artists */,
    17 /* Artist album order */,
    15 /* Year order */,
    19 /* Folder Root */,
    14 /* Folder skip */,
    13 /* Folder art */,
    12 /* Enlarged art */,
    1 /* Filters */,
    6 /* DSD Output */,
    23 /* MQA */,
    7 /* USB Audio */,
};
/* Connections, personalisation and daily controls, then device maintenance; About stays last. */
static const unsigned char sysset_order[] = {
    1 /* Wireless */,     2 /* Network */,  3 /* Display */,       4 /* Power */,
    8 /* Buttons lock */, 7 /* Key tone */, 5 /* Date and time */, 0 /* Language */,
    6 /* In-Vehicle */,   13 /* Boot to */, 12 /* Apps */,         9 /* Update */,
    10 /* Reset */,       11 /* About */,
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

int ringnav_audioset(void *win, void *ctx) {
    static const char *const icons[] = { "playset_covermode", "playset_folderjump" };
    int result = stock_audioset_trampoline(win, ctx);
    pod_rows(win, "scroll_view_playset", POD_YEAR_OLD, 2, icons);
    pod_rows(win, "scroll_view_playset", POD_ARTIST_AZ, 1, icons);
    void *view = win ? widget_lookup(win, "scroll_view_playset", 1) : (void *)0;
    if (view)
        xfade_row(list_row_into(view, "playset_gapless", xfade_open, (void *)0)); /* crossfade.c */
    if (view) {
        char name[256];
        root_read(name);
        st.root_label = list_row(view, "playset_folderjump", root_click, 0);
        root_text(name);
        resume_row(list_row_into(view, "playset_memplay", xfade_open, (void *)2)); /* crossfade.c */
        mono_row(list_row(view, "playset_balance", mono_click, 0));
        swap_row(list_row(view, "playset_balance", swap_click, 0));
        mqa_row(list_row(view, "playset_dsdoutput", mqa_click, 0)); /* crossfade.c */
    }
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
    widget_invalidate_force(w, (void *)0);
}

/* scroll_lrc's rows are 20 px, or 25 a line when wrapped (0x52a0c8), and its 178 px are no whole
 * number of them, so the view's end (content height - view height), where stock's follow and the
 * wheel stop, cut the top row. The content grows to the first row top at or past that end plus the
 * view: under a row of blank space below the last. A relayout (scroll_view's, 0x5f17a8) puts the
 * height back and clamps the offset; the next follow, tick or glide step pads it again. ponytail:
 * the wheel and a drag stop anywhere, so a row can be cut at the top until the page follows. iPod's
 * lyrics page (lyrics.c) ends with its last line in the middle. */
#if IPOD
/* The offset that centres row i of lrc in its view. */
static int lyric_centre(void *lrc, int i) {
    void *row = widget_get_child(lrc, i);
    return I(row, W_Y) + (I(row, W_H) - I(lrc, W_H)) / 2;
}
#endif
static void lyric_pad(void *lrc) {
#if IPOD
    if (lyrics_on(lrc)) {
        I(lrc, VIEW_CONTENT_H) = lyric_centre(lrc, widget_count_children(lrc) - 1) + I(lrc, W_H);
        return;
    }
#endif
    int h = I(lrc, W_H), end = I(lrc, VIEW_CONTENT_H) - h, top = 1 << 30;
    for (int i = widget_count_children(lrc); i-- > 0;) {
        int t = I(widget_get_child(lrc, i), W_Y);
        if (t >= end && t < top) top = t;
    }
    if (end > 0 && top < 1 << 30) I(lrc, VIEW_CONTENT_H) = top + h;
}

#if IPOD
static unsigned np_track(void);

/* The lyrics page in front: Now Playing on top, lit, not sliding and untouched, on the lyrics. */
static int lyrics_shown(void) {
    void *wm = window_manager();
    return st.np_lrc && st.np_slide && usable() && window_manager_get_top_window(wm) == st.np_win &&
           !window_manager_is_animating(wm) && !window_manager_get_pointer_pressed(wm) &&
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
    st.lyric_held = st.lyric_spun = st.lyric_glide = st.lyric_to = 0;
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
#endif

/* The Now Playing timer's follow (LYRIC_FOLLOW in tools/build.py): stock scrolls to the row three
 * lines above the current one, which past the end clamps to it. iPod: lyrics the wheel or a finger
 * moved stay (lyric_held) until the page leaves them or another song starts, and a finger on the
 * screen keeps them too; on the page, the follow glides. The lyrics page follows its own playing
 * line (lyric_follow). */
int ringnav_lyric_follow(void *lrc, int x, int y, int ms) {
    lyric_pad(lrc);
#if IPOD
    if (lrc == st.np_lrc) {
        if (st.lyric_held || lyrics_on(lrc) || window_manager_get_pointer_pressed(window_manager()))
            return 0;
        if (lyrics_shown()) {
            lyric_glide(lrc, y * 16);
            return 0;
        }
        st.lyric_glide = 0;
    }
#endif
    return scroll_view_scroll_to(lrc, x, y, ms);
}

/* The page's refresh rebuilds scroll_lrc's rows (0x529f0c, LYRIC_ROWS in tools/build.py) whenever
 * g_forcerefresh_flag is set, as Play/Pause (playpause_quick_click) and the page's return to the
 * front do, and the rebuild ends at offset 0: the lyrics flicked to the top until the next follow.
 * The offset stays; the follow moves it if the line did. iPod: rows for another song than the last
 * rows' start at the top (lyric_top), whatever moved the old ones since the song changed, and they
 * become the lyrics page's (lyrics_rows). */
int ringnav_lyric_rows(void *win) {
    void *lrc = widget_lookup(win, "scroll_lrc", 1);
    int y = lrc ? I(lrc, SCROLL_Y) : 0, ret = ((int (*)(void *))0x529f0c)(win);
#if IPOD
    if (lrc && lrc == st.np_lrc && lyrics_rows(lrc)) lyric_pad(lrc);
    unsigned song = lrc && lrc == st.np_lrc ? np_track() : st.lyric_rows_song;
    if (song != st.lyric_rows_song) {
        st.lyric_rows_song = st.lyric_song = song;
        lyric_top(lrc);
        return ret;
    }
#endif
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

#if IPOD
/* The status bar's centred label follows stock's 24-hour setting (18:14 or 6:14 PM), without
 * seconds, or --:-- when the time cannot be read. clock_show reads the time once a minute, from a
 * timer due at the next minute, writes the label when the minute shown changes and applies Auto
 * Light Mode's hour; nothing reads the time in between. It runs at once for a new zone
 * (ringnav_timezone), a time set by hand (time_click) and a wake (ringnav_sleep), and on the pass
 * after the bar first paints or paints with a new 24-hour setting (ringnav_paint_bg). ponytail: a
 * network time sync while lit shows at the next minute. */
static void clock_show(void);
static int clock_due(const void *info) {
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
    st.clock_timer = timer_add(clock_due, (void *)0, ok ? (60u - (unsigned)tm[0]) * 1000u : 60000u);
    unsigned key = ok ? ((unsigned)(tm[2] * 60 + tm[1]) + 1) | (unsigned)full << 11
                      : ~0u; /* 0 before the first */
    void *bar = *(void *const *)system_bar;
    void *label = key == st.clock_key || !bar ? (void *)0 : widget_lookup(bar, "label_clock", 1);
    if (!label) return;
    st.clock_key = key;
    char s[16] = "--:--";
    if (ok && full)
        tk_snprintf(s, sizeof s, "%02d:%02d", tm[2], tm[1]);
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

/* img_state follows whichever player plays, stock's or Spotify's, radio's or a video's, and
 * label_idle shows when neither it nor label_eq does. Returns whether img_state changed. */
static int bar_state_sync(void *bar) {
    int changed = 0;
    if (!st.bar_bt) {
        st.bar_bt = widget_lookup(bar, "img_bt", 1);
        st.bar_wifi = widget_lookup(bar, "img_wifi", 1);
        st.bar_pct = widget_lookup(bar, "label_battery", 1);
        st.bar_slot = widget_lookup(bar, "view_battery", 1);
        st.bar_icon = widget_lookup(bar, "img_battery", 1);
        st.bar_idle = widget_lookup(bar, "label_idle", 1);
        st.bar_state = widget_lookup(bar, "img_state", 1);
        st.bar_eq = widget_lookup(bar, "label_eq", 1);
    }
    if (st.bar_state) {
        int status = media_owner() == 1 ? mclGetPlayStatus() :
                     video_status() ?: radio_status() ?: spot_status();
        int on = status == 2 || status == 3;
        changed = on != !!widget_get_visible(st.bar_state);
        widget_set_visible(st.bar_state, on, 0);
        if (on) {
            const char *icon = status == 2 ? "bar_play" : "bar_pause";
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
static int bar_repaint(const void *info) {
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
static void bar_sync(void *bar) {
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
    if (st.home_rockbox != rockbox_available()) coverflow_home_layout();
    int mode = st.battery;
    if (mode) {
        int reach = mode == 1   ? BATT_PCT_W
                    : mode == 3 ? BATT_INSIDE_W + BATT_NUB_W
                                : BATT_BODY_W + BATT_NUB_W + BATT_GAP + BATT_PCT_W;
        if (widget_get_visible(st.bar_wifi)) reach += I(st.bar_wifi, W_W) + STATUS_GAP;
        if (shown)
            reach +=
                STATUS_GAP +
                (st.codec && st.codec_step < CODEC_STEPS ? codec_reach[st.codec - 1] : BT_REACH);
        if (reach > BATT_ROOM) mode = 0;
    }
    widget_set_visible(st.bar_icon, 0, 0);
    widget_set_visible(st.bar_pct, 0, 0);
    widget_set_visible(st.bar_slot, 1, 0);

    /* the level from label_battery's "88%" (stock zeroes progress_battery while charging), then
     * charging (bar_charge) and low (bar_lowcharge), as stock picks the icon */
    const char *icon = widget_get_prop_str(st.bar_icon, "image", "");
    const unsigned *t = widget_get_text(st.bar_pct);
    int level = 0;
    while (t && *t >= '0' && *t <= '9') level = level * 10 + (int)(*t++ - '0');
    unsigned key = (unsigned)(level > 100 ? 100 : level) << 2 |
                   (unsigned)(icon && !tk_strcmp(icon, "bar_charge")) << 1 |
                   (unsigned)(icon && !tk_strcmp(icon, "bar_lowcharge"));
    int pct = pct_width(key >> 2);
    int width = mode == 1 ? pct
                          : (mode == 3 ? BATT_INSIDE_W : BATT_BODY_W) + BATT_NUB_W +
                                (mode == 2 ? BATT_GAP + pct : 0);
    int resized = I(st.bar_slot, W_W) != width;
    widget_resize(st.bar_slot, width, 30);
    /* Resize only relayouts the slot's own children; the right-aligned row must
     * position it again even when Icon + Percent -> Icon keeps it visible. */
    if (resized) widget_layout_children(P(st.bar_slot, W_PARENT));
    if (key == st.batt_key && mode == st.batt_mode && !resized) return;
    st.batt_mode = mode;
    st.batt_key = key;
    widget_invalidate_force(st.bar_slot, (void *)0);
}

/* systembar_showface, each second: stock hides img_state whenever its own player is stopped, so
 * with Spotify, radio or a video playing it went off here and back on at the next paint, two
 * relayouts a second and a frame with EQ drawn in the play icon's place. Correct it before paint,
 * dark or not, so a wake never shows stock's hidden icon; the badge and battery wait for paint. */
int ringnav_showface(void *bar) {
    int result = stock_showface_trampoline(bar);
    bar_state_sync(bar); /* no I/O, and a relayout waits for a paint */
    return result;
}

/* Mode 1 is the percentage alone; mode 3 uses a compact solid battery with dark digits; mode 2
 * puts the percentage beside the charge-level icon. Restore canvas state. */
static void paint_battery(void *w, void *canvas) {
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    unsigned key = st.batt_key, level = key >> 2, s[4], n = put_num(s, level);
    int inside = st.batt_mode == 3;
    if (!inside) s[n++] = '%';
    unsigned color = RGBA(key & 2 ? BATT_CHARGE_RGB : key & 1 ? BATT_LOW_RGB : 0xffffff);
    if (!inside && !st.pct_digit) {
        unsigned c = '0';
        canvas_set_font(canvas, (void *)0, BATT_PX);
        st.pct_digit = (int)(canvas_measure_text(canvas, &c, 1) + 0.5f);
        c = '%';
        st.pct_sign = (int)(canvas_measure_text(canvas, &c, 1) + 0.5f);
    }
    if (st.batt_mode == 1) {
        rect_t r = { 0, 0, I(w, W_W), I(w, W_H) };
        draw_centred(canvas, s, n, &r, BATT_PX, color);
        return;
    }
    const int bw = inside ? BATT_INSIDE_W : BATT_BODY_W, bh = inside ? BATT_INSIDE_H : BATT_BODY_H,
              nh = BATT_NUB_H, y = (I(w, W_H) + 1 - bh) / 2 - BATT_PCT_DY * (st.batt_mode == 2);
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
static void clock_text(void *label, int negative, int t) {
    char s[16] = "-";
    toolsTimeItoa(s + negative, t);
    widget_set_text_utf8(label, s);
}

/* Now Playing's "3 of 12", album and remaining time (slider max less value, stock's seconds, so a
 * drag or scrub previews it). Labels are written only when their source changes. */
static void np_sync(void *top) {
    if (!top || top != st.np_win) return;
    unsigned at, n;
    void *r = queue_now(&at, &n);
    const char *album = now_tag(r, REC_ALBUM);
    char s[24] = "";
    unsigned pos[2] = { at, n };
    unsigned h = hash_bytes(fnv(FNV_SEED, (const unsigned char *)album), (const unsigned char *)pos,
                            sizeof pos);
    if (h != st.np_hash) {
        st.np_hash = h;
        if (r) tk_snprintf(s, sizeof s, "%d of %d", at + 1, n);
        widget_set_text_utf8(st.np_pos, s);
        widget_set_text_utf8(st.np_album, album ? album : "");
        st.np_off = 0;
        stop_timer(&st.np_marquee); /* the next paint restarts it if the album overflows */
    }
    int left =
        widget_get_prop_int(st.np_slider, "max", 0) - widget_get_prop_int(st.np_slider, "value", 0);
    if (left < 0) left = 0;
    if (left == st.np_left) return;
    st.np_left = left;
    clock_text(st.np_remain, 1, left);
}

/* Scrub (docs/internals.md#scrub-ipod) follows stock's own key seek (0x52c754): the page's 250 ms
 * timer stops, the slider and label_playtime preview the target, player_seek_time commits it once
 * on exit in track seconds (it adds a CUE track's start), and the timer restarts. */
/* The accent's light tone (Minimal's own accent: MINIMAL_FILL) fills the progress bar in every
 * state a touch passes through; a white fill (Minimal: a thicker line) marks the scrub. */
static void slider_fill(void *slider, unsigned color) {
    static const char *const fill[] = { "style:normal:fg_color", "style:pressed:fg_color",
                                        "style:over:fg_color", "style:focused:fg_color" };
    for (unsigned i = 0; i < sizeof fill / sizeof *fill; ++i)
        widget_set_prop_int(slider, fill[i], (int)color);
}
static void np_fill(int scrub) {
    if (!st.np_slider) return;
    slider_fill(st.np_slider, scrub ? 0xffffffff : RGBA(accent_tone(TONE_LIGHT)));
    /* Minimal's fill is near white already: its scrub thickens the line to Classic's. */
    if (!ipod_classic())
        widget_set_prop_int(st.np_slider, "bar_size", scrub ? NP_BAR_CLASSIC : np_bar_h());
    widget_invalidate_force(st.np_slider, (void *)0);
}

/* The Theme's progress bar over the asset's (Minimal's square 4px line): Classic's NP_BAR_CLASSIC
 * capsule, and stock's play-state glyph, which Minimal hides. */
static void np_theme(void) {
    static const char *const radius[] = { "style:normal:round_radius", "style:pressed:round_radius",
                                          "style:over:round_radius", "style:disable:round_radius",
                                          "style:focused:round_radius" };
    void *glyph = st.np_win ? widget_lookup(st.np_win, "img_playstate", 1) : (void *)0;
    int h = np_bar_h();
    for (unsigned i = 0; st.np_slider && i < sizeof radius / sizeof *radius; ++i)
        widget_set_prop_int(st.np_slider, radius[i], h / 2);
    if (st.np_slider) widget_set_prop_int(st.np_slider, "bar_size", h);
    if (glyph) widget_set_opacity(glyph, ipod_classic() ? 255 : 0);
    /* Minimal: the album is paint_caps' capitals. */
    if (st.np_album) widget_set_opacity(st.np_album, ipod_classic() ? 255 : 0);
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
 * bar and the highlight going, until the slide_view leaves the lyrics page or another song starts;
 * back on the page they follow again. A scrub under way ends first. */
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
 * them, and a swipe that brings the page in holds nothing. Leaving the page drops the hold; the
 * next follow, off the page, jumps to the current line. Another song starts at the top, its hold
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
    if (st.lyric_held || window_manager_get_pointer_pressed(window_manager())) return;
    lyric_pad(lrc);
    if (shown) {
        if (to * 16 != st.lyric_to || (!st.lyric_glide && I(lrc, SCROLL_Y) != to))
            lyric_glide(lrc, to * 16);
    } else if (I(lrc, SCROLL_Y) != to) {
        lyric_still(lrc);
        scroll_view_set_offset(lrc, I(lrc, SCROLL_X), to);
    }
}

static void lyric_poll(void) {
    void *lrc = st.np_lrc, *wm = window_manager();
    unsigned song = lrc ? np_track() : st.lyric_song;
    if (song != st.lyric_song) {
        st.lyric_song = song;
        lyric_top(lrc);
    }
    int page = lrc && st.np_slide && widget_get_prop_int(st.np_slide, "value", 0) == 1;
    int touch = lrc && window_manager_get_top_window(wm) == st.np_win &&
                window_manager_get_pointer_pressed(wm);
    int shown = page && lyrics_shown(),
        on = shown && widget_count_children(lrc) && mclGetLyricSize() > 0;
    if (!page) st.lyric_held = 0;
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
        wheel_raw = st.lyric_raw = on;
    }
    int travel = on ? __sync_lock_test_and_set(&wheel_travel, 0) : 0;
    if (travel) {
        st.last_input = time_now_ms();
        reset_poweroptions_timer(1, 1, 1);
        lyric_hold();
        lyric_pad(lrc);
        int y = I(lrc, SCROLL_Y),
            from = st.lyric_glide || (st.lyric_to + 8) >> 4 == y ? st.lyric_to : y * 16;
        /* x rises anticlockwise: back up the lyrics */
        int row = lyrics_on(lrc) ? LYRIC_SLOT : LYRIC_ROW_PX;
        lyric_glide(lrc, from - travel * row * 16 * wheel_pct() / (WHEEL_TICK_UNITS * 50));
        st.lyric_spun = 1;
    }
    if (!page)
        st.lyric_spun = 0;
    else if (shown && st.lyric_spun && !wheel_held) {
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

/* A single centre press, DOUBLE_CLICK_MS on, never while scrubbing: it replays the release to stock
 * on_wm_keyup_fun, which turns the screen off (it reads only the event's key). */
static int np_single(const void *info) {
    static const unsigned release[EVENT_KEY / 4 + 1] = { [EVENT_KEY / 4] = KEY_CENTER };
    st.np_press = 0;
    if (g_backlight_status) on_wm_keyup_fun((void *)0, (void *)release);
    return 0;
}

/* Centre and, while scrubbing, the wheel on the top Now Playing page. A centre press waits
 * DOUBLE_CLICK_MS: a second one starts scrub, else it turns the screen off as anywhere else; while
 * scrubbing one press ends it at once (center_press). The wheel moves the target SCRUB_STEP
 * seconds times the WHEEL_RAMP_MS ramp, within the track, and only previews it: the seek waits
 * for the scrub to end. Neither the volume nor its dialog sees the wheel. */
static int np_key(void *top, unsigned key) {
    unsigned now = (unsigned)time_now_ms();
    if (key == KEY_CENTER) {
        int press = center_press(&st.np_press, &st.np_press_at, np_single, st.scrub);
        if (press != 2) return press ? STOP : 0;
        if (st.scrub) /* one press ends a scrub, a double press starts one */
            scrub_end();
        else if (st.np_slider) {
            st.scrub = 1;
            st.scrub_moved = 0;
            st.scrub_to = widget_get_prop_int(st.np_slider, "value", 0);
            st.scrub_track = np_track();
            stop_timer(&st.settle_timer);
            playing_timer_clear(st.np_win);
            np_fill(1);
            rearm(&st.scrub_timer, scrub_expire, SCRUB_MS);
        }
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
            st.np_slide = st.np_lrc = (void *)0;
        st.scrub_moved = 0; /* the page is going: no seek */
        st.np_off = 0;
        stop_timer(&st.np_marquee);
        np_cancel();
        lyrics_gone(); /* the next page's rows may take its rows' place */
    }
    return 0;
}

/* playing_page_init: stock builds the page and starts its 250 ms timer, then the iPod labels bind.
 */
int ringnav_playing(void *win, void *ctx) {
    int result = stock_playing_trampoline(win, ctx);
    if (!win) return result;
    rate_bind(win);
    st.np_win = win;
    st.np_pos = widget_lookup(win, "label_ipod_pos", 1);
    st.np_album = widget_lookup(win, "label_ipod_album", 1);
    st.np_slider = widget_lookup(win, "slider_play", 1);
    st.np_remain = widget_lookup(win, "label_ipod_remain", 1);
    st.np_elapsed = widget_lookup(win, "label_playtime", 1);
    st.np_cover = widget_lookup(win, "img_cover", 1);
    st.np_slide = widget_lookup(win, "slide_view", 1);
    st.np_lrc = widget_lookup(win, "scroll_lrc", 1);
    st.lyric_song = st.lyric_rows_song = np_track(); /* no song change at the start */
    lyric_init();                                    /* the rows stock's init built */
    st.np_hash = 0;
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
 * kind by g_playcover_type (0x51fdcc), so one counts only once it is the type's and g_lastcover_url
 * is this track's, as Home's art; until then the art keeps the shape of g_lastcover_url's cover, or
 * the square once that changes. The file is decoded past the image manager's cached copy, which may
 * be the last track's. Stock's default_bigcover placeholder is the square. */
static void bc_fit(void *win) {
    void *img = widget_lookup(win, "img_bigcover", 1), *shade = st.bc[1], *title = st.bc[0];
    const char *name = img ? widget_get_prop_str(img, "image", "") : "";
    unsigned at, n;
    void *r = queue_now(&at, &n);
    const char *path = r ? P(r, REC_PATH) : (void *)0;
    unsigned type = path && !tk_strcmp((const char *)g_lastcover_url, path) ? g_playcover_type : 0;
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

/* bigcover_page_init: stock's full-screen art (a tap on Now Playing's opens it, any tap closes it)
 * shows the song name BC_MS, then bc_fade fades it out. Stock's own timer writes the next song's
 * name; ringnav_sleep shows it again when the track changes. */
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

/* Every VOL_POLL_MS while the dialog vol_paint drew is still on top: a changed volume repaints it
 * whole. Stock's own invalidation of the hidden slider does not reliably reach the screen, so the
 * bar would stop following the wheel after a tick or two. */
static int vol_poll(const void *info) {
    void *top = window_manager_get_top_window(window_manager());
    void *vol = top && top == st.vol_dialog ? widget_lookup(top, "slider_vol", 1) : (void *)0;
    if (!vol) {
        st.vol_timer = 0;
        st.vol_dialog = (void *)0;
        return 0; /* RET_OK: removed */
    }
    if (widget_get_prop_int(vol, "value", 0) != st.vol_drawn)
        widget_invalidate_force(top, (void *)0);
    return 8; /* RET_REPEAT */
}

/* The volume, drawn by stock's dialog/volume_dialog itself: transparent and full-screen (its
 * dimming highlight removed at build time, tools/ipod.py), it opens on the wheel's volume and
 * sets its slider_vol. Over Now Playing, as on an iPod classic, the band from the progress bar to
 * the times turns black with a white bar over the track and "Volume N"; over any other window
 * (Quick Settings included) a VOL_PANEL_* rounded panel in the fast-scroll letter's style holds
 * a pill bar and the number alone. slider_vol and label_vol are hidden, and slider_vol is moved
 * onto the area drawn, so stock's partial repaints land on it; vol_poll catches the changes those
 * miss. */
static void vol_paint(void *top, void *canvas) {
    void *wm = window_manager(), *lcd = P(canvas, CANVAS_LCD);
    if (!lcd || tk_strcmp(widget_get_prop_str(top, "name", ""), "volume_dialog")) return;
    unsigned n = widget_count_children(wm);
    void *vol = widget_lookup(top, "slider_vol", 1), *label = widget_lookup(top, "label_vol", 1);
    if (n < 2 || !vol) return;
    int np = widget_get_child(wm, n - 2) == st.np_win && st.np_slider && st.np_elapsed;
    rect_t area, bar, text;
    if (np) {
        void *slider = st.np_slider, *times = st.np_elapsed;
        int dy = I(st.np_win, W_Y) - I(top, W_Y), y = dy + I(slider, W_Y),
            bh = widget_get_prop_int(slider, "bar_size", 8);
        area = (rect_t){ 0, y, I(st.np_win, W_W), dy + I(times, W_Y) + I(times, W_H) - y };
        bar = (rect_t){ I(slider, W_X), y + (I(slider, W_H) - bh) / 2, I(slider, W_W), bh };
        text = (rect_t){ 0, dy + I(times, W_Y), area.w, I(times, W_H) };
    } else {
        area = (rect_t){ VOL_PANEL_X, VOL_PANEL_Y, I(top, W_W) - 2 * VOL_PANEL_X, VOL_PANEL_H };
        int x = area.x + VOL_PANEL_PAD, bw = area.w - 2 * VOL_PANEL_PAD - VOL_PANEL_TEXT;
        bar = (rect_t){ x, area.y + (area.h - VOL_PANEL_BAR) / 2, bw, VOL_PANEL_BAR };
        text = (rect_t){ x + bw, area.y, VOL_PANEL_TEXT, area.h };
    }
    if (I(vol, W_Y) != area.y) widget_move_resize(vol, area.x, area.y, area.w, area.h);
    widget_set_visible(vol, 0, 0);
    if (label) widget_set_visible(label, 0, 0);
    int max = widget_get_prop_int(vol, "max", 100), level = widget_get_prop_int(vol, "value", 0);
    st.vol_dialog = top;
    st.vol_drawn = level;
    if (!st.vol_timer) st.vol_timer = timer_add(vol_poll, (void *)0, VOL_POLL_MS);
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), s[12] = { 'V', 'o', 'l', 'u', 'm', 'e', ' ' },
             k = np ? 7 : 0;
    int w = max > 0 ? bar.w * clamp_step(0, max, level) / max : 0;
    if (np) {
        canvas_set_fill_color(canvas, RGBA(0));
        canvas_fill_rect(canvas, area.x, area.y, area.w, area.h);
    } else {
        fill_box(canvas, &area, (LETTER_ALPHA << 24) | FILL_RGB, LETTER_RADIUS);
    }
    /* Pills, like the progress capsule: a non-zero fill is at least round, never a sliver. */
    fill_box(canvas, &bar, RGBA(np ? TRACK_COLOR : VOL_PANEL_TRACK), bar.h / 2);
    if (w) {
        rect_t on = { bar.x, bar.y, w < bar.h ? bar.h : w, bar.h };
        fill_box(canvas, &on, RGBA(0xffffff), bar.h / 2);
    }
    stock_canvas_set_fill_color(canvas, fill);
    k += put_num(s + k, (unsigned)clamp_step(0, 999, level));
    draw_centred(canvas, s, k, &text, np ? NP_TIMES_PX : VOL_PANEL_PX, 0xffffffff);
}

/* Both native album constructors use 110px square covers, with playing overlays either
 * beside the cover or nested inside it. Keep their local origins together when padding the tile.
 * Both themes inset the cover, so the selection card frames it rather than hiding under it. */
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

#if IPOD
/* Quick Settings' brightness slider fills as Now Playing's bar does (np_fill), set before its
 * paint; QFILL holds the fill it last got, so a new accent or Light Mode reaches it. */
#define QFILL "_ringnav_fill"
static void quick_fill(void *w) {
    void *win = w && !tk_strcmp(widget_get_prop_str(w, "name", ""), "slider_backlight")
                    ? window_of(w)
                    : (void *)0;
    if (!win || tk_strcmp(widget_get_prop_str(win, "name", ""), "statusbar_dialog")) return;
    int color = (int)RGBA(accent_tone(TONE_LIGHT));
    if (widget_get_prop_int(w, QFILL, 0) == color) return;
    widget_set_prop_int(w, QFILL, color);
    slider_fill(w, (unsigned)color);
}
/* The confirm pop-up's prompt (stock's own child, "Shut Down?"): CONFIRM_PROMPT_PX, centred in the
 * space above its rows. ponytail: by position, any non-button child above CONFIRM_TOP. */
static void confirm_prompt(void *w) {
    if (tk_strcmp(widget_get_prop_str(w, "name", ""), "confirminfo_dialog")) return;
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) {
        void *c = widget_get_child(w, i);
        if (!tk_strcmp(widget_get_type(c), "button") || I(c, W_Y) + I(c, W_H) > CONFIRM_TOP ||
            (!I(c, W_X) && !I(c, W_Y) && I(c, W_W) == I(w, W_W)))
            continue;
        widget_move_resize(c, 0, 0, I(w, W_W), CONFIRM_TOP);
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
static int press_repaint(const void *info) {
    st.press_timer = 0;
    void *top = window_manager_get_top_window(window_manager());
    if (top) widget_invalidate_force(top, (void *)0);
    return 0;
}
#endif

/* A touched list row's grey is the selection's shape: the whole row, as Classic's bar (the
 * Library's button leaves x 0-20, 355-375 and the pitch's last 4 px), or under Rounded Corners the
 * inset pill. Drawn as the row's background; the button's own box is skipped (1). The press
 * invalidates the whole surface (ringnav_touch), its release only the button, so the row's edges
 * repaint once after it (press_repaint). */
static int pressed_pill(void *w, void *canvas) {
#if IPOD
    if (!w) return 0;
    if (!is_row(w)) { /* the row drew it: skip its button's box */
        void *row = P(w, W_PARENT);
        return is_row(row) && touch_target(row) == w && pressed_row(w) &&
               I(row, W_W) > 2 * SEL_INSET + I(row, W_H);
    }
    void *b = touch_target(w);
    /* childless: not a row but a page, such as Now Playing's visualizer */
    if (!pressed_row(b) || !widget_count_children(w) || I(w, W_W) <= 2 * SEL_INSET + I(w, W_H)) {
        if (w == st.press_row) {
            st.press_row = (void *)0;
            rearm(&st.press_timer, press_repaint, 0);
        }
        return 0;
    }
    st.press_row = w;
    int in = (accent(), st.rounded) ? SEL_INSET : 0;
    rect_t pill = { in, 0, I(w, W_W) - 2 * in, I(w, W_H) };
    fill_box(canvas, &pill, PH_GREY, in ? pill_radius(b, pill.h, SEL_INSET) : 0);
    return b == w; /* a page row's own (clear) background */
#else
    return 0;
#endif
}

/* Stock paints a widget's background before its children, so the bar sits behind the rows.
 * The selection work for the surface happens here, once per frame, instead of in the border hook;
 * a BUTTONS dialog is itself top-level. Home's art is clipped to its panel until the border hook.
 * Other top-level widgets are the status bar, which gets its solid fill, and the windows. Painting
 * the top window or the bar (at least each second, systembar_showface) keeps the bar's clock,
 * Home's art and Now Playing's labels current; Home's art is also checked when Home is painted
 * under another window. */
int ringnav_paint_bg(void *w, void *canvas) {
    st.paint_w = w;
#if IPOD
    quick_fill(w);
    confirm_prompt(w);
#endif
    album_tile(w);
    int result = pressed_pill(w, canvas) ? 0 : stock_paint_bg_trampoline(w, canvas);
    void *wm = window_manager(), *bar = *(void *const *)system_bar;
    paint_selection(w, canvas);
    coverflow_home_clip(w, canvas, 1);
    if (w && w == st.bar_slot) paint_battery(w, canvas);
    if (!w || P(w, W_PARENT) != wm) return result;
    if (w == bar && P(canvas, CANVAS_LCD)) {
        unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
        /* Classic in Light Mode: an iPod's grey bar, flat (final colour) */
        int grey = ipod_classic() && ipod_light();
        stock_canvas_set_fill_color(canvas, grey ? RGBA(LIGHT_BAR) : ui_ink(RGBA(BAR_COLOR)));
        canvas_fill_rect(canvas, 0, 0, I(w, W_W), I(w, W_H));
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
        np_sync(top);
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
static int painting_in(void *row) {
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
    if (st.paint_w && name && tk_str_end_with(name, "text_color")) {
        if (!ipod_classic() && (st.paint_w == st.bar_idle || coverflow_home_owns(st.paint_w))) {
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
        if (ipod_light() && painting_in(st.sel_accent)) return color;
        int selected = selected_ink();
        if (selected && (st.sel_home || minimal_card())) {
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
        if (!ipod_classic() && !selected && (*color & 0xffffff) == 0xaaaaaa)
            *color = (*color & 0xff000000u) | SUDO_MUTED;
    }
    int a = accent();
    if (a == CRIMSON && !ipod_light()) return color;
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
static int settings_icon(const char *name) {
    for (const char *n = SETTINGS_ICON_NAMES; *n;) {
        if (!tk_strcmp(name, n)) return 1;
        while (*n++) {}
    }
    return 0;
}

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

int ringnav_image_add(void *manager, const char *name, void *bitmap) {
    static const unsigned char at[4][4] = BITMAP_RGBA_AT;
    unsigned format = bitmap ? *(unsigned short *)((char *)bitmap + 0xe) - 1u : 4,
             preset = accent();
    unsigned char *data = (void *)0;
    /* Before the manager copies bitmap_t: cached flags must agree with the new alpha. These
     * paths are stock's album/artist thumbnails and Folder view's (.downpic/fd*, its cover
     * thread's); playing covers are rounded after scaling. */
    if (format < 4 && name &&
        (tk_str_start_with(name, "file:///mnt/mmc/.sldp/") ||
         tk_str_start_with(name, "file:///mnt/mmc/.downpic/fd"))) {
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
    const char *s = name;
    int dark = s && tk_str_start_with(s, CONFIRM_IMAGE);
    int control = s && tk_str_start_with(s, DROPDOWN_IMAGE) && !tk_str_start_with(s, DROPDOWN_SUN);
    int paper = ipod_light(); /* Light Mode: tone_of's tones, flipped as drawn */
    unsigned tone = dark ? CONFIRM_SURFACE : flip_if(paper, tone_of(accents[preset], TONE_RED)),
             glyph = 0xffffff, light = flip_if(paper, tone_of(accents[preset], TONE_LIGHT));
    if (control && ((tone >> 16) * 299 + (tone >> 8 & 255) * 587 + (tone & 255) * 114) / 1000 >
                       GLYPH_LIGHT_MAX)
        glyph = CONFIRM_SURFACE;
    while (s && *s && *s != '/' && *s != ':') ++s;
    /* Minimal: the codec badges (img_flac, img_mp3, ...: red with the name cut out) take the
     * secondary text's grey, legible on the black list and on the selected row's off-white card. */
    if (s && !*s && tk_str_start_with(name, "img_") && tk_strcmp(name, "img_delete") &&
        tk_strcmp(name, "img_edit") && tk_strcmp(name, "img_morefolder") &&
        tk_strcmp(name, "img_path") && !dark && !ipod_classic())
        tone = SUDO_MUTED;
    /* Minimal: the settings icons' coloured discs all turn MINIMAL_CHEVRON, like list_into's ink
     * and stock's grey list discs. The Bluetooth rows' white marks (device, refresh) and
     * list_delete (paint_radio's) take list_into's ink too under Minimal's own accent, or they
     * vanish on the selected row's off-white card; another accent's card keeps them white. A
     * pixel's saturation (max - min) against the disc's (the icon's most saturated) says how far it
     * blends towards the white glyph, so edges stay smooth; alpha kept. */
    int into = s && !*s && minimal_card() &&
               (!tk_strcmp(name, "list_into") || !tk_strcmp(name, "bt_lefticon") ||
                !tk_strcmp(name, "bt_refresh") || !tk_strcmp(name, "list_delete"));
    int icon = s && !*s &&
               (settings_icon(name) || tk_str_start_with(name, "local_") ||
                (tk_str_start_with(name, "list_") && !into) || tk_str_start_with(name, "small_") ||
                tk_str_start_with(name, "player_"));
    int mono = s && !*s && !ipod_classic() &&
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
    unsigned char *px = (icon || into) && format < 4 && !ipod_classic()
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
                    int ink = disc ? (255 - (MINIMAL_CHEVRON & 255)) * sat / disc : 0;
                    p[o[0]] = p[o[1]] = p[o[2]] =
                        (unsigned char)(!icon    ? MINIMAL_CHEVRON & 255
                                        : square ? (MINIMAL_CHEVRON & 255) + ink
                                                 : 255 - ink);
                }
        bitmap_unlock_buffer(bitmap);
    }
    int mapped = 0; /* the accent changed a pixel: its colours are final */
    if ((preset != CRIMSON || dark || paper) && format < 4 && s && !*s && !settings_icon(name) &&
        (ipod_classic() || (!icon && !into)))
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
    unsigned char *app = format < 4 && !ipod_classic() && addon_is_icon(name) && I(bitmap, 0) > 0 &&
                                 I(bitmap, 0) <= 4096 && I(bitmap, 4) <= 4096 &&
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
    /* Light Mode: a theme image without colour that is mostly glyph (marks, chevrons, spinners)
     * flips as the colours do. One with a grey or dark disc or plate (LIGHT_SURFACE) keeps its
     * white glyph, an image the accent mapped is final, and switch_off keeps its white knob as
     * switch_on does. Quick Settings' inactive discs (DROPDOWN_IMAGE, unmapped) flip whole: a
     * light disc under a dark glyph, as the active ones stand out in the accent. Premultiplied
     * pixels (bitmap_t flags @0xc) flip their straight colour. */
    unsigned char *ink = paper && !mapped && format < 4 && s && !*s && tk_strcmp(name, "switch_off")
                             ? bitmap_lock_buffer_for_write(bitmap)
                             : (void *)0;
    if (ink) {
        const unsigned char *o = at[format];
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
                    p[o[c]] = (unsigned char)(premul ? flip_v(p[o[c]] * 255u / a) * a / 255
                                                     : flip_v(p[o[c]]));
        bitmap_unlock_buffer(bitmap);
    }
    return stock_image_trampoline(manager, name, bitmap);
}

static void setting_text(int i) {
    static const char *const home[] = { "Home: Split", "Home: Full" },
                             *const battery[] = { "Battery: Icon", "Battery: Percent",
                                                  "Battery: Icon + Percent",
                                                  "Battery: Percent Inside" },
                             *const theme[] = { "Theme: Minimal", "Theme: Classic" },
                             *const light[] = { "Light Mode: Off", "Light Mode: On",
                                                "Light Mode: Auto" };
    accent(); /* reads the settings */
    const char *const names[] = { accent_names[st.classic ? st.accent : st.minimal_accent],
                                  home[st.home_full],
                                  battery[st.battery],
                                  theme[st.classic],
                                  (const char *)0,
                                  (const char *)0,
                                  light[st.light] };
    char shortcut[64];
    if (i == 4) {
        tk_snprintf(shortcut, sizeof shortcut, "Shortcut: %s", ipod_home_shortcut());
        widget_set_text_utf8(st.setting_label[i], shortcut);
        return;
    }
    if (i == 5) {
        list_toggle(st.setting_label[i], "Rounded Corners", st.rounded);
        return;
    }
    widget_set_text_utf8(st.setting_label[i], names[i]);
}

static int settings_list(void *w) {
    return w && !tk_strcmp(widget_get_type(w), "list_view") && !I(w, ROW_HEIGHT) &&
           I(w, LIST_DEFAULT_ITEM_HEIGHT) == SET_ROW;
}

/* Cached pages behind Display must take the new inset before their return transition. */
static void settings_layout(void *w) {
    if (!w) return;
    if (settings_list(w)) {
        int y = ipod_classic() ? 0 : SET_TOP;
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
static int setting_click(void *ctx, void *event) {
    int i = (int)(long)ctx, minimal = !i && !ipod_classic();
    int *value = minimal ? &st.minimal_accent : setting_values[i];
    const char *key = minimal ? "MINIMAL_ACCENT" : setting_keys[i];
    /* Rockbox, Favorites, Shuffle, Rated Albums, Artists, Albums, Games */
    static const int shortcut_next[] = { 3, 2, 6, 5, 1, 4, 0 };
    /* Graphite, ..., Plum, Green, then Ivory in Minimal or Graphite in Classic */
    static const int accent_next[] = { 1, 2, 3, 4, 5, 6, 8, 0, MINIMAL_ACCENT };
    _Static_assert(sizeof accent_next / sizeof *accent_next == ACCENT_N, "one per ACCENTS row");
    if (i == 4)
        *value = shortcut_next[shortcut()];
    else if (i)
        *value = (*value + 1) % setting_counts[i];
    else
        *value = accent_next[*value] == MINIMAL_ACCENT && !minimal ? 0 : accent_next[*value];
    write_int_config(*value, "IPOD", key);
    if (i == 6) { /* Auto applies from this click */
        int buf[11];
        st.light_day = light_day(clock_now(buf));
    }
    if (i == 2)
        widget_invalidate_force(*(void *const *)system_bar, (void *)0); /* bar_sync applies it */
    else if (i == 1 || i == 4)
        coverflow_home_layout();
    else if (i == 5)
        widget_invalidate_force(window_manager(), (void *)0);
    else {
        if (i == 3) {
            coverflow_home_layout();
            settings_layout(window_manager());
            np_theme();
            keep_selected(st.setting_label[3]);
            setting_text(0);
            setting_text(1);
        }
        np_fill(0);
        image_manager_unload_all(image_manager());
        widget_invalidate_force(window_manager(), (void *)0);
    }
    setting_text(i);
    return 0;
}

#else
#define np_cancel() ((void)0)
/* playing_page_init: Stock's page as built, with the heart's rating hold (rate_bind). */
int ringnav_playing(void *win, void *ctx) {
    int result = stock_playing_trampoline(win, ctx);
    if (win) rate_bind(win);
    return result;
}
#endif

/* Now Playing's info page: stock (0x52a508, from its one call, NP_INFO_CALL in tools/build.py)
 * writes "Label:value" lines and the folder alone. Each line gets ": ", a number its unit's space
 * ("35.92 MB", "16-bit", "950 kbps", "44.1 kHz"), the folder its Location label, and an unknown
 * value, en_US's first player_unknown ("Unknown Artist"), reads "Unknown". */
int ringnav_np_info(void *win) {
    static const char *const names[] = { "label_album", "label_genre",  "label_size",
                                         "label_year",  "label_encode", "label_bits",
                                         "label_kbps",  "label_sample", "label_location" },
                             *const units[] = {
                                 "", "", " MB", "", "", "-bit", " kbps", " kHz", ""
                             };
    int result = np_info(win);
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
        tk_snprintf(out, sizeof out, "%.*s: %.*s%s", n, name, split ? len - u : len, v,
                    split ? units[i] : "");
        if (strcmp(out, s)) widget_set_text_utf8(label, out);
    }
    return result;
}

/* The Display sub-page the next display_page is to be (DISPLAY_*), 0 for Display itself. */
enum { DISPLAY_APPEARANCE = 1, DISPLAY_COVERFLOW, DISPLAY_CONTROLS };
static int display_sub __attribute__((section(".scratch")));
static int display_open(void *ctx, void *event) {
    display_sub = (int)(long)ctx;
    navigator_to("systemset/display_page");
    display_sub = 0;
    return 0;
}

/* systemset_display_page_init: stock builds its three rows (0x4c19bc: a s_listitem_black list_item
 * holding a 335x70 s_btn_listitem button with a 52px icon, a 24px label at x 72 and list_into);
 * Appearance (iPod), Coverflow and Controls follow with the same widgets and styles, each opening
 * stock's page again (display_open) with its stock rows dropped and its own instead: Theme,
 * Accent, Corners, Home and Battery; Artist Albums, Coverflow Sort and Refresh Coverflow; Shortcut
 * (iPod), Hold Return and Wheel Sensitivity, the last opening its slider page (crossfade.c). The
 * value rows show it in the label and have no chevron, since they change in place.
 * ponytail: stock's page opened over itself; its own rows' state is never touched on a sub-page. */
int ringnav_display(void *win, void *ctx) {
    static const char *const titles[] = { 0, "Appearance", "Coverflow", "Controls" },
                             *const sub_icons[] = { 0, "system_display", "playset_covermode",
                                                    "playset_folderjump" };
    static const char *const cf_icons[] = { "playset_covermode", "playset_covermode",
                                            "system_update" };
    static const char *const hold_icon[] = { "playset_folderjump" };
    static const char *const wheel_icon[] = { "system_display" };
    int sub = display_sub, result = stock_display_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_display", 1) : (void *)0;
    if (!view) return result;
    if (!sub) {
        for (int i = IPOD ? DISPLAY_APPEARANCE : DISPLAY_COVERFLOW; i <= DISPLAY_CONTROLS; ++i)
            widget_set_text_utf8(list_row_into(view, sub_icons[i], display_open, (void *)(long)i),
                                 titles[i]);
        return result;
    }
    widget_destroy_children(view);
    void *nav = widget_lookup(win, "view_navbar", 1), *title = nav ? widget_get_child(nav, 0) : 0;
    if (title) widget_set_text_utf8(title, titles[sub]);
    if (sub == DISPLAY_COVERFLOW) {
        pod_rows(win, "scroll_view_display", POD_ARTIST_CF, 1, cf_icons);
        pod_rows(win, "scroll_view_display", POD_CF_SORT, 2, cf_icons + 1);
        return result;
    }
#if IPOD
    /* Theme, Light Mode, Accent, Corners, Home, Battery; Shortcut. */
    static const unsigned char appearance[] = { 3, 6, 0, 5, 1, 2 }, controls[] = { 4 };
    static const char *const icons[] = { "system_display",      "playset_covermode",
                                         "system_powermanager", "system_display",
                                         "playset_covermode",   "system_display",
                                         "system_display" };
    const unsigned char *rows = sub == DISPLAY_APPEARANCE ? appearance : controls;
    for (int k = 0, n = sub == DISPLAY_APPEARANCE ? 6 : 1; k < n; ++k) {
        int i = rows[k];
        st.setting_label[i] = list_row(view, icons[i], setting_click, (void *)(long)i);
        setting_text(i);
    }
#endif
    if (sub != DISPLAY_CONTROLS) return result;
    pod_rows(win, "scroll_view_display", POD_HOLD_HOME, 1, hold_icon);
    pod_rows(win, "scroll_view_display", POD_WHEEL_QS, 1, wheel_icon);
    wheel_row(list_row_into(view, "system_display", xfade_open, (void *)1));
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
int ringnav_systemset(void *win, void *ctx) {
    int result = stock_systemset_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_sysset", 1) : (void *)0;
    if (view)
        widget_set_text_utf8(list_row_into(view, "system_update", shelf_open, 0), "App Store");
    if (view && rockbox_available()) {
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
    /* Without Rockbox there is no Boot to (13): the order less it. */
    unsigned char order[sizeof sysset_order];
    unsigned n = 0, rows = view ? widget_count_children(view) : 0;
    for (unsigned i = 0; i < sizeof sysset_order; i++)
        if (sysset_order[i] < rows) order[n++] = sysset_order[i];
    reorder(view, order, n);
    return result;
}

#if IPOD
static int untouch(const void *info) {
    st.untouch_timer = 0;
    st.touch_mode = 0;
    void *top = window_manager_get_top_window(window_manager());
    if (top) widget_invalidate_force(top, (void *)0);
    return 0;
}
#endif

static void hide_outline(void) {
    if (st.touch_mode && !st.untouch_timer) return; /* already hidden: each touch move comes here */
    stop_timer(&st.untouch_timer);
    st.touch_mode = 1;
    void *top = window_manager_get_top_window(window_manager());
    if (top) widget_invalidate_force(top, (void *)0);
}

int ringnav_touch(void *ctx, void *event) {
#if IPOD
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
#endif
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
#if IPOD
    if (ok && st.clock_timer) clock_show();
#endif
    if (ok && window_manager_get_top_window(window_manager()) == page) navigator_back();
    return result;
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

#if IPOD

/* Only audited ordinary row constructors install this per-instance layouter. Stock still
 * positions every child, skips hidden controls, and owns clone/destruction and text overflow. */
static int compact_row_layout(void *layout, void *row) {
    void *text = (void *)0, *title = (void *)0;
    unsigned count = widget_count_children(row);
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
        if (width < 0) width = 0;
        widget_resize(text, width, I(text, W_H));
        /* Stock's rows scroll a long title once as the page opens, every row at a time, its
         * glyphs smeared over the moving clip; focus-only (no row takes AWTK focus) keeps the
         * ellipsis, as the selected row shows it. */
        hscroll_label_set_only_focus(title, 1);
        if (title != text) {
            int available = width - I(title, W_X);
            widget_resize(title, available > 0 ? available : 0, I(title, W_H));
        }
    }
    return ((int (*)(void *, void *))((const unsigned *)DEFAULT_LAYOUT_VTABLE)[2])(layout, row);
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

/* Settings rows (docs/ipod.md#settings). One stock child in row coordinates: full-height children
 * fill the row and shorter ones keep their centre; the icon shrinks to SET_ICON; text starts at
 * the corner-safe column; children from SET_RIGHT_SIDE, and wide ones' right edges, move with the
 * trailing image by `shift`, never past the text margin. */
static void set_child(void *c, int row_w, int row_h, int shift) {
    int x = I(c, W_X), y = I(c, W_Y), w = I(c, W_W), h = I(c, W_H), right = x + w;
    if (h >= SET_STOCK_BODY) {
        y = 0;
        h = row_h;
    } else
        y -= (SET_STOCK_BODY - row_h) / 2;
    if (w == SET_STOCK_ICON && !tk_strcmp(widget_get_type(c), "image")) {
        /* the build pre-sizes the audited icons to SET_ICON, so they draw 1:1; any other one scales
         * down */
        image_set_draw_type(c, IMAGE_DRAW_SCALE_DOWN);
        widget_move_resize(c, SET_ICON_X, (row_h - SET_ICON) / 2, SET_ICON, SET_ICON);
        return;
    }
    int left = x >= SET_RIGHT_SIDE ? SET_STOCK_X + x + shift
               : x > SET_STOCK_ICON / 2 + 10 /* after a stock icon at x 10 */
                   ? SET_ICON_X + SET_ICON + SET_GAP + x - (SET_STOCK_ICON + 20)
                   : SET_TEXT_X + x - 10;
    right = right >= SET_RIGHT_SIDE ? SET_STOCK_X + right + shift : right + left - x;
    if (right > row_w - SET_TEXT_X) right = row_w - SET_TEXT_X;
    widget_move_resize(c, left, y, right > left ? right - left : 0, h);
}

/* The row's stock settings button (SET_STOCK_X, SET_STOCK_W), or none: a row already mapped, or one
 * another builder made, which keeps its own height and geometry. */
static void *set_button(void *item) {
    if (tk_strcmp(widget_get_type(item), "list_item")) return (void *)0;
    for (unsigned i = 0; i < widget_count_children(item); ++i) {
        void *b = widget_get_child(item, i);
        if (I(b, W_X) == SET_STOCK_X && I(b, W_W) == SET_STOCK_W &&
            !tk_strcmp(widget_get_type(b), "button"))
            return b;
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
    }
}

/* The list_view layouter's vtable slot. A list whose asset default_item_height is SET_ROW (only
 * the iPod settings pages) gets settings rows: items stock made SET_STOCK_ROW high take SET_ROW
 * before the stock layout stacks them and sizes the scroll view, then each row is mapped. Settings
 * code never moves, resizes or scrolls its rows afterwards (docs/ipod.md#settings). */
int ipod_list_layout(void *layout, void *view) {
    void *list = view ? P(view, W_PARENT) : (void *)0;
    int rows = settings_list(list);
    if (rows) settings_layout(list);
    unsigned n = rows ? widget_count_children(view) : 0;
    for (unsigned i = 0; i < n; ++i) {
        void *item = widget_get_child(view, i);
        if (I(item, W_H) == SET_STOCK_ROW && set_button(item)) I(item, W_H) = SET_ROW;
    }
    int ret = ((int (*)(void *, void *))LIST_VIEW_LAYOUT)(layout, view);
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
#endif

/* Called by stock long Return (both builds) and short Return on iPod Home, after their gates.
 * Elsewhere it opens Now Playing over the page, remembered as np_origin; on Now Playing it goes
 * back to that page while it lies right under it, else Home. With Hold Return: Home it goes Home
 * from any page but Home itself, where it still opens Now Playing.
 * ponytail: going Home leaves the latch to the stock release filter, as stock's own Home call
 * here did, relying on the hold's key-up reaching it once the windows close; clearing it would
 * let that release act on Home. */
int compact_now_playing(void) {
    pull_cancel();
    drop_input();
    void *wm = window_manager();
    void *top = window_manager_get_top_window(wm);
    fx_cancel();
    if (usable() && top && !window_manager_is_animating(wm)) {
        const char *name = widget_get_prop_str(top, "name", "");
        pod_settings();
        if (st.hold_home && tk_strcmp(name, "home_page")) {
            st.np_origin = (void *)0;
            navigator_back_to_home();
        } else if (!tk_strcmp(name, "playing_page")) {
            /* ponytail: np_origin is only compared, never followed; a reused address goes Back
             * instead of Home */
            unsigned n = st.np_origin ? widget_count_children(wm) : 0;
            int back = n >= 2 && widget_get_child(wm, n - 1) == top &&
                       widget_get_child(wm, n - 2) == st.np_origin;
            st.np_origin = (void *)0;
            if (back)
                navigator_back();
            else
                navigator_back_to_home();
        } else {
            static const int context[4] = { 0, 0, 0xff, 2 };
            st.np_origin = top;
            navigator_switch_to_with_context("playing_page", context, 0);
            /* The window switch stops the hold's key-up from reaching the stock release
             * filter, so the latch it armed would otherwise swallow the next Return. Drop it
             * once the switch really landed, so the first short Return goes back. */
            void *now = window_manager_get_top_window(wm);
            if (now && !tk_strcmp(widget_get_prop_str(now, "name", ""), "playing_page"))
                *(volatile unsigned char *)RETURN_RELEASE_LATCH = 0;
        }
    }
    return 0;
}

/* Play/Pause hold queue menu (docs/internals.md). Stock long press fires once per press, so a hold
 * on a local song, album, artist/composer/genre or folder row opens the stock sortselect dialog
 * rebuilt as that row's menu. */
enum { QM_SONG = 1, QM_ALBUM, QM_GROUP, QM_FOLDER, QM_COVERFLOW, QM_COVERALBUM, QM_PLAYING };
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
    QA_CHAPTERS
};
#define MCL(a) (*(volatile int *)(a))

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

static unsigned rec_hash(void *r) {
    unsigned h = FNV_SEED;
    for (int o = REC_NAME; o <= REC_ARTIST; o += 4) h = fnv(h, P(r, o));
    return hash_bytes(h, (const unsigned char *)r + REC_CUE_START, 8);
}

/* Everything a row's tracks are resolved from; a change while the menu is open cancels it. */
static unsigned browse_hash(void) { return local_hash(hash_bytes(FNV_SEED, g_folder_path, 1024)); }

/* Coverflow's or Most Played's tracks, when page is theirs. */
static void *page_tracks(void *page) {
    return !page                ? (void *)0
           : page == st.mp_page ? st.mp_list
           : page == st.rt_page ? st.rt_list
                                : coverflow_tracks(page);
}

unsigned queue_hash(void *q) {
    unsigned h = FNV_SEED;
    for (unsigned i = 0, n = deque_size(q); i < n; ++i) {
        unsigned r = rec_hash(deque_at(q, i));
        h = hash_bytes(h, (const unsigned char *)&r, sizeof r);
    }
    return h;
}

static void *qm_list(void) {
    if (st.qm_kind == QM_PLAYING) return P(mcl_pdeqplaylist, 0);
    return st.qm_kind == QM_COVERFLOW ? page_tracks(window_manager_get_top_window(window_manager()))
                                      : P(p_deque_showlist, 0);
}

static void *qm_record(void) {
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
static unsigned qm_album_id(void *r) {
    if (st.qm_kind == QM_COVERALBUM || (st.qm_kind == QM_ALBUM && st.qm_cls == CLASS_ALBUMS))
        return album_id(r);
    return st.qm_kind == QM_ALBUM && st.qm_cls == CLASS_ARTIST_ALBUMS && st.art_ids &&
                   st.art_n == st.qm_rows && st.qm_idx < st.art_n
               ? st.art_ids[st.qm_idx]
               : 0;
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
 * batch_add_file (0x4f4be8) expands each class. Artist 0xf004/0xff01 rows name the artist at +0x18,
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

static int qm_query(void *r) { return class_query(st.qm_cls, r); }

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
/* The Albums row renderer's snprintf(buf, 0x100, "%s", album) (0x4a388c), a2 the row. */
int ringnav_art_name(char *buf, unsigned size, unsigned row, const char *album) {
    char key[256];
    void *list = P(p_deque_showlist, 0);
    return snprintf(buf, size, "%s", row < deque_size(list) ? art_key(key, list, row) : album);
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
/* The artist page Coverflow is about to cover opens unseen and without its slide, so the Artists
 * list cuts straight to the covers, as Home's Coverflow card does; once they are up it is shown
 * again under them, its slide back for its own Return. */
static void artist_cf_veil(void *win, int on) {
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
    if (veiled) artist_cf_veil(veiled, 0);
    return 0;
}
/* Artist albums: Grid gives the artist page its stock toolbar back, for its multi-select
 * (s_img_mulsel), which iPod hides on the local pages; the tabs move down under it. A toolbar
 * already showing (Legacy, a reload) is left alone. */
static void artist_bar(void) {
    void *top = window_manager_get_top_window(window_manager()),
         *nav = top ? widget_lookup(top, "view_navbar", 0) : 0,
         *tab = top ? widget_lookup(top, "tab_control", 0) : 0,
         *pages = tab ? widget_lookup(tab, "pages", 0) : 0, *album;
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
        artist_cf_veil(top, 1);
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
/* The artist page's albums renderer's snprintf of the name (0x4ab804), a2 the row. */
int ringnav_artist_art_name(char *buf, unsigned size, unsigned row, const char *album) {
    const char *k = artist_key(0, P(p_deque_showlist, 0), row);
    return snprintf(buf, size, "%s", k ? k : album);
}
/* The artist page's albumcoverinfo_init calls. */
int ringnav_artist_covers(void *list) {
    return st.art_keys ? rename_covers(list, artist_key) : albumcoverinfo_init(list);
}
/* The album page's big cover, snprintf of classinfo +0xd (0x4a72a4): its Albums row's cover task
 * name, as the thread hashed it, when that is this album's key. */
int ringnav_art_header(char *buf, unsigned size, const char *fmt, const char *album) {
    void *covers = P(pdeq_albumcoverlist, 0);
    unsigned i = (unsigned)I(ALBUMINFO_ROW, 0), n = strlen(album);
    const char *key = i < deque_size(covers) ? P(deque_at(covers, i), 4) : 0;
    return snprintf(buf, size, fmt,
                    key && !strncasecmp(key, album, n) && key[n] == 0x1f ? key : album);
}

static int empty_toast(const void *unused) {
    static const char *const none[] = { "No albums", "No artists", "No composers", "No genres" };
    toast(none[st.empty_kind]);
    return 0;
}

/* Library Artists rows have no head picture, so the name gets its width (compact_row_layout skips
 * hidden siblings). Composers and Genres share the rows, which are rebound, so they show theirs
 * again. */
int ringnav_class_icon(void *row, int i) {
    int artists = I(g_class_type, 0) == 0xf004;
    widget_set_visible(widget_lookup(row, "img_icon", 1), !artists, 0);
    return artists ? 0 : class_icon(row, i);
}

/* load_localclass_list: stock fills p_deque_showlist and returns its size. getAllAlbum,
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
static unsigned listen_key(void *r);
/* A deque's songs by path and CUE start (listen_key), open addressing at most half full: slot j + 1
 * for record j, so a key's first record in deque order is met first. calloc'd, 0 if that fails. */
static unsigned *rec_index(void *songs, unsigned *mask) {
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
static void *rec_find(void *songs, const unsigned *slot, unsigned mask, void *r) {
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
static int songs_unsorted(void *);
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
/* Playlists bypass songtable; filter their displayed copies, never their stored rows. */
static int music_records(void *list) {
    void *music = _create_deque("stSongInfo");
    deque_init(music);
    for (unsigned i = 0; i < deque_size(list); ++i) {
        void *r = deque_at(list, i);
        if (I(r, REC_TYPE) != 8 || !spoken(P(r, REC_PATH))) _deque_push_back(music, r);
    }
    deque_clear(list);
    deque_assign(list, music);
    deque_destroy(music);
    return (int)deque_size(list);
}
int ringnav_localclass(int cls) {
    I(g_artist_type, 0) = I(artist_type, 0) == 1;
    int saved = library_music;
    library_music = 1;
    int n = stock_localclass_trampoline(cls);
    void *list = P(p_deque_showlist, 0), *dir = P(tools_pdeq_directory, 0), *a = album_opened();
    if (n > 0 && cls == 0xff20) n = music_records(list);
    if (n > 0 && cls == 0xff10 && a) album_only(list, a, 0), n = (int)deque_size(list);
    if (n > 0 && cls == CLASS_ALBUMS) n = coverflow_split(list);
    if (n > 0 && (cls == 0xf00a || cls == 0xf008 || cls == 0xf007)) /* My Fav, Frequent, Recent */
        fav_titles(list, n);
    if (IPOD) /* an untagged song's row reads its file name without the extension (track_name) */
        for (int i = 0; i < n; ++i) {
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
        class_query((unsigned)cls, deque_at(list, (unsigned)n - 1));
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
        folder_tracks(path, add, 0);
        return;
    }
    void *rows = staged_music(qm_query, r, &n);
    for (unsigned i = 0; i < deque_size(rows); ++i) {
        void *t = deque_at(rows, i);
        if (I(t, REC_TYPE) == 8) _deque_push_back(add, t);
    }
    deque_destroy(rows);
}

/* The row's tracks: the song itself, or what stock would play for the row; an Albums row's own
 * album alone (getMusicByAlbum finds every album of the name), in album order, as Coverflow's. */
static void *qm_collect(void *r) {
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
        st.qm_forced = MCL(MCL_MODE) == 2 ? at + 1 : 0;
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

void toast(const char *text) {
    struct {
        int kind, ms;
        char text[0x400];
    } info = { 1, 2000, { 0 } };
    for (unsigned i = 0; text[i] && i < sizeof info.text - 1; ++i) info.text[i] = text[i];
    navigator_to_with_context("dialog/msginfo_dialog", &info);
}

static int all_songs(void *music) {
    int saved = library_music;
    library_music = music != 0;
    int n = getAllMusic(0);
    library_music = saved;
    return n;
}

/* Shuffle does not need the expensive pinyin title sort; groups order their tracks themselves.
 * Non-zero music leaves audiobooks and podcasts out, as the Library's Artists and Albums
 * (MUSIC_WHERE). */
static int songs_unsorted(void *music) {
    int saved = library_music;
    library_music = music != 0;
    int n = getAllMusic(3);
    library_music = saved;
    return n;
}

/* Collection Shuffle is Shuffle Songs over the collection. */
static int shuffle_play(void *all) {
    int size = (int)deque_size(all);
    if (size) {
        int mode = playback_unshuffle();
        playback_clear(); /* stock Shuffle keeps Shuffle Albums/Folders otherwise */
        config_playmode(2, 1);
        play_folder(all, (int)rand_below((unsigned)size), 0xf001);
        playback_shuffle(mode); /* an order Up Next can list; stock's shuffle stays the fallback */
    }
    return size;
}

static int shuffle_songs(void *ctx, void *event) {
    int n;
    void *all = staged(songs_unsorted, (void *)1, &n);
    if (!shuffle_play(all)) toast("Update Local Music first");
    deque_destroy(all);
    return 0;
}

static int shuffle_groups(void *ctx, void *event) {
    int n;
    void *all = staged(songs_unsorted, (void *)1, &n);
    if (!playback_groups(all, (int)(long)ctx))
        toast(deque_size(all) ? "Too many songs to shuffle" : "Update Local Music first");
    deque_destroy(all);
    return 0;
}

/* Return and destroy for the payload's own pages, shared with Most Played; Return with
 * netupdate.c's Apps page. */
int page_keyup(void *ctx, void *event) {
    if (I(event, EVENT_KEY) != KEY_RETURN) return 0;
    navigator_back();
    return STOP;
}

static void ls_closed(void);
static int page_closed(void *ctx, void *event) {
    if (ctx == st.rt_page) {
        st.rt_page = 0;
        if (st.rt_list) deque_destroy(st.rt_list);
        st.rt_list = 0;
        ls_closed();
    }
    if (ctx == st.rt_menu) st.rt_menu = 0;
    if (ctx == st.mp_page) st.mp_page = 0;
    if (ctx == st.td_page) {
        st.td_page = 0;
        st.td_asking = 0;
        stop_timer(&st.td_timer);
        if (st.td_list) deque_destroy(st.td_list);
        st.td_list = 0;
    }
    if (ctx == st.sp_page) st.sp_page = 0;
    upnext_closed(ctx);
    if (ctx == st.ch_page) {
        st.ch_page = 0;
        if (st.ch && !st.ch->done)
            chapters_cancel(st.ch);
        else {
            free(st.ch);
            st.ch = 0;
        }
    }
    if (ctx == st.sc_page) st.sc_page = 0;
    return 0;
}

/* The Library's Shuffle row opens these three actions on one page. A pick starts playback and
 * leaves the page open, as Most Played's rows do. Album and folder shuffles queue and group the
 * whole library, hence the note. */
static int sp_pick(void *ctx, void *event) {
    if ((long)ctx)
        shuffle_groups((void *)((long)ctx - 1), 0);
    else
        shuffle_songs(0, 0);
    return 0;
}

static int shuffle_menu(void *ctx, void *event) {
    if (st.sp_page || !(st.sp_page = page_open("shuffle_page", page_closed, page_keyup))) return 0;
    void *view = page_list(st.sp_page, st.sp_page, 0, "Shuffle", 3, 48);
    page_row_detail(view, 0, "Shuffle Songs", 0, sp_pick);
    page_row_detail(view, 1, "Shuffle Albums", 0, sp_pick);
    page_row_detail(view, 2, "Shuffle Folders", 0, sp_pick);
    void *note = text(st.sp_page, 20, 200, 335, 24);
    widget_set_prop_int(note, "style:normal:text_color", (int)0xffaaaaaau);
    widget_set_prop_int(note, "style:normal:font_size", 16);
    widget_set_text_utf8(note, "Albums and folders take a moment to shuffle");
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

static void plays_load(void) {
    if (!st.plays_read) {
        st.plays_read = 1;
        BLOB_IO(PLAYS_FILE, st.plays, 0);
    }
}

/* A play count's key: the path's hash, with a CUE track's start mixed in, since a CUE image's
 * tracks share one path. Never 0, which marks a free slot. */
static unsigned listen_key(void *r) {
    unsigned key = fnv(FNV_SEED, P(r, REC_PATH));
    int cue = I(r, REC_CUE_START);
    if (cue) key = hash_bytes(key, (const unsigned char *)&cue, sizeof cue);
    return key | !key;
}

/* Most Played's row: the ranked list from that track, as a library (not folder) queue. */
static int mp_play(void *ctx, void *event) {
    play_folder(st.mp_list, (int)(long)ctx, 0xf001);
    return 0;
}

/* The PLAYS_TOP most played of songs, their indices in idx, most played first and, among equal
 * counts, most recently counted first (the table keeps that order); returns how many. */
static int mp_rank(void *songs, int *idx, unsigned *score) {
    int top = 0, size = (int)deque_size(songs);
    plays_load();
    for (int i = 0; i < size; i++) {
        unsigned key = listen_key(deque_at(songs, i)), c = 0;
        for (int j = 0; j < PLAYS_SLOTS && !c; j++)
            if (st.plays[j].key == key) c = st.plays[j].n * PLAYS_SLOTS + PLAYS_SLOTS - j;
        if (!c || (top == PLAYS_TOP && score[top - 1] >= c)) continue;
        int at = top < PLAYS_TOP ? top++ : PLAYS_TOP - 1;
        for (; at > 0 && score[at - 1] < c; at--) score[at] = score[at - 1], idx[at] = idx[at - 1];
        score[at] = c;
        idx[at] = i;
    }
    return top;
}

/* Coverflow's Most Played order: every counted song's listens added to sum[album_of(song)], the
 * album's index in Coverflow's list (-1 none). One pass over the library. */
void album_plays(int (*album_of)(void *), unsigned *sum) {
    int n;
    void *songs = staged(songs_unsorted, (void *)1, &n);
    plays_load();
    for (unsigned i = 0; i < deque_size(songs); i++) {
        void *r = deque_at(songs, i);
        unsigned key = listen_key(r), c = 0;
        for (int j = 0; j < PLAYS_SLOTS && !c; j++)
            if (st.plays[j].key == key) c = st.plays[j].n;
        int a = c ? album_of(r) : -1;
        if (a >= 0) sum[a] += c;
    }
    deque_destroy(songs);
}

/* Where key is among Most Played's tracks, -1 when not (or none are kept). */
static int mp_find(unsigned key) {
    for (unsigned i = 0; st.mp_list && i < deque_size(st.mp_list); i++)
        if (listen_key(deque_at(st.mp_list, i)) == key) return (int)i;
    return -1;
}

/* Most Played: a page of the PLAYS_TOP most played songs of the library. The whole library is read
 * only when nothing is kept; otherwise the kept tracks are ranked again.
 * ponytail: the first open after boot or a library change still reads every song, and a song first
 * counted from stock's folder browser shows without its artist until then (no library tags). */
static int most_played(void *ctx, void *event) {
    if (st.mp_page || !(st.mp_page = page_open("mostplayed_page", page_closed, page_keyup)))
        return 0;
    int n, idx[PLAYS_TOP];
    unsigned score[PLAYS_TOP], gen = library_gen;
    void *from = st.mp_list;
    if (!from || st.mp_gen != gen) {
        from = staged(all_songs, (void *)1, &n);
        st.mp_gen = gen;
    }
    int top = mp_rank(from, idx, score);
    void *ranked = _create_deque("stSongInfo");
    deque_init(ranked);
    for (int i = 0; i < top; i++) _deque_push_back(ranked, deque_at(from, idx[i]));
    if (st.mp_list && st.mp_list != from) deque_destroy(st.mp_list);
    deque_destroy(from);
    st.mp_list = ranked;
    void *view =
        page_list(st.mp_page, st.mp_page, 0, top ? "Most Played" : "No plays yet", top, 64);
    char name[512], detail[300];
    for (int i = 0; i < top; i++) { /* the artist, if tagged, and the play count, under the title */
        void *t = deque_at(st.mp_list, (unsigned)i);
        const char *artist = P(t, REC_ARTIST);
        unsigned plays = (score[i] - 1) / PLAYS_SLOTS, a = artist && *artist;
        tk_snprintf(detail, sizeof detail, a ? "%s · %u play%s" : "%s%u play%s", a ? artist : "",
                    plays, plays == 1 ? "" : "s");
        page_row_detail(view, i, track_name(name, sizeof name, t), detail, mp_play);
    }
    return 0;
}

/* M4B chapters are read only while this snapshot is open. */
static int chapter_file(const char *path) {
    const char *ext = path ? strrchr(path, '.') : 0;
    return ext && !strcasecmp(ext, ".m4b");
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
#if IPOD
        st.scrub_track = st.ch_track;
        st.scrub_to = st.ch->rows[i].sec;
        st.scrub = st.scrub_moved = 1;
        if (st.np_win) playing_timer_clear(st.np_win);
        if (st.np_slider) widget_set_prop_int(st.np_slider, "value", st.scrub_to);
        if (st.np_elapsed) clock_text(st.np_elapsed, 0, st.scrub_to);
        scrub_end();
#else
        seek_position(st.ch->rows[i].sec);
#endif
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
    int n = st.ch->failed ? 0 : st.ch->count;
    void *view = page_list(st.ch_page, st.ch_page, 0,
                           st.ch->failed ? "Could not read chapters"
                           : n           ? "Chapters"
                                         : "No embedded chapters",
                           n, 64);
    int sec = 0, total, current = 0;
    player_playtime_and_length(&sec, &total);
    for (int i = 0; i < n; ++i) {
        char name[32], at[32];
        chapter *c = &st.ch->rows[i];
        tk_snprintf(name, sizeof name, "Chapter %d", i + 1);
        tk_snprintf(at, sizeof at, "%d:%02d:%02d", c->sec / 3600, c->sec / 60 % 60, c->sec % 60);
        page_row_detail(view, i, *c->title ? c->title : name, at, chapter_pick);
        if (c->sec <= sec) current = i;
    }
    if (n) ringnav_select(view, current, n);
    return 0;
}
static void chapters_open(void *r) {
    if (st.ch || !chapter_file(P(r, REC_PATH))) return;
    if (!mp4_fits(P(r, REC_PATH))) {
        toast("Too long to read chapters on the Q2");
        return;
    }
    st.ch_track = np_track();
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

/* Songs on the card, played from it as a folder queue, without Wi-Fi: Tidal Downloads
 * (docs/internals.md#tidal-cache), Streaming's row listing tidal.c's complete downloads, and an
 * add-on's local rows (songs_open: q2-deezer's downloads). A folder lists its songs newest first,
 * or, an album's (q2-deezer's Artist/Album folders, their files "01 - Title [id].flac"), by name:
 * in track order. ponytail: newest download first, not last played; demo imports no utime to touch
 * a replay. */
typedef struct {
    long mtime;
    void *r;
} td_row;
static int td_newer(const void *a, const void *b) {
    long x = ((const td_row *)a)->mtime, y = ((const td_row *)b)->mtime;
    return (y > x) - (y < x);
}
static int td_named(const void *a, const void *b) {
    const char *x = P(((const td_row *)a)->r, REC_NAME), *y = P(((const td_row *)b)->r, REC_NAME);
    return strcmp(x ? x : "", y ? y : "");
}
static int td_play(void *ctx, void *event) {
    void *t = deque_at(st.td_list, (unsigned)(long)ctx);
    if (t && !access(P(t, REC_PATH), 0)) play_folder(st.td_list, (int)(long)ctx, 0xf001);
    return 0;
}
/* dir's songs, newest first (or, album, by name), a deque the caller destroys. */
static void *card_songs(const char *dir, int album) {
    int n = 0;
    void *rows = staged(directory, (void *)dir, &n);
    unsigned size = deque_size(rows), info[36];
    td_row *v = calloc(size + 1, sizeof *v);
    n = 0;
    for (unsigned i = 0; v && i < size; i++) { /* songs only: .tmp, .part, folders aren't audio */
        void *r = deque_at(rows, i);
        if (I(r, REC_TYPE) == 8 && P(r, REC_PATH) && !__xstat(3, P(r, REC_PATH), info))
            v[n++] = (td_row){ (long)info[16], r };
    }
    if (n) qsort(v, (unsigned)n, sizeof *v, album ? td_named : td_newer);
    void *songs = _create_deque("stSongInfo");
    deque_init(songs);
    for (int i = 0; i < n; i++) _deque_push_back(songs, v[i].r);
    free(v);
    deque_destroy(rows);
    return songs;
}
/* A card app's stream folder's songs in play order (addon.c stream_poll), a deque the caller
 * destroys. */
void *app_album_songs(const char *dir) { return card_songs(dir, 1); }

/* A song's label from its file: Tidal's after its "<id>-<quality> ", a card app's before a
 * trailing " [<id>]" (q2-deezer's, as OpenDeezer names them), either less its extension. */
static void song_label(const char *f, int tidal, char *out, unsigned size) {
    const char *sp = tidal && f ? strstr(f, " ") : 0;
    tk_snprintf(out, size, "%s", sp ? sp + 1 : f ? f : "");
    char *dot = strrchr(out, '.'), *id;
    if (dot && dot != out) *dot = 0;
    if (!tidal && (id = strrchr(out, '[')) && id > out + 1 && id[-1] == ' ' &&
        id[strlen(id) - 1] == ']')
        id[-1] = 0;
}
static int td_show(const void *unused);
/* Clear All Downloads…, then on its confirmation Cancel or Remove (row 2: tidal_clear), each
 * through td_show, from a timer, as a row's click may not rebuild the list it is in. */
static int td_clear(void *ctx, void *event) {
    if (st.td_asking && (long)ctx == 2) tidal_clear();
    st.td_asking = !st.td_asking;
    rearm(&st.td_timer, td_show, 0);
    return 0;
}
/* The page's songs, read again; Tidal's end with Clear All Downloads…, as Spotify's Storage
 * Management does. Returns the list. */
static void *songs_rows(const char *dir, const char *title, int tidal, int album) {
    if (st.td_list) deque_destroy(st.td_list);
    st.td_list = card_songs(dir, album);
    int n = (int)deque_size(st.td_list);
    void *view =
        page_list(st.td_page, st.td_page, 0, n ? title : "No downloads yet", n + (tidal && n), 48);
    char name[256];
    for (int i = 0; i < n; i++) {
        song_label(P(deque_at(st.td_list, (unsigned)i), REC_NAME), tidal, name, sizeof name);
        page_row_detail(view, i, name, 0, td_play);
    }
    if (tidal && n) page_row_detail(view, n, "Clear All Downloads…", 0, td_clear);
    return view;
}
/* Clear All Downloads' confirmation in the list's place, as Spotify's Remove Downloads?, on Cancel;
 * or the list again, on its Clear row. */
static int td_show(const void *unused) {
    st.td_timer = 0;
    if (!st.td_page) return 0;
    unsigned n = deque_size(st.td_list);
    if (!st.td_asking) {
        void *view = songs_rows(TIDAL_DIR, "Tidal Downloads", 1, 0);
        if ((n = deque_size(st.td_list))) ringnav_select(view, (int)n, (int)n + 1);
        return 0;
    }
    char note[32];
    tk_snprintf(note, sizeof note, n == 1 ? "%u track" : "%u tracks", n);
    void *view = page_list(st.td_page, st.td_page, 0, "Remove Downloads?", 3, 64);
    page_row_detail(view, 0, note, "Keeps settings", 0);
    page_row_detail(view, 1, "Cancel", "", td_clear);
    page_row_detail(view, 2, "Remove", "", td_clear);
    ringnav_select(view, 1, 3);
    return 0;
}
/* Return on the confirmation is its Cancel. */
static int td_keyup(void *ctx, void *event) {
    if (!st.td_asking || I(event, EVENT_KEY) != KEY_RETURN) return page_keyup(ctx, event);
    td_clear((void *)1, event);
    return STOP;
}
static int songs_page(const char *window, const char *dir, const char *title, int tidal,
                      int album) {
    if (st.td_page || !(st.td_page = page_open(window, page_closed, tidal ? td_keyup : page_keyup)))
        return 0;
    songs_rows(dir, title, tidal, album);
    return 0;
}
static int tidal_downloads(void *ctx, void *event) {
    (void)ctx, (void)event;
    return songs_page("tidal_downloads", TIDAL_DIR, "Tidal Downloads", 1, 0);
}
/* A card app's local row (addon.c): a folder ("/" last) lists its songs; a song plays its folder
 * from it, as Tidal Downloads does. album: the folder is an album's, listed and played in track
 * order. */
int songs_open(const char *path, const char *title, int album) {
    char dir[256];
    unsigned k = strlen(path);
    if (!k || k >= sizeof dir) return 0;
    tk_snprintf(dir, sizeof dir, "%s", path);
    if (dir[k - 1] == '/') {
        if (k > 1) dir[k - 1] = 0;
        return songs_page("app_songs", dir, title, 0, album);
    }
    char *slash = strrchr(dir, '/');
    if (!slash || slash == dir) return 0;
    *slash = 0;
    void *songs = card_songs(dir, album);
    int at = -1;
    for (unsigned i = 0; at < 0 && i < deque_size(songs); i++) {
        const char *p = P(deque_at(songs, i), REC_PATH);
        if (p && !strcmp(p, path)) at = (int)i;
    }
    if (at >= 0)
        play_folder(songs, at, 0xf001); /* playing_page copies the queue at once */
    else
        toast("It isn't on the card any more");
    deque_destroy(songs);
    return 0;
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

static void sc_text(int i) {
    static const char *const sizes[] = { "512 MB", "1 GB", "2 GB", "4 GB", "8 GB" };
    char s[40];
    tk_snprintf(s, sizeof s, "%s Cache: %s", i ? "Spotify" : "Tidal", sizes[stream_cache(i)]);
    widget_set_text_utf8(widget_get_child(st.sc_row[i], 0), s);
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
static void svc_hidden(char *s) {
    toolsReadConfig("/mnt/data/config.ini", "Q2POD", "STREAMHIDE", s, "");
}
static char *svc_find(char *hide, int i) {
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
    void *view = widget_lookup(window_manager(), "scroll_view_streamsset", 1);
    for (unsigned c = 0, rows = view ? widget_count_children(view) : 0; c < rows; ++c)
        if (widget_get_prop_int(widget_get_child(view, c), SVC_PROP, 0) == i + 1)
            widget_set_visible(widget_get_child(view, c), !hidden, 0);
    if (view) widget_layout(view);
    return 0;
}

/* A cache row steps to the next size and saves it: Tidal's applies at the next download's trim,
 * Spotify's when librespot next starts. Stop Streaming Services ends librespot and its restart
 * loop, a radio station, the card apps (addon.c) and a stock stream (Tidal's) queue, so the keys
 * and the output are local music's again; local music already stopped is left as it is. */
static int sc_click(void *ctx, void *event) {
    int i = (int)(long)ctx;
    if (i > 3) return svc_toggle(i - 4);
    if (i == 2) {
        unsigned type = (unsigned)MCL(MCL_TYPE);
        spot_stop();
        radio_stop();
        addon_stop();
        if (type != 1 && (type & 0xf000) != 0xf000 && mclGetPlayStatus() != 1) player_stop();
        toast("Streaming stopped");
        return 0;
    }
    char path[48], tmp[52], s[8];
    int n = tk_snprintf(s, sizeof s, "%uM", 512u << (stream_cache(i) + 1) % 5);
    tk_snprintf(path, sizeof path, "%s/.cache-size", sc_dirs[i]);
    tk_snprintf(tmp, sizeof tmp, "%s.tmp", path);
    mkdir(sc_dirs[i], 0755);
    if (!blob_io(path, tmp, s, (unsigned)n, 1)) toast("Could not save setting");
    sc_text(i);
    return 0;
}

static int stream_config(void *ctx, void *event) {
    if (st.sc_page || !(st.sc_page = page_open("streamconf_page", page_closed, page_keyup)))
        return 0;
    int n = SVC_FIXED + st.svc_n;
    void *view = page_list(st.sc_page, st.sc_page, 0, "Configuration", 4 + n, 48);
    for (int i = 0; i < 2; ++i)
        st.sc_row[i] = page_row_detail(view, i, "", 0, sc_click), sc_text(i);
    page_row_detail(view, 2, "Stop Streaming Services", 0, sc_click);
    /* a gap before the toggles, clear as the rows (the theme's default list_item fills) */
    widget_use_style(list_item_create(view, 0, 3 * 48, 375, 48), "s_listitem_black");
    char hide[SVC_HIDE];
    svc_hidden(hide);
    for (int i = 0; i < n; ++i) {
        st.sc_check[i] = widget_get_child(page_row_detail(view, 4 + i, "", 0, sc_click), 0);
        list_toggle(st.sc_check[i], svc(i, 1), !svc_find(hide, i));
    }
    return 0;
}

/* Ratings (docs/internals.md#ratings): 0 to 5 hearts a song. 5 is stock's My Fav, so Now Playing's
 * heart, the queue menu's Favourites rows and Loved stay one; 1 to 4 are kept here by listen_key,
 * its low three bits holding the hearts, read once into a table calloc'd on first use. */
#define RATINGS_FILE "/mnt/data/ringnav-ratings"
/* Albums keep 1 to 5 hearts apart from their songs, by coverflow.c's album_id: 5 is the album's
 * favourite, there being no stock one. */
#define ALBUM_RATINGS_FILE "/mnt/data/ringnav-album-ratings"
/* Now Playing's heart click (playing_page_init binds it to img_fav with the window): stock's My
 * Fav toggled for the playing track (manage_myfav), and the heart's image with it. */
#define FAV_CLICK ((int (*)(void *, void *))0x52b154)

/* The songs' table, or with album the albums'. */
static int rate_io(int album, int write) {
    const char *f = album ? ALBUM_RATINGS_FILE : RATINGS_FILE;
    const char *tmp = album ? ALBUM_RATINGS_FILE ".tmp" : RATINGS_FILE ".tmp";
    return blob_io(f, tmp, album ? st.album_rates : st.rates, RATE_SLOTS * sizeof *st.rates, write);
}
static unsigned *rates(int album) {
    unsigned **t = album ? &st.album_rates : &st.rates;
    if (!*t && (*t = calloc(RATE_SLOTS, sizeof **t))) rate_io(album, 0);
    return *t;
}

/* key's slot; with free, the first free one when it has none; -1 when neither. */
static int rate_slot(int album, unsigned key, int free) {
    unsigned *t = rates(album);
    int empty = -1;
    for (int i = 0; t && i < RATE_SLOTS; i++) {
        if (t[i] && (t[i] ^ key) < 8) return i;
        if (!t[i] && empty < 0) empty = i;
    }
    return free ? empty : -1;
}

/* The hearts kept for key, 1 to 4 for a song or 5 for an album, else 0 (a malformed slot too). */
static int rate_kept(int album, unsigned key) {
    int i = rate_slot(album, key, 0), n = i < 0 ? 0 : (int)(rates(album)[i] & 7);
    return n <= 4 + album ? n : 0;
}

/* Keeps n hearts for key: a song's 1 to 4, none for 0 and 5 (My Fav's); an album's 1 to 5. 1 when
 * saved, 0 when the table is full, -1 when the file was not written (the slot then as it was). */
static int rate_keep(int album, unsigned key, int n) {
    int some = n > 0 && n < 5 + album, i = rate_slot(album, key, some);
    if (i < 0) return !some;
    unsigned *t = rates(album), old = t[i];
    t[i] = some ? (key & ~7u) | (unsigned)n : 0;
    if (rate_io(album, 1)) return 1;
    t[i] = old;
    return -1;
}

/* Report only failures from rate_keep. */
static int rate_saved(int k) {
    if (k <= 0) toast(k ? "Rating not saved" : "Ratings are full");
    return k > 0;
}

/* The playing local track (the queue menu's types), or 0. */
static void *rate_track(void) {
    unsigned at, n, type = (unsigned)MCL(MCL_TYPE);
    if (type != 1 && (type & 0xf000) != 0xf000) return (void *)0;
    return queue_now(&at, &n);
}

/* "♥♥♥♡♡" for n hearts, in s (16 bytes). */
static const char *hearts_text(char *s, int n) {
    char *p = s;
    for (int i = 0; i < 5; i++) p += tk_snprintf(p, 4, i < n ? "\xe2\x99\xa5" : "\xe2\x99\xa1");
    return s;
}

/* The badge on an empty heart: the playing track's 1-4 hearts, hidden for none and on a full heart
 * (My Fav, whatever was kept before). */
static void rate_badge(void) {
    if (!st.rate_badge) return;
    void *r = rate_track();
    int n = r ? rate_kept(0, listen_key(r)) : 0;
    if (n && !tk_strcmp(widget_get_prop_str(st.rate_fav, "image", ""), "play_fav")) n = 0;
    /* each set repaints the badge, and this runs every 500 ms: only a change (AWTK's "text" is
     * a wide string widget_get_prop_str doesn't read) */
    if (st.rate_shown == n + 1) return;
    st.rate_shown = n + 1;
    char s[4] = { (char)('0' + n) };
    widget_set_text_utf8(st.rate_badge, s);
    widget_set_visible(st.rate_badge, n != 0, 0);
}

static int rate_tick(const void *unused) {
    if (g_backlight_status) rate_badge();
    return 8; /* RET_REPEAT */
}

static int rate_tap(void *ctx, void *event);
/* The picker goes down with its page or panel (Coverflow's load destroys the page's children). */
static int rate_panel_gone(void *win, void *event) {
    if (win == st.rate_on) st.rate_on = (void *)0;
    return 0;
}
/* The picker on win, made on first use: a dark rounded panel, its caption, then five of the
 * heart's own images. */
static void *rate_panel(void *win) {
    void *panel = widget_lookup(win, "rate_panel", 0);
    if (panel) return panel;
    int h = 110, w = 5 * 56 + 24;
    panel = text(win, (I(win, W_W) - w) / 2, (I(win, W_H) - h) / 2, w, h);
    widget_set_name(panel, "rate_panel");
    widget_set_prop_int(panel, "style:normal:bg_color", (int)(0xff000000u | FILL_RGB)); /* opaque */
    widget_set_prop_int(panel, "style:normal:round_radius", LETTER_RADIUS);
    text(panel, 0, 8, w, 28);
    for (int i = 0; i < 5; i++) {
        void *heart = image_create(panel, 12 + i * 56, 44, 56, 56);
        image_set_draw_type(heart, IMAGE_DRAW_CENTER);
        widget_set_prop_str(heart, "clickable", "true");
        widget_on(heart, EVT_CLICK, rate_tap, (void *)(long)(i + 1));
    }
    widget_on(panel, EVT_DESTROY, rate_panel_gone, win);
    widget_on(win, EVT_DESTROY, rate_panel_gone, win);
    return panel;
}

/* The picker's hearts and caption for rate_pick. */
static void rate_paint(void) {
    void *panel = rate_panel(st.rate_on);
    char t[24] = "Unrated";
    for (int i = 0; i < 5; i++)
        image_base_set_image(widget_get_child(panel, (unsigned)i + 1),
                             i < st.rate_pick ? "play_fav" : "play_unfav");
    if (st.rate_pick) tk_snprintf(t, sizeof t, "%d of 5", st.rate_pick);
    widget_set_text_utf8(widget_get_child(panel, 0), t);
}

/* Closes the picker, and with save rates rate_pick: an album (rate_album) if still Coverflow's
 * centre card, else the playing track, My Fav in or out through the heart's own click and the
 * rest kept here. */
static void rate_close(int save) {
    void *win = st.rate_on;
    if (!win) return;
    st.rate_on = (void *)0;
    void *panel = rate_panel(win);
    widget_set_visible(panel, 0, 0);
    widget_set_sensitive(panel, 0); /* stock's hit test skips only the insensitive, hidden or not */
    int n = st.rate_pick;
    if (save && st.rate_album) { /* the menu's album, still on its page */
        void *a = window_manager_get_top_window(window_manager()) == win ? qm_record() : (void *)0;
        if (!a || qm_album_id(a) != st.rate_album) {
            toast("Album changed");
            return;
        }
        rate_saved(rate_keep(1, st.rate_album, n));
        return;
    }
    void *r = save ? rate_track() : (void *)0;
    if (!r) return;
    int fav = checkFavExist(r) != 0;
    if (!rate_saved(rate_keep(0, listen_key(r), n))) return;
    if ((n == 5) != fav) FAV_CLICK(st.rate_ctx, (void *)0);
    rate_badge();
}

static int rate_tap(void *ctx, void *event) {
    st.rate_pick = (int)(long)ctx;
    rate_close(1);
    return STOP;
}

/* The picker over win on pick hearts, for the queue menu's album album or, with 0, the playing
 * track: the wheel moves the rating, Centre saves it and Return leaves it as it was; a tap on a
 * heart saves that many. */
static int rate_show(void *win, int pick, unsigned album) {
    if (!win || st.rate_on) return 0;
    st.rate_on = win;
    st.rate_pick = pick;
    st.rate_album = album;
    rate_paint();
    void *panel = rate_panel(win);
    widget_set_visible(panel, 1, 0);
    widget_set_sensitive(panel, 1);
    return 1;
}
/* Now Playing's, on the playing track's rating. */
static int rate_open(void) {
    void *r = rate_track();
    return r && rate_show(st.rate_win, checkFavExist(r) ? 5 : rate_kept(0, listen_key(r)), 0);
}

/* The wheel, Centre and Return while the picker is up. */
static int rate_key(void *top, unsigned key) {
    if (!st.rate_on || top != st.rate_on) return 0;
    if (key == KEY_NEXT || key == KEY_PREV) {
        st.rate_pick = clamp_step(st.rate_pick, 5, key == KEY_NEXT ? 1 : -1);
        rate_paint();
    } else if (key == KEY_CENTER || key == KEY_RETURN)
        rate_close(key == KEY_CENTER);
    else
        return 0;
    drop_input();
    return 1;
}

/* Holding the heart RATE_HOLD_MS opens the picker; a tap is stock's, a full heart then keeping
 * nothing here, so taking it away leaves the song unrated. The picker aborts the press, so the
 * slide_view under the heart lets go of its grab and the picker takes the next touch. */
static int rate_long(const void *unused) {
    st.rate_hold = 0;
    if (!window_manager_get_pointer_pressed(window_manager()) || !(st.rate_held = rate_open()))
        return 0;
    unsigned abort_event[12];
    pointer_event_init(abort_event, EVT_POINTER_ABORT, st.rate_win, 0, 0);
    widget_dispatch_event_to_target_recursive(st.rate_win, abort_event);
    return 0;
}
static int rate_down(void *ctx, void *event) {
    st.rate_held = 0;
    rearm(&st.rate_hold, rate_long, RATE_HOLD_MS);
    return 0;
}
/* Returns 0 as stock's does: a STOP skips the slide_view's own release, and it keeps its grab. */
static int rate_fav_click(void *ctx, void *event) {
    stop_timer(&st.rate_hold);
    if (st.rate_held || st.rate_on) {
        st.rate_held = 0;
        return 0;
    }
    FAV_CLICK(ctx, event);
    void *r = rate_track();
    if (r) rate_keep(0, listen_key(r), 0);
    rate_badge();
    return 0;
}

static int rate_gone(void *win, void *event) {
    if (win != st.rate_win) return 0;
    stop_timer(&st.rate_hold);
    stop_timer(&st.rate_tick);
    st.rate_win = st.rate_fav = st.rate_ctx = st.rate_badge = (void *)0;
    st.rate_held = 0;
    return 0;
}

/* Now Playing's heart (both builds' playing_page_init): stock's click is swapped for one that
 * tells a hold from a tap, and the badge sits on the heart's top right. */
static void rate_bind(void *win) {
    void *fav = widget_lookup(win, "img_fav", 1), *emitter = fav ? P(fav, W_EMITTER) : (void *)0;
    stop_timer(&st.rate_tick);
    stop_timer(&st.rate_hold);
    st.rate_ctx = (void *)0;
    for (void *item = emitter ? P(emitter, 0) : 0; item; item = P(item, EMIT_NEXT))
        if (I(item, EMIT_TYPE) == EVT_CLICK && P(item, 12) == (void *)FAV_CLICK &&
            !B(item, EMIT_PENDING_REMOVE)) {
            st.rate_ctx = P(item, 0);
            break;
        }
    if (!st.rate_ctx) return;
    st.rate_win = win;
    st.rate_fav = fav;
    widget_off_by_func(fav, EVT_CLICK, (void *)FAV_CLICK, st.rate_ctx);
    widget_on(fav, EVT_CLICK, rate_fav_click, st.rate_ctx);
    widget_on(fav, EVT_POINTER_DOWN, rate_down, (void *)0);
    st.rate_badge = label(P(fav, W_PARENT), I(fav, W_X) + I(fav, W_W) - 18, I(fav, W_Y) + 6, 16, 16,
                          "s_scrlabel_white20c", 12, 0xffffffffu);
    widget_set_sensitive(st.rate_badge, 0); /* the heart's corner stays the heart's */
    st.rate_shown = 0;
    widget_on(win, EVT_DESTROY, rate_gone, win);
    st.rate_tick = timer_add(rate_tick, (void *)0, 500);
    rate_badge();
}

/* The Library's Ratings row: its menu, then a page of one list's songs, as Most Played's. */
static int fav_songs(void *unused) {
    return getAllFav(); /* select * from myfav, stock's song rows */
}

static int rt_play(void *ctx, void *event) {
    play_folder(st.rt_list, (int)(long)ctx, 0xf001);
    return 0;
}

/* Each of songs' hearts into h: 5 for My Fav's, else the kept 1-4.
 * ponytail: every song against every rated one, fine for thousands rated; a sorted table if not. */
static void rate_all(void *songs, unsigned char *h) {
    int nf, kept = 0;
    void *favs = staged(fav_songs, 0, &nf);
    unsigned nfav = deque_size(favs), n = deque_size(songs), *t = rates(0);
    unsigned *keys = calloc(nfav + RATE_SLOTS + 1, sizeof *keys);
    for (unsigned i = 0; keys && i < nfav; i++) keys[i] = listen_key(deque_at(favs, i)) | 7;
    for (int i = 0; keys && t && i < RATE_SLOTS; i++)
        if (t[i]) keys[nfav + kept++] = t[i];
    for (unsigned i = 0; keys && i < n; i++) {
        unsigned key = listen_key(deque_at(songs, i));
        h[i] = 0;
        for (unsigned j = 0; j < nfav + (unsigned)kept && h[i] < 5; j++)
            if ((keys[j] ^ key) < 8) h[i] = j < nfav ? 5 : (unsigned char)(keys[j] & 7);
    }
    free(keys);
    deque_destroy(favs);
}

/* Top Rated (4-5 hearts), Loved (5), All Rated (1-5) and Disliked (1): most hearts first, then
 * the library's title order, each row's hearts under its title. */
static const char *const rt_names[] = { "Top Rated", "Loved", "All Rated", "Disliked" };
#define RT_ALBUMS 2 /* the Ratings menu's Albums row, after Loved */
static int rated_open(void *ctx, void *event) {
    static const unsigned char lo[] = { 4, 5, 1, 1 }, hi[] = { 5, 5, 5, 1 };
    int k = (int)(long)ctx - ((int)(long)ctx > RT_ALBUMS), n;
    if (st.rt_page || !(st.rt_page = page_open("rated_page", page_closed, page_keyup))) return 0;
    void *songs = staged(all_songs, 0, &n), *list = _create_deque("stSongInfo");
    unsigned size = deque_size(songs);
    unsigned char *h = calloc(size + 1, 1), *of = calloc(size + 1, 1);
    deque_init(list);
    if (h && of) {
        rate_all(songs, h);
        for (int v = hi[k]; v >= lo[k]; v--)
            for (unsigned i = 0; i < size; i++)
                if (h[i] == v)
                    of[deque_size(list)] = (unsigned char)v,
                    _deque_push_back(list, deque_at(songs, i));
    }
    st.rt_list = list;
    deque_destroy(songs);
    int count = (int)deque_size(list);
    void *view = page_list(st.rt_page, st.rt_page, 0, rt_names[k], count ? count : 1, 64);
    char name[512], detail[300], hs[16];
    for (int i = 0; i < count; i++) {
        void *t = deque_at(list, (unsigned)i);
        const char *artist = P(t, REC_ARTIST);
        tk_snprintf(detail, sizeof detail, artist && *artist ? "%s · %s" : "%s%s",
                    hearts_text(hs, of[i]), artist && *artist ? artist : "");
        page_row_detail(view, i, track_name(name, sizeof name, t), detail, rt_play);
    }
    if (!count)
        page_row_detail(view, 0, "No songs yet", "Hold the heart on Now Playing to rate", 0);
    free(h);
    free(of);
    return 0;
}

/* Albums: the rated albums as Coverflow (coverflow_rated), by most hearts unless sorted. */
extern int coverflow_rated(int (*hearts)(void *));
static int album_hearts(void *r) { return rate_kept(1, album_id(r)); }
static int rated_albums(void *ctx, void *event) {
    if (!coverflow_rated(album_hearts)) toast("No rated albums yet");
    return 0;
}

/* The songs' best first, then the albums, then the songs' lists to tidy. */
static int ratings_menu(void *ctx, void *event) {
    if (st.rt_menu || !(st.rt_menu = page_open("ratings_page", page_closed, page_keyup))) return 0;
    void *view = page_list(st.rt_menu, st.rt_menu, 0, "Ratings", 5, 48);
    for (int i = 0; i < 5; i++)
        page_row_into(view, i, i == RT_ALBUMS ? "Albums" : rt_names[i - (i > RT_ALBUMS)],
                      i == RT_ALBUMS ? rated_albums : rated_open);
    return 0;
}

/* Era (docs/internals.md#era): a row per decade with dated music, oldest first, and a decade's
 * albums by year. An album's year is its tracks' latest, as Coverflow's Year sort; undated albums
 * and the untagged have none. ponytail: decades only, no year level or covers, and the album rows
 * have no hold menu; a year page, or the decade as a scoped Coverflow, if asked for. */
#define ERA_DECADES 32
#define ERA_ALBUMS                                                                                 \
    "select album,ifnull(nullif(albumsonger,''),songer)," ALBUM_YEAR                               \
    " as release_year,fileurl from songtable "                                                     \
    "where " MUSIC_WHERE " and ifnull(album,'')<>''" ALBUM_GROUP
typedef struct {
    char *album, *artist, *path;
    int year;
} era_t;
static struct {
    void *page, *albums; /* the decades page, a decade's page */
    int decade[ERA_DECADES], count[ERA_DECADES], n;
    era_t *v; /* the open decade's albums, oldest first, room for cap */
    unsigned m, cap;
} era __attribute__((section(".scratch")));

static int era_sql(const char *sql, int (*row)(void *, int, char **, char **)) {
    void *db = 0;
    /* Keep a full-library grouping query from competing with the player's song records for RAM. */
    int bad = sqlite3_open("/mnt/data/database.db", &db) ||
              sqlite3_exec(db, "PRAGMA cache_size=64;PRAGMA temp_store=FILE", 0, 0, 0) ||
              sqlite3_exec(db, sql, row, 0, 0);
    if (db) sqlite3_close(db);
    return !bad;
}
static int era_decade_row(void *ctx, int n, char **v, char **c) {
    if (n == 2 && v[0] && v[1] && era.n < ERA_DECADES)
        era.decade[era.n] = atoi(v[0]), era.count[era.n++] = atoi(v[1]);
    return 0;
}
static int era_album_row(void *ctx, int n, char **v, char **c) {
    if (n != 4 || !v[0] || !v[2] || !v[3] || era.m == era.cap) return 0;
    era_t *e = &era.v[era.m];
    e->album = strdup(v[0]), e->artist = strdup(v[1] ? v[1] : ""), e->path = strdup(v[3]);
    e->year = atoi(v[2]);
    if (!e->album || !e->artist || !e->path) {
        free(e->album), free(e->artist), free(e->path);
        return 1;
    }
    ++era.m;
    return 0;
}
static void era_free(void) {
    for (unsigned i = 0; i < era.m; ++i)
        free(era.v[i].album), free(era.v[i].artist), free(era.v[i].path);
    free(era.v);
    era.v = 0, era.m = era.cap = 0;
}
static int era_closed(void *ctx, void *event) {
    if (ctx == era.page) era.page = 0;
    if (ctx == era.albums) era.albums = 0, era_free();
    return 0;
}
/* "60s" to "90s", then "2000s" on. */
static const char *era_name(char *buf, int decade) {
    tk_snprintf(buf, 16, decade < 2000 ? "%02ds" : "%ds", decade < 2000 ? decade % 100 : decade);
    return buf;
}

/* Album i's record: the album name's songs, the one at its path. *songs holds it, the caller's. */
static int era_by_name(void *name) { return getMusicByAlbum(name); }
static void *era_record(unsigned i, void **songs) {
    int n;
    *songs = staged_music(era_by_name, era.v[i].album, &n);
    for (unsigned k = 0; k < deque_size(*songs); ++k)
        if (!strcmp(P(deque_at(*songs, k), REC_PATH), era.v[i].path)) return deque_at(*songs, k);
    return 0;
}
/* Every album's tracks, in the page's order, each in album order. ponytail: two queries an album,
 * as Coverflow's All albums; one decade query split back into albums if a large decade drags. */
static void *era_songs(void) {
    void *all = _create_deque("stSongInfo");
    deque_init(all);
    for (unsigned i = 0; i < era.m; ++i) {
        void *songs, *r = era_record(i, &songs);
        void *t = r ? coverflow_album_tracks(r) : 0;
        for (unsigned j = 0; t && j < deque_size(t); ++j) _deque_push_back(all, deque_at(t, j));
        if (t) deque_destroy(t);
        deque_destroy(songs);
    }
    return all;
}
/* Shuffle's songs: one query for the decade's files, then the library filtered by them; its
 * order is about to be shuffled, so era_songs' two queries an album would only cost time. */
static struct {
    unsigned *v, n, cap;
} era_keys __attribute__((section(".scratch")));
static int era_key_row(void *ctx, int n, char **v, char **c) {
    if (n != 1 || !v[0]) return 0;
    if (era_keys.n == era_keys.cap) {
        unsigned cap = era_keys.cap ? era_keys.cap * 2 : 1024, *p = calloc(cap, sizeof *p);
        if (!p) return 1;
        if (era_keys.n) memcpy(p, era_keys.v, era_keys.n * sizeof *p);
        free(era_keys.v);
        era_keys.v = p, era_keys.cap = cap;
    }
    era_keys.v[era_keys.n++] = (unsigned)atoi(v[0]);
    return 0;
}
static int era_key_cmp(const void *a, const void *b) {
    unsigned x = *(const unsigned *)a, y = *(const unsigned *)b;
    return x < y ? -1 : x > y;
}
static void *era_shuffle_songs(void) {
    char sql[2048];
    int from = era.m ? era.v[0].year / 10 * 10 : 0, n;
    void *out = _create_deque("stSongInfo");
    deque_init(out);
    /* The songs of ERA_ALBUMS' groups dated in the decade, matched on ALBUM_GROUP's keys. */
    tk_snprintf(
        sql, sizeof sql,
        "%s from songtable s join (select album a,ifnull(albumsonger,'') b,case "
        "ifnull(albumsonger,'') when '' then rtrim(fileurl,replace(fileurl,'/','')) end d,"
        "%s as y from songtable where %s and ifnull(album,'')<>''%s having y between %d and %d) g "
        "on s.album=g.a COLLATE NOCASE and ifnull(s.albumsonger,'')=g.b COLLATE NOCASE and "
        "(g.d is null or rtrim(s.fileurl,replace(s.fileurl,'/',''))=g.d)",
        "select distinct s.id", ALBUM_YEAR, MUSIC_WHERE, ALBUM_GROUP, from, from + 9);
    era_keys.n = 0;
    if (era.m && era_sql(sql, era_key_row) && era_keys.n) {
        qsort(era_keys.v, era_keys.n, sizeof *era_keys.v, era_key_cmp);
        void *all = staged(songs_unsorted, (void *)1, &n);
        for (unsigned i = 0; i < deque_size(all); ++i) {
            unsigned key = (unsigned)I(deque_at(all, i), REC_ID), lo = 0, hi = era_keys.n;
            while (lo < hi) /* the first key not below this one */
                if (era_keys.v[(lo + hi) / 2] < key)
                    lo = (lo + hi) / 2 + 1;
                else
                    hi = (lo + hi) / 2;
            if (lo < era_keys.n && era_keys.v[lo] == key) _deque_push_back(out, deque_at(all, i));
        }
        deque_destroy(all);
    }
    free(era_keys.v);
    era_keys.v = 0, era_keys.n = era_keys.cap = 0;
    return out;
}
/* Play All from the first song as a library queue, as Coverflow's All albums; Shuffle is Shuffle
 * Songs over them; an album opens its tracks in Coverflow. */
static int era_pick(void *ctx, void *event) {
    int i = (int)(long)ctx;
    if (i < 2) {
        void *all = i ? era_shuffle_songs() : era_songs();
        if (!deque_size(all) || access(P(deque_at(all, 0), REC_PATH), 0))
            toast("Storage unavailable");
        else if (i)
            shuffle_play(all);
        else
            play_folder(all, 0, 0xf001);
        deque_destroy(all);
        return 0;
    }
    void *songs, *r = (unsigned)(i - 2) < era.m ? era_record((unsigned)(i - 2), &songs) : 0;
    if (!r || !coverflow_goto_album(r)) toast("Album not found");
    if ((unsigned)(i - 2) < era.m) deque_destroy(songs);
    return 0;
}
static int era_open_decade(void *ctx, void *event) {
    int k = (int)(long)ctx, from = era.decade[k];
    if (era.albums || !(era.albums = page_open("era_albums_page", era_closed, page_keyup)))
        return 0;
    char sql[1024], name[16], detail[300];
    tk_snprintf(
        sql, sizeof sql, /* MUSIC_WHERE's like patterns hold '%': an argument, not the format */
        "%s having release_year between %d and %d order by release_year,album COLLATE NOCASE",
        ERA_ALBUMS, from, from + 9);
    era.v =
        calloc((unsigned)era.count[k] + 1, sizeof *era.v); /* the decade's count, from era_menu */
    era.cap = era.v ? (unsigned)era.count[k] : 0;
    era_sql(sql, era_album_row);
    void *view = page_list(era.albums, era.albums, 0, era_name(name, from), (int)era.m + 2, 64);
    tk_snprintf(detail, sizeof detail, era.m == 1 ? "%u album" : "%u albums", era.m);
    page_row_detail(view, 0, "Play All", detail, era_pick);
    page_row_detail(view, 1, "Shuffle", "Every song, shuffled", era_pick);
    for (unsigned i = 0; i < era.m; ++i) {
        tk_snprintf(detail, sizeof detail, *era.v[i].artist ? "%s \xe2\x80\xa2 %d" : "%s%d",
                    era.v[i].artist, era.v[i].year);
        page_row_detail_into(view, (int)i + 2, era.v[i].album, detail, era_pick);
    }
    return 0;
}
static int era_menu(void *ctx, void *event) {
    if (era.page) return 0;
    era.n = 0;
    if (!era_sql("select year/10*10,count(*) from (select " ALBUM_YEAR
                 " year from songtable where " MUSIC_WHERE " and ifnull(album,'')<>''" ALBUM_GROUP
                 ") where year>0 group by 1 order by 1",
                 era_decade_row)) {
        toast("Era unavailable");
        return 0;
    }
    if (!era.n) {
        toast("No dated albums yet");
        return 0;
    }
    if (!(era.page = page_open("era_page", era_closed, page_keyup))) return 0;
    void *view = page_list(era.page, era.page, 0, "Era", era.n, 64);
    char name[16], detail[32];
    for (int i = 0; i < era.n; ++i) {
        tk_snprintf(detail, sizeof detail, era.count[i] == 1 ? "%d album" : "%d albums",
                    era.count[i]);
        page_row_detail_into(view, i, era_name(name, era.decade[i]), detail, era_open_decade);
    }
    return 0;
}

/* Long Songs (docs/internals.md#long-songs): the library's songs over LONG_SONG_S, longest first.
 * The database keeps no length, so each file's is probed once, through stock's TagLib, on a worker
 * thread, and kept in LENGTHS_FILE by path hash: {key, seconds} pairs sorted by key, 0 seconds for
 * a file TagLib can't read. A CUE track with both ends needs no probe. */
#define LENGTHS_FILE "/mnt/data/ringnav-lengths"
typedef struct {
    unsigned key, sec;
} length_t;
static struct {
    length_t *tab, *jobs; /* the cache, sorted; the files to probe, sec filled as done */
    char **paths;         /* the jobs' files, the worker's own copies */
    unsigned n, read, timer;
    unsigned long thread;
    int total, running;
    volatile int done, cancel;
    void *songs, *title; /* the library, while its lengths are probed, and the count's title */
} ls __attribute__((section(".scratch")));

static void lengths_read(void) {
    if (ls.read) return;
    unsigned size;
    length_t *tab = read_all(LENGTHS_FILE, ~0u, &size);
    ls.read = tab || file_size(LENGTHS_FILE) <=
                         0; /* unread, not empty: not saved over, read again next time */
    ls.n = size / sizeof(length_t);
    if (!ls.n) free(tab), tab = 0;
    ls.tab = tab;
}

/* key's seconds, -1 when never probed. */
static int length_of(unsigned key) {
    unsigned lo = 0, hi = ls.n;
    while (lo < hi) {
        unsigned mid = lo + (hi - lo) / 2;
        if (ls.tab[mid].key < key)
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo < ls.n && ls.tab[lo].key == key ? (int)ls.tab[lo].sec : -1;
}

/* ponytail: a 32-bit path hash; two files that collide share a length (~1% across 10k files). */
static unsigned file_key(void *r) { return fnv(FNV_SEED, P(r, REC_PATH)); }

/* r's seconds: a CUE track's from its ends (to the file's end for the last), else the file's;
 * -1 when the file was never probed. */
static int song_length(void *r) {
    int start = I(r, REC_CUE_START), end = I(r, REC_CUE_END), len;
    if (end > start) return end - start;
    len = length_of(file_key(r));
    return len < 0 ? -1 : len > start ? len - start : 0;
}

static int by_key(const void *a, const void *b) {
    unsigned x = ((const length_t *)a)->key, y = ((const length_t *)b)->key;
    return (x > y) - (x < y);
}

/* AudioProperties::length(), as libtag_c's taglib_audioproperties_length calls it: vtable +0xc. */
static void *ls_worker(void *unused) {
    while (ls.done < ls.total && !ls.cancel) {
        void *f = taglib_file_new(ls.paths[ls.done], 1);
        void *p = f ? taglib_file_audioproperties(f) : (void *)0;
        int sec = p ? ((int (*)(void *))(*(void ***)p)[3])(p) : 0;
        ls.jobs[ls.done].sec = sec > 0 ? (unsigned)sec : 0;
        if (f) taglib_file_free(f);
        ++ls.done;
    }
    return 0;
}

/* The worker stopped, what it probed merged into the cache and saved, its jobs freed. */
static void ls_stop(void) {
    worker_stop(ls.thread, &ls.running, &ls.cancel, &ls.timer);
    unsigned n = ls.n + (unsigned)ls.done;
    length_t *tab = calloc(n + 1, sizeof *tab);
    if (tab && ls.done) {
        if (ls.n) memcpy(tab, ls.tab, ls.n * sizeof *tab);
        memcpy(tab + ls.n, ls.jobs, (unsigned)ls.done * sizeof *tab);
        qsort(tab, n, sizeof *tab, by_key);
        unsigned k = 0; /* a file probed twice (two CUE tracks to its end) is kept once */
        for (unsigned i = 0; i < n; i++)
            if (!k || tab[i].key != tab[k - 1].key) tab[k++] = tab[i];
        n = k;
        free(ls.tab);
        ls.tab = tab;
        ls.n = n;
        if (ls.read) blob_io(LENGTHS_FILE, LENGTHS_FILE ".tmp", ls.tab, ls.n * sizeof *ls.tab, 1);
    } else
        free(tab);
    for (int i = 0; i < ls.total; i++) free(ls.paths[i]);
    free(ls.paths);
    free(ls.jobs);
    ls.paths = 0;
    ls.jobs = 0;
    ls.total = ls.done = ls.cancel = 0;
}

static int by_length(const void *a, const void *b) {
    unsigned x = ((const length_t *)a)->sec, y = ((const length_t *)b)->sec;
    return (x < y) - (x > y);
}

/* The page's list: the songs over LONG_SONG_S, longest first, each row's artist and length under
 * its title, a row playing the list from itself as Most Played's. */
static void ls_show(void) {
    void *songs = ls.songs, *list = _create_deque("stSongInfo");
    unsigned size = deque_size(songs), n = 0;
    length_t *v = calloc(size + 1, sizeof *v); /* key: the song's index */
    deque_init(list);
    for (unsigned i = 0; v && i < size; i++) {
        int sec = song_length(deque_at(songs, i));
        if (sec > LONG_SONG_S) v[n].key = i, v[n++].sec = (unsigned)sec;
    }
    if (n) qsort(v, n, sizeof *v, by_length);
    for (unsigned i = 0; i < n; i++) _deque_push_back(list, deque_at(songs, v[i].key));
    st.rt_list = list;
    void *view = page_list(st.rt_page, st.rt_page, 0, "Long Songs", n ? (int)n : 1, 64);
    char name[512], detail[300], t[16];
    for (unsigned i = 0; i < n; i++) {
        void *r = deque_at(list, i);
        const char *artist = P(r, REC_ARTIST);
        toolsTimeItoa(t, (int)v[i].sec);
        tk_snprintf(detail, sizeof detail, artist && *artist ? "%s · %s" : "%s%s",
                    artist && *artist ? artist : "", t);
        page_row_detail(view, (int)i, track_name(name, sizeof name, r), detail, rt_play);
    }
    if (!n) page_row_detail(view, 0, "No songs over 10 minutes", 0, 0);
    free(v);
    deque_destroy(songs);
    ls.songs = 0;
}

/* The page gone: what was probed is kept. */
static void ls_closed(void) {
    ls_stop();
    if (ls.songs) deque_destroy(ls.songs);
    ls.songs = 0;
}

static int ls_done(const void *unused) {
    ls.timer = 0;
    ls_stop();
    if (st.rt_page && ls.songs) ls_show();
    return 0;
}

static int ls_cancel(void *ctx, void *event) {
    ls.cancel = 1; /* the poll shows what is known */
    return 0;
}

static int ls_poll(const void *unused) {
    if (ls.done >= ls.total || ls.cancel) return ls_done(0);
    char t[64];
    tk_snprintf(t, sizeof t, "Measuring songs\xe2\x80\xa6 %d/%d", ls.done, ls.total);
    widget_set_text_utf8(ls.title, t);
    return 8; /* RET_REPEAT */
}

/* Long Songs' row: the files never probed are probed first, with their count and Cancel, which
 * keeps what is done; the page closing does the same. */
static int long_songs(void *ctx, void *event) {
    if (st.rt_page || !(st.rt_page = page_open("longsongs_page", page_closed, page_keyup)))
        return 0;
    int n;
    lengths_read();
    ls.songs = staged(songs_unsorted, (void *)1, &n);
    unsigned size = deque_size(ls.songs);
    ls.jobs = calloc(size + 1, sizeof *ls.jobs);
    ls.paths = calloc(size + 1, sizeof *ls.paths);
    for (unsigned i = 0; ls.jobs && ls.paths && i < size; i++) {
        void *r = deque_at(ls.songs, i);
        unsigned key = file_key(r);
        if (song_length(r) < 0 && P(r, REC_PATH) && (ls.paths[ls.total] = strdup(P(r, REC_PATH))))
            ls.jobs[ls.total++].key = key;
    }
    if (ls.total && !pthread_create(&ls.thread, 0, ls_worker, 0)) {
        ls.running = 1;
        page_row_detail(
            page_list(st.rt_page, st.rt_page, &ls.title, "Measuring songs\xe2\x80\xa6", 1, 48), 0,
            "Cancel", 0, ls_cancel);
        ls.timer = timer_add(ls_poll, (void *)0, 250);
    } else {
        ls_stop();
        ls_show();
    }
    return 0;
}

/* Upload Scrobbles: the log goes up on scrobble.c's thread, and a timer reports how it went. */
static int upload_wait(const void *unused) {
    int sent = 0, done = scrobble_poll(&sent);
    if (!done) return 8; /* RET_REPEAT */
    char s[48];
    tk_snprintf(s, sizeof s, done < 0 ? "Upload failed, %d sent" : "Scrobbles sent: %d", sent);
    toast(done > 0 && !sent ? "Nothing to upload" : s);
    return 0;
}

static int upload_scrobbles(void *ctx, void *event) {
    int started = get_wifisignal() > 0 ? scrobble_start() : -2;
    if (started > 0) timer_add(upload_wait, (void *)0, 500);
    toast(started > 0     ? "Uploading scrobbles"
          : !started      ? "Already uploading"
          : started == -2 ? "Connect to Wi-Fi first"
                          : "Upload failed");
    return 0;
}

/* Podcasts, Audiobooks and Videos (docs/internals.md#podcasts-and-audiobooks): the card's top-level
 * folders of these names, case aside, browsed in folder_page from there; Photos opens photos.c,
 * Books books.c. */
enum { PODCASTS = 1, AUDIOBOOKS, PHOTOS, BOOKS, VIDEOS };
static const char *const MEDIA[] = { "Podcasts", "Audiobooks", "Photos", "Books", "Videos" };

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

/* A track in Podcasts or Audiobooks: always resumed, never counted or scrobbled. */
static int spoken(const char *path) {
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
static int media_find(int kind, char *path) {
    void *dir = opendir("/mnt/mmc");
    int found = 0;
    for (struct dirent *e; dir && !found && (e = readdir(dir));)
        if ((e->d_type == 4 || !e->d_type) && media_kind(e->d_name) == kind)
            found = tk_snprintf(path, sizeof st.media_root, "/mnt/mmc/%s", e->d_name) > 0;
    if (dir) closedir(dir);
    return found;
}

static int media_click(void *ctx, void *event) {
    int kind = (int)(long)ctx;
    char root[sizeof st.media_root];
    if (kind == PHOTOS || kind == BOOKS) {
        if (!media_find(kind, root)) return 0;
        if (kind == PHOTOS)
            photos_open(root);
        else
            books_open(root);
        return 0;
    }
    st.media_open = media_find(kind, st.media_root);
    if (st.media_open) navigator_to("folder_page");
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

/* Back at a shortcut or bypassed SD root (media_root, each its own layer) leaves the page (stock's
 * layer 0). */
int ringnav_folder_back(void *yoffset, void *index) {
    if (st.media_root[0] && !tk_strcmp((const char *)g_folder_path, st.media_root)) {
        g_folder_layer = 1;
        st.media_root[0] = 0;
    }
    return stock_folder_back_trampoline(yoffset, index);
}

/* folder_page's row click (stock's, 0x521bc4: the tapped or Centre-pressed row's index into
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
    return stock_folder_click_trampoline(ctx, event);
}

/* A list_row titled text; returns its list_item. */
static void *library_row(void *view, const char *icon, int (*click)(void *, void *), void *ctx,
                         const char *text) {
    void *label = list_row(view, icon, click, ctx);
    widget_set_text_utf8(label, text);
    return P(P(label, W_PARENT), W_PARENT);
}

/* A library row that opens another list: stock's own list_into trailing image, as the stock Local
 * Music categories place it. Returns the row's list_item. */
static void *library_into(void *view, const char *icon, int (*click)(void *, void *), void *ctx,
                          const char *text) {
    void *label = list_row_core(view, icon, click, ctx, 210, 1);
    widget_set_text_utf8(label, text);
    return P(P(label, W_PARENT), W_PARENT);
}

/* localmusic_page_init: stock's 11 category rows (0x5247ec), by index (get_localmusic_showinfo
 * 0x5012d0): Update Local Music, All Songs, Album, Artist, Genre, Hi-Res, My Fav, Frequent,
 * Recent, Recently Added, Playlist. */
enum {
    L_UPDATE,
    L_SONGS,
    L_ALBUMS,
    L_ARTISTS,
    L_GENRES,
    L_HIRES,
    L_FAV,
    L_FREQUENT,
    L_RECENT,
    L_ADDED,
    L_PLAYLISTS,
    L_STOCK
};

/* The Library: one Shuffle row opening its menu, browsing, the user's own lists, listening
 * history, the card's other media, then upkeep. The added rows' buttons have no name, so stock's
 * row click (atoi of the name, 0x5241fc) never sees them. */
int ringnav_localmusic(void *win, void *ctx) {
    int result = stock_localmusic_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_localmusic", 1) : (void *)0;
    if (!view || widget_count_children(view) != L_STOCK) return result;
    void *stock[L_STOCK];
    for (int i = 0; i < L_STOCK; i++) stock[i] = widget_get_child(view, i);
    unsigned n = 0; /* each row moves to n as it comes, so Update Local Music, left, ends last */
    widget_restack(library_into(view, "local_shuffle", shuffle_menu, 0, "Shuffle"), n++);
    static const unsigned char middle[] = { L_ARTISTS,   L_ALBUMS, L_SONGS, L_GENRES,
                                            L_PLAYLISTS, L_FAV,    L_ADDED, L_RECENT };
    for (unsigned i = 0; i < sizeof middle; i++)
        if (middle[i] == L_GENRES) { /* browsing by a tag, as Genre */
            widget_restack(stock[L_GENRES], n++);
            widget_restack(library_into(view, "local_era", era_menu, 0, "Era"), n++);
        } else if (middle[i] == L_FAV) { /* My Fav is Ratings' Loved, its row kept hidden */
            widget_restack(library_into(view, "local_fav", ratings_menu, 0, "Ratings"), n++);
            widget_set_visible(stock[L_FAV], 0, 0);
            widget_restack(stock[L_FAV], n++);
        } else
            widget_restack(stock[middle[i]], n++);
    widget_restack(library_into(view, "local_frequentplay", most_played, 0, "Most Played"), n++);
    widget_restack(stock[L_FREQUENT], n++);
    widget_restack(stock[L_HIRES], n++);
    widget_restack(library_into(view, "local_longsongs", long_songs, 0, "Long Songs"), n++);
    /* Podcasts, Audiobooks, Photos, Books and Videos, each only with its folder; then iPod's Games.
     */
    static const char *const icons[] = { "local_podcasts", "local_audiobooks", "local_photos",
                                         "local_books", "local_videos" };
    char path[sizeof st.media_root];
    for (int k = 0; k < VIDEOS; k++)
        if (media_find(k + 1, path))
            widget_restack(
                library_into(view, icons[k], media_click, (void *)(long)(k + 1), MEDIA[k]), n++);
#if IPOD
    widget_restack(library_into(view, "local_games", games_open, 0, "Games"), n++);
#endif
    if (scrobble_ready())
        widget_restack(library_row(view, "local_scrobble", upload_scrobbles, 0, "Upload Scrobbles"),
                       n++);
    return result;
}

/* Streaming's AirPlay row: stock's own receiver (Shairport 1.1.1, AirPlay 1), whose switch is on
 * System settings' Network service page; that page, one press from Streaming. */
static int airplay_open(void *ctx, void *event) {
    navigator_to("systemset/netservice_page");
    return 0;
}

/* Whether w, or a widget in it, shows the image name. */
static int shows_image(void *w, const char *name) {
    if (!tk_strcmp(widget_get_prop_str(w, "image", ""), name)) return 1;
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i)
        if (shows_image(widget_get_child(w, i), name)) return 1;
    return 0;
}

/* A Streaming row tagged as service i (svc), hidden when STREAMHIDE holds it. */
static void svc_row(void *row, int i, char *hide) {
    prop(row, SVC_PROP, i + 1);
    if (svc_find(hide, i)) widget_set_visible(row, 0, 0);
}

/* stream_page_init: stock's rows but Baidu Netdisk (a Chinese cloud drive, signed in to from
 * Baidu's app: its row, the one with list_baidu's icon, removed), then Spotify (spotify.c), which
 * opens its browser, the card apps on the card (addon.c: Deezer, Soulseek, File Browser… as System
 * settings' Apps or the user put them there), Tidal Downloads, Internet Radio (radio.c), AirPlay
 * and Configuration. Their buttons have no name, so stock's row click never sees them. */
int ringnav_stream(void *win, void *ctx) {
    int result = stock_stream_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_streamsset", 1) : (void *)0;
    for (int i = view ? (int)widget_count_children(view) : 0; i-- > 0;)
        if (shows_image(widget_get_child(view, (unsigned)i), "list_baidu"))
            widget_destroy(widget_get_child(view, (unsigned)i));
    if (view) {
        char hide[SVC_HIDE];
        int i = 1;
        st.svc_n = addon_scan(); /* at most APPS_MAX */
        svc_hidden(hide);
        for (unsigned c = 0, stock = widget_count_children(view); c < stock; ++c)
            if (shows_image(widget_get_child(view, c), "list_tidal"))
                svc_row(widget_get_child(view, c), 0, hide);
        svc_row(library_into(view, "stream_spotify", spot_open, 0, "Spotify"), i++, hide);
        for (int k = 0; k < st.svc_n; ++k)
            svc_row(library_into(view, addon_icon(k), addon_open, (void *)(long)k, addon_name(k)),
                    i++, hide);
        svc_row(library_into(view, "list_tidal", tidal_downloads, 0, "Tidal Downloads"), i++, hide);
        svc_row(library_into(view, "stream_radio", radio_open, 0, "Internet Radio"), i++, hide);
        svc_row(library_into(view, "stream_airplay", airplay_open, 0, "AirPlay"), i, hide);
        library_into(view, "stream_config", stream_config, 0, "Configuration");
    }
    return result;
}

/* usbmode_page_init, as a cable to a computer opens USB mode, before stock unmounts the card for
 * USB storage: the card apps, which write to it (downloads, uploads), end first, so nothing of
 * theirs keeps it busy (they run from /tmp: addon.c), as does Download Album Lyrics' job. */
int ringnav_usbmode(void *a, void *b, void *c, void *d) {
    addon_card_away();
    album_lyrics_stop();
    return stock_usbmode_trampoline(a, b, c, d);
}

/* mclStartPlayer, every local track's start: a playing librespot or radio station lets the DAC's
 * PCM go first. A track too long for RAM fails as stock's own checks do, with -1. */
int ringnav_start_player(void) {
    if (!playback_start_fits()) return -1;
    headset_manual();
    spot_yield();
    radio_stop();
    playback_track_speed();
    return stock_start_player_trampoline();
}

/* systemset_about_page_init: stock's seven rows (0x4bc274), each a s_listitem_black list_item
 * holding a 335x70 s_btn_listitem button, a 166px s_scrlabel_white24l title at x 10 and a 149px
 * s_scrlabel_white20r value at x 176, both focus-only scrolling with ellipses. FW. Version (row 1)
 * reads demo's version literal, which carries the updater tag (tools/build.py VERSIONS), so it
 * shows the stock firmware's again; a CFW. Version row in the same widgets follows it. Its button
 * has no name, so stock's row click (atoi of the name, 0x4bc774) ignores it. */
static void about_label(void *button, int x, int w, const char *style, const char *text) {
    void *label = hscroll_label_create(button, x, 0, w, 70);
    widget_use_style(label, style);
    hscroll_label_set_only_focus(label, 1);
    hscroll_label_set_ellipses(label, 1);
    widget_set_text_utf8(label, text);
}

int ringnav_about(void *win, void *ctx) {
    int result = stock_about_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_about", 1) : (void *)0;
    if (view && widget_count_children(view) == 7) {
        widget_set_text_utf8(widget_get_child(widget_get_child(widget_get_child(view, 1), 0), 1),
                             STOCK_VERSION);
        void *button = list_button(view);
        about_label(button, 10, 166, "s_scrlabel_white24l", "CFW. Version");
        about_label(button, 176, 149, "s_scrlabel_white20r", Q2POD_VERSION);
        widget_restack(P(button, W_PARENT), 2);
    }
    return result;
}

/* Entry IDs are stock's order. Keep our records separate from browsing and the live queue. */
static struct {
    void *page, *view, *songs, *picker, *edit;
    void **match;
    int dest, mode, selected, pending, nmatch;
    unsigned key, timer;
    char name[128], query[128];
} ple __attribute__((section(".scratch")));

/* One private selection for demo's lifetime, so a covered stock picker keeps its batch intact. */
static void *ple_add_select __attribute__((section(".scratch")));
struct ple_check {
    void *a, *b;
    int seen;
};
static int ple_checked(void *ctx, int n, char **v, char **names) {
    struct ple_check *c = ctx;
    if (n != 4 || !v[0] || !v[1]) return 1;
    int id = atoi(v[0]);
    void *r = id == I(c->a, REC_ID) ? c->a : c->b;
    if (!r || id != I(r, REC_ID) || tk_strcmp(v[1], P(r, REC_PATH)) ||
        (v[2] ? atoi(v[2]) : 0) != I(r, REC_CUE_START) ||
        (v[3] ? atoi(v[3]) : 0) != I(r, REC_CUE_END))
        return 1;
    c->seen++;
    return 0;
}
static int ple_changed(void *ctx, int n, char **v, char **names) {
    return n != 1 || !v[0] || atoi(v[0]) != 1;
}
static int ple_number(void *ctx, int n, char **v, char **names) {
    if (n != 1 || !v[0]) return 1;
    *(int *)ctx = atoi(v[0]);
    return 0;
}

/* Revalidate under the write lock; the temporary ID is above every occupied ID. */
static int ple_mutate(int dest, void *a, void *b) {
    void *db = 0;
    int ok = 0, temp = 0;
    char sql[512];
    struct ple_check check = { a, b, 0 };
    if (dest < 0 || dest >= 100 || !a || I(a, REC_ID) <= 100 ||
        (b && (I(b, REC_ID) <= 100 || I(a, REC_ID) == I(b, REC_ID))))
        return 0;
    if (sqlite3_open("/mnt/data/database.db", &db)) goto done;
    if (sqlite3_exec(db, "begin immediate", 0, 0, 0)) goto done;
    tk_snprintf(sql, sizeof sql,
                "select id,fileurl,time,time_end from playlist where cd_num=%d and id in (%d,%d)",
                dest, I(a, REC_ID), b ? I(b, REC_ID) : I(a, REC_ID));
    if (sqlite3_exec(db, sql, ple_checked, &check, 0) || check.seen != (b ? 2 : 1)) goto rollback;
    if (b) {
        /* No HAVING: the firmware's SQLite (3.19.3) refuses one without GROUP BY. The top id gives
         * NULL, which ple_number refuses. */
        if (sqlite3_exec(db,
                         "select case when ifnull(max(id),100)<2147483647 "
                         "then max(100,ifnull(max(id),100))+1 end from playlist",
                         ple_number, &temp, 0) ||
            temp <= 100)
            goto rollback;
        tk_snprintf(sql, sizeof sql,
                    "update playlist set id=%d where cd_num=%d and id=%d;select changes();"
                    "update playlist set id=%d where cd_num=%d and id=%d;select changes();"
                    "update playlist set id=%d where cd_num=%d and id=%d;select changes()",
                    temp, dest, I(a, REC_ID), I(a, REC_ID), dest, I(b, REC_ID), I(b, REC_ID), dest,
                    temp);
    } else
        tk_snprintf(sql, sizeof sql,
                    "delete from playlist where cd_num=%d and id=%d;select changes()", dest,
                    I(a, REC_ID));
    if (sqlite3_exec(db, sql, ple_changed, 0, 0) || sqlite3_exec(db, "commit", 0, 0, 0))
        goto rollback;
    ok = 1;
    goto done;
rollback:
    sqlite3_exec(db, "rollback", 0, 0, 0);
done:
    if (db) sqlite3_close(db);
    return ok;
}

static int ple_query(void *ctx) { return getMusicByPlayList(ple.dest); }
static void ple_reload(void) {
    int n;
    void *songs = staged(ple_query, 0, &n);
    if (ple.songs) deque_destroy(ple.songs);
    ple.songs = songs;
    int size = (int)deque_size(songs);
    if (ple.selected >= size) ple.selected = size ? size - 1 : 0;
}
/* The Add Songs picker's Search row filters by title, artist or album, ASCII case-insensitively.
 * ple.match holds the matching records only; the picker keeps every song, so clearing the query
 * restores the list without re-staging it. */
static int ple_has(const char *text, const char *query) { return text && strcasestr(text, query); }
static void ple_filter(void) {
    free(ple.match);
    ple.match = 0;
    ple.nmatch = 0;
    int n = ple.picker ? (int)deque_size(ple.picker) : 0;
    if (!n || !(ple.match = calloc((unsigned)n, sizeof *ple.match))) return;
    for (int i = 0; i < n; ++i) {
        void *r = deque_at(ple.picker, (unsigned)i);
        char name[512];
        const char *fields[] = { track_name(name, sizeof name, r), P(r, REC_ARTIST),
                                 P(r, REC_ALBUM) };
        for (unsigned f = 0; f < sizeof fields / sizeof *fields; ++f)
            if (ple_has(fields[f], ple.query)) {
                ple.match[ple.nmatch++] = r;
                break;
            }
    }
}
/* Stock's "select" check at the row's end, as the Shuffle and Repeat menus; the caption stops short
 * of it. */
static void *ple_tick(void *item) {
    void *label = item ? widget_get_child(item, 0) : 0;
    if (!label) return 0;
    widget_move_resize(label, I(label, W_X), I(label, W_Y), 330 - I(label, W_X), I(label, W_H));
    void *tick = image_create(item, 340, 0, 24, 48);
    image_base_set_image(tick, "select");
    return tick;
}
static void ple_show(void);
static int ple_closed(void *ctx, void *event) {
    if (ple.timer) timer_remove(ple.timer);
    if (ple.songs) deque_destroy(ple.songs);
    if (ple.picker) deque_destroy(ple.picker);
    free(ple.match);
    unsigned key = ple.key; /* swallow the release even if the page closed during the press */
    memset(&ple, 0, sizeof ple);
    ple.key = key;
    return 0;
}
static void ple_move(int at, int dir) {
    int n = (int)deque_size(ple.songs), next = at + dir;
    if (at < 0 || at >= n || next < 0 || next >= n) return;
    if (ple_mutate(ple.dest, deque_at(ple.songs, (unsigned)at),
                   deque_at(ple.songs, (unsigned)next))) {
        ple.selected = next;
        ple_reload();
        ple_show();
    } else
        toast("Could not save playlist");
}
/* phase: press, release, or native delivery/long press. A repeat never moves twice. */
static int ple_key(unsigned key, int phase) {
    if (key != KEY_BACK_BTN && key != KEY_FWD_BTN) return 0;
    unsigned bit = key == KEY_FWD_BTN ? 2 : 1;
    int live = ple.page && window_manager_get_top_window(window_manager()) == ple.page;
    if (!ple.page && !(ple.key & bit)) return 0;
    if (phase == 1 && !(ple.key & bit)) {
        ple.key |= bit;
        if (live && !ple.mode && !ple.timer && usable())
            ple_move(widget_get_prop_int(ple.view, SEL, ple.selected + 2) - 2,
                     key == KEY_FWD_BTN ? 1 : -1);
    }
    if (phase == 2) ple.key &= ~bit;
    return 1;
}
static int ple_run(const void *unused);
static int ple_back(void *ctx, void *event) {
    if (I(event, EVENT_KEY) != KEY_RETURN) return 0;
    /* The search box's text is applied by the next loop pass, never under its own key event. */
    if (ple.mode == 4) {
        if (!ple.timer) ple.timer = timer_add(ple_run, 0, 0);
        return STOP;
    }
    stop_timer(&ple.timer);
    if (ple.mode) {
        ple.mode = 0;
        ple_reload();
        ple_show();
    } else
        navigator_back();
    return STOP;
}

/* Rebuilds and closes run after click delivery, as the queue menu does. */
static int ple_run(const void *unused) {
    ple.timer = 0;
    if (!ple.page) return 0;
    int action = ple.pending, mode = ple.mode;
    if (!mode) {
        if (action == 1) {
            navigator_back();
            return 0;
        }
        if (!action) {
            int n;
            if (ple.picker) deque_destroy(ple.picker);
            ple.picker = staged_music(all_songs, (void *)1, &n);
            ple_filter();
            ple.mode = 1;
        } else {
            ple.selected = action - 2;
            ple.mode = 2;
        }
    } else if (mode == 1) {
        if (!action)
            ple.mode = 4;
        else if ((unsigned)(action - 1) >= (unsigned)ple.nmatch)
            return 0;
        else {
            void *r = ple.match[action - 1];
            if (checkPlayListExist(r, ple.dest))
                toast("Already in playlist");
            else {
                int before = (int)deque_size(ple.songs);
                void *one = _create_deque("stSongInfo");
                if (!one) {
                    toast("Could not save playlist");
                    return 0;
                }
                deque_init(one);
                _deque_push_back(one, r); /* stock rewrites the added record's ID and cd_num */
                if (!ple_add_select) {
                    ple_add_select = _create_vector("int");
                    if (ple_add_select) {
                        vector_init(ple_add_select);
                        _vector_push_back(ple_add_select, 1);
                    }
                }
                if (ple_add_select) batch_add_file(0xf001, 0xf00b, one, ple_add_select, ple.dest);
                deque_destroy(one);
                ple_reload();
                int added = (int)deque_size(ple.songs) > before;
                if (added) ple_tick(widget_get_child(ple.view, (unsigned)action));
                toast(added ? "Added to Playlist" : "Could not save playlist");
            }
            return 0; /* picker stays on its selected song */
        }
    } else if (mode == 4) {
        if (!ple.edit || widget_get_text_utf8(ple.edit, ple.query, sizeof ple.query))
            ple.query[0] = 0;
        ple.mode = 1;
        ple_filter();
    } else if (mode == 2) {
        if (action == 2)
            ple.mode = 3;
        else {
            ple.mode = 0;
            ple_move(ple.selected, action ? 1 : -1);
        }
    } else {
        if (!action && (unsigned)ple.selected < deque_size(ple.songs)) {
            if (ple_mutate(ple.dest, deque_at(ple.songs, (unsigned)ple.selected), 0))
                ple_reload();
            else
                toast("Could not save playlist");
        }
        ple.mode = 0;
    }
    ple_show();
    return 0;
}
static int ple_pick(void *ctx, void *event) {
    if (!ple.timer) {
        ple.pending = (int)(long)ctx;
        ple.timer = timer_add(ple_run, 0, 0);
    }
    return 0;
}
/* The T9 box over the title, as Internet Radio's pull-down: page children go so the keyboard has
 * room. Its widget is named search_edit, so navigation.c's search_key closes the keyboard before
 * Return can destroy the edit under it. */
static int ple_searched(void *ctx, void *event) {
    widget_set_focused(ple.edit, 0);
    input_method_request(input_method(), 0); /* Detach the keyboard before rebuilding its owner. */
    if (!ple.timer) ple.timer = timer_add(ple_run, 0, 0);
    return 0;
}
static void ple_search_show(void) {
    widget_destroy_children(ple.page);
    ple.view = 0;
    ple.edit = edit_create(ple.page, "text", 2, 26);
    widget_set_name(ple.edit, "search_edit");
    widget_set_prop_str(ple.edit, "tips", "Search songs");
    if (*ple.query) widget_set_text_utf8(ple.edit, ple.query);
    widget_on(ple.edit, EVT_IM_ACTION, ple_searched, 0);
    widget_set_focused(ple.edit, 1);
}
static void ple_show(void) {
    int mode = ple.mode;
    if (mode == 4) {
        ple_search_show();
        return;
    }
    ple.edit = 0;
    int n = mode == 0   ? (int)deque_size(ple.songs) + 2
            : mode == 1 ? (ple.nmatch ? ple.nmatch + 1 : 2)
            : mode == 2 ? 3
                        : 2;
    char title[180], name[512];
    tk_snprintf(title, sizeof title, "%s: %s",
                mode == 0   ? "Editing"
                : mode == 1 ? "Add Songs"
                : mode == 2 ? "Song Actions"
                            : "Remove song?",
                ple.name);
    ple.view = page_list(ple.page, ple.page, 0, title, n, 48);
    static const char *const actions[] = { "Move Up", "Move Down", "Remove from Playlist" };
    if (mode == 1) {
        char search[160];
        tk_snprintf(search, sizeof search, "%s%s", *ple.query ? "Search: " : "Search", ple.query);
        page_row_detail(ple.view, 0, search, 0, ple_pick);
        /* a tick on a song the playlist already holds: same path and CUE segment */
        unsigned mask, *in = ple.nmatch ? rec_index(ple.songs, &mask) : 0;
        if (!ple.nmatch)
            page_row_detail(ple.view, 1, "No songs match", 0, 0);
        else
            for (int i = 0; i < ple.nmatch; i++) {
                void *row = page_row_detail(
                    ple.view, i + 1, track_name(name, sizeof name, ple.match[i]), 0, ple_pick);
                if (in && rec_find(ple.songs, in, mask, ple.match[i])) ple_tick(row);
            }
        free(in);
    } else
        for (int i = 0; i < n; i++) {
            const char *caption =
                mode == 0 && i < 2 ? (i ? "Done" : "Add Songs")
                : mode == 2        ? actions[i]
                : mode == 3 ? (i ? "Cancel" : "Remove from Playlist")
                            : track_name(name, sizeof name,
                                         deque_at(ple.songs, (unsigned)(i - (mode == 0 ? 2 : 0))));
            page_row_detail(ple.view, i, caption, 0, ple_pick);
        }
    if (n) ringnav_select(ple.view, mode == 0 ? ple.selected + 2 < n ? ple.selected + 2 : 0 : 0, n);
}
static int ple_open(void *ctx, void *event) {
    if (ple.page) return 0;
    unsigned at = (unsigned)(long)ctx;
    void *lists = P(p_deque_playlist, 0);
    if (!lists || at >= deque_size(lists)) return 0;
    void *r = deque_at(lists, at);
    ple.dest = I(r, REC_DISC);
    if (ple.dest < 0 || ple.dest >= 100) return 0;
    tk_snprintf(ple.name, sizeof ple.name, "%s", P(r, REC_TITLE));
    ple.page = page_open("playlistedit_page", ple_closed, ple_back);
    if (ple.page) {
        ple_reload();
        ple_show();
    }
    return 0;
}
extern int stock_playlistsmore_trampoline(void *, void *);
/* Stock's allocator assumes ID order; Library presents headers in name order. Allocate under
 * the database lock instead, and report a failed insert instead of pretending it succeeded. */
static int playlist_used(void *ctx, int n, char **v, char **names) {
    if (n != 1 || !v[0]) return 1;
    int id = atoi(v[0]);
    if (id >= 0 && id < 100) ((unsigned char *)ctx)[id] = 1;
    return 0;
}
int ringnav_playlist_create(void *list, const char *name) {
    void *db = 0;
    unsigned char used[100] = { 0 };
    char title[64], quoted[127], sql[512];
    int result = -1, id = 0;
    if (!list || sqlite3_open("/mnt/data/database.db", &db)) goto done;
    if (sqlite3_exec(db, "begin immediate", 0, 0, 0)) goto done;
    if (sqlite3_exec(db, "select id from playlist where id <= 100", playlist_used, used, 0))
        goto rollback;
    for (; id < 100 && used[id]; ++id) {}
    if (id == 100) goto rollback;
    if (name && *name)
        tk_snprintf(title, sizeof title, "%s", name);
    else
        tk_snprintf(title, sizeof title, "Playlist%d", id);
    unsigned j = 0;
    for (unsigned i = 0; title[i]; ++i) {
        quoted[j++] = title[i];
        if (title[i] == '\'') quoted[j++] = '\'';
    }
    quoted[j] = 0;
    int exists = 0;
    tk_snprintf(sql, sizeof sql, "select count(*) from playlist where id <= 100 and filename='%s'",
                quoted);
    /* As stock, the whole name: a cut one matching would make importPlayList file its songs
     * under -2. */
    if ((!name || strlen(name) < sizeof title) && sqlite3_exec(db, sql, ple_number, &exists, 0))
        goto rollback;
    if (exists) {
        result = -2;
        goto rollback;
    }
    tk_snprintf(sql, sizeof sql,
                "insert into playlist values (%d,'%s','','','','','%s','','',0,0,%d,0,0,0,0,0,0);"
                "select changes()",
                id, quoted, quoted, id);
    if (sqlite3_exec(db, sql, ple_changed, 0, 0) || sqlite3_exec(db, "commit", 0, 0, 0))
        goto rollback;
    result = id;
    goto done;
rollback:
    sqlite3_exec(db, "rollback", 0, 0, 0);
done:
    if (db) sqlite3_close(db);
    return result;
}

int ringnav_playlist_delete(void *win, void *event) {
    void *list = P(p_deque_playlist, 0), *db = 0;
    int row = widget_get_prop_int(win, "_playlist", -1), ok = 0;
    if (!list || row < 0 || (unsigned)row >= deque_size(list)) return 0;
    int id = I(deque_at(list, (unsigned)row), REC_ID);
    if (id < 0 || id >= 100) return 0;
    struct {
        int kind, ms;
        char text[0x400];
    } prompt = { 1, 0, "Delete Playlist?" };
    if (navigator_to_with_context("dialog/confirminfo_dialog", &prompt) != 10) return 0;
    char sql[256];
    if (sqlite3_open("/mnt/data/database.db", &db)) goto done;
    if (sqlite3_exec(db, "begin immediate", 0, 0, 0)) goto done;
    tk_snprintf(sql, sizeof sql,
                "delete from playlist where id=%d;select changes();"
                "delete from playlist where id>100 and cd_num=%d",
                id, id);
    if (sqlite3_exec(db, sql, ple_changed, 0, 0) || sqlite3_exec(db, "commit", 0, 0, 0)) {
        sqlite3_exec(db, "rollback", 0, 0, 0);
        goto done;
    }
    ok = 1;
done:
    if (db) sqlite3_close(db);
    if (ok) {
        g_delete_flag = 1;
        navigator_back();
    }
    toast(ok ? "Playlist deleted" : "Could not delete playlist");
    return 0;
}

int ringnav_playlistsmore(void *win, void *ctx) {
    int result = stock_playlistsmore_trampoline(win, ctx);
    if (win) {
        widget_set_prop_int(win, "_playlist", (int)ctx);
        /* Stock's row says Delete Song; this page deletes the whole playlist. Rename only this
         * label, as Now Playing's Delete Song still deletes the song. As its translation key, so a
         * re-translation keeps it (no language has the key, so it reads as given). */
        void *del = widget_lookup(win, "label_del", 1);
        if (del) widget_set_tr_text(del, "Delete Playlist");
        /* A row like this page's stock Rename and Delete: a 375x72 (Legacy 78) list_item holding
         * a 335x68 (Legacy 70) s_btn_listitem, its 52px icon at x 10 and its 20px label at x 72.
         * Edit Playlist follows the Delete row at the same spacing. */
        int h = IPOD ? 72 : 78, inner = IPOD ? 68 : 70;
        void *item = list_item_create(win, 0, IPOD ? 154 : 216, 375, h);
        widget_use_style(item, "s_listitem_black");
        void *button = button_create(item, 20, 0, 335, inner);
        widget_use_style(button, "s_btn_listitem");
        widget_on(button, EVT_CLICK, ple_open, ctx);
        void *image = image_create(button, 10, 0, 52, inner);
        image_base_set_image(image, "rename");
        image_set_draw_type(image, IMAGE_DRAW_CENTER);
        void *label = hscroll_label_create(button, 72, 0, 253, inner);
        widget_use_style(label, "s_scrlabel_white20l");
        set_hscroll_label_attribute(label);
        widget_set_text_utf8(label, "Edit Playlist");
    }
    return result;
}

/* Go to album/artist reuse g_local_classinfo_save, which the pages underneath reload from when
 * they come back, so it is put back as the page opened here closes. */
static int qm_restore(void *page, void *event) {
    memcpy((void *)g_local_classinfo_save, st.qm_classinfo, sizeof st.qm_classinfo);
    return 0;
}

/* Now Playing's More rows, for this record: Album info (0x5283b0) fills the album query and opens
 * playerjumpinfo_page; Artist info opens artistinfo_page with {class, record}. A song played in
 * Coverflow, or with Artist albums: Coverflow, goes to the album's tracks in Coverflow instead, as
 * Go to Artist reaches its covers. */
static void qm_goto(void *r, int album) {
    unsigned char *info = (unsigned char *)g_local_classinfo_save;
    void *wm = window_manager(), *page = window_manager_get_top_window(wm);
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

/* The row alone selected in the stock batch record, as player_addplayList (0x50d0b8) does. */
static void qm_select(void) {
    batch_init_selectrecord((int)st.qm_rows);
    batch_set_selectitem((int)st.qm_idx);
}

/* Stock playlist rows use the queue in add mode 2. Guard their final dispatch as well. */
#define PLAYLIST_PICK ((int (*)(void *, void *))0x4b1d24)
static int qm_playlist_pick(void *ctx, void *event) {
    if (!qm_record()) {
        toast("Queue unchanged");
        return 0; /* a STOP would leave the list's scroll_view holding its grab */
    }
    qm_select();
    return PLAYLIST_PICK(ctx, event);
}

static int qm_playlist_gone(void *ctx, void *event) {
    st.qm_playlist = 0;
    return 0;
}

static void qm_playlist_bind(void *w, int depth) {
    if (!w || depth == 16) return;
    void *emitter = P(w, W_EMITTER);
    for (void *item = emitter ? P(emitter, 0) : 0; item; item = P(item, EMIT_NEXT))
        if (I(item, EMIT_TYPE) == EVT_CLICK && P(item, 12) == (void *)PLAYLIST_PICK &&
            !B(item, EMIT_PENDING_REMOVE)) {
            void *ctx = P(item, 0);
            widget_off_by_func(w, EVT_CLICK, (void *)PLAYLIST_PICK, ctx);
            widget_on(w, EVT_CLICK, qm_playlist_pick, ctx);
            break;
        }
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i)
        qm_playlist_bind(widget_get_child(w, i), depth + 1);
}

/* Rebind the guarded Now Playing picker after stock rebuilds its rows. */
int ringnav_playlist(void *win) {
    int result = stock_playlist_trampoline(win);
    if (win == st.qm_playlist) qm_playlist_bind(win, 0);
    return result;
}

/* Deferred so the dialog is never closed under its own click dispatch. */
static int qm_open(const void *unused);

static int qm_run(const void *unused) {
    st.qm_timer = 0;
    if (!st.qm_dialog) return 0;
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
        if (playback_option(0)) /* a shuffle's play order is not the list's: ours */
            up_next(page_closed, page_keyup);
        else
            navigator_to("playerqueue_page");
    } else if (r && (a == QA_NEXT_GROUP || a == QA_PREV_GROUP)) {
        if (!playback_group_skip(a == QA_NEXT_GROUP)) toast("No other group");
    } else if (aid && a == QA_RATE) {
        rate_show(window_manager_get_top_window(window_manager()), rate_kept(1, aid), aid);
    } else if (aid && (a == QA_FAV || a == QA_UNFAV)) {
        rate_saved(rate_keep(1, aid, a == QA_FAV ? 5 : 0));
    } else if (r && a == QA_RATE) {
        rate_open();
    } else if (r && a >= QA_ADV_SHUFFLE && a <= QA_GROUP) {
        st.qm_choice = a - QA_ADV_SHUFFLE + 1;
        qm_open(0);
    } else if (r && a == QA_FAV) { /* tags a folder file as batch-select's Add to My Fav does */
        qm_select();
        batch_add_file(cls, 0xf00a, qm_list(), P(p_vector_select_record, 0), 0);
    } else if (r && a == QA_UNFAV) {
        deleteMusicFromFav(r);
        if (cls == 0xf00a)
            g_delete_flag = 1;          /* My Fav reloads, as Now Playing's heart (0x52b2e8) */
    } else if (r && a == QA_PLAYLIST) { /* the playlist page's add mode adds the selected row */
        qm_select();
        navigator_to_with_context(
            "localmusic/playlist_page",
            (void *)(long)((st.qm_kind == QM_PLAYING ? 0x20000 : 0x10000) | cls));
        if (st.qm_kind == QM_PLAYING) {
            void *top = window_manager_get_top_window(window_manager());
            if (!tk_strcmp(widget_get_prop_str(top, "name", ""), "playlist_page")) {
                st.qm_playlist = top;
                qm_playlist_bind(top, 0);
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
        widget_set_text_utf8(widget_lookup(dialog, "scrlabel_title", 1), kind == 0   ? "Shuffle"
                                                                         : kind == 1 ? "Repeat"
                                                                                     : "Group by");
        widget_destroy_children(view);
        for (int i = 0; i < count; ++i) {
            void *item = list_item_create(view, 0, i * QM_ROW, 375, QM_ROW);
            widget_use_style(item, "s_listitem_black");
            void *label = hscroll_label_create(item, 30, 0, 310, QM_ROW);
            widget_use_style(label, "s_scrlabel_white24l");
            widget_set_text_utf8(label, names[i]);
            if (i == playback_option(kind))
                image_base_set_image(image_create(item, 340, 0, 24, QM_ROW), "select");
            widget_on(item, EVT_CLICK, qm_pick, (void *)(long)(100 + i));
        }
        widget_set_prop_int(view, "virtual_h", count * QM_ROW);
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
    widget_set_text_utf8(widget_lookup(dialog, "scrlabel_title", 1), title ? title : "");
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
        if (chapter_file(P(r, REC_PATH))) acts[n++] = QA_CHAPTERS;
        acts[n++] = QA_QUEUE;
    }
    if (!song) acts[n++] = QA_SHUFFLE;
    if (song) acts[n++] = checkFavExist(r) ? QA_UNFAV : QA_FAV;
    /* an album's own hearts, 5 its favourite */
    unsigned aid = song ? 0 : qm_album_id(r);
    int album_hearts = aid ? rate_kept(1, aid) : 0;
    if (aid) acts[n++] = album_hearts == 5 ? QA_UNFAV : QA_FAV, acts[n++] = QA_RATE;
    if (st.qm_kind < QM_COVERFLOW || st.qm_kind == QM_PLAYING) acts[n++] = QA_PLAYLIST;
    if ((st.qm_kind == QM_SONG || st.qm_kind == QM_PLAYING) && album && *album &&
        (cls & 0xfff0) != 0xff10 && !navigator_window_is_exist("playerjumpinfo_page") &&
        !coverflow_shows_album(r))
        acts[n++] = QA_ALBUM;
    if ((song || cls == CLASS_ALBUMS) && artist && *artist &&
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
                                        "Chapters" };
    char rated[32];
    /* stock sortselect row styles */
    for (int i = 0; i < n; ++i) {
        void *item = list_item_create(view, 0, i * QM_ROW, 375, QM_ROW);
        widget_use_style(item, "s_listitem_black");
        void *label = hscroll_label_create(item, 30, 0, 266, QM_ROW);
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
        widget_on(item, EVT_CLICK, qm_pick, (void *)(long)acts[i]);
    }
    widget_set_prop_int(view, "virtual_h", n * QM_ROW);
    st.qm_dialog = dialog;
    return 0;
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
        (!playing && !allowed_top(top)) || window_manager_is_animating(wm) ||
        window_manager_get_pointer_pressed(wm) || airplayGetFlag() == 2 || g_navbar_status)
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
    void *album = coverflow_album(top);
    char *key = play_key();
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
    /* A playlist row's hold opens the same More page as its trailing dots. The Add to Playlist
     * picker shares playlist_page's name; its add mode tells the two apart. */
    if (!tk_strcmp(name, "playlist_page") && !I(PLAYLIST_ADD_MODE, 0)) {
        /* The row's index counts Import and Export; its dots' name carries the playlist's. */
        void *more = more_image(g_menu.at[cur]);
        if (!key || !more) return 0;
        st.qm_press = *(unsigned long long *)((char *)key + INPUT_KEY_TIME);
        drop_input();
        navigator_to_with_context("localmusic/playlistsmore_page",
                                  (void *)(long)atoi(widget_get_prop_str(more, "name", "") + 8));
        return 1;
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
 * Play/Pause there switches it (ringnav_keylong). No toast: its msginfo_dialog would cover the
 * pull-down, taking the wheel and Centre for 2 s; the next tick shows the mode. */
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
    return 1;
}
/* Stock's swipe down opens Quick Settings at g_dropdown_menu_state 1 and starts its slide only on
 * the touch's release reaching the dialog (its pointer-up handler adds the timer_idex timer). A
 * release the dialog never gets, as when the window opens slowly at boot, leaves it on top but off
 * screen, holding the wheel. With no touch down and no slide running it is closed, as Return closes
 * the open one; the key that finds it is used up. ponytail: only keys check it; a touch on it shows
 * it, as stock already does. */
static int quick_stuck(void *top) {
    return usable() && top &&
           !tk_strcmp(widget_get_prop_str(top, "name", ""), "statusbar_dialog") &&
           (unsigned)(I(g_dropdown_menu_state, 0) - 1) < 2 &&
           !window_manager_get_pointer_pressed(window_manager()) &&
           widget_get_prop_int(top, "timer_idex", -1) == -1;
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
    if (quick_stuck(top)) return STOP; /* the release closes it (ringnav) */
    const char *fav = radio_hold(top);
    int taken = fav || quick_wheel_toggle(top) || spot_hold(top) || addon_hold(top) || un_hold(top);
    char *key = taken ? play_key() : (char *)0;
    if (taken) {
        if (key) st.qm_press = *(unsigned long long *)(key + INPUT_KEY_TIME);
        drop_input();
        if (fav) toast(fav);
        return STOP;
    }
    if (qm_hold()) return STOP;
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
    return ((const int *(*)(void *, int))0x6a027c)(
        ids, key); /* input_device_status_find_key_long_press_info */
}
unsigned ringnav_hold_timer(int (*fn)(const void *), void *ids, unsigned ms, int key) {
    return timer_add(fn, ids, key == KEY_PLAY || key == KEY_RETURN ? KEY_HOLD_MS : ms);
}

/* Replaces mclNextSong's shuffle pick call: stock picks and bookkeeps, then a pending Play next
 * becomes the next track if it is still at its index. */
int ringnav_shuffle(int forward) {
    int back = playback_back(), result = mcl_shuffle_pick(forward);
    if (back >= 0) MCL(MCL_POS) = back; /* after Previous, Next returns to the track it left */
    void *queue = P(mcl_pdeqplaylist, 0);
    unsigned at = st.qm_forced - 1;
    st.qm_forced = 0;
    if (queue && at < deque_size(queue) && rec_hash(deque_at(queue, at)) == st.qm_forced_hash)
        MCL(MCL_POS) = (int)at;
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

/* Replaces toolsLoadAllFile's 20,000-song check (SCAN_LIMIT): the scan goes on while it holds.
 * A leaf: the scanner reuses its gp after the call without reloading it. */
int ringnav_scan_room(int songs) {
    int folder;
    /* This fixed call site keeps toolsLoadAllFile's third argument (folder scan) in $s5. */
    __asm__("move %0,$21" : "=r"(folder));
    return songs < 65000 || folder; /* at capacity, scan_refresh skips new rows but refreshes old */
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
    /* Most Played's kept tracks take a newly counted song, unless its page shows them (its rows
     * and song menu match the deque); one that loses its count may have hidden the next best,
     * which only the whole library knows. */
    if (st.plays[i].key && st.plays[i].key != key && mp_find(st.plays[i].key) >= 0)
        st.mp_gen = ~library_gen;
    else if (st.mp_list && mp_find(key) < 0) {
        if (!st.mp_page && deque_size(st.mp_list) < 2 * PLAYS_TOP)
            _deque_push_back(st.mp_list, r);
        else
            st.mp_gen = ~library_gen;
    }
    unsigned n = st.plays[i].key == key ? st.plays[i].n + 1 : 1;
    for (; i > 0; i--) st.plays[i] = st.plays[i - 1];
    st.plays[0].key = key;
    st.plays[0].n = n;
    BLOB_IO(PLAYS_FILE, st.plays, 1);
}

/* Scrobbling (docs/internals.md#scrobbling): a Rockbox-style AudioScrobbler 1.1 log at the card's
 * root, for any .scrobbler.log uploader or Upload Scrobbles (scrobble.c). Artist and album are the
 * player's parsed tags when it has parsed this track (now_tag), or with .scrobble.ini's
 * ALBUM_ARTIST the song's album artist when it has one (parsed too: folder queues' records have
 * none); untagged (artist-less) tracks are skipped, as scrobblers reject them. */
static char *scrobble_tag(char *o, char *end, const char *s) {
    for (; s && *s && o < end - 1; s++) *o++ = *s == '\t' || *s == '\n' || *s == '\r' ? ' ' : *s;
    *o++ = '\t';
    return o;
}

static void scrobble(void *r, int total, int heard) {
    const char *artist = now_tag(r, REC_ARTIST), *album_artist = now_tag(r, REC_ALBUM_ARTIST);
    if (!album_artist || !*album_artist)
        album_artist = P(r, REC_ALBUM_ARTIST); /* CUE: none parsed */
    if (album_artist && *album_artist && scrobble_album_artist()) artist = album_artist;
    if (!artist || !*artist) return;
    long long now = wall_time();
    if (now < 1600000000) return; /* clock never set: Last.fm would reject the time */
    char line[800], name[512], *end = line + sizeof line - 48, *o = line; /* 48: the numbers */
    o = scrobble_tag(o, end, artist);
    o = scrobble_tag(o, end, now_tag(r, REC_ALBUM));
    o = scrobble_tag(o, end, track_name(name, sizeof name, r));
    if (I(r, REC_TRACK) > 0) o += tk_snprintf(o, 12, "%d", I(r, REC_TRACK));
    o += tk_snprintf(o, (unsigned)(line + sizeof line - o), "\t%d\tL\t%u\t\n", total,
                     (unsigned)(now - heard));
    scrobble_append(line, (unsigned)(o - line));
}

/* The polled track's place at sec, its file's seconds. A CUE track becomes the book's place only
 * past its own first RESUME_EDGE_S; only the last one (no CUE end) knows the file's end, where the
 * book is finished and forgotten. */
static void spot_save(int sec) {
    if (!(st.rs_cue || st.rs_end) || sec >= RESUME_EDGE_S)
        spot_keep(st.rs_file, st.rs_cue + sec, st.rs_end ? 0x7fffffff : st.rs_cue + st.rs_total);
}

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
    if (!r || player_playtime_and_length(&sec, &total) < 0 || total <= 0) return;
    unsigned key = listen_key(r);                   /* a CUE image's tracks share one path */
    if (now - st.rs_go_at > 8000) st.rs_go_key = 0; /* a Resume row's chapter never came */
    if (key != st.rs_key) {
        if (st.rs_long && !st.rs_pending) spot_save(st.rs_sec);
        st.rs_key = key;
        st.rs_long = 0;
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
    if (st.ch_key == key)
        st.rs_pending = 0;
    else
        st.ch_key = 0;
    /* A listen: half the track or LISTEN_MAX_S heard, seeks and pauses aside; a repeat starts over.
     */
    if (!st.rs_pending && !st.rs_spoken) {
        unsigned lk = listen_key(r);
        if (lk != st.ls_key || (sec < 2 && st.ls_sec > LISTEN_MIN_S)) {
            st.ls_key = lk;
            st.ls_sec = sec;
            st.ls_heard = st.ls_done = 0;
        }
        int d = sec - st.ls_sec;
        if (d > 0 && d <= 2) st.ls_heard += d;
        st.ls_sec = sec;
        if (!st.ls_done && total > LISTEN_MIN_S &&
            st.ls_heard >= (total / 2 < LISTEN_MAX_S ? total / 2 : LISTEN_MAX_S)) {
            st.ls_done = 1;
            play_count(r, lk);
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
            player_seek_time(st.rs_pending); /* blocks the UI up to 2 s, as Scrub's commit */
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

/* Charge limit (docs/internals.md#charge-limit): get_battery_capacity keeps g_power_capacity and
 * g_power_chargestate (1 Charging, 2 Full, the AXP2101's battery status); switch_charge_enable
 * drives the charge enable, as stock's USB mode does with g_usbdac_chargeflag. At CHARGE_STOP
 * charging stops, and again whenever something else (USB mode's exit, AirPlay) turned it back on,
 * until the level falls to CHARGE_RESUME; then, or with the limit off, charging is handed back as
 * stock would have it: USB mode's and AirPlay's own choice while their page is open, else on. */
static void charge_poll(void) {
    unsigned now = time_now_ms();
    if (st.charge_at && now - st.charge_at < CHARGE_POLL_MS) return;
    st.charge_at =
        now | !now; /* never 0, no poll yet; |1 would eat a millisecond when now is even */
    int level = I(g_power_capacity, 0), charging = (unsigned)I(g_power_chargestate, 0) - 1 < 2;
    int hold = st.charge_limit && level > CHARGE_RESUME && (st.charge_held || level >= CHARGE_STOP);
    if (hold && (!st.charge_held || charging))
        switch_charge_enable(0);
    else if (!hold && st.charge_held)
        switch_charge_enable(navigator_window_is_exist("usbmode_page") ||
                                     navigator_window_is_exist("airplay_page")
                                 ? g_usbdac_chargeflag
                                 : 1);
    st.charge_held = hold;
}

/* The Wi-Fi signal (docs/internals.md#battery), for networkGetWifiSignal's two pollers: the status
 * bar's bars (get_wifisignal) and the network monitor thread's connect status, each about once a
 * second. Stock asks with SIOCGIWSTATS, which the driver answers through get_station, whose TX
 * report takes the radio out of power save (LPS) every time. The driver keeps the same signal in
 * rx_signal, a percentage that get_station reports less 100 as dBm, and reading it touches no
 * hardware. Unreadable, it is 0, as stock's failed ioctl. Stock's unsigned level wraps the one
 * value 0 dBm (100%) to -256, one bar; here it is 0, four. */
#define WIFI_SIGNAL "/proc/net/rtl8733bs/wlan0/rx_signal"
static struct {
    unsigned at;
    int dbm;
} wifi __attribute__((section(".scratch")));

int ringnav_wifi_signal(void) {
    unsigned now = time_now_ms();
    if (wifi.at && now - wifi.at < WIFI_SIGNAL_MS) return wifi.dbm;
    char text[512];
    int fd = open(WIFI_SIGNAL, 0x80000); /* O_RDONLY | O_CLOEXEC, MIPS */
    int n = fd < 0 ? 0 : read(fd, text, sizeof text - 1);
    if (fd >= 0) close(fd);
    text[n > 0 ? n : 0] = 0;
    const char *s = strstr(text, "signal_strength:");
    int pct = 0;
    if (s)
        for (s += 16; *s >= '0' && *s <= '9'; ++s) pct = pct * 10 + *s - '0';
    wifi.dbm = s ? pct - 100 : 0;
    wifi.at = now | !now; /* never 0, not read yet */
    return wifi.dbm;
}

/* Low power's second core (docs/internals.md#low-power): CPU1 is offline while the screen is off
 * and no video plays, so the player decodes on one core and the UI, Coverflow and videos keep both.
 * The first offline of a boot is bracketed by CPU1_PENDING, synced to flash: if a kernel stalled on
 * it, the next boot finds the file, renames it CPU1_BAD and never tries again. A write that fails
 * (no hotplug) stops the attempts until the next boot. */
#define CPU1_ONLINE "/sys/devices/system/cpu/cpu1/online"
#define CPU1_PENDING "/mnt/data/q2pod-cpu1"
#define CPU1_BAD "/mnt/data/q2pod-cpu1.bad"
static int cpu1_write(int on) {
    void *f = fopen(CPU1_ONLINE, "w");
    if (!f) return 0;
    int ok = fwrite(on ? "1" : "0", 1, 1, f) == 1;
    return !fclose(f) && ok;
}
static void cpu_poll(void) {
    if (!st.low_power && !st.cpu_off) return; /* off: no file is touched */
    if (!st.cpu_bad) {                        /* once a boot */
        int pending = !access(CPU1_PENDING, 0);
        if (pending) rename(CPU1_PENDING, CPU1_BAD);
        st.cpu_bad = pending || !access(CPU1_BAD, 0) ? 2 : 1;
    }
    int off = st.low_power && !g_backlight_status && !video_on() && st.cpu_bad == 1;
    if (off == st.cpu_off) return;
    unsigned now = time_now_ms();
    if (!off) { /* a refused online is retried once a second */
        if (st.cpu_retry && now - st.cpu_retry < 1000) return;
        st.cpu_off = !cpu1_write(1);
        st.cpu_retry =
            st.cpu_off ? now | !now : 0; /* never 0; |1 would eat a millisecond when now is even */
        return;
    }
    if (!st.cpu_marked) { /* no marker on flash, no offline: the guard must hold */
        void *mark = fopen(CPU1_PENDING, "w");
        int synced = mark && !fflush(mark) && !fsync(fileno(mark));
        if (mark && fclose(mark)) synced = 0;
        int dir = open("/mnt/data", 0); /* persist the marker's directory entry before hotplug */
        if (dir < 0)
            synced = 0;
        else {
            if (fsync(dir)) synced = 0;
            close(dir);
        }
        if (!synced) {
            st.cpu_bad = 3;
            return;
        }
    }
    st.cpu_off = cpu1_write(0);
    if (!st.cpu_marked) {
        unlink(CPU1_PENDING);
        int dir = open("/mnt/data", 0); /* O_RDONLY: the unlink reaches flash too */
        if (dir >= 0) fsync(dir), close(dir);
        st.cpu_marked = 1;
    }
    if (!st.cpu_off) st.cpu_bad = 3;
}
/* Standby limit (docs/internals.md#standby-limit): device_standby puts the radios, the card and the
 * DAC down, then toolsIntoStandby suspends to RAM ("mem" to /sys/power/state) and returns once
 * something resumes it. With a limit, the RTC's wake alarm is set that far ahead first and cleared
 * after. A resume at or past the alarm is the alarm's, and the next UI loop pass powers off as
 * stock's Auto power-off does (poweroff_page), unless a cable is in or the music plays. A key or
 * anything else that resumes it sooner leaves it on, and the next Standby counts from zero.
 * Without the RTC's time, or with an alarm it refuses, Standby is stock's. */
#define RTC_NOW "/sys/class/rtc/rtc0/since_epoch"
#define RTC_ALARM "/sys/class/rtc/rtc0/wakealarm"
static unsigned rtc_now(void) { /* the RTC's seconds since 1970 (UTC, RTC_WRITE); 0 unreadable */
    char s[16];
    void *f = fopen(RTC_NOW, "rb");
    if (!f) return 0;
    unsigned n = fread(s, 1, sizeof s, f), t = 0;
    fclose(f);
    for (unsigned i = 0; i < n && s[i] >= '0' && s[i] <= '9'; ++i) t = t * 10 + s[i] - '0';
    return t;
}
static int rtc_alarm(unsigned at) { /* 0 clears it; one already set refuses another (EBUSY) */
    char s[16];
    void *f = fopen(RTC_ALARM, "w");
    if (!f) return 0;
    unsigned n = (unsigned)tk_snprintf(s, sizeof s, "%u", at);
    int ok = fwrite(s, 1, n, f) == n;
    return !fclose(f) && ok;
}
int ringnav_standby(void) {
    static const unsigned hours[] = { 0, STANDBY_LIMIT_1, STANDBY_LIMIT_2, STANDBY_LIMIT_3 };
    pod_settings();
    unsigned start = st.standby_limit ? rtc_now() : 0;
    unsigned alarm = start + hours[st.standby_limit] * 3600;
    int armed = start && rtc_alarm(0) && rtc_alarm(alarm);
    int result = stock_standby_trampoline();
    if (armed) {
        unsigned now = rtc_now();
        rtc_alarm(0);
        st.standby_off = now >= alarm;
    }
    return result;
}
static void standby_poll(void) {
    if (!st.standby_off) return;
    st.standby_off = 0;
    if (!g_usbdet_value && mclGetPlayStatus() != 2) navigator_to("poweroff_page");
}
/* User boot logo (docs/boot.md): the card's LOGO_SRC is kept as LOGO_DST, which the splash shows at
 * power-on, each time the card is mounted (boot, the end of USB storage). A 375x320 one is turned
 * upright by stock ffmpeg; anything but a 320x375 baseline JPEG is refused and LOGO_DST kept. No
 * card file, no LOGO_DST: the stock logo again. The thread keeps ffmpeg and flash off the UI; the
 * poll toasts its failure. */
#define LOGO_SRC "/mnt/mmc/Q2Pod/boot/boot-logo.jpg"
#define LOGO_DST "/mnt/data/boot-logo.jpg"
#define LOGO_ROT "/tmp/boot-logo.jpg"
#define LOGO_MAX (512 * 1024)
/* tools/build.py's jpeg_size: w << 16 | h of a baseline 8-bit three-component JPEG, the only kind
 * stock display_logo draws right; 0 for anything else. */
static unsigned jpeg_size(const unsigned char *b, unsigned n) {
    unsigned i = 2, m, size;
    if (n < 2 || b[0] != 0xff || b[1] != 0xd8) return 0;
    while (i < n) {
        if (b[i] != 0xff) return 0;
        while (i < n && b[i] == 0xff) i++;
        if (i >= n) return 0;
        m = b[i++];
        if (m == 0xd9 || m == 0xda || m == 0 || m == 0xd8) return 0;
        if (m == 1 || (m >= 0xd0 && m <= 0xd7)) continue;
        if (i + 2 > n || (size = b[i] << 8 | b[i + 1]) < 2 || i + size > n) return 0;
        if (m >= 0xc0 && m <= 0xcf && m != 0xc4 && m != 0xc8 && m != 0xcc) {
            if (size < 8 || size != 8 + 3u * b[i + 7] || m != 0xc0 || b[i + 2] != 8 ||
                b[i + 7] != 3)
                return 0;
            unsigned h = b[i + 3] << 8 | b[i + 4], w = b[i + 5] << 8 | b[i + 6];
            return w && h ? w << 16 | h : 0;
        }
        i += size;
    }
    return 0;
}
static void *logo_sync(void *unused) {
    unsigned n = 0, old_n;
    unsigned char *b = 0, *old = 0;
    const char *err = "Boot logo must be a 320x375 JPEG";
    if (access(LOGO_SRC, 0))
        unlink(LOGO_DST), err = 0;
    else if ((b = read_all(LOGO_SRC, LOGO_MAX, &n)) && jpeg_size(b, n) == (375u << 16 | 320)) {
        free(b),
            b = 0; /* landscape: ffmpeg turns it a quarter clockwise (coverflow.c's ff_thumb) */
        int status, pid = fork();
        if (!pid) {
            execl("/usr/bin/ffmpeg", "/usr/bin/ffmpeg", "-nostdin", "-v", "quiet", "-i", LOGO_SRC,
                  "-vf", "transpose=1", "-pix_fmt", "yuvj444p", "-q:v", "2", "-f", "mjpeg", "-y",
                  LOGO_ROT, (char *)0);
            child_exit(127);
        }
        if (pid > 0 && waitpid(pid, &status, 0) == pid && !status)
            b = read_all(LOGO_ROT, LOGO_MAX, &n);
        unlink(LOGO_ROT);
    }
    if (b && jpeg_size(b, n) == (320u << 16 | 375)) {
        old = read_all(LOGO_DST, LOGO_MAX, &old_n);
        if ((old && old_n == n && !memcmp(old, b, n)) ||
            blob_io(LOGO_DST, LOGO_DST ".tmp", b, n, 1))
            err = 0; /* the same bytes are not written again each boot */
        else
            unlink(LOGO_DST ".tmp"), err = "Could not save boot logo";
    }
    free(b), free(old);
    st.logo_err = err;
    st.logo_busy = 0;
    return 0;
}
static void logo_poll(void) {
    unsigned now = time_now_ms();
    if (st.logo_err) toast(st.logo_err), st.logo_err = 0;
    /* Mount edges (boot, USB storage's end) come lit; dark it keeps its edge for the first lit
     * poll. */
    if (!g_backlight_status || st.logo_busy || (st.logo_at && now - st.logo_at < CHARGE_POLL_MS))
        return;
    st.logo_at = now | !now;
    char m[4096]; /* ponytail: the Q2's few mounts fit (remote.c's card_mounted) */
    int mounted = 0;
    void *f = fopen("/proc/mounts", "rb");
    if (f) {
        unsigned n = fread(m, 1, sizeof m - 1, f);
        fclose(f);
        m[n] = 0;
        mounted = strstr(m, " /mnt/mmc ") != 0;
    }
    unsigned long tid;
    if (mounted && !st.logo_mounted) {
        st.logo_busy = 1;
        if (pthread_create(&tid, 0, logo_sync, 0)) {
            st.logo_busy = 0; /* the edge is tried again next poll */
            return;
        }
        pthread_detach(tid);
    }
    st.logo_mounted = mounted;
}
static void power_poll(void) {
    pod_settings();
    charge_poll();
    cpu_poll();
    standby_poll();
    logo_poll();
}

#if IPOD
/* Home's Rockbox row (docs/boot.md#rockbox-from-home), bound by coverflow_home: a click leaves
 * Q2 Pod for Rockbox. demo cannot start it itself: Rockbox needs the screen, the keys and ALSA. So
 * demo leaves S90play's flag and ends; S90play sees it, runs Rockbox and starts demo again when
 * Rockbox exits. Bluetooth's daemons are not demo's children and keep the headphones connected, so
 * Rockbox finds them and plays there (q2-rockbox's pcm-alsa). The queue is saved as power-off
 * saves it, charging and the second core are handed back as Rockbox expects them at boot, the
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
    if (st.cpu_off) cpu1_write(1);
    /* Rockbox's volume is pcm-alsa's software mixer alone, so the headset's absolute volume, left
     * at the Q2's, would cap it: open it to the maximum volume limit. Demo syncs it back on return.
     */
    if (bt_link() && g_bluetoothflag && bt_linkstatus &&
        (unsigned)btctl_transport_get_volume() <= 127)
        btctl_transport_set_volume(((g_maxvolume < 100 ? g_maxvolume : 100) * 127 + 50) / 100);
    system("killall checkappprocess.sh; killall -9 hciplayer; " SPOT_KILL "; sync; kill -9 $PPID");
    return 0;
}
#endif

#if IPOD
/* Now Playing on top. */
static int now_playing(void) {
    void *top = window_manager_get_top_window(window_manager());
    return top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "playing_page");
}
#endif

/* Now Playing's visualizer repainting, its album marquee moving or its lyrics gliding: their 40 and
 * 50 ms timers and the glide's steps would run late on an idle pass. Its progress and the lyrics'
 * follow tick every 250 ms and keep up. A game's 33 ms frame timer too: Vortex's raw wheel posts no
 * input to stamp. */
static int np_animating(void) {
#if IPOD
    return games_live() ||
           (now_playing() && (visualizer_live() || st.lyric_glide ||
                              (st.np_marquee && st.np_off > 0 && st.np_off < st.np_over)));
#else
    return 0;
#endif
}

/* Keep stock VOLUME for wired/USB and boot; Bluetooth saves its own level. */
int ringnav_write_config(int value, const char *section, const char *key) {
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
    *(volatile signed char *)0xa38c41u = (signed char)v; /* stock responseSetVolume's level */
    device_set_volume(v, 1);
    write_int_config(v, "PLAYSET", "VOLUME");
    void *top = window_manager_get_top_window(window_manager());
    if (top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "volume_dialog")) {
        void *slider = widget_lookup(top, "slider_vol", 1);
        if (slider) widget_set_prop_int(slider, "value", v);
    }
}

/* main_loop_sleep_default paces the UI loop at 8 ms (125 Hz), screen on or off. With the backlight
 * off it first idles SCREEN_OFF_SLEEP_MS (DARK_IDLE_SLEEP_MS once nothing plays and the DAC is
 * off, unless Wake's second press may be coming); stock then finds its 8 ms gone, sleeps 0 and
 * keeps its own bookkeeping. The first pass with it back on repaints every window once, so nothing
 * drawn while dark, or only partly, stays on screen until the next input; after a centre press woke
 * it, it also restarts Wake's window for a dropped press (woke_at). With the screen on, a pass
 * idles IDLE_SLEEP_MS once input and window animations have been still for IDLE_MS, except while
 * Now Playing animates. */
static volatile int dark_quiet; /* read by ringnav_dark_usleep on stock's threads */
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

int ringnav_sleep(void *loop) {
    headset_poll();
    bt_volume_poll();
    video_poll();
    spot_poll();
    radio_poll();
    addon_poll();
    playback_poll();
    resume_poll();
    power_poll();
#if IPOD
    lyric_poll();
    if (st.bc[0] && np_track() != st.bc_track) bc_show();
#endif
    if (!g_backlight_status) {
        st.dark |= 1;
        int quiet = I(g_dacoff_time, 0) < 0 && !media_playing() && !video_on() &&
                    airplayGetFlag() != 2 && (signed char)bt__recv_pageflag != 2 &&
                    time_now_ms() - st.wake_at >= WAKE_MS;
        dark_quiet = quiet;
        sleep_ms(quiet ? DARK_IDLE_SLEEP_MS : SCREEN_OFF_SLEEP_MS);
    } else if (st.dark) {
        dark_quiet = 0;
        wheel_guard_reset();
        if (st.dark & 2) st.woke_at = (unsigned)time_now_ms();
        st.dark = 0;
#if IPOD
        if (st.clock_timer) clock_show(); /* a time set while dark */
#endif
        widget_invalidate_force(window_manager(), (void *)0);
    } else if (time_now_ms() - st.last_input >= IDLE_MS &&
               !window_manager_is_animating(window_manager()) && !np_animating())
        sleep_ms(IDLE_SLEEP_MS);
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
 * UI; a key's release goes to the player instead. Every event times ringnav_sleep's idle. */
int ringnav(void *ctx, void *event);
int ringnav_input(void *wm, void *e) {
    if (e && wake_gate(e)) return 0;
    st.last_input = time_now_ms();
    if (video_on()) {
        if (e && I(e, EVENT_TYPE) == EVT_KEY_UP) video_key((unsigned)I(e, EVENT_KEY));
        return 0;
    }
    unsigned type = e ? (unsigned)I(e, EVENT_TYPE) : 0;
    unsigned key = e ? (unsigned)I(e, EVENT_KEY) : 0;
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
        int value = g_volume + (key == KEY_NEXT ? 1 : -1);
        if (value < 0) value = 0;
        if (value > g_maxvolume) value = g_maxvolume;
        if (value > 100) value = 100;
        if (value != g_volume) {
            g_volume = value;
            device_set_volume(value, 1);
            write_int_config(value, "PLAYSET", "VOLUME");
        }
        return 0;
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
 * (vtable +0xc), except while q2video has the framebuffer (video_poll repaints after) and while the
 * screen is off, unless a window animates (AWTK steps it from the paint); the first lit pass
 * repaints the whole screen (ringnav_sleep; docs/internals.md Battery). */
int ringnav_wm_paint(void *wm) {
    if (wm && !g_backlight_status && !window_manager_is_animating(wm)) return 0; /* RET_OK */
    void *vt = wm && !video_on() ? P(wm, 0x94) : (void *)0;
    int (*paint)(void *) = vt ? (int (*)(void *))P(vt, 0xc) : (int (*)(void *))0;
    return paint ? paint(wm) : video_on() ? 0 : 0x10; /* RET_BAD_PARAMS, as stock */
}

/* buzzeer_switch (0x4f3cc8), every Key Tone click, stock's and ringnav()'s: the MCU's buzzer
 * (system("cmd_mcu write_str buzzer") when g_keytone_flag is set). Silent while headphones (either
 * jack) or a Bluetooth or USB output listen, since the buzzer is the device's own speaker;
 * hciplayer holds their PCM, so the click has no other way out. Playback alone leaves it clicking.
 * The command waits out the buzzer and the MCU's park (tools/mcu_pwm_clock.c), so it runs on a
 * worker, not the UI thread; a click while one sounds is dropped, as the buzzer is busy anyway.
 */
static volatile int tone_busy;
static void *tone_worker(void *on) {
    stock_buzzer_trampoline((int)(long)on);
    tone_busy = 0;
    return 0;
}
int ringnav_buzzer(int on) {
    if (g_po_status || g_bal_status || mclGetOutputWay() || !g_keytone_flag || tone_busy) return 0;
    unsigned long tid;
    tone_busy = 1;
    if (pthread_create(&tid, 0, tone_worker, (void *)(long)on))
        tone_busy = 0;
    else
        pthread_detach(tid);
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
    if (quick_stuck(top)) return STOP; /* the release closes it (ringnav) */
#if IPOD
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
#endif
    int result = stock_keydown_trampoline(ctx, event);
#if IPOD
    if (st.tone_key) g_keytone_flag = tone;
#endif
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

static int fields(void *top) {
    const char *name = top ? widget_get_prop_str(top, "name", "") : "";
    return !tk_strcmp(name, "manualtime_page") ? 5 : !tk_strcmp(name, "sleepshutdown_page") ? 2 : 0;
}

static void *field_widget(void *top, int f) {
    static const char *const names[] = { "tselector_year", "tselector_month", "tselector_day",
                                         "tselector_hour", "tselector_min",   "btn_enter" };
    int n = fields(top);
    return n ? widget_lookup(top, names[f == n ? 5 : n == 2 ? f + 3 : f], 1) : (void *)0;
}

/* The field the wheel edits; a swipe between the date and time pages moves it to that page's first.
 */
static int field_at(void *top) {
    int n = fields(top), f = clamp_step(widget_get_prop_int(top, "_edit_field", 0), n, 0);
    void *slide = n == 5 && f < n ? widget_lookup(top, "slide_view", 1) : (void *)0;
    int time = slide && widget_get_prop_int(slide, "value", 0) == 1;
    if (slide && time != (f >= 3)) f = time ? 3 : 0;
    return f;
}

static void field_set(void *top, int f) {
    prop(top, "_edit_field", f);
    void *slide = fields(top) == 5 && f < 5 ? widget_lookup(top, "slide_view", 1) : (void *)0;
    if (slide) widget_set_prop_int(slide, "value", f >= 3);
    widget_invalidate_force(top, (void *)0);
}

/* The side keys move between the fields and OK: Next on, Previous back, stopping at the ends. */
static int field_key(void *top, unsigned key) {
    int n = fields(top);
    if (!n || (key != KEY_FWD_BTN && key != KEY_BACK_BTN)) return 0;
    cancel_center(); /* a pending Centre would step (or press OK) again */
    field_set(top, clamp_step(field_at(top), n, key == KEY_FWD_BTN ? 1 : -1));
    return 1;
}

static int edit_single(const void *info) {
    st.edit_press = 0;
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    if (!usable() || window_manager_is_animating(wm) || window_manager_get_pointer_pressed(wm))
        return 0;
    *(volatile unsigned char *)KEY_LOCKOUT = 0; /* stock's wheel lockout after the centre key */
    int n = fields(top), f = field_at(top);
    if (!n) {
        if (slider_page(top)) navigator_back();
    } else if (f < n)
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
    if (quick && quick_volume()) return 0; /* stock's wheel volume */
    void *wm = window_manager();
    if (window_manager_is_animating(wm) || window_manager_get_pointer_pressed(wm)) return 1;
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
        if (f == n)
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
/* A wheel tick the page cannot take yet: counted (a reversal cancels), its event kept for replay.
 */
static int hold_wheel(void *ctx, void *event, int dir) {
    unsigned now = (unsigned)time_now_ms();
    if (!st.held) st.held_at = now;
    if (st.held * dir < HOLD_MAX) st.held += dir;
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
    if (window_manager_is_animating(wm) || window_manager_get_pointer_pressed(wm)) return 8;
    int dir = st.held > 0 ? 1 : -1;
    unsigned key = dir > 0 ? KEY_NEXT : KEY_PREV;
    st.held_event[EVENT_KEY / 4] = key;
#if IPOD
    st.tone_key = key; /* clicks on its row change, as a live tick whose press was silenced */
#endif
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
 * first, so the blur's EVT_VALUE_CHANGED reads as no entry and its page shows what it had. */
static int search_key(unsigned key) {
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
#if IPOD
    /* This release's press was silenced by ringnav_keydown: a row change below clicks instead. */
    int owned = st.tone_key == key;
    st.tone_key = 0;
#endif
    if ((key == KEY_PREV || key == KEY_NEXT) &&
        *(volatile signed char *)KEY_LOCKOUT <= 8 - LOCKOUT_KEEP)
        *(volatile unsigned char *)KEY_LOCKOUT = 0;
    if (key == KEY_CENTER || key == KEY_RETURN) st.held = 0; /* a button ends a held turn */
    void *front = window_manager_get_top_window(window_manager());
    if (quick_stuck(front)) {
        drop_input();
        dialog_quit(front, 0);
        return STOP;
    }
    if (usable() && field_key(front, key)) return STOP; /* before stock's track skip */
    /* The rating picker's keys, before the page's own (its Return would leave it) */
    if (usable() && rate_key(window_manager_get_top_window(window_manager()), key)) return STOP;
#if IPOD
    /* A game's keys, before stock's lock filter, media keys and screen-off double Centre */
    if (usable() && games_key(window_manager_get_top_window(window_manager()), key)) return STOP;
#endif
#if IPOD
    /* Return or Centre closes quick settings, as its swipe up does (dialog_quit); stock leaves
     * Return to its Home button and Centre to turning the screen off. A held Return's release
     * goes to the stock filter, which consumes its latch (a hold may go Home from here). */
    if ((key == KEY_CENTER ||
         (key == KEY_RETURN && !*(volatile unsigned char *)RETURN_RELEASE_LATCH)) &&
        usable()) {
        void *top = window_manager_get_top_window(window_manager());
        if (top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "statusbar_dialog")) {
            dialog_quit(top, 0);
            return STOP;
        }
    }
#endif
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
#if IPOD
    if (key == KEY_RETURN && st.scrub) { /* ends the scrub without leaving the page */
        np_cancel();
        if (window_manager_get_top_window(window_manager()) == st.np_win) return STOP;
    }
    /* Home's Settings list goes back to Home's list (coverflow_home_back). */
    if (key == KEY_RETURN && coverflow_home_back(window_manager_get_top_window(window_manager())))
        return STOP;
    /* Return on Home's main list selects Now Playing (its first row); on that row it opens Now
     * Playing with the current queue untouched. */
    if (key == KEY_RETURN && usable()) {
        void *wm = window_manager(), *top = window_manager_get_top_window(wm);
        void *w = home_menu(top) && !window_manager_is_animating(wm)
                      ? surface_under(top, (void *)0, (void *)0, 1)
                      : (void *)0;
        if (w && load(&g_menu, w, 1) && g_menu.n) {
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
#else
    if (key == KEY_RETURN) st.touch_mode = 0; /* Back by button: the page behind shows its row. */
#endif
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

#if IPOD
    if (top != st.np_win || window_manager_is_animating(wm) ||
        window_manager_get_pointer_pressed(wm))
        np_cancel();
    else if (key != KEY_CENTER && np_lyrics(key))
        return STOP;
    else if (key == KEY_CENTER || st.scrub)
        return np_key(top, key);
#endif
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
    if (window_manager_is_animating(wm) || window_manager_get_pointer_pressed(wm)) {
        drop_input();
        return dir && window_manager_is_animating(wm) ? hold_wheel(ctx, event, dir) : STOP;
    }
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
        int speed = ramp(top, w, g_menu.scope, g_menu.ctx, dir, now, CF_FIRST_MS, LIST_RAMP_MS);
        home_step(w, dir, now, speed > 1);
        widget_invalidate_force(w, (void *)0);
        return STOP;
    }
    /* Every accepted tick times the run and takes its place in it, short lists included. */
    int list = IPOD && g_menu.n; /* iPod row lists: the gentler ramp */
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
#if IPOD
        if (step > 1) rearm(&st.letter_timer, letter_expire, LETTER_MS);
#endif
        int next = clamp_step(id < 0 ? (cur >= 0 ? g_menu.id[cur] : 0) : id, g_menu.rows - 1,
                              id < 0 ? 0 : dir * step);
        if (next == 0 || next == g_menu.rows - 1) drop_wheel();
        if (next == id) {
            int home = IPOD && home_menu(top) && g_menu.rows >= 2;
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
#if IPOD
        if (owned && id >= 0) buzzeer_switch(1); /* one click per row change, however far */
#endif
    } else {
        int top = view_top(&g_menu);
        int next = clamp_step(top, max_top(&g_menu), dir * RING_STEP * step);
        if (next == 0 || next >= max_top(&g_menu)) st.wheel_run = 0;
        wheel_offset(&g_menu, next);
    }
    widget_invalidate_force(w, (void *)0);
    return STOP;
}
