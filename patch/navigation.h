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
    int cf_letter_held; /* the thumb's letters: shown until it lifts */
    widget_t *cf_letter_surface;
    unsigned last_home;
    int home_dir;
    widget_t *home_surface;   /* non-null also marks a step accepted at time zero */
    widget_t *center_top;     /* top window of that press */
    widget_t *center_surface; /* navigation surface of that press */
    unsigned last_wheel;      /* time of the previous wheel detent */
    int wheel_dir;            /* direction of that detent */
    unsigned wheel_run;       /* continuous same-direction spin ms + 1; capped at full speed */
    int touch_mode;           /* session-wide drawing preference, independent of selection */
    widget_t *wheel_top, *wheel_surface;
    unsigned wheel_scope;
    int wheel_ctx;
    /* iPod: a Return by button ends touch mode once stock has handled it */
    unsigned untouch_timer;
    /* iPod: the row pressed_pill last drew grey, and the repaint once it is released */
    widget_t *press_row;
    unsigned press_timer;
    /* wheel ticks held while a page slides or loads (hold_wheel), replayed into it */
    int held;
    unsigned held_at, held_timer;
    void *held_ctx;
    unsigned held_event[12];
    position_t pos[POS_MEM];  /* most recently selected first; keyed by context and scope */
    widget_t *reveal_surface; /* surface of the interrupted recall glide, 0 when none */
    int reveal_id;            /* logical row that glide was bringing into view */
    /* Bump repaint and wrap arm; the widget token rejects recycled surfaces. */
    widget_t *fx_surface;
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
     * opens while the library is at library_lists.c's mp_gen; the hold menu reads them too
     * (page_tracks). */
    widget_t *mp_page;
    void *mp_list;
    widget_t *ch_page;
    chapters *ch;
    unsigned ch_timer, ch_track, ch_key;
    int ch_cue, ch_end;            /* the Chapters track's CUE start and end, file seconds */
    widget_t *sc_page, *sc_row[3]; /* Streaming's Configuration page, its cache rows and Erase's */
    int sc_armed;                  /* Erase Spotify Data pressed once: the next press erases */
