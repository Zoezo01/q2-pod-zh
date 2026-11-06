#ifndef Q2_PEQ_H
#define Q2_PEQ_H

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
#else
#include "peq_platform.h"
#define PEQ_ROOT "" /* host tests pass a scratch directory */
void child_exit(int status) __attribute__((noreturn));
void child_oom_first(void); /* between fork and exec: the OOM killer takes this child, not demo (#169) */
void ringnav_clean_end(void); /* a clean end begins: the watchdog stops, the state record goes (navigation.c) */
#ifdef canvas_set_fill_color
/* Light Mode: every colour the payload draws passes ui_ink (navigation.c) on its way to these stock
 * setters, as stock's own colours pass the style and image hooks. stock_* sets a colour read back
 * from the LCD, which has passed already. */
unsigned ui_ink(unsigned rgba);
unsigned ui_ink_raw(unsigned raw);
static inline int stock_canvas_set_fill_color(void *c, unsigned x) {
    return canvas_set_fill_color(c, x);
}
static inline int stock_canvas_set_text_color(void *c, unsigned x) {
    return canvas_set_text_color(c, x);
}
static inline int stock_canvas_set_stroke_color(void *c, unsigned x) {
    return canvas_set_stroke_color(c, x);
}
static inline int ink_canvas_fill_rounded_rect(void *c, const void *r, const void *g,
                                               const void *color, unsigned radius) {
    unsigned v = ui_ink(*(const unsigned *)color);
    return canvas_fill_rounded_rect(c, r, g, &v, radius);
}
static inline int ink_canvas_stroke_rounded_rect(void *c, const void *r, const void *g,
                                                 const void *color, unsigned radius, unsigned w) {
    unsigned v = ui_ink(*(const unsigned *)color);
    return canvas_stroke_rounded_rect(c, r, g, &v, radius, w);
}
static inline int ink_vgcanvas_set_fill_color(void *vg, unsigned x) {
    return vgcanvas_set_fill_color(vg, ui_ink(x));
}
static inline int ink_vgcanvas_set_stroke_color(void *vg, unsigned x) {
    return vgcanvas_set_stroke_color(vg, ui_ink(x));
}
static inline int ink_vgcanvas_set_fill_linear_gradient(void *vg, float sx, float sy, float ex,
                                                        float ey, unsigned from, unsigned to) {
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
#define EQ_OUTPUTS PEQ_ROOT "/mnt/data/peq-outputs" /* EQ per Output: an eq_state per output, and owner */
#define EQ_DEVICES 32 /* Bluetooth devices remembered at once */

/* Versioned disk representation: fixed-width fields, no pointers or implicit padding.
 * enabled: 0 off, 1 both channels, 2 left only, 3 right only (v1 files hold 0 or 1).
 * balance: dB, above 0 turns the left channel down, below 0 the right (v2 only).
 * automatic: the preamp is 1 Auto (the player cuts each rate's own peak; preamp holds the 48 kHz
 * cut), 2 picked (an import's own preamp too), 0 never picked (saved before 1.3.6: PEQ_PRESET_V2
 * bytes, or imported before 1.4.4), which the UI and PEQ playback also take as Auto. */
typedef struct { int enabled, type; double frequency, gain, q; } peq_band;
typedef struct {
    int count, bypass;
    double preamp;
    peq_band bands[PEQ_BANDS];
    double balance;
    int automatic, spare;
} peq_preset;
#define PEQ_PRESET_V2 __builtin_offsetof(peq_preset, automatic)
typedef struct { unsigned line; const char *reason; } peq_error;
typedef struct { double b0, b1, b2, a1, a2; } peq_coeff;
typedef struct {
    peq_coeff c[PEQ_BANDS];
    double z[PEQ_CHANNELS][PEQ_BANDS][2], gain[PEQ_CHANNELS];
    int bypass, used, only[PEQ_BANDS]; /* used: bands past it are identity with zero memory; only: 0 every channel, else channel only-1 */
} peq_engine;
/* The EQ state (docs/internals.md#3eq), PEQ_ACTIVE's Q2PEQ03 record, replaced whole: which EQ plays
 * (mode, EQ_*), the last applied PEQ and the 3EQ, its three fixed bands (eq3_bands) and their
 * headroom preamp. hold: Off, but the 3EQ's preamp kept while its page compares against bypass. */
enum { EQ_OFF, EQ_3, EQ_PEQ };
typedef struct { int mode, hold; peq_preset peq, eq3; } eq_state;
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

/* The visualizer's PCM tap (docs/internals.md#visualizer-ipod): hciplayer's filter writes what plays, its
 * first two channels and above 48 kHz every rate / 44100th frame, to ring[seq % VIS_RING], then sets
 * rate (of the ring), stamp (CLOCK_MONOTONIC ns of that write) and seq, the frames written. demo
 * creates the file, sized, under another name and renames it, so it never maps a short one, and bumps
 * want each frame it shows: a second of audio without a bump and the writer stops copying. */
#define VIS_FILE PEQ_ROOT "/tmp/q2vis"
#define VIS_RING 65536 /* a power of two: 1.5 s at 44.1 kHz, room for VIS_LATENCY_MS */
typedef struct { unsigned seq, rate; long long stamp; unsigned want, pad; float ring[VIS_RING][2]; } vis_tap;
long long now_ns(void);
unsigned long long now_ms(void);
/* Crossfade (docs/internals.md#crossfade): demo writes the next track change's fade, an int in ms,
 * 0 for none; hciplayer's filter maps the file read-only and holds the outgoing track's tail for it. */
#define XFADE_FILE PEQ_ROOT "/tmp/q2xfade" /* the next fade's ms (XF_MS), under demo's count of stops (XF_STOPS) */
#define XF_MS 0xffff
#define XF_STOPS 16
/* Mono Audio: demo writes an int, 1 to mix both channels into each; hciplayer's filter and q2video map
 * it read-only (video.c keeps its own copy of the path). */
#define MONO_FILE PEQ_ROOT "/tmp/q2mono"
#define SWAP_FILE PEQ_ROOT "/tmp/q2swap"
/* The wired outputs' wait (docs/internals.md#wired-start): demo writes now_ms, never 0, at a press that starts
 * a track or seeks, and 0 at a natural change; hciplayer reads it at init's end and seek's unmute. */
#define PRESS_FILE PEQ_ROOT "/tmp/q2press"
extern int press_natural; /* crossfade.c: inside a track change nobody pressed for, nothing stamps */
void press_stamp(void), press_forget(void);
/* An unpause goes back to where the fade began (docs/internals.md#unpause): demo writes now_ms, never 0, just
 * before a pause's mute; hciplayer reads it at the resume. */
#define MUTE_FILE PEQ_ROOT "/tmp/q2mute"
void mute_stamp(void);
/* librespot (spotify.c) and its restart loop, whose pid its launcher left, stopped: Spotify's idle
 * stop and Home's Rockbox row, which lets ALSA go too. A q2video sink ends with librespot's pipe. */
#define SPOT_OUT "/tmp/q2-librespot.out" /* the output for the card's aplay.sh, none on the DAC */
#define SPOT_DOWNLOAD "/tmp/q2-librespot.download" /* backend download progress, independent of playback */
#define SPOT_KILL                                                                                  \
    "kill -9 $(cat /tmp/q2-librespot); killall -9 librespot aplay; "                               \
    "rm -f /tmp/q2-librespot /tmp/q2-librespot.state " SPOT_OUT " " SPOT_DOWNLOAD
double decibels(double power); /* 10 log10 */

/* Audited against hciplayer 9c3f8c6d… and MPlayer 1.3.0 libaf/af.h: the filter peq_player.c opens. */
typedef struct { void *audio; int len, rate, nch, format, bps; } af_data;
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

/* The payload's shared helpers: coverflow.c's pages, hashes and worker, photos.c's, navigation.c's. */
void *text(void *parent, int x, int y, int w, int h);
void *page_open(const char *name, int (*closed)(void *, void *), int (*keyup)(void *, void *));
void motion_open(void *page,
                 const char *name); /* navigation.c: page_open's page takes the theme's hint */
void *page_title(void *body, const char *caption, int n);
void page_empty(void *body, const char *name);
#define PAGE_TITLE "_page_title" /* the caption the bar reads (navigation.c page_caption) */
#define PAGE_TALL "_page_tall"   /* a two-line row, its separators' (paint_separators) */
int page_h(int h);
int page_x(void);
void page_retitle(void *page, void *title, const char *caption);
void *page_list(void *page, void *body, void **title, const char *caption, int n, int item_h);
void *page_list_or(void *page, void *body, const char *name, const char *none, int n, int item_h);
int page_push(void *view, int dir);
void *page_row_detail(void *view, int index, const char *caption, const char *detail,
                      int (*click)(void *, void *));
void *page_row_detail_into(void *view, int index, const char *caption, const char *detail,
                           int (*click)(void *, void *));
void page_row_into(void *view, int index, const char *caption, int (*click)(void *, void *));
void row_into(void *item, int h);
void list_toggle(void *label, const char *title, int on); /* navigation.c: a row's switch */
/* navigation.c: Classic's value */
void list_value(void *label, const char *title, const char *value);
char *csv_find(char *list, const char *item);                /* navigation.c: comma lists */
int csv_toggle(char *list, unsigned size, const char *item);
void *bottom_caption(void *parent, int h);
int text_differs(void *w, const char *s);
long file_size(const char *path); /* coverflow.c: -1 when it can't be opened */
void *read_all(const char *path, unsigned max, unsigned *size); /* scrobble.c */
int visual(void *s, int n, int *c, int *frac);
int thumb(const char *src, const char *dst, int w, int h);
int card_space(const char *dir);
int by_string(const void *a, const void *b);
unsigned hash_bytes(unsigned h, const unsigned char *s, unsigned n);
unsigned fnv(unsigned h, const unsigned char *s);
void stop_timer(unsigned *timer), rearm(unsigned *timer, int (*fn)(const void *), unsigned ms);
int return_key(void *event, unsigned *timer, int (*leave)(const void *)); /* a page's keyup: Return */
int center_press(unsigned *timer, unsigned *at, int (*single)(const void *), int scrubbing);
int config_value(const char *section, const char *key, int n, int def); /* navigation.c: a config.ini digit */
int image_show(void *img, const char *url, unsigned *size);
int album_cmp(void *a, void *b);
void peq_attach(void); /* peq_ui.c */
/* EQ per Output (docs/internals.md#eq-per-output): peq_ui.c swaps the EQ when the output named key
 * ("3.5mm", "4.4mm", "usb", a Bluetooth MAC) takes over; forgetting whose it is ends the following. */
void eq_output(const char *key), eq_output_forget(void);
int ringnav_eq_output(void);         /* navigation.c: the setting */
void ringnav_eq_output_toggle(void); /* and its row's click */
void peq_boot(void); /* clear a previous session's Simple EQ comparison before restoring playback */
extern int peq_chained; /* peq_ui.c: peq_stock_eq ran this boot */
void peq_chain(void);   /* crossfade.c: peq_attach once a connect (ringnav_mode), while playing */
int swap_on(void);
int mono_on(void);      /* crossfade.c: Mono Audio's setting */
#define NS_WARM 2000    /* nightshift.c: Night Shift's temperatures, K */
#define NS_COOL 5000
#define NS_STEP 100
#define NS_DEFAULT 3400
int night_on(void), night_kelvin(void); /* night_on: 0 Off, 1 On, 2 Auto */
void night_set(int on, int kelvin), night_save(void), night_paint(void *canvas), night_clock(int dark);
int clock_night(void); /* navigation.c: Night Shift's Auto hours now */
int worker_stop(unsigned long thread, int *running, volatile int *cancel, unsigned *timer);
void ringnav_select(void *w, int id, int rows);
int library_scanning(void); /* coverflow.c: a library scan's thread runs */
int blob_io(const char *path, const char *tmp, void *buf, unsigned size, int write);
#define BLOB_IO(file, buf, write) blob_io(file, file ".tmp", &(buf), sizeof(buf), write)
int clip_within(void *canvas, int *old, int *clip, int x, int y, int w, int h);
void play_folder(void *dq, int idx, int cls);
int directory(void *path); /* playback.c: toolsLoadDirectory, for staged */
#define TIDAL_DIR "/mnt/mmc/.tidal" /* tidal.c's cache, Tidal Downloads' list */
void tidal_clear(void);
#define SPOT_DIR "/mnt/mmc/.spotify" /* librespot and its launcher, spotify.c's */
/* addon.c: card apps, each a card folder .<id> holding its manifest q2app, its
 * launcher run, its binary q2-<id> and its icons. addon_scan finds them again (how many slots: a
 * slot's index stays its app's), addon_name, addon_id and addon_icon (an image name: file://… or the
 * shared stream_app) are one's while it is on the card (else 0), addon_open
 * its Streaming row's click (ctx: the slot). addon_forget and addon_quit: the Apps page replaces or
 * removes the files of the app in folder dir (".<id>"): forgotten on the UI thread, then asked to
 * end and waited for on the Apps page's worker. */
#define APPS_MAX 8 /* card apps on the card at once */
int addon_scan(void), addon_open(void *ctx, void *event), addon_hold(void *top);
int addon_games_last(void); /* the card's games as the last addon_scan found them */
#define GAMES_BUILTIN 4     /* games.c's Sudoku, Vortex, Solitaire and Snake */
const char *addon_name(int a), *addon_id(int a), *addon_icon(int a);
int addon_is_icon(const char *name); /* a card app's Streaming icon, by its file:// name */
void addon_poll(void), addon_stop(void), addon_paint(void *w, void *canvas), addon_card_away(void);
void addon_forget(const char *dir), addon_quit(const char *dir);
/* addon.c's card games (q2app game=1), Games' rows after its own: how many (a scan), one's name,
 * its start over the screen (1 when it runs), whether one runs, and ringnav_input's events for it */
int addon_games(void), addon_game_start(int k), addon_game_on(void);
const char *addon_game_name(int k);
int addon_game_input(void *e), addon_is_game(const char *id);
void ringnav_wdt_long(void); /* navigation.c: the watchdog's long count for a long pass */
int ringnav_smart_awake(void); /* navigation.c: Smart Awake (IPOD KEEPLIT) is on */
void library_poll(void); /* addon_library.c: an app's library update request, once per addon_poll */
/* spotify.c's Q2SPOT/1 framing (docs/librespot.md), which the card apps speak too */
int spot_escape(const char *s, char *out, unsigned size);
int spot_reply(char *buf, unsigned len, char **f, int max);
void spot_shut(int fd);
/* navigation.c: a card folder's songs played from the card (a card app's local row): a folder
 * opens its list, a file plays its folder from it; album, in track order (by name), else newest
 * first. */
int songs_open(const char *path, const char *title, int album);
void *app_album_songs(const char *dir); /* navigation.c: a folder's songs by name, for addon.c */
int addon_goto(const char *path, const char *title, int album); /* Go to on a card app's song */
int addon_goes(const char *path); /* addon.c: Go to on path opens a page */
int addon_scrobbles(const char *path); /* addon.c: not when path's app says scrobble=0 */
extern unsigned auto_nexts; /* playback.c ringnav_next: tracks that ended by themselves */
int stream_cache(int spotify); /* navigation.c: Streaming's Configuration, a cap of 512 << it MB */
void album_memory(void *r);
void draw_centred(void *canvas, const unsigned *s, unsigned n, const void *r, unsigned px, unsigned color);
unsigned book_char(const unsigned char *t, unsigned n, unsigned *i); /* books.c: UTF-8, else Latin-1 */
int utf8_text(const char *s); /* navigation.c: valid UTF-8, a 4-byte character too */
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
int lyrics_rows(void *lrc), lyrics_fit(void *lrc), lyrics_on(void *lrc), lyrics_now(void),
    lyrics_sec(void), lyrics_line(void *lrc, int ms, int page);
void lyrics_paint(void *row, void *canvas), lyrics_gone(void);
unsigned accent_tone(int tone); /* iPod: the Accent's ACCENTS column (Minimal's own: MINIMAL_FILL) */
unsigned rgba(unsigned rgb, unsigned alpha), mix(unsigned from, unsigned to, int j, int n);
void caption(void *canvas, const char *s, int x, int y, int w, int h, unsigned px, unsigned color);
void peq_paint(void *w, void *canvas);
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
void *label(void *parent, int x, int y, int w, int h, const char *style, int px, unsigned color);
void spot_yield(void), spot_stop(void), radio_stop(void);
void headset_volume_reset(void);
void headset_poll(void), headset_eq_poll(void), headset_manual(void);
int media_owner(void), media_playing(void), media_focused(void);
void media_set_playing(int playing), media_key(unsigned key);
void video_key(unsigned key);
int radio_media(unsigned key), spot_media(unsigned key);
int headset_gain(void);
int spot_status(void), radio_status(void), video_status(void);
unsigned spot_session(void), radio_session(void), video_session(void);
void spot_set_playing(int playing), radio_set_playing(int playing), video_set_playing(int playing);
int video_start(const char *file, int radio); /* books.c */
/* peq_ui.c's T9 edit, named EDIT_NAME: navigation.c's search_key cancels it on Return while its
 * keyboard is up. */
#define EDIT_NAME "q2_edit"
void *edit_create(void *item, const char *input_type, int y, int h);
void t9_box(void *page, void *edit, const char *tips); /* navigation.c: a search box and its hint */
const char *output_device(char *vol);         /* books.c */
void dac_take(void);                          /* books.c */
/* navigation.c: the theme's font for role on w, or stock's; canvas text's face, 0: the system
 * font */
int theme_font(void *w, int role, int was);
#define FONT_ROW -2 /* was: cache the answer in w's own "_font" prop */
const char *theme_face(const unsigned *s, int n, int *px); /* *px, unless 0: the px to draw it at */
void bar_title_sync(void *win); /* navigation.c: Classic's bar title names win */
/* navigation.c: Classic's Spotify and Internet Radio Now Playing, laid out as the local page, 0
 * under the other themes; Classic's bar on a canvas (its scrub cue with mark), 0 where none. */
int stream_ipod(void *art, void *const text[4], void *elapsed, void *remain, void *bar,
                void *heart);
int ipod_bar(void *canvas, int x, int y, int bw, int on, int mark);
/* navigation.c's painters for Classic's info screen (lockscreen.c); colours are final. */
typedef struct {
    unsigned rgb;       /* 0xRRGGBB */
    short r;            /* a disc's radius in tenths of the -50..50 box; 0: pts */
    unsigned char n;    /* pts */
    unsigned char hand; /* 1 the hour's angle, 2 the minute's */
    short pts[5][2];    /* tenths, pointing at 12 */
} clock_part_t;
void paint_clock(void *w, void *canvas, int x, int y, int size, const char *art, int h, int m,
                 const clock_part_t *parts, int n);
void paint_page(void *w, void *canvas, int y);
void paint_art(void *w, void *canvas, const char *name, int x, int y);
void paint_dark_bar(void *w, void *canvas, const unsigned *title, unsigned key);
int dark_title_y(void);
void paint_tray(void *canvas, int x, int y, int w, int h);
void text_shadow(void *canvas, const char *face, const unsigned *s, const void *r, unsigned px,
                 unsigned alpha);
void final_text(void *canvas, const char *face, const unsigned *s, const void *r, unsigned px,
                unsigned color);
unsigned dark_bar_key(const unsigned *t);
int clock_minutes(void), bar_status(void);
void slider_label(void *w, int x, int y, int ww, int h, int px, const char *face);
void text_ink(void *w, unsigned color, int n); /* a label's final 0xAARRGGBB, in either half */
void slider_number(void *label), slider_tray_dirty(void *page),
    slider_centre_hint(void *page, int night);
void switch_row(void *win, void *view); /* the toolbar's, as a row */
void stock_more_speed(void *win);
void stock_more_into(void *win);
void info_poll(void);      /* lockscreen.c: the info screen's timer, with the screen lit */
int xfade_page(void *top); /* crossfade.c */
int ipod_battery(void);    /* navigation.c: IPOD BATTERY, 0 Icon, else a percentage */
int title_gone(void *w, void *event); /* ... and looks for its source again at the next sync */
int bar_title_fits(void *label);      /* label's text fits the bar's title box */
int ipod_light(void);   /* navigation.c: Light Mode */
unsigned unflip(unsigned v); /* navigation.c: the grey Light Mode's flip turns into v */
int song_seconds(void *r);   /* navigation.c, its lengths' cache, for Cover Flow's tracks panel */
int ipod_square_art(void); /* navigation.c: Classic, and Modern under Square */
#define SPOT_GREY 0xffaaaaaau
void radio_search(void);       /* radio.c */
void toast(const char *text); /* navigation.c */
const char *coverflow_sort_name(void); /* coverflow.c */
void coverflow_sort_step(void), coverflow_refresh_art(void);
int rockbox_available(void);  /* navigation.c: a regular Rockbox binary on the card */
int page_keyup(void *ctx, void *event); /* navigation.c: Return leaves a payload page */
int ringnav_localclass(int cls);        /* navigation.c: load_localclass_list's hook */
int shelf_open(void *ctx, void *event); /* netupdate.c: System settings' Apps page */
const char *shelf_busy(void);           /* netupdate.c: the app folder a job replaces */
int shelf_erase(int start);             /* netupdate.c: Spotify's erase, started or running */
void stream_erased(void);               /* navigation.c: Configuration follows a finished erase */
void album_lyrics_stop(void);           /* onlinematch.c: Download Album Lyrics before USB storage */

/* 2038 (docs/internals.md#2038). glibc 2.28's time() truncates the kernel's 64-bit seconds to 32
 * bits, so read unsigned it is right until 2106; -1 still means it failed. */
static inline long long wall_time(void) {
    long t = time((void *)0);
    return t == -1 ? -1 : (long long)(unsigned long)t;
}
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
