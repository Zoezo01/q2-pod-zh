#ifndef Q2_PEQ_H
#define Q2_PEQ_H

#define STR_(x) #x
#define STR(x) STR_(x) /* a macro's value as a string literal */
#include "stock_types.h"

#ifdef PEQ_HOST
#ifndef _GNU_SOURCE
#define _GNU_SOURCE /* mmap64 */
#endif
#include <fcntl.h>
#include <sys/mman.h>
#include <time.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <unistd.h>
#define child_exit _exit
static inline void child_oom_first(void) {
    int fd = open("/proc/self/oom_score_adj", O_WRONLY);
    if (fd >= 0) write(fd, "1000", 4), close(fd);
}
static inline void crash_note(const char *path, const char *file, const char *log, unsigned every,
                              unsigned *n) {
    (void)path, (void)file, (void)log, (void)every, (void)n; /* the device's crash log only */
}
#else
#include "peq_platform.h"
#define PEQ_ROOT "" /* host tests pass a scratch directory */
void child_exit(int status) __attribute__((noreturn));
void child_oom_first(
    void); /* between fork and exec: the OOM killer takes this child, not demo (#169) */
void ringnav_clean_end(
    void); /* a clean end begins: the watchdog stops, the state record goes (system.c) */
void reboot_record(void *caller); /* a Bluetooth abnormal reboot's q2-crash.log (system.c) */
int card_mounted(void);           /* /proc/mounts has the card (system.c) */
/* The crash log's record of a long job's current file and demo's memory (tags.c) */
void crash_note(const char *path, const char *file, const char *log, unsigned every, unsigned *n);
/* The o32 syscall nr(a, b, c): its result, or -1 when the kernel reports an error
 * (coverflow_loader.c) */
int cf_syscall(int nr, long a, long b, long c);
/* Card apps without root (app_user.c): the card mounted for APP_UID; the drop, in a forked child;
 * system() as APP_UID */
int app_card(void);
void app_user(void);
void app_system(const char *cmd);
void app_own(const char *path);
#ifdef canvas_set_fill_color
/* Light Mode: every colour the payload draws passes ui_ink (theme_engine.c) on its way to these
 * stock setters, as stock's own colours pass the style and image hooks. stock_* sets a colour read
 * back from the LCD, which has passed already. */
unsigned ui_ink(unsigned rgba);
unsigned ui_ink_raw(unsigned raw);
static inline int stock_canvas_set_fill_color(canvas_t *c, unsigned x) {
    return canvas_set_fill_color(c, x);
}
static inline int stock_canvas_set_text_color(canvas_t *c, unsigned x) {
    return canvas_set_text_color(c, x);
}
static inline int stock_canvas_set_stroke_color(canvas_t *c, unsigned x) {
    return canvas_set_stroke_color(c, x);
}
static inline int ink_canvas_fill_rounded_rect(canvas_t *c, const void *r, const void *g,
                                               const void *color, unsigned radius) {
    unsigned v = ui_ink(*(const unsigned *)color);
    return canvas_fill_rounded_rect(c, r, g, &v, radius);
}
static inline int ink_canvas_stroke_rounded_rect(canvas_t *c, const void *r, const void *g,
                                                 const void *color, unsigned radius, unsigned w) {
    unsigned v = ui_ink(*(const unsigned *)color);
    return canvas_stroke_rounded_rect(c, r, g, &v, radius, w);
}
static inline int ink_vgcanvas_set_fill_color(vgcanvas_t *vg, unsigned x) {
    return vgcanvas_set_fill_color(vg, ui_ink(x));
}
static inline int ink_vgcanvas_set_stroke_color(vgcanvas_t *vg, unsigned x) {
    return vgcanvas_set_stroke_color(vg, ui_ink(x));
}
static inline int ink_vgcanvas_set_fill_linear_gradient(vgcanvas_t *vg, float sx, float sy,
                                                        float ex, float ey, unsigned from,
                                                        unsigned to) {
    return vgcanvas_set_fill_linear_gradient(vg, sx, sy, ex, ey, ui_ink(from), ui_ink(to));
}
#undef canvas_set_fill_color
#undef canvas_set_text_color
#undef canvas_set_stroke_color
#undef canvas_fill_rounded_rect
#undef canvas_stroke_rounded_rect
#undef vgcanvas_set_fill_color
#undef vgcanvas_set_stroke_color
#undef vgcanvas_set_fill_linear_gradient
#define canvas_set_fill_color(c, x) stock_canvas_set_fill_color(c, ui_ink(x))
#define canvas_set_text_color(c, x) stock_canvas_set_text_color(c, ui_ink(x))
#define canvas_set_stroke_color(c, x) stock_canvas_set_stroke_color(c, ui_ink(x))
#define canvas_fill_rounded_rect ink_canvas_fill_rounded_rect
#define canvas_stroke_rounded_rect ink_canvas_stroke_rounded_rect
#define vgcanvas_set_fill_color ink_vgcanvas_set_fill_color
#define vgcanvas_set_stroke_color ink_vgcanvas_set_stroke_color
#define vgcanvas_set_fill_linear_gradient ink_vgcanvas_set_fill_linear_gradient
#endif
#endif
#include "theme.h"

#define PEQ_BANDS 30 /* 10 per side plus 10 shared, as L/R APO files use */
#define PEQ_CHANNELS 8
#define PEQ_FILE_LIMIT 16384
#define PEQ_LINE_LIMIT 512
#define PEQ_ACTIVE PEQ_ROOT "/mnt/data/peq-active"
#define PEQ_SAVED PEQ_ROOT "/mnt/data/peq-presets"
#define PEQ_IMPORT PEQ_ROOT "/mnt/mmc/EQ"
#define EQ_OUTPUTS                                                                                 \
    PEQ_ROOT "/mnt/data/peq-outputs" /* EQ per Output: an eq_state per output, and owner */
#define EQ_DEVICES 32                /* Bluetooth devices remembered at once */

/* Versioned disk representation: fixed-width fields, no pointers or implicit padding.
 * enabled: 0 off, 1 both channels, 2 left only, 3 right only (v1 files hold 0 or 1).
 * balance: dB, above 0 turns the left channel down, below 0 the right (v2 only).
 * automatic: the preamp is 1 Auto (the player cuts each rate's own peak; preamp holds the 48 kHz
 * cut), 2 picked (an import's own preamp too), 0 never picked (saved before 1.3.6: PEQ_PRESET_V2
 * bytes, or imported before 1.4.4), which the UI and PEQ playback also take as Auto. */
