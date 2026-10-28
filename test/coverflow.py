#!/usr/bin/env python3
"""Run patch/coverflow.c's art cache on the host: the library query, the modal art build on a real
pthread, the stock art calls and their locks. Built 32-bit (-m32, like test/peq.py's player) so the
stSongInfo pointer offsets hold. The stock calls are stubbed; UI widgets are plain records."""
import ast
import pathlib
import re
import sqlite3
import subprocess
import tempfile
import sys; sys.path.insert(0, sys.path[0] + '/../tools')  # tools/ first
from build import FUNCTIONS, ROOT
from peq import LIBC

PROTOTYPES = {**FUNCTIONS, **LIBC}  # the stock calls the host shims stand in for

SHIM_H = r"""
#include <dirent.h>
#include <errno.h>
#include <pthread.h>
#include <sys/stat.h>
#include <sys/wait.h>
typedef int (*handler)(void *, void *);
extern pthread_mutex_t shim_parse, shim_play;
extern void *shim_dir;
#define parse_cover_mutex ((const unsigned char *)&shim_parse)
#define g_playcover_mutex ((const unsigned char *)&shim_play)
#define tools_pdeq_directory ((const unsigned char *)&shim_dir)
static unsigned char shim_artist_type[4] __attribute__((unused)); /* Artists: Artist */
#define artist_type shim_artist_type
#define pthread_mutex_lock shim_lock
#define pthread_mutex_unlock shim_unlock
#define statfs shim_statfs
#define tk_snprintf snprintf
extern void *shim_queue;
extern unsigned char shim_covertype;
extern char shim_lastcover[1024], shim_id3[2716];
#define mcl_pdeqplaylist ((const unsigned char *)&shim_queue)
#define g_lastcover_url ((const unsigned char *)shim_lastcover)
#define g_play_id3_info ((const unsigned char *)shim_id3)
#define g_playcover_type shim_covertype
extern unsigned char shim_memory[0x800];
#define g_memory_info shim_memory
int shim_lock(void *), shim_unlock(void *), shim_statfs(const char *, void *);
""" + ''.join(f'{r} {n}({a});\n' for n in """
getAllAlbum getMusicByAlbum toolsQueryDbTable album_row toolsThumbSpecCover toolsGetAlbumCover toolsGetExternCover _create_deque deque_init_copy deque_clear
deque_init _deque_push_back deque_pop_back
deque_assign deque_destroy deque_size deque_at window_create widget_factory
widget_factory_create_widget image_create hscroll_label_create set_hscroll_label_attribute
slide_menu_set_value slide_menu_item_width list_view_create scroll_view_create list_item_create image_set_draw_type
image_base_set_image widget_load_image widget_unload_image widget_set_name widget_use_style
widget_set_text_utf8 widget_restack widget_animator_prop_create widget_animator_prop_set_params widget_animator_start widget_set_visible widget_get_prop_int widget_set_prop_int
widget_get_prop_str widget_on widget_destroy_children widget_invalidate_force
widget_count_children widget_get_child widget_lookup widget_move_resize widget_get_visible
canvas_get_clip_rect canvas_set_clip_rect widget_set_sensitive widget_get_type tk_strcmp
timer_add timer_remove navigator_back_to_home navigator_back window_manager_close_window_force navigator_to_with_context bitmap_create_ex bitmap_destroy bitmap_unlock_buffer
bitmap_lock_buffer_for_read bitmap_lock_buffer_for_write bitmap_get_line_length
canvas_draw_image slide_menu_set_spacer window_manager
""".split() for r, a in [PROTOTYPES[n]]) + r"""
void *shim_calloc(size_t, size_t);
#define calloc shim_calloc
"""

