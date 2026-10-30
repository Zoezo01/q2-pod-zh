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
#else
#include "peq_platform.h"
#define PEQ_ROOT "" /* host tests pass a scratch directory */
void child_exit(int status) __attribute__((noreturn));
#ifdef canvas_set_fill_color
/* Light Mode: every colour the payload draws passes ui_ink (navigation.c) on its way to these stock
 * setters, as stock's own colours pass the style and image hooks. stock_* sets a colour read back
 * from the LCD, which has passed already. */
unsigned ui_ink(unsigned rgba);
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

#define PEQ_BANDS 30 /* 10 per side plus 10 shared, as L/R APO files use */
#define PEQ_CHANNELS 8
#define PEQ_FILE_LIMIT 16384
#define PEQ_LINE_LIMIT 512
#define PEQ_ACTIVE PEQ_ROOT "/mnt/data/peq-active"
#define PEQ_SAVED PEQ_ROOT "/mnt/data/peq-presets"
#define PEQ_IMPORT PEQ_ROOT "/mnt/mmc/EQ"

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
/* Crossfade (docs/internals.md#crossfade): demo writes the next track change's fade, an int in ms,
 * 0 for none; hciplayer's filter maps the file read-only and holds the outgoing track's tail for it. */
#define XFADE_FILE PEQ_ROOT "/tmp/q2xfade"
/* Mono Audio: demo writes an int, 1 to mix both channels into each; hciplayer's filter and q2video map
 * it read-only (video.c keeps its own copy of the path). */
#define MONO_FILE PEQ_ROOT "/tmp/q2mono"
#define SWAP_FILE PEQ_ROOT "/tmp/q2swap"
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
void *page_title(void *body, const char *caption);
void *page_list(void *page, void *body, void **title, const char *caption, int n, int item_h);
void *page_row_detail(void *view, int index, const char *caption, const char *detail,
                      int (*click)(void *, void *));
void *page_row_detail_into(void *view, int index, const char *caption, const char *detail,
                           int (*click)(void *, void *));
void page_row_into(void *view, int index, const char *caption, int (*click)(void *, void *));
void row_into(void *item, int h);
void list_toggle(void *label, const char *title, int on); /* navigation.c: a row's switch */
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
void peq_boot(void); /* clear a previous session's Simple EQ comparison before restoring playback */
extern int peq_chained; /* peq_ui.c: peq_stock_eq ran this boot */
void peq_chain(void);   /* crossfade.c: peq_attach once a boot, while playing */
int swap_on(void);
int mono_on(void);      /* crossfade.c: Mono Audio's setting */
int worker_stop(unsigned long thread, int *running, volatile int *cancel, unsigned *timer);
void ringnav_select(void *w, int id, int rows);
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
const char *addon_name(int a), *addon_id(int a), *addon_icon(int a);
int addon_is_icon(const char *name); /* a card app's Streaming icon, by its file:// name */
void addon_poll(void), addon_stop(void), addon_paint(void *w, void *canvas), addon_card_away(void);
void addon_forget(const char *dir), addon_quit(const char *dir);
/* spotify.c's Q2SPOT/1 framing (docs/librespot.md), which the card apps speak too */
int spot_escape(const char *s, char *out, unsigned size);
int spot_reply(char *buf, unsigned len, char **f, int max);
void spot_shut(int fd);
/* navigation.c: a card folder's songs played from the card (a card app's local row): a folder
 * opens its list, a file plays its folder from it; album, in track order (by name), else newest
 * first. */
int songs_open(const char *path, const char *title, int album);
void *app_album_songs(const char *dir); /* navigation.c: a folder's songs by name, for addon.c */
int stream_cache(int spotify); /* navigation.c: Streaming's Configuration, a cap of 512 << it MB */
void album_memory(void *r);
void draw_centred(void *canvas, const unsigned *s, unsigned n, const void *r, unsigned px, unsigned color);
unsigned book_char(const unsigned char *t, unsigned n, unsigned *i); /* books.c: UTF-8, else Latin-1 */
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
 * into the page's (whether it is on, as lyrics_on asks); lyrics_now is the playing position in ms,
 * lyrics_sec its whole second (off the page), lyrics_line the playing line there (-1 before the
 * first), repainting it while page shows; lyrics_paint draws a row's line. */
void lyrics_publish(const lrc_line *l, unsigned n, const lrc_mark *m);
int lyrics_rows(void *lrc), lyrics_on(void *lrc), lyrics_now(void), lyrics_sec(void),
    lyrics_line(void *lrc, int ms, int page);
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
void headset_poll(void), headset_manual(void);
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
const char *output_device(char *vol);         /* books.c */
void dac_take(void);                          /* books.c */
#if IPOD
int ipod_classic(void); /* navigation.c: Theme: Classic */
int bold_label(void *w, int was); /* navigation.c: Classic's bold font for w's text, or stock's */
int ipod_light(void);   /* navigation.c: Light Mode */
#define np_bar_h() (ipod_classic() ? NP_BAR_CLASSIC : NP_BAR_MINIMAL)
#else
#define np_bar_h() SPOT_BAR_H
#endif
#define SPOT_GREY 0xffaaaaaau
void radio_search(void);       /* radio.c */
void toast(const char *text); /* navigation.c */
const char *coverflow_sort_name(void); /* coverflow.c */
void coverflow_sort_step(void), coverflow_refresh_art(void);
int rockbox_available(void);  /* navigation.c: a regular Rockbox binary on the card */
int page_keyup(void *ctx, void *event); /* navigation.c: Return leaves a payload page */
int shelf_open(void *ctx, void *event); /* netupdate.c: System settings' Apps page */
const char *shelf_busy(void);           /* netupdate.c: the app folder a job replaces */
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