/* Streaming's services (svc): five fixed, and at most APPS_MAX card apps. */
#define SVC_FIXED 5
#define SVC_MAX (SVC_FIXED + APPS_MAX)
#define SVC_HIDE 260 /* STREAMHIDE, "key,key" */
#define SVC_PROP "ringnav_svc"
    widget_t *sc_check[SVC_MAX]; /* the Configuration page's switch labels, by service */
    int svc_n;                   /* the card apps Streaming listed at its last build */
    /* Ratings (ratings.c keeps the rest): Now Playing's window and the window the picker is up on
     * (0 when down); the page a Ratings list or Long Songs opened, with its tracks (page_tracks).
     */
    widget_t *rate_win, *rate_on;
    int empty_kind; /* the empty Library list empty_toast names and closes */
    widget_t *rt_page;
    void *rt_list;
    int dark; /* 1: the backlight was off at the last UI loop pass; 2: a centre press woke it */
    /* Power's Charge limit (read once from config.ini's Q2POD), the value labels of that row
     * and Artists, and the poll: charging is held off at the limit;
     * last_input times the screen-on idle. year_old: Year order, oldest first. resume:
     * Bluetooth's Resume on connect on. hold_home: Hold Return goes Home. standby_limit: Standby
     * limit's choice.
     * smart_wifi: Power's Smart Wi-Fi on (smart_poll). big_library: Display's Fast Navigation
     * on. playlist_cf: Playlists open as Cover Flow. */
    int pod_read, charge_limit, wake_double, wake_pass, wake_eat,
        wake_down, /* 1: the held Centre press passes; 2: it is dropped */
        charge_held, artist_cf, year_old, resume, lock_clock, artist_az, hold_home, standby_limit,
        eq_output, smart_wifi, big_library, playlist_cf, scan_durations, cf_position, hide_stream,
        hide_folder, remind_daily;
    widget_t *pod_label[POD_COUNT];
    widget_t *art_cf_win;
    void *art_cf_list;     /* the artist page given its Coverflow, the albums pending */
    widget_t *art_cf_veil; /* the artist page unseen until they show, and its anim_hint */
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
    int centre_rim;                  /* 1: an edge press came within CHORD_MS of it: no click */
    int return_eat;                  /* 1: a ghost Return press is dropped */
    unsigned return_eat_at;
    widget_t *np_origin; /* the page Hold Return opened Now Playing over */
    widget_t *dc_top;    /* the page of the last centre press no list click took, and its time */
    unsigned dc_at;
    unsigned charge_at, last_input;
    unsigned touch_at; /* the last pointer event's time, type and place (stale_heal, stale_note) */
    int touch_type, touch_x, touch_y, stale_notes;
    int toasts; /* toast()'s dialogs open, nested ones too, for the stall record (stall_write) */
    widget_t *pull_page, *pull_surface;
    int pull_x, pull_y, pull_claimed;
    unsigned pull_scope;
    widget_t *sel_w; /* the surface whose selection was last drawn */
    int sel_row, sel_y;
    widget_t *paint_w,
        *sel_widget; /* paint-local color mapping, never stored on recycled widgets */
    int sel_home;
    unsigned clock_key;   /* minute + 1 and format bits; 0 before the first, ~0 for --:-- */
    unsigned clock_timer; /* clock_due, at the next minute; 0 until the bar first paints */
    int clock_full;       /* g_time24h_flag as clock_show last read it */
    /* The status bar's Bluetooth, Wi-Fi and battery widgets, looked up once (the bar is never
     * destroyed); the codec badge shown (index + 1 in BT_CODECS, 0 none), its fade step (0 showing,
     * CODEC_STEPS the glyph fading in, 2 * CODEC_STEPS done) and timer; the battery slot's last
     * level, charge and low state. Classic's bar also has a title (bar_title_sync), from the
     * top window title_win and, when a label holds it, title_lbl (both found once per window). */
    widget_t *bar_bt, *bar_wifi, *bar_pct, *bar_slot, *bar_icon, *bar_idle, *bar_state, *bar_eq,
        *bar_title, *title_win, *title_lbl,
        *action_view; /* Songs' action bar on title_win, under a set with action_row */
    /* The row action's confirm on show (confirm_over): its second cell's verb, and its window */
    const char *confirm_verb;
    widget_t *confirm_win;
    int codec, codec_step, batt_mode;
    unsigned codec_timer, batt_key, bar_timer;
    int pct_digit, pct_sign; /* BATT_PX advances of '0' and '%', measured on the first paint */
    unsigned letter_timer;   /* the fast-scroll letter shows while this runs */
    /* Now Playing's window and payload-filled widgets, and the sources they last showed. */
    widget_t *np_win, *np_pos, *np_album, *np_slider, *np_remain, *np_elapsed, *np_cover, *np_title;
    int np_bold;                 /* the title's font: theme_font's face | px << 2, -1 not set yet */
    widget_t *np_slide, *np_lrc; /* the art/lyrics/info pages and the lyric lines' scroll_view */
    int np_full; /* Now Playing's full-screen lyrics (np_lyrics.c np_full): 2 the iPod page's */
    /* The full-screen art's song name, its shade and its window, the fade's step and timer
     * (ringnav_bigcover) */
    widget_t *bc[3];
    int bc_step;
    unsigned bc_timer, bc_track, bc_repaint;
    unsigned bc_key, bc_box, bc_shape,
        bc_url; /* the track, cover and image bc_fit sized for, the box
                 * it left, the image's w << 16 | h and its track */
    /* Classic (classic_pages): the rating panel, the slider page and its slider; the volume in the
     * time row's place. */
    widget_t *rate_box, *slider_win, *slider_w, *slider_sw;
    const __WCHAR_TYPE__ *slider_hint; /* its caption */
    widget_t *t9_page, *t9_back;       /* t9_box's page and chevron, while its box lives */
    /* Classic's message screens (msg_open; classic_pages.c keeps the rest): USB's page and the
     * scan's dialog. usb_lun: USB Storage's card has read open (usb_poll). */
    widget_t *usb_win, *msg_win;
    int usb_lun;
    /* Classic's T9 keyboard window, and Search with its tab group (search_open). */
    widget_t *kb_win, *search_win, *search_tabs, *pl_box;
    const char *number_face; /* the slider page's value's, as slider_number last set it */
    unsigned char slider_kind;
    unsigned np_hash; /* of the album text, position and queue length shown, 0 to refresh */
    unsigned np_art;  /* the track and player cover img_cover last took (np_art_poll) */
    unsigned np_seen; /* the track and tags the page's update last drew (np_update_poll) */
    int np_left;
    /* Minimal's album marquee: np_off px scrolled of the np_over px the album overflows its label.
     */
    int np_off, np_over;
    unsigned np_marquee;
    unsigned np_remain_timer; /* remain_due */
    unsigned np_fit_timer;    /* np_fit_due */
    /* Classic's Spotify or Internet Radio Now Playing (stream_ipod): its slanted art's widget, and
     * the radio's bar, always full */
    widget_t *stream_art, *stream_bar;
    /* The info page's Genre line: its label, np_info's copy (genre_fill), and px scrolled. */
    widget_t *genre_label;
    int genre_off;
    unsigned genre_timer;
    /* The selected row's marquee (marq_moved); Time in Title off (bar_title). */
    widget_t *marq_row, *marq_label;
    unsigned marq_timer;
    int marq_id, marq_passes;
    int time_title, bar_titled;
    /* Scrub: a centre press at np_press_at waits DOUBLE_CLICK_MS in np_press for a second one;
     * scrub_to is the target second, scrub_moved set once the wheel changed it. */
    unsigned np_press, np_press_at, scrub_timer, scrub_track, settle_timer;
    int scrub, scrub_to, scrub_moved, settle_played;
    int held_sec; /* a paused seek to scrub_to on scrub_track: the player's second then, +1; 0 none
                   */
    /* The lyrics (np_lyrics.c keeps the rest), of the song lyric_song, their rows built for
     * lyric_rows_song: lyric_held once the wheel or a drag moved them; lyric_glide while the glide
     * moves them. */
    int lyric_held, lyric_glide;
    unsigned lyric_song, lyric_rows_song;
    /* The Display settings, read from config.ini on first use, and the display page's value labels.
     */
    int settings_read, accent, minimal_accent, home, battery, home_rockbox, home_groovebox, theme,
        shortcut, rounded, light, light_day, /* light_day: Auto's answer for now (LIGHT=2) */
        keep_lit,                            /* Smart Awake (#57, keep_lit_poll) */
        np_centred,                          /* Centred Now Playing (ringnav_ui_load) */
        keytone_hp,                          /* Click With Headphones (ringnav_buzzer) */
        scrub_single,                        /* Single Press Scrub (np_key) */
        theme_booted,                        /* theme_boot ran (ringnav_ui_load) */
        raw_ink,                             /* a final colour's draw: ui_ink leaves it alone */
        geom;            /* the geometry set theme_boot wrote; 0, SET0, until then */
    int own[THEME_KEYS]; /* the themes' own keys' values (theme_keys[], tools/theme.py), read after
                            the thirteen */
    int card_page;       /* a card theme's page (card_theme_load): 0 none, 1 dark, 2 light */
    int card_rules;      /* the pages a card theme's rules reach (card_rules_used): 1 << CARD_* */
    unsigned keep_lit_at;
    widget_t *sel_accent; /* the row on a coloured selection, for Light Mode's ink */
    int style_depth;      /* nested style hooks (a mutable style's fallback) map nothing */
    widget_t *setting_label[12 + THEME_KEYS]; /* SETTING_OWN + THEME_KEYS */
    unsigned tone_key;  /* the wheel key whose press ringnav_keydown silenced, 0 when none */
    unsigned tick_drop; /* tick_gate: the wheel keys whose press it dropped, bits */
    unsigned tick_at;   /* tick_gate: the last tick it passed (ringnav_sleep's dark turn) */
    int greeted;        /* the first reachable list got its boot repaint */
    /* Queue menu (queue_menu.c keeps the rest): the hold's AWTK press time marks its release; the
     * target row's place in its list, checked by count; qm_forced is a shuffle Play next; the
     * dialog, and the playlist page Add to Playlist opened. */
    unsigned long long qm_press;
    unsigned qs_hold; /* Return's hold closed Quick Settings then, 0 once acted on (quick_poll) */
    unsigned qm_idx, qm_rows, qm_forced, qm_forced_hash;
    widget_t *qm_dialog, *qm_playlist;
    /* Podcasts/Audiobooks: folder_page opens at media_root once media_open is set (else at the
     * card, /mnt/mmc, with a card and no drive; otherwise the storage selector stays); while the
     * page is there, Back at that root leaves it. */
    int media_open;
    char media_root[32];
    /* Go to Folder: once set, folder_page opens at go_folder at g_folder_layer go_layer. */
    int go_layer;
    char go_folder[1024]; /* g_folder_path's size */
    widget_t *root_label; /* Playback settings' Folder Root row */
    void *album;     /* what an Albums row or Go to album opened (album_open), a one-record deque */
    char **art_keys; /* an artist's albums tab's art keys by row, 0 for a name's own
                        (ringnav_artist_list) */
    unsigned *art_ids; /* and its rows' album_id, 0 for an album not found */
    unsigned art_n;
} scratch_t;