TEST = r"""
#include <assert.h>
#include <stdarg.h>
#include <sys/mman.h>
#include "peq.h"
#include "offsets.inc"
#include <stdint.h>
#undef pthread_mutex_lock /* the stubs below record, then take the real lock */
#undef pthread_mutex_unlock
int coverflow_home(void *, void *), coverflow_player_cover(const char *, const char *, int, int);
const char *track_name(char *, unsigned, void *);

/* Widgets: raw memory first, so the payload's field reads (SLIDE_INDEX) land in it. */
typedef struct { char raw[0x100]; int parent, visible, kids[512], nkids, bg; char type[32], text[160], image[600];
                 handler click; void *ctx; } widget;
static widget w[8192];
static int nw;
static widget *W(void *p) { return (widget *)p; }
static void *make(void *parent, const char *type) {
    widget *x = &w[++nw];
    memset(x, 0, sizeof(*x));
    x->visible = 1;
    snprintf(x->type, sizeof(x->type), "%s", type);
    if (parent) { x->parent = (int)(W(parent) - w); W(parent)->kids[W(parent)->nkids++] = nw; *(void **)(x->raw + W_PARENT) = parent; }
    return x;
}
static void *placed(void *x, int left, int top, int ww, int h) {
    int *r = (int *)W(x)->raw; /* W_X, W_Y, W_W, W_H */
    r[0] = left; r[1] = top; r[2] = ww; r[3] = h;
    return x;
}
void *window_create(void *p, int x, int y, int ww, int h) { return make(0, "window"); }
void *widget_factory(void) { return (void *)1; }
void *widget_factory_create_widget(void *f, const char *t, void *p, int x, int y, int ww, int h) { return placed(make(p, t), x, y, ww, h); }
#define CREATE(name, type) void *name(void *p, int x, int y, int ww, int h) { return placed(make(p, type), x, y, ww, h); }
CREATE(image_create, "image") CREATE(list_view_create, "list_view")
CREATE(hscroll_label_create, "hscroll_label")
CREATE(scroll_view_create, "scroll_view") CREATE(list_item_create, "list_item")
int image_set_draw_type(void *x, int t) { return 0; }
int image_base_set_image(void *x, const char *s) { snprintf(W(x)->image, 600, "%s", s); return 0; }
/* A zero-length "no art" marker does not decode. Every successful load is unloaded again and
   decodes to art_w x art_h (bitmap_t w @0, h @4). */
static int loads, unloads, theme_placeholder;
static unsigned art_w = 160, art_h = 160;
/* Decoded art (bitmap_t w @0, h @4, line length @8, format @0xe, and here the pixels @0x14):
   RGBA8888, a pattern whose colour comes from the file's path, so every album looks different. */
static unsigned char pixels[16][160 * 160 * 4];
static int next_pixels, reads;
static void pattern(unsigned char *px, unsigned seed) {
    static const unsigned char hue[8][3] = { { 214, 68, 58 }, { 58, 140, 214 }, { 236, 180, 50 }, { 90, 176, 96 },
                                             { 150, 90, 200 }, { 230, 120, 170 }, { 60, 190, 190 }, { 200, 200, 200 } };
    const unsigned char *c = hue[seed % 8];
    for (int y = 0; y < 160; ++y)
        for (int x = 0; x < 160; ++x) {
            int dx = x - 80, dy = y - 80, ring = dx * dx + dy * dy, k = 120 + (x + y) * 135 / 318;
            if (ring < 30 * 30 && ring > 22 * 22) k = 40; /* a ring, off-centre text-like bars */
            if (y > 118 && y < 128 && x > 20 && x < 20 + (int)(seed * 37 % 100)) k = 255;
            if (x < 4 || y < 4 || x > 155 || y > 155) k = 255 - k / 2;
            for (int i = 0; i < 3; ++i) px[4 * (y * 160 + x) + i] = c[i] * k / 255;
            px[4 * (y * 160 + x) + 3] = 255;
        }
}
int widget_load_image(void *x, const char *url, void *b) {
    struct stat s;
    int themed = theme_placeholder && strncmp(url, "file://", 7);
    int failed = !themed && (strncmp(url, "file://", 7) || stat(url + 7, &s) || !s.st_size);
    loads += !failed && !themed;
    if (!failed) {
        unsigned *bm = b, seed = 0;
        for (const char *c = strrchr(url, '/') ? strrchr(url, '/') : url; *c; ++c) seed = seed * 31 + (unsigned char)*c; /* the key, not the scratch path */
        unsigned char *px = pixels[next_pixels++ % 16];
        pattern(px, seed % 1000);
        if (themed) memset(px, 0, sizeof pixels[0]); /* transparent theme placeholder */
        bm[0] = art_w, bm[1] = art_h, bm[2] = art_w * 4;
        ((unsigned short *)b)[7] = 1; /* RGBA8888 */
        *(unsigned char **)((char *)b + 0x14) = px;
    }
    return failed;
}
const unsigned char *bitmap_lock_buffer_for_read(void *b) { ++reads; return *(unsigned char **)((char *)b + 0x14); }
int widget_unload_image(void *x, void *b) { ++unloads; return 0; }
void set_hscroll_label_attribute(void *x) { assert(!strcmp(W(x)->type, "hscroll_label")); }
int slide_menu_set_value(void *x, int value) { *(int *)(W(x)->raw + SLIDE_INDEX) = value; return 0; }
int widget_set_name(void *x, const char *s) { snprintf(W(x)->type, 32, "%s", s); return 0; }
int widget_use_style(void *x, const char *s) { return 0; }
int widget_set_text_utf8(void *x, const char *s) { snprintf(W(x)->text, 160, "%s", s); return 0; }
int widget_restack(void *x, unsigned i) { return 0; }
void *widget_animator_prop_create(void *x, unsigned d, unsigned l, int e, const char *p) { return 0; }
int widget_animator_prop_set_params(void *a, double f, double t) { return 0; }
int widget_animator_start(void *a) { return 0; }
int widget_set_visible(void *x, int v, int r) { W(x)->visible = v; return 0; }
int widget_get_prop_int(void *x, const char *k, int d) { return d; }
int widget_set_prop_int(void *x, const char *k, int v) { if (!strcmp(k, "style:normal:bg_color")) W(x)->bg = v; return 0; }
const char *widget_get_prop_str(void *x, const char *k, const char *d) { return strcmp(k, "image") ? d : W(x)->image; }
unsigned widget_on(void *x, unsigned type, handler f, void *ctx) {
    if (!x) return 0;
    if (type == EVT_CLICK || type == EVT_KEY_UP || type == EVT_DESTROY) {
        widget *h = type == EVT_CLICK ? W(x) : &w[0] + (type == EVT_KEY_UP ? 8190 : 8191); /* page handlers */
        h->click = f; h->ctx = ctx;
    }
    return 1;
}
int widget_destroy_children(void *x) { W(x)->nkids = 0; return 0; }
static int wm_repaints; /* whole-screen invalidations, from outside a paint */
void *window_manager(void) { return &wm_repaints; }
int widget_invalidate_force(void *x, void *y) { wm_repaints += x == &wm_repaints; return 0; }
unsigned widget_count_children(void *x) { return x == &wm_repaints ? 0 : W(x)->nkids; } /* no window stack */
void *widget_get_child(void *x, unsigned i) { return &w[W(x)->kids[i]]; }
static void *home_art, *home_list; /* iPod Home's art and list; none in the carousel tests */
void *widget_lookup(void *x, const char *n, int r) {
    return !x ? 0 : !strcmp(n, "img_coverflow") ? x : !strcmp(n, "img_homeart") ? home_art :
           !strcmp(n, "list_view_home") ? home_list : 0;
}
static int home_full, classic, light;
int ipod_home_full(void) { return home_full; }
int ipod_classic(void) { return classic; }
int ipod_light(void) { return light; }
int ipod_home_rockbox(void) { return 0; }
const char *ipod_home_shortcut(void) { return "Rockbox"; }
int home_shortcut_open(void *x, void *e) { return 0; }
int widget_move_resize(void *x, int left, int top, int ww, int h) {
    int *r = (int *)W(x)->raw; /* W_X, W_Y, W_W, W_H */
    r[0] = left; r[1] = top; r[2] = ww; r[3] = h;
    return 0;
}
static int insensitive; /* the widget last made insensitive */
int widget_set_sensitive(void *x, int v) { if (!v) insensitive = W(x) - w; return 0; }
static int clip_rect[4] = { 0, 0, 375, 320 }; /* the canvas clip, screen x, y, w, h */
int canvas_get_clip_rect(void *c, void *r) { memcpy(r, clip_rect, sizeof clip_rect); return 0; }
int canvas_set_clip_rect(void *c, const void *r) { memcpy(clip_rect, r, sizeof clip_rect); return 0; }
const char *widget_get_type(void *x) { return W(x)->type; }
int widget_get_visible(void *x) { return W(x)->visible; }
int tk_strcmp(const char *a, const char *b) { return strcmp(a ? a : "", b ? b : ""); }
int navigator_back_to_home(void) { return 0; }
int navigator_back(void) { return 0; }
int window_manager_close_window_force(void *m, void *x) { return 0; }
int navigator_to_with_context(const char *n, const void *c) { return 0; }
/* slide_menu: square items as high as the menu, plus the spacer. */
int slide_menu_item_width(void *x) { return *(int *)(W(x)->raw + W_H); }
int slide_menu_set_spacer(void *x, int v) { *(int *)(W(x)->raw + SLIDE_SPACER) = v; return 0; }
/* The frame bitmap (bitmap_t as above) and the canvas: canvas_draw_image keeps what it drew. */
static int frames, frame_fail, tex_fail, locks, draws, drawn[4];
static unsigned shown[CF_VIEW_H * CF_VIEW_W];
#undef calloc /* the payload's calloc is this shim; the tests' own is libc's */
void *calloc(size_t, size_t);
void *shim_calloc(size_t n, size_t size) { return tex_fail && size == 160 * 160 * 4 ? 0 : calloc(n, size); }
void *bitmap_create_ex(unsigned ww, unsigned h, unsigned line, unsigned format) {
    if (frame_fail) return 0;
    unsigned *b = calloc(1, 0x48);
    b[0] = ww, b[1] = h, b[2] = line, ((unsigned short *)b)[7] = format;
    *(unsigned char **)((char *)b + 0x14) = calloc(h, line);
    ++frames;
    return b;
}
int bitmap_destroy(void *b) { free(*(void **)((char *)b + 0x14)); free(b); --frames; return 0; }
unsigned char *bitmap_lock_buffer_for_write(void *b) { ++locks; return *(unsigned char **)((char *)b + 0x14); }
int bitmap_unlock_buffer(void *b) { return 0; }
unsigned bitmap_get_line_length(void *b) { return ((unsigned *)b)[2]; }
int canvas_draw_image(void *c, void *b, const void *src, const void *dst) {
    ++draws;
    const int *r = dst, *q = src;
    assert(!memcmp(r, q, 16) && ((unsigned short *)b)[6] & 1); /* 1:1, and marked opaque */
    memcpy(drawn, r, sizeof drawn);
    for (int y = 0; y < CF_VIEW_H; ++y) memcpy(shown + y * CF_VIEW_W, *(unsigned char **)((char *)b + 0x14) + y * ((unsigned *)b)[2], CF_VIEW_W * 4);
    return 0;
}
int stock_home_trampoline(void *win, void *ctx) { return 0; }
/* The songtable writers' stock bodies; during_write runs inside one, as an open mid-scan would. */
static void (*during_write)(void);
static int stock_write(void) { if (during_write) during_write(); return 0; }
int stock_scan_all_trampoline(void *a, void *b) { return stock_write(); }
int stock_scan_folder_trampoline(void *a, void *b) { return stock_write(); }
int stock_delete_song_trampoline(void *a, void *b) { return stock_write(); }
int coverflow_scan_all(void *, void *);
static void rescan(void) { coverflow_scan_all(0, 0); } /* the library changed, as only a scan changes it */

static int (*timer_fn)(const void *);
unsigned timer_add(int (*f)(const void *), void *ctx, unsigned ms) { timer_fn = f; return 1; }
int timer_remove(unsigned id) { timer_fn = 0; return 0; }
void stop_timer(unsigned *t) { if (*t) timer_remove(*t); *t = 0; } /* navigation.c's */
void rearm(unsigned *t, int (*f)(const void *), unsigned ms) { stop_timer(t); *t = timer_add(f, 0, ms); }
static void run(void) { while (timer_fn) { int (*f)(const void *) = timer_fn; timer_fn = 0; usleep(1000); f(0); } }

/* libcstl: a deque of record pointers; the query stubs fill the staging deque. */
typedef struct { unsigned n; void *at[64]; } deque;
void *shim_dir;
void *_create_deque(const char *t) { return calloc(1, sizeof(deque)); }
void deque_init_copy(void *d, const void *s) { *(deque *)d = *(const deque *)s; }
void deque_assign(void *d, const void *s) { *(deque *)d = *(const deque *)s; }
void deque_clear(void *d) { ((deque *)d)->n = 0; }
void deque_init(void *d) { ((deque *)d)->n = 0; }
void _deque_push_back(void *d, ...) {
    va_list a;
    va_start(a, d);
    ((deque *)d)->at[((deque *)d)->n++] = va_arg(a, void *);
    va_end(a);
}
void deque_destroy(void *d) { free(d); }
void deque_pop_back(void *d) { --((deque *)d)->n; }
unsigned deque_size(const void *d) { return ((const deque *)d)->n; }
void *deque_at(const void *d, unsigned i) { return i < ((const deque *)d)->n ? ((const deque *)d)->at[i] : 0; }

static char records[64][0x60], names[64][64], paths[64][600];
static int albums, queries;
static void album(const char *name, const char *art) {
    char *r = records[albums], dir[500];
    snprintf(names[albums], 64, "%s", name);
    snprintf(dir, sizeof(dir), PEQ_ROOT "/music/%s", name);
    mkdir(dir, 0755);
    snprintf(paths[albums], 600, "%s/01.flac", dir);
    FILE *f = fopen(paths[albums], "w"); fclose(f);
    if (art) { snprintf(dir + strlen(dir), 100, "/%s", art); f = fopen(dir, "w"); fclose(f); }
    memset(r, 0, 0x60);
    *(int *)(r + REC_ID) = albums + 1;
    *(char **)(r + REC_ALBUM) = names[albums];
    *(char **)(r + REC_ARTIST) = "Artist";
    *(char **)(r + REC_PATH) = paths[albums++];
}
/* Stock groups albums by name alone, and appends an "Unknown Album" row (id -1) whenever any album
   exists; unknown_songs is what its press, getMusicByAlbum(NULL), finds. */
static int unknown_row, unknown_songs;
static char unknown_record[0x60], untagged_song[0x60];
int getAllAlbum(void) {
    deque *d = shim_dir;
    ++queries;
    d->n = 0;
    for (int i = 0, j; i < albums; ++i) {
        for (j = 0; j < i && strcasecmp(names[j], names[i]); ++j) {}
        if (j == i) d->at[d->n++] = records[i];
    }
    if (unknown_row && albums) {
        *(int *)(unknown_record + REC_ID) = -1;
        *(const char **)(unknown_record + REC_ALBUM) = "Unknown Album";
        *(const char **)(unknown_record + REC_ARTIST) = "Unknown Artist";
        d->at[d->n++] = unknown_record;
    }
    return albums;
}
static int music_only;
extern void *staged(int (*query)(void *), void *arg, int *count);
void *staged_music(int (*query)(void *), void *arg, int *count) {
    music_only = 1;
    void *out = staged(query, arg, count);
    music_only = 0;
    return out;
}
static int spoken_path(const char *path) {
    return !strncasecmp(path, "/mnt/mmc/audiobooks/", 20) ||
           !strncasecmp(path, "/mnt/mmc/podcasts/", 18) ||
           (strlen(path) >= 4 && !strcasecmp(path + strlen(path) - 4, ".m4b"));
}
static int unknown_queries;
int getMusicByAlbum(const char *a) {
    deque *d = shim_dir;
    d->n = 0;
    if (!a) {
        ++unknown_queries;
        if (unknown_songs) d->at[d->n++] = untagged_song;
    } else
        for (int i = 0; i < albums; ++i)
            if (!strcasecmp(names[i], a) && (!music_only || !spoken_path(paths[i]))) d->at[d->n++] = records[i]; /* each album's one song: its record */
    return (int)d->n;
}

/* The albums told apart (a record each), and Sort's ranking queries over that grouping in another
   order, here the test's own order by name; a row may differ in case from the stock one, as the
   grouping allows. */
static const char *ranked[16], *rank_sql;
static char rank_rows[16][0x60];
/* navigation.c's Year order: newest first (0) or oldest. */
static int year_old;
int ringnav_year_old(void) { return year_old; }
static int artist_view;
int ringnav_artist_view(void) { return artist_view; }
int ringnav_artist_az(void) { return 0; }
int album_row(void *a, int n, char **v, char **c) { return 0; }
int toolsQueryDbTable(const char *db, const char *sql, void *row, int sort) {
    assert(!strcmp(db, "/mnt/data/database.db") && !sort && strstr(sql, "group by album COLLATE NOCASE,ifnull(albumsonger"));
    deque *d = shim_dir;
    d->n = 0;
    if (!strstr(sql, " order by ")) {
        int music = strstr(sql, "fileurl not like") != 0;
        for (int i = 0; i < albums; ++i) {
            const char *path = *(const char **)(records[i] + REC_PATH);
            if (music && (!strncasecmp(path, "/mnt/mmc/audiobooks/", 20) ||
                          !strncasecmp(path, "/mnt/mmc/podcasts/", 18) ||
                          (strlen(path) >= 4 && !strcasecmp(path + strlen(path) - 4, ".m4b")))) continue;
            d->at[d->n++] = records[i];
        }
        if (music && unknown_songs) d->at[d->n++] = unknown_record;
        return (int)d->n;
    }
    assert(strstr(sql, "time_create") || strstr(sql, "year"));
    rank_sql = sql;
    for (int i = 0; ranked[i]; ++i) {
        memset(rank_rows[i], 0, 0x60);
        for (int j = 0; j < albums; ++j)
            if (!strcasecmp(names[j], ranked[i])) memcpy(rank_rows[i], records[j], 0x60);
        *(const char **)(rank_rows[i] + REC_ALBUM) = ranked[i];
        d->at[d->n++] = rank_rows[i];
    }
    ++queries;
    return (int)d->n;
}
/* navigation.c's: Most Played's counts, per stock record here, and the library lists' article. */
static unsigned played[64];
/* playback.c's generator, falling: Random shows the stock order reversed. */
static unsigned dice = 1000;
unsigned rand_below(unsigned n) { return dice-- % n; }
void album_plays(int (*album_of)(void *), unsigned *sum) {
    for (int i = 0; i < albums; ++i) {
        int a = played[i] ? album_of(records[i]) : -1;
        if (a >= 0) sum[a] += played[i];
    }
}
void ringnav_sort_key(char *s) {
    if (!strncasecmp(s, "the ", 4)) memmove(s, s + 4, strlen(s + 4) + 1);
}

/* The stock art calls: each writes its source into dst, and checks the locks it runs under. */
pthread_mutex_t shim_parse = PTHREAD_MUTEX_INITIALIZER, shim_play = PTHREAD_MUTEX_INITIALIZER;
static int held[2], order[64], norder, calls, embedded_fails, block_at = -1, blocked;
static char made[64][600];
int shim_lock(void *m) {
    order[norder++] = m == &shim_parse ? 1 : 2;
    assert(!held[m == &shim_play]);
    held[m == &shim_play] = 1;
    return pthread_mutex_lock(m);
}
int shim_unlock(void *m) { held[m == &shim_play] = 0; return pthread_mutex_unlock(m); }
static int write_art(const char *dst, const char *what) {
    snprintf(made[calls++], 600, "%s", what);
    if (calls - 1 == block_at) { blocked = 1; while (blocked) usleep(1000); }
    FILE *f = fopen(dst, "w");
    fputs(what, f);
    return !fclose(f);
}
int toolsThumbSpecCover(const char *src, const char *dst, int ww, int h) {
    assert(ww == 160 && h == 160 && held[0] && !held[1] && strstr(dst, ".jpg.tmp"));
    if (strstr(src, ".webp")) return 0; /* stock decodes only JPEG and PNG */
    return write_art(dst, strrchr(src, '/') + 1);
}
int toolsGetAlbumCover(const char *src, const char *dst, int ww, int h) {
    assert(ww == 160 && h == 160 && held[0] && held[1] && strstr(dst, ".jpg.tmp"));
    return embedded_fails ? 0 : write_art(dst, "embedded");
}
static int extern_found;
int toolsGetExternCover(const char *src, const char *dst, int ww, int h) {
    assert(ww == 320 && h == 320 && !held[0] && !held[1]);
    return extern_found ? write_art(dst, "extern") : 0;
}
static unsigned free_blocks = 1 << 20;
int shim_statfs(const char *p, void *out) {
    assert(!strcmp(p, PEQ_ROOT "/mnt/mmc/.coverflow"));
    unsigned *s = out;
    s[1] = 4096; s[7] = free_blocks; /* MIPS o32 statfs: f_bsize, f_bavail */
    return 0;
}

void *shim_queue;
unsigned char shim_memory[0x800]; /* g_memory_info, which album_memory fills */
unsigned char shim_covertype;
char shim_lastcover[1024], shim_id3[2716];
static widget *page;
static void *release(void *unused) { usleep(50000); blocked = 0; return 0; }
static void open_page(void) {
    widget home;
    memset(&home, 0, sizeof(home));
    coverflow_home(&home, 0);
    page = &w[nw + 1];
    home.click(home.ctx, 0);
    run();
}
static void key(int k) { int e[16] = {0}; e[EVENT_KEY / 4] = k; w[8190].click(0, e); run(); }
static void close_page(void) { w[8191].click(w[8191].ctx, 0); }
static void mid_write(void) { open_page(); close_page(); }
unsigned fnv(unsigned h, const unsigned char *s);
static int last_album(int i) { /* the card holds album i's key as the one to reopen on */
    unsigned key = 0;
    FILE *f = fopen(PEQ_ROOT "/mnt/mmc/.coverflow/album", "rb");
    int ok = f && fread(&key, sizeof(key), 1, f) == 1;
    if (f) fclose(f);
    return ok && key == fnv(fnv(FNV_SEED, (const unsigned char *)"Artist"), (const unsigned char *)names[i]);
}
static widget *slide(void) {
    for (int i = nw; i > page - w; --i) if (!strcmp(w[i].type, "slide_menu")) return &w[i];
    return 0;
}
static const char *title(void) {
    for (int i = nw; i > page - w; --i) if (!strcmp(w[i].type, "hscroll_label") && w[w[i].parent].parent == page - w) return w[i].text;
    return "";
}
/* The cache file of Artist's album_name. */
static char *art_file(char *path, const char *album_name) {
    unsigned h = 2166136261u;
    for (const unsigned char *s = (const unsigned char *)"Artist"; *s; ++s) h = (h ^ *s) * 16777619u;
    h *= 16777619u;
    for (const unsigned char *s = (const unsigned char *)album_name; *s; ++s) h = (h ^ *s) * 16777619u;
    h *= 16777619u;
    snprintf(path, 600, PEQ_ROOT "/mnt/mmc/.coverflow/%08x.jpg", h);
    return path;
}
static long size(const char *album_name) {
    char path[600];
    for (int i = 0; i < albums; ++i) {
        if (strcmp(names[i], album_name)) continue;
        art_file(path, names[i]);
        struct stat s;
        return stat(path, &s) ? -1 : s.st_size;
    }
    return -2;
}
static int tmp_files(void) {
    int n = 0;
    DIR *d = opendir(PEQ_ROOT "/mnt/mmc/.coverflow");
    for (struct dirent *e; d && (e = readdir(d));) n += strstr(e->d_name, ".tmp") != 0;
    if (d) closedir(d);
    return n;
}


/* ---- Depth: the renderer on its own, then on the page. ---- */
void coverflow_render(unsigned *, int, int, const unsigned *const[7]);
void coverflow_paint(void *, void *);
#define ONE 65536
#define PITCH (CF_VIEW_W + 5) /* guard columns, and guard rows below, to catch clipping errors */
static unsigned frame[(CF_VIEW_H + 3) * PITCH], flat[7][160 * 160], art[160 * 160];
static const unsigned colours[7] = { 0xff0000ffu, 0xff00ff00u, 0xffff0000u, 0xff00ffffu, 0xffffff00u, 0xffff00ffu, 0xff0080ffu };
static void render(int frac, const unsigned *const ring[7]) {
    for (unsigned i = 0; i < sizeof(frame) / 4; ++i) frame[i] = 0x12345678u;
    coverflow_render(frame, PITCH, frac, ring);
    for (int y = 0; y < CF_VIEW_H + 3; ++y)
        for (int x = 0; x < PITCH; ++x)
            assert((y < CF_VIEW_H && x < CF_VIEW_W) == (frame[y * PITCH + x] != 0x12345678u)); /* all of it, and only it */
}
static unsigned px(int x, int y) { return frame[y * PITCH + x]; }
static int ch(unsigned p, int i) { return (int)(p >> 8 * i & 255); }
/* The slot whose flat colour p is a shade of (by channel proportions), or -1. */
static int slot_of(unsigned p) {
    int m = ch(p, 0) > ch(p, 1) ? ch(p, 0) : ch(p, 1);
    m = m > ch(p, 2) ? m : ch(p, 2);
    for (int j = 0; m >= 24 && j < 7; ++j) {
        int ok = 1;
        for (int i = 0; i < 3; ++i) { int want = ch(colours[j], i) * m / 255; ok &= abs(ch(p, i) - want) <= 3 + m / 40; }
        if (ok) return j;
    }
    return -1;
}
static int differ(const unsigned *a, const unsigned *b, int tolerance) {
    int n = 0;
    for (int y = 0; y < CF_VIEW_H; ++y)
        for (int x = 0; x < CF_VIEW_W; ++x)
            for (int i = 0; i < 3; ++i) if (abs(ch(a[y * PITCH + x], i) - ch(b[y * PITCH + x], i)) > tolerance) { ++n; break; }
    return n;
}
static void capture(const char *name) {
    const char *dir = getenv("CF_CAPTURES");
    if (!dir) return;
    char path[600];
    snprintf(path, sizeof(path), "%s/%s.rgba", dir, name);
    FILE *f = fopen(path, "wb");
    fwrite(shown, 4, CF_VIEW_H * CF_VIEW_W, f);
    fclose(f);
}

static void renderer(void) {
    const unsigned *ring[7], *mirror[7];
    for (int j = 0; j < 7; ++j) {
        for (int i = 0; i < 160 * 160; ++i) flat[j][i] = colours[j];
        ring[j] = flat[j];
    }
    for (int i = 0; i < 160 * 160; ++i) art[i] = 0xff000000u | (unsigned)(i * 2654435761u >> 8 & 0xffffff);
    /* At rest the selected cover is the texture itself, 160 px square at CF_TOP, centred, full
       brightness; the neighbours at CF_BRIGHT1, then CF_BRIGHT2; the third out is not drawn. */
    const unsigned *rest[7] = { flat[0], flat[1], flat[2], art, flat[4], flat[5], flat[6] };
    render(0, rest);
    for (int y = 0; y < 160; ++y)
        for (int x = 0; x < 160; ++x)
            if (!IPOD || (x >= 12 && x < 148) || (y >= 12 && y < 148))
                assert(px(CF_CX - 80 + x, CF_TOP + y) == art[y * 160 + x]);
    assert(px(CF_CX - 81, CF_TOP + 80) != art[80 * 160] && px(CF_CX, CF_TOP - 1) == 0xff000000u);
    /* Each neighbour's visible run on the horizon, sampled in its middle: its colour at its
       brightness, two a side, each run a real slice of cover. */
    for (int j = -2; j <= 2; ++j) {
        if (!j) continue;
        int first = -1, last = -1;
        for (int x = 0; x < CF_VIEW_W; ++x)
            if (slot_of(px(x, CF_TOP + 80)) == j + 3) last = x, first = first < 0 ? x : first;
        assert(last - first >= (abs(j) == 1 ? 40 : 18));
        unsigned p = px((first + last) / 2, CF_TOP + 80), want = colours[j + 3];
        int b = abs(j) == 1 ? CF_BRIGHT1 : CF_BRIGHT2;
        for (int i = 0; i < 3; ++i) assert(abs(ch(p, i) - ch(want, i) * b / 256) <= 1);
    }
    static unsigned without[(CF_VIEW_H + 3) * PITCH];
    memcpy(without, frame, sizeof(frame));
    const unsigned *inner[7] = { 0, flat[1], flat[2], art, flat[4], flat[5], 0 };
    render(0, inner);
    assert(!memcmp(without, frame, sizeof(frame))); /* the third out is not drawn at rest */
    /* Everything, reflections included, fits the frame with room to spare. */
    for (int x = 0; x < CF_VIEW_W; ++x) assert(!(px(x, 0) & 0xffffff) && !(px(x, CF_VIEW_H - 1) & 0xffffff));
#if IPOD
    unsigned centre = px(CF_CX, CF_TOP + 80); /* Light Mode: the same covers over paper */
    light = 1;
    render(0, inner);
    assert(px(0, 0) == (0xff000000u | LIGHT_PAPER * 0x010101u) && px(CF_CX, CF_TOP + 80) == centre);
    light = 0;
    render(0, inner);
#endif
    /* The reflection: CF_REFLECT rows under the cover, from CF_REFLECT_TOP of its brightness down
       to black, never brighter going down; nothing after it. */
    render(0, ring);
    int prev = 256, rows = 0;
    for (int y = CF_TOP + 160; y < CF_VIEW_H; ++y) {
        int v = ch(px(CF_CX, y), 0); /* slot 3 is 0xff00ffff: red and green */
        assert(v <= prev);
        if (y == CF_TOP + 160) assert(abs(v - 255 * CF_REFLECT_TOP / 256) <= 3);
        rows += v > 0, prev = v;
    }
    assert(rows >= CF_REFLECT - 1 && rows <= CF_REFLECT);
    /* No seam where a body ends mid-row: down any column from a cover's middle, brightness never
       dips between the body, the row they share and the reflection. */
    const unsigned *alone[7] = { 0, 0, 0, flat[3], 0, 0, 0 };
    render(0, alone);
    assert(px(CF_CX, CF_TOP + 80) == colours[3]);
    assert(px(CF_CX - 80, CF_TOP) == (IPOD ? 0xff000000u : colours[3]));
    assert(px(CF_CX - 80, CF_TOP + 160) == (IPOD ? 0xff000000u : px(CF_CX, CF_TOP + 160)));
    for (int frac = -ONE / 2; frac < ONE / 2; frac += ONE / 16 + 77) {
        render(frac, alone);
        for (int x = 0; x < CF_VIEW_W; ++x) {
            if (slot_of(px(x, CF_TOP + 80)) != 3) continue;
            if (IPOD && x != CF_CX) continue; /* rounded edges leave a gap before their reflection */
            int body = 256, dipped = 0;
            for (int y = CF_TOP + 80; y < CF_VIEW_H; ++y) {
                int v = ch(px(x, y), 0);
                dipped |= v < body && v < ch(px(x, y + 1 < CF_VIEW_H ? y + 1 : y), 0) - 2;
                body = v;
            }
            assert(!dipped);
        }
    }
    /* Symmetry: a mirror-image ring renders the mirror image, at rest and mid-turn either way. */
    for (int j = 0; j < 7; ++j) mirror[j] = flat[j < 3 ? j : 6 - j];
    render(0, mirror);
    for (int y = 0; y < CF_VIEW_H; ++y)
        for (int x = 0; x < 2 * CF_CX; ++x) assert(px(x, y) == px(2 * CF_CX - 1 - x, y));
    static unsigned left[(CF_VIEW_H + 3) * PITCH];
    const unsigned *swapped[7];
    for (int j = 0; j < 7; ++j) swapped[j] = ring[6 - j];
    for (int frac = ONE / 8; frac < ONE / 2; frac += ONE / 8) {
        render(-frac, swapped);
        memcpy(left, frame, sizeof(frame));
        render(frac, ring);
        for (int y = 0; y < CF_VIEW_H; ++y)
            for (int x = 0; x < 2 * CF_CX; ++x) assert(px(x, y) == left[y * PITCH + 2 * CF_CX - 1 - x]);
    }
    /* Continuity: covers move, turn and fade from one continuous position, so a small step changes
       only a little, including where the centre changes hands (frac -1/2 after c, +1/2 before). */
    static unsigned a[(CF_VIEW_H + 3) * PITCH];
    const unsigned *next[7];
    for (int j = 0; j < 7; ++j) next[j] = ring[(j + 1) % 7];
    render(ONE / 2 - 1, ring);
    memcpy(a, frame, sizeof(frame));
    render(-ONE / 2, next);
    assert(differ(a, frame, 6) < 200);
    int worst = 0;
    for (int f = -ONE / 2; f + ONE / 256 < ONE / 2; f += ONE / 256) {
        render(f, ring);
        memcpy(a, frame, sizeof(frame));
        render(f + ONE / 256, ring);
        int d = differ(a, frame, 24);
        worst = d > worst ? d : worst;
    }
    assert(worst < CF_VIEW_W * CF_VIEW_H / 50);
}

static void depth(void) {
    renderer();
    /* On the page: the slide_menu spans the frame, one album per CF_STRIDE px, with empty children
       under the drawn covers; the captions sit under the frame in both builds. */
    frame_fail = 0;
    open_page();
    widget *s = slide();
    char *raw = s->raw;
    int *geo = (int *)raw;
    assert(frames == 1 && geo[0] == 0 && geo[1] == 0 && geo[2] == CF_VIEW_W && geo[3] == CF_VIEW_H);
    assert(s == &w[insensitive]); /* wheel only: the covers take no touch */
    assert(slide_menu_item_width(s) + *(int *)(raw + SLIDE_SPACER) == CF_STRIDE);
    for (int i = 0; i < s->nkids; ++i) assert(!w[s->kids[i]].image[0]);
    widget *covers_view = &w[s->parent];
    int labels = 0;
    for (int i = 0; i < covers_view->nkids; ++i) {
        widget *l = &w[covers_view->kids[i]];
        if (strcmp(l->type, "hscroll_label")) continue;
        int *g = (int *)l->raw;
        assert(g[0] == CF_EDGE && g[2] == 375 - 2 * CF_EDGE && g[1] == CF_TEXT_Y + labels * CF_NAME_H && g[1] >= CF_VIEW_H);
        ++labels;
    }
    assert(labels == 2);
    /* Paint: the seven covers around the position decoded (each load released at once; albums 2
       and 3, Embedded and None, have only the empty marker and show the placeholder), one render,
       drawn 1:1 at the slide_menu's origin. A repaint in place only draws again. */
    int canvas[16] = { 0 };
    *(int *)(raw + SLIDE_INDEX) = 3; *(int *)(raw + SLIDE_OFFSET) = 0;
    loads = unloads = 0;
    coverflow_paint(s, canvas);
    assert(draws == 1 && locks == 1 && drawn[0] == 0 && drawn[1] == 0 && drawn[2] == CF_VIEW_W && drawn[3] == CF_VIEW_H);
    assert(loads == 5 && unloads == 5 && reads == 5);
    coverflow_paint(s, canvas);
    coverflow_paint(covers_view, canvas); /* other widgets: nothing */
    assert(draws == 2 && locks == 1 && loads == 5);
    capture("rest");
    /* Turning a quarter, a half and three quarters towards the next album draws from the live
       offset without loading; crossing into the next album loads just the one entering reach. */
    static const char *turns[] = { "quarter", "half", "three-quarter" };
    for (int q = 1; q < 4; ++q) {
        *(int *)(raw + SLIDE_OFFSET) = -CF_STRIDE * q / 4;
        coverflow_paint(s, canvas);
        assert(locks == 1 + q && loads == 5 + (q >= 2)); /* past half, album 7 comes into reach */
        capture(turns[q - 1]);
    }
    *(int *)(raw + SLIDE_OFFSET) = 0; *(int *)(raw + SLIDE_INDEX) = 4;
    coverflow_paint(s, canvas);
    assert(loads == 6 && unloads == 6);
    /* The wheel's centre press clicks the centre child, which opens its tracks. */
    *(int *)(raw + SLIDE_INDEX) = 6;
    int (*waiting)(const void *) = timer_fn;
    w[s->kids[6]].click(w[s->kids[6]].ctx, 0);
    assert(timer_fn != waiting);
    run();
    assert(!strcmp(title(), names[6]) && !covers_view->visible && last_album(6)); /* kept for a reboot */
    key(KEY_RETURN); /* back to the same album, still drawn */
    assert(covers_view->visible && *(int *)(raw + SLIDE_INDEX) == 6);
    coverflow_paint(s, canvas);
#if IPOD
    /* Auto Light Mode refreshes a stationary frame and its composited placeholder in RAM. */
    *(int *)(raw + SLIDE_INDEX) = 3;
    theme_placeholder = 1;
    for (int mode = 1; mode >= 0; --mode) {
        light = mode;
        int before = locks;
        coverflow_paint(s, canvas);
        unsigned paper = 0xff000000u | (mode ? LIGHT_PAPER * 0x010101u : 0);
        assert(shown[0] == paper && shown[(CF_TOP + 80) * CF_VIEW_W + CF_CX] == paper);
        assert(locks == before + 1 && frames == 1);
        before = locks;
        int decoded = loads;
        coverflow_paint(s, canvas);
        assert(locks == before && loads == decoded); /* no work on the next unchanged paint */
    }
    theme_placeholder = 0;
#endif
    /* Refresh and close release the frame and every texture. */
    close_page();
    assert(frames == 0 && loads == unloads);
    /* Small libraries: two albums wrap round the ring (no Sort or Refresh card: Display settings' rows
       now), each album decoded once however often it repeats; missing art shows the placeholder. */
    int keep = albums;
    albums = 2; rescan();
    open_page();
    s = slide(); raw = s->raw;
    loads = unloads = 0;
    coverflow_paint(s, canvas);
    assert(s->nkids == 2 && loads <= 2 && loads == unloads);
    render(0, (const unsigned *const[7]){ 0 }); /* nothing to draw: all black */
    for (int i = 0; i < CF_VIEW_W; ++i) assert(px(i, CF_TOP + 80) == 0xff000000u);
    close_page();
    albums = 1; rescan();
    open_page(); s = slide();
    coverflow_paint(s, canvas);
    assert(s->nkids == 1);
    capture("one-album");
    close_page();
    albums = keep; rescan();
    /* Allocation failure: the flat covers, as before. */
    tex_fail = 1;
    open_page(); s = slide();
    int flat_images = 0;
    for (int i = 0; i < s->nkids; ++i) flat_images += !strncmp(w[s->kids[i]].image, "file://", 7);
    assert(frames == 0 && ((int *)s->raw)[1] == 24 && ((int *)s->raw)[3] == 160 && flat_images > 0);
    for (int i = 0; i < s->nkids; ++i) assert(w[s->kids[i]].image[0]); /* art or the placeholder, as before */
    int before = draws;
    coverflow_paint(s, canvas);
    assert(draws == before);
    close_page();
    assert(loads == unloads);
    tex_fail = 0;
}

/* Display settings' Coverflow Sort (coverflow_sort_step): each press takes the next order, saved and
   taken at the next open; ties keep the stock order. */
static const char *album_at(int i) {
    static char got[160];
    widget *s = slide();
    s->kids[i] && (w[s->kids[i]].click(w[s->kids[i]].ctx, 0), 0);
    run();
    snprintf(got, sizeof got, "%s", title());
    key(KEY_RETURN);
    return got;
}
static void order_is(const char *const *want) {
    for (int i = 0; want[i]; ++i) assert(!strcmp(album_at(i), want[i]));
}
static int saved_sort(void) {
    int v = -1;
    FILE *f = fopen(PEQ_ROOT "/mnt/data/ringnav-coversort", "rb");
    if (f) assert(fread(&v, sizeof v, 1, f) == 1), fclose(f);
    return v;
}
extern void coverflow_sort_step(void), coverflow_refresh_art(void);
static void press_sort(void) {
    coverflow_sort_step();
    close_page();
    open_page();
}
extern int coverflow_jump(void *, int, int, unsigned *);

static void sorting(void) {
    mkdir(PEQ_ROOT "/mnt/data", 0755);
    /* Cover Folder Embedded None New A B C Tight; B is The Beatles', Tight Abba's. */
    assert(albums == 9 && !strcmp(names[8], "Tight"));
    *(const char **)(records[6] + REC_ARTIST) = "The Beatles";
    *(const char **)(records[8] + REC_ARTIST) = "Abba";
    rescan();
    open_page();
    widget *s = slide();
    assert(s->nkids == albums);
    order_is((const char *const[]){ "Cover", "Folder", "Embedded", "None", "New", "A", "B", "C", "Tight", 0 });
    int q = queries;
    /* Artist, articles aside (The Beatles under B), then year (the query: None, Folder), then stock. */
    ranked[0] = "none", ranked[1] = "Folder", ranked[2] = 0;
    press_sort();
    assert(saved_sort() == 1 && queries == q + 1); /* only the ranking is queried */
    order_is((const char *const[]){ "Tight", "None", "Folder", "Cover", "Embedded", "New", "A", "C", "B", 0 });
    unsigned letter;
    assert(coverflow_jump(slide(), 0, 1, &letter) == 8 && letter == 'B');
    assert(coverflow_jump(slide(), 1, 1, &letter) == 7 && letter == 'B');
    assert(coverflow_jump(slide(), 8, -1, &letter) == 8 && letter == 'A');
    /* Year: the query's order (newest first, undated last), the rest after. */
    ranked[0] = "New", ranked[1] = "a", ranked[2] = 0;
    press_sort();
    assert(saved_sort() == 6);
    order_is((const char *const[]){ "New", "A", "Cover", "Folder", "Embedded", "None", "B", "C", "Tight", 0 });
    assert(coverflow_jump(slide(), 0, 1, &letter) == 1 && !letter);
    assert(strstr(rank_sql, "else 0 end) desc"));
    /* Year order: Oldest first: the next open ranks again, ascending (the query's order still). */
    close_page();
    year_old = 1, q = queries;
    open_page();
    assert(queries == q + 1 && strstr(rank_sql, "else 0 end),album") && !strstr(rank_sql, "desc"));
    order_is((const char *const[]){ "New", "A", "Cover", 0 });
    q = queries;
    close_page();
    open_page();
    assert(queries == q); /* unchanged: kept */
    year_old = 0;
    /* An artist page's albums (coverflow_by_year), in place: the query's order, the rest after. */
    extern void coverflow_by_year(void *);
    void *mine = _create_deque("stSongInfo");
    deque_init(mine);
    for (int i = 5; i < 8; ++i) _deque_push_back(mine, records[i]); /* A B C */
    ranked[0] = "C", ranked[1] = "New", ranked[2] = "a", ranked[3] = 0;
    coverflow_by_year(mine);
    assert(deque_size(mine) == 3 && deque_at(mine, 0) == records[7] && deque_at(mine, 1) == records[5] &&
           deque_at(mine, 2) == records[6]);
    deque_destroy(mine);
    /* Recently Added: the query's order (a name in another case still matches), the rest after. */
    ranked[0] = "C", ranked[1] = "a", ranked[2] = "New", ranked[3] = "COVER", ranked[4] = "Gone", ranked[5] = 0;
    press_sort();
    order_is((const char *const[]){ "C", "A", "New", "Cover", "Folder", "Embedded", "None", "B", "Tight", 0 });
    assert(coverflow_jump(slide(), 0, 1, &letter) == 1 && !letter);
    /* Most Played: the album's listens, most first, ties and unplayed in stock order. */
    played[4] = 5, played[1] = 2, played[7] = 2;
    q = queries;
    press_sort();
    assert(saved_sort() == 3 && queries == q);
    order_is((const char *const[]){ "New", "Folder", "C", "Cover", "Embedded", "None", "A", "B", "Tight", 0 });
    assert(coverflow_jump(slide(), 0, 1, &letter) == 1 && !letter);
    /* The order outlives the page; Album again is the stock order. */
    close_page();
    open_page();
    order_is((const char *const[]){ "New", "Folder", "C", 0 });
    /* Random: the generator's order, nothing queried. */
    q = queries;
    press_sort();
    assert(saved_sort() == 4 && queries == q);
    order_is((const char *const[]){ "Tight", "C", "B", "A", "New", "None", "Embedded", "Folder", "Cover", 0 });
    assert(coverflow_jump(slide(), 0, 1, &letter) == 1 && !letter);
    press_sort();
    assert(saved_sort() == 0);
    order_is((const char *const[]){ "Cover", "Folder", "Embedded", "None", "New", "A", "B", "C", "Tight", 0 });
    close_page();
    assert(loads == unloads);
    *(const char **)(records[6] + REC_ARTIST) = "Artist";
    *(const char **)(records[8] + REC_ARTIST) = "Artist";
    memset(played, 0, sizeof played);
    rescan();
}

/* Artist albums: Coverflow (navigation.c): an artist's albums alone, in their order, then All
   albums, no Sort or Refresh card; the library's list is back once the page closes. */
int coverflow_artist(void *albums);
static void artist_only(void) {
    rescan();
    open_page();
    int all = slide()->nkids;
    close_page();
    deque *d = _create_deque(0);
    d->at[d->n++] = records[6], d->at[d->n++] = records[8];
    page = &w[nw + 1];
    assert(coverflow_artist(d));
    run();
    assert(slide()->nkids == 3);
    order_is((const char *const[]){ "B", "Tight", 0 });
    deque *again = _create_deque(0);
    again->at[again->n++] = records[0];
    page = &w[nw + 1];
    assert(coverflow_artist(again)); /* a scoped page replaces the open one (#73) and owns the list */
    run();
    order_is((const char *const[]){ "Cover", 0 });
    close_page();
    open_page();
    assert(slide()->nkids == all);
    order_is((const char *const[]){ "Cover", "Folder", 0 });
    close_page();
    deque *none = _create_deque(0);
    assert(!coverflow_artist(none));
    free(none);
    assert(loads == unloads);
    artist_view = 2;
    d = _create_deque(0);
    d->at[d->n++] = records[6], d->at[d->n++] = records[8];
    page = &w[nw + 1];
    int before = loads;
    assert(coverflow_artist(d));
    run();
    assert(loads == before); /* text mode never decodes artwork */
    widget *row = 0;
    for (int i = (int)(page - w); i <= nw; ++i)
        if (!strcmp(w[i].type, "list_item") && w[i].click) { row = &w[i]; break; }
    assert(row);
    row->click(row->ctx, 0); run();
    assert(!strcmp(title(), "B"));
    key(KEY_RETURN); /* tracks return to the text list */
    assert(!strcmp(title(), "Albums") && loads == before);
    close_page();
    artist_view = 0;
}

/* The Unknown Album card shows only when its press would find a song: getAllAlbum appends it even
   when every song carries an album tag. It stays last whatever the Sort. */
static void unknown_card(void) {
    unknown_row = 1;
    rescan();
    open_page();
    assert(slide()->nkids == albums && unknown_queries == 0);
    close_page();
    unknown_songs = 1;
    rescan();
    open_page();
    widget *s = slide();
    assert(s->nkids == albums + 1 && !strcmp(album_at(albums), "Unknown Album"));
    ranked[0] = "Unknown Album", ranked[1] = "None", ranked[2] = 0; /* Artist, then year */
    press_sort();
    assert(!strcmp(album_at(albums), "Unknown Album") && !strcmp(album_at(0), "None"));
    ranked[0] = 0;
    for (int i = 0; i < 4; ++i) press_sort(); /* to Random */
    assert(!strcmp(album_at(albums), "Unknown Album"));
    press_sort(); /* back to Album */
    close_page();
    assert(loads == unloads);
    unknown_row = unknown_songs = 0;
    rescan();
}

/* Baroness' and Stone Temple Pilots' "Purple": getAllAlbum's one row for the name becomes two cards,
   each with its own art and only its own track. */
static void same_name(void) {
    extern void *coverflow_tracks(void *);
    static const char *const who[2] = { "Baroness", "Stone Temple Pilots" };
    album(who[0], "cover.jpg"), album(who[1], "cover.jpg"); /* their folders */
    for (int i = 0; i < 2; ++i) {
        snprintf(names[albums - 2 + i], 64, "Purple");
        *(const char **)(records[albums - 2 + i] + REC_ARTIST) = who[i];
        *(const char **)(records[albums - 2 + i] + REC_ALBUM_ARTIST) = who[i];
    }
    rescan();
    free_blocks = 1 << 20;
    open_page();
    widget *s = slide();
    assert(s->nkids == albums);
    for (int i = albums - 2; i < albums; ++i) {
        char art[600];
        snprintf(art, sizeof art, PEQ_ROOT "/mnt/mmc/.coverflow/%08x.jpg",
                 fnv(fnv(FNV_SEED, (const unsigned char *)who[i - albums + 2]), (const unsigned char *)"Purple"));
        assert(!access(art, 0));
        w[s->kids[i]].click(w[s->kids[i]].ctx, 0);
        run();
        void *t = coverflow_tracks(page);
        assert(!strcmp(title(), "Purple") && deque_size(t) == 1 && deque_at(t, 0) == records[i]);
        key(KEY_RETURN);
    }
    close_page();
    albums -= 2;
    rescan();
}
/* Spoken-audio folders and M4B files never make covers, including artist and rated views. */
static int five_hearts(void *r) { return 5; }
static void audiobooks(void) {
    extern int coverflow_rated(int (*hearts)(void *));
    int music = albums;
    static const char *const books[] = {
        "/mnt/mmc/Audiobooks/Novel/01.mp3", "/mnt/mmc/aUdIoBoOkS/02.flac",
        "/mnt/mmc/Music/Novel.M4B", "/mnt/mmc/Podcasts/Show/01.mp3",
        "/mnt/mmc/pOdCaStS/02.mp3"
    };
    for (unsigned i = 0; i < sizeof books / sizeof *books; ++i) {
        album("Novel", 0);
        snprintf(paths[albums - 1], 600, "%s", books[i]);
    }
    /* Music, a book and a podcast share album and album-artist tags. */
    memmove(names[music], names[0], sizeof names[0]);
    *(const char **)(records[0] + REC_ALBUM_ARTIST) = "Shared";
    *(const char **)(records[music] + REC_ALBUM_ARTIST) = "Shared";
    memmove(names[music + 3], names[0], sizeof names[0]);
    *(const char **)(records[music + 3] + REC_ALBUM_ARTIST) = "Shared";
    extern void *coverflow_album_tracks(void *);
    deque *tracks = coverflow_album_tracks(records[0]);
    assert(tracks->n == 1 && tracks->at[0] == records[0]);
    free(tracks);

    rescan();
    open_page();
    assert(slide()->nkids == music);
    close_page();
    deque *artist = _create_deque(0);
    artist->at[artist->n++] = records[music + 1];
    artist->at[artist->n++] = records[music + 4];
    assert(!coverflow_artist(artist) && artist->n == 2);
    free(artist);
    assert(coverflow_rated(five_hearts));
    run();
    assert(slide()->nkids == music + 1);
    close_page();
    *(const char **)(records[0] + REC_ALBUM_ARTIST) = 0;
    albums = music;
    /* An audiobook-only library renders the empty state. */
    char saved[64][600];
    for (int i = 0; i < albums; ++i) {
        strcpy(saved[i], paths[i]);
        snprintf(paths[i], 600, "/mnt/mmc/Audiobooks/%d.mp3", i);
    }
    rescan();
    open_page();
    assert(!slide());
    close_page();
    for (int i = 0; i < albums; ++i) strcpy(paths[i], saved[i]);
    rescan();
}

static void embedded_first(void) {
    /* Embedded art comes before a folder's cover.jpg, and a folder shared with another album gives
       its albums no cover.jpg: with embedded art failing, they keep the placeholder. */
    embedded_fails = 0;
    album("Both", "cover.jpg");
    album("Embedded2", 0);
    rescan();
    open_page();
    assert(size("Both") == 8 && size("Embedded2") == 8 && !tmp_files());
    close_page();
    album("Shared", "cover.jpg");
    album("Mate", 0);
    snprintf(paths[albums - 1], 600, PEQ_ROOT "/music/Shared/02.flac");
    fclose(fopen(paths[albums - 1], "w"));
    embedded_fails = 1;
    rescan();
    open_page();
    assert(size("Shared") == 0 && size("Mate") == 0 && !tmp_files());
    close_page();
}

/* Without album artists: an album's disc folders (CD1, Disc 2) are one card with both tracks,
   and a same-named album in another folder is its own. */
static void disc_folders(void) {
    extern void *coverflow_tracks(void *);
    static const char *const dirs[3] = { "Purple/CD1", "Purple/Disc 2", "Other Purple" }, *const who[3] = { "Baroness", "Baroness", "STP" };
    mkdir(PEQ_ROOT "/music/Purple", 0755);
    for (int i = 0; i < 3; ++i) {
        album(dirs[i], 0);
        snprintf(names[albums - 1], 64, "Purple");
        *(const char **)(records[albums - 1] + REC_ARTIST) = who[i];
    }
    rescan();
    open_page();
    widget *s = slide();
    assert(s->nkids == albums - 1);
    for (int i = 0; i < 2; ++i) {
        w[s->kids[albums - 3 + i]].click(w[s->kids[albums - 3 + i]].ctx, 0);
        run();
        void *t = coverflow_tracks(page);
        assert(deque_size(t) == (unsigned)(2 - i) && deque_at(t, 0) == records[albums - 3 + 2 * i]);
        if (!i) assert(deque_at(t, 1) == records[albums - 2]);
        key(KEY_RETURN);
    }
    close_page();
    albums -= 3;
    rescan();
}

static void ffmpeg_art(void) {
    /* What stock cannot decode goes to ffmpeg (FFMPEG_STUB): a folder's cover.webp, and the
       embedded picture of an album whose track only ffmpeg reads. */
    int before = calls;
    embedded_fails = 1;
    album("Webp", "cover.webp");
    album("Ffok", 0);
    rescan();
    open_page();
    assert(calls == before && size("Webp") == 6 && size("Ffok") == 6 && !tmp_files());
    close_page();
}

static void player_cover(void) {
    /* Now Playing's folder art (coverflow_player_cover): stock's, else ffmpeg alone on a folder image
       (stock already refused it), then on the embedded picture. No stock thumbnailer: its parse lock
       would nest under the player's. */
    const char *dst = PEQ_ROOT "/externpic.jpg";
    char got[16];
    struct { const char *album, *want; int found; } cases[] = {
        { "Webp", "ffmpeg", 0 }, { "Ffok", "ffmpeg", 0 }, { "Cover", 0, 0 }, { "Cover", "extern", 1 } };
    for (unsigned i = 0; i < sizeof(cases) / sizeof(*cases); ++i) {
        char track[600];
        snprintf(track, sizeof track, PEQ_ROOT "/music/%s/01.flac", cases[i].album);
        unlink(dst);
        extern_found = cases[i].found;
        int before = calls, ok = coverflow_player_cover(track, dst, 320, 320);
        FILE *f = fopen(dst, "r");
        size_t n = f ? fread(got, 1, sizeof(got) - 1, f) : 0;
        if (f) fclose(f);
        got[n] = 0;
        assert(ok == !!cases[i].want && calls == before + cases[i].found);
        assert(cases[i].want ? !strcmp(got, cases[i].want) : !f);
    }
    extern_found = 0;
    /* Once the player has parsed the track, an album Coverflow marked empty skips ffmpeg; a
       missing marker, or a real cover, still goes to it. */
    char track[600], marker[600];
    snprintf(track, sizeof track, PEQ_ROOT "/music/Ffok/01.flac");
    snprintf(shim_id3, sizeof(shim_id3), "%s", track);
    snprintf(shim_id3 + ID3_ALBUM, 256, "%s", "Ffok");
    snprintf(shim_id3 + ID3_ARTIST, 256, "%s", "Artist");
    art_file(marker, "Ffok");
    for (int i = 0; i < 3; ++i) {
        FILE *f = i == 1 ? 0 : fopen(marker, "w");
        if (f) fprintf(f, "%s", i ? "ffmpeg" : ""), fclose(f);
        if (i == 1) unlink(marker);
        unlink(dst);
        int ok = coverflow_player_cover(track, dst, 320, 320);
        assert(ok == !!i && access(dst, 0) == (i ? 0 : -1));
    }
    /* The marker skips only the embedded probe: with it, Webp's cover.webp still shows (a shared
       folder, or a disc Coverflow did not look at), and a track the player has not parsed probes. */
    FILE *f = fopen(art_file(marker, "Webp"), "w");
    fclose(f);
    snprintf(track, sizeof track, PEQ_ROOT "/music/Webp/01.flac");
    snprintf(shim_id3, sizeof(shim_id3), "%s", track);
    snprintf(shim_id3 + ID3_ALBUM, 256, "%s", "Webp");
    unlink(dst);
    assert(coverflow_player_cover(track, dst, 320, 320) && !access(dst, 0));
    f = fopen(marker, "w"); /* Webp's art back, as ffmpeg_art left it */
    fputs("ffmpeg", f), fclose(f);
    f = fopen(art_file(marker, "Ffok"), "w");
    fclose(f);
    snprintf(track, sizeof track, PEQ_ROOT "/music/Ffok/01.flac");
    unlink(dst);
    assert(coverflow_player_cover(track, dst, 320, 320) && !access(dst, 0));
    fputs("ffmpeg", f = fopen(marker, "w")), fclose(f);
    memset(shim_id3, 0, sizeof(shim_id3));
}

int main(void) {
    /* Titles come from tags, unchanged; missing tags retain filename/CUE fallbacks. */
    char track[0x60] = {0}, name[512];
    *(const char **)(track + REC_NAME) = "01 - File.flac";
    *(const char **)(track + REC_PATH) = "/music/01 - File.flac";
    *(const char **)(track + REC_TITLE) = "Tagged title.flac";
    assert(!strcmp(track_name(name, sizeof name, track), "Tagged title.flac"));
    *(const char **)(track + REC_TITLE) = "";
    assert(!strcmp(track_name(name, sizeof name, track), "01 - File"));
    *(const char **)(track + REC_TITLE) = 0;
    assert(!strcmp(track_name(name, sizeof name, track), "01 - File"));
    *(const char **)(track + REC_NAME) = "CUE title";
    assert(!strcmp(track_name(name, sizeof name, track), "CUE title"));
    /* The stock scan flags are raw addresses in the device ABI; map them here. */
    assert(mmap((void *)(SCAN_THREAD & ~4095), 4096, PROT_READ | PROT_WRITE,
                MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0) != MAP_FAILED);
    deque staging = {0};
    shim_dir = &staging;
    frame_fail = 1; /* the flat fallback first: stock images on the slide_menu */
    mkdir(PEQ_ROOT, 0755); mkdir(PEQ_ROOT "/mnt", 0755); mkdir(PEQ_ROOT "/mnt/mmc", 0755); mkdir(PEQ_ROOT "/music", 0755);

    /* An empty library, or one being scanned, shows the message and builds nothing. */
    open_page();
    assert(!strcmp(title(), "Update Local Music first") && !slide() && queries == 1);
    /* Every list (message, progress, tracks) comes from list(): iPod rows are transparent, so its
       list_view paints black itself rather than the theme's light card. */
    int lists = 0;
    for (int i = nw; i > page - w; --i) if (!strcmp(w[i].type, "list_view")) { assert((unsigned)w[i].bg == 0xff000000u); ++lists; }
    assert(lists == 1);
    key(KEY_RETURN); close_page();
    album("Cover", "cover.jpg");
    *(volatile int *)SCAN_THREAD = 1;
    open_page();
    assert(!strcmp(title(), "Update Local Music first") && queries == 1 && !calls);
    close_page();
    *(volatile int *)SCAN_DONE = 1;

    /* With embedded art failing, cover.jpg beats folder.jpg; with no art an empty marker makes the
       placeholder show. Folder art takes only the parse lock. */
    char both[600];
    snprintf(both, sizeof(both), PEQ_ROOT "/music/Cover/folder.jpg");
    fclose(fopen(both, "w"));
    album("Folder", "folder.jpg");
    album("Embedded", 0);
    embedded_fails = 1;
    rescan();
    open_page();
    assert(calls == 2 && !strcmp(made[0], "cover.jpg") && !strcmp(made[1], "folder.jpg"));
    assert(size("Cover") == 9 && size("Folder") == 10 && size("Embedded") == 0 && !tmp_files());
    widget *s = slide();
    assert(s && s->nkids == 3 && !strncmp(w[s->kids[0]].image, "file://", 7));
    close_page();
    album("None", 0);
    rescan();
    open_page();
    assert(calls == 2 && size("None") == 0 && !tmp_files());
    s = slide(); /* Cover and Folder have art; Embedded and None the placeholder; nothing after them */
    int with_art = 0, bare = 0;
    for (int i = 0; i < 4; ++i) {
        if (!strncmp(w[s->kids[i]].image, "file://", 7)) ++with_art;
        else if (!strcmp(w[s->kids[i]].image, PLACEHOLDER)) ++bare;
    }
    assert(with_art == 2 && bare == 2 && s->nkids == 4);
    close_page();

    /* A later open builds only the albums with no cache file; the marker is not retried. */
    album("New", "cover.jpg");
    rescan();
    open_page();
    assert(calls == 3 && !strcmp(made[2], "cover.jpg") && size("New") == 9);
    close_page();

    /* Cancel mid-build (Return on the progress screen) keeps the finished thumbnails, leaves no
       .tmp, and shows the covers; the next open resumes with the rest. */
    album("A", "cover.jpg"); album("B", "cover.jpg"); album("C", "cover.jpg");
    rescan();
    block_at = calls + 1;
    widget home;
    memset(&home, 0, sizeof(home));
    coverflow_home(&home, 0);
    page = &w[nw + 1];
    home.click(home.ctx, 0);
    while (!blocked) usleep(1000);
    assert(!strncmp(title(), "Preparing artwork", 17));
    int e[16] = {0}; e[EVENT_KEY / 4] = KEY_RETURN;
    w[8190].click(0, e);
    pthread_t t;
    pthread_create(&t, 0, release, 0); /* finish the album in hand once the cancel is set */
    run();
    pthread_join(t, 0);
    assert(size("A") == 9 && size("B") == 9 && size("C") == -1 && !tmp_files() && slide() && slide()->visible);
    close_page();
    block_at = -1;
    open_page();
    assert(size("C") == 9 && !strcmp(made[calls - 1], "cover.jpg"));

    /* Display settings' Refresh Coverflow (coverflow_refresh_art) clears the cache and, at the next
       open, queries the albums again and rebuilds every one. */
    int before = calls, q = queries;
    close_page();
    coverflow_refresh_art();
    assert(size("Cover") == -1 && size("None") == -1);
    open_page();
    run();
    assert(calls == before + albums - 2 && size("None") == 0 && size("Embedded") == 0 && size("Cover") == 9);
    assert(queries == q + 1); /* Refresh always queries again */
    close_page();

    /* The album list outlives the page: reopening queries nothing until a songtable writer runs,
       and a list queried while one runs is not kept. */
    q = queries;
    open_page(); assert(queries == q && slide()); close_page();
    rescan(); /* each writer's hook is checked on the MIPS build (test/patch.py) */
    open_page(); assert(queries == ++q && slide()); close_page();
    open_page(); assert(queries == q); close_page();
    during_write = mid_write; rescan(); during_write = 0;
    assert(queries == ++q);
    open_page(); assert(queries == ++q); close_page();

    /* Low free space on the card skips the build: placeholders, no thread. */
    album("Tight", "cover.jpg");
    rescan();
    free_blocks = 4000; /* under 16 MB of 4 KB blocks */
    before = calls;
    open_page();
    assert(calls == before && size("Tight") == -1 && slide() && !strcmp(w[slide()->kids[albums - 1]].image, PLACEHOLDER));
    close_page();
    assert(loads == unloads);
    depth();
    sorting();
    artist_only();
    unknown_card();
    same_name();
    disc_folders();
    audiobooks();
    ffmpeg_art();
    player_cover();
#if IPOD
    /* iPod Home: the player's cover for its type, once the player has parsed the current track
       (g_lastcover_url is its path), else the track album's Coverflow thumbnail, else the
       placeholder; reloaded only when the track or the usable cover changes, and only while Home
       is the painted top window. Both themes split the screen, the art itself in the right panel. */
    extern void coverflow_home_art(void *);
    assert(mmap((void *)(MCL_POS & ~4095), 4096, PROT_READ | PROT_WRITE,
                MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0) != MAP_FAILED);
    deque queue = {0};
    shim_queue = &queue;
    home_art = make(0, "image");
    int *geo = (int *)W(home_art)->raw, asset[4] = { 0, 0, 375, 290 }, /* W_X, W_Y, W_W, W_H */
        split[4] = { HOME_SPLIT_W, 0, 375 - HOME_SPLIT_W, 290 };
    memcpy(geo, asset, sizeof asset);
    widget *win = make(0, "window");
    const char *art = W(home_art)->image, *player = "file://" PEQ_ROOT "/tmp/coverpic.jpg";
    mkdir(PEQ_ROOT "/tmp", 0755);
    FILE *f = fopen(PEQ_ROOT "/tmp/coverpic.jpg", "w"); fputs("jpg", f); fclose(f);
    coverflow_home(win, 0);
    assert(&w[insensitive] == W(home_art)); /* taps under a fitted cover still find the list */
    coverflow_home_art(page);
    assert(!*art);
    coverflow_home_art(win);
    assert(!strcmp(art, "default_album_home") && !memcmp(geo, split, sizeof split)); /* no size: the panel */
    queue.n = 2; queue.at[0] = records[0]; queue.at[1] = records[3]; /* "Cover" is cached, "None" is not */
    *(volatile int *)MCL_POS = 0;
    shim_covertype = 1;
    coverflow_home_art(win); /* the cover is still the previous track's */
    assert(strstr(art, "/mnt/mmc/.coverflow/") && size("Cover") > 0);
    snprintf(shim_lastcover, sizeof(shim_lastcover), "%s", paths[0]);
    timer_fn = 0;
    coverflow_home_art(win);
    assert(!strcmp(art, player));
    /* a paint drops its own invalidations: the whole screen repaints after it */
    assert(!wm_repaints && timer_fn && (timer_fn(0), wm_repaints == 1));
    /* Square, portrait and landscape covers keep their proportions, just cover the panel and are
       centred on it; C division leaves an odd overflow's extra pixel on the right or bottom. */
    static const int covers[][6] = { { 160, 160, 136, 0, 290, 290 }, { 206, 290, 178, 0, 206, 290 },
                                     { 300, 200, 64, 0, 435, 290 }, { 100, 400, 187, -231, 188, 752 } };
    for (unsigned i = 0; i < 4; ++i) {
        art_w = covers[i][0], art_h = covers[i][1];
        *(volatile int *)MCL_POS = 1; coverflow_home_art(win); *(volatile int *)MCL_POS = 0; coverflow_home_art(win);
        for (int k = 0; k < 4; ++k) assert(geo[k] == covers[i][2 + k]);
    }
    art_w = art_h = 160;
    /* The art's paint is clipped to the panel on screen (canvas origin at the art, window at y 30)
       from the background hook to the border hook; other widgets keep the clip. */
    extern void coverflow_home_clip(void *, void *, int);
    int canvas[2] = { geo[0], 30 + geo[1] }, full[4] = { 0, 0, 375, 320 }, want[4] = { HOME_SPLIT_W, 30, 375 - HOME_SPLIT_W, 290 };
    coverflow_home_clip(home_art, canvas, 1);
    assert(!memcmp(clip_rect, want, sizeof want));
    coverflow_home_clip(win, canvas, 0);
    assert(!memcmp(clip_rect, want, sizeof want));
    coverflow_home_clip(home_art, canvas, 0);
    assert(!memcmp(clip_rect, full, sizeof full));
    coverflow_home_clip(win, canvas, 1);
    assert(!memcmp(clip_rect, full, sizeof full));
    before = loads;
    coverflow_home_art(win);
    assert(loads == before);
    shim_covertype = 2; /* a folder image the player has not written: the cached thumbnail */
    coverflow_home_art(win);
    assert(strstr(art, "/mnt/mmc/.coverflow/"));
    *(volatile int *)MCL_POS = 1; shim_covertype = 1; /* next track, the old cover still in place */
    coverflow_home_art(win);
    assert(!strcmp(art, "default_album_home"));
    snprintf(shim_lastcover, sizeof(shim_lastcover), "%s", paths[3]);
    coverflow_home_art(win);
    assert(!strcmp(art, player) && loads == unloads);
    /* Issue #7: the next folder stock queues after a folder play carries no tags; with no player
       cover the art is the placeholder until the player's parsed tags name the cached album. */
    static char next[0x60];
    *(const char **)(next + REC_PATH) = "/mnt/mmc/Next/01.flac";
    queue.n = 3; queue.at[2] = next;
    *(volatile int *)MCL_POS = 2; shim_covertype = 3;
    snprintf(shim_lastcover, sizeof(shim_lastcover), "%s", "/mnt/mmc/Next/01.flac");
    coverflow_home_art(win);
    assert(!strcmp(art, "default_album_home"));
    snprintf(shim_id3, sizeof(shim_id3), "%s", "/mnt/mmc/Next/01.flac");
    snprintf(shim_id3 + ID3_ALBUM, 256, "%s", "Cover");
    snprintf(shim_id3 + ID3_ARTIST, 256, "%s", "Artist");
    coverflow_home_art(win);
    assert(strstr(art, "/mnt/mmc/.coverflow/") && loads == unloads);
    memset(shim_id3, 0, sizeof(shim_id3));
    queue.n = 2; *(volatile int *)MCL_POS = 1; shim_covertype = 1;
    snprintf(shim_lastcover, sizeof(shim_lastcover), "%s", paths[3]);
    coverflow_home_art(win);
    assert(!strcmp(art, player) && loads == unloads);
    /* Home layout from the asset's full-width list: Split narrows the list, its rows and their tap
       targets to HOME_SPLIT_W and shows the art; Full widens them (rows to HOME_FULL_ROW in Minimal,
       HOME_CLASSIC_ROW in Classic, labels ending before the chevron) and hides the art. Minimal's
       labels start after the dot, Classic's at HOME_CLASSIC_TEXT_X. */
    extern void coverflow_home_layout(void);
    home_list = make(0, "list_view");
    widget *sv = make(home_list, "scroll_view"), *row = make(sv, "view"), *label = make(row, "hscroll_label"),
           *tap = make(row, "image"), *all[] = { home_list, sv, row, tap };
    for (int i = 0; i < 4; ++i) *(int *)(all[i]->raw + W_W) = 375;
    int dot_x = HOME_DOT_X + HOME_DOT + HOME_DOT_GAP;
    *(int *)(label->raw + W_X) = dot_x;
    home_full = 1;
    coverflow_home(win, 0);
    for (int i = 0; i < 4; ++i) assert(*(int *)(all[i]->raw + W_W) == (i < 2 ? 375 : HOME_FULL_ROW));
    assert(*(int *)(label->raw + W_W) == HOME_FULL_ROW - dot_x - HOME_LABEL_END && !W(home_art)->visible);
    before = loads;
    coverflow_home_art(win); /* hidden: nothing loads */
    assert(loads == before);
    home_full = 0;
    coverflow_home_layout();
    for (int i = 0; i < 4; ++i) assert(*(int *)(all[i]->raw + W_W) == HOME_SPLIT_W);
    assert(*(int *)(label->raw + W_W) == HOME_SPLIT_W - dot_x - HOME_SPLIT_LABEL_END && W(home_art)->visible);
    coverflow_home_art(win);
    assert(loads == before + 1 && !strcmp(art, player));
    classic = 1; /* Classic halves the screen: the 160px cover fills 290px from 187 + (188 - 290) / 2 */
    coverflow_home_layout();
    for (int i = 0; i < 4; ++i) assert(*(int *)(all[i]->raw + W_W) == HOME_SPLIT_W);
    assert(*(int *)(label->raw + W_X) == HOME_CLASSIC_TEXT_X &&
           *(int *)(label->raw + W_W) == HOME_SPLIT_W - HOME_CLASSIC_TEXT_X - HOME_CLASSIC_LABEL_END);
    coverflow_home_art(win);
    static const int classic_geo[4] = { 136, 0, 290, 290 };
    assert(loads == before + 2 && !strcmp(art, player) && !memcmp(geo, classic_geo, sizeof classic_geo));
    home_full = 1;
    coverflow_home_layout();
    for (int i = 0; i < 4; ++i) assert(*(int *)(all[i]->raw + W_W) == (i < 2 ? 375 : HOME_CLASSIC_ROW));
    assert(*(int *)(label->raw + W_W) == HOME_CLASSIC_ROW - HOME_CLASSIC_TEXT_X - HOME_CLASSIC_LABEL_END && !W(home_art)->visible);
    classic = 0; home_full = 0;
    coverflow_home_layout();
    assert(*(int *)(label->raw + W_X) == dot_x && !memcmp(geo, split, sizeof split) && loads == unloads);

#endif
    embedded_first();
    return 0;
}
"""


