#ifndef Q2_COVERFLOW_H
#define Q2_COVERFLOW_H
/* What coverflow.c shares with coverflow_thumb.c, coverflow_letters.c and coverflow_loader.c: the
 * page's state, the depth renderer's and the flip's, which the thumb reads, the thumb's follower,
 * which the renderer reads, the poses and columns the letters draw with, and the texture pool the
 * loader fills. */
#include "offsets.inc"
#include "peq.h"
#ifndef PEQ_HOST
#include "stock.h"
#endif
#include "chapters.h"
#include "library_scope.h"
#include "playback.h"
#include "xiph.h"

enum { ARTIST_ALBUMS = 1, RATED_ALBUMS, ALBUM_TRACKS, PLAYLISTS }; /* cf.scoped */
/* after the albums: an artist's All albums (two or more), the rated albums' Sort; none after the
 * library's, whose Sort and Refresh are Cover Flow settings' Sort and Refresh Cover Flow
 * (coverflow_sort_step, coverflow_refresh_art), nor after the playlists */
#define CARDS                                                                                      \
    (cf.scoped == ARTIST_ALBUMS ? deque_size(cf.albums) > 1 : cf.scoped && cf.scoped != PLAYLISTS)
#define ABS(x) ((x) < 0 ? -(x) : (x))

typedef struct {
    char *track; /* the album's first track, copied so the thread never reads stock deques */
    unsigned key;
    int shared; /* another album has a track in this folder, so its cover.jpg may be theirs */
} job_t;
typedef struct {
    void *page, *body, *covers, *slide, *name, *artist, *title;
    void *albums, *tracks; /* our copies of the stock query results; albums in the Sort's order */
    void *stock;           /* the albums in stock order, which albums is, or is sorted from */
    int sort, sort_read;   /* the Sort card's order (SORT_*), from SORT_FILE once */
    int year_old, artists; /* the Year order and Artists settings albums was sorted in */
    int on_sort;           /* the next load centres the Sort card */
    int resort; /* Display settings changed the library's order: the next load reads it */
    job_t *jobs;
    unsigned long thread;
    unsigned timer, title_timer; /* title_timer: the captions follow the centre cover mid-move */
    int titled;                  /* the album the captions name, + 1 */
    unsigned saved_album, albums_gen; /* albums_gen: library_gen when albums was queried */
    int screen, album, running, text_albums;
    volatile int done, total, cancel;
    /* Scoped (ARTIST_ALBUMS, RATED_ALBUMS, ALBUM_TRACKS or PLAYLISTS, else 0): an artist's albums
     * (coverflow_artist), albums holding them, the rated ones (coverflow_rated), stock holding them
     * in the library's order and sort their own order, one album's tracks alone
     * (coverflow_goto_album), or the playlists, albums holding their containers
     * (coverflow_playlists); all_albums, all_stock and all_sort the library's, back on close.
     * No saved album; hearts gives a rated album's. */
    int scoped, all_sort;
    void *all_albums, *all_stock;
    int (*hearts)(void *);
} cf_page_t;
extern cf_page_t cf;

/* On the card, beside stock's own cover cache (/mnt/mmc/.sldp); playlist_art.c's files too. */
#define ART_DIR PEQ_ROOT "/mnt/mmc/.coverflow"
#define PL_BUILDING ART_DIR "/pl-building" /* playlist_art.c's art_note */
#define ART_SIZE 160                       /* a texture's side, texels */
#define CF_TEXELS (ART_SIZE * ART_SIZE)
#define CF_ONE 65536
#define CF_REACH 3 /* ring slots either side of the centre that can show (fading in) or preload */
#define CF_EYE16 (16 * cfs.eye) /* the eye's distance, 1/16 px */
#define CF_HALF16 (16 * ART_SIZE / 2)
#ifndef ART_FFMPEG
#define ART_FFMPEG "/usr/bin/ffmpeg"
#endif
#define CF_WIDE_REACH 7 /* CF_REACH under a thumb theme, for the row at speed */
#define CF_WIDE (2 * CF_WIDE_REACH + 1)
/* textures going by letters: the covers on the screen and the ones ahead of the row, the letter's
 * and the letters' either side of it (fx_window); 100 KB each */
