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
extern char system_bar[];
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
#define g_backlight_status 0 /* dark: Home's cover never pans here */
#define g_lockscreen_pageflag 0
int shim_lock(void *), shim_unlock(void *), shim_statfs(const char *, void *);
int tk_str_start_with(const char *, const char *);
const unsigned *widget_get_text(void *);
int canvas_set_font(void *, const char *, unsigned);
float canvas_measure_text(void *, const unsigned *, unsigned);
""" + ''.join(f'{r} {n}({a});\n' for n in """
canvas_fill_rect getAllAlbum getMusicByAlbum toolsQueryDbTable toolsUpdateDbTable album_row toolsThumbSpecCover toolsGetAlbumCover toolsGetExternCover _create_deque deque_init_copy deque_clear
deque_init _deque_push_back deque_pop_back
deque_assign deque_destroy deque_size deque_at window_create widget_factory
widget_factory_create_widget image_create hscroll_label_create set_hscroll_label_attribute
slide_menu_set_value slide_menu_item_width list_view_create scroll_view_create list_item_create image_set_draw_type
image_base_set_image widget_load_image widget_unload_image widget_set_name widget_use_style
widget_set_text_utf8 widget_restack widget_animator_prop_create widget_animator_prop_set_params widget_animator_start widget_set_visible widget_get_prop_int widget_set_prop_int
widget_get_prop_str widget_on widget_destroy_children widget_invalidate_force
widget_count_children widget_get_child widget_lookup widget_move_resize widget_get_visible
canvas_get_clip_rect canvas_set_clip_rect widget_set_sensitive widget_get_type tk_strcmp widget_animator_destroy
timer_add timer_remove navigator_back_to_home navigator_back window_manager_close_window_force navigator_to_with_context bitmap_create_ex bitmap_destroy bitmap_unlock_buffer
bitmap_lock_buffer_for_read bitmap_lock_buffer_for_write bitmap_get_line_length
canvas_draw_image slide_menu_set_spacer window_manager window_manager_get_top_window window_manager_is_animating time_now_ms
widget_to_local widget_animator_pause window_manager_get_pointer_pressed image_manager image_manager_unload_bitmap_by_name image_manager_unload_all
on_home_music_onclick on_home_stream_onclick widget_layout widget_destroy widget_paint canvas_translate widget_set_prop_str
canvas_offline_create canvas_offline_begin_draw canvas_offline_end_draw canvas_offline_destroy toolsTimeItoa
""".split() for r, a in [PROTOTYPES[n]]) + r"""
int stock_canvas_set_fill_color(void *, unsigned);
void *shim_calloc(size_t, size_t);
#define calloc shim_calloc
"""

TEST = r"""
#include <assert.h>
#include <math.h>
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
typedef struct { char raw[0x100]; int parent, visible, numb, kids[512], nkids, bg; char type[32], text[160], image[600];
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
static int loads, unloads, theme_placeholder, cover_fail, clear_cover;
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
static int note_loads, note_drawn[4], note_fail; /* the pane image: a theme image, translucent */
static char pane_image[32];
int widget_load_image(void *x, const char *url, void *b) {
    struct stat s;
    if (!strncmp(url, "classic_light_", 14)) {
        snprintf(pane_image, sizeof pane_image, "%s", url);
        memset(b, 0, 0x18);
        int note = strstr(url, "note") != 0; /* the pane icons are 96 x 96 */
        ((unsigned *)b)[0] = note ? 100 : 96, ((unsigned *)b)[1] = note ? 115 : 96, note_loads += !note_fail;
        return note_fail;
    }
    int themed = theme_placeholder && strncmp(url, "file://", 7);
    int failed = cover_fail || (!themed && (strncmp(url, "file://", 7) || stat(url + 7, &s) || !s.st_size));
    loads += !failed && !themed;
    if (!failed) {
        unsigned *bm = b, seed = 0;
        for (const char *c = strrchr(url, '/') ? strrchr(url, '/') : url; *c; ++c) seed = seed * 31 + (unsigned char)*c; /* the key, not the scratch path */
        unsigned char *px = pixels[next_pixels++ % 16];
        pattern(px, seed % 1000);
        if (themed || clear_cover) memset(px, 0, sizeof pixels[0]); /* transparent theme placeholder */
        bm[0] = art_w, bm[1] = art_h, bm[2] = art_w * 4;
        ((unsigned short *)b)[7] = 1; /* RGBA8888 */
        *(unsigned char **)((char *)b + 0x14) = px;
    }
    return failed;
}
const unsigned char *bitmap_lock_buffer_for_read(void *b) { ++reads; return *(unsigned char **)((char *)b + 0x14); }
int widget_unload_image(void *x, void *b) { ++unloads; return 0; }
static char dropped[600]; /* the last cached bitmap dropped by name */
void *image_manager(void) { return (void *)8; }
int image_manager_unload_bitmap_by_name(void *m, const char *name) { snprintf(dropped, sizeof dropped, "%s", name); return 0; }
static int drops; /* image_manager_unload_all calls */
int image_manager_unload_all(void *m) { ++drops; return 0; }
void set_hscroll_label_attribute(void *x) { assert(!strcmp(W(x)->type, "hscroll_label")); }
int slide_menu_set_value(void *x, int value) { *(int *)(W(x)->raw + SLIDE_INDEX) = value; return 0; }
int widget_set_name(void *x, const char *s) { snprintf(W(x)->type, 32, "%s", s); return 0; }
void page_empty(void *body, const char *name) { (void)body; (void)name; } /* navigation.c: page_list_or only */
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
static handler touch_fn, changed_fn; /* the covers' touch and the slide_menu's change */
unsigned widget_on(void *x, unsigned type, handler f, void *ctx) {
    if (!x) return 0;
    if (type >= EVT_POINTER_DOWN && type <= EVT_POINTER_ABORT) touch_fn = f;
    if (type == EVT_VALUE_CHANGED) changed_fn = f;
    if (type == EVT_CLICK || type == EVT_KEY_UP || type == EVT_DESTROY) {
        widget *h = type == EVT_CLICK ? W(x) : &w[0] + (type == EVT_KEY_UP ? 8190 : 8191); /* page handlers */
        h->click = f; h->ctx = ctx;
    }
    return 1;
}
int widget_destroy_children(void *x) { W(x)->nkids = 0; return 0; }
static int wm_repaints; /* whole-screen invalidations, from outside a paint */
void *window_manager(void) { return &wm_repaints; }
char system_bar[sizeof(void *)];  /* no bar: the window manager's first child is whatever the test put there */
static void *top_window;
static int animating;
void *window_manager_get_top_window(void *wm) { return top_window; }
int window_manager_is_animating(void *wm) { return animating; }
static unsigned clock_ms, pressed = 1, paused;
unsigned time_now_ms(void) { return clock_ms; }
int window_manager_get_pointer_pressed(void *wm) { return pressed; }
int widget_animator_pause(void *a) { ++paused; return 0; }
static int animators_gone;
int widget_animator_destroy(void *a) { ++animators_gone; return 0; }
int widget_to_local(void *x, void *p) { int *r = (int *)W(x)->raw, *q = p; q[0] -= r[0], q[1] -= r[1]; return 0; }
/* navigation.c's: the glide asked for, the animator standing in as running until glide_end. */
static int glides, glide_goal, glide_ms, glide_easing;
void motion_open(void *p, const char *n) {}
int carousel_glide(void *x, int goal, unsigned ms, int easing) {
    if (!ms) { /* the immediate move, as navigation.c's: the index moves, the offset is 0 */
        int n = W(x)->nkids, stride = slide_menu_item_width(x) + *(int *)(W(x)->raw + SLIDE_SPACER);
        int id = (*(int *)(W(x)->raw + SLIDE_INDEX) - goal / stride) % n;
        *(void **)(W(x)->raw + SLIDE_ANIMATOR) = 0;
        *(int *)(W(x)->raw + SLIDE_OFFSET) = 0;
        slide_menu_set_value(x, id < 0 ? id + n : id);
        return 0;
    }
    *(void **)(W(x)->raw + SLIDE_ANIMATOR) = (void *)1;
    ++glides, glide_goal = goal, glide_ms = (int)ms, glide_easing = easing;
    return 1;
}
/* navigation.c's big letter: the last one shown, and how many */
static unsigned letter_shown;
static int letters;
void coverflow_letter(void *x, unsigned letter, unsigned now) { letter_shown = letter, ++letters; }
int widget_invalidate_force(void *x, void *y) { wm_repaints += x == &wm_repaints; return 0; }
unsigned widget_count_children(void *x) { return x == &wm_repaints ? 0 : W(x)->nkids; } /* no window stack */
void *widget_get_child(void *x, unsigned i) { return &w[W(x)->kids[i]]; }
static void *home_art, *home_list; /* iPod Home's art and list; none in the carousel tests */
void *widget_lookup(void *x, const char *n, int r) {
    return !x ? 0 : !strcmp(n, "img_coverflow") ? x : !strcmp(n, "img_homeart") ? home_art :
           !strcmp(n, "list_view_home") ? home_list : 0;
}
static int home_mode, classic, light; /* home_mode: HOME, 0 Split, 1 Full, 2 Carousel */
int ipod_home(void) { return home_mode; }
int home_lib_on(void) { return 1; }
int row_preview_on(void) { return 1; }
int library_search(void) { return 1; }
void library_rows(void *list) {}
void stream_rows(void *list) {}
int tk_str_start_with(const char *s, const char *p) { return !strncmp(s, p, strlen(p)); }
const unsigned *widget_get_text(void *w) { return 0; }
int canvas_set_font(void *c, const char *f, unsigned px) { return 0; }
float canvas_measure_text(void *c, const unsigned *s, unsigned n) { return 0; }
const char *theme_face(const unsigned *s, int n, int *px) { return 0; }
void home_pane_read(int *services, int *games, int *free_tenths) { *services = 5; *games = 4; *free_tenths = 412; }
extern volatile unsigned library_gen;
static int count_calls, count_answer = 704;
static pthread_t count_thread_seen;
int library_count(const char *sql) {
    ++count_calls, count_thread_seen = pthread_self();
    return strstr(sql, "sum(e=0)") ? count_answer == 704 ? 312 : count_answer : count_answer;
}
int on_home_music_onclick(void *ctx, void *e) { return 0; }
int on_home_stream_onclick(void *ctx, void *e) { return 0; }
int widget_layout(void *w) { return 0; }
int widget_paint(void *w, void *c) { return 0; }
int widget_set_prop_str(void *w, const char *k, const char *v) { return 0; }
int widget_destroy(void *w) { return 0; }
int canvas_translate(void *c, int x, int y) { return 0; }
void *canvas_offline_create(unsigned ww, unsigned h, unsigned f) { return 0; }
int canvas_offline_begin_draw(void *c) { return 0; }
int canvas_offline_end_draw(void *c) { return 0; }
int canvas_offline_destroy(void *c) { return 0; }
int song_seconds(void *r) { return -1; }
int toolsTimeItoa(char *s, int sec) { return 0; }
unsigned unflip(unsigned v) { return v; }
unsigned mix(unsigned a, unsigned b, int j, int n) { return a; }
void bar_title_sync(void *win) {}
int bar_title_fits(void *label) { return 1; }
void list_value(void *l, const char *t, const char *v) { (void)l, (void)t, (void)v; }
/* CF_STYLE0: main() passes Minimal's .cf from tools/theme.py ROLE_MACROS, thumb off: the tick cases' (Modern inherits it);
 * CF_STYLE1: Classic's, resolved */
static theme_t shim_themes[3] = {
    { .page_row = 48, .page_detail = 64, .page_title_h = 48, .page_x = 36, .page_cap_y = 4, .page_cap_h = 32, .page_det_y = 36, .page_det_h = 24, .home_top = HOME_TOP, .home_full_row = HOME_FULL_ROW, .home_text_x = HOME_DOT_X + HOME_DOT + HOME_DOT_GAP,
      .home_label_end = { HOME_SPLIT_LABEL_END, HOME_LABEL_END }, .cf = CF_STYLE0 },
    { .page_row = 32, .page_detail = 52, .page_title_h = 0, .page_x = 20, .page_cap_y = -1, .page_cap_h = 28, .page_det_y = 28, .page_det_h = 18, .home_label_end = { 20, 33 }, .home_text_x = 20, .home_full_row = 375,
      .home_pane =
          { { 2, { 0, 100 }, { 0x1c1c1c, 0x1c1c1c } }, { 2, { 0, 100 }, { 0x63717b, 0x63717b } } }, .pane_shadow = 0,
      .pane_shadow_w = 0,
      .pane_shadow_a = { 116, 100, 93, 86, 79, 73, 68, 62, 56, 50, 44, 39, 34, 28, 24, 19, 16, 13, 10, 8, 6, 4, 3, 2, 1 }, .pane_light_a = 0, .pane_note_x = 40, .pane_note_y = 57, .pane_count_y = 185, .pane_count_w = 182,
      .pane_count_px = 27, .cf = CF_STYLE1 },
    { .page_row = 48, .page_detail = 64, .page_title_h = 48, .page_x = 36, .page_cap_y = 4, .page_cap_h = 32, .page_det_y = 36, .page_det_h = 24, .home_full_row = HOME_MODERN_ROW, .home_text_x = HOME_MODERN_TEXT_X,
      .home_label_end = { HOME_MODERN_LABEL_END, HOME_MODERN_LABEL_END }, .cf = CF_STYLE0 } };
static short stub_bar_h = 30;
const theme_t *theme(void) { return &shim_themes[classic]; }
const scheme_t *scheme(void) { /* light is only asked of it in its light half */
    static scheme_t s;
    s.paper = classic == 1 ? 0xffffffu : LIGHT_PAPER * 0x010101u;
    return &s;
}
int client_h(void) { return 320 - stub_bar_h; }
int theme_font(void *w, int role, int was) { return was; } /* the font only: no geometry */
/* The Split pane's painters: the stops asked for, each fill in the colour last set, the count's text. */
#define SHADOW_A(i) ((int[]){ 116, 100, 93, 86, 79, 73, 68, 62, 56, 50, 44, 39, 34, 28, 24, 19, 16, 13, 10, 8, 6, 4, 3, 2, 1 }[i])
static int stops_calls, stops_rect[4], fills;
static const stops_t *stops_arg;
static struct { int x, y, w, h; unsigned color; } fill[1024];
static unsigned fill_color, centred_color;
static char centred_text[32];
static int centred_box[4];
static unsigned centred_px;
void paint_stops(void *c, int x, int y, int ww, int h, const stops_t *s) {
    ++stops_calls; stops_rect[0] = x; stops_rect[1] = y; stops_rect[2] = ww; stops_rect[3] = h; stops_arg = s;
}
int stock_canvas_set_fill_color(void *c, unsigned x) { fill_color = x; return 0; }
int canvas_fill_rect(void *c, int x, int y, int ww, int h) {
    assert(fills < 1024);
    fill[fills].x = x; fill[fills].y = y; fill[fills].w = ww; fill[fills].h = h; fill[fills++].color = fill_color;
    return 0;
}
void draw_centred(void *c, const unsigned *s, unsigned n, const void *r, unsigned px, unsigned color) {
    unsigned i = 0;
    for (; i < n && i + 1 < sizeof centred_text; ++i) centred_text[i] = (char)s[i];
    centred_text[i] = 0, centred_color = color;
    memcpy(centred_box, r, sizeof centred_box), centred_px = px;
}
int ipod_light(void) { return light; }
static int square_art;
int ipod_square_art(void) { return square_art; }
int ipod_home_rockbox(void) { return 0; }
const char *ipod_home_shortcut(void) { return "Rockbox"; }
int home_shortcut_open(void *x, void *e) { return 0; }
int widget_move_resize(void *x, int left, int top, int ww, int h) {
    int *r = (int *)W(x)->raw; /* W_X, W_Y, W_W, W_H */
    r[0] = left; r[1] = top; r[2] = ww; r[3] = h;
    return 0;
}
static int insensitive; /* the widget last made insensitive */
int widget_set_sensitive(void *x, int v) { W(x)->numb = !v; if (!v) insensitive = W(x) - w; return 0; }
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
    const int *r = dst, *q = src;
    if (!(((unsigned short *)b)[6] & 1) && (((unsigned *)b)[0] == 100 || ((unsigned *)b)[0] == 96)) {
        assert(!memcmp(q, (int[]){ 0, 0, ((unsigned *)b)[0], ((unsigned *)b)[1] }, 16));
        memcpy(note_drawn, r, sizeof note_drawn);
        return 0;
    }
    ++draws;
    assert(!memcmp(r, q, 16) && ((unsigned short *)b)[6] & 1); /* 1:1, and marked opaque */
    memcpy(drawn, r, sizeof drawn);
    unsigned fw = ((unsigned *)b)[0] < CF_VIEW_W ? ((unsigned *)b)[0] : CF_VIEW_W, fh = ((unsigned *)b)[1] < CF_VIEW_H ? ((unsigned *)b)[1] : CF_VIEW_H;
    for (unsigned y = 0; y < fh; ++y) memcpy(shown + y * CF_VIEW_W, *(unsigned char **)((char *)b + 0x14) + y * ((unsigned *)b)[2], fw * 4);
    return 0;
}
int stock_home_trampoline(void *win, void *ctx) { return 0; }
/* The songtable writers' stock bodies; during_write runs inside one, as an open mid-scan would. */
static void (*during_write)(void);
static int stock_write(void) { if (during_write) during_write(); return 0; }
int stock_scan_all_trampoline(void *a, void *b) { return stock_write(); }
int stock_scan_folder_trampoline(void *a, void *b) { return stock_write(); }
int stock_delete_song_trampoline(void *a, void *b) { return stock_write(); }
void library_scope_scan(void) {} /* the marker walk: test/library_scope.py */
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
int toolsUpdateDbTable(const char *db, const char *sql) { return 1; } /* only a drop-flag scan writes */
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
       brightness; the neighbours at CF_BRIGHT1, then CF_BRIGHT2, the third out too (CF_THIRD). */
    const unsigned *rest[7] = { flat[0], flat[1], flat[2], art, flat[4], flat[5], flat[6] };
    /* Classic under Square: the corners are the cover too, and a change back rounds them again. */
    square_art = 1;
    render(0, rest);
    for (int y = 0; y < 160; ++y)
        for (int x = 0; x < 160; ++x) assert(px(CF_CX - 80 + x, CF_TOP + y) == art[y * 160 + x]);
    square_art = 0;
    render(0, rest);
    assert(px(CF_CX - 80 + 1, CF_TOP) != art[1]); /* art[0] is black, as the corner's background */
    for (int y = 0; y < 160; ++y)
        for (int x = 0; x < 160; ++x)
            if ((x >= 12 && x < 148) || (y >= 12 && y < 148))
                assert(px(CF_CX - 80 + x, CF_TOP + y) == art[y * 160 + x]);
    assert(px(CF_CX - 81, CF_TOP + 80) != art[80 * 160] && px(CF_CX, CF_TOP - 1) == 0xff000000u);
    /* Each neighbour's visible run on the horizon, sampled in its middle: its colour at its
       brightness, three a side, each run a real slice of cover. */
    for (int j = -3; j <= 3; ++j) {
        if (!j) continue;
        int first = -1, last = -1;
        for (int x = 0; x < CF_VIEW_W; ++x)
            if (slot_of(px(x, CF_TOP + 80)) == j + 3) last = x, first = first < 0 ? x : first;
        assert(last - first >= (abs(j) == 1 ? 40 : abs(j) == 2 ? 18 : 8));
        unsigned p = px((first + last) / 2, CF_TOP + 80), want = colours[j + 3];
        int b = abs(j) == 1 ? CF_BRIGHT1 : CF_BRIGHT2;
        for (int i = 0; i < 3; ++i) assert(abs(ch(p, i) - ch(want, i) * b / 256) <= 1);
    }
    static unsigned without[(CF_VIEW_H + 3) * PITCH];
    memcpy(without, frame, sizeof(frame));
    const unsigned *inner[7] = { 0, flat[1], flat[2], art, flat[4], flat[5], 0 };
    render(0, inner);
    assert(memcmp(without, frame, sizeof(frame))); /* the third out stays at rest, as in the row */
    /* Everything, reflections included, fits the frame with room to spare. */
    for (int x = 0; x < CF_VIEW_W; ++x) assert(!(px(x, 0) & 0xffffff) && !(px(x, CF_VIEW_H - 1) & 0xffffff));
    unsigned centre = px(CF_CX, CF_TOP + 80); /* Light Mode: the same covers over paper */
    light = 1;
    render(0, inner);
    assert(px(0, 0) == (0xff000000u | LIGHT_PAPER * 0x010101u) && px(CF_CX, CF_TOP + 80) == centre);
    light = 0;
    render(0, inner);
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
    assert(px(CF_CX - 80, CF_TOP) == 0xff000000u);
    assert(px(CF_CX - 80, CF_TOP + 160) == 0xff000000u);
    for (int frac = -ONE / 2; frac < ONE / 2; frac += ONE / 16 + 77) {
        render(frac, alone);
        for (int x = 0; x < CF_VIEW_W; ++x) {
            if (slot_of(px(x, CF_TOP + 80)) != 3) continue;
            if (x != CF_CX) continue; /* rounded edges leave a gap before their reflection */
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

int coverflow_hit(int, int, int);
void coverflow_lost_touch(void);
static unsigned batch; /* the dispatch time of moves queued together, else 0 */
static int touch_at(int type, int x, int y, unsigned t) { /* t: when the event was posted */
    int e[16] = {0};
    e[0] = type, e[EVENT_TIME / 4] = t, e[EVENT_X / 4] = x, e[EVENT_Y / 4] = y;
    clock_ms = batch ? batch : t;
    return touch_fn(0, e);
}
/* The glide ends as stock completion would: the index takes the offset, which goes back to 0. */
static void glide_end(widget *s) {
    char *raw = s->raw;
    int n = s->nkids, i = (*(int *)(raw + SLIDE_INDEX) - glide_goal / CF_STRIDE) % n;
    *(int *)(raw + SLIDE_INDEX) = i < 0 ? i + n : i;
    *(int *)(raw + SLIDE_OFFSET) = 0;
    *(void **)(raw + SLIDE_ANIMATOR) = 0;
    changed_fn(0, 0);
}
static int offset(widget *s) { return *(int *)(s->raw + SLIDE_OFFSET); }
static const char *album_caption(widget *covers_view) { /* the first label: the album's */
    for (int i = 0; i < covers_view->nkids; ++i)
        if (!strcmp(w[covers_view->kids[i]].type, "hscroll_label")) return w[covers_view->kids[i]].text;
    return "";
}

/* Touch: the hit test agrees with the drawn covers; taps, drags, flicks, a vertical move, a catch
   and a release never seen. */
static void touches(widget *s, widget *covers_view) {
    const unsigned *ring[7];
    for (int j = 0; j < 7; ++j) ring[j] = flat[j];
    for (int frac = -ONE / 2; frac < ONE / 2; frac += ONE / 8) {
        render(frac, ring);
        for (int y = 1; y < CF_TOP + 160 - 1; ++y) /* the bodies, above the reflections */
            for (int x = 1; x < CF_VIEW_W - 1; ++x) {
                int j = coverflow_hit(frac, x, y), same = 1;
                for (int d = 0; d < 4; ++d) same &= coverflow_hit(frac, x + (d & 1 ? d - 2 : 0), y + (d & 1 ? 0 : d - 1)) == j;
                if (same && j != 99) assert(slot_of(px(x, y)) == j + 3); /* away from the edges */
                if (same && j == 99 && y < CF_TOP + 80) /* black, or a cover too faint to tap; reflections below */
                    for (int i = 0; i < 3; ++i) assert(ch(px(x, y), i) < 64);
            }
    }
    char *raw = s->raw;
    int n = s->nkids, side = CF_CX + CF_X1 * CF_EYE / (CF_EYE + CF_Z1), mid = CF_TOP + 80;
    *(int *)(raw + SLIDE_INDEX) = 3, *(int *)(raw + SLIDE_OFFSET) = 0;
    /* A tap on the right-hand cover glides it to the centre, as the wheel's step; the left-hand one,
       back. A tap on the background does nothing. */
    touch_at(EVT_POINTER_DOWN, side, mid, 0); touch_at(EVT_POINTER_UP_BEFORE, side, mid, 80);
    assert(glides == 1 && glide_goal == -CF_STRIDE && glide_easing == SLIDE_EASING && glide_ms == CF_TAP_MS);
    glide_end(s); assert(*(int *)(raw + SLIDE_INDEX) == 4 && !strcmp(album_caption(covers_view), names[4]));
    touch_at(EVT_POINTER_DOWN, 2 * CF_CX - side, mid, 0); touch_at(EVT_POINTER_UP_BEFORE, 2 * CF_CX - side, mid, 80);
    assert(glides == 2 && glide_goal == CF_STRIDE && paused == 0);
    glide_end(s);
    touch_at(EVT_POINTER_DOWN, 5, 5, 0); touch_at(EVT_POINTER_UP_BEFORE, 5, 5, 80);
    assert(glides == 2 && !timer_fn);
    /* A drag follows the finger, CF_DRAG_PX an album, from where it passed the slop. A flick right glides on, decelerating, to a cover further left in the list. */
    touch_at(EVT_POINTER_DOWN, 100, 100, 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 100 + CF_SLOP - 1, 100, 10); assert(offset(s) == 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 110, 102, 20); assert(offset(s) == 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 110 + CF_DRAG_PX / 2, 102, 40); assert(offset(s) == CF_STRIDE / 2);
    touch_at(EVT_POINTER_MOVE_BEFORE, 110 + CF_DRAG_PX, 102, 60); assert(offset(s) == CF_STRIDE);
    touch_at(EVT_POINTER_UP_BEFORE, 110 + CF_DRAG_PX + 10, 102, 64);
    assert(glides == 3 && glide_easing == EASE_CUBIC_OUT && glide_goal % CF_STRIDE == 0 && glide_goal >= 3 * CF_STRIDE);
    assert(glide_ms > CF_SETTLE_MS && glide_ms <= CF_GLIDE_MAX);
    int albums = glide_goal / CF_STRIDE;
    glide_end(s); assert(*(int *)(raw + SLIDE_INDEX) == ((3 - albums) % n + n) % n);
    /* A drag that rests before it lifts settles on the nearest cover; a third of the way it goes back. */
    *(int *)(raw + SLIDE_INDEX) = 3;
    touch_at(EVT_POINTER_DOWN, 200, 100, 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 20, 100, 20);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 20 - CF_DRAG_PX * 2 / 3, 100, 60);
    touch_at(EVT_POINTER_UP_BEFORE, 200 - 20 - CF_DRAG_PX * 2 / 3, 100, 60 + CF_STILL_MS + 40);
    assert(glides == 4 && glide_goal == -CF_STRIDE && glide_ms == CF_SETTLE_MS);
    glide_end(s);
    touch_at(EVT_POINTER_DOWN, 200, 100, 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 20, 100, 20);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 20 - CF_DRAG_PX / 3, 100, 60);
    touch_at(EVT_POINTER_UP_BEFORE, 200 - 20 - CF_DRAG_PX / 3, 100, 200);
    assert(glides == 5 && glide_goal == 0);
    glide_end(s); assert(*(int *)(raw + SLIDE_INDEX) == 4);
    /* A slow flick still moves on at least to the next cover, never back against itself. */
    touch_at(EVT_POINTER_DOWN, 200, 100, 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 20, 100, 100);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 33, 100, 150); /* 260 px/s */
    touch_at(EVT_POINTER_UP_BEFORE, 200 - 33, 100, 155);
    assert(glides == 6 && glide_goal == -CF_STRIDE);
    glide_end(s); assert(*(int *)(raw + SLIDE_INDEX) == 5);
    /* Up or down is not the covers': no move, no tap. */
    touch_at(EVT_POINTER_DOWN, 200, 100, 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 203, 130, 20); touch_at(EVT_POINTER_MOVE_BEFORE, 260, 160, 40);
    touch_at(EVT_POINTER_UP_BEFORE, 260, 160, 60);
    assert(glides == 6 && offset(s) == 0 && !timer_fn);
    /* A touch while they glide stops them where they are; lifting settles, and opens nothing. */
    touch_at(EVT_POINTER_DOWN, side, mid, 0); touch_at(EVT_POINTER_UP_BEFORE, side, mid, 50);
    assert(glides == 7);
    *(int *)(raw + SLIDE_OFFSET) = -CF_STRIDE / 3;
    touch_at(EVT_POINTER_DOWN, CF_CX, mid, 100); assert(paused == 1);
    touch_at(EVT_POINTER_UP_BEFORE, CF_CX, mid, 150);
    assert(glides == 8 && glide_goal == 0 && glide_ms == CF_SETTLE_MS && !timer_fn);
    glide_end(s);
    /* A release that never arrived: the next key lets go, and so does the next touch. */
    touch_at(EVT_POINTER_DOWN, 200, 100, 0); touch_at(EVT_POINTER_MOVE_BEFORE, 180, 100, 20);
    touch_at(EVT_POINTER_MOVE_BEFORE, 130, 100, 40);
    coverflow_lost_touch(); assert(glides == 9);
    glide_end(s);
    /* A flick whose moves and release were queued behind a frame, dispatched at once, is timed by
       when they were posted. */
    batch = 500;
    touch_at(EVT_POINTER_DOWN, 200, 100, 0); touch_at(EVT_POINTER_MOVE_BEFORE, 180, 100, 20);
    touch_at(EVT_POINTER_MOVE_BEFORE, 140, 100, 40); touch_at(EVT_POINTER_UP_BEFORE, 140, 100, 45);
    batch = 0;
    assert(glides == 10 && glide_easing == EASE_CUBIC_OUT && glide_goal <= -2 * CF_STRIDE && glide_ms > CF_SETTLE_MS);
    glide_end(s);
    /* The centre cover opens its album. */
    touch_at(EVT_POINTER_DOWN, CF_CX, mid, 0); touch_at(EVT_POINTER_UP_BEFORE, CF_CX, mid, 80);
    assert(glides == 10 && timer_fn);
    int album = *(int *)(raw + SLIDE_INDEX);
    run(); assert(!strcmp(title(), names[album]) && !covers_view->visible);
    key(KEY_RETURN); assert(covers_view->visible);
}