typedef struct {
    int enabled, type;
    double frequency, gain, q;
} peq_band;
typedef struct {
    int count, bypass;
    double preamp;
    peq_band bands[PEQ_BANDS];
    double balance;
    int automatic, spare;
} peq_preset;
#define PEQ_PRESET_V2 __builtin_offsetof(peq_preset, automatic)
typedef struct {
    unsigned line;
    const char *reason;
} peq_error;
typedef struct {
    double b0, b1, b2, a1, a2;
} peq_coeff;
typedef struct {
    peq_coeff c[PEQ_BANDS];
    double z[PEQ_CHANNELS][PEQ_BANDS][2], gain[PEQ_CHANNELS];
    int bypass, used, only[PEQ_BANDS]; /* used: bands past it are identity with zero memory; only: 0
                                          every channel, else channel only-1 */
} peq_engine;
/* The EQ state (docs/internals.md#3eq), PEQ_ACTIVE's Q2PEQ03 record, replaced whole: which EQ plays
 * (mode, EQ_*), the last applied PEQ and the 3EQ, its three fixed bands (eq3_bands) and their
 * headroom preamp. hold: Off, but the 3EQ's preamp kept while its page compares against bypass. */
enum { EQ_OFF, EQ_3, EQ_PEQ };
typedef struct {
    int mode, hold;
    peq_preset peq, eq3;
} eq_state;
typedef struct {
    peq_engine current, next, pending;
    int rate, channels, ramp, ramp_length, waiting;
} peq_dsp;

void peq_default(peq_preset *p);
int peq_valid(const peq_preset *p);
int peq_parse(const char *text, unsigned size, peq_preset *out, peq_error *error);
int peq_number(const char *s, double *out);
int peq_import_file(const char *path, peq_preset *out, peq_error *error);
int peq_load(const char *path, peq_preset *out);
int peq_load_active(peq_preset *p); /* the effective preset (eq_effective); 0 if none was read */
int peq_save(const char *path, const peq_preset *p, int replace);
void eq3_bands(peq_preset *p, const double gain[3]);
int eq_load(eq_state *s); /* 0: no readable state, s holds the defaults */
int eq_save(const eq_state *s);
int eq_load_from(const char *path, eq_state *s), eq_save_to(const char *path, const eq_state *s);
int peq_save_file(const char *path, const char *magic, const void *data, unsigned size);
void eq_effective(const eq_state *s, peq_preset *out);
int peq_compile(const peq_preset *p, int rate, peq_engine *out); /* the saved preamp, Auto or not */
void peq_response(const peq_engine *e, int rate, double f, double *l, double *r);
/* Auto's preamp at rate, dB: what the player plays */
double peq_auto(const peq_preset *p, int rate);
void peq_reset(peq_dsp *d, int rate, int channels, const peq_preset *p);
int peq_update(peq_dsp *d, const peq_preset *p);
void peq_process(peq_dsp *d, float *audio, unsigned frames);

/* The visualizer's PCM tap (docs/internals.md#visualizer-ipod): hciplayer's filter writes what
 * plays, its first two channels and above 48 kHz every rate / 44100th frame, to ring[seq %
 * VIS_RING], then sets rate (of the ring), stamp (CLOCK_MONOTONIC ns of that write) and seq, the
 * frames written. demo creates the file, sized, under another name and renames it, so it never maps
 * a short one, and bumps want each frame it shows: a second of audio without a bump and the writer
 * stops copying. */
#define VIS_FILE PEQ_ROOT "/tmp/q2vis"
#define VIS_RING 65536 /* a power of two: 1.5 s at 44.1 kHz, room for VIS_LATENCY_MS */
typedef struct {
    unsigned seq, rate;
    long long stamp;
    unsigned want, pad;
    float ring[VIS_RING][2];
} vis_tap;
long long now_ns(void);
unsigned long long now_ms(void);
/* Crossfade (docs/internals.md#crossfade): demo writes the next track change's fade, an int in ms,
 * 0 for none; hciplayer's filter maps the file read-only and holds the outgoing track's tail for
 * it. */
#define XFADE_FILE                                                                                 \
    PEQ_ROOT "/tmp/q2xfade" /* the next fade's ms (XF_MS), under demo's count of stops (XF_STOPS)  \
                             */
#define XF_MS 0xffff
#define XF_STOPS 16
/* Mono Audio: demo writes an int, 1 to mix both channels into each; hciplayer's filter and q2video
 * map it read-only (video.c keeps its own copy of the path). */
#define MONO_FILE PEQ_ROOT "/tmp/q2mono"
#define SWAP_FILE PEQ_ROOT "/tmp/q2swap"
/* AirPlay (docs/internals.md#airplay-sender): the q2-airplay card app makes CAST_FILE, a cast_tap,
 * whole under another name and renames it, and keeps it. While the app reads (want, its CAST_CLOCK,
 * less than CAST_STALE old) hciplayer's filter puts what plays into ring[seq % CAST_RING] at
 * CAST_RATE, 16-bit, then sets id, pts_ms and stamp, then seq; mute set, it also skips the EQ and
 * silences the Q2's own output. A seek or a skip (not a song running into the next) sets jump_seq,
 * then bumps jumps. demo writes its part each pass and the playing song's tags to CAST_TRACK; it
 * reads the app's state and the speaker's place in the song, for the lyrics. The app (q2-airplay's
 * tap.go) mirrors this layout. */
#define CAST_FILE PEQ_ROOT "/tmp/q2cast"
#define CAST_TRACK PEQ_ROOT "/tmp/q2cast.track"
#define CAST_MAGIC 0x31544351u /* "QCT1" */
#define CAST_RATE 44100
#define CAST_RING 262144 /* a power of two: 5.9 s at CAST_RATE */
#define CAST_CLOCK(ns)                                                                             \
    ((unsigned)((unsigned long long)(ns) >> 20)) /* 1.048576 ms ticks: no division */
#define CAST_STALE 1430                          /* 1.5 s of ticks: demo lets go first */
#define CAST_STALE_PLAYER                                                                          \
    1900 /* 2 s: then the filter, its output 500 ms behind in ALSA's buffer                        \
          */
enum { CAST_OFF, CAST_CONNECTING, CAST_ON };
typedef struct {
    unsigned magic, size;
    /* the app's */
    /* clang-format off */
    unsigned want;   /* CAST_CLOCK while it reads */
    int mute;        /* the Q2's own output silent, its EQ skipped */
    int state;       /* CAST_*, for demo */
    int pos_ms;      /* the speaker's place in the song of filter instance pos_id, at pos_at (CAST_CLOCK) */
    unsigned pos_id, pos_at;
    int pos_run;     /* 1 while it plays on from there */
    char speaker[64];
    /* the filter's */
    unsigned seq;    /* frames written */
    unsigned id;     /* the filter instance (one a song) of the frame before seq */
    int pts_ms;      /* that frame's place in its song */
    unsigned jumps, jump_seq;
    int dsd;         /* the song plays through untouched (DSD, DoP): nothing to send */
    int spk;         /* the app's: the speaker's volume (0-100), as it said or the Q2 last set it */
    long long stamp; /* CLOCK_MONOTONIC ns of the last write */
    /* demo's */
    unsigned beat;   /* bumped each pass */
    int status;      /* mclGetPlayStatus: 1 stopped, 2 playing, 3 paused */
    int volume, maxvol; /* volume: the speaker's (0-100) while one plays, else -1 (also the filter's
                         * mute until demo has the earphones' level back) */
    unsigned track;  /* CAST_TRACK's writes */
    unsigned spk_seq; /* the app's: bumped with each spk (0: an app that never says) */
    /* clang-format on */
    short ring[CAST_RING][2];
} cast_tap;
_Static_assert(__builtin_offsetof(cast_tap, stamp) == 128 &&
                   __builtin_offsetof(cast_tap, ring) == 160,
               "q2-airplay's cast_tap layout");