typedef struct {
    widget_t *w, *at[MAX_ENTRIES];
    int id[MAX_ENTRIES], n, kind, rows, row, height, ctx;
    int cols; /* a table row's entries: ALBUM_GRID in the album grid, else 1 */
    unsigned scope;
} menu_t;

extern scratch_t st;
extern menu_t g_menu; /* the one row list load() fills */

/* Outside NAVIGATION: stock's trampolines (tools/build.py) and the other payload files'. */
extern const char *track_name(char *buf, unsigned size, void *t);
extern void *staged(int (*query)(void *), void *arg, int *count);
extern int spot_media(unsigned key), spot_key(widget_t *top, unsigned key),
    spot_open(void *ctx, event_t *event), spot_hold(widget_t *top);
extern int radio_media(unsigned key), radio_open(void *ctx, event_t *event);
extern int games_key(widget_t *top, unsigned key), games_open(void *ctx, event_t *event);
extern int stock_keyup_trampoline(void *, event_t *), stock_touch_trampoline(void *, event_t *),
    stock_output_trampoline(int), stock_write_config_trampoline(int, const char *, const char *),
    stock_paint_trampoline(widget_t *, canvas_t *),
    stock_dispatch_trampoline(widget_t *, event_t *), stock_keylong_trampoline(void *, event_t *),
    stock_paint_bg_trampoline(widget_t *, canvas_t *), stock_showface_trampoline(widget_t *),
    stock_playing_trampoline(widget_t *, void *), stock_display_trampoline(widget_t *, void *),
    stock_bigcover_trampoline(widget_t *, void *), stock_localmusic_trampoline(widget_t *, void *),
    stock_keydown_trampoline(void *, event_t *), stock_btvol_trampoline(int, int),
    stock_sleep_trampoline(void *), stock_color_trampoline(void *, void *, const char *, unsigned),
    stock_image_trampoline(void *, const char *, void *),
    stock_about_trampoline(widget_t *, void *), stock_folder_trampoline(widget_t *, void *),
    stock_folder_back_trampoline(void *, void *), stock_folder_top_trampoline(void),
    stock_folder_click_trampoline(void *, event_t *), stock_input_trampoline(widget_t *, event_t *),
    stock_buzzer_trampoline(int), stock_localclass_trampoline(int),
    stock_detail_trampoline(int, void *), stock_artist_trampoline(int),
    stock_power_trampoline(widget_t *, void *), stock_systemset_trampoline(widget_t *, void *),
    stock_netservice_trampoline(void *, void *), stock_audioset_trampoline(widget_t *, void *),
    stock_btrows_trampoline(widget_t *, void *), stock_playlist_trampoline(widget_t *),
    stock_stream_trampoline(widget_t *, void *), stock_start_player_trampoline(void),
    stock_standby_trampoline(void), stock_cardmount_trampoline(void),
    stock_config_write_trampoline(const char *, const char *, const char *, const char *),
    stock_usbmode_trampoline(void *, void *, void *, void *),
    stock_hscroll_start_trampoline(widget_t *), stock_wifi_page_trampoline(widget_t *, void *),
    stock_stream_row_trampoline(widget_t *, event_t *),
    stock_netservice_row_trampoline(widget_t *, event_t *), stock_online_match_trampoline(void *);