#define FX_POOL 64
#define LD_SLOTS 32       /* the half copies the loader holds at most; 25.6 KB each */
#define CF_ENTER_SPEED 12 /* the entry into letters' least speed, albums a second */
#define CF_FOLD_MS 450    /* the fold out of letters onto the letter's first album, ms */
extern cf_style_t cfs; /* the active theme's Cover Flow values, copied at page open (cf_restyle) */
/* A right-hand cover t (16.16, >= 0) from the visual position: centre xc and depth zc (1/16 px),
 * rotation c, s (16.16), brightness (of 256) and the eye it is seen from (1/16 px). Left covers
 * mirror it. */
typedef struct {
    int xc, zc, c, s, bright, eye;
} pose_t;

/* The frame being drawn, nearest cover first: each pixel's alpha byte holds the coverage so far
 * (255 opaque), and a layer only fills what is left, which composites as drawing back to front
 * would. Per column, rows solid_top to solid_bottom are known opaque and skipped outright, so the
 * hidden parts of side covers cost nothing. */
typedef struct {
    unsigned *d;
    int pitch;
    short solid_top[CF_VIEW_W], solid_bottom[CF_VIEW_W];
} shelf_t;

/* fx.mode: the shelf, letters' held row (leaning right or left), the fold, the entry */
enum { FX_SHELF, FX_HELD, FX_FOLD, FX_HELD_LEFT, FX_ENTRY };
/* The depth renderer's state: the frame, the textures of the covers around the visual position
 * and the placeholder's. No frame: the flat fallback (stock images on the slide_menu). */
typedef struct {
    void *frame;                     /* bitmap_t *, CF_VIEW_W x cfs.view_h BGRA8888 */
    unsigned *tex;                   /* fx_base()'s slots, then the placeholder */
    int album[FX_POOL];              /* each slot's album + 1; 0 is empty */
    const unsigned *art[FX_POOL];    /* its texture: the slot's own, or the placeholder */
    unsigned char half[FX_POOL];     /* it holds the album's half copy (half_read) */
    int c, frac, drawn, light, lead; /* the position and Light Mode the frame holds, once drawn */
    int arch;                        /* and how far its arch had formed (16.16) */
    int todo;                        /* a cover wanted still waits for its load (FX_BUDGET_MS) */
    int mode;                        /* what the frame holds (FX_SHELF ...) */
    int letters_at, next, prev; /* the album + 1 the letters' windows are of, and those letters */
    int halves;                 /* coverflow_halves' next album */
    unsigned half_at;           /* and when it last made a copy, ms (HALF_GAP_MS) */
    int half_ms, full_ms;       /* the last load's ms, a half copy wanted or not */
    /* The loader: a thread that reads the half copies fx_window asks for into stage, ld[s]'s
     * at stage + s * HALF_TEXELS. state (LD_*) hands each request over, atomically; the rest of
     * a request is the UI thread's while it is LD_FREE, ok the loader's while LD_READ. */
    struct {
        unsigned key;
        int album, rank, state, ok;
    } ld[LD_SLOTS];
    unsigned *stage;
    unsigned gate[8];     /* a zeroed sem_t, posted when there are requests */
    unsigned long loader; /* the thread */
    int ld_on, ld_stop;   /* ld_on: 1 running, 0 not asked yet, -1 could not start (the UI thread
                           * then reads the half copies) */
    unsigned paint_at;    /* fx_window's last frame, ms */
    unsigned *more; /* going by letters, FX_POOL's slots past the shelf's; freed as they end */
    int no_more;    /* refused (MemAvailable short) or their calloc failed: not asked again until
                       letters end */
} cf_depth_t;
extern cf_depth_t fx;
/* Classic's flip to the tracks panel (flip_open): its frame, the panel as painted and the turn. */
typedef struct {
    void *frame;    /* bitmap_t *, CF_VIEW_W x FLIP_H */
    unsigned *back; /* BACK_W x BACK_H, the panel as painted */
    const unsigned *front;
    void *panel, *sheet, *title, *artist;
    unsigned timer, t0;
    int dir;   /* 1 turning to the panel, -1 back, 0 at rest */
    int drawn; /* the e the frame holds */
    int light;
} cf_flip_t;
extern cf_flip_t fl;

/* The covers held by the thumb (docs/internals.md#coverflow-thumb): one follower per UI pass, in
 * albums from SLIDE_INDEX, its speeds a ms. on: it owns the offset, until it commits. */