/* The wired outputs' wait (docs/internals.md#wired-start): demo writes now_ms, never 0, at a press
 * that starts a track or seeks, and 0 at a natural change; hciplayer reads it at init's end and
 * seek's unmute. */
#define PRESS_FILE PEQ_ROOT "/tmp/q2press"
extern int
    press_natural; /* crossfade.c: inside a track change nobody pressed for, nothing stamps */
void press_stamp(void), press_forget(void);
/* An unpause goes back to where the fade began (docs/internals.md#unpause): demo writes now_ms,
 * never 0, just before a pause's mute; hciplayer reads it at the resume. */
#define MUTE_FILE PEQ_ROOT "/tmp/q2mute"
void mute_stamp(void);
/* librespot (spotify.c) and its restart loop, whose pid its launcher left, stopped: Spotify's idle
 * stop and Home's Rockbox row, which lets ALSA go too. A q2video sink ends with librespot's pipe.
 */
#define SPOT_OUT "/tmp/q2-librespot.out" /* the output for the card's aplay.sh, none on the DAC */
#define SPOT_DOWNLOAD                                                                              \
    "/tmp/q2-librespot.download" /* backend download progress, independent of playback */
#define SPOT_KILL                                                                                  \
    "kill -9 $(cat /tmp/q2-librespot); killall -9 librespot aplay; "                               \
    "rm -f /tmp/q2-librespot /tmp/q2-librespot.state " SPOT_OUT " " SPOT_DOWNLOAD
double decibels(double power); /* 10 log10 */

/* Audited against hciplayer 9c3f8c6d… and MPlayer 1.3.0 libaf/af.h: the filter peq_player.c opens.
 */
typedef struct {
    void *audio;
    int len, rate, nch, format, bps;
} af_data;
typedef struct af_instance {
    const void *info;
    int (*control)(struct af_instance *, int, void *);
    void (*uninit)(struct af_instance *);
    af_data *(*play)(struct af_instance *, af_data *);
    void *setup;
    af_data *data;
    struct af_instance *next, *prev;
    double delay, mul;
} af_instance;
/* id: one per filter instance, for crossfade and the carried filter memory; fresh: negotiated, not
 * yet played; ending: its last block was filtered near the track's end */
typedef struct {
    peq_dsp dsp;
    peq_preset preset;
    int id, fresh, ending;
} player_state;

/* The payload's shared helpers: coverflow.c's pages, hashes and worker, photos.c's, navigation.c's.
 */
widget_t *text(widget_t *parent, int x, int y, int w, int h);
/* coverflow.c's flip panel, which navigation.c's hold menu wears under Classic */
#define PANEL_X 40 /* the panel's first column: Cover Flow's axis less half of it */
#define BACK_W 294 /* its width and its rows' */
void coverflow_panel_paint(canvas_t *canvas, int x, int y, int head, int h, int over);
/* The landing's path (coverflow_land.c): the cubic ease-out, and the face's four corners. */
float land_ease(float x);
void land_quad(const int from[4], const int to[3], int ms, int len, int travel, float q[8],
               float *deg);
/* Classic's landing, the panel turning into Now Playing's art: what a frame of it is drawn from
 * (coverflow_land.c land_frame). from is the panel's box (x, y, w, h), to the art's (x, y, side),
 * in the frame's coordinates; the ms are the motion's, the covers' fade's and Now Playing's. The
 * pixels are the screen's BGRA: shelf the side covers' frame (CF_VIEW_W wide, or 0), back BACK_W x
 * BACK_H (0 once face up), front ART_SIZE square. */
typedef struct {
    int from[4], to[3], len, travel, out_at, out_ms, np_at, np_ms;
    const unsigned *shelf, *back, *front;
    unsigned paper; /* opaque */
} land_t;
int land_frame(unsigned *d, int pitch, int ms, const land_t *L, int box[4], int *veil);
void panel_font(widget_t *label, int px, int bold);
widget_t *page_open(const char *name, int (*closed)(void *, event_t *),
                    int (*keyup)(void *, event_t *));
void motion_open(widget_t *page,
                 const char *name); /* navigation.c: page_open's page takes the theme's hint */
widget_t *page_title(widget_t *body, const char *caption, int n);
void page_empty(widget_t *body, const char *name);
#define PAGE_TITLE "_page_title" /* the caption the bar reads (navigation.c page_caption) */
#define PAGE_TALL "_page_tall"   /* a two-line row, its separators' (paint_separators) */
int page_h(int h);
int page_x(void);
void page_retitle(widget_t *page, widget_t *title, const char *caption);
widget_t *page_list(widget_t *page, widget_t *body, widget_t **title, const char *caption, int n,
                    int item_h);
widget_t *page_list_or(widget_t *page, widget_t *body, const char *name, const char *none, int n,
                       int item_h);
int page_push(widget_t *view, int dir);
widget_t *page_row_detail(widget_t *view, int index, const char *caption, const char *detail,
                          int (*click)(void *, event_t *));
widget_t *page_row_detail_into(widget_t *view, int index, const char *caption, const char *detail,
                               int (*click)(void *, event_t *));
void page_row_into(widget_t *view, int index, const char *caption, int (*click)(void *, event_t *));
void row_into(widget_t *item, int h);
void list_toggle(widget_t *label, const char *title, int on); /* navigation.c: a row's switch */
/* navigation.c: Classic's value */
void list_value(widget_t *label, const char *title, const char *value);
char *csv_find(char *list, const char *item); /* navigation.c: comma lists */
int csv_toggle(char *list, unsigned size, const char *item);
int text_differs(widget_t *w, const char *s);
void *song_list(void);                              /* coverflow.c: an empty stSongInfo deque */
long file_size(const char *path);                   /* coverflow.c: -1 when it can't be opened */
int picture_size(const char *path, int *w, int *h); /* coverflow.c */
void *read_all(const char *path, unsigned max, unsigned *size); /* scrobble.c */
int visual(widget_t *s, int n, int *c, int *frac);
int thumb(const char *src, const char *dst, int w, int h);
extern volatile unsigned ff_kills; /* coverflow.c: ffmpeg runs killed or never started */
int card_space(const char *dir);
int card_room(const char *dir, unsigned mb, unsigned pct);
unsigned hash_bytes(unsigned h, const unsigned char *s, unsigned n);
unsigned fnv(unsigned h, const unsigned char *s);
void stop_timer(unsigned *timer), rearm(unsigned *timer, int (*fn)(const void *), unsigned ms);
int return_key(event_t *event, unsigned *timer,
               int (*leave)(const void *)); /* a page's keyup: Return */