/* A theme's corner radius takes effect at the next page open (cf_restyle refills cf_inset). */
static void restyle(void) {
    const unsigned *rest[7] = { flat[0], flat[1], flat[2], art, flat[4], flat[5], flat[6] };
    theme_t *t = (theme_t *)theme();
    close_page(); t->cf.radius = 0; open_page();
    render(0, rest);
    for (int y = 0; y < 160; ++y)
        for (int x = 0; x < 160; ++x) assert(px(CF_CX - 80 + x, CF_TOP + y) == art[y * 160 + x]); /* square */
    close_page(); t->cf.radius = NP_ART_RADIUS; open_page();
    render(0, rest);
    assert(px(CF_CX - 80 + 159, CF_TOP) != art[159]); /* rounded again */
    close_page();
}

/* Minimal's frames, dark and light, at rest and mid-turn: the same bytes whatever other themes add. */
static void pinned(void) {
    const unsigned *ring[7];
    for (int j = 0; j < 7; ++j) ring[j] = j == 3 ? art : flat[j];
    unsigned hash = 2166136261u;
    for (light = 0; light < 2; ++light)
        for (int frac = -ONE / 2; frac < ONE / 2; frac += ONE / 5) {
            render(frac, ring);
            for (unsigned i = 0; i < sizeof(frame) / 4; ++i) hash = (hash ^ frame[i]) * 16777619u;
        }
    light = 0;
    assert(hash == PINNED_FRAMES);
}