extern int scrobble_ready(void), scrobble_start(void), scrobble_poll(int *sent),
    scrobble_flag(const char *key), scrobble_value(const char *key);
extern const char *now_tag(void *r, int field);
extern void *queue_now(unsigned *pos, unsigned *n);
extern void *coverflow_tracks(widget_t *page);
extern void *coverflow_album(widget_t *page);
extern int coverflow_goto_album(void *r), coverflow_shows_album(void *r);
extern void up_next(int (*keyup)(void *, event_t *));

/* navigation.c */
void pod_settings(void);
extern const char *const ink_state[];
extern const unsigned standby_hours[4];
#define COUNT "_ringnav_count"
#define SEL "_ringnav_index"
enum { SLIDER_LIGHT, SLIDER_MAX, SLIDER_BALANCE, SLIDER_BOOT }; /* st.slider_kind */
#define ROUND_RIGHT 2
#define ROUND_LEFT 1
#define STYLE_KEY 48
#define CONFIRM_CELL_PX 23
#define CONFIRM_FLAT_PX 21 /* the flat look's: a cell's words and its height */
#define CONFIRM_FLAT_H 36
/* a confirm's form: none, the page, confirm_over's tray */
enum { FORM_NONE, FORM_PAGE, FORM_TRAY };
/* the toast's box, and the flat look's tray: the same in both halves */
#define TOAST_RGBA 0xee2b2b2bu
#define MCL(a) (*(volatile int *)(a))
int clamp_step(int offset, int maximum, int delta);
unsigned put_num(unsigned *s, unsigned v);
void paint_panel(widget_t *w, canvas_t *canvas, const rect_t *r, const char *shadow, int flat);
#define PANEL_PAD 8
#define PANEL_DROP 2
unsigned stops_row(const stops_t *s, int j, int h) __attribute__((pure));
int bar_repaint(const void *info);
void clock_text(widget_t *label, int negative, int t);
void label_font(widget_t *w, int bold);
widget_t *list_row(widget_t *view, const char *icon, int (*click)(void *, event_t *), void *ctx);
int language_offered(void); /* language.c */
widget_t *list_row_core(widget_t *view, const char *icon, int (*click)(void *, event_t *),
                        void *ctx, int w, int into);