int center_press(unsigned *timer, unsigned *at, int (*single)(const void *), int scrubbing);
int config_value(const char *section, const char *key, int n,
                 int def); /* theme_engine.c: a config.ini decimal */
int image_show(widget_t *img, const char *url, unsigned *size);
int album_cmp(void *a, void *b);
int spoken(const char *path);         /* navigation.c: under Podcasts or Audiobooks, or an M4B */
void peq_attach(void);                /* peq_ui.c */
void peq_page_replace(widget_t *old); /* peq_ui.c: the open EQ page closed for a new one */
/* navigation.c: the settings rows pod_rows builds (pod_values and its Q2POD keys); pod_value(i) is
 * one's value (only rows with a pod_values entry), read once from config.ini. */
enum {
    POD_CHARGE,
    POD_WAKE,
    POD_ARTIST_CF,
    POD_YEAR_OLD,
    POD_ARTISTS,
    POD_RESUME,
    POD_LOCKCLOCK, /* 1 Large, else Standard */
    POD_ARTIST_AZ,
    POD_VOL_GUARD,
    POD_HOLD_HOME,
    POD_STANDBY,
    POD_BT_LINK,
    POD_CF_SORT,    /* Cover Flow's Sort: coverflow.c's SORT_FILE, not config.ini */
    POD_CF_REFRESH, /* and Refresh Cover Flow, an action */
    POD_EQ_OUTPUT,  /* EQ per Output: the value and key; its row is Advanced EQ's (peq_ui.c) */
    POD_KEYTONE, /* Key Tone and In-Vehicle Mode: stock's bytes and SYSSET keys, as its rows did */
    POD_CARMODE,
    POD_SMART_WIFI,
    POD_COVERMODE, /* Enlarged Album Art and Folder Album Cover: stock's bytes and PLAYSET keys, as
                      its rows do */
    POD_FOLDERCOVER,
    POD_BIG_LIB, /* Display's Fast Navigation: Cover Flow's letters and their half copies
                    (coverflow_thumb.c, lettered, coverflow_halves); off unless the key is 1 */
    POD_PL_CF,   /* Display's Playlist View: List or Cover Flow, as Artist View */
    POD_SCAN_DURATIONS,
    POD_CF_POSITION,
    POD_HIDE_STREAM, /* Appearance's Hide Streaming / Hide Folder (coverflow_home_layout) */
    POD_HIDE_FOLDER,
    POD_REMIND_DAILY, /* System Update's Update Notifications (netupdate_poll); netupdate.c passes
                         pod_rows 26 */
    POD_ALBUM_GRID,   /* Library & Folders' Album View: stock's value and PLAYSET key
                         (album_view_toggle) */
    POD_HIDE_APPS,    /* Appearance's Hide Apps, Hide Games and Hide App Store: Library's rows
                         (home.c ringnav_localmusic) */
    POD_HIDE_GAMES,
    POD_HIDE_STORE,
    POD_BT_RECEIVE, /* Bluetooth's Receive Audio (headset_bt_service; the bluealsa2 launcher) */
    POD_CF_ART_LOG, /* Display's Log Failed Artwork: coverflow.c build_art */
    /* Display's Hide Duplicate Albums and Hide Duplicate Songs: coverflow.c music_albums and
     * coverflow_cover_tracks */
    POD_CF_DUP_ALBUMS,
    POD_CF_DUP_SONGS,
    POD_COUNT
};
int pod_value(int i);

/* EQ per Output (docs/internals.md#eq-per-output): peq_ui.c swaps the EQ when the output named key
 * ("3.5mm", "4.4mm", "usb", a Bluetooth MAC) takes over; forgetting whose it is ends the following.
 */
void eq_output(const char *key), eq_output_forget(void);
void ringnav_eq_output_toggle(void); /* and its row's click */
/* and a card app's (addon_eq.c): p playing, as Apply, else mode (EQ_PEQ, EQ_OFF), as the switch */
const char *eq_set(const peq_preset *p, int mode);
void peq_boot(void); /* clear a previous session's Simple EQ comparison before restoring playback */
extern int peq_chained; /* peq_ui.c: peq_stock_eq ran this boot */
void peq_chain(void);   /* crossfade.c: peq_attach once a connect (ringnav_mode), while playing */
/* crossfade.c: Mono Audio (0) and Swap L/R (1), as set */
int opt_on(int k);
#define NS_WARM 2000 /* nightshift.c: Night Shift's temperatures, K */
#define NS_COOL 5000
#define NS_STEP 100
#define NS_DEFAULT 3400
int night_on(void), night_kelvin(void); /* night_on: 0 Off, 1 On, 2 Auto */
void night_set(int on, int kelvin), night_save(void), night_paint(canvas_t *canvas),
    night_clock(int dark);
int clock_night(void); /* theme_engine.c: Night Shift's Auto hours now */
int worker_stop(unsigned long thread, int *running, volatile int *cancel, unsigned *timer);
void ringnav_select(widget_t *w, int id, int rows);
int library_scanning(void); /* coverflow.c: a library scan's thread runs */
int library_counting(void); /* coverflow.c: a song count reads songtable */
/* coverflow.c: the library's generation, one up each rewrite (Most Played's too); an album row's
 * tracks, its id and the length of a path's album folder */
extern volatile unsigned library_gen;
/* durations.c: cache-only reads for Cover Flow; no file probes on the UI thread. */
int duration_seconds(void *song);
const char *duration_text(char *text, unsigned size, void *song);
void durations_scanned(void); /* a library scan's end, on its thread */
int thread_lower(int idle);   /* coverflow_loader.c: a worker's nice 19 and its I/O class */
/* coverflow.c: the o32 statx syscall, as demo imports none */
int ringnav_statx(int dirfd, const char *path, int flags, unsigned mask, void *buf);
void *coverflow_album_tracks(void *r);
void *coverflow_cover_tracks(void *r); /* as Cover Flow lists them: Hide Duplicate Songs */
/* coverflow.c: qsort's order of unsigned keys; whether key is in keys[0..n), sorted so, and the
 * first there not below it (n for none) */