/* The flip's frames: the quad from its formula at 0, 45, 90, 135 and 180 degrees, the cover to 90 and
   the back after, narrowed to the axis at 90; the back reads right at the end; the eased time; the side
   covers parting over the same ease. */
void coverflow_flip_render(unsigned *, int, int, const unsigned *const[3]);
int coverflow_flip_eased(int);
static void flip_frames(void) {
    static unsigned f[294 * 375], sides[294 * 375], front[160 * 160], back[285 * 294], flatf[160 * 160], flatb[285 * 294];
    const cf_style_t *cs = &shim_themes[1].cf;
    classic = 1, frame_fail = 0, square_art = 1;
    open_page();
    int H = cs->view_h, top = cs->top;
    for (int y = 0; y < H; ++y) for (int x = 0; x < 375; ++x) sides[y * 375 + x] = 0xff000000u | (unsigned)x;
    for (int i = 0; i < 160 * 160; ++i) front[i] = 0xff000000u | (unsigned)(i % 160) << 8 | (unsigned)(i / 160) << 16, flatf[i] = 0xff0000ffu;
    for (int i = 0; i < 285 * 294; ++i) back[i] = 0xff000000u | (unsigned)(i % 294) << 8 | (unsigned)(i / 294) << 16, flatb[i] = 0xff00ff00u;
    static unsigned plain[294 * 375];
    for (int i = 0; i < 294 * 375; ++i) plain[i] = 0xffff0000u;
    const unsigned *flat_tex[3] = { plain, flatf, flatb }, *tex[3] = { sides, front, back };
    for (int deg = 0; deg <= 180; deg += 45) {
        double E = deg / 180.0, S = sin(deg * M_PI / 180), C = cos(deg * M_PI / 180), hw = 80 + 67 * E, t0 = top + (9 - top) * E, b0 = t0 + 160 + 125 * E;
        double kr = 1 / (1 - 147 * S / 700), kl = 1 / (1 + 147 * S / 700); /* the right and left edge of the cover's own */
        double xr = 187 + hw * C * kr, xl = 187 - hw * C * kl;
        coverflow_flip_render(f, 375, deg * 65536 / 180, flat_tex);
        unsigned want = deg < 90 ? 0xff0000ffu : 0xff00ff00u, other = deg < 90 ? 0xff00ff00u : 0xff0000ffu;
        int first = -1, last = -1;
        for (int x = 0; x < 375; ++x)
            for (int y = 0; y < 294; ++y) {
                assert(f[y * 375 + x] != other);
                if (f[y * 375 + x] == want) { if (first < 0) first = x; last = x; }
            }
        if (deg == 90) { assert(first < 0); continue; } /* edge on, on the axis */
        double lo = xl < xr ? xl : xr, hi = xl < xr ? xr : xl, klo = xl < xr ? kl : kr, khi = xl < xr ? kr : kl;
        assert(fabs(first - lo) <= 1 && fabs(last + 1 - hi) <= 1);
        for (int side = 0; side < 2; ++side) {
            int x = side ? last : first, y0 = -1, y1 = -1;
            double k = side ? khi : klo;
            for (int y = 0; y < 294; ++y) if (f[y * 375 + x] == want) { if (y0 < 0) y0 = y; y1 = y; }
            double wt = 147 + (t0 - 147) * k, wb = 147 + (b0 - 147) * k;
            assert(fabs(y0 - (wt < 0 ? 0 : wt)) <= 2 && fabs(y1 + 1 - (wb > 294 ? 294 : wb)) <= 2);
        }
    }
    /* The cover where the shelf has it, 1:1; the back reading right, 1:1 at the panel's place. */
    coverflow_flip_render(f, 375, 0, tex);
    for (int y = 0; y < 160; ++y) for (int x = 0; x < 160; ++x) assert(f[(top + y) * 375 + 107 + x] == front[y * 160 + x]);
    assert((f[(top + 5) * 375 + 106] & 0xffffffu) == 106);
    coverflow_flip_render(f, 375, 65536, tex);
    for (int y = 0; y < 285; ++y) for (int x = 0; x < 294; ++x) assert(f[(9 + y) * 375 + 40 + x] == back[y * 294 + x]);
    /* The eased time: 90 degrees at half of it, and its own mirror, so Return is the same frames. Six frames on a 45 ms clock. */
    assert(coverflow_flip_eased(0) == 0 && coverflow_flip_eased(135) == 32768 && coverflow_flip_eased(270) == 65536 && coverflow_flip_eased(300) == 65536);
    int was = 0, count = 0;
    for (int ms = 45; ms <= 270; ms += 45, ++count) { int e = coverflow_flip_eased(ms); assert(e > was); was = e; }
    assert(count == 6);
    for (int ms = 0; ms <= 270; ++ms) { assert(abs(coverflow_flip_eased(ms) + coverflow_flip_eased(270 - ms) - 65536) <= 6); assert(coverflow_flip_eased(ms + 1) >= coverflow_flip_eased(ms)); }
    /* ... the bezier's own values, a sixteenth at a time and between */
    assert(abs(coverflow_flip_eased(67) - 8337) <= 100 && abs(coverflow_flip_eased(203) - 57199) <= 100);
    /* The sides part over the same ease: none at the start, 12 at half, 24 at the end, the paper between and under the shelf. */
    const unsigned *none[3] = { sides, 0, 0 };
    for (int half = 0; half < 2; ++half) {
        light = half;
        unsigned paper = half ? 0xffffffffu : 0xff000000u;
        static const struct { int e, part; } at[3] = { { 0, 0 }, { 32768, 12 }, { 65536, 24 } };
        for (int i = 0; i < 3; ++i) {
            int p = at[i].part;
            coverflow_flip_render(f, 375, at[i].e, none);
            for (int y = 0; y < 294; ++y)
                for (int x = 0; x < 375; ++x) {
                    unsigned got = f[y * 375 + x], want = y >= H || (x >= 187 - p && x < 187 + p) ? paper : 0xff000000u | (unsigned)(x < 187 ? x + p : x - p);
                    assert(got == want);
                }
        }
    }
    light = 0;
    square_art = 0;
    close_page();
    classic = 0;
    open_page(); /* Minimal's values again */
    close_page();
}

/* Classic's shelf: side covers facing the centre, their edges meeting there, on a horizon 63%
   down the cover, each flat colour exact out to the third, reflections under the slanted feet. */
static void classic_light_shelf(void) {
    static unsigned f[300 * PITCH], g[300 * PITCH];
    const cf_style_t *c = &shim_themes[1].cf;
    const unsigned *ring[7];
    for (int j = 0; j < 7; ++j) ring[j] = flat[j];
    classic = 1, frame_fail = 0, square_art = 1;
    open_page();
    widget *s = slide();
    int *geo = (int *)s->raw, H = c->view_h, canvas[16] = { 0 }, top = c->top, hz = top + 160 * c->horizon / 100;
    /* The page: one frame as tall as the theme says, its stride kept, drawn 1:1; the captions in their boxes. */
    assert(frames == 1 && geo[3] == H && slide_menu_item_width(s) + *(int *)(s->raw + SLIDE_SPACER) == c->stride);
    *(int *)(s->raw + SLIDE_INDEX) = 3; *(int *)(s->raw + SLIDE_OFFSET) = 0;
    coverflow_paint(s, canvas);
    assert(drawn[2] == CF_VIEW_W && drawn[3] == H);
#define F(x, y) f[(y) * PITCH + (x)]
    for (unsigned i = 0; i < sizeof(f) / 4; ++i) f[i] = 0x12345678u;
    coverflow_render(f, PITCH, 0, ring);
    for (int y = 0; y < 300; ++y)
        for (int x = 0; x < PITCH; ++x) assert((y < H && x < CF_VIEW_W) == (F(x, y) != 0x12345678u));
    /* The outer edge, its height and where it sits on the horizon, a cover at a time; no shading. */
    static const int out[3] = { 121, 154, 188 }, tall[2] = { 166, 164 };
    int prev = 0, h1 = 0;
    for (int j = 1; j <= 3; ++j) {
        int edge = -1, rows = 0, first = -1;
        for (int x = CF_CX; x < CF_VIEW_W; ++x)
            if (F(x, hz) == colours[3 + j]) edge = x;
        assert(abs(edge + 1 - CF_CX - out[j - 1]) <= 1);
        if (j > 1 && j < 3) assert(abs(edge + 1 - CF_CX - prev - 33) <= 1);
        prev = edge + 1 - CF_CX;
        for (int y = 0; y < H; ++y) /* most of the row: the reflection stays under that */
            if (ch(F(edge, y), 0) + ch(F(edge, y), 1) + ch(F(edge, y), 2) >= 256) first = first < 0 ? y : first, ++rows;
        if (j < 3) assert(abs(rows - tall[j - 1]) <= 2);
        if (j == 1) h1 = rows;
        assert(abs((hz - first) * 100 / rows - 63) <= 2);
        for (int x = 0; x < CF_VIEW_W; ++x) assert(F(x, H - 1) == 0xff000000u); /* no reflection cut */
    }
    assert(h1);
    /* Top and bottom edges of the first cover meet within 3 px of the centre column. */
    int xs[2] = { CF_CX + 84, CF_CX + 118 }, hs[2] = { 0, 0 };
    for (int i = 0; i < 2; ++i)
        for (int y = 0; y < H; ++y) hs[i] += F(xs[i], y) == colours[4];
    assert(hs[1] > hs[0]);
    assert(abs((xs[0] - CF_CX) - hs[0] * (xs[1] - xs[0]) / (hs[1] - hs[0])) <= 3);
    /* The centre cover is square at 58 to 218 on the screen under the 26 px bar. */
    assert(top + 26 == 58 && F(CF_CX, top - 1) == 0xff000000u && F(CF_CX, top) == colours[3] && F(CF_CX, top + 159) == colours[3]);
    /* Its reflection: 46 rows, from 35% of the cover, never brighter going down, all in the frame. */
    int rows = 0, last = 256;
    for (int y = top + 160; y < H; ++y) {
        int v = ch(F(CF_CX, y), 0);
        assert(v <= last);
        if (y == top + 160) assert(abs(v - 255 * 35 / 100) <= 3);
        rows += v > 0, last = v;
    }
    assert(rows >= 45 && rows <= 46);
    /* The side covers' reflections start at their own feet, lower towards the outer edge. */
    int foot[2];
    for (int i = 0; i < 2; ++i) {
        foot[i] = 0;
        for (int y = hz; y < H; ++y) foot[i] = F(xs[i], y) == colours[4] ? y + 1 : foot[i];
        assert(slot_of(F(xs[i], foot[i] + 1)) == 4 && F(xs[i], foot[i] + 1) != colours[4]);
    }
    assert(foot[1] > foot[0]);
    /* The hit test follows the horizon; the third cover is a target. */
    assert(coverflow_hit(0, CF_CX, top - 1) == 99 && coverflow_hit(0, CF_CX, top) == 0);
    assert(coverflow_hit(0, CF_CX, top + 159) == 0 && coverflow_hit(0, CF_CX, top + 160) == 99);
    assert(coverflow_hit(0, CF_CX + 100, hz) == 1 && coverflow_hit(0, CF_CX + 140, hz) == 2 && coverflow_hit(0, 373, hz) == 3);
    assert(coverflow_hit(0, 2 * CF_CX - 1 - 373, hz) == -3);
    /* Mid-turn every step changes only a little, including at the fourth cover's edge of reach and
       where the centre changes hands. */
    const unsigned *next[7];
    for (int j = 0; j < 7; ++j) next[j] = ring[(j + 1) % 7];
    int worst = 0;
    for (int fr = -ONE / 2; fr + ONE / 256 < ONE / 2; fr += ONE / 256) {
        coverflow_render(f, PITCH, fr, ring);
        coverflow_render(g, PITCH, fr + ONE / 256, ring);
        int d = differ(f, g, 24);
        worst = d > worst ? d : worst;
    }
    assert(worst < CF_VIEW_W * CF_VIEW_H / 50);
    coverflow_render(f, PITCH, ONE / 2 - 1, ring);
    coverflow_render(g, PITCH, -ONE / 2, next);
    assert(differ(f, g, 6) < 200);
    /* The light half: the shelf is the scheme's paper, and the reflection fades into it. */
    light = 1;
    coverflow_render(f, PITCH, 0, ring);
    assert(F(0, 0) == 0xffffffffu && F(CF_CX, top) == colours[3]);
    assert(abs(ch(F(CF_CX, top + 160), 2) - 255 * 65 / 100) <= 3 && F(CF_CX, top + 160 + 46) == 0xffffffffu);
    light = 0;
    close_page();
    classic = 0, square_art = 0;
    open_page(); /* Minimal's values again */
    close_page();
#undef F
}

/* Classic's step (coverflow.shelf.swing): at rest, the bytes of its path switched off; mid-way the near edge swells
   to about 173 px (165 at rest) and eases out to 3 px past its rest place, where switched off it reaches about 198 and 48 px. */
static void cf_near_edge(const unsigned *f, int H, int hz, int j, int *reach, int *rows) { /* cover j, on the right */
    int edge = -1, n = 0;
    for (int x = CF_CX; x < CF_VIEW_W; ++x) edge = f[hz * PITCH + x] == colours[j] ? x : edge;
    assert(edge >= CF_CX);
    for (int y = 0; y < H; ++y) { /* most of the row: the reflection stays under that */
        unsigned p = f[y * PITCH + edge];
        n += ch(p, 0) + ch(p, 1) + ch(p, 2) >= 256;
    }
    *reach = edge + 1 - CF_CX, *rows = n;
}
static void cf_step_peak(unsigned *f, int H, int hz, const unsigned *const *ring, int *reach, int *tallest) {
    *reach = *tallest = 0;
    for (int fr = -ONE / 2; fr < ONE / 2; fr += ONE / 128) { /* the cover turning on the right: the centre's, then the next */
        int x, rows;
        coverflow_render(f, PITCH, fr, ring);
        cf_near_edge(f, H, hz, fr < 0 ? 3 : 4, &x, &rows);
        *reach = x > *reach ? x : *reach, *tallest = rows > *tallest ? rows : *tallest;
    }
}
/* The largest change of an edge's speed from one frame to the next (1/16 px), through a follow of 81 ms from rest at
   60 fps: t falling from 1 for the cover moving in, rising from 0 for the one moving out. */