int media_find(int kind, char *path);
void prop(widget_t *w, const char *name, int value);
char *svc_find(char *hide, int i);
void svc_hidden(char *s);
int media_click(void *ctx, event_t *event);
int stream_config(void *ctx, event_t *event);
void bar_place(widget_t *w, int y, int h);
void bar_rect(canvas_t *canvas, unsigned color, int x, int y, int w, int h);
void dark_bar_rows(canvas_t *canvas, int y, int h);
void inset_top(canvas_t *canvas, const rect_t *r, int R, unsigned a);
void lit_rows(canvas_t *canvas, const rect_t *c, int ends);
const char *number_face(widget_t *label, int *px);
int painting_in(widget_t *row);
void paint_round_rows(canvas_t *canvas, const rect_t *r, const stops_t *s, int R);
void round_fill(canvas_t *canvas, const rect_t *r, unsigned rgb, int R);
int round_inset(int R, int i, int h);
void round_ring(canvas_t *canvas, const rect_t *r, int R, unsigned c);
void round_row(canvas_t *canvas, const rect_t *r, int R, int i, unsigned c);
void row_below(canvas_t *canvas, const rect_t *r, int R, unsigned c);
int slider_part(widget_t *s, int w);
void slider_switch(widget_t *w, canvas_t *canvas, widget_t *sw);
const char *style_key(char *key, const char *state, const char *name);
unsigned wide_len(const unsigned *s);
widget_t *window_of(widget_t *w);
void cf_veil(widget_t *win, int on);          /* win unseen (1) or seen again under Coverflow */
int music_records(void *list, int discovery); /* list's songs less spoken (0) or hidden (1) ones */
extern const stops_t confirm_dark, confirm_tray, confirm_well;
int search_key(unsigned key);
int geom(int group);
int load_rows(menu_t *m, widget_t *w);
void *rec_find(void *songs, const unsigned *slot, unsigned mask, void *r);
unsigned *rec_index(void *songs, unsigned *mask);
void reveal(menu_t *m, int id, int immediate);
void row_meta(widget_t *w);
void *staged_music(int (*query)(void *), void *arg, int *count);
int usable(void);
int all_songs(void *music);
void text_inks(widget_t *w);
unsigned listen_key(void *r) __attribute__((pure));
void plays_load(void);
int songs_unsorted(void *music);
void drop_input(void);
int pointer_down(void);
char *scrobble_tag(char *o, char *end, const char *s);
unsigned np_track(void);
void scrub_end(void);
void view_still(widget_t *w);
extern const char *const lock_names[4];
int lock_choice(void);
void lock_set(int choice);
int named_in(const char *names, const char *name);
int bright_tone(unsigned tone);
int ringnav_dispatch(widget_t *target, event_t *event);
int compact_now_playing(void);
int fields(widget_t *top);
widget_t *field_widget(widget_t *top, int f);
void field_set(widget_t *top, int f);
/* A swipe between the date and time pages moves the field to that page's first. */
static inline int field_on_page(int f, int time) { return time != (f >= 3) ? (time ? 3 : 0) : f; }
int slider_gone(void *win, event_t *event);
const char *bar_title_of(widget_t *win, widget_t **label);
/* Audited top windows (contexts.inc): each its content-identity class and its flags. */
enum { CTX_DYNAMIC, CTX_FIXED, CTX_FOLDER, CTX_LOCAL };
typedef struct {
    const char *name;
    unsigned char kind, flags;
} context_t;
extern const context_t contexts[];
int context_id(const char *name);
int allowed_top(widget_t *top);
widget_t *surface_under(widget_t *top, widget_t *target, widget_t **other, int wheel);
unsigned local_hash(unsigned h);
int load(menu_t *m, widget_t *w, int recall);
int moving(menu_t *m);
int reconcile(menu_t *m, int settle);
widget_t *more_image(widget_t *w);
#define BAR_ROWS geom_val(V_BAR)
/* Classic's dialogs: the toast, small dialogs' cleared dim, a game's panel, the hold menu; and the
 * prompts, where flat_dialogs doesn't take them */