int key_cmp(const void *a, const void *b);
int has_key(const unsigned *keys, unsigned n, unsigned key);
unsigned key_at(const unsigned *keys, unsigned n, unsigned key);
unsigned album_id(void *r), album_dir(const char *p, int disc);
/* navigation.c: Cover Flow's glide and its letter */
int carousel_glide(widget_t *w, int goal, unsigned ms, int easing);
void coverflow_letter(widget_t *w, unsigned letter, unsigned now);
void coverflow_letter_hold(widget_t *w, unsigned letter);
/* A letter with this bit is the Year sort's year instead, in its low bits, 0 undated. */
#define CF_YEAR 0x80000000u
int blob_io(const char *path, const char *tmp, void *buf, unsigned size, int write);
/* card_theme.c: 1 when path holds these n bytes, read at most max */
int file_holds(const char *path, const void *b, unsigned n, unsigned max);
#define BLOB_IO(file, buf, write) blob_io(file, file ".tmp", &(buf), sizeof(buf), write)
int clip_within(canvas_t *canvas, int *old, int *clip, int x, int y, int w, int h);
void play_folder(void *dq, int idx, int cls);
int app_urls_play(const char *path); /* tidal.c: a card app's Q2URLS/1 queue, played */
int app_lyrics(const char *id, char *out, unsigned size); /* tidal.c: an app track's .lrc path */
int app_fav(void);         /* tidal.c: Now Playing's heart on an app's streamed song, -1 not one */
void app_fav_load(void);   /* tidal.c: a queue loading clears an app song's heart */
int app_urls_at(void);     /* tidal.c: the song playing's n in a now=1 URL queue playing; -1 none */
int directory(void *path); /* playback.c: toolsLoadDirectory, for staged */
#define TIDAL_DIR "/mnt/mmc/.tidal" /* tidal.c's cache, Tidal Downloads' list */
void tidal_clear(void);
/* Download All (tidal.c): Tidal Downloads' first row's caption, a press starting or ending it; its
 * once-a-second step (ringnav_sleep); its end (Stop Streaming Services); the row written again
 * (library_lists.c); every transfer ended for USB Storage (ringnav_usbmode) */
void tidal_all(int press, char *out, unsigned size), tidal_poll(void), tidal_all_stop(void),
    tidal_all_show(void), tidal_card_away(void);
#define SPOT_DIR "/mnt/mmc/.spotify" /* librespot and its launcher, spotify.c's */
/* addon.c: card apps, each a card folder .<id> holding its manifest q2app, its
 * launcher run, its binary q2-<id> and its icons. addon_scan finds them again (how many rows: a
 * slot's index stays its app's), addon_name, addon_id and addon_icon (an image name: file://… or
 * the shared stream_app) are one's while it is on the card (else 0), addon_slot its slot (else -1),
 * and addon_open its Streaming row's click (ctx: that slot, which a later scan's sort leaves on its
 * app). An app whose q2app says row=library is a row on Library's Apps page
 * (addon_apps) instead: addon_libs (how many, a scan), addon_lib_name and addon_lib_open, its click
 * (ctx: its place by name). addon_forget and addon_quit: the Apps page replaces or removes the
 * files of the app in folder dir (".<id>"): asked to end and waited for on the Apps page's worker,
 * then, once replaced or removed, forgotten on the UI thread. */
/* Card apps on the card at once: 16 more than the App Store lists (netupdate.c SH_MAX), for those
 * put there by hand. ponytail: a fixed table in .scratch, 532 bytes a slot over addon.c,
 * addon_library.c, addon_eq.c and navigation.h, its pages untouched until a slot is used; an app
 * past the last slot gets no row and nothing says so. A heap table that grows, if a card ever holds
 * more. */
#define APPS_MAX 64
int addon_scan(void), addon_open(void *ctx, event_t *event), addon_hold(widget_t *top);
int addon_games_last(void); /* the card's games as the last addon_scan found them */
#define GAMES_BUILTIN 4     /* games.c's Sudoku, Vortex, Solitaire and Snake */
const char *addon_name(int a), *addon_id(int a), *addon_icon(int a);
int addon_slot(int a);
const char *addon_slot_id(int i); /* slot i's app's id while it has a row anywhere, else 0 */
int addon_libs(void), addon_lib_open(void *ctx, event_t *event);
const char *addon_lib_name(int k);
int addon_apps(void *ctx, event_t *event), addon_apps_sync(void *win, event_t *event);
void library_apps_show(widget_t *row, int on); /* home.c: the Library's Apps row */
/* addon.c: System settings → Network's rows for apps whose q2app says row=network, as the Library's
 * above: addon_network (how many, a scan), addon_network_name, addon_network_icon,
 * addon_network_open (ctx: its place by name). */
int addon_network(void), addon_network_open(void *ctx, event_t *event);
const char *addon_network_name(int k), *addon_network_icon(int k);
/* addon.c: the wifi=1 apps, for addon_wifi.c's link follower: each started (but those the user
 * stopped), each stopped, and the user's stops forgotten. */
void addon_wifi_start(void), addon_wifi_stop(void), addon_wifi_release(void);
void addon_wifi_poll(void); /* addon_wifi.c: once per addon_poll pass */
/* addon.c: a running app's status bar icon, the one its status file's bar= names (bar.png for 1,
 * bar-<word>.png for a word; white, which Light Mode turns to the bar's ink), or 0 */
const char *addon_bar_icon(void);
int addon_bar_width(void);       /* addon.c: that icon's width, 0 for none */
int card_path(const char *path); /* on the card, no ".." segment: what an app or server may name */
int addon_is_icon(const char *name);     /* a card app's Streaming icon, by its file:// name */
int addon_is_bar_icon(const char *name); /* and its status bar icon */
void addon_poll(void), addon_stop(void), addon_paint(widget_t *w, canvas_t *canvas),
    addon_card_away(void);
void addon_forget(const char *dir), addon_quit(const char *dir);
/* addon.c's card games (q2app game=1, or any app whose row=games), Games' rows after its own: how
 * many (a scan), one's name, its press (a game's start over the screen, another app's page: 1 when
 * it runs or opened), whether it is in front (addon_game_front: not behind Now Playing), whether
 * chord_gate leaves Centre to it (addon_game_keys), and ringnav_input's events for it */
int addon_games(void), addon_game_start(int k);
int addon_game_front(void), addon_game_keys(void);
int addon_game_audio(
    void); /* a card game with audio=1 runs: the sound and the media keys are its */
int addon_game_open(const char *id); /* Home's Shortcut: the card game with this id, -1 none */
const char *addon_game_name(int k);
/* games.c: the theme's pop-up for a card game (Q2_POPUP's value), 7 + 6 * 7 + 64 * 7 + 1 bytes at
 * most */
#define POPUP_ENV 512
void popup_env(char *out, unsigned size);
int addon_game_input(event_t *e);
/* where app id's row is, as the screen names it: "Streaming", "Library's Games" or "Library" */
const char *addon_where(const char *id);
void ringnav_wdt_long(void);   /* system.c: the watchdog's long count for a long pass */
int ringnav_smart_awake(void); /* navigation.c: Smart Awake (IPOD KEEPLIT) is on */
void library_poll(void); /* addon_library.c: an app's library update request, once per addon_poll */
void addon_eq_poll(int slot, const char *id); /* addon_eq.c: app id's EQ request, in its poll */
/* spotify.c's Q2SPOT/1 framing (docs/librespot.md), which the card apps speak too */
int spot_escape(const char *s, char *out, unsigned size);
int spot_put(char *out, unsigned size, const char *s); /* "\t" and s escaped appended */
int spot_reply(char *buf, unsigned len, char **f, int max);
int spot_exchange(const char *path, unsigned wait_ms, unsigned max, char *buf, unsigned len,
                  unsigned *n, volatile int *cancel);