int cf_path_x(int t, int u);
static int cf_step_jerk(int moving_in, int u, int first) { /* from frame first */
    enum { FRAMES = 33 };
    int x[FRAMES + 1], worst = 0;
    for (int i = 0; i <= FRAMES; ++i) {
        double s = i * (1000 / 60.0) / 81;
        int eased = i == FRAMES ? ONE : (int)(ONE * (1 - (1 + s) * exp(-s)));
        x[i] = cf_path_x(moving_in ? ONE - eased : eased, u);
    }
    for (int i = first; i < FRAMES; ++i) {
        int jerk = abs(x[i + 1] - 2 * x[i] + x[i - 1]);
        worst = jerk > worst ? jerk : worst;
    }
    return worst;
}
static void classic_light_step(void) {
    static unsigned f[300 * PITCH], off[300 * PITCH];
    cf_style_t *c = &shim_themes[1].cf;
    const unsigned *ring[7];
    for (int j = 0; j < 7; ++j) ring[j] = flat[j];
    int H = c->view_h, hz = c->top + 160 * c->horizon / 100, rest, rows, reach, tallest;
    assert(c->swing == 1 && c->lift == 20);
    classic = 1, frame_fail = 0, square_art = 1;
    c->swing = 0;
    open_page();
    for (unsigned i = 0; i < sizeof(off) / 4; ++i) off[i] = f[i] = 0;
    coverflow_render(off, PITCH, 0, ring);
    cf_step_peak(f, H, hz, ring, &reach, &tallest);
    assert(reach > 160 && tallest > 190); /* what the swing takes away */
    close_page();
    c->swing = 1;
    open_page();
    for (unsigned i = 0; i < sizeof(f) / 4; ++i) f[i] = 0;
    coverflow_render(f, PITCH, 0, ring);
    assert(!memcmp(f, off, sizeof(f)));
    cf_near_edge(f, H, hz, 4, &rest, &rows);
    cf_step_peak(f, H, hz, ring, &reach, &tallest);
    assert(reach > rest && reach <= rest + 3);
    assert(abs(tallest - 173) <= 1 && abs(rows - 165) <= 1);
    /* no corner in the path: about 3 px a frame moving in, 6 at most moving out, where the near edge's quick start
       settles without a stop and the far edge's start is steep on any path through this short an eye. The landing,
       the last 300 ms: a px or two moving in, under a px moving out, with no dead stop. */
    enum { NEAR = 16 * 80, FAR = -NEAR };
    assert(cf_step_jerk(1, FAR, 1) <= 56 && cf_step_jerk(1, NEAR, 1) <= 28);
    assert(cf_step_jerk(0, NEAR, 1) <= 78 && cf_step_jerk(0, FAR, 1) <= 96);
    assert(cf_step_jerk(1, FAR, 15) <= 34 && cf_step_jerk(1, NEAR, 15) <= 28);
    assert(cf_step_jerk(0, NEAR, 15) <= 4 && cf_step_jerk(0, FAR, 15) <= 12);
    close_page();
    classic = 0, square_art = 0;
    open_page(); /* Minimal's values again */
    close_page();
}

/* A cover's far edge under the widened eye (coverflow.shelf.eye_turn): the near edge's path and height stay, the far edge's
   is the model's, and every rest pose is the bytes it was. */
static void classic_light_eye(void) {
    static unsigned f[300 * PITCH], off[300 * PITCH];
    cf_style_t *c = &shim_themes[1].cf;
    const unsigned *ring[7];
    for (int j = 0; j < 7; ++j) ring[j] = flat[j];
    enum { NEAR = 16 * 80, FAR = -NEAR };
    assert(c->eye_turn == 150);
    classic = 1, frame_fail = 0, square_art = 1;
    c->eye_turn = 0;
    open_page();
    coverflow_render(off, PITCH, 0, ring);
    int near0[5], slot0[3][2];
    for (int i = 1; i < 5; ++i) near0[i] = cf_path_x(i * ONE / 5, NEAR);
    for (int i = 0; i < 3; ++i) slot0[i][0] = cf_path_x((i + 1) * ONE, NEAR), slot0[i][1] = cf_path_x((i + 1) * ONE, FAR);
    close_page();
    c->eye_turn = 150;
    open_page();
    coverflow_render(f, PITCH, 0, ring);
    assert(!memcmp(f, off, sizeof(f)));
    for (int i = 0; i < 3; ++i) /* the slots: both edges, as with no widening */
        assert(cf_path_x((i + 1) * ONE, NEAR) == slot0[i][0] && cf_path_x((i + 1) * ONE, FAR) == slot0[i][1]);
    const double T = 89 * M_PI / 180, s1 = sin(T), c1 = cos(T), E0 = 94, HW = 80, Z1 = 77;
    const double PK = (116 + HW * c1) * E0 / (E0 + Z1 - HW * s1) + 3;
    for (int i = 1; i < 5; ++i) {
        double u = i / 5.0, s = sin(T * u), co = cos(T * u), z0 = Z1 * s / s1 - 20 * u * (1 - u), d0 = E0 + z0 - HW * s;
        double O = u < .45 ? PK - (PK - 80) * (.45 - u) * (.45 - u) / (.45 * .45) : PK - 3 * (u - .45) * (u - .45) / (.55 * .55);
        double E = E0 + 150 * (1 - u * u * u * u), xc = O * d0 / E0 - HW * co, zc = E * d0 / E0 - E + HW * s;
        double far = (xc - HW * co) * E / (E + zc + HW * s), near = (xc + HW * co) * E / (E + zc - HW * s);
        assert(fabs(cf_path_x(i * ONE / 5, FAR) / 16.0 - far) < .5);
        assert(fabs(cf_path_x(i * ONE / 5, NEAR) / 16.0 - near) < .5);
        assert(abs(cf_path_x(i * ONE / 5, NEAR) - near0[i]) <= 2);
    }
    /* A tap mid-turn lands on the cover drawn there, under the wider eye. Down to the horizon: every cover's body,
       above any reflection. */
    int hz = c->top + 160 * c->horizon / 100, hits = 0;
    for (int frac = -ONE / 2; frac < ONE / 2; frac += ONE / 8) {
        coverflow_render(f, PITCH, frac, ring);
        for (int y = 1; y <= hz; ++y)
            for (int x = 1; x < CF_VIEW_W - 1; ++x) {
                int j = coverflow_hit(frac, x, y), same = 1;
                for (int d = 0; d < 4; ++d) same &= coverflow_hit(frac, x + (d & 1 ? d - 2 : 0), y + (d & 1 ? 0 : d - 1)) == j;
                if (!same || j == 99) continue;
                assert(slot_of(f[y * PITCH + x]) == j + 3);
                ++hits;
            }
    }
    assert(hits > 100000);
    close_page();
    classic = 0, square_art = 0;
    open_page();
    close_page();
}

/* Classic's step as a follow: the shares stock's animator gets, as the whole px it writes, against the critically damped
   model, from rest and through ticks that move the goal mid-step, with its speed carried across each. */
float coverflow_follow(float);
int coverflow_follow_aim(int, int, int);
static double follow_x, follow_v; /* the model: px, px/ms */
static void follow_step(double goal, double ms, double tau) {
    double w = 1 / tau, y = follow_x - goal, B = follow_v + w * y, e = exp(-w * ms);
    follow_x = goal + (y + B * ms) * e, follow_v = (B - w * (y + B * ms)) * e;
}
static void classic_light_follow(void) {
    enum { TAU = 81, STRIDE = 110 };
    /* from rest: the share is the model's, starts at no speed, stays inside the goal and ends on it */
    int T = coverflow_follow_aim(-STRIDE, TAU, -1);
    assert(T >= 550 && T <= 650);
    assert(abs((int)(-STRIDE * coverflow_follow((float)(T - 1) / T)) + STRIDE) <= 1); /* the last move: a whole px */
    follow_x = 0, follow_v = 0;
    double last = 0;
    for (int t = 0; t < T; t += 16) {
        double m = (double)t / TAU, want = 1 - (1 + m) * exp(-m), got = coverflow_follow((float)t / T);
        assert(fabs(got - want) < .002 && got >= last && got < 1);
        last = got;
    }
    assert(coverflow_follow(1) == 1);
    /* ticks at these ms after the one before: the whole px against the model, the speed across each */
    static const int gaps[] = { 75, 120, 40, 200, 60 };
    int from = 0, goal = -STRIDE, elapsed = -1;
    follow_x = 0, follow_v = 0;
    T = coverflow_follow_aim(goal - from, TAU, elapsed);
    for (unsigned i = 0; i < sizeof gaps / sizeof *gaps; ++i) {
        double before = 0;
        for (int t = 16; t <= gaps[i]; t += 16) {
            follow_step(goal, 16, TAU);
            int px = (int)(from + (goal - from) * (double)coverflow_follow((float)t / T));
            assert(abs(px - (int)follow_x) <= 1);
            before = (goal - from) * ((double)coverflow_follow((float)t / T) - coverflow_follow((float)(t - 1) / T));
        }
        int at = gaps[i] / 16 * 16;
        from = (int)(from + (goal - from) * (double)coverflow_follow((float)at / T));
        follow_x = from;
        goal -= STRIDE;
        T = coverflow_follow_aim(goal - from, TAU, at);
        double after = (goal - from) * ((double)coverflow_follow(1.0f / T) - coverflow_follow(0));
        assert(fabs(after - before) < .08); /* px a ms: nothing a frame could show */
    }
    /* A reversal carries on a moment, then comes back, with the same speed at the turn. */
    double before = (goal - from) * ((double)coverflow_follow(101.0f / T) - coverflow_follow(100.0f / T));
    assert(before < 0);
    from = (int)(from + (goal - from) * (double)coverflow_follow(100.0f / T)), goal = from + 60;
    T = coverflow_follow_aim(goal - from, TAU, 100);
    assert(fabs((goal - from) * ((double)coverflow_follow(1.0f / T) - coverflow_follow(0)) - before) < .08);
    assert(coverflow_follow(4.0f / T) < 0 && coverflow_follow(1) == 1);
    /* A goal right ahead of a fast cover never overshoots, and a goal where it stands is no division by nothing. */
    T = coverflow_follow_aim(-STRIDE * 8, TAU, -1);
    T = coverflow_follow_aim(-3, TAU, 150);
    for (int t = 0; t < T; t += 8) assert(coverflow_follow((float)t / T) < 1);
    T = coverflow_follow_aim(0, TAU, 100);
    assert(T > 0 && fabs(coverflow_follow(.5f)) < 2);
}