# Stands in for stock ffmpeg: "ffmpeg" in its output (the last argument) for a .webp source or a
# track under Ffok/, else a failure.
FFMPEG_STUB = r"""#!/bin/sh
for a; do [ "$prev" = -i ] && src=$a; prev=$a; done
case $src in *.webp|*/Ffok/*) printf ffmpeg > "$a";; *) exit 1;; esac
"""


def main():
    import argparse, os
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--captures', type=pathlib.Path,
                    help='write the renderer\'s frames (rest, quarter, half, three-quarter turns, one album) as PNGs here')
    a = ap.parse_args()
    source = ((ROOT/'patch/offsets.inc').read_text() + (ROOT/'patch/coverflow.c').read_text()).replace('\\\n', '')
    def macro(name):
        line = re.search(r'^#define ' + name + r' (.*)$', source, re.M)[1]
        return ''.join(ast.literal_eval(x) for x in re.findall(r'"(?:[^"\\]|\\.)*"', line))
    sql = macro('MUSIC_ALBUM_SQL') + macro('MUSIC_WHERE') + macro('ALBUM_GROUP')
    with sqlite3.connect(':memory:') as db:
        db.execute('create table songtable(id,album,songer,fileurl,albumsonger)')
        paths = ['/mnt/mmc/Audiobooks/Book/01.mp3', '/mnt/mmc/aUdIoBoOkS/02.flac',
                 '/mnt/mmc/Music/book.M4B', '/mnt/mmc/Music/song.flac',
                 '/mnt/mmc/Audiobooks Extra/song.mp3', '/mnt/mmc/Music/book.m4b.mp3',
                 '/mnt/mmc/Podcasts/Show/episode.mp3', '/mnt/mmc/pOdCaStS/untagged.mp3',
                 '/mnt/mmc/Podcasts Extra/song.mp3', '/mnt/mmc/Music/Podcasts/song.mp3']
        db.executemany('insert into songtable values (?,"Shared","Artist",?,"Artist")', enumerate(paths))
        assert {r[0] for r in db.execute('select id from songtable where '+macro('MUSIC_WHERE'))} == {3,4,5,8,9}
        rows = db.execute(sql).fetchall()
        assert len(rows) == 1 and rows[0][3] in paths[3:]
        db.execute('delete from songtable where id >= 3')
        assert not db.execute(sql).fetchall()
        db.execute("insert into songtable values (10,NULL,'Artist','/mnt/mmc/Music/untagged.mp3',NULL)")
        assert db.execute(sql).fetchone()[:2] == (-1, 'Unknown Album')
        db.execute("update songtable set fileurl='/mnt/mmc/Audiobooks/untagged.mp3' where id=10")
        assert not db.execute(sql).fetchall()
        db.execute("update songtable set fileurl='/mnt/mmc/PODCASTS/untagged.mp3' where id=10")
        assert not db.execute(sql).fetchall()

    with tempfile.TemporaryDirectory(prefix='q2-coverflow-check-') as directory:
        tmp = pathlib.Path(directory)
        (tmp/'shim.h').write_text(SHIM_H)
        (tmp/'test.c').write_text(TEST)
        (tmp/'ffmpeg').write_text(FFMPEG_STUB)
        (tmp/'ffmpeg').chmod(0o755)
        for ipod in (0, 1):  # a fresh card each run
            binary = tmp/f'coverflow_test{ipod}'
            subprocess.run(['cc', '-m32', '-pthread', '-DPEQ_HOST', f'-DPEQ_ROOT="{tmp}/root{ipod}"', f'-DIPOD={ipod}',
                            f'-DART_FFMPEG="{tmp}/ffmpeg"', '-D_GNU_SOURCE', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-function',
                            '-I', str(ROOT/'patch'), '-include', str(tmp/'shim.h'), str(ROOT/'patch/coverflow.c'),
                            str(tmp/'test.c'), '-o', str(binary)], check=True)
            env = dict(os.environ)
            if a.captures and ipod:  # capture the iPod reflection intensity
                (tmp/'frames').mkdir()
                env['CF_CAPTURES'] = str(tmp/'frames')
            subprocess.run([str(binary)], check=True, env=env)
        if a.captures:
            a.captures.mkdir(parents=True, exist_ok=True)
            from ipod import inc, imagemagick
            size = f"{inc('CF_VIEW_W')}x{inc('CF_VIEW_H')}"
            for raw in sorted((tmp/'frames').glob('*.rgba')):  # RGBA8888 rows, the frame's byte order
                (a.captures/(raw.stem + '.png')).write_bytes(imagemagick(
                    '-size', size, '-depth', '8', 'rgba:-', '-alpha', 'off', 'png:-', data=raw.read_bytes()))
    print('Coverflow: art order, ffmpeg fallback, locks, markers, resume, cancel, Refresh, album list reuse, low space and empty library passed;'
          ' depth renderer (exact centre, clipping, symmetry, reflection, continuity),'
          ' its texture window, centre click, small libraries and flat fallback passed;'
          ' Sort by artist, year, recently added, most played and random, kept across opens, passed; an artist\'s albums alone passed;'
          ' iPod Home art sources, fit, clip, cache and Split/Full layout in both themes passed.')


if __name__ == '__main__':
    main()