enum { SPOT_OK, SPOT_NOSOCK, SPOT_FAIL }; /* spot_exchange's results */
unsigned curl_save(const void *data, unsigned size, unsigned n, void *file); /* netupdate.c */
/* scrobble.c: a libcurl handle for url, verified with the firmware's CA file */
void *tls_easy(const char *url, long connect_s, long timeout_s);
/* library_lists.c: a card folder's songs played from the card (a card app's local row): a
 * folder opens its list, a file plays its folder from it; album, in track order (by name), else
 * newest first. */
int songs_open(const char *path, const char *title, int album);
void *app_album_songs(const char *dir); /* library_lists.c: a folder's songs by name, for addon.c */
int addon_goto(const char *path, const char *title, int album); /* Go to on a card app's song */
int addon_goes(const char *path);      /* addon.c: Go to on path opens a page */
int addon_scrobbles(const char *path); /* addon.c: not when path's app says scrobble=0 */
int addon_more(void); /* addon.c: Now Playing's ⋯ opens the playing queue's app's page */
void addon_open_id(const char *id); /* a card app's page by its id, hidden or not */
/* airplay.c: the q2-airplay app's side (docs/internals.md#airplay-sender) */
void airplay_poll(void);
int airplay_more(widget_t *view); /* whether it added its row */
int airplay_on(void), airplay_lyrics(int *ms);
/* addon.c: a heart's press, appended to the app's fav.log for it to send (docs/librespot.md);
 * addon_fav_path's for a song in a card folder an app's q2app names as favdir, else nothing to do.
 * 0 when the card didn't take it. */
int addon_fav(const char *id, const char *kind, const char *key, int on);
int addon_fav_path(const char *path, int on);
/* addon.c: a URL queue's app started, if it isn't running, before the player asks it for a song
 * (tidal.c app_track): whether it started lately; and whether its socket takes a connection yet */
int addon_wake(const char *id), addon_answers(const char *id);
extern unsigned auto_nexts;    /* playback.c ringnav_next: tracks that ended by themselves */
int stream_cache(int spotify); /* navigation.c: Streaming's Configuration, a cap of 512 << it MB */
void album_memory(void *r);
void draw_centred(canvas_t *canvas, const unsigned *s, unsigned n, const void *r, unsigned px,
                  unsigned color);
/* bidi_text.c: right-to-left text as AWTK's widgets draw it (visual order, Arabic shaped) */
int bidi_rtl(const unsigned *s, unsigned n);
int bidi_visual(const unsigned *s, unsigned n, unsigned *out);
int utf8_text(const char *s); /* xiph.c: valid UTF-8, a 4-byte character too */
void utf8_mend(char *s);      /* xiph.c: s valid UTF-8, the bytes of no whole character gone */
/* Lyrics as lyrics.c's loader reads them: each line's start (ms, LRC_FAR unsynced), the order it
 * came in, its UTF-8 text and its word stamps (A2's <mm:ss.xx>), each the ms a word starts and its
 * byte offset in the text. */
#define LRC_FAR 0x7fffffff
typedef struct {
    int ms;
    unsigned at;
} lrc_mark;
typedef struct {
    int time, at;
    const char *text;
    unsigned mark, marks;
} lrc_line;
/* lyrics.c, iPod: Now Playing's lyrics page, three large lines lit word by word (docs/internals.md#
 * word-timed-lyrics). lyrics_publish takes each load's lines; lyrics_rows turns stock's rows of lrc
 * into the page's (whether it is on, as lyrics_on asks), and lyrics_fit lays them out again at
 * lrc's new width; lyrics_now is the playing position in ms,
 * lyrics_sec its whole second (off the page), lyrics_line the playing line there (-1 before the
 * first), repainting it while page shows; lyrics_paint draws a row's line. */
void lyrics_publish(const lrc_line *l, unsigned n, const lrc_mark *m);
int lyrics_rows(widget_t *lrc), lyrics_fit(widget_t *lrc), lyrics_on(widget_t *lrc),
    lyrics_now(void), lyrics_sec(void), lyrics_line(widget_t *lrc, int ms, int page);
void lyrics_paint(widget_t *row, canvas_t *canvas), lyrics_gone(void);
unsigned
accent_tone(int tone); /* iPod: the Accent's ACCENTS column (Minimal's own: MINIMAL_FILL) */
unsigned rgba(unsigned rgb, unsigned alpha);
typedef struct {
    int x, y, w, h;
} rect_t;
/* navigation.c: a rounded box in color, or a square one when the canvas declines to round it */
void fill_box(canvas_t *canvas, rect_t *r, unsigned color, unsigned radius);
/* flat.c: the hold menu's card, which Cover Flow's flip panel wears under the flat look: HOLD_FOOT
 * of paper under its rows, and its ring no lower than QM_FOOT (the glass hides 38 px of the
 * screen's corners: the ring is at 39) */
#define HOLD_FOOT 10
#define QM_FOOT 308
void hold_card_flat(canvas_t *canvas, const rect_t *card, int head, int over);
/* const and pure below: each one's result rests on its arguments (and pure's, memory) alone, so
 * clang can keep one call for several (theme_engine.c and navigation.c define these). */
unsigned mix(unsigned from, unsigned to, int j, int n) __attribute__((const));
void caption(canvas_t *canvas, const char *s, int x, int y, int w, int h, unsigned px,
             unsigned color);
/* s's characters into to, at most room - 1 and a 0; how many (lyrics.c; a painter's tr() text) */
unsigned wide_text(unsigned *to, unsigned room, const char *s);
void peq_paint(widget_t *w, canvas_t *canvas);
/* spotify.c's, shared with radio.c */
unsigned long long now_ms(void); /* CLOCK_MONOTONIC */
void field(const char *raw, const char *key, char *out, unsigned n);
unsigned long long number(const char *raw, const char *key);
unsigned state_read(const char *path, char *raw, unsigned size);
struct unix_addr { /* sockaddr_un */
    unsigned short family;
    char path[108];
};
void dgram_send(int *sock, const char *path, const char *c, unsigned n);
int hex(char c); /* a hex digit's value, -1 for any other character; headset.c's too */
widget_t *label(widget_t *parent, int x, int y, int w, int h, const char *style, int px,
                unsigned color);
void spot_yield(void), spot_stop(void), radio_stop(void);
void headset_volume_reset(void);
void headset_poll(void), headset_eq_poll(void), headset_manual(void);
int media_owner(void), media_playing(void), media_focused(void);
void media_set_playing(int playing), media_key(unsigned key);
int radio_media(unsigned key), spot_media(unsigned key);
int headset_gain(void);
int spot_status(void), radio_status(void);
unsigned spot_session(void), radio_session(void);
void spot_set_playing(int playing), radio_set_playing(int playing);
int radio_start(const char *url); /* radio.c */
/* peq_ui.c's T9 edit, named EDIT_NAME: navigation.c's search_key cancels it on Return while its
 * keyboard is up. */