static inline int classic_dialogs(void) {
    return theme()->painter[PAINT_CONFIRM] & CONFIRM_CLASSIC_LIGHT;
}
/* The flat look's prompts: the confirm control's painters alone */
static inline int flat_dialogs(void) { return !!(theme()->painter[PAINT_CONFIRM] & CONFIRM_FLAT); }
char *play_key(void);
unsigned rec_hash(void *r);
unsigned queue_hash(void *q);
extern const unsigned char qm_by[3];
int class_query(unsigned cls, void *r);
widget_t *np_more(void);
void album_open(void *r);
int chapter_file(void *r);
void chapters_open(void *r);
void *book_place(void *list, void *r, int *at, int *off);
int song_folder(void *r, char *path);

/* np_lyrics.c */
void lyric_init(void);
void lyric_poll(void);
void np_full(int on);
int np_lyrics(unsigned key);
int np_lyric_touch(void *ctx, event_t *event);

/* quick_settings.c */
int quick_dispatch(widget_t *target, event_t *event);
void quick_fill(widget_t *w);
void quick_page(widget_t *win, canvas_t *canvas);
void quick_scrub(widget_t *w, canvas_t *canvas);
extern const rect_t qs_bar_card;
void quick_vol_flat(widget_t *w, canvas_t *canvas, int dy, int level, int max);
void quick_poll(void);
int quick_volume(void);
const char *quick_wheel_toggle(widget_t *top);

/* flat.c */
extern const unsigned flat_rest[2],
    flat_ink[2]; /* final, [0] dark, [1] light; flat_quiet: theme.h */
unsigned flat_on(void);
const char *flat_face(void);
enum { BAR_PLAIN, BAR_SUNS, BAR_SPEAKERS }; /* flat_bar's marks */
void flat_bar(widget_t *w, canvas_t *canvas, const rect_t *r, int part, unsigned rest, int marks);
void flat_box(canvas_t *canvas, rect_t *r, unsigned rgba, unsigned radius);
void page_label(widget_t *w, int x, int y, int ww, int h, int px, const char *face, unsigned rgb);
void confirm_track_flat(widget_t *w, canvas_t *canvas, int form);
void confirm_lit(canvas_t *canvas, const rect_t *c);
void confirm_labels_flat(widget_t *w, canvas_t *canvas, int form);