static void depth(void) {
    renderer();
    pinned();
    /* On the page: the slide_menu spans the frame, one album per CF_STRIDE px, with empty children
       under the drawn covers; the captions sit under the covers, over the reflections' tail. */
    frame_fail = 0;
    open_page();
    widget *s = slide();
    char *raw = s->raw;
    int *geo = (int *)raw;
    assert(frames == 1 && geo[0] == 0 && geo[1] == 0 && geo[2] == CF_VIEW_W && geo[3] == CF_VIEW_H);
    assert(s == &w[insensitive]); /* the covers' view takes the touch, never stock's slide_menu */
    assert(slide_menu_item_width(s) + *(int *)(raw + SLIDE_SPACER) == CF_STRIDE);
    for (int i = 0; i < s->nkids; ++i) assert(!w[s->kids[i]].image[0]);
    widget *covers_view = &w[s->parent];
    int labels = 0;
    for (int i = 0; i < covers_view->nkids; ++i) {
        widget *l = &w[covers_view->kids[i]];
        if (strcmp(l->type, "hscroll_label")) continue;
        int *g = (int *)l->raw;
        assert(g[0] == CF_EDGE && g[2] == 375 - 2 * CF_EDGE && g[1] == CF_TEXT_Y + labels * CF_NAME_H && g[1] >= CF_TOP + 160);
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
    assert(covers_view->numb); /* stock hit-tests hidden widgets: the list below takes the touch */
    key(KEY_RETURN); /* back to the same album, still drawn */
    assert(covers_view->visible && !covers_view->numb && *(int *)(raw + SLIDE_INDEX) == 6);
    coverflow_paint(s, canvas);
    touches(s, covers_view);
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

/* Display settings' Cover Flow Sort (coverflow_sort_step): each press takes the next order, saved and
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
    /* Past either end an artist's shelf gives, never half a cover (it would wrap to the other end),
       and lifting springs back to the end. */
    widget *s = slide();
    int stride = slide_menu_item_width(s) + *(int *)(s->raw + SLIDE_SPACER);
    for (int dir = -1; dir <= 1; dir += 2) {
        int i0 = *(int *)(s->raw + SLIDE_INDEX), end = (dir > 0 ? i0 : i0 - 2) * stride; /* first or last card */
        touch_at(EVT_POINTER_DOWN, 187, 100, 0);
        for (int k = 1; k <= 40; ++k) touch_at(EVT_POINTER_MOVE_BEFORE, 187 + dir * 20 * k, 100, 10 * k);
        int over = (offset(s) - end) * dir;
        assert(over > 0 && over < stride / 2);
        touch_at(EVT_POINTER_UP_BEFORE, 187 + dir * 800, 100, 405);
        assert(glide_goal == end);
        glide_end(s);
    }
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
    /* An ffmpeg that never reached its own exit (killed: out of memory, #169) leaves no marker, so
       the album is tried again at the next open, not shown artless until Refresh Cover Flow. */
    album("Fkill", 0);
    rescan();
    for (int i = 0; i < 2; ++i) {
        open_page();
        assert(size("Fkill") == -1 && !tmp_files());
        close_page();
    }
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

/* ---- The thumb (Classic's covers here): the follower's numbers, as tuned. ---- */
volatile int wheel_raw, wheel_travel, wheel_held;
static int test_wheel_pct = 50;
int wheel_pct(void) { return test_wheel_pct; }
int coverflow_wheel_live(void), coverflow_wheel_poll(int usable, int gated), coverflow_wheel_on(void *);
void coverflow_wheel_land(void), cf_thumb(float *out), cf_thumb_lead(float lead);
enum { T_POS, T_VEL, T_GOAL, T_LEAD, T_RATE, T_ON };
static float th[6];
static int th_pass(int travel, int ms) {
    clock_ms += ms;
    wheel_travel += travel;
    int r = coverflow_wheel_poll(1, 0);
    cf_thumb(th);
    return r;
}
/* The wheel turned at ticks a second going forward (x falls), 17 ms a pass. */
static void th_spin(float ticks, int ms) {
    static float carry;
    for (int t = 0; t < ms; t += 17) {
        carry += ticks * 200 / 36 * 17 / 1000;
        int u = (int)carry;
        carry -= u;
        th_pass(-u, 17);
    }
}
static void th_down(void) { wheel_held = 1; th_pass(0, 50); th_pass(0, 50); }
static void th_rest(void) {
    wheel_held = 0;
    for (int i = 0; i < 400 && (i < 8 || th[T_ON]); ++i) th_pass(0, 17);
    assert(!th[T_ON]);
}
/* One cover alone in the frame, a albums from frac 0's centre, at a lead. */
static void row_alone(unsigned *f, float lead, int a, const unsigned *art) {
    const unsigned *one[15] = { 0 };
    one[a - (int)floorf(lead + .5f) + 7] = art; /* the ring goes round the facing cover */
    cf_thumb_lead(lead);
    coverflow_render(f, PITCH, 0, one + 4);
}
static int col_top(const unsigned *f, int x) {
    for (int y = 0; y < CF_VIEW_H; ++y)
        if (slot_of(f[y * PITCH + x]) >= 0) return y;
    return -1;
}
static void thumb_wheel(void) {
    const unsigned *wide[15], **ring = wide + 4;
    static unsigned f[300 * PITCH], g2[300 * PITCH], tex[15][160 * 160];
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0;
    open_page();
    widget *s = slide(), *covers_view = &w[s->parent];
    char *raw = s->raw;
    int n = s->nkids, canvas[16] = { 0 }, H = shim_themes[1].cf.view_h;
    int *index = (int *)(raw + SLIDE_INDEX), *offset = (int *)(raw + SLIDE_OFFSET);
    top_window = page;
    #define CAPTION(a) (!strcmp(album_caption(covers_view), names[((a) % n + n) % n]))
    for (int i = 0; i < 15; ++i) {
        wide[i] = tex[i];
        for (int k = 0; k < 160 * 160; ++k) tex[i][k] = colours[i % 7];
    }
    assert(coverflow_wheel_live());
    th_pass(0, 17);
    assert(wheel_raw == 2 && !wheel_travel);

    /* The facing point's lead at steady speeds; none at all while slow. */
    static const struct { int rate; float lead; } steady[] = { { 30, 0 }, { 50, 0 }, { 70, 1.7f }, { 90, 3 }, { 110, 4.8f }, { 130, 5.1f } };
    for (unsigned i = 0; i < sizeof steady / sizeof *steady; ++i) {
        th_down();
        th_spin((float)steady[i].rate, 3000);
        assert(fabsf(th[T_LEAD] - steady[i].lead) < .4f);
        th_rest();
    }

    /* Wheel Sensitivity scales the travel: 100% goes twice as far as 50%. */
    test_wheel_pct = 100;
    th_down();
    for (int i = 0; i < 36; ++i) th_pass(-1, 17); /* slow: a burst would meet the cap */
    assert(fabsf(th[T_GOAL] - 2 * 35 * 36 / 200.f / 13) < .01f);
    test_wheel_pct = 50;
    th_rest();

    /* Slow: about 72 units an album, and a cover part-way stays while the thumb does, frames or none,
       its shake inside the dead band moving nothing. The hold's timeout lands it, Return's way or not. */
    th_down();
    int from = *index, before;
    for (int i = 0; i < 18; ++i) th_pass(-2, 100);
    th_pass(0, 200);
    float held = th[T_POS];
    assert(th[T_ON] && held > .35f && held < .65f && *offset == -(int)floorf(held * 110 + .5f));
    assert(fabsf(th[T_GOAL] - 35 * 36 / 200.f / 13) < .01f && th[T_LEAD] == 0); /* the dead band keeps a unit */
    th_pass(1, 17); th_pass(-1, 17); th_pass(1, 17); th_pass(0, 17);
    for (int i = 0; i < 4 * 60; ++i) th_pass(0, 17);
    assert(fabsf(th[T_POS] - held) < .001f && th[T_ON] && *index == from);
    for (int i = 0; i < 80; ++i) th_pass(0, 17);
    assert(!th[T_ON] && *index == (from + 0) % n + (held >= .5f) && !*offset);
    wheel_held = 0;

    /* One album's worth, slowly, lands on it. */
    th_down();
    from = *index;
    for (int i = 0; i < 37; ++i) th_pass(-2, 60);
    th_rest();
    assert(*index == (from + 1) % n && !*offset && CAPTION(from + 1));

    /* A stop from speed: the caption already names where it lands, and speed only falls, from the
       thumb's last frame to rest; with the thumb still down. */
    th_down();
    from = *index;
    th_spin(110, 2000);
    char last[64];
    #define KEEP(buf) snprintf(buf, sizeof buf, "%s", album_caption(covers_view))
    KEEP(last);
    float was = fabsf(th[T_VEL]);
    assert(th[T_LEAD] > 3);
    before = *index;
    for (int i = 0; i < 200 && th[T_ON]; ++i) {
        th_pass(0, 17);
        assert(fabsf(th[T_VEL]) <= was + .0005f);
        was = fabsf(th[T_VEL]);
        assert(!strcmp(album_caption(covers_view), last));
        /* once the wheel's stopped, the facing point never passes the album the covers land on */
        float left = th[T_GOAL] - th[T_POS];
        assert(i * 17 <= 60 || th[T_LEAD] * left <= 0 || fabsf(th[T_LEAD]) <= fabsf(left) + 1e-5f);
    }
    assert(!th[T_ON] && th[T_LEAD] == 0 && !strcmp(album_caption(covers_view), last) && *index != before);
    assert(CAPTION(*index));
    wheel_held = 0;
    th_rest();

    /* From slow to full speed and back: nothing snaps, in the facing point or the shift. */
    th_down();
    float top = 0;
    for (int r = 20; r <= 130; r += 2) {
        for (int k = 0; k < 6; ++k) {
            float l0 = th[T_LEAD];
            th_spin((float)r, 17);
            assert(fabsf(th[T_LEAD] - l0) < .12f);
            top = th[T_LEAD] > top ? th[T_LEAD] : top;
        }
    }
    assert(top > 4.5f);
    for (int r = 130; r >= 20; r -= 2)
        for (int k = 0; k < 6; ++k) {
            float l0 = th[T_LEAD];
            th_spin((float)r, 17);
            assert(fabsf(th[T_LEAD] - l0) < .15f);
        }
    th_rest();

    /* Elapsed time, not a frame's step: the same spin at 25 ms a pass comes out alike. */
    th_down();
    for (int t = 0; t < 2000; t += 25) th_pass(-14, 25); /* 100 ticks/s, near enough */
    float slower = th[T_LEAD];
    th_rest();
    th_down();
    th_spin(100, 2000);
    assert(fabsf(th[T_LEAD] - slower) < .4f);

    /* Turning back drops what's queued: the goal within half an album of the covers, the rate 0. */
    assert(th[T_GOAL] - th[T_POS] > 1);
    th_pass(+6, 17);
    assert(fabsf(th[T_GOAL] - th[T_POS]) < .55f && th[T_RATE] < 60);
    float rate = th[T_RATE];
    th_pass(+6, 17);
    assert(th[T_RATE] < rate + 60);
    th_rest();

    /* A contact under 40 ms is nothing (a press only anchors, so its travel here is what a stray
       frame could add); a real one's first frames count once it is real. */
    wheel_held = 0;
    th_pass(0, 100);
    int rest_index = *index;
    wheel_held = 1; th_pass(-30, 20);
    wheel_held = 0; th_pass(0, 20);
    th_pass(-30, 20); th_pass(0, 100);
    assert(!th[T_ON] && *index == rest_index && !*offset);
    wheel_held = 1; th_pass(-30, 20); assert(!th[T_ON]);
    th_pass(0, 40); assert(th[T_ON] && th[T_GOAL] > .3f);
    /* A lift that short while moving doesn't settle; a longer one does. */
    th_spin(80, 400);
    float g0 = th[T_GOAL];
    wheel_held = 0; th_pass(0, 17); th_pass(0, 17);
    assert(th[T_GOAL] == g0);
    wheel_held = 1; th_pass(0, 17); th_pass(-6, 17);
    assert(th[T_GOAL] > g0);
    wheel_held = 0; th_pass(0, 17); th_pass(0, 17); th_pass(0, 17); th_pass(0, 17);
    assert(th[T_GOAL] == floorf(th[T_GOAL]));
    th_rest();

    /* Gated travel moves nothing, and is gone. */
    th_down();
    wheel_travel = -300; clock_ms += 17;
    assert(!coverflow_wheel_poll(1, 1) && !wheel_travel);
    cf_thumb(th);
    assert(!th[T_ON]);
    /* A turn answers that it moved, turned and, once an album, clicked. */
    int clicks = 0, turned = 0, moved = 0;
    for (int i = 0; i < 37 * 4; ++i) {
        int r = th_pass(-2, 60);
        clicks += !!(r & 4), turned += !!(r & 2), moved += !!(r & 1);
    }
    assert(clicks == 4 && turned >= 37 * 4 - 2 && moved >= turned);
    th_rest();

    /* A tick queued as the wheel went raw goes nowhere while the thumb holds the covers. */
    assert(!coverflow_wheel_on(s));
    th_down(); th_pass(-30, 17);
    assert(coverflow_wheel_on(s));
    th_rest();

    /* Centre: at once, on the album the caption names. */
    th_down();
    th_spin(110, 1500);
    char shown_album[64];
    KEEP(shown_album);
    coverflow_wheel_land();
    cf_thumb(th);
    assert(!th[T_ON] && !*offset && !strcmp(album_caption(covers_view), shown_album) && CAPTION(*index));
    th_rest();

    /* A touch during a spin settles first: no drag, no tap, the row runs down to the album named.
       At rest again a tap hits what's drawn. */
    th_down();
    th_spin(110, 1500);
    KEEP(shown_album);
    int g = glides, side = CF_CX + 121 - 3, mid = shim_themes[1].cf.top + 106;
    pressed = 1;
    touch_at(EVT_POINTER_DOWN, 200, mid, 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 280, mid, 20);
    th_pass(0, 17);
    assert(!wheel_raw && th[T_ON]);
    wheel_held = 0;
    float o0 = (float)*offset;
    for (int i = 0; i < 200 && th[T_ON]; ++i) th_pass(0, 17);
    assert(!th[T_ON] && (float)*offset != o0 && !*offset && !strcmp(album_caption(covers_view), shown_album));
    touch_at(EVT_POINTER_UP_BEFORE, 280, mid, 2000);
    assert(glides == g && !timer_fn);
    pressed = 0;
    th_pass(0, 17);
    assert(wheel_raw == 2);
    from = *index;
    for (int j = 0; j <= 3; ++j) assert(coverflow_hit(0, CF_CX + (j ? 121 + 33 * (j - 1) - 3 : 0), mid) == j);
    touch_at(EVT_POINTER_DOWN, side, mid, 3000); touch_at(EVT_POINTER_UP_BEFORE, side, mid, 3080);
    assert(glides == g + 1 && glide_goal == -shim_themes[1].cf.stride);
    glide_end(s);
    /* A touch's glide still running when the thumb turns: the covers take over from where it is
       and go on to its goal. */
    *(void **)(raw + SLIDE_ANIMATOR) = 0;
    th_pass(0, 17);
    static int fake_anim[64];
    fake_anim[ANIM_X_TO / 4] = -2 * shim_themes[1].cf.stride;
    *(void **)(raw + SLIDE_ANIMATOR) = fake_anim;
    *offset = -20;
    int gone = animators_gone;
    th_down(); th_pass(-6, 17);
    assert(animators_gone == gone + 1 && !*(void **)(raw + SLIDE_ANIMATOR) && th[T_GOAL] > 2 && th[T_POS] < .5f);
    wheel_held = 0;
    th_rest();

    /* Leaving: another window, a dialog, a window animating, the screen dark or locked (not usable),
       the finger down, each lands the covers and gives the wheel back, dropping travel. */
    for (int exit_kind = 0; exit_kind < 4; ++exit_kind) {
        th_down();
        th_spin(80, 300);
        assert(wheel_raw == 2);
        KEEP(shown_album);
        int use = 1;
        if (exit_kind == 0) top_window = &w[1];
        if (exit_kind == 1) animating = 1;
        if (exit_kind == 2) use = 0;
        if (exit_kind == 3) pressed = 1;
        wheel_travel = -40;
        clock_ms += 17;
        coverflow_wheel_poll(use, 0);
        cf_thumb(th);
        assert(!wheel_raw && !wheel_travel);
        if (exit_kind != 3) assert(!th[T_ON] && !*offset);
        wheel_held = 0;
        top_window = page, animating = 0;
        for (int i = 0; i < 200 && th[T_ON]; ++i) th_pass(0, 17);
        pressed = 0;
        assert(!strcmp(album_caption(covers_view), shown_album));
        th_pass(0, 17);
        assert(wheel_raw == 2);
    }

    /* The row. At rest and on the path with no lead the frame is the shelf's, byte for byte; a tiny
       lead draws the same covers; a full lead shows more of them, narrower, all in the frame. */
    #define R(buf, fr) do { for (unsigned i_ = 0; i_ < sizeof(buf) / 4; ++i_) buf[i_] = 0x12345678u; coverflow_render(buf, PITCH, fr, ring); } while (0)
    for (int frac = -ONE / 2; frac < ONE / 2; frac += ONE / 8) {
        cf_thumb_lead(0);
        R(f, frac);
        cf_thumb_lead(-1);
        R(g2, frac);
        assert(!memcmp(f, g2, sizeof f));
        cf_thumb_lead(1e-4f);
        R(g2, frac);
        int differ = 0;
        for (int y = 0; y < H; ++y)
            for (int x = 0; x < CF_VIEW_W; ++x) differ += abs(ch(f[y * PITCH + x], 1) - ch(g2[y * PITCH + x], 1)) > 40;
        
        assert(differ < H * 8); /* a cover's edge, a row of a px at most, either side */
    }
    /* One mapping of the art across a cover, at rest and in the row: with each texel's column in its
       low byte and its cover in the next, a tiny lead puts the same texel in each column, give or take
       one or two. Alone, cover 1's near half is about 1.22 times as wide as its far half (art_persp),
       where the plane's own would be 2.76; the outlines at rest stay the bytes they were. */
    {
        static unsigned idx[15][160 * 160];
        const unsigned *iring[15], *one[15] = { 0 };
        for (int i = 0; i < 15; ++i) {
            iring[i] = idx[i];
            for (int k = 0; k < 160 * 160; ++k) idx[i][k] = 0xff000000u | (unsigned)(i + 1) << 8 | (unsigned)(k % 160);
        }
        int hz = shim_themes[1].cf.top + 160 * shim_themes[1].cf.horizon / 100, same = 0, near = 0, far = 0;
        cf_thumb_lead(0);
        coverflow_render(f, PITCH, 0, iring + 4);
        cf_thumb_lead(1e-4f);
        coverflow_render(g2, PITCH, 0, iring + 4);
        for (int x = 0; x < CF_VIEW_W; ++x) {
            unsigned a = f[hz * PITCH + x], b = g2[hz * PITCH + x];
            if ((a >> 8 & 255) != (b >> 8 & 255) || !(a >> 8 & 255)) continue; /* another cover, by an edge */
            ++same;
            assert(abs((int)(a & 255) - (int)(b & 255)) <= 2);
        }
        assert(same > CF_VIEW_W * 9 / 10);
        one[8] = idx[8];
        cf_thumb_lead(0);
        coverflow_render(f, PITCH, 0, one + 4);
        for (int x = 0; x < CF_VIEW_W; ++x)
            if ((f[hz * PITCH + x] >> 8 & 255) == 9) ++*((f[hz * PITCH + x] & 255) < 80 ? &far : &near);
        assert(far && fabsf((float)near / (float)far - 1.22f) <= .1f);
        unsigned hash = 2166136261u;
        for (int frac = -ONE / 2; frac < ONE / 2; frac += ONE / 5) {
            R(f, frac);
            for (unsigned i = 0; i < sizeof f / 4; ++i) hash = (hash ^ f[i]) * 16777619u;
        }
        assert(hash == 0xe9548ed4u);
    }
    int spans = 0, prev = -1, rows = shim_themes[1].cf.top + 90;
    cf_thumb_lead(5);
    R(f, 0);
    for (int y = 0; y < 300; ++y)
        for (int x = 0; x < PITCH; ++x) assert((y < H && x < CF_VIEW_W) == (f[y * PITCH + x] != 0x12345678u));
    for (int x = 0; x < CF_VIEW_W; ++x) {
        int j = slot_of(f[rows * PITCH + x]);
        spans += j >= 0 && j != prev;
        prev = j >= 0 ? j : prev;
    }
    
    assert(spans >= 9);
    /* The row's numbers, on one cover at a time so nothing hides its edges. Below the row a cover's
       edges are its pose's, lead * row_shift px on: linear in the lead. */
    const cf_style_t *cs = &shim_themes[1].cf;
    int fr, to;
    #define SPAN() do { fr = to = -1; for (int x = 0; x < CF_VIEW_W; ++x) if (slot_of(f[rows * PITCH + x]) >= 0) to = x, fr = fr < 0 ? x : fr; } while (0)
    #define SMOOTH(v) ((v) >= 1 ? 1.f : (v) * (v) * (3 - 2 * (v)))
    static const float below[] = { .1f, .25f, .4f, .8f };
    for (unsigned i = 0; i < sizeof below / sizeof *below; ++i) {
        float shift = below[i] * cs->row_shift;
        int t = (int)(below[i] * ONE);
        row_alone(f, below[i], 0, tex[7]);
        SPAN();
        assert(fabsf((float)(fr - CF_CX) - (shift - cf_path_x(t, 1280) / 16.f)) <= 2);
        assert(fabsf((float)(to - CF_CX) - (shift - cf_path_x(t, -1280) / 16.f)) <= 2);
    }
    /* The facing cover forms with the lead: shrunk, the shift shrinking with it. */
    for (int a = 1; a <= 3; ++a) {
        float v = (float)(a * 100 - cs->row_from) / (float)(cs->row_full - cs->row_from);
        float sh = 1 - SMOOTH(v) * cs->row_shrink / 100, tall = 0;
        row_alone(f, (float)a, a, tex[a + 7]);
        SPAN();
        for (int y = 0; y < H; ++y) tall += f[y * PITCH + fr + 40] == colours[a];
        assert(fabsf(tall - 160 * sh) <= 2);
        assert(fabsf((float)(fr - CF_CX) - (float)(a * cs->row_shift - 80) * sh) <= 2);
    }
    /* Away from the focus at a full lead: thinner, spaced by row_out past row_flat, and no smaller
       there; a cover's far side keeps (100 - row_key)% of the keystone it has at rest. */
    float sh = 1 - cs->row_shrink / 100.f, shift16 = (float)(3 * cs->row_shift * 16);
    static const int away[] = { 2, 1, -2 };
    int outer[3] = { 0 }, slope = 0;
    for (int i = 0; i < 3; ++i) {
        int t = 3 - away[i], tz = t * 100 > cs->row_flat ? cs->row_flat / 100 : t;
        float v = (float)t * 100 / (float)cs->row_edge, e = SMOOTH(v);
        float near = (float)cf_path_x(tz * ONE, 1280) + (float)((t - tz) * cs->row_out * 16);
        float wide16 = (float)(cf_path_x(tz * ONE, 1280) - cf_path_x(tz * ONE, -1280)) * (1 - e * cs->row_width / 100);
        row_alone(f, 3, away[i], tex[away[i] + 7]);
        SPAN();
        assert(fabsf((float)(fr - CF_CX) - (shift16 - near) * sh / 16) <= 2);
        assert(fabsf((float)(to - CF_CX) - (shift16 - near + wide16) * sh / 16) <= 2);
        outer[i] = col_top(f, fr + 2);
        if (t == 2) slope = col_top(f, to - 2) - outer[i];
    }
    assert(abs(outer[2] - outer[1]) <= 1);
    row_alone(f, 0, 2, tex[9]);
    SPAN();
    assert(fabsf((float)slope - (float)(col_top(f, fr + 2) - col_top(f, to - 2)) * sh * (1 - cs->row_key / 100.f)) <= 2);
    cf_thumb_lead(-1);
    cf_thumb(th);
    th_pass(0, 17);
    assert(wheel_raw == 2);
    coverflow_paint(s, canvas);
    /* Closed mid-spin with no pass between, the album named is the one kept. */
    th_down();
    th_spin(110, 1000);
    KEEP(shown_album);
    int kept = -1;
    for (int i = 0; i < n; ++i) kept = !strcmp(names[i], shown_album) ? i : kept;
    assert(kept >= 0);
    wheel_held = 0;
    close_page();
    assert(last_album(kept));
    top_window = 0;
    th_pass(0, 17);
    assert(!wheel_raw && !coverflow_wheel_live());

    /* An artist's albums stop at their ends: the first push past kicks the covers out by a fifth of
       a stride, once, they spring back, and the thumb never wraps to the other end. */
    deque *d = _create_deque(0);
    d->at[d->n++] = records[6], d->at[d->n++] = records[8];
    page = &w[nw + 1];
    assert(coverflow_artist(d));
    run();
    s = slide(), top_window = page;
    index = (int *)(s->raw + SLIDE_INDEX), n = s->nkids;
    for (int dir = 1; dir >= -1; dir -= 2) {
        int end = dir > 0 ? n - 1 : 0;
        float past = 0;
        wheel_held = 1, wheel_travel = 0;
        th_pass(0, 50); th_pass(0, 50);
        for (int i = 0; i < 300; ++i) {
            th_pass(-dir * 3, 17);
            float e = (float)(end - *index);
            past = (th[T_POS] - e) * (float)dir > past ? (th[T_POS] - e) * (float)dir : past;
            assert(th[T_ON] ? th[T_GOAL] * (float)dir <= e * (float)dir : 1);
        }
        assert(past > .1f && past <= .21f);
        th_rest();
        assert(*index == end && !*(int *)(s->raw + SLIDE_OFFSET));
    }
    close_page();
    top_window = 0;
    th_pass(0, 17);

    /* Minimal and Modern, as shipped (Minimal's theme.xml): their covers are the thumb's too, and
       give the wheel back as Classic's do. */
    static const cf_style_t shipped0 = CF_SHIPPED0;
    const cf_style_t ticks = shim_themes[0].cf;
    shim_themes[0].cf = shim_themes[2].cf = shipped0;
    for (classic = 0; classic <= 2; classic += 2) {
        open_page();
        s = slide();
        top_window = page;
        assert(coverflow_wheel_live());
        th_pass(0, 17);
        assert(wheel_raw == 2);
        coverflow_paint(s, canvas);
        /* Their row settles onto the shelf with no pop: a tiny lead draws the rest frame's covers,
           the third out included (CF_THIRD). */
        for (int frac = -ONE / 2; frac < ONE / 2; frac += ONE / 8) {
            cf_thumb_lead(-1);
            R(f, frac);
            cf_thumb_lead(1e-4f);
            R(g2, frac);
            int differ = 0;
            for (int y = 0; y < shipped0.view_h; ++y)
                for (int x = 0; x < CF_VIEW_W; ++x) differ += abs(ch(f[y * PITCH + x], 1) - ch(g2[y * PITCH + x], 1)) > 40;
            assert(differ < shipped0.view_h * 8);
        }
        cf_thumb_lead(-1);
        close_page();
        top_window = 0;
        th_pass(0, 17);
        assert(!wheel_raw);
    }
    shim_themes[0].cf = shim_themes[2].cf = ticks;
    classic = 0;
    pressed = 1, frame_fail = 0, square_art = 0;
}

/* ---- Classic's thumb on the shipped numbers (theme.xml's coverflow.shelf): the cap at every
   sensitivity, the letters after the dwell and back, and a long spin's landing. ---- */
static int initial_of(int i) { char c = names[i][0]; return c >= 'a' && c <= 'z' ? c - 32 : c; }
static void thumb_shipped(void) {
    static const cf_style_t shipped = CF_SHIPPED;
    const cf_style_t fixture = shim_themes[1].cf;
    shim_themes[1].cf = shipped;
    shim_themes[1].cf.jump_ticks = 0; /* letters off for the cap: its spin outlasts jump_ms */
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0;
    open_page();
    widget *s = slide(), *covers_view = &w[s->parent];
    int n = s->nkids, *index = (int *)(s->raw + SLIDE_INDEX);
    top_window = page;
    th_pass(0, 17);
    assert(wheel_raw == 2);
    #define AT() ((float)*index + th[T_GOAL]) /* the goal in albums, wherever SLIDE_INDEX is */

    /* The cap: top_speed albums a second at every Wheel Sensitivity, whatever the thumb does past it
       (th_spin's ticks are 50%'s: 100 is 400 ticks/s at 200%, 57 albums/s uncapped). No letters. */
    static const int pcts[] = { 50, 100, 200 };
    for (int k = 0; k < 3; ++k) {
        test_wheel_pct = pcts[k];
        int letters0 = letters;
        th_down();
        th_spin(100, 340);
        float from = AT(), peak = 0;
        for (int i = 0; i < 15; ++i) {
            th_spin(100, 17);
            peak = fabsf(th[T_VEL]) > peak ? fabsf(th[T_VEL]) : peak;
        }
        float moved = AT() - from;
        moved -= (float)n * floorf(moved / (float)n + .5f);
        moved = moved < 0 ? moved + (float)n : moved; /* a small library wraps */
        assert(moved > 20 * .255f * .9f && moved < 20 * .255f * 1.05f);
        assert(peak < .0205f); /* albums a ms */
        assert(letters == letters0);
        th_rest();
    }
    test_wheel_pct = 50;
    close_page();
    shim_themes[1].cf = shipped;
    open_page();
    s = slide(), covers_view = &w[s->parent], n = s->nkids, index = (int *)(s->raw + SLIDE_INDEX);
    top_window = page;
    th_pass(0, 17);

    /* At jump_at for jump_ms, letters: each re-centres the row, at rest, on a letter's first album,
       its letter shown; on in the album order, one a jump_ticks of travel. */
    th_down();
    int letters0 = letters, t = 0, prev = -1, spread = 0, canvas[16] = { 0 };
    while (letters == letters0 && t < 2000) th_spin(150, 17), t += 17;
    assert(t >= shipped.jump_ms && t <= shipped.jump_ms + 300);
    for (int i = 0; i < 30; ++i) {
        int was = letters;
        if (i) th_spin(150, 17);
        if (letters == was && i) continue;
        assert(th[T_POS] == 0 && th[T_GOAL] == 0 && th[T_LEAD] == 0 && th[T_ON]);
        assert(CAPTION(*index) && (*index == 0 || initial_of(*index) != initial_of(*index - 1)));
        assert(letter_shown == (unsigned)initial_of(*index) || (!letter_shown && *index == 0));
        assert(prev < 0 || *index > prev || *index == 0); /* forward, wrapping to the first */
        prev = *index;
        /* a frame decodes two covers at most, nearest first; the poll asks for frames until done */
        int l0 = loads;
        for (int f = 0; f < 10; ++f) {
            int l1 = loads;
            coverflow_paint(s, canvas);
            assert(loads - l1 <= 2);
        }
        spread += loads - l0 > 2;
    }
    assert(spread);
    assert(letters >= letters0 + 3);
    /* Once in, letters while the thumb turns on from fast_at: a re-grip (a still moment, then a slow
       turn) keeps them, no album moving, and the cap's speed again goes on at once, no second dwell. */
    #define LANDED() (th[T_POS] == 0 && th[T_GOAL] == 0 && CAPTION(*index) && \
                      (*index == 0 || initial_of(*index) != initial_of(*index - 1)))
    int was = letters;
    th_spin(0, 34);
    for (int i = 0; i < 8; ++i) th_spin(50, 17), assert(th[T_GOAL] == 0 && th[T_POS] == 0);
    assert(letters > was);
    was = letters;
    th_spin(150, 102);
    assert(letters >= was + 2 && LANDED());
    /* A moderate turn (40 ticks/s, under half the cap's) reads them: one each jump_ticks, each
       landed on its letter's first album. */
    th_spin(40, 102); /* the rate falls to it in rate_down */
    was = letters;
    for (int i = 0; i < 59; ++i) {
        int before = letters;
        th_spin(40, 17);
        assert(letters - before <= 1 && (letters == before || LANDED()));
    }
    assert(letters >= was + 40 / shipped.jump_ticks - 1 && letters <= was + 40 / shipped.jump_ticks + 1);
    /* Under fast_at: album by album again under the thumb, on from the letter's album, never back. */
    th_spin(15, 102);
    int jumped = letters, from = *index;
    th_spin(15, 600);
    float on = AT() - (float)from;
    on -= (float)n * floorf(on / (float)n + .5f);
    assert(letters == jumped && on > .3f && on < 3);
    th_rest();
    assert(CAPTION(*index) && !*(int *)(s->raw + SLIDE_OFFSET));
    /* A lift in letters lands on the letter shown, its first album. */
    th_down();
    for (t = 0; letters == jumped && t < 2000; t += 17) th_spin(150, 17);
    for (was = letters; letters == was;) th_spin(150, 17);
    from = *index;
    assert(LANDED() && letter_shown == (unsigned)initial_of(from));
    th_rest();
    assert(*index == from && !*(int *)(s->raw + SLIDE_OFFSET));
    jumped = letters;
    #undef LANDED
    /* A pass a ms after the last moves the goal as one a frame long: the same ticks, as far. */
    float by[2];
    for (int k = 0; k < 2; ++k) {
        th_down();
        for (int i = 0; i < 20; ++i) th_pass(-10, 16);
        float a = AT();
        th_pass(-10, k ? 1 : 16);
        by[k] = AT() - a;
        th_rest();
    }
    assert(by[0] > .1f && fabsf(by[0] - by[1]) < 1e-4f && letters == jumped);

    /* Over 2000 albums under jump_at (no letters), then a lift: SLIDE_INDEX takes the whole
       albums, so the follower's numbers stay small and it lands exactly, on the album named. */
    th_down();
    float went = 0, last = AT(), most = 0;
    for (int i = 0; i < 7600; ++i) {
        th_spin(70, 17);
        float d = AT() - last;
        went += d - (float)n * floorf(d / (float)n + .5f);
        last = AT();
        most = fabsf(th[T_GOAL]) > most ? fabsf(th[T_GOAL]) : most;
    }
    assert(went > 2000 && most < 70 && letters == jumped);
    char named[64];
    snprintf(named, sizeof named, "%s", album_caption(covers_view));
    th_rest();
    assert(!strcmp(album_caption(covers_view), named) && CAPTION(*index));
    assert(!*(int *)(s->raw + SLIDE_OFFSET) && th[T_POS] == 0 && th[T_GOAL] == 0);

    /* Under jump_at, however long: albums, never letters. */
    th_down();
    int l0 = letters;
    th_spin(60, 2000);
    assert(letters == l0);
    th_rest();
    /* A re-grip's dip under jump_at, still over fast_at, drains the dwell rather than dropping it:
       letters come before a dwell started again after the dip would bring them. */
    th_down();
    int tl = 0;
    th_spin(150, 255), tl += 255;
    th_spin(40, 102), tl += 102;
    assert(letters == l0);
    while (letters == l0 && tl < 2000) th_spin(150, 17), tl += 17;
    assert(letters > l0 && tl < 255 + 102 + shipped.jump_ms);
    th_rest();

    close_page();
    top_window = 0;
    th_pass(0, 17);
    shim_themes[1].cf = fixture;
    #undef AT
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
    /* Only a cold open (the albums queried, or art to build) touches /mnt/data/q2-cf-trace, and
       removes it when it ends; a warm one leaves even a stale one alone. */
    mkdir(PEQ_ROOT "/mnt/data", 0755);
    fclose(fopen(PEQ_ROOT "/mnt/data/q2-cf-trace", "w"));
    open_page();
    assert(calls == 3 && !access(PEQ_ROOT "/mnt/data/q2-cf-trace", 0));
    close_page();
    rescan();
    open_page();
    assert(calls == 3 && access(PEQ_ROOT "/mnt/data/q2-cf-trace", 0));
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

    /* Display settings' Refresh Cover Flow (coverflow_refresh_art) clears the cache and, at the next
       open, queries the albums again and rebuilds every one. Stock's Library covers go too, with
       AWTK's cached bitmaps, as the same names are written again. */
    int before = calls, q = queries, d = drops;
    close_page();
    mkdir(PEQ_ROOT "/mnt/mmc/.sldp", 0755);
    fclose(fopen(PEQ_ROOT "/mnt/mmc/.sldp/x0.jpg", "w"));
    coverflow_refresh_art();
    assert(size("Cover") == -1 && size("None") == -1);
    assert(access(PEQ_ROOT "/mnt/mmc/.sldp/x0.jpg", F_OK) && drops == d + 1);
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
    restyle();
    classic_light_shelf();
    classic_light_step();
    classic_light_eye();
    classic_light_follow();
    thumb_wheel();
    thumb_shipped();
    flip_frames();
    sorting();
    artist_only();
    unknown_card();
    same_name();
    disc_folders();
    audiobooks();
    ffmpeg_art();
    player_cover();
    /* Home: the player's cover for its type, once the player has parsed the current track
       (g_lastcover_url is its path), else the track album's Coverflow thumbnail, else the
       placeholder; reloaded only when the track or the usable cover changes, and only while Home
       is the painted top window. Both themes split the screen, the art itself in the right panel. */
    extern void coverflow_home_art(void *);
    /* The pan's texture is sharpened once, [-1,6,-1]/4 a side (home_sharpen), against its cubic
       B-spline's softness: rounded and clamped a channel, the ends their own neighbours; flat
       colour stays. */
    extern void home_sharpen(unsigned *, int, int);
    unsigned line[5] = { 0xff000000u, 0xffffffffu, 0xff000000u, 0xff515151u, 0xff204080u };
    home_sharpen(line, 5, 1);
    assert(line[0] == 0xff000000u && line[1] == 0xffffffffu && line[2] == 0xff000000u &&
           line[3] == 0xff726a5au && line[4] == 0xff143c8cu);
    unsigned flat[3] = { 0xff123456u, 0xff123456u, 0xff123456u };
    home_sharpen(flat, 3, 1); assert(flat[0] == 0xff123456u && flat[1] == 0xff123456u && flat[2] == 0xff123456u);
    /* Classic's slanted art (slant_render), a cover blue over red on white paper: the near edge the
       side high, unflipped; the far edge SLANT_FAR/256 of it, centred, its top row by coverage; under
       the near edge the mirror, red at about SLANT_REFLECT_A/256, fading to the paper by
       SLANT_REFLECT_PCT of the height. */
    extern void slant_render(unsigned *, int, int, int, const unsigned *, int, unsigned);
    {
        enum { S = 40, R = 50, T = 2 * S };
        static unsigned tex[T * T], fr[S * R];
        for (int i = 0; i < T * T; ++i) tex[i] = i < T * T / 2 ? 0xffff0000u : 0xff0000ffu;
        slant_render(fr, S, S, R, tex, T, 255);
#define CH(p, s) ((p) >> (s) & 255)
        assert(CH(fr[0], 16) == 255 && CH(fr[0], 0) < 16 && CH(fr[39 * S], 0) == 255 && CH(fr[39 * S], 16) < 16);
        assert(fr[S - 1] == 0xffffffffu && CH(fr[2 * S + S - 1], 0) > 100 && CH(fr[2 * S + S - 1], 0) < 140 &&
               fr[3 * S + S - 1] == 0xffff0000u && fr[30 * S + S - 1] == 0xff0000ffu);
        assert(CH(fr[40 * S], 8) > 170 && CH(fr[40 * S], 8) < 182 && CH(fr[44 * S], 8) > CH(fr[40 * S], 8) &&
               fr[49 * S] == 0xffffffffu);
#undef CH
    }
    /* Its one frame (coverflow_slant) serves local Now Playing and Classic's Spotify and Internet Radio
       pages: keyed by the cover's url and pixels, so local, Spotify, local each show their own, drawn
       again at each change of page and not while one stays; a new size closes it. */
    extern void coverflow_slant(void *, void *, unsigned), coverflow_slant_close(void);
    {
        enum { S = 40, R = 50 };
        static unsigned seen[3][S * R];
        static const char *const files[] = { PEQ_ROOT "/tmp/coverpic.jpg", PEQ_ROOT "/tmp/q2spot.jpg" };
        void *art[2];
        mkdir(PEQ_ROOT "/tmp", 0755);
        for (int i = 0; i < 2; ++i) {
            FILE *f = fopen(files[i], "w"); fputs("jpg", f); fclose(f);
            art[i] = placed(make(0, "image"), 0, 0, S, R);
            snprintf(W(art[i])->image, sizeof W(art[i])->image, "file://%s", files[i]);
        }
        static const int order[] = { 0, 0, 1, 0 }; /* local, again, Spotify, local */
        int before = frames, loaded = loads; /* coverflow_slant keeps stock's copy loaded: no unload to match */
        for (int k = 0, was = locks, drew = draws; k < 4; ++k) {
            coverflow_slant(art[order[k]], (void *)1, 255);
            assert(locks == was + (k != 1) && draws == drew + 1 && frames == before + 1);
            was = locks, drew = draws;
            unsigned *into = seen[k < 2 ? 0 : k - 1];
            for (int y = 0; y < R; ++y) memcpy(into + y * S, shown + y * CF_VIEW_W, S * 4);
        }
        assert(memcmp(seen[0], seen[1], sizeof seen[0]) && !memcmp(seen[0], seen[2], sizeof seen[0]));
        placed(art[0], 0, 0, S + 2, R + 2);
        coverflow_slant(art[0], (void *)1, 255);
        coverflow_slant_close();
        assert(frames == before);
        loads = loaded;
    }
    assert(mmap((void *)(MCL_POS & ~4095), 4096, PROT_READ | PROT_WRITE,
                MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0) != MAP_FAILED);
    deque queue = {0};
    shim_queue = &queue;
    home_art = make(0, "image");
    frame_fail = 1; /* no pan frame: covers show still (as a 16-bit or too long one), the image named */
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
    /* the player reuses the file for each track: its cached bitmap is dropped, not the last track's shown */
    assert(!strcmp(dropped, player));
    /* a paint drops its own invalidations: the whole screen repaints after it */
    assert(!wm_repaints && timer_fn && (timer_fn(0), wm_repaints == 1));
    /* Square, portrait and landscape still covers keep their proportions, just cover the panel and
       centre on it. */
    static const int covers[][4] = { { 160, 160, 290, 290 }, { 206, 290, 206, 290 },
                                     { 300, 200, 435, 290 }, { 100, 400, 188, 752 } };
    for (unsigned i = 0; i < 4; ++i) {
        art_w = covers[i][0], art_h = covers[i][1];
        *(volatile int *)MCL_POS = 1; coverflow_home_art(win); *(volatile int *)MCL_POS = 0; coverflow_home_art(win);
        int want[4] = { HOME_SPLIT_W + (375 - HOME_SPLIT_W - covers[i][2]) / 2, (290 - covers[i][3]) / 2,
                        covers[i][2], covers[i][3] };
        for (int k = 0; k < 4; ++k) assert(geo[k] == want[k]);
    }
    art_w = art_h = 160;
    /* The art's paint is clipped to the panel on screen (canvas origin at the art, window at y 30)
       from the background hook to the border hook; other widgets keep the clip. */
    extern void coverflow_home_clip(void *, void *, int);
    static char lcd[0x100]; /* the canvas's LCD: the pane saves and restores its fill */
    int canvas[32] = { geo[0], 30 + geo[1] }, full[4] = { 0, 0, 375, 320 }, want[4] = { HOME_SPLIT_W, 30, 375 - HOME_SPLIT_W, 290 };
    *(void **)((char *)canvas + CANVAS_LCD) = lcd;
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
       HOME_MODERN_ROW in Modern, labels ending before the chevron) and hides the art. Minimal's labels
       start after the dot, Modern's at HOME_MODERN_TEXT_X. */
    extern void coverflow_home_layout(void);
    home_list = make(0, "list_view");
    widget *sv = make(home_list, "scroll_view"), *row = make(sv, "view"), *label = make(row, "hscroll_label"),
           *tap = make(row, "image"), *all[] = { home_list, sv, row, tap };
    for (int i = 0; i < 4; ++i) *(int *)(all[i]->raw + W_W) = 375;
    int dot_x = HOME_DOT_X + HOME_DOT + HOME_DOT_GAP;
    *(int *)(label->raw + W_X) = dot_x;
    home_mode = 1;
    coverflow_home(win, 0);
    for (int i = 0; i < 4; ++i) assert(*(int *)(all[i]->raw + W_W) == (i < 2 ? 375 : HOME_FULL_ROW));
    assert(*(int *)(label->raw + W_W) == HOME_FULL_ROW - dot_x - HOME_LABEL_END && !W(home_art)->visible);
    before = loads;
    coverflow_home_art(win); /* hidden: nothing loads */
    assert(loads == before);
    home_mode = 0;
    coverflow_home_layout();
    for (int i = 0; i < 4; ++i) assert(*(int *)(all[i]->raw + W_W) == HOME_SPLIT_W);
    assert(*(int *)(label->raw + W_W) == HOME_SPLIT_W - dot_x - HOME_SPLIT_LABEL_END && W(home_art)->visible);
    coverflow_home_art(win);
    assert(loads == before + 2 && !strcmp(art, player)); /* home_tex tries it, then the still fill */
    classic = 2; /* Modern halves the screen: the 160px cover fills 290px from 187 + (188 - 290) / 2 */
    coverflow_home_layout();
    for (int i = 0; i < 4; ++i) assert(*(int *)(all[i]->raw + W_W) == HOME_SPLIT_W);
    assert(*(int *)(label->raw + W_X) == HOME_MODERN_TEXT_X &&
           *(int *)(label->raw + W_W) == HOME_SPLIT_W - HOME_MODERN_TEXT_X - HOME_MODERN_LABEL_END);
    coverflow_home_art(win);
    static const int modern_geo[4] = { 136, 0, 290, 290 };
    assert(loads == before + 4 && !strcmp(art, player) && !memcmp(geo, modern_geo, sizeof modern_geo));
    home_mode = 1;
    coverflow_home_layout();
    for (int i = 0; i < 4; ++i) assert(*(int *)(all[i]->raw + W_W) == (i < 2 ? 375 : HOME_MODERN_ROW));
    assert(*(int *)(label->raw + W_W) == HOME_MODERN_ROW - HOME_MODERN_TEXT_X - HOME_MODERN_LABEL_END && !W(home_art)->visible);
    classic = 0; home_mode = 0;
    coverflow_home_layout();
    assert(*(int *)(label->raw + W_X) == dot_x && !memcmp(geo, split, sizeof split) && loads == unloads);
    /* A 26 px bar: the art panel takes the 294 px client height (client_h). */
    stub_bar_h = 26;
    coverflow_home_layout();
    static const int tall[4] = { HOME_SPLIT_W, 0, 375 - HOME_SPLIT_W, 294 };
    assert(!memcmp(geo, tall, sizeof tall));
    stub_bar_h = 30;
    coverflow_home_layout();
    assert(!memcmp(geo, split, sizeof split));
    /* The pane's counts: asked once, off the UI thread, published by the timer; 0 is an answer, a failure
     * is asked again at the next Home; Full and Carousel ask nothing. */
    classic = 1; home_mode = 1; shim_themes[1].pane_icon_x = 43; coverflow_home(win, 0);
    int (*counts)(const void *) = timer_fn;
    timer_fn = 0; count_calls = 0;
    assert(!counts(0) && !count_calls);
    home_mode = 0;
    #define COUNTS_DONE() do { int r; while ((r = counts(0)) == 8) usleep(1000); assert(!r); } while (0)
    ++library_gen; COUNTS_DONE();
    int asked = count_calls; /* the songs and the albums */
    assert(asked == 2 && !pthread_equal(count_thread_seen, pthread_self()));
    COUNTS_DONE(); assert(count_calls == asked);
    ++library_gen; count_answer = -1; COUNTS_DONE(); assert(count_calls == asked + 2);
    COUNTS_DONE(); assert(count_calls == asked + 4);
    count_answer = 0; COUNTS_DONE(); assert(count_calls == asked + 6);
    COUNTS_DONE(); assert(count_calls == asked + 6);
    count_answer = 704; ++library_gen; COUNTS_DONE(); assert(count_calls == asked + 8);
    classic = 0; timer_fn = 0; shim_themes[1].pane_icon_x = 0;
    /* Classic (shim_themes[1]): no cover shows the pane instead of default_album_home. The art spans the
     * panel and paints the pane's stops over all of it, its light (white, alpha 40 at the left, 0 at the
     * right edge, a band per alpha), the shadow down its left edge (black, the theme's alpha per column),
     * the note (its image, the theme's place) and the queue's count, "12 Songs", at 27 px, centred on its
     * 24 px line box at the theme's place. Classic's own pane is flat (light and shadow 0): here they
     * are on, as a theme may state them. */
    shim_themes[1].pane_light_a = 40; shim_themes[1].pane_shadow_w = 25;
    unsigned queued = queue.n;
    classic = 1; home_mode = 0; stub_bar_h = 26; coverflow_home_layout();
    queue.n = 12; *(volatile int *)MCL_POS = 0; cover_fail = 1; coverflow_home_art(win);
    static const int pane[4] = { HOME_SPLIT_W, 0, 375 - HOME_SPLIT_W, 294 };
    assert(!*art && !memcmp(geo, pane, sizeof pane));
    *(unsigned *)(lcd + LCD_FILL_COLOR) = 0x12345678;
    fills = 0; stops_calls = 0; memset(note_drawn, 0, sizeof note_drawn); coverflow_home_clip(home_art, canvas, 0);
    assert(stops_calls == 1 && !memcmp(stops_rect, (int[]){ 0, 0, 375 - HOME_SPLIT_W, 294 }, 16) &&
           stops_arg == &shim_themes[1].home_pane[0]); /* the dark half's (light 0) */
    for (int i = 0, x = 0; i < 40; x += fill[i++].w) {
        assert(fill[i].x == x && fill[i].h == 294 && fill[i].color >> 24 == 40u - i && (fill[i].color & 0xffffff) == 0xffffff);
        assert(fill[i].w == 4 || fill[i].w == 5);
        if (i == 39) assert(x + fill[i].w == 375 - HOME_SPLIT_W);
    }
    for (int i = 0; i < 25; ++i) {
        const typeof(*fill) *f = &fill[40 + i];
        assert(f->x == i && f->w == 1 && f->h == 294 && (int)(f->color >> 24) == SHADOW_A(i) && !(f->color & 0xffffff));
    }
    assert(fills == 65 && !strcmp(centred_text, "12 Songs"));
    assert(!memcmp(note_drawn, (int[]){ 40, 57, 100, 115 }, 16));
    assert(!memcmp(centred_box, (int[]){ 0, 185, 182, 24 }, 16) && centred_px == 27);
    assert((centred_color & 0xffffff) == 0xffffff); /* light 0, the dark half: the count white, raw */
    assert(fill_color == 0x12345678); /* the LCD's fill back */
    light = 1; fills = 0; coverflow_home_clip(home_art, canvas, 0);
    assert(!(centred_color & 0xffffff)); /* the light half: black, which ui_ink draws as the white paper */
    assert(stops_arg == &shim_themes[1].home_pane[1]); /* and its own pane */
    light = 0;
    note_fail = 1; fills = 0; coverflow_home_clip(home_art, canvas, 0); note_fail = 0;
    assert(fills == 65 && !strcmp(centred_text, "12 Songs")); /* a missing image: the rest still draws */
    *centred_text = 0; queue.n = 0; coverflow_home_art(win); fills = 0; coverflow_home_clip(home_art, canvas, 0);
    assert(!*centred_text && fills); /* no queue: the note alone */
    /* A panning cover or a still one: no pane. */
    cover_fail = 0; queue.n = queued; coverflow_home_layout(); coverflow_home_art(win);
    stops_calls = 0; fills = 0; *(unsigned *)(lcd + LCD_FILL_COLOR) = 0x12345678; coverflow_home_clip(home_art, canvas, 0);
    assert(!stops_calls && *art);
    /* Where the left column meets the cover: the pane's own shadow over it, from the panel's edge, whatever the art's
     * own size and place, all the client's rows; the fill back */
    assert(fills == 25 && fill_color == 0x12345678); /* the shadow alone: the light is the pane's */
    for (int i = 0; i < 25; ++i)
        assert(fill[i].x == HOME_SPLIT_W - geo[0] + i && fill[i].y == -geo[1] && fill[i].w == 1 && fill[i].h == 294 &&
               (int)(fill[i].color >> 24) == SHADOW_A(i) && !(fill[i].color & 0xffffff));
    shim_themes[1].pane_light_a = shim_themes[1].pane_shadow_w = 0; /* Classic's own: flat */
    fills = 0; coverflow_home_clip(home_art, canvas, 0);
    assert(!fills);
    classic = 0; stub_bar_h = 30; coverflow_home_layout();
    fills = 0; coverflow_home_clip(home_art, canvas, 0);
    assert(!fills); /* Minimal has no pane shadow to draw */
    assert(!memcmp(geo, split, sizeof split));
    /* A cover that pans: the art's image is empty and fills the panel, the pan's frame drawn over it. */
    frame_fail = 0;
    *(volatile int *)MCL_POS = 0;
    coverflow_home_art(win);
    assert(!*art && !memcmp(geo, split, sizeof split) && loads == unloads);
    int drew = draws;
    coverflow_home_clip(home_art, canvas, 0);
    assert(draws == drew + 1);
    /* A transparent cover pans over the half's paper: the theme's own, else Light Mode's, else black. */
    clear_cover = 1;
    static const struct { int classic, light; unsigned paper; } papers[] = {
        { 1, 1, 0xffffffffu }, { 0, 1, 0xff000000u | LIGHT_PAPER * 0x010101u }, { 0, 0, 0xff000000u } };
    for (int i = 0; i < 3; ++i) {
        classic = papers[i].classic, light = papers[i].light;
        coverflow_home_layout();
        coverflow_home_art(win);
        coverflow_home_clip(home_art, canvas, 0);
        assert(shown[0] == papers[i].paper && shown[100 * CF_VIEW_W + 90] == papers[i].paper);
    }
    clear_cover = 0;

    embedded_first();
    return 0;
}
"""


# Stands in for stock ffmpeg: "ffmpeg" in its output (the last argument) for a .webp source or a
# track under Ffok/, else a failure.
FFMPEG_STUB = r"""#!/bin/sh
for a; do [ "$prev" = -i ] && src=$a; prev=$a; done
case $src in *.webp|*/Ffok/*) printf ffmpeg > "$a";; */Fkill/*) kill -9 $$;; *) exit 1;; esac
"""


def main():
    import argparse, os
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--captures', type=pathlib.Path,
                    help='write the renderer\'s frames (rest, quarter, half, three-quarter turns, one album) as PNGs here')
    a = ap.parse_args()
    # shim_themes stands in for the generated themes[]: each theme's .home_* fields must match the
    # offsets.inc expressions tools/theme.py ROLE_MACROS gives it (0, unset in the shim, left out).
    from theme import ROLE_MACROS, builtins, stops_c, half, accent_default
    def home_fields(text):
        return [re.findall(r'\.(home_\w+) = (\{[^}]*\}|[^,{}\n]+)', t.split('.n_opts')[0]) for t in text]
    nav = [[(k, v) for k, v in (('home_top', m['home_top']), ('home_full_row', m['home_full_row']),
                                ('home_text_x', m['home_text_x']),
                                ('home_label_end', f'{{ {m["home_label_end[0]"]}, {m["home_label_end[1]"]} }}')) if v != '0']
           for m in (ROLE_MACROS['minimal'], ROLE_MACROS['modern'])]
    shim = re.search(r'shim_themes\[3\] = \{(.*?)\} \};', TEST, re.S)[1].split('},\n')
    norm = lambda fs: [[(k, ' '.join(v.split())) for k, v in f] for f in fs]
    assert norm(home_fields(shim[::2])) == norm(nav), 'shim_themes drifted from tools/theme.py ROLE_MACROS'
    page = [tuple(map(int, re.search(r'\.page_row = (\d+), \.page_detail = (\d+), \.page_title_h = (\d+), \.page_x = (\d+), \.page_cap_y = (-?\d+), \.page_cap_h = (\d+), \.page_det_y = (\d+), \.page_det_h = (\d+)', t).groups())) for t in shim]
    assert page == [tuple(t[f] for f in ('page_row', 'page_detail', 'page_title_h', 'page_x', 'page_cap_y', 'page_cap_h', 'page_det_y', 'page_det_h')) for t in builtins()[:len(page)]], 'shim_themes page rows drifted from the theme files'
    # Classic's are its theme.xml's, resolved; home_top 0 is unset in the shim.
    classic_light, tight = builtins()[1], re.sub(r'\s', '', shim[1])
    assert classic_light['home_top'] == 0 and f"home_label_end={{{classic_light['home_label_end[0]']},{classic_light['home_label_end[1]']}}}" in tight
    d = classic_light['schemes'][max(accent_default(classic_light), 0)]  # the halves as emit() writes them
    panes = [half(classic_light, light, d['paper'], d['ink'])['home_pane'] for light in (False, True)]
    assert 'home_pane={' + ','.join(stops_c(p).replace(' ', '') for p in panes) + '}' in tight, 'shim_themes drifted from Classic'
    assert 'pane_shadow_a={' + ','.join(map(str, classic_light['pane_shadow_a'])) in tight, 'shim_themes drifted from Classic'
    for field in ('home_text_x', 'home_full_row', 'pane_shadow', 'pane_shadow_w', 'pane_light_a', 'pane_note_x', 'pane_note_y', 'pane_count_y', 'pane_count_w', 'pane_count_px'):
        assert int(re.search(rf'\.{field}=(\w+)', tight)[1], 0) == classic_light[field], f'shim_themes drifted from Classic: {field}'
    # Each theme's .cf, as designated initialisers, so neither a CF_ROLES change nor a field reorder drifts.
    cf_style = [f'-DCF_STYLE{i}={{ ' + ', '.join(f'.{f[3:]} = {v}' for f, v in m.items() if f.startswith('cf.')) + ' }'
                for i, m in enumerate(({**ROLE_MACROS['minimal'], 'cf.art_persp': builtins()[0]['cf.art_persp']},))]
    # The wheel's numbers are retuned on the device: these cases pin the model, with its own fixed ones.
    # (letters off: thumb_shipped has them on the shipped numbers)
    fixture = {**classic_light, 'cf.ticks_album': 13, 'cf.fast_at': 25, 'cf.fast_full': 120, 'cf.jump_ticks': 0}
    cf_style.append('-DCF_STYLE1={ ' + ', '.join(f'.{f[3:]} = {v}' for f, v in fixture.items() if f.startswith('cf.')) + ' }')
    cf_style.append('-DCF_SHIPPED={ ' + ', '.join(f'.{f[3:]} = {v}' for f, v in classic_light.items() if f.startswith('cf.')) + ' }')
    cf_style.append('-DCF_SHIPPED0={ ' + ', '.join(f'.{f[3:]} = {v}' for f, v in builtins()[0].items() if f.startswith('cf.')) + ' }')
    cf_style.append('-DPINNED_FRAMES=0x055534d4u')
    source = ((ROOT/'patch/offsets.inc').read_text() + (ROOT/'patch/coverflow.c').read_text()).replace('\\\n', '')
    def macro(name):
        line = re.search(r'^#define ' + name + r' (.*)$', source, re.M)[1]
        return ''.join(ast.literal_eval(x) for x in re.findall(r'"(?:[^"\\]|\\.)*"', line))
    # Marker expansion is executed against real SQLite by test/library_scope.py and the MIPS
    # wrapper tests. These base predicates also apply when no directories have markers.
    music_where = macro('MUSIC_WHERE') + '1'
    sql = macro('MUSIC_ALBUM_SQL').replace(',,', ',' + macro('ALBUM_TRACK') + ',') + music_where + macro('ALBUM_GROUP')
    with sqlite3.connect(':memory:') as db:
        db.execute('create table songtable(id,album,songer,fileurl,albumsonger,track,cd_num)')
        paths = ['/mnt/mmc/Audiobooks/Book/01.mp3', '/mnt/mmc/aUdIoBoOkS/02.flac',
                 '/mnt/mmc/Music/book.M4B', '/mnt/mmc/Music/song.flac',
                 '/mnt/mmc/Audiobooks Extra/song.mp3', '/mnt/mmc/Music/book.m4b.mp3',
                 '/mnt/mmc/Podcasts/Show/episode.mp3', '/mnt/mmc/pOdCaStS/untagged.mp3',
                 '/mnt/mmc/Podcasts Extra/song.mp3', '/mnt/mmc/Music/Podcasts/song.mp3']
        db.executemany('insert into songtable values (?,"Shared","Artist",?,"Artist",1,1)', enumerate(paths))
        assert {r[0] for r in db.execute('select id from songtable where '+music_where)} == {3,4,5,8,9}
        rows = db.execute(sql).fetchall()
        assert len(rows) == 1 and rows[0][3] in paths[3:]
        db.execute('delete from songtable where id >= 3')
        assert not db.execute(sql).fetchall()
        db.execute("insert into songtable(id,album,songer,fileurl,albumsonger) values (10,NULL,'Artist','/mnt/mmc/Music/untagged.mp3',NULL)")
        assert db.execute(sql).fetchone()[:2] == (-1, 'Unknown Album')
        db.execute("update songtable set fileurl='/mnt/mmc/Audiobooks/untagged.mp3' where id=10")
        assert not db.execute(sql).fetchall()
        db.execute("update songtable set fileurl='/mnt/mmc/PODCASTS/untagged.mp3' where id=10")
        assert not db.execute(sql).fetchall()
        for path in ('/mnt/mmc/Discovery/weekly/01.flac', '/mnt/mmc/dIsCoVeRy/01.flac'):
            db.execute('update songtable set fileurl=? where id=10', (path,))
            assert db.execute(sql).fetchone()[:2] == (-1, 'Unknown Album')
        for path in ('/mnt/mmc/Discovery Extra/01.flac', '/mnt/mmc/Music/Discovery/01.flac'):
            db.execute('update songtable set fileurl=? where id=10', (path,))
            assert db.execute(sql).fetchone()[:2] == (-1, 'Unknown Album')
        # An album's art track (ALBUM_TRACK): its lowest disc, then track, then path, whatever order its
        # rows went in (the order the files were copied); no track number counts as 0.
        db.execute('delete from songtable')
        album = '/mnt/mmc/Music/Album/'
        for i, (name, track, disc) in enumerate((('d2t1.flac', 1, 2), ('d1t3.flac', 3, 1), ('z.flac', 2, 1), ('a.flac', 2, 1))):
            db.execute('insert into songtable(id,album,songer,fileurl,albumsonger,track,cd_num) values (?,"Album","Artist",?,"Artist",?,?)', (20 + i, album + name, track, disc))
        art = lambda: db.execute(sql).fetchone()[3]
        assert art() == album + 'a.flac'
        db.execute("update songtable set track=1 where id=22")
        assert art() == album + 'z.flac'
        db.execute("update songtable set track=NULL where id=21")
        assert art() == album + 'd1t3.flac'
        # A number outside 0-9999 (a date as the track, or -1) takes its bound: the path stays whole.
        db.execute("update songtable set track=-1 where id=21")
        assert art() == album + 'd1t3.flac'
        db.execute("update songtable set track=20231015 where id=21")
        assert art() == album + 'z.flac'
        db.execute("update songtable set cd_num=-1 where id=20")
        assert art() == album + 'd2t1.flac'

    with tempfile.TemporaryDirectory(prefix='q2-coverflow-check-') as directory:
        tmp = pathlib.Path(directory)
        (tmp/'shim.h').write_text(SHIM_H)
        (tmp/'test.c').write_text(TEST)
        (tmp/'ffmpeg').write_text(FFMPEG_STUB)
        (tmp/'ffmpeg').chmod(0o755)
        binary = tmp/'coverflow_test'
        subprocess.run(['cc', '-m32', '-pthread', '-DPEQ_HOST', f'-DPEQ_ROOT="{tmp}/root"',
                        f'-DART_FFMPEG="{tmp}/ffmpeg"', '-D_GNU_SOURCE', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-result', '-Wno-unused-parameter', '-Wno-unused-function',
                        *cf_style, '-I', str(ROOT/'patch'), '-include', str(tmp/'shim.h'), str(ROOT/'patch/coverflow.c'),
                        str(tmp/'test.c'), '-lm', '-o', str(binary)], check=True)
        env = dict(os.environ)
        if a.captures:  # capture the reflection intensity
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
          ' Home art sources, fit, clip, cache and Split/Full layout in every theme, and Classic\'s pane, passed; Classic\'s slanted art and its one frame\'s key passed.')


if __name__ == '__main__':
    main()