#define EDIT_NAME "q2_edit"
widget_t *edit_create(widget_t *item, const char *input_type, int y, int h);
void t9_box(widget_t *page, widget_t *edit,
            const char *tips);        /* classic_pages.c: a search box and its hint */
const char *output_device(char *vol); /* radio.c */
void dac_take(void);                  /* radio.c */
/* navigation.c: the theme's font for role on w, or stock's */
int theme_font(widget_t *w, int role, int was);
#define FONT_ROW (-2)               /* was: cache the answer in w's own "_font" prop */
void marq_restart(widget_t *row);   /* navigation.c: its title changed, its marquee re-armed */
void list_stop_scroll(widget_t *w); /* navigation.c: its scroll animation stopped */
/* script.c: per-script display (docs/internals.md#themes, Scripts) */
/* w keeps the theme's bold intent where its text fell back to stock's face */
void font_weight_mark(widget_t *w, int bold);
void font_weight_paint(widget_t *w,
                       canvas_t *canvas); /* a marked label's foreground again 1 px right */
int script_face(int face, const unsigned *s, int n, int *px); /* the FONT_* that draws s */
/* canvas text's face, 0: the system font; *px, unless 0: the px to draw it at */
const char *theme_face(const unsigned *s, int n, int *px);
/* 1 for a character lyrics break a line beside and time as three: CJK ideographs, kana, Hangul
 * and full-width forms. */
static inline int script_cjk(unsigned c) {
    return (c >= 0x2e80 && c < 0xa000) || (c >= 0xac00 && c < 0xd7b0) ||
           (c >= 0xff00 && c < 0xfff0);
}
/* c's capital for Minimal's small capitals: Unicode's simple capital of each letter of ASCII,
 * Latin-1 and Latin Extended-A but µ and ſ, of ơ ư, Cyrillic and Vietnamese's letters with tone
 * marks; anything else (Greek too) is c. ponytail: checked on stock V1.36's font after font_letters
 * (tools/build.py): where it has the small letter it has the capital (of Cyrillic from U+0492 it
 * has neither case); a new base font, or a new range here, needs the same check: a capital the font
 * lacks draws nothing. */
static inline unsigned script_upper(unsigned c) {
    if ((c >= 'a' && c <= 'z') || (c >= 0xe0 && c <= 0xfe && c != 0xf7) ||
        (c >= 0x430 && c <= 0x44f))
        return c - 32;
    if (c >= 0x450 && c <= 0x45f) return c - 80; /* ѐ to џ */
    if (c == 0xff) return 0x178;                 /* ÿ */
    if (c == 0x131) return 'I';                  /* dotless ı */
    if ((c >= 0x100 && c <= 0x137) || (c >= 0x14a && c <= 0x177) || c == 0x1a1 ||
        (c >= 0x460 && c <= 0x481) || (c >= 0x48a && c <= 0x4bf) || (c >= 0x4d0 && c <= 0x52f) ||
        (c >= 0x1ea0 && c <= 0x1ef9))
        return c & ~1u; /* pairs, the capital even */
    if ((c >= 0x139 && c <= 0x148) || (c >= 0x179 && c <= 0x17e) || c == 0x1b0 ||
        (c >= 0x4c1 && c <= 0x4ce))
        return (c - 1) | 1; /* pairs, the capital odd */
    return c == 0x4cf ? 0x4c0 : c;
}
void bar_title_sync(widget_t *win); /* navigation.c: Classic's bar title names win */
void bar_title_name(widget_t *win); /* navigation.c: names win by its name, on top or not */
void np_mark_add(widget_t *const *image, unsigned n); /* np_marks.c: the flat look takes them */
/* navigation.c: Classic's Spotify and Internet Radio Now Playing, laid out as the local page, 0
 * under the other themes; Classic's bar on a canvas (its scrub cue with mark), 0 where none. */
int stream_ipod(widget_t *art, widget_t *const text[4], widget_t *elapsed, widget_t *remain,
                widget_t *bar, widget_t *heart);
/* navigation.c: Classic's times and bar fitted to a track sec long (the bar 0: the radio's); 1 when
 * they moved. Not in a paint. */
int ipod_times(widget_t *elapsed, widget_t *remain, widget_t *bar, int sec);
int ipod_bar(canvas_t *canvas, int x, int y, int bw, int on, int mark);
/* navigation.c's and clock.c's painters for Classic's info screen (lockscreen.c); colours are
 * final. */
typedef struct {
    unsigned rgb;       /* 0xRRGGBB */
    short r;            /* a disc's radius in tenths of the -50..50 box; 0: pts */
    unsigned char n;    /* pts */
    unsigned char hand; /* 1 the hour's angle, 2 the minute's */
    short pts[5][2];    /* tenths, pointing at 12 */
} clock_part_t;
void paint_clock(widget_t *w, canvas_t *canvas, int x, int y, int size, const char *art, int h,
                 int m, const clock_part_t *parts, int n);
void paint_page(widget_t *w, canvas_t *canvas, int y);
void paint_art(widget_t *w, canvas_t *canvas, const char *name, int x, int y);
void paint_bar(widget_t *w,
               canvas_t *canvas); /* classic_pages.c: the status bar, on a window over it */
int bar_title_x(void);
int bar_title_y(void);
void paint_tray(canvas_t *canvas, int x, int y, int w, int h);
void text_shadow(canvas_t *canvas, const char *face, const unsigned *s, const void *r, unsigned px,
                 unsigned alpha);
void final_text(canvas_t *canvas, const char *face, const unsigned *s, const void *r, unsigned px,
                unsigned color);
unsigned bar_battery_key(void);
int clock_minutes(void), bar_status(void);
void slider_label(widget_t *w, int x, int y, int ww, int h, int px, const char *face);
void text_ink(widget_t *w, unsigned color, int n); /* a label's final 0xAARRGGBB, in either half */
void slider_number(widget_t *label), slider_tray_dirty(widget_t *page),
    slider_centre_hint(widget_t *page, int night);
void switch_row(widget_t *win, widget_t *view); /* the toolbar's, as a row */
void stock_more_speed(widget_t *win);
void stock_more_into(widget_t *win);
void info_poll(void);          /* lockscreen.c: the info screen's timer, with the screen lit */
int xfade_page(widget_t *top); /* crossfade.c */
int ipod_battery(void);        /* theme_engine.c: IPOD BATTERY, 0 Icon, else a percentage */
int title_gone(void *w, event_t *event); /* ... and looks for its source again at the next sync */
int bar_title_fits(widget_t *label);     /* label's text fits the bar's title box */
int ipod_light(void);                    /* theme_engine.c: Light Mode */
unsigned flat_page(void);    /* flat.c: the page's ground, 0xRRGGBB, 0 unless the flip is on */
unsigned unflip(unsigned v); /* theme_engine.c: the grey Light Mode's flip turns into v */
int ipod_square_art(void);   /* navigation.c: Classic, and Modern under Square */
/* card_rules.c: a card theme's rules for a page, on its window as it opens */
enum { CARD_PLAYING, CARD_LOCK, CARD_HOME };
void card_rules_apply(widget_t *win, int page);
/* and Home's, for coverflow.c, which reads card_home first (CARD_HOME_ROWS: a Home row's rule,
 * CARD_HOME_ART: homeart names a JPEG) */