/* np_marks.c */
void np_mark_add(widget_t *const *image, unsigned n);
void np_marks_page(widget_t *win);
void np_marks(widget_t *w, canvas_t *canvas);

/* queue_menu.c */
int qm_hold(void);
void *qm_record(void);
void *qm_collect(void *r);
unsigned qm_album_id(void *r);

/* clock.c */
long clock_back(long long wall);
int clock_due(const void *info);
void clock_show(void);
int time_click(widget_t *target, event_t *event);
int time_form(widget_t *top);
void time_open(widget_t *win);
void time_paint(widget_t *w, canvas_t *canvas);
void time_wheel(widget_t *top, int f, int dir);

/* coverflow_position.c */
void coverflow_position_reset(void);
void coverflow_tail(void); /* coverflow.c: the covers follow the queue's album */

/* np_bar.c */
int np_beside(void);
int remain_due(const void *info);
int np_fit_due(const void *info);
int np_sync(widget_t *top);
void bar_title(widget_t *bar, widget_t *top);
int home_menu(widget_t *top);           /* Home's page in front (navigation.c) */
const char *coverflow_home_title(void); /* Home's title (coverflow.c) */
void slider_fill(widget_t *slider, unsigned color);
void slider_knobless(widget_t *slider);
void np_theme(void);
void paint_scrubber(widget_t *w, canvas_t *canvas);
/* One row of final colour, its alpha the row's. Inline: the round boxes' edges call it a pixel at a
 * time, and an exported call passes w on the stack. */
