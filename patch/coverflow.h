#ifndef Q2_COVERFLOW_H
#define Q2_COVERFLOW_H
/* What coverflow.c shares with coverflow_thumb.c: the page's state, the depth renderer's and the
 * flip's, which the thumb reads, and the thumb's follower, which the renderer reads. */
#include "offsets.inc"
#include "peq.h"
#ifndef PEQ_HOST
#include "stock.h"
#endif
#include "library_scope.h"
#include "playback.h"
#include "xiph.h"

enum { ARTIST_ALBUMS = 1, RATED_ALBUMS, ALBUM_TRACKS }; /* cf.scoped */
/* after the albums: an artist's All albums (two or more), the rated albums' Sort; none after the
 * library's, whose Sort and Refresh are Display settings' Coverflow Sort and Refresh Coverflow
 * (coverflow_sort_step, coverflow_refresh_art) */
#define CARDS (cf.scoped == ARTIST_ALBUMS ? deque_size(cf.albums) > 1 : cf.scoped ? 1 : 0)
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
    /* Scoped (ARTIST_ALBUMS, RATED_ALBUMS or ALBUM_TRACKS, else 0): an artist's albums
     * (coverflow_artist), albums holding them, the rated ones (coverflow_rated), stock holding them
     * in the library's order and sort their own order, or one album's tracks alone
     * (coverflow_goto_album); all_albums, all_stock and all_sort the library's, back on close.
     * No saved album; hearts gives a rated album's. */
    int scoped, all_sort;
    void *all_albums, *all_stock;
    int (*hearts)(void *);
} cf_page_t;
extern cf_page_t cf;

#define CF_WIDE_REACH 7 /* CF_REACH under a thumb theme, for the row at speed */
#define CF_WIDE (2 * CF_WIDE_REACH + 1)
extern cf_style_t cfs; /* the active theme's Cover Flow values, copied at page open (cf_restyle) */
/* The depth renderer's state: the frame, the textures of the covers around the visual position
 * and the placeholder's. No frame: the flat fallback (stock images on the slide_menu). */
typedef struct {
    void *frame;                     /* bitmap_t *, CF_VIEW_W x cfs.view_h BGRA8888 */
    unsigned *tex;                   /* fx_reach's texture slots, then the placeholder */
    int album[CF_WIDE];              /* each slot's album + 1; 0 is empty */
    const unsigned *art[CF_WIDE];    /* its texture: the slot's own, or the placeholder */
    int c, frac, drawn, light, lead; /* the position and Light Mode the frame holds, once drawn */
    int todo;                        /* a slot still waits for its decode (FX_DECODES) */
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
    int raw, on, finger, held, dir, dead, early, clicked, bounced, capped, jump, jump_to;
} cf_thumb_t;
extern cf_thumb_t th;

/* coverflow.c */
unsigned album_key(void *r);
int cf_floor(float x);
int cf_round(float x);
float cf_smooth(float x);
int coverflow_bounded(void *w);
int coverflow_jump(void *w, int from, int dir, unsigned *letter);
int coverflow_round(int dir, unsigned *letter);
int covers_live(void);
float ff_decay(float s);
int lettered(void *w);
int stride_of(void *s);
void titles(unsigned c);

#endif
