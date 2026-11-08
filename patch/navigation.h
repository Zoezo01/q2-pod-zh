#ifndef Q2_NAVIGATION_H
#define Q2_NAVIGATION_H
/* navigation.c's shared state, types and macros, and the functions each file of tools/build.py's
 * NAVIGATION calls in another. */
#include "chapters.h"
#include "offsets.inc"
#include "peq.h" /* libc/libcstl imports (deque_*, send) and the shared helpers */
#include "playback.h"
#include "stock.h"
#include "themes.h" /* tools/theme.py: the themes' enums and macros; theme_engine.c has the tables */
#include "xiph.h"
#undef select /* navigation.c's UI selection helper; headset.c uses libc select */

#define STOP 11
#define MAX_ENTRIES 512
#define POS_MEM 64
#define B(p, o) (*(unsigned char *)((char *)(p) + (o)))

typedef struct {
    int ctx, id; /* logical row + 1; 0 = unused */
    unsigned scope, hash, hash2;
} position_t;

/* The settings rows pod_rows builds (pod_values and the Q2POD keys below). */
enum {
    POD_CHARGE,
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
    POD_EQ_OUTPUT,  /* EQ per Output: the value and key; its row is Advanced EQ's (peq_ui.c) */
    POD_KEYTONE, /* Key Tone and In-Vehicle Mode: stock's bytes and SYSSET keys, as its rows did */
    POD_CARMODE,
    POD_SMART_WIFI,
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
    int rs_long, rs_settle, rs_sec, rs_saved, rs_still, rs_pending, rs_cue, rs_end, rs_total, rs_go,
        rs_stopped;
    int rs_spoken; /* under Podcasts or Audiobooks: resumed at any length, never counted */
    /* Play counts by path hash (plays_read once loaded); the playing track's last second, the
     * seconds of it heard, whether this play has counted and been scrobbled, and .scrobble.ini's
     * PERCENT (1-100, else 0) as it read when the play began. */
    struct {
        unsigned key, n;
    } plays[PLAYS_SLOTS];
    unsigned plays_read, ls_key;
    int ls_sec, ls_heard, ls_done, ls_sent, ls_pct;
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
    int ch_cue, ch_end;        /* the Chapters track's CUE start and end, file seconds */
    void *sc_page, *sc_row[3]; /* Streaming's Configuration page, its cache rows and Erase's */
    int sc_armed;              /* Erase Spotify Data pressed once: the next press erases */
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
    int empty_kind; /* the empty Library list empty_toast names and closes */
    int rate_pick, rate_held;
    unsigned rate_hold, rate_tick, rate_album;
    void *rt_menu, *rt_page, *rt_list;
    int dark; /* 1: the backlight was off at the last UI loop pass; 2: a centre press woke it */
    /* Power's Charge limit (read once from config.ini's Q2POD), the value labels of that row
     * and Artists, and the poll: charging is held off at the limit;
     * last_input times the screen-on idle. year_old: Year order, oldest first. resume:
     * Bluetooth's Resume on connect on. hold_home: Hold Return goes Home. standby_limit: Standby
     * limit's choice; standby_off: its alarm ended a Standby, so the next pass powers off.
     * smart_wifi: Power's Smart Wi-Fi on (smart_poll). */
    int pod_read, charge_limit, wake_double, wake_pass, wake_eat,
        wake_down, /* 1: the held Centre press passes; 2: it is dropped */
        charge_held, artist_cf, year_old, resume, lock_clock, artist_az, hold_home, standby_limit,
        standby_off, eq_output, smart_wifi;
    void *pod_label[POD_COUNT];
    void *art_cf_win, *art_cf_list; /* the artist page given its Coverflow, the albums pending */
    void *art_cf_veil;              /* the artist page unseen until they show, and its anim_hint */
    char art_cf_hint[64];
    unsigned art_cf_timer;
    unsigned wake_at; /* Wake: Double press, the screen-off centre press a second one must follow */
    unsigned woke_at; /* the centre press that woke the screen, then its first lit loop pass */
    unsigned return_at;          /* chord_gate: Return's press while held, else 0 */
    int chord_eat;               /* 1: the held Centre press is dropped; -1: it passes */
    unsigned chord_at;           /* the last Centre event chord_gate saw */
    unsigned return_up;          /* chord_gate: a hold's release, else 0 */
    unsigned edge_held, edge_at; /* chord_gate: edge buttons held (bits), the last one's press */
    unsigned edge_seen;          /* chord_gate: an edge button's last press or repeat */
    unsigned centre_at, centre_hold; /* a passing Centre press, and its hold timer */
    int return_eat;                  /* 1: a ghost Return press is dropped */
    unsigned return_eat_at;
    void *np_origin; /* the page Hold Return opened Now Playing over */
    void *dc_top;    /* the page of the last centre press no list click took, and its time */
    unsigned dc_at;
    unsigned charge_at, last_input;
    unsigned touch_at; /* the last pointer event's time, type and place (stale_heal, stale_note) */
    int touch_type, touch_x, touch_y, stale_notes;
    int wdt;      /* the hardware watchdog's fd: 0 untried, -1 none or refused (wdt_poll) */
    int wdt_long; /* counting WDT_LONG_MS for a long pass (ringnav_wdt_long) */
    int ended;    /* a clean end began: no feed and no state record from here (ringnav_clean_end) */
    int toasts;   /* toast()'s dialogs open, nested ones too, for the stall record (stall_write) */
    int suspended;  /* inside Standby's suspend (ringnav_standby), for the state record */
    char state[96]; /* the state record last written (state_poll) */
    /* the boot logo's poll: last check, card seen mounted, its sync thread, its failure to toast */
    unsigned logo_at;
    int logo_mounted;
    volatile int logo_busy;
    const char *volatile logo_err;
    void *pull_page, *pull_surface;
    int pull_x, pull_y, pull_claimed;
    unsigned pull_scope;
    void *sel_w; /* the surface whose selection was last drawn */
    int sel_row, sel_y;
    void *paint_w, *sel_widget; /* paint-local color mapping, never stored on recycled widgets */
    int sel_home;
    unsigned clock_key;   /* minute + 1 and format bits; 0 before the first, ~0 for --:-- */
    unsigned clock_timer; /* clock_due, at the next minute; 0 until the bar first paints */
    int clock_full;       /* g_time24h_flag as clock_show last read it */
    /* The status bar's Bluetooth, Wi-Fi and battery widgets, looked up once (the bar is never
     * destroyed); the codec badge shown (index + 1 in BT_CODECS, 0 none), its fade step (0 showing,
     * CODEC_STEPS the glyph fading in, 2 * CODEC_STEPS done) and timer; the battery slot's last
     * level, charge and low state. Classic's bar also has a title (bar_title_sync), from the
     * top window title_win and, when a label holds it, title_lbl (both found once per window), and
     * a play state of Home's own (home_state), which takes the title's place there (bar_home). */
    void *bar_bt, *bar_wifi, *bar_pct, *bar_slot, *bar_icon, *bar_idle, *bar_state, *bar_eq,
        *bar_title, *home_state, *title_win, *title_lbl,
        *action_view; /* Songs' action bar on title_win, under a set with action_row */
    /* The row action's confirm on show (confirm_over): its second cell's verb, and its window */
    const char *confirm_verb;
    void *confirm_win;
    int codec, codec_step, batt_mode;
    unsigned codec_timer, batt_key, bar_timer;
    int pct_digit, pct_sign; /* BATT_PX advances of '0' and '%', measured on the first paint */
    unsigned letter_timer;   /* the fast-scroll letter shows while this runs */
    /* Now Playing's window and payload-filled widgets, and the sources they last showed. */
    void *np_win, *np_pos, *np_album, *np_slider, *np_remain, *np_elapsed, *np_cover, *np_title;
    int np_bold;             /* the title's font: theme_font's face | px << 2, -1 not set yet */
    void *np_slide, *np_lrc; /* the art/lyrics/info pages and the lyric lines' scroll_view */
    /* Now Playing's full-screen lyrics (np_full), where the last touch on them came
     * down and the timer of its hold on a line (np_lyric_touch) */
    int np_full, np_tap_x, np_tap_y;
    unsigned np_line_timer;
    int np_was[9]; /* the column before full screen (np_full) */
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
    /* Classic (classic_pages): the rating panel, the slider page and its slider; the dark
     * bar and the title's place under it; the volume in the time row's place. */
    void *rate_box, *slider_win, *slider_w, *slider_sw;
    const __WCHAR_TYPE__ *slider_hint; /* its caption */
    void *t9_page, *t9_back;           /* t9_box's page and chevron, while its box lives */
    /* Classic's message screens (msg_open): USB's page; the scan's dialog, stock's label_msg
     * in it, our line, btn_no, the songs before the scan, the bar's part in px, stock's text as
     * last read and the poll following it. msg_counting: 1 msg_count runs, 3 + n it has answered
     * n (-1 failed): one word, so the UI thread can't see the flag before the value. */
    void *usb_win, *msg_win, *msg_src, *msg_line, *msg_btn;
    int msg_total, usb_lun; /* usb_lun: USB Storage's card has read open (usb_poll) */
    volatile int msg_counting;
    short msg_part;
    unsigned msg_timer;
    char msg_seen[128];
    /* Classic's T9 keyboard window, and Search with its tab group (search_open). */
    void *kb_win, *search_win, *search_tabs, *pl_box;
    unsigned pl_timer;       /* pl_click's pending row, its action the timer's ctx */
    int search_px;           /* search_edit's size in its disabled state, 0 until set */
    const char *number_face; /* the slider page's value's, as slider_number last set it */
    int title_color;
    short title_x, title_y, title_w;
    unsigned char title_align, slider_kind, bar_dark;
    unsigned np_hash; /* of the album text, position and queue length shown, 0 to refresh */
    unsigned np_art;  /* the track and player cover img_cover last took (np_art_poll) */
    int np_left;
    /* Minimal's album marquee: np_off px scrolled of the np_over px the album overflows its label.
     */
    int np_off, np_over;
    unsigned np_marquee;
    unsigned np_remain_timer; /* remain_due */
    unsigned np_fit_timer;    /* np_fit_due */
    /* Classic's Spotify or Internet Radio Now Playing (stream_ipod): its slanted art's widget, and
     * the radio's bar, always full */
    void *stream_art, *stream_bar;
    /* The info page's Genre line: its label, np_info's copy (genre_fill), and px scrolled. */
    void *genre_label;
    int genre_off;
    unsigned genre_timer;
    /* The selected row's marquee (marq_moved); Time in Title off (bar_title). */
    void *marq_row, *marq_label;
    unsigned marq_timer;
    int marq_id, marq_passes;
    int time_title, bar_titled, bar_bold, bar_home;
    /* Scrub: a centre press at np_press_at waits DOUBLE_CLICK_MS in np_press for a second one;
     * scrub_to is the target second, scrub_moved set once the wheel changed it. */
    unsigned np_press, np_press_at, scrub_timer, scrub_track, settle_timer;
    int scrub, scrub_to, scrub_moved, settle_played;
    int held_sec; /* a paused seek to scrub_to on scrub_track: the player's second then, +1; 0 none
                   */
    /* The lyrics (lyric_poll), of the song lyric_song, their rows built for lyric_rows_song:
     * lyric_raw while their page holds the wheel's raw travel; lyric_held once the wheel or a drag
     * moved them; lyric_spun once the wheel moved them since it was let go (2: only nudged them,
     * still following); lyric_touch while a
     * finger is on Now Playing, 1 if it came down on the lyrics, lyric_drag_y the offset then; the
     * glide's target lyric_to (1/16 px), its last step lyric_at, and lyric_glide while it moves;
     * lyric_follow_at the last follow. */
    int lyric_raw, lyric_held, lyric_spun, lyric_touch, lyric_drag_y, lyric_glide, lyric_to;
    int lyric_push, lyric_home; /* travel short of LYRIC_PUSH_DETENTS, and where it nudged from */
    unsigned lyric_push_at;
    unsigned lyric_at, lyric_follow_at, lyric_song, lyric_rows_song;
    /* The Display settings, read from config.ini on first use, and the display page's value labels.
     */
    int settings_read, accent, minimal_accent, home, battery, home_rockbox, theme, shortcut,
        rounded, light, light_day, /* light_day: Auto's answer for now (LIGHT=2) */
        keep_lit,                  /* Smart Awake (#57, keep_lit_poll) */
        np_centred,                /* Centred Now Playing (ringnav_ui_load) */
        keytone_hp,                /* Click With Headphones (ringnav_buzzer) */
        scrub_single,              /* Single Press Scrub (np_key) */
        theme_booted,              /* theme_boot ran (ringnav_ui_load) */
        raw_ink,                   /* a final colour's draw: ui_ink leaves it alone */
        geom;                      /* the geometry set theme_boot wrote; 0, SET0, until then */
    int own[THEME_KEYS]; /* the themes' own keys' values (theme_keys[], tools/theme.py), read after
                            the thirteen */
    unsigned keep_lit_at;
    void *sel_accent; /* the row on a coloured selection, for Light Mode's ink */
    int style_depth;  /* nested style hooks (a mutable style's fallback) map nothing */
    void *setting_label[12 + THEME_KEYS]; /* SETTING_OWN + THEME_KEYS */
    unsigned tone_key;  /* the wheel key whose press ringnav_keydown silenced, 0 when none */
    unsigned tick_drop; /* tick_gate: the wheel keys whose press it dropped, bits */
    int greeted;        /* the first reachable list got its boot repaint */
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

typedef struct {
    int x, y, w, h;
} rect_t;
typedef struct {
    void *w, *at[MAX_ENTRIES];
    int id[MAX_ENTRIES], n, kind, rows, row, height, ctx;
    unsigned scope;
} menu_t;

extern scratch_t st;
extern menu_t g_menu; /* the one row list load() fills */

/* Outside NAVIGATION: stock's trampolines (tools/build.py) and the other payload files'. */
extern const char *track_name(char *buf, unsigned size, void *t);
extern void *staged(int (*query)(void *), void *arg, int *count);
extern int spot_media(unsigned key), spot_key(void *top, unsigned key),
    spot_open(void *ctx, void *event), spot_hold(void *top);
extern int radio_media(unsigned key), radio_open(void *ctx, void *event);
extern int games_key(void *top, unsigned key), games_open(void *ctx, void *event);
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
    stock_standby_trampoline(void), stock_usbmode_trampoline(void *, void *, void *, void *),
    stock_hscroll_start_trampoline(void *), stock_wifi_page_trampoline(void *, void *),
    stock_stream_row_trampoline(void *, void *), stock_netservice_row_trampoline(void *, void *),
    stock_online_match_trampoline(void *);
extern int scrobble_ready(void), scrobble_start(void), scrobble_poll(int *sent),
    scrobble_flag(const char *key), scrobble_value(const char *key);
extern const char *now_tag(void *r, int field);
extern void *queue_now(unsigned *pos, unsigned *n);

/* navigation.c */
void pod_settings(void);
#define COUNT "_ringnav_count"
#define SEL "_ringnav_index"
enum { SLIDER_LIGHT, SLIDER_MAX, SLIDER_BALANCE, SLIDER_BOOT }; /* st.slider_kind */
#define ROUND_RIGHT 2
#define ROUND_LEFT 1
#define STYLE_KEY 48
#define CONFIRM_CELL_PX 23
#define MCL(a) (*(volatile int *)(a))
int clamp_step(int offset, int maximum, int delta);
void fill_box(void *canvas, rect_t *r, unsigned color, unsigned radius);
unsigned put_num(unsigned *s, unsigned v);
void paint_panel(void *w, void *canvas, const rect_t *r, const char *shadow);
#define PANEL_PAD 8
#define PANEL_DROP 2
unsigned stops_row(const stops_t *s, int j, int h) __attribute__((pure));
int clock_due(const void *info);
int bar_repaint(const void *info);
void clock_text(void *label, int negative, int t);
void label_font(void *w, int bold);
void *list_row(void *view, const char *icon, int (*click)(void *, void *), void *ctx);
void *list_row_core(void *view, const char *icon, int (*click)(void *, void *), void *ctx, int w,
                    int into);
int media_find(int kind, char *path);
void prop(void *w, const char *name, int value);
char *svc_find(char *hide, int i);
void svc_hidden(char *s);
int media_click(void *ctx, void *event);
int stream_config(void *ctx, void *event);
void bar_place(void *w, int x, int y, int ww, int h);
void bar_rect(void *canvas, unsigned color, int x, int y, int w, int h);
void dark_bar_rows(void *canvas, int y, int w, int h);
void inset_top(void *canvas, const rect_t *r, int R, unsigned a);
void lit_rows(void *canvas, const rect_t *c, int ends);
const char *number_face(void *label, int *px);
int painting_in(void *row);
void paint_round_rows(void *canvas, const rect_t *r, const stops_t *s, int R);
int round_inset(int R, int i, int h);
void round_ring(void *canvas, const rect_t *r, int R, unsigned c);
void round_row(void *canvas, const rect_t *r, int R, int i, unsigned c);
void row_below(void *canvas, const rect_t *r, int R, unsigned c);
int slider_part(void *s, int w);
void slider_switch(void *w, void *canvas, void *sw);
const char *style_key(char *key, const char *state, const char *name);
unsigned wide_len(const unsigned *s);
void *window_of(void *w);
extern const stops_t confirm_dark;
int search_key(unsigned key);
int geom(int group);
int load_rows(menu_t *m, void *w);
void *qm_record(void);
void *rec_find(void *songs, const unsigned *slot, unsigned mask, void *r);
unsigned *rec_index(void *songs, unsigned *mask);
void reveal(menu_t *m, int id, int immediate);
void row_meta(void *w);
void *staged_music(int (*query)(void *), void *arg, int *count);
int usable(void);
int all_songs(void *music);
long clock_back(long long wall);
void text_inks(void *w);
unsigned listen_key(void *r) __attribute__((pure));
int page_closed(void *ctx, void *event);
void plays_load(void);
int songs_unsorted(void *music);
void drop_input(void);
int pointer_down(void);
unsigned qm_album_id(void *r);
char *scrobble_tag(char *o, char *end, const char *s);

/* np_bar.c */
int np_beside(void);
int remain_due(const void *info);
int np_fit_due(const void *info);
int np_sync(void *top);
void bar_title(void *bar, void *top);
void slider_fill(void *slider, unsigned color);
void np_theme(void);
void paint_scrubber(void *w, void *canvas);
/* One row of final colour, its alpha the row's. Inline: the round boxes' edges call it a pixel at a
 * time, and an exported call passes w on the stack. */
static inline void scrub_row(void *canvas, unsigned c, int x, int y, int w) {
    stock_canvas_set_fill_color(canvas, (RGBA(c & 0xffffffu) & 0xffffffu) | (c & 0xff000000u));
    canvas_fill_rect(canvas, x, y, w, 1);
}
void np_fill(int scrub);

/* theme_engine.c */
#define ACCENT_N SETTING_N_ACCENT /* the ACCENTS rows: theme_engine.c's accents[] has as many */
enum {
    SETTING_ACCENT,
    SETTING_HOME,
    SETTING_BATTERY,
    SETTING_THEME,
    SETTING_SHORTCUT,
    SETTING_ROUNDED,
    SETTING_LIGHT,
    SETTING_KEEPLIT,
    SETTING_NPCENTRED,
    SETTING_KEYTONEHP,
    SETTING_SCRUBSINGLE,
    SETTING_TIMETITLE,
    SETTING_OWN
};
#define INK_LABEL 1
#define INK_EDIT 15
#define INK_TAB 10
#define INK_BUTTON 5
enum { PODCASTS = 1, AUDIOBOOKS, PHOTOS, BOOKS, VIDEOS };
enum { TONE_LIGHT = 2, TONE_RED = 3 }; /* columns of ACCENTS */
int geom_val(int v) __attribute__((pure));
extern const char *const MEDIA[];
unsigned flip_if(int on, unsigned c) __attribute__((pure));
int accent(void);
unsigned accent_map(unsigned c, int preset, int tone);
const int *clock_now(int *buf);
unsigned flip(unsigned c) __attribute__((pure));
unsigned flip_v(unsigned v, unsigned paper, unsigned ink);
int ipod_home(void);
const char *ipod_home_shortcut(void);
void light_apply(const int *tm);
int light_day(const int *tm);
int neutral(unsigned c);
int np_layout(void);
const char *option_key(const theme_t *T, int setting);
const option_t *option_of(const theme_t *T, int setting);
int *option_slot(const theme_t *T, int setting);
unsigned red_map(unsigned c, unsigned tone, unsigned glyph, int light);
int setting(int i);
int shortcut(void);
unsigned tone_of(const unsigned *a, int tone);
extern const char *const accent_names[];
extern const char *const np_pages[];
extern const int setting_counts[];
extern int *const setting_values[];
const theme_t *theme_of(int t);
static inline const unsigned *tones_of(int v) { return theme()->schemes[v].tones; }
static inline const unsigned *tones(void) { return tones_of(accent()); }

/* scrobble_ui.c */
void scrobble_listened(void);
void scrobble_auto(void);
int upload_scrobbles(void *ctx, void *event);

/* volume.c */
void vol_paint(void *top, void *canvas);

/* smart_wifi.c */
void smart_wake(void); /* Smart Wi-Fi's radio back now, if it had it off */
int smart_config(const char *section, const char *key);
void smart_poll(void);

/* system.c */
int card_mounted(void);
void charge_poll(void);
void logo_poll(void);
void standby_poll(void);
void state_poll(void);
void wdt_poll(void);

/* library_lists.c */
int era_menu(void *ctx, void *event);
int long_songs(void *ctx, void *event);
void ls_closed(void);
int most_played(void *ctx, void *event);
int mp_find(unsigned key);
int shuffle_menu(void *ctx, void *event);
int shuffle_play(void *all);
int tidal_downloads(void *ctx, void *event);

/* ratings.c */
const char *hearts_text(char *s, int n);
int rate_keep(int album, unsigned key, int n);
int rate_kept(int album, unsigned key);
int rate_key(void *top, unsigned key);
int rate_open(void);
int rate_saved(int k);
int rate_show(void *win, int pick, unsigned album);
void rate_bind(void *win); /* ratings: Now Playing's heart */
int rated_albums(void *ctx, void *event);
int ratings_menu(void *ctx, void *event);
void ratings_copy_due(void);
void ratings_copy_poll(void);
int rt_play(void *ctx, void *event);

/* classic_pages.c */
int kb_key(void *w);
void kb_open(void *win);
void kb_paint_key(void *w, void *canvas);
void msg_open(void *win);
void msg_paint(void *w, void *canvas);
void paint_field(void *w, void *canvas, int x, int y, int fw);
void paint_ramp(void *canvas, int y0, int y1, int top);
void pl_box_open(void *win);
void pl_box_paint(void *w, void *canvas);
void search_open(void *win);
void search_paint(void *w, void *canvas);
void search_tabs_paint(void *group, void *canvas);
void search_x(void *w, void *canvas);
void slider_paint(void *w, void *canvas);
int search_del(void *w);
void search_over(void *w, void *canvas);
void slider_scrub(void *w, void *canvas);

/* playlists.c */
/* Classic's Playlists (pl_dress): a page's add mode (ringnav_playlist), the button of a row that
 * isn't a playlist, its list item's action + 1, a playlist row's item, whether the rows pl_rows
 * last showed were select mode's, and the empty list's label. */
#define PL_MODE "_pl_mode"
#define PL_ACTION "_pl_action"
#define PL_ACT "_pl_act"
#define PL_ROW "_pl_row"
#define PL_MARKING "_pl_marks"
#define PL_NONE "_pl_none"
void qm_playlist_bind(void *w, int depth, int (*pick)(void *, void *));
void qm_select(void);
int qm_playlist_gone(void *ctx, void *event);
int qm_playlist_pick(void *ctx, void *event);
int ple_key(unsigned key, int phase);
int pl_first(menu_t *m, int cur);
void pl_fit(void *button);

#endif