static inline void scrub_row(canvas_t *canvas, unsigned c, int x, int y, int w) {
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
enum { PODCASTS = 1, AUDIOBOOKS, PHOTOS };
enum { TONE_LIGHT = 2, TONE_RED = 3 }; /* columns of ACCENTS */
int geom_val(int v) __attribute__((pure));
extern const char *const MEDIA[];
unsigned flip_if(int on, unsigned c) __attribute__((pure));
int accent(void);
unsigned accent_map(unsigned c, int preset, int tone);
const int *clock_now(int *buf);
unsigned flip(unsigned c) __attribute__((pure));
unsigned flip_rgb(unsigned c) __attribute__((pure));
/* Whether the flip is on: the light half, or a card theme's page in either. Inline: ui_ink asks
 * for every colour drawn. */
static inline __attribute__((always_inline)) int ipod_paper(void) {
    return st.card_page || ipod_light();
}
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
/* card_theme.c */
void card_theme_load(void);
const scheme_t *card_theme_row(const scheme_t *s);
int card_theme_sets(int i);
void card_theme_ran(void);
void card_theme_poll(void);
void card_theme_sync(void);
/* card_rules.c (card_rules_apply: peq.h) */
void card_rule_line(const char *q);
void card_rules_clear(void);
int card_rules_used(void);
int card_rule_px(widget_t *w, int def);
int card_rules_art_hidden(void);

static inline const unsigned *tones_of(int v) {
    const theme_t *T = theme();
    return card_theme_row(&T->schemes[v])->tones;
}
static inline const unsigned *tones(void) { return tones_of(accent()); }

/* scrobble_ui.c */
void scrobble_listened(void);
void scrobble_auto(void);
int upload_scrobbles(void *ctx, event_t *event);

/* volume.c */
void vol_paint(widget_t *top, canvas_t *canvas);

/* netservice.c: System settings → Network's card app rows */
int ringnav_netservice(void *win, void *ctx);

/* addon_bar.c: a card app's status bar icon, shown while room (bar_sync) */
void bar_addon_sync(int room);

/* smart_wifi.c */
void smart_wake(void); /* Smart Wi-Fi's radio back now, if it had it off */
int smart_config(const char *section, const char *key);
void smart_poll(void);
/* the automatic scrobble upload's smart_wifi_wait, and its try over */
int smart_wifi_auto(void);
void smart_wifi_release(void);

/* battery.c */
void battery_tick(int level, int cable);
unsigned battery_log(char *out, unsigned size);
int battery_history(void *ctx, event_t *event);
void battery_paint(widget_t *w, canvas_t *canvas);

/* system.c */
void power_poll(void);
int told_once(const char *src, const char *seen, const char *tmp);

/* side_seek.c: a dark hold of Previous or Next (ple_key's phases) */
int side_seek(unsigned key, int phase);

/* usb_charge.c: wrappers for charging, configuration loading and USB gadget dispatch. */
int ringnav_usb_charge(int on), usb_charge_held(void), usb_charge_wanted(void);
void usb_charge_row(widget_t *win, widget_t *view); /* Power's USB DAC Charging */
void row_label(widget_t *label,
               const char *text); /* navigation.c: a settings row's "Title: value" */
int ringnav_usb_config(void), ringnav_usb_detect(void), ringnav_usb_status(void);
int stock_usb_config_trampoline(void), stock_usb_detect_trampoline(void);
int stock_usb_status_trampoline(void);

/* library_lists.c */
void album_view_toggle(void);
int era_menu(void *ctx, event_t *event);
int era_sql(const char *sql, int (*row)(void *, int, char **, char **), void *ctx);
int long_songs(void *ctx, event_t *event);
void *songs_by_path(const char *const *paths, unsigned n);
/* durations.c: UI-thread API (duration_seconds and duration_text in peq.h); the worker alone
 * probes files, the UI publishes after join. */
int durations_start(void *songs), durations_busy(int *done, int *total);
void durations_cancel(void), durations_poll(void), durations_shutdown(int bounded);
void mp_counted(void *r, unsigned key, unsigned evicted);
int most_played(void *ctx, event_t *event);
int shuffle_menu(void *ctx, event_t *event);
int shuffle_play(void *all);
int tidal_downloads(void *ctx, event_t *event);

/* tags.c */
int scan_quote(char *sql, unsigned cap, unsigned *n, const char *s);

/* ratings.c */
const char *hearts_text(char *s, int n);
int rate_keep(int album, unsigned key, int n);
int rate_kept(int album, unsigned key);
int rate_key(widget_t *top, unsigned key);
int rate_open(void);
int rate_saved(int k);
void rate_album_favs(void *album, int on);
int fav_told(void *r, int on); /* ratings: a My Fav change told to the song's card app */
int rate_show(widget_t *win, int pick, unsigned album);
void rate_bind(widget_t *win); /* ratings: Now Playing's heart */
int rated_albums(void *ctx, event_t *event);
int rt_closed(void *ctx, event_t *event);
int ratings_menu(void *ctx, event_t *event), rated_hold(widget_t *top);
void ratings_copy_due(void);
void ratings_copy_poll(void);
int rt_play(void *ctx, event_t *event);

/* classic_pages.c */
int kb_key(widget_t *w);
void kb_open(widget_t *win);
void kb_paint_key(widget_t *w, canvas_t *canvas);
void msg_open(widget_t *win);
void msg_paint(widget_t *w, canvas_t *canvas);
void paint_field(widget_t *w, canvas_t *canvas, int x, int y, int fw);
void paint_ramp(canvas_t *canvas, int top);
void pl_box_open(widget_t *win);
void pl_box_paint(widget_t *w, canvas_t *canvas);
void search_open(widget_t *win);
void search_paint(widget_t *w, canvas_t *canvas);
void search_tabs_paint(widget_t *group, canvas_t *canvas);
void search_x(widget_t *w, canvas_t *canvas);
void slider_paint(widget_t *w, canvas_t *canvas);
int search_del(widget_t *w);
void search_over(widget_t *w, canvas_t *canvas);
void slider_scrub(widget_t *w, canvas_t *canvas);

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
#define PL_ART "_pl_art"
void qm_playlist_bind(widget_t *w, int depth, int (*pick)(void *, event_t *));
void qm_select(void);
int qm_playlist_gone(void *ctx, event_t *event);
int qm_playlist_pick(void *ctx, event_t *event);
int ple_key(unsigned key, int phase);
int ple_listing(void);
int pl_first(menu_t *m, int cur);
void pl_fit(widget_t *button);

#endif