typedef struct {
    float pos, vel, goal, lead, lead_v, rate, tau, ticks, seen, seen_lead, stretch;
    unsigned at, tick_at, edge_at, letter_at;
    int raw, on, finger, held, dir, dead, early, clicked, bounced, capped, jump;
    /* Fast Navigation off: 1.7.6's letters (th_beat), each named on the moving row from the last
     * one named (jump_to), jump_pace apart (letter_at) */
    int beat, jump_to;
    int lean, fold;   /* the way the row leans (1: as right-hand covers; against th.dir as letters
                         began), kept through the entry, the letters and the fold, 0 from the
                         landing; folding back out from it */
    unsigned fold_at; /* the fold's start */
    float enter;      /* the entry's travel left to letters, in albums; 0 once in */
    float fold_off, fold_vel; /* where the row was, albums from the letter's first, and its
                                 speed, albums a ms, as the fold began */
    float hold_lead, hold_run, hold_xrun, hold_xs; /* going by letters, the row held as the entry
                              left it (coverflow_hold): its facing point's lead and run (albums),
                              the shelf's shift and a cover's xc an album as a pose (1/16 px) */
    int hold_k; /* and the album at the screen's middle, from the row's frac centre (16.16) */
    float arch, seen_arch; /* how far the arch has formed, 0 to 1 (th_arch) */
} cf_thumb_t;
extern cf_thumb_t th;

/* coverflow.c */
unsigned album_key(void *r);
unsigned tags_key(const char *artist, const char *album);
unsigned album_ident(const char *album, const char *album_artist, const char *path);
int album_art(const char *track, const char *dst, int shared);
void art_note(const char *note, unsigned key, const char *track);
void art_strike(const char *note);
int ff_wait(int pid, const char *tmp, const char *dst, int *exited);
int ff_thumb(const char *src, const char *dst, int w, int h);
char *art_path(char *out, unsigned key, const char *suffix);
int cf_decode(const char *url, unsigned *out, int unload);
/* the texture pool (fx_window): the shelf's slots, then the placeholder */
int fx_base(void);
#define FX_PLACEHOLDER (fx.tex + fx_base() * CF_TEXELS)
unsigned *fx_buf(int i);
int fx_slot(int album);
void fx_fill(int i, int a, int half);
void fx_unletter(int c);
/* the depth renderer, which the letters draw with (coverflow_letters.c) */
extern const int cf_cos90[17];
void cf_restyle(void);
void turn(int a, int other, pose_t *p);
void pose(int t, pose_t *p);
int project(const pose_t *p, int u);
int unproject(const pose_t *p, int dx, int *u, int *h);
void draw_posed(shelf_t *f, const pose_t *p, int side, const unsigned *tex);
void draw_quad(shelf_t *f, int xl, int xr, int hl, int hr, const unsigned *tex, int bright);
int cf_floor(float x);
int cf_round(float x);
float cf_smooth(float x);
int coverflow_bounded(void *w);
int coverflow_jump(void *w, int from, int dir, unsigned *letter);
int coverflow_letter_first(int i, unsigned *letter);
int coverflow_letter_next(void *w, int from, int dir, int n, unsigned *letter);
int covers_live(void);
float ff_decay(float s);
int lettered(void *w);
int stride_of(void *s);
void titles(unsigned c);

/* coverflow_letters.c */
float coverflow_enter_v(void);
float coverflow_hold(void);
int coverflow_window(int frac);
void draw_row(shelf_t *f, int frac, const unsigned *const *ring);
void arch_pose(int t, pose_t *p);
int fold_t16(void);
void draw_fold(shelf_t *f, const unsigned *const *ring, int t16, int lean, int off16);

/* coverflow_loader.c: half copies, and the loader that reads them (its requests' LD_* states) */
enum { LD_FREE, LD_WANT, LD_READ, LD_READY };
int half_read(unsigned key, unsigned *tex);
int ld_state(int s);
int ld_find(int album);
int ld_drop(int s);
void ld_ask(const int *want, int k);
void ld_take(int s, int i, int a);
void ld_quit(void);
void coverflow_halves(void);

/* Optional browsing position. */
int coverflow_position_enabled(void);
void coverflow_position_paint(void *w, void *canvas);
void coverflow_position_reset(void);
#endif