enum { CARD_HOME_ROWS = 1, CARD_HOME_ART = 2 };
extern int card_home;
void card_rules_home(widget_t *list);
unsigned card_rules_art(const char **cover, unsigned key);
#define SPOT_GREY 0xffaaaaaau
void radio_search(void);      /* radio.c */
void toast(const char *text); /* navigation.c */
/* language.c: Q2 Pod's English in the language picked (the same pointer when it has no line; a
 * format keeps its checks), N_ marking a static table's English for the pack's list (translated
 * where it is shown), and the card's languages kept in step */
const char *tr(const char *en) __attribute__((format_arg(1)));
#define N_(en) en
void language_sync(void);
void language_removing(const char *dir);
int valid_id(const char *s); /* addon.c: a card app's id */
/* smart_wifi.c: its radio woken, 1 while its link comes up; its radio off; an action
 * held for that link (fn(ctx, 0) once it is up, if page, or any page when 0, is still in use) */
int smart_wifi_wait(void), smart_wifi_off(void);
int smart_hold(int (*fn)(void *, event_t *), void *ctx, const char *page);
int need_link(int (*fn)(void *, event_t *),
              void *ctx); /* smart_hold, else "Connect to Wi-Fi first" */
/* the work smart_wifi.c keeps the radio on for: spotify.c's, scrobble.c's, tidal.c's, addon.c's,
 * netupdate.c's and onlinematch.c's */
int spot_running(void), scrobble_busy(void), tidal_busy(void), addon_running(void),
    netupdate_busy(void), album_lyrics_busy(void);
int addon_offline(void);               /* addon.c: the card app's page open is a wifi=0 app's */
void netupdate_poll(void);             /* power_poll: a newer release told on Home (netupdate.c) */
const char *coverflow_sort_name(void); /* coverflow.c */
void coverflow_sort_step(void), coverflow_refresh_art(void);
void coverflow_clean_end(void); /* coverflow.c: a clean end mid-build strikes no album */
/* Playlists as Cover Flow: coverflow.c's covers of lists (p_deque_playlist), 0 when it has none,
 * its centred playlist (a cover's hold) and its close of the covers; playlists.c's open of playlist
 * cd_num from its row on the Playlists page win under them, its index in the live list, and New
 * Playlist (edit 0) or Select Playlists… (1) on that page. */
int coverflow_playlists(void *lists);
int coverflow_playlist_queue(void); /* the queue a playlist's cover played, no load since */
void *coverflow_playlist(widget_t *page);
widget_t *coverflow_playlists_close(void);
void playlist_open(widget_t *win, int cd_num);
unsigned playlist_index(int cd_num);
void playlists_edit(int edit);
int rockbox_available(void);   /* theme_engine.c: a regular Rockbox binary on the card */
int groovebox_available(void); /* theme_engine.c: the Groovebox card app's binary on the card */
#define SHORTCUT_GROOVEBOX 10  /* IPOD SHORTCUT's Groovebox, after the ten stock-era values */
#define SHORTCUT_NONE 11       /* IPOD SHORTCUT's None: Home has no Shortcut row */
int page_keyup(void *ctx, event_t *event); /* navigation.c: Return leaves a payload page */
int ringnav_localclass(int cls);           /* navigation.c: load_localclass_list's hook */
int shelf_open(void *ctx, event_t *event); /* netupdate.c: Library's App Store page */
void shelf_tab(int tab); /* netupdate.c: the App Store's open tab, 0 Apps, 1 Games */
int shelf_key(widget_t *top, unsigned key); /* netupdate.c: Previous and Next switch its tabs */
/* app_store.c: the App Store's tabs and rows, drawn in ringnav_paint (store_paint) */
widget_t *store_tabs(widget_t *page, widget_t *view);
void store_tabs_set(widget_t *strip, int tab, const int *updates);
void store_state(widget_t *item, int state);
void store_paint(widget_t *w, canvas_t *canvas);
/* games.c: s in the theme's face at px, h rows from y: align 0 from x, 1 centred on it, 2 ending at
 * it; Minimal's capitals spaced. Its width. */
int text_at(canvas_t *canvas, const char *s, int x, int y, int h, unsigned px, unsigned color,
            int align);
enum { LEFT, MID, RIGHT };      /* text_at's align */
const char *shelf_busy(void);   /* netupdate.c: the app folder a job replaces */
const char *shelf_moving(void); /* netupdate.c: the app folder deleted or moved into */
int shelf_erase(int start);     /* netupdate.c: Spotify's erase, started or running */
void stream_erased(void);       /* navigation.c: Configuration follows a finished erase */
void album_lyrics_stop(void);   /* onlinematch.c: Download Album Lyrics before USB storage */

int lib_db_open(void **db, const char *pragmas); /* playlists.c: open the library database */
/* mp3_gapless.c: MPEG tables and a Layer III frame's bytes, shared with peq_player.c's frame checks
 */
extern const unsigned short mp3_rates[3], mp3_kbps[2][15];
unsigned mp3_frame_bytes(unsigned version, unsigned br, unsigned hz, unsigned pad);

/* 2038 (docs/internals.md#2038). glibc 2.28's time() truncates the kernel's 64-bit seconds to 32
 * bits, so read unsigned it is right until 2106; -1 still means it failed. */
static inline long long wall_time(void) {
    long t = time((void *)0);
    return t == -1 ? -1 : (long long)(unsigned long)t;
}
/* A wall time before this (2020-09-13) is a clock never set: scrobbling's test, Battery History's.
 */
#define CLOCK_SET 1600000000u
/* CURLOPT_SSL_CTX_FUNCTION: sets X509_V_FLAG_NO_CHECK_TIME on the SSL_CTX's X509_VERIFY_PARAM, so
 * chains still verify against the pinned roots after they expire (2035 on) and after the 32-bit
 * clock wraps. The rootfs's OpenSSL 1.1.1f exports these but demo does not import them, so this
 * writes its fields: SSL_CTX->param at 0x100, ->flags at 0xc, which tools/build.py audits.
 * ponytail: an expired server certificate is accepted too; the firmware has no revocation anyway.
 */
static inline int tls_untimed(void *curl, void *ctx, void *unused) {
    (void)curl, (void)unused;
    (*(unsigned **)((char *)ctx + 0x100))[3] |= 0x200000;
    return 0; /* CURLE_OK */
}

#endif
