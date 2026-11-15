#!/usr/bin/env python3
"""Run patch/coverflow.c's art cache on the host: the library query, the modal art build on a real
pthread, the stock art calls and their locks. Built 32-bit (-m32, like test/peq.py's player) so the
stSongInfo pointer offsets hold. The stock calls are stubbed; UI widgets are plain records."""
import ast
import json
import pathlib
import re
import sqlite3
import subprocess
import tempfile
import sys; sys.path.insert(0, sys.path[0] + '/../tools')  # tools/ first
from build import FUNCTIONS, ROOT
from peq import LIBC

PROTOTYPES = {**FUNCTIONS, **LIBC}  # the stock calls the host shims stand in for
# The tick cases' shelf, 1.8.0's Minimal: side covers at coverflow.c's first sine table's 60 degrees (CF_ANGLE)
SHELF_60 = {'cf.x1': 112, 'cf.xs': 48, 'cf.z1': 90, 'cf.zs': 20, 'cf.eye': 300, 'cf.tilt': 60, 'cf.horizon': 50, 'cf.swing': 0}
# The thumb cases' shelf and follower, 1.8.0's Classic: the door's swing, the eye drawn back mid-turn, no arch
SHELF_180 = {'cf.eye': 94, 'cf.tilt': 89, 'cf.x1': 116, 'cf.xs': 34, 'cf.z1': 77, 'cf.zs': 1, 'cf.horizon': 63, 'cf.swing': 1,
             'cf.lift': 20, 'cf.eye_turn': 150, 'cf.art_persp': 13, 'cf.arch_span': 0}

# coverflow.c, coverflow_thumb.c, coverflow_letters.c, coverflow_loader.c and playlist_art.c as one unit, so the shim's
# stubs are defined once
CF_SOURCES = '#include "coverflow.c"\n#include "coverflow_thumb.c"\n#include "coverflow_letters.c"\n#include "coverflow_loader.c"\n#include "playlist_art.c"\n#include "coverflow_position.c"\n'
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
int shim_join(pthread_t, void **);
#define pthread_join shim_join
#define statfs shim_statfs
#define __xstat shim_xstat /* o32's stat: st_size word 12, st_mtime word 16 */
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
int shim_xstat(int, const char *, void *);
int tk_str_start_with(const char *, const char *);
const unsigned *widget_get_text(void *);
int canvas_set_font(void *, const char *, unsigned);
float canvas_measure_text(void *, const unsigned *, unsigned);
""" + ''.join(f'{r} {n}({a});\n' for n in """
canvas_fill_rect getAllAlbum getMusicByAlbum toolsQueryDbTable toolsUpdateDbTable toolsGetUserStop album_row toolsThumbSpecCover toolsGetAlbumCover toolsGetExternCover _create_deque deque_init_copy deque_clear
deque_init _deque_push_back deque_pop_back
deque_assign deque_destroy deque_size deque_at window_create widget_factory
widget_factory_create_widget image_create hscroll_label_create set_hscroll_label_attribute
slide_menu_set_value slide_menu_item_width list_view_create scroll_view_create list_item_create image_set_draw_type
image_base_set_image widget_load_image widget_unload_image widget_set_name widget_use_style
widget_set_text_utf8 widget_restack widget_animator_prop_create widget_animator_prop_set_params widget_animator_start widget_set_visible widget_get_prop_int widget_set_prop_int
widget_get_prop_str widget_on widget_destroy_children widget_invalidate_force
widget_count_children widget_get_child widget_lookup widget_move_resize widget_resize widget_get_canvas widget_get_visible
canvas_get_clip_rect canvas_set_clip_rect widget_set_sensitive widget_get_type tk_strcmp widget_animator_destroy
timer_add timer_remove navigator_back_to_home navigator_back navigator_window_is_exist window_manager_close_window_force navigator_to_with_context bitmap_create_ex bitmap_destroy bitmap_unlock_buffer
bitmap_lock_buffer_for_read bitmap_lock_buffer_for_write bitmap_get_line_length
canvas_draw_image slide_menu_set_spacer window_manager window_manager_get_top_window window_manager_is_animating time_now_ms
widget_to_local widget_animator_pause window_manager_get_pointer_pressed image_manager image_manager_unload_bitmap_by_name image_manager_unload_all
on_home_music_onclick on_home_stream_onclick widget_layout widget_destroy widget_paint canvas_translate widget_set_prop_str
canvas_offline_create canvas_offline_begin_draw canvas_offline_end_draw canvas_offline_destroy
sqlite3_open sqlite3_exec sqlite3_close reset_poweroptions_timer getMusicByPlayList getAllPlayList
""".split() for r, a in [PROTOTYPES[n]]) + r"""
int stock_canvas_set_fill_color(void *, unsigned);
void *shim_calloc(size_t, size_t);
#define calloc shim_calloc
#include <stdio.h>
extern void shim_read(size_t) __attribute__((weak)); /* the tests' clock: half copies take half_ms */
static inline size_t shim_fread(void *p, size_t s, size_t n, FILE *f) {
    if (shim_read) shim_read(s * n);
    return (fread)(p, s, n, f);
}
#define fread(p, s, n, f) shim_fread(p, s, n, f)
#include <semaphore.h>
#include <string.h>
/* The tests' loader: run on a thread of its own, or step by step at each wake-up (shim_wake), and
   a half copy's install takes half_ms on the fake clock (shim_install). */
extern int shim_thread(pthread_t *, void *(*)(void *), void *) __attribute__((weak));
extern int shim_wake(sem_t *) __attribute__((weak));
extern void shim_install(void) __attribute__((weak));
static inline int shim_create(pthread_t *t, const pthread_attr_t *a, void *(*f)(void *), void *x) {
    return shim_thread ? shim_thread(t, f, x) : (pthread_create)(t, a, f, x);
}
static inline int shim_post(sem_t *s) { return shim_wake ? shim_wake(s) : (sem_post)(s); }
/* ponytail: any 25.6 KB memcpy counts as a half copy's install; none other in coverflow.c yet */
__attribute__((weak, noinline)) void *shim_memcpy(void *d, const void *s, size_t n) {
    if (shim_install && n == 80 * 80 * 4) shim_install();
    return (memcpy)(d, s, n);
}
#define pthread_create(t, a, f, x) shim_create(t, a, f, x)
#define sem_post(s) shim_post(s)
#define memcpy(d, s, n) shim_memcpy(d, s, n)
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
#undef pthread_join /* the payload's joins say so (shim_join); the tests' own are libc's */
#undef pthread_create
#undef sem_post
#undef memcpy
int coverflow_home(void *, void *), coverflow_player_cover(const char *, const char *, int, int);
int coverflow_artist(void *albums);
const char *track_name(char *, unsigned, void *);

/* Cache boundary: the duration service has its own tests, test/fw/test_durations.py. */
int duration_seconds(void *song) { return -1; }
void durations_scanned(void) {}
const char *duration_text(char *text, unsigned size, void *song) {
    if (size) text[0] = 0;
    return text;
}

/* Widgets: raw memory first, so the payload's field reads (SLIDE_INDEX) land in it. */
typedef struct { char raw[0x100]; int parent, visible, numb, kids[1024], nkids, bg, fg, right; char type[32], text[160], image[600];
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
static int window_fail; /* no memory for a page */
void *window_create(void *p, int x, int y, int ww, int h) { return window_fail ? 0 : make(0, "window"); }
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
static unsigned clock_ms; /* the fake clock, time_now_ms's */
static int decode_ms; /* a cache file's decode, on that clock */
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
    if (!failed && !themed) clock_ms += decode_ms;
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
int widget_set_text_utf8(void *x, const char *s) { if (s) snprintf(W(x)->text, 160, "%s", s); return 0; } /* stock: NULL keeps the text */
int widget_restack(void *x, unsigned i) { return 0; }
void *widget_animator_prop_create(void *x, unsigned d, unsigned l, int e, const char *p) { return 0; }
int widget_animator_prop_set_params(void *a, double f, double t) { return 0; }
int widget_animator_start(void *a) { return 0; }
int widget_set_visible(void *x, int v, int r) { W(x)->visible = v; return 0; }
int widget_get_prop_int(void *x, const char *k, int d) { return d; }
int widget_set_prop_int(void *x, const char *k, int v) {
    if (!strcmp(k, "style:normal:bg_color")) W(x)->bg = v;
    if (!strcmp(k, "style:normal:text_color")) W(x)->fg = v;
    return 0;
}
/* widget_set_name writes the type's place, so a window's name reads back from there */
const char *widget_get_prop_str(void *x, const char *k, const char *d) {
    return !strcmp(k, "image") ? W(x)->image : !strcmp(k, "name") ? W(x)->type : d;
}
static handler touch_fn, changed_fn; /* the covers' touch and the slide_menu's change */
static handler foreground_fn; /* the page's, as a window over it closes */
unsigned widget_on(void *x, unsigned type, handler f, void *ctx) {
    if (!x) return 0;
    if (type >= EVT_POINTER_DOWN && type <= EVT_POINTER_ABORT) touch_fn = f;
    if (type == EVT_VALUE_CHANGED) changed_fn = f;
    if (type == EVT_WINDOW_FOREGROUND) foreground_fn = f;
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
static int letters, letter_held; /* held: the thumb's, with no LETTER_MS, until a coverflow_letter */
void coverflow_letter(void *x, unsigned letter, unsigned now) { letter_shown = letter, letter_held = 0, letters += letter != 0; }
void coverflow_letter_hold(void *x, unsigned letter) { letter_shown = letter, letter_held = 1, ++letters; }
static void *invalidated; /* the last widget invalidated */
int widget_invalidate_force(void *x, void *y) { wm_repaints += x == &wm_repaints; invalidated = x; return 0; }
static void *wm_stack[4]; /* the window manager's windows, bottom first: none unless a test stacks them */
static int wm_n;
unsigned widget_count_children(void *x) { return x == &wm_repaints ? (unsigned)wm_n : (unsigned)W(x)->nkids; }
void *widget_get_child(void *x, unsigned i) { return x == &wm_repaints ? wm_stack[i] : &w[W(x)->kids[i]]; }
static void *home_art, *home_list; /* iPod Home's art and list; none in the carousel tests */
void *widget_lookup(void *x, const char *n, int r) {
    return !x ? 0 : !strcmp(n, "img_cover_flow") ? x : !strcmp(n, "img_homeart") ? home_art :
           !strcmp(n, "list_view_home") ? home_list : 0;
}
static int home_mode, classic, light; /* home_mode: HOME, 0 Split, 1 Full, 2 Carousel */
int ipod_home(void) { return home_mode; }
int home_lib_on(void) { return 1; }
int row_preview_on(void) { return 1; }
int show_q2pod(void) { return 1; }
int ringnav_hide_folder(void) { return 0; }
int ringnav_hide_stream(void) { return 0; }
int library_search(void) { return 1; }
void library_rows(void *list) {}
void stream_rows(void *list) {}
int tk_str_start_with(const char *s, const char *p) { return !strncmp(s, p, strlen(p)); }
const unsigned *widget_get_text(void *w) { return 0; }
int canvas_set_font(void *c, const char *f, unsigned px) { return 0; }
float canvas_measure_text(void *c, const unsigned *s, unsigned n) { return 0; }
const char *theme_face(const unsigned *s, int n, int *px) { return 0; }
void font_weight_mark(void *w, int bold) {}
int coverflow_position_enabled(void) { return 0; }
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
int widget_set_prop_str(void *x, const char *k, const char *v) {
    if (!strcmp(k, "style:normal:text_align_h")) W(x)->right = !strcmp(v, "right");
    return 0;
}
int widget_destroy(void *w) { return 0; }
int canvas_translate(void *c, int x, int y) { return 0; }
/* Stock's off-screen canvas, when offline_ok: the screen's size and BGRA8888 format, its pixels offline[] */
static int offline_ok;
static unsigned offline[320 * 375];
static char offline_lcd[0x200], offline_canvas[0x100];
void *canvas_offline_create(unsigned ww, unsigned h, unsigned f) {
    if (!offline_ok) return 0;
    assert(ww == 375 && h == 320 && f == LCD_BGRA8888);
    *(void **)(offline_canvas + CANVAS_LCD) = offline_lcd;
    *(int *)(offline_lcd + LCD_W) = 375, *(int *)(offline_lcd + LCD_H) = 320;
    *(int *)(offline_lcd + LCD_LINE_LENGTH) = 375 * 4, *(int *)(offline_lcd + LCD_FORMAT) = LCD_BGRA8888;
    *(void **)(offline_lcd + LCD_OFFLINE_FB) = offline;
    return offline_canvas;
}
int canvas_offline_begin_draw(void *c) { return 0; }
int canvas_offline_end_draw(void *c) { return 0; }
int canvas_offline_destroy(void *c) { return 0; }
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
int widget_resize(void *x, int ww, int h) { return widget_move_resize(x, ((int *)W(x)->raw)[0], ((int *)W(x)->raw)[1], ww, h); }
void *widget_get_canvas(void *x) { return 0; } /* no canvas: the panel keeps its reserve */
static int insensitive; /* the widget last made insensitive */
int widget_set_sensitive(void *x, int v) { W(x)->numb = !v; if (!v) insensitive = W(x) - w; return 0; }
static int clip_rect[4] = { 0, 0, 375, 320 }; /* the canvas clip, screen x, y, w, h */
int canvas_get_clip_rect(void *c, void *r) { memcpy(r, clip_rect, sizeof clip_rect); return 0; }
int canvas_set_clip_rect(void *c, const void *r) { memcpy(clip_rect, r, sizeof clip_rect); return 0; }
const char *widget_get_type(void *x) { return W(x)->type; }
int widget_get_visible(void *x) { return W(x)->visible; }
int tk_strcmp(const char *a, const char *b) { return strcmp(a ? a : "", b ? b : ""); }
int navigator_back_to_home(void) { return 0; }
static int backs, force_closes, played_cls = -1, played_idx = -1;
unsigned playback_loads; /* playback.c's: ringnav_load's count of queue loads */
static void *force_closed; /* the last window closed unanimated */
int navigator_back(void) { return ++backs, 0; }
static int usb_page; /* USB Storage's page open */
int navigator_window_is_exist(const char *name) { return usb_page && !strcmp(name, "usbmode_page"); }
int window_manager_close_window_force(void *m, void *x) { force_closed = x; return ++force_closes, 0; }
/* play_folder's: the page, its context and the queue as it stood */
static char nav_to[32];
static struct { void *dq; int idx, cls, mode; } nav_ctx;
static void *nav_queue[64];
static unsigned nav_n, navs;
int navigator_to_with_context(const char *n, const void *c) {
    snprintf(nav_to, sizeof nav_to, "%s", n);
    memcpy(&nav_ctx, c, sizeof nav_ctx);
    if (!strcmp(n, "playing_page")) played_idx = nav_ctx.idx, played_cls = nav_ctx.cls, ++playback_loads; /* stock's load */
    nav_n = *(const unsigned *)nav_ctx.dq; /* the queue's deque, as below */
    memcpy(nav_queue, (const char *)nav_ctx.dq + sizeof(unsigned), sizeof nav_queue);
    return ++navs, 0;
}
/* slide_menu: square items as high as the menu, plus the spacer. */
int slide_menu_item_width(void *x) { return *(int *)(W(x)->raw + W_H); }
int slide_menu_set_spacer(void *x, int v) { *(int *)(W(x)->raw + SLIDE_SPACER) = v; return 0; }
/* A BGRA8888 pixel, the screen's, in the textures' order (RGBA8888, red low), and back. */
static unsigned texel(unsigned p) { return (p & 0xff00ff00u) | (p & 255u) << 16 | (p >> 16 & 255u); }
/* The frame bitmap (bitmap_t as above) and the canvas: canvas_draw_image keeps what it drew, in the
   textures' order. */
static unsigned drawn_format; /* the last frame drawn's: 3, the screen's BGRA8888, is copied */
static int frames, frame_fail, tex_fail, locks, draws, drawn[4];
static unsigned shown[CF_VIEW_H * CF_VIEW_W];
#undef calloc /* the payload's calloc is this shim; the tests' own is libc's */
void *calloc(size_t, size_t);
static int calloc_fail, more_fail, more_asks; /* no memory at all; none for fx.more's slots alone */
static int tex_slots; /* fx_open's last textures: fx_base's and the placeholder */
static unsigned mem_kb = ~0u, mem_asks; /* /proc/meminfo's MemAvailable (chapters.c's), and its reads */
unsigned mem_available_kb(void) { return ++mem_asks, mem_kb; }
void *shim_calloc(size_t n, size_t size) {
    int more = size == 160 * 160 * 4 && n == 64 - 15; /* fx_window's: FX_POOL past a thumb theme's fx_base */
    more_asks += more;
    if (size == 160 * 160 * 4 && !more) tex_slots = (int)n;
    return calloc_fail || (tex_fail && size == 160 * 160 * 4) || (more_fail && more) ? 0 : calloc(n, size);
}
static int half_ms; /* a half copy's read, on the fake clock */
static int card_us; /* a half copy's read, slept by the loader on a real thread (card()) */
static void card(int us) { __atomic_store_n(&card_us, us, __ATOMIC_SEQ_CST); }
void shim_read(size_t bytes) {
    int us = __atomic_load_n(&card_us, __ATOMIC_SEQ_CST);
    if (bytes != 80 * 80 * 4) return;
    if (us) usleep(us);
    else clock_ms += half_ms;
}
/* The payload's o32 syscalls (cf_syscall), logged: gettid answers 4242; idle_fails refuses the idle
   I/O class. sys_main: one came from the tests' own thread. */
static int sys_log[16][4], sys_n, sys_main, idle_fails;
static pthread_t main_thread;
int cf_syscall(int nr, int a, int b, int c) {
    int *l = sys_log[sys_n++ & 15];
    sys_main |= pthread_equal(pthread_self(), main_thread);
    l[0] = nr, l[1] = a, l[2] = b, l[3] = c;
    return nr == 4222 ? 4242 : nr == 4314 && c == 3 << 13 && idle_fails ? -1 : 0;
}
#define SYS_IS(i, nr, a, b, c) (sys_log[i][0] == (nr) && sys_log[i][1] == (a) && sys_log[i][2] == (b) && sys_log[i][3] == (c))
/* The loader: by default each wake-up reads every copy asked for, its reads off the UI thread's
   clock, as on a thread of its own on a card at once; ld_hold leaves them asked for (cf_ld_step
   reads one); ld_threads runs it on a real thread, card_us each read. */
static int ld_threads, ld_hold, ld_live, ld_started;
void *(*cf_loader(void))(void *);
int cf_ld_step(void), cf_ld(int a);
static void *(*ld_body)(void *);
static void *idle_body(void *x) { return x; }
static void *loader_body(void *x) { __atomic_fetch_add(&ld_live, 1, __ATOMIC_SEQ_CST); ld_body(x); __atomic_fetch_sub(&ld_live, 1, __ATOMIC_SEQ_CST); return 0; }
int shim_thread(pthread_t *t, void *(*f)(void *), void *x) {
    if (f == cf_loader()) ++ld_started, ld_body = f, f = ld_threads ? loader_body : idle_body;
    return pthread_create(t, 0, f, x);
}
int shim_wake(sem_t *s) {
    if (ld_threads) return sem_post(s);
    unsigned c = clock_ms;
    while (!ld_hold && cf_ld_step()) {
    }
    clock_ms = c;
    return 0;
}
void shim_install(void) { clock_ms += half_ms; }
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
static int moves_ok, moves, moved[2][2][4]; /* moves_ok: a draw elsewhere than its source, src and dst kept */
int canvas_draw_image(void *c, void *b, const void *src, const void *dst) {
    const int *r = dst, *q = src;
    if (!(((unsigned short *)b)[6] & 1) && (((unsigned *)b)[0] == 100 || ((unsigned *)b)[0] == 96)) {
        assert(!memcmp(q, (int[]){ 0, 0, ((unsigned *)b)[0], ((unsigned *)b)[1] }, 16));
        memcpy(note_drawn, r, sizeof note_drawn);
        return 0;
    }
    if (moves_ok && memcmp(r, q, 16)) {
        assert(moves < 2 && ((unsigned short *)b)[6] & 1);
        memcpy(moved[moves][0], q, 16), memcpy(moved[moves++][1], r, 16);
        return 0;
    }
    ++draws;
    assert(!memcmp(r, q, 16) && ((unsigned short *)b)[6] & 1); /* 1:1, and marked opaque */
    memcpy(drawn, r, sizeof drawn);
    unsigned fw = ((unsigned *)b)[0] < CF_VIEW_W ? ((unsigned *)b)[0] : CF_VIEW_W, fh = ((unsigned *)b)[1] < CF_VIEW_H ? ((unsigned *)b)[1] : CF_VIEW_H;
    for (unsigned y = 0; y < fh; ++y) memcpy(shown + y * CF_VIEW_W, *(unsigned char **)((char *)b + 0x14) + y * ((unsigned *)b)[2], fw * 4);
    if ((drawn_format = ((unsigned short *)b)[7]) == 3)
        for (unsigned i = 0; i < fh * CF_VIEW_W; ++i) shown[i] = texel(shown[i]);
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
int library_scope_hidden(const char *path) { (void)path; return 0; } /* no markers here */
int coverflow_scan_all(void *, void *);
static void rescan(void) { coverflow_scan_all(0, 0); } /* the library changed, as only a scan changes it */

static int (*timer_fn)(const void *);
unsigned timer_add(int (*f)(const void *), void *ctx, unsigned ms) { timer_fn = f; return 1; }
int timer_remove(unsigned id) { timer_fn = 0; return 0; }
void stop_timer(unsigned *t) { if (*t) timer_remove(*t); *t = 0; } /* navigation.c's */
void rearm(unsigned *t, int (*f)(const void *), unsigned ms) { stop_timer(t); *t = timer_add(f, 0, ms); }
static void run(void) { while (timer_fn) { int (*f)(const void *) = timer_fn; timer_fn = 0; usleep(1000); f(0); } }

/* libcstl: a deque of record pointers; the query stubs fill the staging deque. */
typedef struct { unsigned n; void *at[1024]; } deque; /* a playlist of 300 songs fits */
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

static char records[1024][0x60], names[1024][64], paths[1024][600];
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
static char unknown_record[0x60], untagged_song[0x60], untagged_more[0x60];
static int untagged_two; /* getMusicByAlbum(NULL) finds untagged_more too */
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
/* navigation.c's: a playlist's displayed copy, its spoken songs left out */
int music_records(void *list, int discovery) {
    deque *d = list;
    unsigned k = 0;
    for (unsigned i = 0; i < d->n; ++i) {
        const char *path = *(const char **)((char *)d->at[i] + REC_PATH);
        if (discovery || !path || !spoken_path(path)) d->at[k++] = d->at[i];
    }
    return (int)(d->n = k);
}
int spoken(const char *path) { return path && spoken_path(path); } /* navigation.c's */
static int unknown_queries;
int getMusicByAlbum(const char *a) {
    deque *d = shim_dir;
    d->n = 0;
    if (!a) {
        ++unknown_queries;
        if (unknown_songs) d->at[d->n++] = untagged_song;
        if (untagged_two) d->at[d->n++] = untagged_more;
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
static int rank_year[16]; /* the Year query's year column, each ranked row's (split_row's REC_YEAR) */
/* navigation.c's Year order: newest first (0) or oldest. */
static int year_old;
int ringnav_year_old(void) { return year_old; }
/* navigation.c's Fast Navigation: letters, years and half copies; main turns it on for their tests */
static int big_library;
int ringnav_big_library(void) { return big_library; }
static int artist_view;
int ringnav_artist_view(void) { return artist_view; }
int ringnav_artist_az(void) { return 0; }
/* Stock's row maker for a ranking query (split_row's first four columns): the album named by v[1]
   as a row of its own, added to the staged deque. */
static int rank_n;
int album_row(void *a, int n, char **v, char **c) {
    assert(n == 4 && rank_n < 16);
    char *r = rank_rows[rank_n++];
    memset(r, 0, 0x60);
    for (int j = 0; j < albums; ++j)
        if (!strcasecmp(names[j], v[1])) memcpy(r, records[j], 0x60);
    *(const char **)(r + REC_ALBUM) = v[1]; /* ranked[]'s, which outlives the row */
    ((deque *)shim_dir)->at[((deque *)shim_dir)->n++] = r;
    return 0;
}
int toolsUpdateDbTable(const char *db, const char *sql) { return 1; } /* only a drop-flag scan writes */
int toolsGetUserStop(void) { return 0; }
int msg_counting(void) { return 0; }
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
    /* each row's columns as text, as SQLite hands them: id, album, songer, the art track,
       albumsonger, and YEAR_SQL's year */
    int cols = strstr(sql, "else 0 end) from") ? 6 : 5;
    rank_n = 0;
    for (int i = 0; ranked[i]; ++i) {
        char id[12], year[12];
        snprintf(id, sizeof id, "%d", i + 1), snprintf(year, sizeof year, "%d", rank_year[i]);
        char *v[6] = { id, (char *)ranked[i], "Artist", "/music/x/01.flac", 0, year };
        ((int (*)(void *, int, char **, char **))row)(0, cols, v, 0);
    }
    ++queries;
    return (int)d->n;
}
/* library_lists.c's and navigation.c's: Most Played's counts, per stock record here, and the
 * library lists' article. */
static unsigned played[1024];
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
    order[norder++ % 64] = m == &shim_parse ? 1 : 2;
    assert(!held[m == &shim_play]);
    held[m == &shim_play] = 1;
    return pthread_mutex_lock(m);
}
int shim_unlock(void *m) { held[m == &shim_play] = 0; return pthread_mutex_unlock(m); }
static int write_art(const char *dst, const char *what) {
    snprintf(made[calls++ % 64], 600, "%s", what);
    if (calls - 1 == block_at) { blocked = 1; while (blocked) usleep(1000); }
    FILE *f = fopen(dst, "w"); /* none once the card is gone */
    if (f) fputs(what, f);
    return f && !fclose(f);
}
/* Cover Flow's .jpg.tmp; playlist art's .tmp.jpg, its 52 px small one too. */
static int tmp_dst(const char *dst) { return strstr(dst, ".jpg.tmp") || strstr(dst, ".tmp.jpg"); }
int toolsThumbSpecCover(const char *src, const char *dst, int ww, int h) {
    assert((ww == 160 || ww == 52) && h == ww && held[0] && !held[1] && tmp_dst(dst));
    /* a playlist's own picture, any file the user drops in, is decoded by ffmpeg alone, out of demo */
    assert(!strstr(src, "_explaylist_data"));
    if (strstr(src, ".webp") || strstr(src, "Bad")) return 0; /* stock decodes only JPEG and PNG */
    return write_art(dst, strrchr(src, '/') + 1);
}
static int album_asks; /* each toolsGetAlbumCover, found or not */
int toolsGetAlbumCover(const char *src, const char *dst, int ww, int h) {
    assert(ww == 160 && h == 160 && held[0] && held[1] && tmp_dst(dst));
    ++album_asks;
    return embedded_fails ? 0 : write_art(dst, "embedded");
}
static int extern_found;
int toolsGetExternCover(const char *src, const char *dst, int ww, int h) {
    assert(ww == 320 && h == 320 && !held[0] && !held[1]);
    return extern_found ? write_art(dst, "extern") : 0;
}
static unsigned free_blocks = 1 << 20, card_blocks = 1 << 21, statfs_calls; /* 4 GB free of 8 GB */
int shim_statfs(const char *p, void *out) {
    assert(!strcmp(p, PEQ_ROOT "/mnt/mmc/.coverflow"));
    ++statfs_calls;
    unsigned *s = out;
    s[1] = 4096, s[3] = card_blocks, s[7] = free_blocks; /* MIPS o32 statfs: f_bsize, f_blocks, f_bavail */
    return 0;
}

void *shim_queue;
unsigned char shim_memory[0x800]; /* g_memory_info, which album_memory fills */
unsigned char shim_covertype;
char shim_lastcover[1024], shim_id3[2716];
static widget *page;
/* A payload join has begun: worker_stop sets the cancel, then joins the art worker. */
static volatile int joining;
int shim_join(pthread_t t, void **result) { joining = 1; return pthread_join(t, result); }
/* The album in hand finishes once the cancel is set, however late the main thread gets to stop(); the caller clears
   joining before it starts this. */
static void *release(void *unused) { while (!joining) usleep(1000); blocked = 0; return 0; }
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
/* The renderers draw the screen's BGRA8888 (opaque_frame); the tests hand them and read their frames
   in the textures' order, RGBA8888 with red low: R and B swapped back in each row the render filled
   (all opaque; the guards and any row below left). bgra_frames checks the frames as drawn. */
static void (*const bgra_render)(unsigned *, int, int, const unsigned *const[7]) = coverflow_render;
static void texel_render(unsigned *d, int pitch, int frac, const unsigned *const ring[7]) {
    bgra_render(d, pitch, frac, ring);
    for (unsigned *row = d;; row += pitch) {
        int x = 0;
        while (x < CF_VIEW_W && row[x] >> 24 == 255) ++x;
        if (x < CF_VIEW_W) break;
        for (x = 0; x < CF_VIEW_W; ++x) row[x] = texel(row[x]);
    }
}
#define coverflow_render texel_render
#define ONE 65536
#define PITCH (CF_VIEW_W + 5) /* guard columns, and guard rows below, to catch clipping errors */
static unsigned frame[(CF_VIEW_H + 3) * PITCH], flat[7][160 * 160], art[160 * 160];
static const unsigned colours[7] = { 0xff0000ffu, 0xff00ff00u, 0xffff0000u, 0xff00ffffu, 0xffffff00u, 0xffff00ffu, 0xff0080ffu };
/* A thumb theme's ring, wide's fifteen (cover j - 7 at wide[j]): the seven flat colours round and
   round, as a small library repeats; the seven's ring. */
static const unsigned **flat_ring(const unsigned **wide) {
    for (int j = 0; j < 15; ++j) wide[j] = flat[(j + 3) % 7];
    return wide + 4;
}
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
    /* A drag back to where it began lifts at rest: no glide, and the still frame is asked for (the
       moving one drew the edges' covers). */
    touch_at(EVT_POINTER_DOWN, 200, 100, 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 20, 100, 20);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 50, 100, 40); assert(offset(s) != 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 20, 100, 60); assert(offset(s) == 0);
    invalidated = 0;
    touch_at(EVT_POINTER_UP_BEFORE, 200 - 20, 100, 60 + CF_STILL_MS + 40);
    assert(glides == 5 && invalidated == s);
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
/* As texel_render: the side covers (the shelf's frame) and the back go in as the screen's, the frame
   comes out in the textures' order. */
static void (*const bgra_flip)(unsigned *, int, int, const unsigned *const[3]) = coverflow_flip_render;
static void texel_flip(unsigned *d, int pitch, int e, const unsigned *const tex[3]) {
    static unsigned sides[294 * 375], back[285 * 294];
    for (int i = 0; i < 294 * 375; ++i) sides[i] = texel(tex[0][i]);
    for (int i = 0; tex[2] && i < 285 * 294; ++i) back[i] = texel(tex[2][i]);
    bgra_flip(d, pitch, e, (const unsigned *[3]){ sides, tex[1], tex[2] ? back : 0 });
    for (int y = 0; y < 294; ++y)
        for (int x = 0; x < 375; ++x) d[y * pitch + x] = texel(d[y * pitch + x]);
}
#define coverflow_flip_render texel_flip
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
    const unsigned *wide[15], **ring = flat_ring(wide);
    classic = 1, frame_fail = 0, square_art = 1;
    open_page();
    widget *s = slide();
    int *geo = (int *)s->raw, H = c->view_h, canvas[16] = { 0 }, top = c->top, hz = top + 160 * c->horizon / 100;
    /* The page: one frame as tall as the theme says, its stride kept, drawn 1:1; the captions in their boxes. */
    assert(frames == 1 && geo[3] == H && slide_menu_item_width(s) + *(int *)(s->raw + SLIDE_SPACER) == c->stride);
    *(int *)(s->raw + SLIDE_INDEX) = 3; *(int *)(s->raw + SLIDE_OFFSET) = 0;
    coverflow_paint(s, canvas);
    assert(drawn[2] == CF_VIEW_W && drawn[3] == H && drawn_format == 3); /* the screen's, so stock copies it */
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
    const unsigned *next_wide[15], **next = next_wide + 4;
    for (int j = -4; j <= 10; ++j) next[j] = ring[j + 1 <= 10 ? j + 1 : j - 6];
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
    const unsigned *wide[15], **ring = flat_ring(wide);
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
    const unsigned *wide[15], **ring = flat_ring(wide);
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
                assert(slot_of(f[y * PITCH + x]) == (j + 10) % 7); /* the ring's colours go round */
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

/* The frames as drawn: the screen's BGRA8888, opaque, red at bit 16 (texel_render's, R and B
   swapped); at rest the centre cover's middle texel, swapped. */
static void bgra_frames(void) {
    static unsigned raw[(CF_VIEW_H + 3) * PITCH];
    const unsigned *ring[7];
    for (int j = 0; j < 7; ++j) ring[j] = j == 3 ? art : flat[j];
    for (light = 0; light < 2; ++light) {
        bgra_render(raw, PITCH, ONE / 5, ring);
        render(ONE / 5, ring);
        for (int y = 0; y < CF_VIEW_H; ++y)
            for (int x = 0; x < CF_VIEW_W; ++x) assert(raw[y * PITCH + x] == texel(px(x, y)) && raw[y * PITCH + x] >> 24 == 255);
    }
    light = 0;
    bgra_render(raw, PITCH, 0, ring);
    unsigned a = art[80 * 160 + 80];
    assert(raw[(CF_TOP + 80) * PITCH + CF_CX] == (0xff000000u | (a & 0xff00u) | (a & 255u) << 16 | (a >> 16 & 255u)));
}

/* column() as it was before its fast path (1.7.5): column_fuzz's reference, on a frame and solid
   span of its own, c and paper as the open page has them. */
#define ART_SIZE 160
static unsigned ref_shade(unsigned p, unsigned b) {
    return ((p & 0xff00ffu) * b >> 8 & 0xff00ffu) | ((p & 0xff00u) * b >> 8 & 0xff00u);
}
static void ref_put(unsigned *px, unsigned colour, unsigned cov) {
    unsigned a = *px >> 24, have = a + (a >> 7), w = cov * (256 - have) >> 8;
    if (!a && cov == 256) {
        *px = colour | 0xff000000u;
        return;
    }
    if (!w) return;
    have += w;
    *px = ((*px & 0xffffffu) + ref_shade(colour, w)) | (have > 255 ? 255u : have) << 24;
}
static void ref_column(const cf_style_t *c, unsigned paper, unsigned *d0, int pitch, short *solid, int col, const unsigned *tex, int h, int bright, int inset) {
    if (h < 16) return;
    unsigned *d = d0 + col;
    int top = 256 * c->top + 40960 * c->horizon / 100 - h * c->horizon / 100, bottom = top + h, step = (ART_SIZE << 20) / (h >> 4);
    int ins = h * inset / (ART_SIZE * 256), clipped_top = top + ins, clipped_bottom = bottom - ins;
    int solid_top = solid[0], solid_bottom = solid[1];
    int y = clipped_top >> 8, end = (clipped_bottom + 255) >> 8, last = end < c->view_h ? end : c->view_h;
    int first = y, final = end - 1;
    int v = (((y << 8) + 128 - top) >> 4) * step >> 4;
    if (y < 0) v -= y * step, y = 0;
    unsigned *px = d + y * pitch, wash = ref_shade(paper, 256u - (unsigned)bright);
    while (y < last) {
        if (y >= solid_top && y < solid_bottom) {
            int skip = solid_bottom - y;
            v += skip * step, px += skip * pitch, y = solid_bottom;
            continue;
        }
        int run = y < solid_top && solid_top < last ? solid_top : last;
        for (; y < run; ++y, v += step, px += pitch) {
            int row = v < 0 ? 0 : v >> 16 >= ART_SIZE ? ART_SIZE - 1 : v >> 16;
            unsigned cov = 256;
            if (y == first || y == final) {
                int from = y << 8 > clipped_top ? y << 8 : clipped_top, to = (y + 1) << 8 < clipped_bottom ? (y + 1) << 8 : clipped_bottom;
                cov = (unsigned)(to - from);
            }
            ref_put(px, ref_shade(tex[row * ART_SIZE], (unsigned)bright) + wash, cov);
        }
    }
    int reflect = h * c->reflect / ART_SIZE, stop = (bottom + reflect + 255) >> 8;
    int fade = (256 << 16) / reflect;
    unsigned dim = (unsigned)bright * c->reflect_top >> 8;
    wash = ref_shade(paper, 256u - dim);
    if (stop > c->view_h) stop = c->view_h;
    for (y = (bottom + ins) >> 8, px = d + y * pitch; y < stop; ++y, px += pitch) {
        int from = y << 8 > bottom + ins ? y << 8 : bottom + ins, dist = ((from + ((y + 1) << 8)) >> 1) - bottom;
        if (dist >= reflect) break;
        if (y < 0 || (y >= solid_top && y < solid_bottom)) continue;
        int mirrored = dist * step >> 8 >> 16;
        int row = ART_SIZE - 1 - (mirrored >= ART_SIZE ? ART_SIZE - 1 : mirrored);
        unsigned cov = (unsigned)(((y + 1) << 8) - from) * (256u - (unsigned)(dist * fade >> 16)) >> 8;
        ref_put(px, ref_shade(tex[row * ART_SIZE], dim) + wash, cov);
    }
    int s0 = (clipped_top + 255) >> 8, s1 = clipped_bottom >> 8;
    s0 = s0 < 0 ? 0 : s0, s1 = s1 > c->view_h ? c->view_h : s1;
    if (s1 <= s0) return;
    if (solid_bottom > solid_top && s0 <= solid_bottom && s1 >= solid_top) {
        s0 = s0 < solid_top ? s0 : solid_top;
        s1 = s1 > solid_bottom ? s1 : solid_bottom;
    } else if (s1 - s0 <= solid_bottom - solid_top)
        return;
    solid[0] = (short)s0, solid[1] = (short)s1;
}
void cf_column(unsigned *, int, short *, int, const unsigned *, int, int, int);
/* column() byte for byte its reference, over many random columns in Minimal and Classic, dark and
   light: any height to three times the rest's, brightness (whole half the time), corner inset,
   texture column, solid span, and what lies under it (empty, opaque or part covered). */
static void column_fuzz(void) {
    enum { ROWS = 300, N = 40000 };
    static unsigned a[ROWS * PITCH], b[ROWS * PITCH], tex[160 * 160];
    unsigned r = 12345;
#define RND() (r = r * 1103515245u + 12345u, r >> 8)
    for (int i = 0; i < 160 * 160; ++i) tex[i] = RND() << 8 ^ RND();
    for (classic = 0; classic < 2; ++classic) {
        open_page();
        const cf_style_t *c = &theme()->cf;
        for (light = 0; light < 2; ++light)
            for (int n = 0; n < N; ++n) {
                int col = (int)(RND() % CF_VIEW_W), h = (int)(RND() % (3 * 40960)), bright = RND() & 1 ? 256 : (int)(RND() % 256) + 1;
                int inset = RND() % 3 ? 0 : (int)(RND() % 4000);
                const unsigned *t = tex + RND() % 160;
                short sa[2] = { 0, 0 }, sb[2];
                if (RND() % 3) {
                    int s0 = (int)(RND() % (c->view_h + 1)), s1 = (int)(RND() % (c->view_h + 1));
                    sa[0] = (short)(s0 < s1 ? s0 : s1), sa[1] = (short)(s0 < s1 ? s1 : s0);
                }
                sb[0] = sa[0], sb[1] = sa[1];
                for (int y = 0; y < ROWS; ++y) {
                    unsigned k = RND() % 4, p = RND() << 8 ^ RND();
                    a[y * PITCH + col] = b[y * PITCH + col] = k == 0 ? 0 : k == 1 ? p | 0xff000000u : p;
                }
                ref_column(c, light ? scheme()->paper : 0, a, PITCH, sa, col, t, h, bright, inset);
                cf_column(b, PITCH, sb, col, t, h, bright, inset);
                assert(sa[0] == sb[0] && sa[1] == sb[1]);
                for (int y = 0; y < ROWS; ++y) assert(a[y * PITCH + col] == b[y * PITCH + col]);
            }
        close_page();
    }
#undef RND
    classic = 0, light = 0;
}
#undef ART_SIZE

static void depth(void) {
    renderer();
    pinned();
    bgra_frames();
    column_fuzz();
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
    {   /* Reopened, the covers centre the queue's song's album (#350), not the one last centred; no
           queue, a song with no album tag (a folder play's) or one whose album the list lacks (a
           same-named album in another folder) leaves the last one. A compilation's track, with an artist of its own, still finds its album. */
        void *pos = mmap((void *)(MCL_POS & ~4095), 4096, PROT_READ | PROT_WRITE,
                         MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
        assert(pos != MAP_FAILED);
        deque playing = {0};
        char untagged[sizeof records[0]] = {0}, elsewhere[sizeof records[0]], guest[sizeof records[0]];
        memcpy(elsewhere, records[1], sizeof elsewhere);
        *(char **)(elsewhere + REC_PATH) = "/mnt/mmc/audiobooks/Folder/01.m4b";
        memcpy(guest, records[2], sizeof guest);
        *(char **)(guest + REC_ARTIST) = "A guest";
        int at[5];
        void *songs[5] = { 0, untagged, elsewhere, guest, records[1] };
        for (int k = 0; k < 5; ++k) {
            playing.n = 1; playing.at[0] = songs[k];
            shim_queue = songs[k] ? &playing : 0;
            open_page();
            at[k] = *(int *)(slide()->raw + SLIDE_INDEX);
            close_page();
        }
        assert(at[0] == 6 && at[1] == 6 && at[2] == 6 && at[3] == 2 && at[4] == 1);
        /* A scoped page (an artist's albums) opens where it does with nothing queued, whichever of
           its albums plays. */
        for (int k = 0; k < 2; ++k) {
            deque *theirs = _create_deque(0);
            theirs->at[theirs->n++] = records[6], theirs->at[theirs->n++] = records[8];
            playing.at[0] = records[8];
            shim_queue = k ? &playing : 0;
            page = &w[nw + 1];
            assert(coverflow_artist(theirs));
            run();
            at[k] = *(int *)(slide()->raw + SLIDE_INDEX);
            close_page();
        }
        assert(at[0] == 0 && at[1] == 0); /* B; Tight, the one playing, is 1 */
        shim_queue = 0;
        munmap(pos, 4096);
    }
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

/* Display settings' Cover Flow → Sort (coverflow_sort_step): each press takes the next order, saved and
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
    ranked[0] = "New", ranked[1] = "a", ranked[2] = "Cover", ranked[3] = 0;
    rank_year[0] = 2001, rank_year[1] = 1994, rank_year[2] = 70000; /* past 9999: undated */
    press_sort();
    assert(saved_sort() == 6);
    order_is((const char *const[]){ "New", "A", "Cover", "Folder", "Embedded", "None", "B", "C", "Tight", 0 });
    /* its groups are years, the query's year column; the albums it missed are undated, last */
    assert(coverflow_jump(slide(), 0, 1, &letter) == 1 && letter == (CF_YEAR | 1994));
    assert(coverflow_jump(slide(), 1, 1, &letter) == 1 && letter == CF_YEAR);
    assert(coverflow_jump(slide(), 2, -1, &letter) == 1 && letter == (CF_YEAR | 1994));
    assert(coverflow_jump(slide(), 2, 1, &letter) == albums - 2 && !letter);
    rank_year[0] = rank_year[1] = rank_year[2] = 0;
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

/* ---- The thumb (1.8.0's Classic's covers here): the follower's numbers, as tuned. ---- */
volatile int wheel_raw, wheel_travel, wheel_held;
static int test_wheel_pct = 50;
int wheel_pct(void) { return test_wheel_pct; }
int coverflow_wheel_live(void), coverflow_wheel_poll(int usable, int gated), coverflow_wheel_on(void *);
void coverflow_wheel_land(void), cf_thumb(float *out), cf_thumb_lead(float lead);
enum { T_POS, T_VEL, T_GOAL, T_LEAD, T_RATE, T_ON, T_JUMP, T_LEAN, T_FOLD, T_ENTER, T_FOLD_OFF, T_FOLD_VEL, T_ARCH };
static float th[13];
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
int cf_frame_window(int frac);
static void row_alone(unsigned *f, float lead, int a, const unsigned *art) {
    const unsigned *one[15] = { 0 };
    cf_thumb_lead(lead);
    int sh = cf_frame_window(0); /* the ring goes round the covers the row shows */
    assert(abs(a - sh) <= 7);
    one[a - sh + 7] = art;
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
    int still[16] = { 0 };
    coverflow_paint(s, still);
    assert(coverflow_hit(0, 374, mid) == 99); /* the fourth's sliver, only drawn moving: no tap at rest */
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
    /* ring round the window's middle, as the page has it: the colours go round */
    #define R(buf, fr) do { int sh_ = cf_frame_window(fr); for (int i_ = 0; i_ < 15; ++i_) { wide[i_] = tex[((i_ + sh_) % 7 + 7) % 7]; } \
                            for (unsigned i_ = 0; i_ < sizeof(buf) / 4; ++i_) { buf[i_] = 0x12345678u; } coverflow_render(buf, PITCH, fr, ring); } while (0)
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
       where the plane's own would be 2.76; mid-step frames pinned (still ones: rest_pinned). */
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
        for (int i = 0, sh = cf_frame_window(0); i < 15; ++i) iring[i] = idx[(i + sh + 15) % 15]; /* round its window */
        coverflow_render(g2, PITCH, 0, iring + 4);
        for (int i = 0; i < 15; ++i) iring[i] = idx[i];
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
        assert(hash == 0x40597fadu);
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

/* ---- Going by letters the row is held as the entry left it, the fast row going on: in each built-in
   theme, spinning either way, its frame is the entry's last, cover for cover; every cover on the
   screen is as wide, as far from the next and as keystoned (far:near) as the formed row at speed's
   covers away from its facing point, all leaning against the spin, and stays so through an album's
   slide (none turns towards the eye as the albums pass the facing point off the leading edge); the
   covers reach both edges of the screen; half an album on from one cover is half an album back from
   the next, the same frame; leaning left is leaning right in a mirror. ---- */
void cf_thumb_lean(int lean);
float cf_thumb_hold(int lean, float lead, int frac);
int cf_frame_window(int frac);
int cf_enter_quads(int frac, float lead, float enter, int lean, int *centre, int (*out)[6]);
void cf_thumb_enter(int lean, float lead, float enter), cf_thumb_speed(float vel, float lead_v);
int cf_frame_quads(int frac, int (*out)[6]);
static int quad_cols(const int *q) { /* whether draw_quad draws any column of q: its middle in [xl, xr) */
    int n = 0;
    for (int x = 0; x < CF_VIEW_W && q[5] > 0; ++x) n += (2 * x + 1 - 2 * CF_CX) * 8 >= q[1] && (2 * x + 1 - 2 * CF_CX) * 8 < q[2];
    return n;
}
static void held_row(void) {
    static unsigned f[300 * PITCH], g[300 * PITCH], art[43][160 * 160];
    static const cf_style_t shipped[3] = { CF_SHIPPED0, CF_SHIPPED, CF_SHIPPED2 };
    const cf_style_t fixture = shim_themes[1].cf;
    #define ART(a) (abs(a) <= 21 ? art[(a) + 21] : 0) /* album a */
    for (int i = 0; i < 43; ++i)
        for (int k = 0; k < 160 * 160; ++k) art[i][k] = 0xff000000u | ((unsigned)(i * 160 * 160 + k) * 2654435761u >> 8 & 0xffffff);
    square_art = 1, classic = 1;
    for (int ti = 0; ti < 3; ++ti) {
        shim_themes[1].cf = shipped[ti];
        open_page();
        const cf_style_t *cs = &shim_themes[1].cf;
        int mirror[2][43][6] = { { { 0 } } };
        for (int dir = -1; dir <= 1; dir += 2) {
            int lean = -dir, frac = dir * 13107, q[15][6], lq[15][6], centre;
            float lead = dir * 3.2f; /* formed: the row at speed's covers away from it all alike */
            cf_thumb_speed(dir * .0139f, dir * .0094f);
            float x0 = cf_thumb_hold(lean, lead, frac);
            int r0 = (int)floorf(x0 + .5f), f0 = (int)((x0 - (float)r0) * ONE);
            /* the entry's last frame (its albums from frac's centre) is the held row's first */
            int n = cf_enter_quads(frac, lead, 0, lean, &centre, q), on = 0;
            cf_thumb_lean(lean);
            int ln = cf_frame_quads(f0, lq);
            for (int i = 0; i < n; ++i) {
                if (!quad_cols(q[i])) continue;
                int j = 0;
                while (j < ln && lq[j][0] + r0 != q[i][0]) ++j;
                assert(j < ln);
                for (int e = 1; e < 6; ++e) assert(abs(lq[j][e] - q[i][e]) <= 1);
                ++on;
            }
            assert(on >= 11);
            /* the formed row at speed: a cover row_flat and row_edge or more from its facing point */
            float turned = (cs->row_flat > cs->row_edge ? cs->row_flat : cs->row_edge) / 100.f;
            int rq[15][6], rn, ref = -1, next = -1;
            cf_thumb_enter(0, lead, 0);
            rn = cf_frame_quads(frac, rq);
            for (int i = 0; i < rn; ++i) {
                float t = ((float)rq[i][0] - ((float)frac / ONE + lead)) * dir;
                if (t <= -turned - 1 && quad_cols(rq[i])) {
                    for (int j = 0; j < rn; ++j) next = rq[j][0] == rq[i][0] - dir ? j : next;
                    ref = i;
                    break;
                }
            }
            assert(ref >= 0 && next >= 0);
            int width = rq[ref][2] - rq[ref][1], gap = (rq[ref][1] - rq[next][1]) * dir;
            float key = (float)(lean > 0 ? rq[ref][3] : rq[ref][4]) / (float)(lean > 0 ? rq[ref][4] : rq[ref][3]);
            assert(key < .99f && gap > 0);
            /* through an album's slide, every cover on the screen so, leaning against the spin */
            cf_thumb_lean(lean);
            for (int ph = -ONE / 2; ph < ONE / 2; ph += ONE / 16) {
                int fq[15][6], fn = cf_frame_quads(ph, fq), lo = 1 << 30, hi = -(1 << 30), on = 0;
                for (int i = 0; i < fn; ++i) {
                    if (!quad_cols(fq[i])) continue;
                    ++on, lo = fq[i][1] < lo ? fq[i][1] : lo, hi = fq[i][2] > hi ? fq[i][2] : hi;
                    float k = (float)(lean > 0 ? fq[i][3] : fq[i][4]) / (float)(lean > 0 ? fq[i][4] : fq[i][3]);
                    if (abs(fq[i][2] - fq[i][1] - width) > 2 || fabsf(k - key) > .005f || (fq[i][4] - fq[i][3]) * lean <= 0) {
                        fprintf(stderr, "held_row: theme %d dir %d at %d: album %d %d wide, far:near %.3f (the row's %d, %.3f)\n",
                                ti, dir, ph, fq[i][0], fq[i][2] - fq[i][1], k, width, key);
                        assert(0);
                    }
                    for (int j = 0; j < fn; ++j) /* the next one the way the covers came from */
                        if (fq[j][0] == fq[i][0] - dir && quad_cols(fq[j])) assert(abs((fq[i][1] - fq[j][1]) * dir - gap) <= 2);
                }
                /* the screen, edge to edge: a cover within a spacing of each */
                assert(on >= 11 && lo < (1 - 2 * CF_CX) * 8 + gap && hi > (2 * CF_VIEW_W - 1 - 2 * CF_CX) * 8 - gap);
            }
            /* half an album on from one cover is half an album back from the next */
            for (int o = -3; o <= 3; ++o) {
                const unsigned *one[15] = { 0 }, *nx[15] = { 0 };
                int a = cf_frame_window(ONE / 2), b = cf_frame_window(-ONE / 2);
                if (abs(o - a) > 7 || abs(o - 1 - b) > 7) continue;
                one[o - a + 7] = nx[o - 1 - b + 7] = art[o + 21];
                coverflow_render(f, PITCH, ONE / 2, one + 4);
                coverflow_render(g, PITCH, -ONE / 2, nx + 4);
                assert(!memcmp(f, g, sizeof f));
            }
            /* for the mirror: each cover at frac 0 */
            int mq[15][6], mn = cf_frame_quads(0, mq);
            for (int i = 0; i < mn; ++i)
                if (abs(mq[i][0]) <= 21) memcpy(mirror[dir > 0][mq[i][0] + 21], mq[i], sizeof mq[i]);
        }
        /* Leaning left is leaning right in a mirror: cover -a is cover a's, mirrored. */
        for (int a = -21; a <= 21; ++a) {
            const int *l = mirror[0][a + 21], *r = mirror[1][21 - a];
            if (!l[5] || !r[5] || !quad_cols(l)) continue;
            assert(abs(l[1] + r[2]) <= 1 && abs(l[2] + r[1]) <= 1 && abs(l[3] - r[4]) <= 1 && abs(l[4] - r[3]) <= 1 && l[5] == r[5]);
        }
        /* The page's frame follows the mode, the position unchanged: letters, then the shelf again. */
        static unsigned before[CF_VIEW_H * CF_VIEW_W];
        int canvas[16] = { 0 };
        frame_fail = 0, top_window = page;
        cf_thumb_lean(0);
        coverflow_paint(slide(), canvas);
        memcpy(before, shown, sizeof shown);
        cf_thumb_lean(1);
        coverflow_paint(slide(), canvas);
        assert(memcmp(before, shown, sizeof shown));
        cf_thumb_lean(0);
        coverflow_paint(slide(), canvas);
        assert(!memcmp(before, shown, sizeof shown));
        close_page();
        top_window = 0;
    }
    #undef ART
    shim_themes[1].cf = fixture;
    classic = 0, square_art = 0;
}

/* ---- The lift folds the held row back out onto the shelf: every cover glides from the pose that
   draws it in the held row to its rest pose, and the side that must turn its lean over swings out on
   an arc and turns while out. Seen from above (each cover a segment 160 px wide), no two covers ever
   cross or come within 4% of a cover's width, in each built-in theme, leaning either way. ---- */
int cf_fold_pose(int o, int t16, int lean, int *xc, int *zc, int *c, int *s, int *eye);
int cf_fold_order(int t16, int lean, signed char *order, int (*out)[6]);
static void hold_formed(int lean) { /* the row held as an entry from the formed row at speed leaves it */
    cf_thumb_speed(-lean * .0139f, -lean * .0094f);
    cf_thumb_hold(lean, -lean * 3.2f, 0);
}
void cf_thumb_fold(int lean, unsigned at);
int cf_path_x(int t, int u);
static int orient(const double *p, const double *q, const double *r) {
    double v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
    return (v > 0) - (v < 0);
}
static int crossing(const double *a, const double *b) { /* touching counts */
    int d1 = orient(b, b + 2, a), d2 = orient(b, b + 2, a + 2), d3 = orient(a, a + 2, b), d4 = orient(a, a + 2, b + 2);
    return d1 * d2 <= 0 && d3 * d4 <= 0 &&
           !(!d1 && !d2 && !d3 && !d4 && (fmax(a[0], a[2]) < fmin(b[0], b[2]) || fmax(b[0], b[2]) < fmin(a[0], a[2])));
}
static double to_seg(const double *p, const double *s) {
    double dx = s[2] - s[0], dy = s[3] - s[1], l = dx * dx + dy * dy, u = l ? ((p[0] - s[0]) * dx + (p[1] - s[1]) * dy) / l : 0;
    u = u < 0 ? 0 : u > 1 ? 1 : u;
    return hypot(p[0] - s[0] - u * dx, p[1] - s[1] - u * dy);
}
static double seg_gap(const double *a, const double *b) {
    if (crossing(a, b)) return 0;
    return fmin(fmin(to_seg(a, b), to_seg(a + 2, b)), fmin(to_seg(b, a), to_seg(b + 2, a)));
}
static int fold_x(int xc, int zc, int c, int s, int eye, int u) { /* project()'s own sums */
    return (xc + (u * c >> 16)) * eye / (eye + zc - (u * s >> 16));
}
static void fold_clearance(void) {
    static const cf_style_t shipped[3] = { CF_SHIPPED0, CF_SHIPPED, CF_SHIPPED2 };
    const cf_style_t fixture = shim_themes[1].cf;
    classic = 1;
    for (int ti = 0; ti < 3; ++ti) {
        shim_themes[1].cf = shipped[ti];
        const cf_style_t *cs = &shim_themes[1].cf;
        for (int lean = -1; lean <= 1; lean += 2) {
            hold_formed(lean);
            for (int k = 0; k <= 100; ++k) {
                int t16 = k * ONE / 100, n = 0, at[15];
                double seg[15][4];
                for (int o = -7; o <= 7; ++o) {
                    int xc, zc, c, s, eye;
                    if (cf_fold_pose(o, t16, lean, &xc, &zc, &c, &s, &eye) <= 0) continue;
                    double hc = 1280. * c / ONE, hs = 1280. * s / ONE;
                    seg[n][0] = xc - hc, seg[n][1] = zc + hs, seg[n][2] = xc + hc, seg[n][3] = zc - hs;
                    at[n++] = o;
                }
                for (int i = 0; i < n; ++i)
                    for (int j = i + 1; j < n; ++j) {
                        double gap = seg_gap(seg[i], seg[j]);
                        if (gap < 2 * 1280 * 4 / 100.) {
                            fprintf(stderr, "fold_clearance: theme %d lean %d at %d/100: covers %d and %d %.2f px apart\n",
                                    ti, lean, k, at[i], at[j], gap / 16);
                            assert(0);
                        }
                    }
            }
            /* The turning side turns only between FOLD_FROM and FOLD_TO % of the fold. */
            for (int o = -7; o <= 7; ++o) {
                if (o * lean >= 0) continue;
                int xc, zc, c, s, eye, c0, s0, c1, s1;
                cf_fold_pose(o, 0, lean, &xc, &zc, &c0, &s0, &eye);
                cf_fold_pose(o, ONE, lean, &xc, &zc, &c1, &s1, &eye);
                cf_fold_pose(o, FOLD_FROM * ONE / 100, lean, &xc, &zc, &c, &s, &eye);
                assert(c == c0 && s == s0);
                cf_fold_pose(o, (FOLD_TO * ONE + 99) / 100, lean, &xc, &zc, &c, &s, &eye);
                assert(c == c1 && s == s1);
            }
            /* It starts as the held row, each cover the pose that draws its quad there, to a 1/16 px
               (heights to 1/128), and ends as the shelf at rest. The top view above takes every cover
               as seen from one eye; the held row's covers on the screen are seen from eyes within 20%
               of each other (9% at Minimal's numbers, 18% at Classic's: each cover's own turn sets its
               eye), and fold_frames checks the screen itself, every column's cover in front the
               tallest there. */
            int lq[15][6], ln, eye_lo = 1 << 30, eye_hi = 0;
            cf_thumb_lean(lean);
            ln = cf_frame_quads(0, lq);
            cf_thumb_lean(0);
            for (int o = -7; o <= 7; ++o) {
                int xc, zc, c, s, eye, n = abs(o), side = o < 0 ? -1 : 1, j = 0;
                int bright = cf_fold_pose(o, 0, lean, &xc, &zc, &c, &s, &eye);
                while (j < ln && lq[j][0] != o) ++j;
                if (j < ln && quad_cols(lq[j])) {
                    eye_lo = eye < eye_lo ? eye : eye_lo, eye_hi = eye > eye_hi ? eye : eye_hi;
                    double e = eye, dl = e + zc + 1280. * s / ONE, dr = e + zc - 1280. * s / ONE;
                    double xl = (xc - 1280. * c / ONE) * e / dl, xr = (xc + 1280. * c / ONE) * e / dr;
                    if (!(fabs(xl - lq[j][1]) <= 1.5 && fabs(xr - lq[j][2]) <= 1.5 && fabs(40960. * e / dl - lq[j][3]) <= 2 &&
                          fabs(40960. * e / dr - lq[j][4]) <= 2 && bright == lq[j][5])) {
                        fprintf(stderr, "fold_clearance: theme %d lean %d cover %d: %.1f %.1f %.1f %.1f, the held row's %d %d %d %d\n",
                                ti, lean, o, xl, xr, 40960. * e / dl, 40960. * e / dr, lq[j][1], lq[j][2], lq[j][3], lq[j][4]);
                        assert(0);
                    }
                }
                if (o == 7) assert(eye_hi > 0 && eye_hi * 100 <= eye_lo * 120);
                cf_fold_pose(o, ONE, lean, &xc, &zc, &c, &s, &eye);
                if (n) assert(xc == side * 16 * (cs->x1 + cs->xs * (n - 1)) && zc == 16 * (cs->z1 + cs->zs * (n - 1)));
                for (int u = -1280; u <= 1280; u += 1280) /* the pose seen as pose()'s, mirrored on the left */
                    assert(fold_x(side * xc, zc, c, side * s, eye, u) == cf_path_x(n * ONE, u));
            }
        }
    }
    shim_themes[1].cf = fixture;
    classic = 0;
}

/* Its frames: per column the nearest cover, in fold_order's order, which is the nearest there; it
   starts on the held row's frame (each cover's quad to a 1/16 px, and the same pixels drawn but for
   which texel a column takes) and ends on the shelf's at rest; an empty side (the landing album
   first or last of an artist's) draws nothing; and the page draws every frame of it anew. */
void cf_shelf_still(int still);
static void fold_frames(void) {
    static unsigned f[300 * PITCH], g[300 * PITCH], one_f[300 * PITCH], art[15][160 * 160], line[15][CF_VIEW_W];
    static const cf_style_t shipped[3] = { CF_SHIPPED0, CF_SHIPPED, CF_SHIPPED2 };
    const cf_style_t fixture = shim_themes[1].cf;
    const unsigned *ring[15];
    for (int i = 0; i < 15; ++i) {
        ring[i] = art[i];
        for (int k = 0; k < 160 * 160; ++k) art[i][k] = 0xff000000u | ((unsigned)(i * 160 * 160 + k) * 2654435761u >> 8 & 0xffffff);
    }
    square_art = 1, classic = 1;
    for (int ti = 0; ti < 3; ++ti) {
        shim_themes[1].cf = shipped[ti];
        open_page();
        const cf_style_t *cs = &shim_themes[1].cf;
        int H = cs->view_h, hz = cs->top + 160 * cs->horizon / 100;
        for (int lean = -1; lean <= 1; lean += 2) {
            hold_formed(lean);
            for (int empty = 0; empty <= 2; ++empty) /* none, the left side, the right side */
                for (int k = 0, steps = empty ? 8 : 50; k <= steps; ++k) { /* whole */
                    int ms = k * CF_FOLD_MS / steps, t16 = ms * ONE / CF_FOLD_MS;
                    const unsigned *have[15];
                    for (int o = -7; o <= 7; ++o) have[o + 7] = (empty == 1 && o < 0) || (empty == 2 && o > 0) ? 0 : ring[o + 7];
                    cf_thumb_fold(lean, clock_ms - ms);
                    signed char order[15];
                    int n = cf_fold_order(t16, lean, order, 0);
                    for (int o = -7; o <= 7; ++o) {
                        const unsigned *one[15] = { 0 };
                        one[o + 7] = have[o + 7];
                        coverflow_render(one_f, PITCH, 0, one + 4);
                        for (int x = 0; x < CF_VIEW_W; ++x) line[o + 7][x] = one_f[hz * PITCH + x];
                    }
                    coverflow_render(f, PITCH, 0, have + 4);
                    for (int x = 0; x < CF_VIEW_W; ++x) {
                        int want = -99;
                        for (int i = 0; i < n && want == -99; ++i)
                            if (line[order[i] + 7][x] != 0xff000000u) want = order[i];
                        assert(f[hz * PITCH + x] == (want == -99 ? 0xff000000u : line[want + 7][x]));
                        if (want == -99) continue;
                        /* no cover showing at x is nearer there than the one in front: taller,
                           each seen from its own eye */
                        double dx = (2 * x + 1 - 2 * CF_CX) * 8, tallest = 0, front = 0;
                        for (int o = -7; o <= 7; ++o) {
                            if (line[o + 7][x] == 0xff000000u) continue;
                            int xc, zc, c, s, eye;
                            cf_fold_pose(o, t16, lean, &xc, &zc, &c, &s, &eye);
                            double cc = (double)c / ONE, ss = (double)s / ONE;
                            double u = (dx * (eye + zc) - (double)xc * eye) / (cc * eye + dx * ss), h = 160. * eye / (eye + zc - u * ss);
                            tallest = h > tallest ? h : tallest;
                            front = o == want ? h : front;
                        }
                        assert(front >= tallest - 1);
                    }
                }
            /* the held row's frame at the start, the shelf's at rest at the end */
            signed char order[15];
            int fq[15][6], lq[15][6], fn = cf_fold_order(0, lean, order, fq), ln, sh, off = 0;
            const unsigned *lring[15] = { 0 };
            cf_thumb_fold(lean, clock_ms);
            coverflow_render(f, PITCH, 0, ring + 4);
            cf_thumb_lean(lean);
            ln = cf_frame_quads(0, lq), sh = cf_frame_window(0);
            for (int j = 0; j < 15; ++j) lring[j] = j + sh >= 0 && j + sh < 15 ? ring[j + sh] : 0;
            coverflow_render(g, PITCH, 0, lring + 4);
            for (int i = 0; i < ln; ++i) {
                int j = 0;
                if (!quad_cols(lq[i])) continue;
                while (j < fn && fq[j][0] != lq[i][0]) ++j;
                assert(j < fn);
                for (int e = 1; e < 6; ++e) assert(abs(fq[j][e] - lq[i][e]) <= 2);
            }
            for (int y = 0; y < H; ++y)
                for (int x = 0; x < CF_VIEW_W; ++x) off += (f[y * PITCH + x] == 0xff000000u) != (g[y * PITCH + x] == 0xff000000u);
            assert(off < H);
            /* the fold's last frame hands over to the still shelf it lands on, with no pop: the
               covers past the third out are gone by then */
            cf_thumb_fold(lean, clock_ms - CF_FOLD_MS);
            coverflow_render(f, PITCH, 0, ring + 4);
            cf_thumb_fold(0, 0);
            cf_shelf_still(1);
            coverflow_render(g, PITCH, 0, ring + 4);
            cf_shelf_still(0);
            int jump = 0;
            for (int i = 0; i < H * PITCH; ++i)
                for (int b = 0; b < 24; b += 8) jump = abs(ch(f[i], b / 8) - ch(g[i], b / 8)) > jump ? abs(ch(f[i], b / 8) - ch(g[i], b / 8)) : jump;
            if (jump > 2) fprintf(stderr, "fold_frames: theme %d lean %d: the landing jumps %d levels\n", ti, lean, jump);
            assert(jump <= 2);
        }
        /* The page: a held row leaning the other way is another frame, and the fold's frames move
           with the clock, the position unchanged. */
        static unsigned before[CF_VIEW_H * CF_VIEW_W];
        int canvas[16] = { 0 };
        frame_fail = 0, top_window = page;
        hold_formed(1), cf_thumb_lean(1);
        for (int i = 0; i < 30; ++i) coverflow_paint(slide(), canvas); /* every texture loaded */
        memcpy(before, shown, sizeof shown);
        hold_formed(-1), cf_thumb_lean(-1);
        coverflow_paint(slide(), canvas);
        assert(memcmp(before, shown, sizeof shown));
        cf_thumb_fold(1, clock_ms);
        coverflow_paint(slide(), canvas);
        memcpy(before, shown, sizeof shown);
        clock_ms += CF_FOLD_MS / 2;
        coverflow_paint(slide(), canvas);
        assert(memcmp(before, shown, sizeof shown));
        cf_thumb_fold(0, 0);
        close_page();
        top_window = 0;
    }
    shim_themes[1].cf = fixture;
    classic = 0, square_art = 0;
}

/* ---- Into letters the facing point overtakes: from the row at speed as it was, the row goes on and
   the facing point runs on ahead of it, the way the spin goes, through the covers ahead, each turning
   as it is passed, till it is so far off the screen's leading edge that no cover on the screen is
   within row_flat or row_edge of it; the covers it has passed lean the way the covers behind a
   facing cover do. Every
   frame of it, in each built-in theme, spinning either way: per column the cover in front is the one
   painted first there and the tallest there (no two pass through each other); every cover behind the
   facing point is turned the lean's way and none ahead of it is; the facing point's x on the screen
   only goes on towards the leading edge, and off it; a cover leaves or joins the window only off the
   screen; it starts on the row's frame and ends on the held row's, every cover leaning, against the
   spin. The facing point sets off at its own speed in the row, and every cover within row_flat and
   row_edge of it is off the screen as it stops running. ---- */
void cf_enter_quad(int frac, float lead, float enter, int lean, int a, int *out);
float cf_enter_face(int frac, float lead, float enter, int lean, int *x);
int coverflow_letter_next(void *w, int from, int dir, int n, unsigned *letter);
static int turned_on(int frac, float lead, float enter, int lean, float at) { /* a cover within the row's turn on the screen */
    const cf_style_t *cs = &shim_themes[1].cf;
    float turned = (cs->row_flat > cs->row_edge ? cs->row_flat : cs->row_edge) / 100.f;
    int out[6], on = 0;
    for (int a = (int)floorf(at - turned); a <= at + turned; ++a)
        if (fabsf(a - at) < turned) cf_enter_quad(frac, lead, enter, lean, a, out), on |= quad_cols(out) > 0;
    return on;
}
static void enter_frames(void) {
    static unsigned f[300 * PITCH], g[300 * PITCH], one_f[300 * PITCH], last_f[300 * PITCH], art[43][160 * 160], line[15][CF_VIEW_W];
    static const cf_style_t shipped[3] = { CF_SHIPPED0, CF_SHIPPED, CF_SHIPPED2 };
    const cf_style_t fixture = shim_themes[1].cf;
    #define ART(a) (abs(a) <= 21 ? art[(a) + 21] : 0) /* album a from frac's centre */
    for (int i = 0; i < 43; ++i)
        for (int k = 0; k < 160 * 160; ++k) art[i][k] = 0xff000000u | ((unsigned)(i * 160 * 160 + k) * 2654435761u >> 8 & 0xffffff);
    square_art = 1, classic = 1;
    for (int ti = 0; ti < 3; ++ti) {
        shim_themes[1].cf = shipped[ti];
        open_page(); /* cf_restyle's */
        const cf_style_t *cs = &shim_themes[1].cf;
        int H = cs->view_h, hz = cs->top + 160 * cs->horizon / 100;
        for (int dir = -1; dir <= 1; dir += 2)
            for (int start = 0; start < 2; ++start) { /* at the cap's lead, and a short one */
                int lean = -dir; /* the covers behind the facing cover's turn, against the spin */
                float lead = dir * (start ? .7f : 2.6f);
                float travel = cs->enter_albums;
                int frac = dir * (start ? -19661 : 13107), steps = 100; /* the run ends at a step: k 99 */
                int last_n = 0, last[15][6], face_x = 0;
                float prev_enter = 0, face = 0, ahead = 0, first = 0;
                int last_centre = 0;
                /* the row's speed and the facing point's own as letters began, albums a ms */
                float vel = dir * (start ? .012f : .0139f), lead_v = dir * (start ? .003f : .0094f);
                float v = fabsf(vel) > CF_ENTER_SPEED / 1000.f ? fabsf(vel) : CF_ENTER_SPEED / 1000.f;
                int black0 = 0, black1 = 0, worst = 0, worst_k = 0;
                cf_thumb_speed(vel, lead_v);
                for (int k = 0; k <= steps; ++k) {
                    float enter = k < steps ? travel * (steps - k) / steps : 1e-5f;
                    int q[15][6], centre, n = cf_enter_quads(frac, lead, enter, lean, &centre, q), x;
                    float at = cf_enter_face(frac, lead, enter, lean, &x);
                    const unsigned *have[15];
                    for (int j = -7; j <= 7; ++j) have[j + 7] = ART(centre + j);
                    cf_thumb_enter(lean, lead, enter);
                    for (int i = 0; i < n; ++i) assert(q[i][0] >= centre - 7 && q[i][0] <= centre + 7);
                    /* the facing point runs on, the way the spin goes, on the screen and through the row */
                    if (!k)
                        assert(fabsf(at - ((float)frac / ONE + lead)) < 1e-4f);
                    else if (!((at - face) * dir >= 0 && (x - face_x) * dir >= 0)) {
                        fprintf(stderr, "enter_frames: theme %d dir %d start %d at %d/%d: the facing point at %.3f, x %d, from %.3f, x %d\n", ti, dir, start, k, steps, at, x, face, face_x);
                        assert(0);
                    }
                    /* faster and faster while it runs (ENTER_RUN % of the entry), then with the row */
                    /* (the share gone is to 1/65536, of enter_albums: the row's step jitters by that) */
                    if (k >= 2 && k * 100 <= steps * ENTER_RUN) assert((at - face) * dir >= ahead - 2.f * cs->enter_albums / ONE);
                    if (k == 1) first = (at - face) * dir;
                    /* from its speed in the row as letters began, the row's and the lead's own */
                    if (k == 1) assert(first >= (fabsf(vel) + fabsf(lead_v)) * travel / steps / v * .99f);
                    /* the cube on top of its own speed: at the run's end over 1.5x its first step
                       (the lead's own speed covers most of a run as long as the entry, so it is as
                       little as 1.9x, Classic's; a run with no cube left would end at 1x, and one
                       ending slower than it came stalls the facing cover going off) */
                    if (k * 100 == steps * ENTER_RUN) assert((at - face) * dir > 1.5f * first);
                    /* as it stops running, every cover within the row's turn off the screen, the
                       facing cover too, from the row at any phase, and for an entry of 3 albums too
                       (where it stops between albums otherwise) */
                    for (int ph = -ONE / 2, e = cs->enter_albums; k * 100 == steps * ENTER_RUN && ph < ONE / 2; ph += ONE / 16)
                        for (int albums3 = 0; albums3 < 2; ++albums3) {
                            int out[6], xx;
                            float stop = (albums3 ? 3 : e) * (100 - ENTER_RUN) / 100.f, fa;
                            shim_themes[1].cf.enter_albums = albums3 ? 3 : e;
                            fa = cf_enter_face(ph, lead, stop, lean, &xx);
                            cf_enter_quad(ph, lead, stop, lean, (int)floorf(fa + .5f), out);
                            int on = turned_on(ph, lead, stop, lean, fa);
                            shim_themes[1].cf.enter_albums = e;
                            cf_enter_face(ph, lead, stop, lean, &xx); /* cf_restyle's, the theme's again */
                            if (quad_cols(out) || on) {
                                fprintf(stderr, "enter_frames: theme %d dir %d start %d frac %d (%d albums): the facing cover at %d..%d as the run ends\n",
                                        ti, dir, start, ph, albums3 ? 3 : e, out[1], out[2]);
                                assert(0);
                            }
                        }
                    ahead = (at - face) * dir, face = at, face_x = x;
                    /* per column, every fourth step: the first painted, and the tallest there */
                    for (int i = 0; !(k & 3) && i < n; ++i) {
                        const unsigned *one[15] = { 0 };
                        one[q[i][0] - centre + 7] = have[q[i][0] - centre + 7];
                        coverflow_render(one_f, PITCH, frac, one + 4);
                        for (int x = 0; x < CF_VIEW_W; ++x) line[i][x] = one_f[hz * PITCH + x];
                    }
                    coverflow_render(f, PITCH, frac, have + 4);
                    int black = 0;
                    for (int x = 0; x < CF_VIEW_W; ++x) black += f[hz * PITCH + x] == 0xff000000u;
                    worst = black > worst ? black : worst, worst_k = black == worst ? k : worst_k;
                    if (!k) black0 = black;
                    for (int x = 0; !(k & 3) && x < CF_VIEW_W; ++x) {
                        int want = -1, mx = (2 * x + 1 - 2 * CF_CX) * 8; /* draw_quad's column middle */
                        #define DRAWS(i) (q[i][5] > 0 && mx >= q[i][1] && mx < q[i][2])
                        for (int i = 0; i < n && want < 0; ++i)
                            if (DRAWS(i)) want = i;
                        assert(f[hz * PITCH + x] == (want < 0 ? 0xff000000u : line[want][x]));
                        if (want < 0) continue;
                        double dx = mx, tallest = 0, front = 0;
                        for (int i = 0; i < n; ++i) {
                            if (!DRAWS(i)) continue;
                            double h = q[i][3] + (double)(q[i][4] - q[i][3]) * (dx - q[i][1]) / (q[i][2] - q[i][1]);
                            tallest = h > tallest ? h : tallest;
                            front = i == want ? h : front;
                        }
                        if (front < tallest - 256) {
                            fprintf(stderr, "enter_frames: theme %d dir %d start %d at %d/%d: column %d shows %d, %.1f px under %.1f\n",
                                    ti, dir, start, k, steps, x, q[want][0], front / 256, tallest / 256);
                            assert(0);
                        }
                        #undef DRAWS
                    }
                    /* behind the facing point, turned the lean's way (the near edge, the taller, on
                       the lean's side); ahead of it, not */
                    for (int i = 0; i < n; ++i) {
                        int a = q[i][0], d = q[i][4] - q[i][3];
                        float u = ((float)a - at) * dir;
                        if (!quad_cols(q[i]) || (u > -1e-3f && u < 1e-3f)) continue;
                        if (u < 0 ? d * lean < 0 : d * lean > 0) {
                            fprintf(stderr, "enter_frames: theme %d dir %d start %d at %d/%d: album %d, %.2f from the facing point, edges %d and %d\n",
                                    ti, dir, start, k, steps, a, u, q[i][3], q[i][4]);
                            assert(0);
                        }
                    }
                    /* the window drops a cover only once it shows nothing, and gains one only while
                       it still does: off the screen, or behind nearer covers in the frame with it */
                    for (int pass = 0; k && pass < 2; ++pass) {
                        int (*from)[6] = pass ? q : last, (*to)[6] = pass ? last : q, nf = pass ? n : last_n, nt = pass ? last_n : n;
                        int c0 = pass ? centre : last_centre;
                        for (int i = 0; i < nf; ++i) {
                            int kept = 0;
                            for (int j = 0; j < nt; ++j) kept |= to[j][0] == from[i][0];
                            if (kept) continue;
                            int out[6];
                            cf_enter_quad(frac, lead, pass ? prev_enter : enter, lean, from[i][0], out);
                            if (!quad_cols(out)) continue; /* off the screen in the frame without it */
                            const unsigned *less[15]; /* or adding nothing to the frame with it */
                            for (int j = -7; j <= 7; ++j) less[j + 7] = c0 + j == from[i][0] ? 0 : ART(c0 + j);
                            cf_thumb_enter(lean, lead, pass ? enter : prev_enter);
                            coverflow_render(g, PITCH, frac, less + 4);
                            if (memcmp(pass ? f : last_f, g, H * PITCH * sizeof *f)) {
                                fprintf(stderr, "enter_frames: theme %d dir %d start %d at %d/%d: album %d %s the window showing\n",
                                        ti, dir, start, k, steps, from[i][0], pass ? "joins" : "leaves");
                                assert(0);
                            }
                        }
                    }
                    cf_thumb_enter(lean, lead, enter);
                    memcpy(last, q, sizeof q), last_n = n, last_centre = centre, prev_enter = enter;
                    memcpy(last_f, f, sizeof last_f);
                    if (k == 0) { /* the row at speed's frame, as it was */
                        const unsigned *row[15];
                        cf_thumb_enter(0, lead, 0);
                        for (int j = -7, sh = cf_frame_window(frac); j <= 7; ++j) row[j + 7] = ART(sh + j);
                        coverflow_render(g, PITCH, frac, row + 4);
                        assert(!memcmp(f, g, H * PITCH * sizeof *f));
                        /* and its black as the facing point goes through an album, as the overtake's
                           does, and as the row formed does (the run forms it) */
                        for (int formed = 0; formed < 2; ++formed)
                            for (int ph = -ONE / 2; ph < ONE / 2; ph += ONE / 16) {
                                int b = 0;
                                float l = formed ? dir * 3.2f : lead;
                                cf_thumb_enter(0, l, 0);
                                for (int j = -7, sh = cf_frame_window(ph); j <= 7; ++j) row[j + 7] = ART(sh + j);
                                coverflow_render(g, PITCH, ph, row + 4);
                                for (int x = 0; x < CF_VIEW_W; ++x) b += g[hz * PITCH + x] == 0xff000000u;
                                black0 = b > black0 ? b : black0;
                            }
                    }
                }
                /* the facing cover wholly off the screen's leading edge at the end, and every cover
                   within the row's turn, every cover on it leaning, and the held row's frame there */
                int out[6];
                cf_enter_quad(frac, lead, 1e-5f, lean, (int)floorf(face + .5f), out);
                assert(dir > 0 ? out[1] > (2 * CF_VIEW_W - 1 - 2 * CF_CX) * 8 : out[2] <= (1 - 2 * CF_CX) * 8);
                assert(!turned_on(frac, lead, 1e-5f, lean, face));
                for (int i = 0; i < last_n; ++i)
                    if (quad_cols(last[i]) && !((last[i][4] - last[i][3]) * lean > 0 && (last[i][0] - face) * dir <= -1 + 1e-3f)) {
                        fprintf(stderr, "enter_frames: theme %d dir %d start %d: album %d on the screen at the end, %d..%d, edges %d %d, facing %.2f\n",
                                ti, dir, start, last[i][0], last[i][1], last[i][2], last[i][3], last[i][4], face);
                        assert(0);
                    }
                /* the held row exactly: each cover on the screen where the held row has it, to a
                   1/16 px, its album the same, and the same pixels drawn */
                int lq[15][6], ln, off = 0;
                float x0 = cf_thumb_hold(lean, lead, frac);
                int r0 = (int)floorf(x0 + .5f), f0 = (int)((x0 - (float)r0) * ONE), sh;
                const unsigned *held_ring[15];
                cf_thumb_enter(lean, 0, 0); /* in: the held row */
                ln = cf_frame_quads(f0, lq), sh = cf_frame_window(f0);
                for (int j = -7; j <= 7; ++j) held_ring[j + 7] = ART(r0 + sh + j);
                coverflow_render(g, PITCH, f0, held_ring + 4);
                for (int x = 0; x < CF_VIEW_W; ++x) black1 += g[hz * PITCH + x] == 0xff000000u;
                for (int i = 0; i < last_n; ++i) {
                    int j = 0;
                    if (!quad_cols(last[i])) continue;
                    while (j < ln && lq[j][0] + r0 != last[i][0]) ++j;
                    assert(j < ln && lq[j][5] == last[i][5]);
                    for (int e = 1; e < 5; ++e) assert(abs(lq[j][e] - last[i][e]) <= 1);
                }
                for (int y = 0; y < H; ++y)
                    for (int x = 0; x < CF_VIEW_W; ++x) off += (f[y * PITCH + x] == 0xff000000u) != (g[y * PITCH + x] == 0xff000000u);
                assert(off < H);
                /* no hole opens on the way: never more of the horizon black than the row at speed,
                   as it was or formed, its facing point anywhere through an album, or the held row
                   shows, and 2% of it */
                if (worst > (black0 > black1 ? black0 : black1) + CF_VIEW_W / 50) {
                    fprintf(stderr, "enter_frames: theme %d dir %d start %d at %d/%d: %d px of the horizon black, the row's %d, the held row's %d\n",
                            ti, dir, start, worst_k, steps, worst, black0, black1);
                    assert(0);
                }
            }
        cf_thumb_lean(0);
        close_page();
    }
    #undef ART
    shim_themes[1].cf = fixture;
    classic = 0, square_art = 0;
}

/* ---- At rest, nothing moving (a whole album at the middle), each built-in theme's frame is the bytes
   it has always been, dark and light, square and rounded art: only frames while the covers move draw
   the cover coming in at the edge. ---- */
void cf_shelf_still(int still);
static void rest_pinned(void) {
    static const cf_style_t shipped[3] = { CF_SHIPPED0, CF_SHIPPED, CF_SHIPPED2 };
    static const unsigned pinned[3] = { 0x09b59e69u, 0x40a22303u, 0x09b59e69u };
    static unsigned f[300 * PITCH], art[15][160 * 160];
    const cf_style_t fixture = shim_themes[1].cf;
    for (int i = 0; i < 15; ++i)
        for (int k = 0; k < 160 * 160; ++k) art[i][k] = 0xff000000u | ((unsigned)(i * 160 * 160 + k) * 2654435761u >> 8 & 0xffffff);
    const unsigned *ring[15];
    for (int j = 0; j < 15; ++j) ring[j] = art[j];
    classic = 1;
    cf_shelf_still(1); /* nothing moving, as coverflow_paint has it */
    for (int ti = 0; ti < 3; ++ti) {
        shim_themes[1].cf = shipped[ti];
        open_page();
        unsigned hash = 2166136261u;
        for (light = 0; light < 2; ++light)
            for (square_art = 0; square_art < 2; ++square_art) {
                for (unsigned i = 0; i < sizeof f / 4; ++i) f[i] = 0;
                coverflow_render(f, PITCH, 0, ring + 4);
                for (int i = 0; i < shipped[ti].view_h * PITCH; ++i) hash = (hash ^ f[i]) * 16777619u;
            }
        if (hash != pinned[ti]) fprintf(stderr, "rest_pinned: theme %d 0x%08xu\n", ti, hash);
        assert(hash == pinned[ti]);
        if (ti == 1) { /* the page: a glide's frame on a whole album is a moving one (Classic's fourth
                          sliver drawn), and once it stops the still frame is drawn again */
            static unsigned before[CF_VIEW_H * CF_VIEW_W];
            static int fake_anim[64];
            int canvas[16] = { 0 };
            widget *sl = slide();
            frame_fail = 0, top_window = page, square_art = 1;
            for (int i = 0; i < 30; ++i) coverflow_paint(sl, canvas); /* every texture loaded */
            memcpy(before, shown, sizeof shown);
            *(void **)(sl->raw + SLIDE_ANIMATOR) = fake_anim;
            coverflow_paint(sl, canvas);
            assert(memcmp(before, shown, sizeof shown));
            *(void **)(sl->raw + SLIDE_ANIMATOR) = 0;
            coverflow_paint(sl, canvas);
            assert(!memcmp(before, shown, sizeof shown));
            top_window = 0, square_art = 0;
            cf_shelf_still(1); /* the page's own render leaves it moving */
        }
        close_page();
    }
    light = 0, square_art = 0, classic = 0;
    cf_shelf_still(0);
    shim_themes[1].cf = fixture;
}

/* ---- A cover comes onto the screen already drawn: in each built-in theme, dark and light, square
   and rounded art, at rest, in the row at speed (slow and fast, either way) and in the held row (at
   the theme's row_out, and at the smallest tools/theme.py takes, CF_WIDE covers on the screen),
   every cover any of which is on the screen is at its own brightness (bright2 or more: no fading at
   the screen's edge), and as the covers slide an eighth of an album a frame (a quarter in the other looks), the window drops a
   cover only once it shows nothing in the frame, and gains one only while it still shows nothing:
   none appears or disappears while any of it shows, but in Light Mode, where one the window drops
   at the screen's edge can still move the paper there by up to 2 levels (the dark frame's black
   shows nothing of it). (Into letters, enter_frames checks the same as the entry runs.) ---- */
int cf_pose_bright(int t);
void cf_frame_quad(int frac, int a, int *out);
static unsigned edge_art[64][160 * 160];
/* The frame at frac with the window's middle at album mid, each album its own art, less album gone */
static void edge_frame(unsigned *f, int frac, int mid, int gone) {
    const unsigned *ring[15];
    for (int j = -7; j <= 7; ++j) ring[j + 7] = mid + j == gone ? 0 : edge_art[((mid + j) % 64 + 64) % 64];
    coverflow_render(f, PITCH, frac, ring + 4);
}
static int edge_levels(const unsigned *a, const unsigned *b, int h) { /* the most h rows differ by, a channel */
    int m = 0;
    for (int i = 0; i < h * PITCH; ++i)
        for (int k = 0, d; k < 24; k += 8) d = abs((int)(a[i] >> k & 255) - (int)(b[i] >> k & 255)), m = d > m ? d : m;
    return m;
}
static void edge_drawn(void) {
    static unsigned f[300 * PITCH], last[300 * PITCH], g[300 * PITCH];
    static const cf_style_t shipped[3] = { CF_SHIPPED0, CF_SHIPPED, CF_SHIPPED2 };
    static const float leads[] = { 0, .05f, .3f, 1, 2, 3.2f, 5 };
    static const int row_out_min[3] = CF_ROW_OUT_MIN; /* tools/theme.py's, each theme's */
    const cf_style_t fixture = shim_themes[1].cf;
    for (int i = 0; i < 64; ++i)
        for (int k = 0; k < 160 * 160; ++k) edge_art[i][k] = 0xff000000u | ((unsigned)(i * 160 * 160 + k) * 2654435761u >> 8 & 0xffffff);
    square_art = 1, classic = 1;
    for (int ti = 0; ti < 3; ++ti) {
        shim_themes[1].cf = shipped[ti];
        open_page(); /* cf_restyle's */
        const cf_style_t *cs = &shim_themes[1].cf;
        for (int t = 2 * ONE; t <= 15 * ONE / 2; t += ONE / 16) assert(cf_pose_bright(t) == cs->bright2);
        for (int v = 0; v < 2 * 9; ++v) /* each c dark with square art, and by turns dark rounded, light square or light rounded */
            for (int dir = -1, c = v % 9; dir <= 1; dir += 2) { /* c 0: at rest; 1-6: the row at speed at leads[c]; 7, 8: held, 8 at row_out_min */
                int look = v < 9 ? 1 : c % 3 ? 1 + c % 3 : 0; /* light << 1 | square */
                light = look >> 1, square_art = look & 1;
                shim_themes[1].cf.row_out = c == 8 ? row_out_min[ti] : shipped[ti].row_out;
                if (c < 7) cf_thumb_enter(0, 0, 0), cf_thumb_lead(c ? dir * leads[c] : -1);
                else hold_formed(-dir), cf_thumb_enter(-dir, 0, 0);
                /* half an album on from one cover is half an album back from the next: no cover cut
                   where the window or the shelf's reach ends */
                edge_frame(f, ONE / 2, cf_frame_window(ONE / 2), 1 << 30);
                edge_frame(g, -ONE / 2, 1 + cf_frame_window(-ONE / 2), 1 << 30);
                assert(!memcmp(f, g, cs->view_h * PITCH * sizeof *g));
                int last_at = 0, last_mid = 0, last_frac = 0;
                for (int s = 0; s <= 48; s += look == 1 ? 2 : 4) { /* three albums the spin's way */
                    float p = (float)(dir * s) / 16;
                    int at = (int)floorf(p + .5f), frac = (int)((p - (float)at) * ONE), q[15][6], n = cf_frame_quads(frac, q);
                    int mid = at + cf_frame_window(frac);
                    for (int i = 0; c && i < n; ++i)
                        if (quad_cols(q[i]) && q[i][5] < cs->bright2) {
                            fprintf(stderr, "edge_drawn: theme %d light %d square %d case %d dir %d at %d: album %d on the screen at %d\n",
                                    ti, light, square_art, c, dir, s, q[i][0], q[i][5]);
                            assert(0);
                        }
                    edge_frame(f, frac, mid, 1 << 30);
                    /* a cover joining or leaving the window: off the screen in the frame without it,
                       or adding nothing to the frame with it (behind nearer covers) */
                    for (int pass = 0; s && pass < 2; ++pass)
                        for (int a = (pass ? mid : last_mid) - 7; a <= (pass ? mid : last_mid) + 7; ++a) {
                            int out[6];
                            if (abs(a - (pass ? last_mid : mid)) <= 7) continue;
                            cf_frame_quad(pass ? last_frac : frac, a - (pass ? last_at : at), out);
                            if (!quad_cols(out)) continue;
                            edge_frame(g, pass ? frac : last_frac, pass ? mid : last_mid, a);
                            if (edge_levels(pass ? f : last, g, cs->view_h) > (light ? 2 : 0)) { /* the frame's rows */
                                fprintf(stderr, "edge_drawn: theme %d light %d square %d case %d dir %d at %d: album %d %s the window showing\n",
                                        ti, light, square_art, c, dir, s, a, pass ? "joins" : "leaves");
                                assert(0);
                            }
                        }
                    memcpy(last, f, sizeof last), last_at = at, last_mid = mid, last_frac = frac;
                }
            }
        light = 0, shim_themes[1].cf.row_out = shipped[ti].row_out;
        /* The shelf moving, a tenth to nine tenths (odd) of a touch glide's or a step's album either way:
           both edges' covers whole, each side shows as many covers in their bodies (reflections out
           of the frame, reflect_top 0; rounded corners) as the other, give or take the one the
           geometry adds; and it draws its whole ring (shelf_draws): every cover any of which is on
           the screen changes the frame. */
        shim_themes[1].cf.reflect_top = 0, square_art = 0;
        cf_thumb_enter(0, 0, 0), cf_thumb_lead(-1);
        for (int dir = -1; dir <= 1; dir += 2)
            for (int s = 1; s <= 9; s += 2) {
                float p = (float)(dir * s) / 10;
                int at = (int)floorf(p + .5f), frac = (int)((p - (float)at) * ONE), side[2] = { 0, 0 };
                edge_frame(f, frac, at, 1 << 30);
                int q[15][6];
                for (int a = at - 7; a <= at + 7; ++a) cf_frame_quad(frac, a - at, q[a - at + 7]);
                for (int a = at - 7; a <= at + 7; ++a) {
                    int shows, bare = 0; /* its columns on the screen that no nearer cover's quad reaches */
                    edge_frame(g, frac, at, a);
                    side[(float)a > p] += shows = memcmp(f, g, cs->view_h * PITCH * sizeof *g) != 0;
                    for (int x = 0, m; x < CF_VIEW_W; ++x) {
                        int in = (m = (2 * x + 1 - 2 * CF_CX) * 8) >= q[a - at + 7][1] && m < q[a - at + 7][2];
                        for (int b = at - 7; in && b <= at + 7; ++b)
                            in = b == a || fabsf(b - p) > fabsf(a - p) || m < q[b - at + 7][1] || m >= q[b - at + 7][2];
                        bare += in;
                    }
                    if (bare && !shows) {
                        fprintf(stderr, "edge_drawn: theme %d dir %d at %d/10: album %d not drawn, %d columns bare\n", ti, dir, s, a, bare);
                        assert(0);
                    }
                }
                if (abs(side[0] - side[1]) > 1 || side[0] < 3 || side[1] < 3) {
                    fprintf(stderr, "edge_drawn: theme %d dir %d at %d/10: %d covers show left, %d right\n", ti, dir, s, side[0], side[1]);
                    assert(0);
                }
            }
        shim_themes[1].cf = shipped[ti], square_art = 1;
        cf_thumb_enter(0, 0, 0), cf_thumb_lead(-1), cf_thumb_lean(0);
        edge_frame(f, 0, 0, 1 << 30); /* cf_corners' table square again (the flip's frames draw with it) */
        close_page();
    }
    shim_themes[1].cf = fixture;
    classic = 0, square_art = 0;
}

/* ---- Classic's thumb on the shipped numbers (theme.xml's coverflow.shelf): the cap at every
   sensitivity, the letters after the dwell and back, and a long spin's landing. ---- */
static int initial_of(int i) { char c = names[i][0]; return c >= 'a' && c <= 'z' ? c - 32 : c; }
/* Going by letters, where the row is going: the goal's album, a letter's first; the row's place,
   unwrapped from last; the row there, the thumb still; a letter's step. */
#define TARGET() ((((*index + (int)lroundf(th[T_GOAL])) % n) + n) % n)
#define FIRST(a) ((a) == 0 || initial_of(a) != initial_of((a) - 1))
static float th_at(int *index, int n, float *last) { /* the row's place, unwrapped from last */
    float d = (float)*index + th[T_POS] - *last;
    return *last += d - (float)n * floorf(d / (float)n + .5f);
}
static void th_arrive(void) { /* the thumb still, till the row is there */
    for (int i = 0; i < 300 && th[T_POS] != th[T_GOAL]; ++i) th_pass(0, 17);
    assert(th[T_POS] == th[T_GOAL]);
}
static int th_step(int dir) { /* one letter on (1) or back (-1): under one's travel a pass */
    int was = letters, r = 0;
    for (int i = 0; i < 8 && letters == was; ++i) r |= th_pass(-10 * dir, 17);
    assert(letters == was + 1);
    return r;
}
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
    /* big_jump_ms 0: letters at big_jump_at at once, and never from a slow turn */
    close_page();
    shim_themes[1].cf = shipped, shim_themes[1].cf.big_jump_ms = 0;
    open_page();
    top_window = page;
    th_pass(0, 17);
    th_down();
    for (int i = 0; i < 40; ++i) th_spin(10, 17);
    assert(!th[T_JUMP]);
    for (int i = 0; i < 40 && !th[T_JUMP]; ++i) th_spin(150, 17);
    assert(th[T_JUMP]);
    th_rest();
    close_page();
    shim_themes[1].cf = shipped;
    open_page();
    s = slide(), covers_view = &w[s->parent], n = s->nkids, index = (int *)(s->raw + SLIDE_INDEX);
    top_window = page;
    th_pass(0, 17);

    /* At big_jump_at for big_jump_ms, letters: the first carries the row on to its letter's first
       album, each after sends it there, its letter shown; on in the album order, one a jump_ticks of
       travel. */
    th_down();
    int letters0 = letters, t = 0, prev = -1, spread = 0, canvas[16] = { 0 }, tail = albums - 1;
    while (tail && initial_of(tail - 1) == initial_of(tail)) --tail; /* the last letter's first album */
    while (letters == letters0 && t < 2000) th_spin(150, 17), t += 17;
    /* the rate rising to big_jump_at, the entry at CF_ENTER_SPEED or more */
    assert(t >= shipped.big_jump_ms && t <= shipped.big_jump_ms + 100 + shipped.enter_albums * 1000 / CF_ENTER_SPEED);
    for (int i = 0; i < 30 && prev != tail; ++i) {
        int was = letters;
        if (i) th_spin(150, 17);
        if (letters == was && i) continue;
        /* the first: the row short of it, going on at the entry's speed, the caption the row's album */
        assert(th[T_LEAD] == 0 && th[T_ON] && (i || (th[T_POS] < 0 && th[T_VEL] > 0 && th[T_GOAL] == 0 &&
                                                     CAPTION(((*index + (int)floorf(th[T_POS] + .5f)) % n + n) % n))));
        assert(FIRST(TARGET()) && letter_shown == (unsigned)initial_of(TARGET()) && letter_held);
        assert(prev < 0 || TARGET() > prev); /* forward, to the last letter */
        prev = TARGET();
        /* a frame's loads end within FX_BUDGET_MS, a decode 6 ms as on the device: one, a second
           would run past it; the poll asks for frames until done */
        int l0 = loads;
        decode_ms = 6;
        for (int f = 0; f < 10; ++f) {
            int l1 = loads;
            coverflow_paint(s, canvas);
            assert(loads - l1 <= 1);
        }
        decode_ms = 0;
        spread += loads - l0 > 2;
    }
    assert(spread);
    assert(letters >= letters0 + 3);
    /* Past the last letter, round to the first, as the albums wrap: its first album, its letter and
       a click, as any step. */
    assert(prev == tail);
    int r = 0, at_last = letters;
    for (int i = 0; i < 20 && TARGET() == prev; ++i) r |= th_pass(-30, 17);
    assert(TARGET() == 0 && r & 4 /* THUMB_CLICK */ && letters == at_last + 1 && letter_held &&
           letter_shown == (unsigned)initial_of(0));
    th_arrive();
    assert(*index == 0 && CAPTION(0));
    /* Once in, letters while the thumb turns from fast_at, back here: a re-grip (a still moment,
       then a slow turn) keeps them, no album moving, and the cap's speed again goes on at once, no
       second dwell. */
    #define LANDED() (FIRST(TARGET()) && th[T_GOAL] == roundf(th[T_GOAL]) && letter_shown == (unsigned)initial_of(TARGET()))
    int was = letters;
    th_spin(0, 34);
    for (int i = 0; i < 8; ++i) th_spin(-50, 17), assert(LANDED());
    assert(letters > was);
    was = letters;
    th_spin(-150, 102);
    assert(letters >= was + 2 && LANDED());
    /* A moderate turn on (40 ticks/s, under half the cap's) reads them: one each jump_ticks, each
       landed on its letter's first album. */
    th_spin(40, 102); /* the rate falls to it in rate_down */
    was = letters;
    for (int i = 0; i < 17; ++i) {
        int before = letters;
        th_spin(40, 17);
        assert(letters - before <= 1 && (letters == before || LANDED()));
    }
    int read = 40 * 17 * 17 / 1000 / shipped.jump_ticks;
    assert(letters >= was + read - 1 && letters <= was + read + 1);
    /* Under fast_at, the thumb still down: letters still, never albums under the thumb. */
    th_spin(15, 102);
    int jumped = letters;
    for (int i = 0; i < 36; ++i) th_spin(15, 17), assert(th[T_JUMP] && LANDED());
    assert(letters > jumped);
    int from = TARGET();
    th_rest();
    assert(*index == from && CAPTION(*index) && !*(int *)(s->raw + SLIDE_OFFSET) && !letter_held);
    jumped = letters;
    /* A lift in letters lands on the letter shown, its first album. */
    th_down();
    for (t = 0; letters == jumped && t < 2000; t += 17) th_spin(-150, 17);
    for (t = 0, was = letters; letters == was && t < 2000; t += 17) th_spin(-150, 17);
    assert(letters != was);
    from = TARGET();
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

    /* Over 2000 albums with letters off, then a lift: SLIDE_INDEX takes the whole albums, so the
       follower's numbers stay small and it lands exactly, on the album named. */
    shim_themes[1].cf.jump_ticks = 0;
    cf_frame_window(0); /* cf_restyle's */
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
    shim_themes[1].cf = shipped;
    cf_frame_window(0);

    /* Under big_jump_at, however long: albums, never letters. That is a spin at 1.7.6's trigger,
       jump_at up to under big_jump_at, held far past jump_ms: Fast Navigation off, it goes by
       letters (on, a false start costs till the lift, so it takes a faster spin). */
    int l0 = letters;
    const float under[] = { 30, shipped.jump_at + 8, shipped.big_jump_at - 5 };
    for (int k = 0; k < 3; ++k) {
        th_down();
        th_spin(under[k], 2000);
        assert(letters == l0 && !th[T_JUMP]);
        th_rest();
    }
    close_page();
    big_library = 0;
    open_page();
    s = slide(), index = (int *)(s->raw + SLIDE_INDEX);
    top_window = page;
    th_pass(0, 17);
    for (int k = 1; k < 3; ++k) {
        th_down();
        th_spin(under[k], shipped.jump_ms + 600);
        assert(letters > l0 && !th[T_JUMP]);
        th_rest();
        l0 = letters;
    }
    close_page();
    big_library = 1;
    open_page();
    s = slide(), index = (int *)(s->raw + SLIDE_INDEX);
    top_window = page;
    th_pass(0, 17);
    /* At big_jump_at, letters after big_jump_ms, not a pass sooner nor much later. */
    th_down();
    int tl = 0, at = -1;
    while (!th[T_JUMP] && tl < 2000) {
        th_spin(150, 17), tl += 17;
        if (at < 0 && th[T_RATE] >= shipped.big_jump_at) at = tl;
    }
    assert(th[T_JUMP] && at > 0 && tl - at >= shipped.big_jump_ms - 17 && tl - at <= shipped.big_jump_ms + 17);
    th_rest();
    /* A re-grip's dip under big_jump_at, still over fast_at, drains the dwell rather than dropping
       it: letters come before a dwell started again after the dip would bring them. */
    l0 = letters;
    th_down();
    tl = 0;
    th_spin(150, 204), tl += 204;
    th_spin(28, 102), tl += 102; /* the rate falls under big_jump_at in about 50 ms */
    assert(letters == l0 && !th[T_JUMP]);
    while (!th[T_JUMP] && tl < 2000) th_spin(150, 17), tl += 17; /* letters begun: the entry */
    assert(th[T_JUMP] && tl < 204 + 102 + shipped.big_jump_ms);
    th_rest();

    close_page();
    top_window = 0;
    th_pass(0, 17);
    shim_themes[1].cf = fixture;
    #undef AT
}

/* ---- Fast Navigation off: 1.7.6's letters, as 1.7.6's own tests had them (Classic's shipped
   numbers): after the dwell the covers flow on and each letter is named on the moving row,
   jump_pace apart, shown for LETTER_MS; slowing, turning back or a lift ends them. No held row, no
   entry or fold, no letters' slots or loader. ---- */
int cf_fx_letters(void);
static int first_of_letter(int i) { return i >= 0 && (!i || initial_of(i) != initial_of(i - 1)); }
/* The first album of the letter after (dir 1) or before i's, round the ends. */
static int next_letter(int i, int n, int dir) {
    int j = i;
    do j = (j + dir + n) % n; while (initial_of(j) == initial_of(i));
    while (dir < 0 && !first_of_letter(j)) --j;
    return j;
}
static int named_album(widget *covers_view, int n) {
    int i = 0;
    while (i < n && strcmp(album_caption(covers_view), names[i])) ++i;
    return i < n ? i : -1;
}
static void letters_176(void) {
    static const cf_style_t shipped = CF_SHIPPED;
    const cf_style_t fixture = shim_themes[1].cf;
    shim_themes[1].cf = shipped;
    big_library = 0;
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0;
    open_page();
    widget *s = slide(), *covers_view = &w[s->parent];
    int n = s->nkids, *index = (int *)(s->raw + SLIDE_INDEX);
    top_window = page;
    th_pass(0, 17);
    #define AT() ((float)*index + th[T_GOAL]) /* the goal in albums, wherever SLIDE_INDEX is */
    /* none of Fast Navigation's: no held row, entry or fold, no letters' slots or loader */
    #define PLAIN() (!th[T_JUMP] && !th[T_LEAN] && !th[T_ENTER] && !th[T_FOLD] && !letter_held && !cf_fx_letters())

    /* At jump_at for jump_ms, letters: each names a letter's first album, its letter shown, and no
       geometry moves at one: the goal goes on by this pass's share and the covers keep moving. Each
       letter in turn, however far the covers have gone since the last, one a jump_ticks of travel. */
    th_down();
    int letters0 = letters, t = 0, spread = 0, letter_at = 0, canvas[16] = { 0 }, twos = 0, arched = 0;
    while (letters == letters0 && t < 2000) th_spin(150, 17), t += 17;
    assert(t >= shipped.jump_ms && t <= shipped.jump_ms + 300);
    for (int i = 0; i < 30; ++i) {
        int was = letters;
        float goal = th[T_GOAL];
        if (i) th_spin(150, 17);
        if (letters == was && i) continue;
        float went = th[T_GOAL] - goal;
        assert(th[T_ON] && th[T_VEL] > .005f && (!i || fabsf(went) > 32 || (went > 0 && went < .5f)));
        int at = named_album(covers_view, n);
        assert(first_of_letter(at) || !letter_shown);
        assert(!letter_shown || letter_shown == (unsigned)initial_of(at));
        assert(!i || at == next_letter(letter_at, n, 1));
        letter_at = at;
        /* 1.7.6's loads: FX_DECODES (two) a frame, whatever a decode takes (6 ms here, as on the
           device); the poll asks for frames until done */
        int l0 = loads;
        decode_ms = 6;
        for (int f = 0; f < 10; ++f) {
            int l1 = loads;
            coverflow_paint(s, canvas);
            assert(loads - l1 <= 2 && PLAIN());
            twos += loads - l1 == 2;
        }
        decode_ms = 0;
        /* and 1.7.6's window round the facing cover, the one in the middle: the arch has no facing
           point ahead of the covers, and it stays formed through a letter */
        assert(!th[T_LEAD] && !cf_frame_window(0));
        arched += th[T_ARCH] > .9f;
        spread += loads - l0 > 2;
    }
    assert(spread && twos && arched);
    assert(letters >= letters0 + 3);
    /* However hard the spin, a steady beat: jump_pace apart. */
    int beat = letters;
    th_spin(150, 1700);
    assert(letters - beat >= 1700 / shipped.jump_pace - 2 && letters - beat <= 1700 / shipped.jump_pace);
    /* Once in, letters while the thumb turns on from fast_at: a still moment then a slower turn keep
       them, and the cap's speed again goes on at once, no second dwell. */
    int was = letters;
    th_spin(0, 34);
    th_spin(50, 136);
    assert(letters > was);
    was = letters;
    th_spin(150, 340);
    assert(letters >= was + 2);
    /* A lift shorter than a letter shows keeps them: the next spin goes on from there, no dwell. */
    wheel_held = 0;
    th_spin(0, 170);
    th_down();
    was = letters;
    th_spin(150, 170);
    assert(letters > was);
    /* A moderate turn (30 ticks/s) reads them: one each jump_ticks. */
    th_spin(30, 102); /* the rate falls to it in rate_down */
    was = letters;
    th_spin(30, 1003);
    assert(letters >= was + 30 / shipped.jump_ticks - 2 && letters <= was + 30 / shipped.jump_ticks + 1);
    /* Under fast_at: album by album again under the thumb, with nothing thrown back. */
    th_spin(10, 102);
    int jumped = letters;
    float on = AT();
    th_spin(10, 600);
    on = AT() - on;
    on -= (float)n * floorf(on / (float)n + .5f);
    assert(letters == jumped && on > .3f && on < 3 && PLAIN());
    th_rest();
    assert(CAPTION(*index) && !*(int *)(s->raw + SLIDE_OFFSET));
    /* A lift in letters lands on the letter shown, its first album, however far the covers had
       flowed on from it. */
    th_down();
    for (t = 0; letters == jumped && t < 2000; t += 17) th_spin(150, 17);
    for (was = letters; letters == was;) th_spin(150, 17);
    unsigned shown = letter_shown;
    th_spin(150, 85);
    assert(letters == was + 1 && shown);
    th_rest();
    assert(first_of_letter(*index) && (unsigned)initial_of(*index) == shown && !*(int *)(s->raw + SLIDE_OFFSET));
    assert(PLAIN());

    /* Under jump_at, however long: albums, never letters. */
    th_down();
    int l0 = letters;
    th_spin(28, 2000);
    assert(letters == l0);
    th_rest();
    /* A re-grip's dip under jump_at takes its own ms off the dwell rather than dropping it: letters
       come before a dwell started again after the dip would bring them. */
    th_down();
    int tl = 0;
    th_spin(150, 255), tl += 255;
    th_spin(25, 204), tl += 204;
    assert(letters == l0);
    while (letters == l0 && tl < 2000) th_spin(150, 17), tl += 17;
    assert(letters > l0 && tl < 255 + 204 + shipped.jump_ms);
    th_rest();
    /* A lift just short of the dwell starts no letters: the rate's fall after it isn't a spin. */
    th_down();
    l0 = letters;
    th_spin(150, shipped.jump_ms - 34);
    assert(letters == l0);
    wheel_held = 0;
    th_spin(0, 170);
    assert(letters == l0);
    th_rest();
    /* A letter earned before a lift, waiting on its beat, doesn't fire after it: the lift lands on
       the letter shown. */
    th_down();
    for (was = letters; letters == was;) th_spin(150, 17);
    for (was = letters; letters == was;) th_spin(150, 17);
    th_spin(150, 68);
    was = letters;
    unsigned earned = letter_shown;
    wheel_held = 0;
    th_spin(0, 340);
    assert(letters == was);
    th_rest();
    assert(first_of_letter(*index) && (unsigned)initial_of(*index) == earned);
    /* A creeping thumb on the quick loop of a page not repainting, a unit in twelve 8 ms passes, is a
       slow thumb: albums again, under it. */
    th_down();
    for (was = letters; letters == was;) th_spin(150, 17);
    was = letters;
    th_pass(-1, 8);
    for (int k = 0; k < 11; ++k) th_pass(0, 8);
    th_pass(-1, 8);
    for (int k = 0; k < 11; ++k) th_pass(0, 8);
    float at = AT();
    for (int i = 0; i < 40; ++i) {
        th_pass(-1, 8);
        for (int k = 0; k < 11; ++k) th_pass(0, 8);
    }
    float crept = AT() - at;
    crept -= (float)n * floorf(crept / (float)n + .5f);
    assert(letters == was && crept > .5f);
    th_rest();
    /* A lift with the covers at rest stays where they are: no change of pictures on a still row. */
    th_down();
    for (was = letters; letters == was;) th_spin(150, 17);
    th_spin(30, 1207); /* long enough for the lag of the spin to go */
    for (was = letters; letters == was;) th_spin(30, 17);
    th_spin(30, 153); /* most of an album on from the letter's */
    th_spin(0, 238);
    assert(fabsf(th[T_VEL]) < .004f);
    int still = ((int)floorf(AT() + .5f) % n + n) % n;
    th_rest();
    assert(*index == still);
    /* Nor does a re-grip onto a still row: the letter it had earned waits for the covers to move. */
    th_down();
    for (was = letters; letters == was;) th_spin(150, 17);
    th_spin(40, 1207);
    for (was = letters; letters == was;) th_spin(40, 17);
    th_spin(40, 102);
    wheel_held = 0;
    th_spin(0, 204);
    wheel_held = 1;
    th_spin(0, 51);
    assert(fabsf(th[T_VEL]) < .004f);
    was = letters;
    th_pass(-6, 17);
    assert(letters == was);
    th_rest();

    /* Turned back too: the covers leave a letter's first album at once, going that way. Round the
       start to the last letter. */
    th_down();
    for (was = letters; letters == was;) th_spin(-150, 17);
    letter_at = named_album(covers_view, n);
    for (int i = 0; i < 12; ++i) {
        for (was = letters; letters == was;) th_spin(-150, 17);
        int at = named_album(covers_view, n);
        assert(at == next_letter(letter_at, n, -1) && first_of_letter(at) && letter_shown == (unsigned)initial_of(at));
        letter_at = at;
    }
    th_rest();
    assert(PLAIN());

    close_page();
    top_window = 0;
    th_pass(0, 17);
    shim_themes[1].cf = fixture;
    big_library = 1;
    #undef PLAIN
    #undef AT
}

/* ---- The arch (#329's), as shipped, Fast Navigation off: at speed the covers within arch_span of
   the middle are at the side covers' depth, none nearer the eye, each turned more the farther out;
   their places across and everything arch_span out are the shelf's; unformed, the shelf's own pose.
   The follower: no arch at a turn a second or from a step of one album, formed at two turns a
   second, gone by the landing, and none with Fast Navigation on, which keeps the row at speed. ---- */
void cf_thumb_arch(float arch), cf_arch_pose(int t, float arch, int *out);
static void arch(void) {
    static const cf_style_t shipped[3] = { CF_SHIPPED0, CF_SHIPPED, CF_SHIPPED2 };
    static unsigned f[300 * PITCH];
    const cf_style_t fixture = shim_themes[1].cf;
    const unsigned *wide[15], **ring = flat_ring(wide);
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0, big_library = 0;
    for (int ti = 0; ti < 3; ++ti) {
        const cf_style_t *cs = &shim_themes[1].cf;
        shim_themes[1].cf = shipped[ti];
        open_page();
        int span = cs->arch_span * ONE / 100, sine = 0;
        assert(span >= ONE && cs->arch_full > cs->arch_at && cs->arch_tau && cs->swing == 2);
        for (int t = 0; t <= 7 * ONE; t += ONE / 16) {
            int rest[5], half[5], full[5], side = 16 * cs->z1 + (t > ONE ? cs->zs * (t - ONE) >> 12 : 0);
            cf_arch_pose(t, 0, rest), cf_arch_pose(t, .5f, half), cf_arch_pose(t, 1, full);
            assert(rest[1] == cf_path_x(t, 80 * 16) && rest[2] == cf_path_x(t, -80 * 16));
            assert(full[4] == rest[4] && half[4] == rest[4]); /* its place across */
            if (t >= span) { /* out of the arch: the shelf's */
                assert(!memcmp(full, rest, sizeof rest) && !memcmp(half, rest, sizeof rest));
                continue;
            }
            assert(abs(full[0] - side) <= 1 && half[0] >= rest[0] && half[0] <= full[0]); /* back, none forward */
            assert(full[3] >= sine && full[3] <= rest[3] && half[3] >= full[3] && half[3] <= rest[3]);
            assert(t || !full[3]); /* the middle one still faces the eye */
            sine = full[3];
        }
        assert(sine > 60000); /* turning on into the shelf's tilt */
        /* The frame: the middle cover as tall as a side cover's plane makes it, on the horizon. */
        cf_thumb_arch(1);
        coverflow_render(f, PITCH, 0, ring);
        cf_thumb_arch(0);
        int h = 160 * cs->eye / (cs->eye + cs->z1), top = cs->top + (160 - h) * cs->horizon / 100;
        assert(abs(col_top(f, CF_CX) - top) <= 1 && slot_of(f[(top + h / 2) * PITCH + CF_CX]) == 3);
        close_page();
    }
    shim_themes[1].cf = shipped[1];
    open_page();
    widget *s = slide();
    int *offset = (int *)(s->raw + SLIDE_OFFSET);
    top_window = page;
    th_pass(0, 17);
    th_down();
    for (int i = 0; i < 60; ++i) th_spin(36, 17), assert(!th[T_ARCH] && !th[T_LEAD]); /* a turn a second */
    float peak = 0;
    for (int i = 0; i < 60; ++i) { /* two: no facing point ahead of the covers, they bend where they are */
        th_spin(72, 17);
        assert(!th[T_LEAD] && !cf_frame_window(0));
        peak = th[T_ARCH] > peak ? th[T_ARCH] : peak;
    }
    assert(peak > .8f && th[T_ARCH] > .8f && fabsf(th[T_VEL]) > .016f);
    wheel_held = 0; /* the lift: it only relaxes, and is gone before the covers commit */
    for (int i = 0; i < 400 && (i < 8 || th[T_ON]); ++i) {
        float was = th[T_ARCH];
        th_pass(0, 17);
        assert(th[T_ARCH] <= was);
    }
    assert(!th[T_ON] && !th[T_ARCH] && !*offset);
    th_down(); /* an album's travel in one pass: the goal an album on at most, so no arch */
    th_pass(-39, 17);
    for (int i = 0; i < 30; ++i) th_pass(0, 17), assert(!th[T_ARCH]);
    th_rest();
    big_library = 1; /* Fast Navigation: the row at speed, its facing point ahead, and no arch */
    th_down();
    for (int i = 0; i < 60; ++i) th_spin(72, 17), assert(!th[T_ARCH]);
    assert(th[T_LEAD] > 1);
    th_rest();
    close_page();
    top_window = 0;
    th_pass(0, 17);
    shim_themes[1].cf = fixture;
}

/* ---- Pages, the scoped pages' cards, Go to Album, the build's Cancel row. ---- */
extern int coverflow_turning(void), coverflow_rated(int (*)(void *)), coverflow_goto_album(void *), coverflow_shows_album(void *);
extern void *coverflow_tracks(void *);
extern void coverflow_by_year(void *);
static int under(const widget *x, const widget *a) {
    for (; x->parent; x = &w[x->parent])
        if (&w[x->parent] == a) return 1;
    return 0;
}
static widget *label_in(widget *v, int k) { /* v's k-th label of its own */
    for (int i = 0; i < v->nkids; ++i)
        if (!strcmp(w[v->kids[i]].type, "hscroll_label") && !k--) return &w[v->kids[i]];
    return 0;
}
static widget *panel_of(void) { /* the flip's panel, the page's newest view at its column */
    for (int i = nw; i > page - w; --i)
        if (!strcmp(w[i].type, "view") && w[i].parent == page - w && *(int *)w[i].raw == PANEL_X) return &w[i];
    return 0;
}
static widget *row_under(widget *a, int k) { /* the k-th list_item under a */
    for (int i = (int)(a - w) + 1; i <= nw; ++i)
        if (!strcmp(w[i].type, "list_item") && under(&w[i], a) && !k--) return &w[i];
    return 0;
}

/* Classic's flip (docs/ipod.md#cover-flow): Centre on a cover builds its album's panel, which stock paints off
   screen as the turn's back, then over 270 ms the cover turns into it, the side covers parting each way; at rest
   the panel stands over the parted shelf. A track plays the album from it; Return turns back, a key mid-turn
   does nothing. With no room for a turn's buffers the text list stands in, or Return goes straight back. */
static void flip_turn(void) {
    theme_t *t = &shim_themes[1];
    t->painter[PAINT_PAGES] = PAGES_CLASSIC_LIGHT, t->font[ROLE_TITLE] = FONT_CLASSIC_LIGHT;
    classic = 1, frame_fail = 0, square_art = 1, offline_ok = 1, moves_ok = 1;
    for (int i = 0; i < 320 * 375; ++i) offline[i] = 0xff302010u; /* the panel as stock paints it: BGRA 10 20 30 */
    open_page();
    widget *s = slide(), *cv = &w[s->parent];
    assert(label_in(cv, 1)->fg == (int)CF_GREY_BOLD); /* the artist in Classic's grey, its dark half's */
    static char lcd[0x200];
    int canvas[32] = { 0 }, a = 1, H = t->cf.view_h, y = t->cf.top + 80;
    *(void **)((char *)canvas + CANVAS_LCD) = lcd;
    *(unsigned *)(lcd + LCD_FILL_COLOR) = 0x12345678;
    *(const char **)(records[a] + REC_TITLE) = "Opening";
    *(int *)(s->raw + SLIDE_INDEX) = a, *(int *)(s->raw + SLIDE_OFFSET) = 0;
    clock_ms = 1000;
    w[s->kids[a]].click(w[s->kids[a]].ctx, 0);
    int (*to_tracks)(const void *) = timer_fn;
    timer_fn = 0, to_tracks(0);
    int (*tick)(const void *) = timer_fn;
    widget *panel = panel_of(), *row = panel ? row_under(panel, 0) : 0;
    assert(tick && coverflow_turning() && frames == 2 && panel && !panel->visible && row && !row_under(panel, 1));
    assert(!s->visible && !label_in(cv, 0)->visible && !label_in(cv, 1)->visible && cv->numb); /* the shelf's surface goes */
    assert(!strcmp(label_in(panel, 0)->text, names[a]) && !strcmp(label_in(panel, 1)->text, "Artist"));
    assert(!strcmp(label_in(row, 0)->text, "Opening") && !label_in(row, 1)); /* no length */
    /* The frames: the cover itself at first; half way edge on, the side covers parted (measured against the rest's
       parting below); at the end the panel as captured. */
    static unsigned zero[CF_VIEW_W], half[CF_VIEW_W];
    coverflow_paint(cv, canvas);
    memcpy(zero, shown + y * CF_VIEW_W, sizeof zero);
    clock_ms = 1000 + 135;
    assert(tick(0) == 8);
    coverflow_paint(cv, canvas);
    memcpy(half, shown + y * CF_VIEW_W, sizeof half);
    assert(zero[CF_CX] != 0xff000000u && half[CF_CX] == 0xff000000u && half[CF_CX - 1] == 0xff000000u);
    clock_ms = 1000 + 270;
    coverflow_paint(cv, canvas);
    assert(shown[120 * CF_VIEW_W + CF_CX] == 0xff102030u && shown[120 * CF_VIEW_W + PANEL_X + 5] == 0xff102030u);
    assert(tick(0) == 0 && !coverflow_turning() && panel->visible && frames == 1); /* the turn's buffers go */
    /* At rest: the shelf's halves parted, the paper between them and below the shelf, the LCD's fill back. */
    moves = fills = 0;
    coverflow_paint(cv, canvas);
    assert(moves == 2 && fills == 2 && fill_color == 0x12345678);
    int part = moved[0][0][0];
    assert(part > 0 && moved[0][1][0] == 0 && moved[0][0][2] == CF_CX - part && moved[0][1][2] == CF_CX - part);
    /* Half way through the turn (its ease's own middle) the halves had parted half as far, symmetrically. */
    for (int x = 0; x < 80; ++x)
        assert(half[x] == zero[x + part / 2] && half[CF_VIEW_W - 1 - x] == zero[CF_VIEW_W - 1 - part / 2 - x]);
    assert(moved[1][0][0] == CF_CX && moved[1][1][0] == CF_CX + part && moved[1][1][2] == CF_VIEW_W - CF_CX - part);
    assert(moved[0][0][3] == H && moved[1][1][3] == H && fill[0].x == CF_CX - part && fill[0].w == 2 * part && fill[0].h == H);
    assert(fill[1].y == H && fill[1].w == CF_VIEW_W && fill[1].y + fill[1].h == 294 && fill[0].color == 0xff000000u);
    /* The panel's own paint: its sheet the paper and the header's rows, the panel over its rows the header's
       shadow and a border just outside. */
    fills = 0;
    coverflow_paint(&w[panel->kids[0]], canvas);
    assert(fills > 2 && fill[0].x == 0 && fill[0].y == 0 && fill[0].w == BACK_W && fill[1].h == 1 && fill[fills - 1].y == fills - 2);
    fills = 0;
    coverflow_paint(panel, canvas);
    assert(fills == 12 && fill[8].x == -1 && fill[8].y == -1 && fill[8].w == BACK_W + 2 && fill[11].x == BACK_W);
    /* Light Mode: the shelf drawn again over the light paper. */
    int was = locks;
    light = 1, moves = fills = 0;
    coverflow_paint(cv, canvas);
    assert(locks == was + 1 && fill[0].color == 0xffffffffu);
    light = 0, moves = fills = 0;
    coverflow_paint(cv, canvas);
    assert(locks == was + 2 && fill[0].color == 0xff000000u);
    /* A track plays the album from it, by album (0xff10, resumed by name); with the card gone, the title says so. */
    navs = 0;
    row->click(row->ctx, 0);
    assert(navs == 1 && !strcmp(nav_to, "playing_page") && nav_ctx.dq == coverflow_tracks(page) && !nav_ctx.idx);
    assert(nav_ctx.cls == 0xff10 && nav_ctx.mode == 2 && nav_n == 1 && nav_queue[0] == records[a]);
    assert(!shim_memory[0x60d] && !strcmp((char *)shim_memory + 0x611, names[a]));
    char away[620];
    snprintf(away, sizeof away, "%.600s.away", paths[a]);
    rename(paths[a], away);
    row->click(row->ctx, 0);
    assert(navs == 1 && !strcmp(label_in(panel, 0)->text, "Storage unavailable"));
    rename(away, paths[a]);
    /* Centre while it stands: the panel stays. Return turns back; a key mid-turn does nothing. */
    w[s->kids[a]].click(w[s->kids[a]].ctx, 0);
    run();
    assert(panel_of() == panel && !coverflow_turning());
    key(KEY_RETURN);
    assert(coverflow_turning() && frames == 2);
    key(KEY_RETURN);
    assert(coverflow_turning());
    clock_ms += 270;
    assert(tick(0) == 0 && !coverflow_turning() && frames == 1);
    assert(s->visible && label_in(cv, 0)->visible && cv->visible && !cv->numb && !coverflow_tracks(page));
    /* No room for the turn: the text list; none for the turn back: straight to the covers. */
    frame_fail = 1;
    w[s->kids[a]].click(w[s->kids[a]].ctx, 0);
    run();
    widget *caption = 0; /* Classic's page title is named for the bar, which reads it */
    for (int i = nw; !caption && i > page - w; --i)
        if (!strcmp(w[i].type, PAGE_TITLE)) caption = &w[i];
    assert(!coverflow_turning() && panel_of() == panel && caption && !strcmp(caption->text, names[a]) && !cv->visible);
    key(KEY_RETURN);
    assert(cv->visible);
    frame_fail = 0;
    w[s->kids[a]].click(w[s->kids[a]].ctx, 0);
    to_tracks = timer_fn;
    timer_fn = 0, to_tracks(0);
    clock_ms += 270;
    assert(tick(0) == 0 && panel_of() != panel && panel_of()->visible);
    frame_fail = 1;
    key(KEY_RETURN);
    assert(!coverflow_turning() && cv->visible && s->visible && frames == 1);
    frame_fail = 0;
    close_page();
    assert(!frames);
    light = 1;
    open_page();
    assert(label_in(&w[slide()->parent], 1)->fg == (int)0xffffffffu); /* the light half's: the ink, as the album's */
    close_page();
    light = 0;
    t->painter[PAINT_PAGES] = PAGES_STOCK, t->font[ROLE_TITLE] = 0;
    classic = 0, square_art = 0, offline_ok = 0, moves_ok = 0;
    *(const char **)(records[a] + REC_TITLE) = 0;
}

static int no_hearts(void *r) { return 0; }
static int rated_sort(void) {
    int v = -1;
    FILE *f = fopen(PEQ_ROOT "/mnt/data/ringnav-ratedsort", "rb");
    if (f) assert(fread(&v, sizeof v, 1, f) == 1), fclose(f);
    return v;
}
/* An artist's All albums card plays every album of theirs, in the covers' order, as one library queue; Return closes
   the artist page under the covers too. The rated albums' Sort card steps their own order, saved apart from the
   library's, and stays on the card. Go to Album's page is the album's tracks alone. */
static void scoped_pages(void) {
    rescan();
    deque *d = _create_deque(0);
    d->at[d->n++] = records[6], d->at[d->n++] = records[8];
    page = &w[nw + 1];
    assert(coverflow_artist(d));
    run();
    widget *s = slide(), *cv = &w[s->parent];
    assert(s->nkids == 3);
    navs = 0;
    w[s->kids[2]].click(w[s->kids[2]].ctx, 0);
    run();
    assert(navs == 1 && !strcmp(nav_to, "playing_page") && nav_ctx.cls == 0xf001 && !nav_ctx.idx && nav_ctx.mode == 2);
    assert(nav_n == 2 && nav_queue[0] == records[6] && nav_queue[1] == records[8]);
    char away[620];
    snprintf(away, sizeof away, "%.600s.away", paths[6]);
    rename(paths[6], away);
    w[s->kids[2]].click(w[s->kids[2]].ctx, 0);
    run();
    assert(navs == 1 && !strcmp(album_caption(cv), "Storage unavailable"));
    rename(away, paths[6]);
    widget *artist_page = make(0, "artistinfo_page");
    wm_stack[0] = artist_page, wm_stack[1] = page, wm_n = 2;
    backs = force_closes = 0;
    key(KEY_RETURN);
    assert(force_closes == 1 && force_closed == artist_page && backs == 1);
    snprintf(artist_page->type, sizeof artist_page->type, "home_page"); /* not an artist's: it stays */
    key(KEY_RETURN);
    assert(force_closes == 1 && backs == 2);
    wm_n = 0;
    close_page();
    assert(!coverflow_rated(no_hearts)); /* nothing rated: no page */
    page = &w[nw + 1];
    assert(coverflow_rated(five_hearts));
    run();
    s = slide();
    int n = s->nkids - 1, lib = saved_sort();
    unlink(PEQ_ROOT "/mnt/data/ringnav-ratedsort");
    static const char *const steps[] = { "Sort: Album", "Sort: Artist" };
    for (int k = 0; k < 2; ++k) {
        w[s->kids[n]].click(w[s->kids[n]].ctx, 0);
        run();
        s = slide();
        assert(rated_sort() == k && saved_sort() == lib && *(int *)(s->raw + SLIDE_INDEX) == n);
        assert(!strcmp(album_caption(&w[s->parent]), steps[k]));
    }
    close_page();
    page = &w[nw + 1];
    assert(coverflow_goto_album(records[0]));
    assert(!strcmp(title(), names[0]) && !slide() && coverflow_shows_album(records[0]) && !coverflow_shows_album(records[1]));
    backs = 0;
    key(KEY_RETURN);
    assert(backs == 1); /* no covers under it: Return closes the page */
    close_page();
    assert(!coverflow_shows_album(records[0]));
    static char gone[0x60];
    *(const char **)(gone + REC_ALBUM) = "Nowhere", *(const char **)(gone + REC_PATH) = "/x";
    assert(!coverflow_goto_album(gone)); /* no tracks: nothing opens */
    window_fail = 1;
    assert(!coverflow_goto_album(records[0]) && !coverflow_rated(five_hearts)); /* no page to open */
    window_fail = 0;
    /* An artist page's albums with no memory to sort them keep their order. */
    void *mine = _create_deque(0);
    _deque_push_back(mine, records[5]), _deque_push_back(mine, records[7]);
    ranked[0] = "C", ranked[1] = 0;
    calloc_fail = 1;
    coverflow_by_year(mine);
    calloc_fail = 0;
    assert(deque_at(mine, 0) == records[5] && deque_at(mine, 1) == records[7]);
    coverflow_by_year(mine);
    assert(deque_at(mine, 0) == records[7] && deque_at(mine, 1) == records[5]);
    ranked[0] = 0;
    free(mine);
}

/* The build's Cancel row, as Return: the album in hand finishes, the rest wait for the next open. */
static void cancel_build(void) {
    album("Hold", "cover.jpg"), album("Back", "cover.jpg");
    rescan();
    block_at = calls;
    widget home;
    memset(&home, 0, sizeof(home));
    coverflow_home(&home, 0);
    page = &w[nw + 1];
    home.click(home.ctx, 0);
    while (!blocked) usleep(1000);
    widget *row = row_under(page, 0);
    assert(!strncmp(title(), "Preparing artwork", 17) && row && !strcmp(label_in(row, 0)->text, "Cancel"));
    row->click(row->ctx, 0);
    pthread_t t;
    joining = 0;
    pthread_create(&t, 0, release, 0);
    run();
    pthread_join(t, 0);
    assert(size("Hold") == 9 && size("Back") == -1 && !tmp_files() && slide() && slide()->visible);
    close_page();
    block_at = -1;
    open_page();
    close_page();
    assert(size("Back") == 9);
    albums -= 2;
    rescan();
}

/* Letters last until the lift: a slow turn, a still thumb and a turn back keep them, the row leaning
   the way they began, each letter re-centring it on that letter's first album, the letter held. */
static void thumb_letters_held(void) {
    static const cf_style_t shipped = CF_SHIPPED;
    const cf_style_t fixture = shim_themes[1].cf;
    shim_themes[1].cf = shipped;
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0;
    open_page();
    widget *s = slide(), *covers_view = &w[s->parent];
    int n = s->nkids, *index = (int *)(s->raw + SLIDE_INDEX);
    top_window = page;
    th_pass(0, 17);
    /* on its way to a letter's first album, its letter held; there, at rest on it */
    #define GOING() (FIRST(TARGET()) && th[T_GOAL] == roundf(th[T_GOAL]) && letter_shown == (unsigned)initial_of(TARGET()))
    #define LANDED() (th[T_POS] == 0 && th[T_GOAL] == 0 && CAPTION(*index) && FIRST(*index) && \
                      letter_shown == (unsigned)initial_of(*index))
    int end = albums - 1, canvas[16] = { 0 };
    static unsigned wrapped[CF_VIEW_H * CF_VIEW_W];
    while (end && initial_of(end - 1) == initial_of(end)) --end; /* the last letter's first album */
    th_down();
    int was = letters, t = 0;
    while (letters == was && t < 2000) th_spin(150, 17), t += 17;
    /* the first letter: the row going on to its first album from short of it */
    assert(letters > was && th[T_JUMP] && th[T_LEAN] == -1 && letter_held && GOING() && th[T_GOAL] == 0 &&
           th[T_POS] < 0 && FIRST(*index));
    for (t = 0; t < 1000 && TARGET() != end; t += 17) {
        was = letters;
        th_spin(150, 17);
        assert(th[T_JUMP] && th[T_LEAN] == -1 && letter_held && GOING());
    }
    th_arrive();
    /* past the last letter, round to the first's first album, a step like any: a click, its letter */
    assert(*index == end && LANDED());
    int r = 0;
    for (t = 0; t < 1000 && TARGET() == end; t += 17) r |= th_pass(-30, 17);
    assert(TARGET() == 0 && r & 4 /* THUMB_CLICK */ && th[T_JUMP] && th[T_LEAN] == -1 && GOING());
    th_arrive();
    assert(*index == 0 && LANDED());
    /* the held row across the seam: the albums before the first are the last ones, drawn as on any
       landing there (the same frame as a step back onto it, below) */
    for (int f = 0; f < 12; ++f) coverflow_paint(s, canvas);
    memcpy(wrapped, shown, sizeof shown);
    int side[2] = { 0, 0 }; /* drawn either side of the middle: the left, the row's back, about 60% */
    for (int i = 0; i < CF_VIEW_H * CF_VIEW_W; ++i) side[i % CF_VIEW_W >= CF_CX] += shown[i] != shown[0];
    assert(side[1] > 5000 && side[0] * 2 > side[1]); /* blanks past the seam would leave about 12% */
    /* and back from the first, round to the last letter's first album */
    for (r = 0, t = 0; t < 1000 && !TARGET(); t += 17) r |= th_pass(30, 17);
    assert(TARGET() == end && r & 4 && th[T_JUMP] && th[T_LEAN] == -1 && GOING());
    th_arrive();
    assert(*index == end && LANDED());
    /* back: a letter back each jump_ticks, the row still leaning the way letters began */
    int back = 0, last = *index;
    for (t = 0; t < 500; t += 17) {
        was = letters;
        th_spin(-40, 17);
        assert(th[T_JUMP] && th[T_LEAN] == -1 && letter_held);
        if (letters == was) continue;
        assert(GOING() && TARGET() < last);
        ++back, last = TARGET();
    }
    assert(back >= 2);
    /* back to the first letter, a step back onto it: the frame the wrap drew */
    for (t = 0; t < 3000 && TARGET(); t += 17) th_spin(-40, 17);
    th_arrive();
    assert(*index == 0 && LANDED() && th[T_LEAN] == -1);
    for (int f = 0; f < 12; ++f) coverflow_paint(s, canvas);
    assert(!memcmp(wrapped, shown, sizeof shown));
    /* slowly, far under fast_at: on by letters still, one each jump_ticks */
    was = letters;
    th_spin(10, 2000);
    int went = letters - was, want = 2000 * 10 / 1000 / shipped.jump_ticks;
    assert(th[T_JUMP] && th[T_LEAN] == -1 && went >= want - 1 && went <= want + 1 && GOING());
    th_arrive();
    assert(LANDED());
    /* a thumb resting on the wheel, past hold_ms: still letters, the letter still shown */
    was = letters, end = *index;
    for (t = 0; t < shipped.hold_ms + 500; t += 17) th_pass(0, 17);
    assert(th[T_JUMP] && th[T_LEAN] == -1 && letter_held && letters == was && LANDED());
    th_rest();
    assert(*index == end && !letter_held && !th[T_JUMP] && !th[T_LEAN]);
    /* At 200%, a slow turn: never more than a letter a pass. */
    test_wheel_pct = 200;
    th_down();
    for (was = letters, t = 0; letters == was && t < 2000; t += 17) th_spin(150, 17);
    for (t = 0; t < 600; t += 17) {
        was = letters;
        th_spin(-15, 17);
        assert(letters - was <= 1 && th[T_JUMP]);
    }
    th_rest();
    test_wheel_pct = 50;
    /* Every album under one initial: letters, each step on that letter's (the first) album, as the
       wrap comes back round to it. */
    char first[64];
    for (int i = 0; i < albums; ++i) first[i] = names[i][0], names[i][0] = 'M';
    th_down();
    for (was = letters, t = 0; letters == was && t < 2000; t += 17) th_spin(150, 17);
    assert(letters > was && th[T_JUMP]);
    for (t = 0; t < 800; t += 17) th_spin(t < 400 ? 40 : -40, 17), assert(*index == 0 && letter_shown == 'M');
    th_rest();
    for (int i = 0; i < albums; ++i) names[i][0] = first[i];
    #undef LANDED
    #undef GOING
    close_page();
    top_window = 0;
    th_pass(0, 17);
    shim_themes[1].cf = fixture;
}

/* ---- Each step in letters slides the held row there, through the albums between, on a follower
   slide_ms long: on at once from where it is, never back, never past the letter's first album, the
   caption on the album nearest the middle as it passes, the letter and the one click at the step. A
   step before it arrives goes on from there without stopping; round the seam it goes forward through
   it; a lift lets it arrive, then folds. ---- */
int cf_cover_x(int o, int frac);
static int target_x(int *index, int n, int to) { /* the letter's cover's middle on the screen, 1/16 px */
    int q = (int)floorf(th[T_POS] + .5f), c = ((*index + q) % n + n) % n, o = ((to - c) % n + n + n / 2) % n - n / 2;
    return cf_cover_x(th[T_FOLD] ? 0 : o, (int)((th[T_POS] - (float)q) * ONE));
}
static void letters_slide(void) {
    static const cf_style_t shipped = CF_SHIPPED;
    static unsigned before[CF_VIEW_H * CF_VIEW_W];
    const cf_style_t fixture = shim_themes[1].cf;
    shim_themes[1].cf = shipped;
    float near = (float)shipped.fold_near / 100; /* folding from as far out as it goes: at a speed worth measuring */
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0;
    open_page();
    widget *s = slide(), *covers_view = &w[s->parent];
    int n = s->nkids, *index = (int *)(s->raw + SLIDE_INDEX), canvas[16] = { 0 };
    top_window = page;
    th_pass(0, 17);
    #define WRAP(a) ((((a) % n) + n) % n)
    int tail = albums - 1;
    while (tail && initial_of(tail - 1) == initial_of(tail)) --tail; /* the last letter's first album */
    th_down();
    for (int t = 0; !(th[T_JUMP] && th[T_ENTER] <= 0) && t < 2000; t += 17) th_spin(150, 17);
    /* the first: the row on its way to it from short of it, at the entry's speed */
    assert(th[T_JUMP] && th[T_POS] < 0 && th[T_VEL] > 0 && th[T_GOAL] == 0 && FIRST(*index));
    th_arrive();
    if (*index == tail) th_step(1), th_arrive(); /* not at the seam yet */
    float at = (float)*index, was_at;
    /* (dir, lift): a step on; one back; on to the last letter and round the seam; back round it; a
       step and a lift on the way */
    for (int k = 0; k < 5; ++k) {
        int dir = k == 1 || k == 3 ? -1 : 1;
        if (k == 2) {
            while (TARGET() != tail) th_step(1);
            th_arrive();
            assert(*index == tail);
        }
        int from = *index;
        th_at(index, n, &at);
        int r = th_step(dir), to = TARGET(), passes = 0, clicks = r & 4 ? 1 : 0;
        float goal = at + (float)(dir > 0 ? WRAP(to - from) : -WRAP(from - to)), most = 0;
        assert(r & 4 && FIRST(to) && to != from && letter_held && letter_shown == (unsigned)initial_of(to));
        if (k == 2) assert(to == 0); /* round the seam, forward through it */
        if (k == 3) assert(to == tail);
        float d0 = fabsf(goal - at);
        int xs[3] = { 0, 0, target_x(index, n, to) };
        if (k == 4) wheel_held = 0; /* the lift, on the way */
        while (th[T_JUMP] && passes < 200) {
            was_at = at;
            memcpy(before, shown, sizeof shown);
            clicks += th_pass(0, 17) & 4 ? 1 : 0;
            th_at(index, n, &at);
            float moved = (at - was_at) * (float)dir;
            most = moved > most ? moved : most;
            /* on, never back, never past it, no faster than a follower slide_ms long from rest goes;
               lifted, folding in the pass the row comes within fold_near of it, from there */
            assert(moved >= 0 && (goal - at) * (float)dir >= -1e-4f);
            if (th[T_JUMP]) assert(moved <= d0 * 17 / ((float)shipped.slide_ms * 2.7f) + 1e-3f);
            if (k == 4) assert(th[T_JUMP] ? fabsf(goal - at) > near : fabsf(th[T_FOLD_OFF]) <= near && th[T_FOLD_OFF] != 0);
            xs[0] = xs[1], xs[1] = xs[2], xs[2] = target_x(index, n, to);
            assert(CAPTION(WRAP((int)lroundf(at))) && TARGET() == to);
            if (th[T_POS] == th[T_GOAL] && *index == to) break; /* arrived */
            coverflow_paint(s, canvas);
            if (moved > 1e-3f) assert(memcmp(before, shown, sizeof shown)); /* a frame each pass */
            ++passes;
        }
        assert(clicks == 1 && passes > 3 && most > 0);
        if (k < 4) {
            assert(th[T_JUMP] && *index == to && th[T_POS] == 0 && CAPTION(to) && letter_held);
            continue;
        }
        /* the lift: the fold begins once the row is near, on that letter's first album, and goes on at
           the speed the row had: on the screen the letter's cover moves on as it did, no step */
        assert(th[T_FOLD] && *index == to && !letter_held);
        th_pass(0, 17);
        /* The follower's tail goes at about .63 of its distance a slide_ms (measured, fold_vel against
           the left over), at most fold_near of an album: .63 x .5 x 33 px x 17 ms / 160 ms is about
           1.1 px a pass, so 12/16 px is a speed worth measuring (it was 16 at slide_ms 120 and a
           fold_near of 40); a fold that stopped would be off by all of it, against the 30% and 4/16 px. */
        int was_v = xs[2] - xs[1], v = target_x(index, n, to) - xs[2];
        assert(abs(was_v) > 12 && abs(v - was_v) <= abs(was_v) * 3 / 10 + 4);
        for (int t = 0; t < CF_FOLD_MS + 34 && th[T_ON]; t += 17) th_pass(0, 17);
        assert(!th[T_ON] && *index == to && CAPTION(to) && !*(int *)(s->raw + SLIDE_OFFSET));
    }
    /* A step before the row arrives: on from where it is, at the speed it has, to the letter after the
       one it was going to. */
    th_down();
    for (int t = 0; !(th[T_JUMP] && th[T_ENTER] <= 0) && t < 2000; t += 17) th_spin(150, 17);
    if (*index == tail) th_step(1), th_arrive();
    at = (float)*index;
    th_step(1);
    int first = TARGET();
    for (int i = 0; i < 3; ++i) was_at = at, th_pass(0, 17), th_at(index, n, &at);
    float v0 = th[T_VEL], m0 = at - was_at;
    was_at = at;
    th_step(1);
    th_at(index, n, &at);
    int second = WRAP(first + 1);
    while (!FIRST(second)) second = WRAP(second + 1);
    assert(TARGET() == second && th[T_VEL] * v0 > 0 && at - was_at >= m0 * .9f);
    th_arrive();
    assert(*index == second && th[T_POS] == 0 && CAPTION(second));
    th_rest();
    #undef WRAP
    close_page();
    top_window = 0;
    th_pass(0, 17);
    shim_themes[1].cf = fixture;
}

/* ---- The lift's hand-off, at fold_near hundredths of an album: the follower goes on till the row is
   within fold_near of the letter's first album, then the fold takes the rest (th.fold_off). Within
   half an album the album nearest the middle, the caption's, is already the letter's, so fold_near
   is at most 50 (a hundredth over folds while the caption still names the album before, and names
   the letter's only from the hand-off); at exactly half an album the two tie. Swept both ways, over
   letters of one album each and of sixty, lifting at every pass of a step's slide: the fold starts
   on the step's letter, its first album, within half an album of it, the caption on it, and lands
   there. In every theme (Classic and Modern take Minimal's by extends) fold_near is the same and at
   most 50, and the sweep reaches a hand-off over a third of an album out. ---- */
static void lift_handoff(void) {
    static const cf_style_t shipped[3] = { CF_SHIPPED0, CF_SHIPPED, CF_SHIPPED2 };
    static char nm[101][32];
    const cf_style_t fixture = shim_themes[1].cf;
    int keep = albums, starts[3] = { keep + 5, keep + 30, keep + 45 }, runs = 0;
    float far = 0;
    for (int t = 0; t < 3; ++t) assert(shipped[t].fold_near == shipped[1].fold_near && shipped[t].fold_near <= 50);
    for (int i = 0; i < 101; ++i) /* 40 letters of one album ('0' to 'W'), one of 60, one of one */
        snprintf(nm[i], 32, "%c lift %03d", i < 40 ? '0' + i : i < 100 ? 'X' : 'Y', i), album(nm[i], 0);
    rescan();
    shim_themes[1].cf = shipped[1];
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0;
    open_page();
    widget *s = slide(), *covers_view = &w[s->parent];
    int n = s->nkids, *index = (int *)(s->raw + SLIDE_INDEX);
    top_window = page;
    th_pass(0, 17);
    for (int dir = 1; dir >= -1; dir -= 2)
        for (int from = 0; from < 3; ++from)
            for (int lift = 0; lift < 40; ++lift) { /* passes of the slide before the lift */
                slide_menu_set_value(s, starts[from]);
                th_down();
                for (int t = 0; !(th[T_JUMP] && th[T_ENTER] <= 0) && t < 2000; t += 17) th_spin(150 * dir, 17);
                assert(th[T_JUMP] && th[T_ENTER] <= 0);
                th_arrive();
                assert(FIRST(*index));
                th_step(dir);
                int to = TARGET();
                for (int i = 0; i < lift && th[T_POS] != th[T_GOAL]; ++i) th_pass(0, 17);
                wheel_held = 0;
                for (int i = 0; i < 60 && !th[T_FOLD]; ++i) th_pass(0, 17);
                float off = fabsf(th[T_FOLD_OFF]);
                assert(th[T_FOLD] && *index == to && FIRST(to) && CAPTION(to) && off <= .5f);
                far = off > far ? off : far;
                th_rest();
                assert(!th[T_ON] && *index == to && CAPTION(to));
                ++runs;
            }
    assert(runs == 240 && far > 1.f / 3);
    close_page();
    top_window = 0;
    th_pass(0, 17);
    albums = keep;
    rescan();
    shim_themes[1].cf = fixture;
}

/* ---- Into letters on the wheel, both ways: from the pass letters begin, the row goes on enter_albums
   at the spin's speed (CF_ENTER_SPEED at least), each pass a frame of the entry, no letter yet and
   nothing else moving; the facing point runs on ahead the way the spin goes, the covers behind it
   turned the lean's way, against the spin, and none ahead of it, till the facing cover is off the
   screen. Then, in one pass, the first letter, drawn as the held row, the row going on at the entry's
   speed through its own albums, the rest of the pass's follow on, to the first letter far enough on
   for that follow to land with no overshoot, and none before the one after the caption's album. A
   lift on the way in lets the entry end and the row come near, then folds onto that letter's first
   album. ---- */
static void letters_entry(void) {
    static const cf_style_t shipped = CF_SHIPPED;
    static unsigned before[CF_VIEW_H * CF_VIEW_W];
    const cf_style_t fixture = shim_themes[1].cf;
    shim_themes[1].cf = shipped;
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0;
    open_page();
    widget *s = slide(), *covers_view = &w[s->parent];
    int n = s->nkids, *index = (int *)(s->raw + SLIDE_INDEX), *offset = (int *)(s->raw + SLIDE_OFFSET);
    int canvas[16] = { 0 };
    top_window = page;
    th_pass(0, 17);
    for (int dir = 1; dir >= -1; dir -= 2)
        for (int lift = 0; lift < 2; ++lift) {
            int lean = -dir; /* against the spin: the covers behind the facing cover's turn */
            th_down();
            int was = letters, t = 0, named = -1;
            unsigned letter;
            while (!th[T_JUMP] && t < 2000) th_spin(150 * dir, 17), t += 17;
            for (int a = 0; a < n; ++a) named = CAPTION(a) ? a : named; /* where the wheel had got to */
            float travel = shipped.enter_albums, pos = th[T_POS], lead = th[T_LEAD], speed = fabsf(th[T_VEL]) * 1000, face = 0;
            int q = (int)floorf(pos + .5f), frac = (int)((pos - q) * ONE);
            int face_x = 0;
            float left = 0;
            assert(th[T_JUMP] && th[T_LEAN] == lean && th[T_ENTER] == travel && letters == was && !letter_held);
            speed = speed > CF_ENTER_SPEED ? speed : CF_ENTER_SPEED;
            if (lift) wheel_held = 0;
            coverflow_paint(s, canvas);
            int passes = 0;
            for (; th[T_ENTER] > 0; ++passes) {
                int qd[15][6], centre, k = cf_enter_quads(frac, lead, th[T_ENTER], lean, &centre, qd), x;
                float at = cf_enter_face(frac, lead, th[T_ENTER], lean, &x);
                assert(!passes || ((at - face) * dir > 0 && (x - face_x) * dir >= 0)); /* the facing point runs on */
                face = at, face_x = x;
                for (int i = 0; i < k; ++i) {
                    int a = qd[i][0], d = qd[i][4] - qd[i][3];
                    float u = ((float)a - at) * dir;
                    assert(a > -32 && a < 32);
                    if (quad_cols(qd[i]) && (u < -1e-3f || u > 1e-3f)) /* behind it the lean's turn; ahead, not */
                        assert(u < 0 ? d * lean >= 0 : d * lean <= 0);
                }
                memcpy(before, shown, sizeof shown);
                left = th[T_ENTER];
                int r = lift ? th_pass(0, 17) : (th_spin(150 * dir, 17), 1);
                assert(r & 1);
                if (th[T_ENTER] <= 0) break;
                assert(letters == was && !letter_held && !th[T_FOLD] && th[T_POS] == pos && th[T_LEAD] == lead);
                coverflow_paint(s, canvas); /* a frame each pass, the covers on */
                assert(memcmp(before, shown, sizeof shown));
            }
            /* enter_albums at the spin's speed: at most enter_albums / CF_ENTER_SPEED s, and a pass */
            assert(fabsf(passes * 17 - travel * 1000 / speed) <= 17 && passes * 17 <= travel * 1000 / CF_ENTER_SPEED + 17);
            int out[6], x;
            face = cf_enter_face(frac, lead, 0, lean, &x);
            cf_enter_quad(frac, lead, 0, lean, (int)floorf(face + .5f), out);
            assert(!quad_cols(out) && x * dir > 16 * CF_CX); /* the facing cover is off the screen */
            int at = *index;
            assert(letters == was + 1 && th[T_ENTER] == 0 && (!at || initial_of(at - 1) != initial_of(at)));
            /* the first letter: the row p from its first album (the album at the middle then) at the
               entry's speed, followed for the rest of the pass alone (th_follow's, from p at speed) */
            float rest = 17 - left / (speed / 1000), tau = shipped.slide_ms, e = expf(-rest / tau), v0 = dir * speed / 1000;
            float p = (th[T_POS] / e - v0 * rest) / (1 + rest / tau), b = v0 + p / tau, qf = p + b * rest;
            assert(th[T_JUMP] && th[T_GOAL] == 0 && letter_held && letter_shown == (unsigned)initial_of(at));
            assert(fabsf(th[T_VEL] - (b - qf / tau) * e) < 2e-4f && th[T_VEL] * dir > 0 && th[T_POS] * dir < 0);
            /* far enough on to land with no overshoot; the letter before it too near, or the
               caption's album's own */
            int pf = coverflow_letter_next(s, at, -dir, n, &letter), pr = dir > 0 ? -((at - pf + n) % n) : (pf - at + n) % n;
            assert(-p * dir >= speed * tau / 1000 - 1e-3f && b * dir <= 0);
            assert(named >= 0 && (at == coverflow_letter_next(s, named, dir, n, &letter) || ((float)pr - p) * dir < speed * tau / 1000));
            assert(*offset && CAPTION(((at + (int)floorf(th[T_POS] + .5f)) % n + n) % n));
            if (!lift) { /* the held row drawn */
                coverflow_paint(s, canvas);
                memcpy(before, shown, sizeof shown);
                cf_thumb_lean(0), coverflow_paint(s, canvas), cf_thumb_lean(lean), coverflow_paint(s, canvas);
                assert(!memcmp(before, shown, sizeof shown));
                th_rest();
            } else { /* the row comes near, then folds onto it at the speed it has, then at rest on it */
                for (t = 0; t < 1000 && !th[T_FOLD]; t += 17) th_pass(0, 17);
                assert(th[T_FOLD] && !th[T_JUMP] && th[T_LEAN] == lean && !letter_held && !letter_shown);
                assert(th[T_FOLD_VEL] * dir > 0 && fabsf(th[T_FOLD_OFF]) * 100 <= shipped.fold_near);
                for (t = 0; t < CF_FOLD_MS + 17 && th[T_ON]; t += 17) th_pass(0, 17);
                assert(!th[T_ON] && *index == at && CAPTION(at) && !*offset);
            }
        }
    close_page();
    top_window = 0;
    th_pass(0, 17);
    shim_themes[1].cf = fixture;
}

/* ---- Into letters on a held spin, both ways, at 100 and 150 ticks a second: from the row at speed
   through the overtake and the first letter to three letter steps on, every frame's covers go on the
   way they went, the median cover's move a pass never back and never under 70% of the row's before
   the entry, nor jumping on as the letter comes; no cover goes back during the entry; as the facing
   point stops running every cover within the row's turn is off the screen. Across the first letter
   every cover stays on its album, moved on by the slide alone, no art changing, to the nearest letter
   whose first album is v slide_ms or more on from the album at the middle (where the follow from the
   entry's speed v lands with no overshoot) and past the caption's own letter, either way. (CF_HANDOVER_LOG
   set: each pass's median move and the row's spacing, px.) ---- */
static void entry_handover(void) {
    static const cf_style_t shipped = CF_SHIPPED;
    static char nm[200][32];
    const cf_style_t fixture = shim_themes[1].cf;
    int keep = albums;
    assert(keep + 200 <= 256);
    for (int i = 0; i < 200; ++i) snprintf(nm[i], 32, "%c handover %03d", 'A' + i / 8, i), album(nm[i], 0);
    rescan();
    shim_themes[1].cf = shipped;
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0;
    open_page();
    widget *s = slide(), *covers_view = &w[s->parent];
    int n = s->nkids, *index = (int *)(s->raw + SLIDE_INDEX);
    #define WRAP(a) ((((a) % n) + n) % n)
    top_window = page;
    th_pass(0, 17);
    for (int rate = 100; rate <= 150; rate += 50)
        for (int dir = 1; dir >= -1; dir -= 2) {
            int lean = -dir, was = letters, last = 0, key[2][15], seen[2][15], checked = 0, named = -1;
            float mx[2][15], row = 0, before = 0;
            th_down();
            for (int pass = 0, cur = 0; pass < 600 && letters < was + 4; ++pass, cur ^= 1) {
                int entering = th[T_JUMP] && th[T_ENTER] > 0;
                float left = th[T_ENTER], vel0 = th[T_VEL];
                th_spin(rate * dir, 17);
                if (th[T_JUMP] && th[T_ENTER] > 0 && named < 0) /* the caption's album as letters began */
                    for (int a = 0; a < n; ++a) named = CAPTION(a) ? a : named;
                if (entering && th[T_ENTER] <= 0) {
                    /* the first letter: p from its first album, the album at the middle as it began
                       (th_follow's from p at the entry's speed, the rest of the pass), as far on as
                       the follow needs, and no letter's first album nearer that is, past the
                       caption's own letter (forward, past its album; back, before its letter's first) */
                    float speed = fabsf(vel0) > CF_ENTER_SPEED / 1000.f ? fabsf(vel0) : CF_ENTER_SPEED / 1000.f;
                    float rest = 17 - left / speed, tau = shipped.slide_ms, e = expf(-rest / tau), v0 = dir * speed;
                    float p = (th[T_POS] / e - v0 * rest) / (1 + rest / tau), need = speed * tau - 1e-3f;
                    int at = *index, bound = (WRAP(named - at) + n / 2) % n - n / 2;
                    while (dir < 0 && !FIRST(WRAP(at + bound))) --bound;
                    assert(named >= 0 && FIRST(at) && -p * dir >= need);
                    for (int r = -dir; (r - p) * dir >= need; r -= dir)
                        if (FIRST(WRAP(at + r)) && (r - bound) * dir > 0) {
                            fprintf(stderr, "entry_handover: %d ticks/s dir %d: the first letter at %d, %.2f from the middle; %d, %.2f, was nearer\n",
                                    rate, dir, at, -p, WRAP(at + r), r - p);
                            assert(0);
                        }
                }
                int q = (int)floorf(th[T_POS] + .5f), frac = (int)((th[T_POS] - q) * ONE), c = *index + q;
                int out[15][6], k = cf_frame_quads(frac, out), x;
                if (th[T_JUMP] && th[T_ENTER] > 0 && !checked) { /* as the facing point stops running */
                    float stop = shipped.enter_albums * (100 - ENTER_RUN) / 100.f;
                    int face[6];
                    float at = cf_enter_face(frac, th[T_LEAD], stop, lean, &x);
                    cf_enter_quad(frac, th[T_LEAD], stop, lean, (int)floorf(at + .5f), face);
                    assert(!quad_cols(face) && !turned_on(frac, th[T_LEAD], stop, lean, at));
                    checked = 1;
                }
                float dx[15];
                int m = 0;
                for (int i = 0; i < k; ++i) {
                    key[cur][i] = ((c + out[i][0]) % n + n) % n;
                    seen[cur][i] = quad_cols(out[i]) > 0, mx[cur][i] = (out[i][1] + out[i][2]) / 32.f;
                    for (int j = 0; j < last && seen[cur][i]; ++j)
                        if (seen[cur ^ 1][j] && key[cur ^ 1][j] == key[cur][i]) dx[m++] = mx[cur][i] - mx[cur ^ 1][j];
                }
                int was_k = last;
                last = k;
                if (!th[T_LEAD] && !th[T_JUMP]) { last = 0; continue; } /* not at speed yet */
                if (!was_k || m < 3) continue;
                for (int i = 0; i < m; ++i) /* sorted, for the median */
                    for (int j = i + 1; j < m; ++j)
                        if (dx[j] < dx[i]) { float t = dx[i]; dx[i] = dx[j], dx[j] = t; }
                float med = -dir * dx[m / 2], gap = 0;
                for (int i = 0; i < k; ++i)
                    for (int j = 0; j < k; ++j)
                        if (seen[cur][i] && seen[cur][j] && key[cur][j] == ((key[cur][i] - dir) % n + n) % n) gap = fabsf(mx[cur][i] - mx[cur][j]);
                if (getenv("CF_HANDOVER_LOG"))
                    fprintf(stderr, "handover %d %d %d %d %d %.2f %.2f\n", rate, dir, pass, entering, letters - was, med, gap);
                if (!th[T_JUMP]) { row = med; continue; } /* the row at speed, the last pass before */
                if (entering)
                    for (int i = 0; i < m; ++i) assert(-dir * dx[i] >= 0); /* no cover goes back */
                /* the pass that ends the entry: the rest of the pass's follow, a pass's worth, not
                   more, and every cover on its album, as far on as the rest */
                if (entering && th[T_ENTER] <= 0) {
                    assert(med <= 1.25f * before && m >= 8);
                    for (int i = 0; i < m; ++i) assert(fabsf(-dir * dx[i] - med) <= 2);
                }
                before = med;
                if (!(row > 0 && med >= .7f * row)) {
                    fprintf(stderr, "entry_handover: %d ticks/s dir %d pass %d (entering %d, letters %d): the median cover %.2f px a pass, the row's %.2f\n",
                            rate, dir, pass, entering, letters - was, med, row);
                    assert(0);
                }
            }
            assert(letters >= was + 4);
            th_rest();
        }
    #undef WRAP
    close_page();
    top_window = 0;
    th_pass(0, 17);
    albums = keep;
    rescan();
    shim_themes[1].cf = fixture;
}

/* ---- Real spins into letters (test/fixtures/coverflow_spins.json, CF_SPINS as main() writes it: a
   Q2's wheel at its frame times, on that card's initials), Classic, each pass's frame as it is drawn.
   The halt: from the entry to five passes into letters, no cover on the screen moves under 1.25x the
   entry's drift (the row's median) for more than 110 ms, which is what the eye reads as a stop once
   only the row's slivers drift: six passes of 17 ms. The climb: in the first 25 passes of letters
   the median cover's speed never grows more than 1.6x from a pass to the next. Each lands on the
   first album of the letter its letters went to. (CF_SPINS_LOG set: each run's numbers.) ---- */
static void real_spins(void) {
    static const cf_style_t shipped = CF_SHIPPED;
    static char nm[600][32], lib[600], keep_rec[64][0x60], keep_nm[64][64], keep_pa[64][600];
    static float med[600], top[600];
    static int ms_of[600], mode[600];
    const cf_style_t fixture = shim_themes[1].cf;
    FILE *in = fopen(getenv("CF_SPINS"), "r");
    assert(in);
    int got = fscanf(in, "%599s", lib);
    assert(got == 1);
    /* the card's albums alone: the ones here aside till the end */
    int keep = albums, nlib = (int)strlen(lib);
    assert(keep <= 64);
    memcpy(keep_rec, records, sizeof keep_rec), memcpy(keep_nm, names, sizeof keep_nm), memcpy(keep_pa, paths, sizeof keep_pa);
    albums = 0;
    for (int i = 0; i < nlib; ++i) snprintf(nm[i], 32, "%c spin %03d", lib[i] == '#' ? '0' : lib[i], i), album(nm[i], 0);
    rescan();
    shim_themes[1].cf = shipped;
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0;
    open_page();
    widget *s = slide();
    int n = s->nkids, *index = (int *)(s->raw + SLIDE_INDEX);
    assert(n == nlib);
    top_window = page;
    th_pass(0, 17);
    char name[8];
    int start, lands, passes, runs = 0;
    while (fscanf(in, "%7s %d %d %d", name, &start, &lands, &passes) == 4) {
        int key[2][64], kn[2] = { 0, 0 }, e = -1, h = -1;
        float kx[2][64];
        assert(passes <= 600);
        slide_menu_set_value(s, start);
        for (int p = 0, cur = 0; p < passes; ++p, cur ^= 1) {
            int travel, held, read = fscanf(in, "%d %d %d", &ms_of[p], &travel, &held);
            assert(read == 3);
            wheel_held = held;
            th_pass(travel, ms_of[p]);
            int on = th[T_ON] && !th[T_FOLD], q = (int)floorf(th[T_POS] + .5f), out[64][6];
            int k = on ? cf_frame_quads((int)((th[T_POS] - q) * ONE), out) : 0;
            mode[p] = !on || !th[T_JUMP] ? 0 : th[T_ENTER] > 0 ? 4 : 1;
            if (mode[p] == 1) { /* the letter held, going to its first album as the card has them ('#': '0') */
                int to = ((*index + (int)roundf(th[T_GOAL])) % n + n) % n, c = initial_of(to);
                assert(letter_held && FIRST(to) && letter_shown == (unsigned)(c == '0' ? '#' : c));
            }
            if (e < 0 && mode[p] == 4) e = p;
            if (e >= 0 && h < 0 && mode[p] == 1) h = p;
            /* every cover lit here and a pass before, by album: its move, px a ms */
            float dx[64];
            int m = 0;
            kn[cur] = 0;
            for (int i = 0; i < k; ++i) {
                if (out[i][5] <= 0) continue;
                int a = ((*index + q + out[i][0]) % n + n) % n;
                float x = (out[i][1] + out[i][2]) / 32.f;
                key[cur][kn[cur]] = a, kx[cur][kn[cur]++] = x;
                for (int j = 0; j < kn[cur ^ 1] && x > -190 && x < 190; ++j)
                    if (key[cur ^ 1][j] == a) dx[m++] = fabsf(x - kx[cur ^ 1][j]) / ms_of[p];
            }
            for (int i = 0; i < m; ++i)
                for (int j = i + 1; j < m; ++j)
                    if (dx[j] < dx[i]) { float t = dx[i]; dx[i] = dx[j], dx[j] = t; }
            med[p] = m ? (dx[(m - 1) / 2] + dx[m / 2]) / 2 : -1, top[p] = m ? dx[m - 1] : -1;
        }
        assert(e >= 0 && h > e && h + 25 <= passes);
        float d[600], drift, step = 0;
        int nd = 0, halt = 0, run = 0;
        for (int p = e; p < h; ++p)
            if (med[p] > 0) d[nd++] = med[p];
        for (int i = 0; i < nd; ++i)
            for (int j = i + 1; j < nd; ++j)
                if (d[j] < d[i]) { float t = d[i]; d[i] = d[j], d[j] = t; }
        assert(nd);
        drift = (d[(nd - 1) / 2] + d[nd / 2]) / 2;
        for (int p = e; p < h + 6; ++p) {
            run = top[p] >= 0 && top[p] < 1.25f * drift ? run + ms_of[p] : 0;
            halt = run > halt ? run : halt;
        }
        for (int p = h, was = -1; p < h + 25; ++p)
            if (med[p] > 0) {
                if (was >= 0 && med[p] / med[was] > step) step = med[p] / med[was];
                was = p;
            }
        th_rest(); /* the fold, if the lift's is still to come */
        if (getenv("CF_SPINS_LOG"))
            fprintf(stderr, "spin %s: drift %.3f px/ms, halt %d ms, step %.2fx, lands %d\n", name, drift, halt, step, *index);
        if (!(halt <= 110 && step <= 1.6f && *index == lands)) {
            fprintf(stderr, "real_spins: %s: halt %d ms, step %.2fx, lands on %d, not %d\n", name, halt, step, *index, lands);
            assert(0);
        }
        ++runs;
    }
    assert(runs == 7);
    fclose(in);
    close_page();
    top_window = 0;
    th_pass(0, 17);
    memcpy(records, keep_rec, sizeof keep_rec), memcpy(names, keep_nm, sizeof keep_nm), memcpy(paths, keep_pa, sizeof keep_pa);
    albums = keep;
    rescan();
    shim_themes[1].cf = fixture;
}

/* ---- Turning back in letters, mid-slide, either way, Classic: with letters of 80 albums the step
   back can put the letter far behind the row. Every pass in letters its speed grows at most by
   e^(ms / slide_ms) from the last pass's going the same way, or from CF_ENTER_SPEED turning back, so the
   row never turns round at many times the spin's speed; and the lift lands on the letter the steps
   went to. Swept over where in the slide the turn comes. ---- */
static void letters_turn_back(void) {
    static const cf_style_t shipped = CF_SHIPPED;
    static char nm[184][32];
    const cf_style_t fixture = shim_themes[1].cf;
    int keep = albums, sizes[5] = { 8, 80, 8, 80, 8 }, reversed = 0;
    for (int l = 0, i = 0; l < 5; ++l)
        for (int k = 0; k < sizes[l]; ++k, ++i) snprintf(nm[i], 32, "%c turn %03d", 'A' + l, i), album(nm[i], 0);
    rescan();
    shim_themes[1].cf = shipped;
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0;
    open_page();
    widget *s = slide();
    int n = s->nkids, *index = (int *)(s->raw + SLIDE_INDEX);
    float least = CF_ENTER_SPEED / 1000.f, grow = expf(17.f / shipped.slide_ms) * 1.01f;
    top_window = page;
    th_pass(0, 17);
    for (int dir = 1; dir >= -1; dir -= 2)
        for (int at = 0; at < 60; at += 2) { /* passes into letters before the turn */
            slide_menu_set_value(s, dir > 0 ? keep : keep + 183);
            th_down();
            int in = 0, was_in = 0, k = 0;
            for (int t = 0; t < 4000 && k < at + 40; t += 17) {
                float v0 = th[T_VEL];
                th_spin(150 * (k < at ? dir : -dir), 17);
                in = th[T_JUMP] && th[T_ENTER] <= 0 && !th[T_FOLD];
                if (in && was_in) {
                    float v = th[T_VEL], from = v * v0 > 0 && fabsf(v0) > least ? fabsf(v0) : least;
                    reversed |= v * v0 < 0;
                    if (fabsf(v) > from * grow) {
                        fprintf(stderr, "letters_turn_back: dir %d turn at %d, pass %d: %.2f albums/s from %.2f\n",
                                dir, at, k, v * 1000, v0 * 1000);
                        assert(0);
                    }
                }
                k += in, was_in = in;
            }
            assert(k == at + 40);
            int to = ((*index + (int)roundf(th[T_GOAL])) % n + n) % n;
            th_rest();
            assert(*index == to && FIRST(to));
        }
    assert(reversed);
    close_page();
    top_window = 0;
    th_pass(0, 17);
    albums = keep;
    rescan();
    shim_themes[1].cf = fixture;
}

/* The lift in letters folds the row back out, CF_FOLD_MS long, onto the letter's first album, the wheel
   steering nothing meanwhile; then the thumb lets go there. Centre, Return, a touch or leaving the
   page, in letters or folding, land at once on the caption's album, with no fold. */
static void fold_flow(void) {
    static const cf_style_t shipped = CF_SHIPPED;
    const cf_style_t fixture = shim_themes[1].cf;
    shim_themes[1].cf = shipped;
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0;
    open_page();
    widget *s = slide(), *covers_view = &w[s->parent];
    int n = s->nkids, *index = (int *)(s->raw + SLIDE_INDEX), *offset = (int *)(s->raw + SLIDE_OFFSET);
    top_window = page;
    th_pass(0, 17);
    #define LETTERS(ticks) do { \
        th_down(); \
        for (int t_ = 0, l_ = letters; letters == l_ && t_ < 2000; t_ += 17) th_spin(ticks, 17); \
        assert(th[T_JUMP] && letter_held); } while (0)
    #define LIFT() do { \
        wheel_held = 0; \
        for (int i_ = 0; i_ < 60 && th[T_JUMP]; ++i_) th_pass(0, 17); } while (0)
    #define LANDED_AT(a) (!th[T_ON] && !th[T_FOLD] && !th[T_JUMP] && !th[T_LEAN] && *index == (a) && \
                          CAPTION(a) && !*offset && !letter_shown && !letter_held)
    for (int last = 0; last < 3; ++last) { /* a letter; past the last, round to the first; a letter back */
        int dir = last == 2 ? -1 : 1, lean = -dir; /* the row leans against the spin */
        LETTERS(150 * dir);
        if (last == 1)
            for (int t = 0; t < 3000 && initial_of(TARGET()) != initial_of(albums - 1); t += 17) th_spin(150, 17);
        if (last == 1)
            for (int i = 0, end = TARGET(); i < 10 && TARGET() == end; ++i) th_pass(-30, 17);
        th_arrive();
        int at = *index;
        assert(last != 1 || at == 0); /* the fold across the seam: the last albums before it */
        assert(!at || initial_of(at - 1) != initial_of(at)); /* a letter's first album */
        LIFT();
        /* at once: folding, the letter gone, the ring still on the letter's first album */
        unsigned from = clock_ms;
        assert(th[T_ON] && th[T_FOLD] && !th[T_JUMP] && th[T_LEAN] == lean && !letter_held && !letter_shown);
        assert(*index == at && CAPTION(at) && !*offset && th[T_POS] == 0 && th[T_GOAL] == 0);
        /* a turn during the fold moves nothing and clicks nothing; every pass asks for the next */
        wheel_held = 1;
        while (clock_ms + 17 - from < (unsigned)CF_FOLD_MS - 60) {
            int r = th_pass(-30, 17);
            assert(r == 1 /* THUMB_MOVED */ && th[T_FOLD] && *index == at && !*offset && th[T_POS] == 0);
        }
        wheel_held = 0;
        while (clock_ms + 17 - from < (unsigned)CF_FOLD_MS) {
            assert(th_pass(0, 17) == 1 && th[T_FOLD] && *index == at);
        }
        th_pass(0, 17);
        assert(LANDED_AT(at));
        th_rest();
        assert(LANDED_AT(at));
    }
    /* the other ways out, in letters (folding 0), folding (1) or on the way in (2): Centre and
       Return (both coverflow_wheel_land), a touch, the page left; on the way in and in letters, the
       row on its way to the letter, at once on the album the caption names */
    for (int folding = 0; folding < 3; ++folding)
        for (int way = 0; way < 3; ++way) {
            if (folding == 2) {
                th_down();
                for (int t_ = 0; !th[T_JUMP] && t_ < 2000; t_ += 17) th_spin(150, 17);
                assert(th[T_JUMP] && th[T_ENTER] > 0 && !letter_shown);
            } else
                LETTERS(150); /* the row on its way to the letter: on the album it passes */
            int at = *index;
            if (folding != 1) { /* the album the caption names: ahead of the covers, or the row's */
                th_pass(0, 17);
                for (int d = 0; d < n; ++d)
                    if (CAPTION((*index + d) % n)) { at = (*index + d) % n; break; }
                assert((folding ? th[T_ENTER] > 0 : th[T_POS] != 0) && CAPTION(at));
            }
            if (folding == 1) {
                LIFT();
                th_pass(0, 17);
                assert(th[T_FOLD]);
            }
            if (way == 0) coverflow_wheel_land(), cf_thumb(th);
            if (way == 1) pressed = 1, th_pass(0, 17);
            if (way == 2) top_window = &w[1], th_pass(0, 17);
            assert(LANDED_AT(at));
            pressed = 0, top_window = page, wheel_held = 0;
            th_pass(0, 17);
            th_rest();
            assert(LANDED_AT(at));
        }
    /* a load or a close dropping the covers mid-fold (th.on 0) leaves the fold behind: the wheel's
       next turn takes the covers afresh */
    LETTERS(150);
    LIFT();
    assert(th[T_FOLD]);
    cf_thumb_lead(-1);
    th_down();
    assert(th_pass(-30, 17) & 2 /* THUMB_TURNED */);
    assert(th[T_ON] && !th[T_FOLD] && th[T_GOAL] != 0);
    th_rest();
    /* ... and in letters, the thumb still down: the next turn takes the covers afresh, the held
       letter gone with its letters */
    LETTERS(150);
    cf_thumb_lead(-1);
    assert(th_pass(-30, 17) & 2 /* THUMB_TURNED */);
    assert(th[T_ON] && !th[T_JUMP] && !th[T_FOLD] && !letter_held && !letter_shown);
    th_rest();
    /* a touch in the fold's last contact_ms is nothing past the landing */
    LETTERS(150);
    int at = *index;
    LIFT();
    unsigned from = clock_ms;
    th_pass(0, CF_FOLD_MS - 25 - (int)(clock_ms - from));
    wheel_held = 1;
    th_pass(-30, 10), th_pass(-30, 10); /* under contact_ms: held over, not yet the thumb's */
    assert(th[T_FOLD]);
    th_pass(0, 10);
    assert(!th[T_ON] && *index == at);
    for (int i = 0; i < 6; ++i) th_pass(0, 17);
    assert(LANDED_AT(at));
    wheel_held = 0;
    th_rest();
    /* but in an ordinary settle's, a touch's first contact_ms of travel still counts: twice the
       same slow turn and lift, the second touched again the pass before it comes to rest */
    int settle = 0;
    for (int again = 0; again < 2; ++again) {
        th_down();
        for (int i = 0; i < 10; ++i) th_pass(-3, 17);
        wheel_held = 0;
        int i = 0;
        for (; th[T_ON] && (!again || i < settle - 1); ++i) th_pass(0, 17);
        if (!again) {
            settle = i;
            continue;
        }
        assert(th[T_ON]);
        wheel_held = 1;
        th_pass(-30, 17); /* the settle's last pass, under contact_ms */
        assert(!th[T_ON]);
        for (int k = 0; k < 4; ++k) th_pass(0, 17);
        assert(th[T_ON] && th[T_GOAL] != 0);
        th_rest();
    }
    #undef LETTERS
    #undef LIFT
    #undef LANDED_AT
    close_page();
    top_window = 0;
    th_pass(0, 17);
    shim_themes[1].cf = fixture;
}

/* Only the Album and Artist sorts go by letters, and Year by years (lettered): in Recently Added,
   Most Played, Random and the rated albums' orders, Album's among them, a spin at the cap well past jump_ms stays the fast row,
   album by album, with no entry, lean or letter, and a lift lands as it always did, with no fold.
   Album first: the same spin goes by letters there. */
static void at_cap(int by_letters) {
    widget *s = slide(), *covers_view = &w[s->parent];
    int n = s->nkids, *index = (int *)(s->raw + SLIDE_INDEX), *offset = (int *)(s->raw + SLIDE_OFFSET);
    top_window = page;
    th_pass(0, 17);
    th_down();
    int at = *index;
    float pos = th[T_POS], went = 0;
    char named[160];
    for (int t = 0; t < 1500; t += 17) {
        th_spin(150, 17);
        float d = (float)(((*index - at) % n + n + n / 2) % n - n / 2) + th[T_POS] - pos;
        at = *index, pos = th[T_POS], went += d;
        if (by_letters) continue;
        assert(!th[T_JUMP] && !th[T_LEAN] && !th[T_ENTER] && !th[T_FOLD] && !letter_shown && !letter_held);
        assert(d > -.01f && d < .5f); /* on, never past an album in a pass */
    }
    if (by_letters) {
        assert(th[T_JUMP] && letter_held);
        coverflow_wheel_land(), cf_thumb(th);
    } else { /* a stop from speed lands on the album the caption names */
        snprintf(named, sizeof named, "%s", album_caption(covers_view));
        assert(went > 10);
        wheel_held = 0;
        for (int i = 0; i < 3; ++i) th_pass(0, 17), assert(!th[T_FOLD] && !th[T_JUMP] && !letter_shown);
    }
    th_rest();
    assert(!*offset && !letter_shown && !letter_held);
    assert(by_letters ? CAPTION(*index) : !strcmp(album_caption(covers_view), named));
}
static void unlettered_sorts(void) {
    static const cf_style_t shipped = CF_SHIPPED;
    const cf_style_t fixture = shim_themes[1].cf;
    shim_themes[1].cf = shipped;
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0;
    open_page();
    assert(saved_sort() == 0);
    big_library = 0; /* Fast Navigation off: Year at the cap, far past jump_ms, stays albums (Album: letters_176) */
    while (saved_sort() != 6) press_sort();
    at_cap(0);
    while (saved_sort() != 0) press_sort();
    big_library = 1;
    at_cap(1);
    static const int sorts[] = { 2 /* Recently Added */, 3 /* Most Played */, 4 /* Random */ };
    for (unsigned i = 0; i < sizeof sorts / sizeof *sorts; ++i) {
        while (saved_sort() != sorts[i]) press_sort();
        at_cap(0);
    }
    while (saved_sort() != 0) press_sort();
    close_page();
    page = &w[nw + 1];
    extern int coverflow_rated(int (*hearts)(void *));
    assert(coverflow_rated(five_hearts)); /* scoped, in its own order: Rating first */
    run();
    at_cap(0);
    close_page();
    /* scoped alone keeps them off: the rated albums in Album order, a letters order elsewhere */
    FILE *f = fopen(PEQ_ROOT "/mnt/data/ringnav-ratedsort", "wb");
    int album_sort = 0;
    assert(f && fwrite(&album_sort, sizeof album_sort, 1, f) == 1 && !fclose(f));
    page = &w[nw + 1];
    assert(coverflow_rated(five_hearts));
    run();
    at_cap(0);
    close_page();
    unlink(PEQ_ROOT "/mnt/data/ringnav-ratedsort");
    shim_themes[1].cf = fixture;
    classic = 0, square_art = 0, top_window = 0;
}

/* ---- Letters of one or two albums (#276): a fast spin, on and then back, steps to each letter in
   turn, every letter's first album, round from the last to the first and back from the first to the
   last, however far the row has got: each step goes on from the letter the row is going to, not
   from where it is. A spin fast enough to step a letter a pass, and a slow one; then a lift lands on
   the last letter shown. ---- */
static void short_letters(void) {
    static const cf_style_t shipped = CF_SHIPPED;
    static const char lib[] = "ABBCDDEFFGH"; /* 8 letters of one or two albums */
    static char nm[sizeof lib][32], keep_rec[64][0x60], keep_nm[64][64], keep_pa[64][600];
    const cf_style_t fixture = shim_themes[1].cf;
    int keep = albums, nlib = (int)strlen(lib);
    assert(keep <= 64);
    memcpy(keep_rec, records, sizeof keep_rec), memcpy(keep_nm, names, sizeof keep_nm), memcpy(keep_pa, paths, sizeof keep_pa);
    albums = 0;
    for (int i = 0; i < nlib; ++i) snprintf(nm[i], 32, "%c short %02d", lib[i], i), album(nm[i], 0);
    rescan();
    shim_themes[1].cf = shipped;
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0;
    open_page();
    widget *s = slide(), *covers_view = &w[s->parent];
    int n = s->nkids, *index = (int *)(s->raw + SLIDE_INDEX);
    assert(n == nlib);
    top_window = page;
    th_pass(0, 17);
    for (int k = 0; k < 2; ++k) { /* 300 ticks a second, a letter or more a pass; 80, under one */
        float rate = k ? 80 : 300;
        slide_menu_set_value(s, k ? 5 : 0);
        th_down();
        for (int t = 0; !(th[T_JUMP] && th[T_ENTER] <= 0) && t < 2000; t += 17) th_spin(rate, 17);
        assert(th[T_JUMP] && th[T_ENTER] <= 0 && FIRST(TARGET()));
        int at = TARGET(), seen[2][8] = { { 0 } }, rounds[2] = { 0, 0 };
        for (int d = 0; d < 2; ++d) /* on, then back */
            for (int p = 0; p < 200; ++p) {
                int was = letters;
                th_spin(d ? -rate : rate, 17);
                for (int i = was; i < letters; ++i) { /* each step a letter on (back) from the last */
                    int to = at;
                    do to = (to + (d ? -1 : 1) + n) % n; while (lib[to] == lib[at]);
                    while (!FIRST(to)) --to;
                    rounds[d] += d ? to > at : to < at;
                    at = to;
                    seen[d][lib[at] - 'A'] = 1;
                }
                assert(TARGET() == at && letter_held && letter_shown == (unsigned)lib[at]);
            }
        for (int d = 0; d < 2; ++d) {
            for (int l = 0; l < 8; ++l) assert(seen[d][l]);
            assert(rounds[d] >= 2); /* round the ends, and on after them */
        }
        th_rest();
        assert(!th[T_ON] && *index == at && CAPTION(at));
    }
    close_page();
    top_window = 0;
    th_pass(0, 17);
    memcpy(records, keep_rec, sizeof keep_rec), memcpy(names, keep_nm, sizeof keep_nm), memcpy(paths, keep_pa, sizeof keep_pa);
    albums = keep;
    rescan();
    shim_themes[1].cf = fixture;
}

/* Year goes by years as Album and Artist go by letters, in the Year order either way: the same spin
   enters them, each step lands on the first album of the next year that has albums, its year held
   (CF_YEAR), the undated last as CF_YEAR alone, then round to the first year and back; a lift folds
   onto the year's first album. The query ranks the dated and B (undated); the albums it missed come after. */
static void by_years(int old) {
    static const char *const order[2][10] = {
        { "New", "A", "C", "Tight", "Cover", "B", "Folder", "Embedded", "None", 0 },
        { "Cover", "C", "Tight", "New", "A", "B", "Folder", "Embedded", "None", 0 },
    };
    static const int year[2][9] = { { 2001, 2001, 1994, 1994, 1987 }, { 1987, 1994, 1994, 2001, 2001 } };
    static const int firsts[2][4] = { { 0, 2, 4, 5 }, { 0, 1, 3, 5 } }; /* each year's first album */
    static const cf_style_t shipped = CF_SHIPPED;
    const cf_style_t fixture = shim_themes[1].cf;
    year_old = old;
    for (int i = 0; i < 6; ++i) ranked[i] = order[old][i], rank_year[i] = year[old][i];
    ranked[6] = 0;
    open_page();
    while (saved_sort() != 6) press_sort();
    order_is(order[old]);
    close_page();
    shim_themes[1].cf = shipped;
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0;
    open_page();
    widget *s = slide(), *covers_view = &w[s->parent];
    int *index = (int *)(s->raw + SLIDE_INDEX), *offset = (int *)(s->raw + SLIDE_OFFSET), n = s->nkids;
    #define NAMED(a) (!strcmp(album_caption(covers_view), order[old][a]))
    #define TO_YEAR(a) (letter_held && letter_shown == (CF_YEAR | (unsigned)year[old][a]) && \
                        (!(a) || year[old][(a) - 1] != year[old][a]))
    #define AT_YEAR(a) (NAMED(a) && TO_YEAR(a))
    top_window = page;
    th_pass(0, 17);
    th_down();
    int t = 0, was = letters, prev = -1;
    while (letters == was && t < 2000) th_spin(150, 17), t += 17;
    /* the rate rising to big_jump_at, the entry at CF_ENTER_SPEED or more */
    assert(th[T_JUMP] && t <= shipped.big_jump_ms + 100 + shipped.enter_albums * 1000 / CF_ENTER_SPEED);
    for (int i = 0; i < 60 && TARGET() != firsts[old][3]; ++i, th_spin(150, 17)) /* on: later years, then the undated */
        if (TARGET() != prev) assert(TARGET() > prev && TO_YEAR(TARGET())), prev = TARGET();
    th_arrive();
    assert(*index == firsts[old][3] && AT_YEAR(*index) && letter_shown == CF_YEAR && !*offset);
    int r = 0;
    prev = *index;
    for (t = 0; TARGET() == prev && t < 1000; t += 17) r |= th_pass(-30, 17); /* on past the undated: the first year */
    assert(TARGET() == 0 && r & 4 && TO_YEAR(0));
    th_arrive();
    assert(*index == 0 && AT_YEAR(0));
    for (r = 0, t = 0; !TARGET() && t < 1000; t += 17) r |= th_pass(30, 17); /* and back round: the undated */
    th_arrive();
    assert(*index == firsts[old][3] && r & 4 && AT_YEAR(*index));
    prev = *index;
    for (int k = 2; k >= 0; --k) { /* back: each year before, to the first */
        for (t = 0; TARGET() == prev && t < 2000; t += 17) th_spin(-150, 17);
        th_arrive();
        assert(*index == firsts[old][k] && AT_YEAR(*index) && !*offset);
        prev = *index;
    }
    for (t = 0; !TARGET() && t < 1000; t += 17) th_spin(150, 17); /* on a year, then the lift */
    int at = TARGET();
    assert(at > 0 && TO_YEAR(at));
    wheel_held = 0;
    for (int i = 0; i < 100 && th[T_JUMP]; ++i) th_pass(0, 17); /* the row arrives, then folds */
    assert(th[T_FOLD] && !letter_held && !letter_shown && *index == at);
    th_rest();
    assert(*index == at && !*offset && NAMED(at) && !th[T_FOLD] && !th[T_JUMP]);
    #undef NAMED
    #undef TO_YEAR
    #undef AT_YEAR
    close_page();
    memset(rank_year, 0, sizeof rank_year), ranked[0] = 0, year_old = 0;
    shim_themes[1].cf = fixture;
    classic = 0, square_art = 0, top_window = 0;
}

/* ---- Half copies for letters (docs/internals.md#coverflow-depth): made once from a decoded cover,
   or by idle passes at rest from the cache, beside its file, whole or not at all; read with no decode
   and spread two by two. Going by letters each frame loads while it has spent under FX_LETTERS_MS:
   first the covers on the screen, then those ahead of the row towards the letter it goes
   to, that letter's, then the next and the previous letters', each window as far from its letter's
   first album as the screen's is from the album at the middle, and full art over the screen's half
   copies once the row is there. Four letters of 40 albums make the windows apart. ---- */
int cf_fx_has(int a), cf_fx_letters(void);
unsigned cf_album_key(int a), album_key(void *r);
void coverflow_halves(void);
int coverflow_letter_next(void *w, int from, int dir, int n, unsigned *letter);
static void cache_file(unsigned key, const char *suffix, char *path) {
    snprintf(path, 600, PEQ_ROOT "/mnt/mmc/.coverflow/%08x.jpg%s", key, suffix);
}
static long size_of(const char *path) { struct stat st; return stat(path, &st) ? -1 : (long)st.st_size; }
static int halves(int remove) { /* the half copies on the card; removed, if asked */
    int k = 0;
    DIR *d = opendir(PEQ_ROOT "/mnt/mmc/.coverflow");
    for (struct dirent *e; d && (e = readdir(d));) {
        size_t l = strlen(e->d_name);
        if (l < 5 || strcmp(e->d_name + l - 5, ".half")) continue;
        char path[600];
        snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/.coverflow/%s", e->d_name);
        if (remove) unlink(path);
        ++k;
    }
    if (d) closedir(d);
    return k;
}
static void cover_tex(int a, unsigned *tex) { /* album a's cover as decode() makes it of the shim's */
    static unsigned char px[160 * 160 * 4];
    char path[600];
    unsigned seed = 0;
    cache_file(cf_album_key(a), "", path);
    for (const char *c = strrchr(path, '/'); *c; ++c) seed = seed * 31 + (unsigned char)*c;
    pattern(px, seed % 1000);
    for (int i = 0; i < 160 * 160; ++i) tex[i] = 0xff000000u | px[4 * i] | px[4 * i + 1] << 8 | px[4 * i + 2] << 16;
}
static int read_half(int a, unsigned *half) { /* album a's half copy, 80 x 80 */
    char path[600];
    cache_file(cf_album_key(a), ".half", path);
    FILE *f = fopen(path, "rb");
    int ok = f && fread(half, 4, 80 * 80, f) == 80 * 80;
    if (f) fclose(f);
    return ok;
}
static void half_art(void) {
    static const cf_style_t shipped = CF_SHIPPED;
    static unsigned tex[160 * 160], half[80 * 80], wide[15][160 * 160], f[300 * PITCH];
    const cf_style_t fixture = shim_themes[1].cf;
    char path[600], name[8];
    shim_themes[1].cf = shipped;
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0, light = 0, decode_ms = 0;
    for (int g = 0; g < 4; ++g)
        for (int i = 0; i < 40; ++i) snprintf(name, sizeof name, "%c%02d", "QRUV"[g], i), album(name, 0);
    mkdir(PEQ_ROOT "/mnt/mmc/.coverflow", 0755);
    for (int i = 0; i < albums; ++i) { /* every album cached: no build */
        cache_file(album_key(records[i]), "", path);
        FILE *fp = fopen(path, "w");
        fputs("jpg", fp);
        fclose(fp);
    }
    rescan();
    halves(1);
    open_page();
    while (saved_sort() != 0) press_sort();
    close_page();
    open_page();
    widget *s = slide();
    int n = s->nkids, *index = (int *)(s->raw + SLIDE_INDEX), canvas[16] = { 0 };
    top_window = page;
    assert(n == albums);
    /* At rest the shelf's seven decode at once and no half copy is written in a frame: the idle
       passes make them, each texel the rounded mean of four, once: a copy there already is kept as
       it is. Done, an idle pass returns before it asks the card for its space. */
    coverflow_paint(s, canvas);
    assert(!halves(0) && tex_slots == 16); /* fx_base's 15 and the placeholder: no slot of the copies' */
    big_library = 0; /* Fast Navigation off: the idle passes make none */
    for (int i = 0; i < 3 * albums; ++i) th_pass(0, 17);
    assert(!halves(0));
    /* Opened with it off and turned on: the copies start at once, no reopen. */
    close_page(), open_page(), s = slide(), index = (int *)(s->raw + SLIDE_INDEX), top_window = page;
    coverflow_paint(s, canvas);
    assert(tex_slots == 16);
    big_library = 1;
    for (int i = 0; i < 3 * albums && !halves(0); ++i) th_pass(0, 17), coverflow_paint(s, canvas);
    assert(halves(0));
    halves(1);
    close_page(), open_page(), s = slide(), index = (int *)(s->raw + SLIDE_INDEX), top_window = page;
    coverflow_paint(s, canvas);
    assert(tex_slots == 16);
    for (int i = 0; i < 80 * 80; ++i) half[i] = 0xff000000u | i;
    cache_file(cf_album_key(*index), ".half", path);
    FILE *fp = fopen(path, "wb");
    fwrite(half, 4, 80 * 80, fp); /* another copy in its place */
    fclose(fp);
    for (int i = 0; i < 20 * albums && halves(0) < albums; ++i) th_pass(0, 17);
    assert(halves(0) == albums && !tmp_files());
    static unsigned kept[80 * 80];
    assert(read_half(*index, kept) && !memcmp(kept, half, sizeof half));
    for (int d = -3; d <= 3; ++d) {
        int a = ((*index + d) % n + n) % n;
        cache_file(cf_album_key(a), ".half", path);
        assert(size_of(path) == 80 * 80 * 4 && read_half(a, half));
        if (!d) continue;
        cover_tex(a, tex);
        for (int y = 0; y < 80; ++y)
            for (int x = 0; x < 80; ++x)
                for (int k = 0; k < 32; k += 8) {
                    const unsigned *p = tex + 2 * y * 160 + 2 * x;
                    unsigned sum = (p[0] >> k & 255) + (p[1] >> k & 255) + (p[160] >> k & 255) + (p[161] >> k & 255);
                    assert((half[y * 80 + x] >> k & 255) == (sum + 2) / 4);
                }
    }
    unsigned asked = statfs_calls;
    for (int i = 0; i < 5; ++i) th_pass(0, 17);
    assert(statfs_calls == asked);
    halves(1);
    /* Idle passes at rest make the missing ones, at most one a pass and one each HALF_GAP_MS; closing
       stops them, the next open goes on, from the first album, till every album has one. */
    close_page(), open_page(), s = slide(), index = (int *)(s->raw + SLIDE_INDEX), top_window = page;
    int made = halves(0), passes = 0;
    for (; passes < 40; ++passes) {
        th_pass(0, 17);
        assert(halves(0) - made <= 1);
        made = halves(0);
    }
    assert(made >= 40 * 17 / HALF_GAP_MS && made <= 40 * 17 / HALF_GAP_MS + 1 && made < albums);
    /* none while a thumb rests on the wheel, and no decode with the card short of MIN_FREE_MB */
    wheel_held = 1;
    for (int i = 0; i < 5; ++i) th_pass(0, 17);
    assert(halves(0) == made);
    wheel_held = 0, free_blocks = 4000;
    int decoded = loads;
    for (int i = 0; i < 5; ++i) th_pass(0, 17);
    assert(halves(0) == made && loads == decoded);
    free_blocks = 1 << 20;
    th_pass(0, 17);
    assert(halves(0) == made + 1);
    made = halves(0);
    /* the copies leave the larger of 512 MB and 5% of the card free: 512 MB on an 8 GB card, 5% on
       a 64 GB one, to the block */
    unsigned edge[][2] = { { 1 << 21, 131072 }, { 1 << 24, 838861 } };
    for (int e = 0; e < 2; ++e) {
        card_blocks = edge[e][0], free_blocks = edge[e][1] - 1;
        for (int i = 0; i < 7; ++i) th_pass(0, 17);
        assert(halves(0) == made);
        free_blocks = edge[e][1];
        th_pass(0, 17);
        assert(halves(0) == ++made);
    }
    card_blocks = 1 << 21, free_blocks = 1 << 20;
    close_page(), top_window = 0;
    for (int i = 0; i < 10; ++i) th_pass(0, 17);
    assert(halves(0) == made);
    open_page(), s = slide(), index = (int *)(s->raw + SLIDE_INDEX), top_window = page;
    for (int i = 0; i < 20 * albums && halves(0) < albums; ++i) th_pass(0, 17);
    assert(halves(0) == albums && !tmp_files());
    /* Every album with its copy, a decode 9 ms: into letters on a held spin from early in Q, the
       entry reads a copy or two a frame and decodes nothing, and the first letter's frame, the
       entry's row going on, has its covers; a lift mid-slide folds decoding nothing; landed, the
       screen's covers sharpen one a frame, the landing cover first. */
    {
        static int was[256];
        int to = -1, entered = 0;
        close_page(), open_page(), s = slide(), index = (int *)(s->raw + SLIDE_INDEX), top_window = page;
        for (int a = 0; a < n; ++a) *index = strcmp(names[a], "Q04") ? *index : a;
        assert(!strcmp(names[*index], "Q04"));
        th_pass(0, 17);
        for (int i = 0; i < 20; ++i) coverflow_paint(s, canvas);
        decode_ms = 9, half_ms = 1;
        th_down();
        for (int pass = 0, folded = 0; pass < 400 && (th[T_ON] || !folded); ++pass) {
            for (int a = 0; a < n; ++a) was[a] = cf_fx_has(a);
            int l0 = loads, read = 0;
            if (th[T_JUMP] && th[T_ENTER] <= 0 && wheel_held && th[T_POS] != th[T_GOAL] && to < 0)
                wheel_held = 0, to = TARGET(); /* lifted on the way to the first letter */
            if (wheel_held) th_spin(150, 17); else th_pass(0, 17);
            int entering = th[T_JUMP] && th[T_ENTER] > 0;
            folded |= th[T_FOLD] != 0;
            coverflow_paint(s, canvas);
            for (int a = 0; a < n; ++a) read += cf_fx_has(a) == 1 && was[a] != 1;
            if (entering) assert(loads == l0 && read <= 2);
            if (th[T_JUMP] && !entering && !entered++) { /* the first letter's frame: each cover it draws */
                int q = (int)floorf(th[T_POS] + .5f), out[64][6], k = cf_frame_quads((int)((th[T_POS] - q) * ONE), out);
                assert(names[*index][0] == 'R' && k);
                for (int i = 0; i < k; ++i) assert(!quad_cols(out[i]) || was[((*index + q + out[i][0]) % n + n) % n]);
            }
            if (th[T_FOLD]) assert(loads == l0);
        }
        assert(to >= 0 && *index == to);
        for (int f = 0, l0 = loads; f < 15; ++f, l0 = loads) {
            coverflow_paint(s, canvas);
            assert(loads - l0 <= 1 && (f || cf_fx_has(to) == 2));
        }
        for (int a = 0; a < n; ++a)
            if (abs((((a - to) % n) + n + n / 2) % n - n / 2) <= 3) assert(cf_fx_has(a) == 2);
        decode_ms = half_ms = 0;
        th_rest();
        shim_themes[1].cf = shipped;
    }
    /* A copy cut short is none: the cover's own art loads in its place. */
    halves(1);
    close_page(), open_page(), s = slide(), index = (int *)(s->raw + SLIDE_INDEX), top_window = page;
    th_pass(0, 17);
    #define WINDOW(a, c) (abs(((((a) - (c)) % n) + n + n / 2) % n - n / 2) <= 7) /* round the ring */
    #define TO_LETTER(l) do { \
        for (int t_ = 0; t_ < 500 && (TARGET() == *index || names[TARGET()][0] != (l)); ++t_) th_step(1); \
        th_arrive(); } while (0)
    th_down();
    for (int t = 0; !(th[T_JUMP] && th[T_ENTER] <= 0) && t < 2000; t += 17) th_spin(150, 17);
    TO_LETTER('Q');
    int q0 = *index, sh = cf_frame_window(0); /* the window's middle from the letter's first */
    assert(names[q0][0] == 'Q' && FIRST(q0));
    /* Held at a letter: the screen's covers in full art; the next and previous letters' windows
       loaded ahead, so a step finds the letter it goes to there. */
    for (int i = 0; i < 100; ++i) coverflow_paint(s, canvas);
    unsigned letter;
    int next = coverflow_letter_next(s, q0, 1, n, &letter), prev = coverflow_letter_next(s, q0, -1, n, &letter);
    for (int a = 0; a < n; ++a) {
        if (WINDOW(a, q0 + sh)) assert(cf_fx_has(a) == 2);
        if (WINDOW(a, next + sh) || WINDOW(a, prev + sh)) assert(cf_fx_has(a));
    }
    halves(1); /* from here, until the idle passes make them again, every load a decode */
    th_step(1);
    int to = TARGET();
    assert(to == next && names[to][0] == 'R');
    for (int a = 0; a < n; ++a)
        if (WINDOW(a, to + sh)) assert(cf_fx_has(a));
    th_step(1); /* on to U before the row is at R: U's window not loaded yet */
    to = TARGET();
    assert(names[to][0] == 'U' && FIRST(to));
    /* The order, one decode a frame (9 ms, over FX_BUDGET_MS) and the row held where it is: U's,
       nearest first, where the row will stop; the screen's, nearest first; ahead towards U; the
       next letter's (V); the previous one's (R, loaded). */
    decode_ms = 9;
    float pos = th[T_POS];
    int c = *index + (int)floorf(pos + .5f);
    c = (c + cf_frame_window((int)((pos - floorf(pos + .5f)) * ONE))) % n; /* the screen's window */
    int nx = coverflow_letter_next(s, to, 1, n, &letter) + sh, pv = coverflow_letter_next(s, to, -1, n, &letter) + sh;
    to += sh;
    #define AHEAD(a) ((((a) - c) % n + n) % n >= 8 && (((a) - c) % n + n) % n <= 22 && \
                      (((a) - c) % n + n) % n < ((to - c) % n + n) % n - 7)
    #define CAT(a) (WINDOW(a, to) ? 0 : WINDOW(a, c) ? 1 : AHEAD(a) ? 2 : WINDOW(a, nx) ? 3 : WINDOW(a, pv) ? 4 : -1)
    static int had[256];
    for (int a = 0; a < n; ++a) had[a] = cf_fx_has(a);
    int last_cat = 0, last_d = 0, frames = 0, loaded = 0;
    for (; frames < 80; ++frames) {
        int l0 = loads, got = -1;
        coverflow_paint(s, canvas);
        assert(loads - l0 <= 1);
        for (int a = 0; a < n; ++a)
            if (!had[a] && cf_fx_has(a)) assert(got < 0), got = a, had[a] = 1;
        if (got < 0) break;
        ++loaded;
        int cat = CAT(got), d = ((got - c) % n + n) % n;
        d = cat == 1 ? abs((d + n / 2) % n - n / 2) : d;
        if (cat == 0) d = abs((((got - to) % n) + n + n / 2) % n - n / 2); /* to: its window's middle */
        assert(cat >= last_cat && (cat != last_cat || cat > 2 || d >= last_d));
        last_cat = cat, last_d = d;
    }
    assert(loaded >= 14 + 15 + 15 && last_cat >= 3); /* ahead, U's and V's at least */
    for (int a = 0; a < n; ++a)
        if (CAT(a) >= 0 && CAT(a) <= 3) assert(cf_fx_has(a));
    assert(th[T_POS] == pos);
    /* Going by letters a frame's loads stop at FX_LETTERS_MS, or at FX_SLACK_MS in a frame
       FX_LATE_MS or more after the last: at 3 ms a decode, one (the first always starts), and late
       three (0, 3 and 6 ms in; a fourth would end at 12), the pool emptied each time. */
    decode_ms = 3;
    th_step(1);
    int l0 = loads;
    for (int late = 0; late < 2; ++late) {
        light = 1, coverflow_paint(s, canvas), light = 0;
        l0 = loads, clock_ms += late ? FX_LATE_MS : 0;
        coverflow_paint(s, canvas);
        assert(loads - l0 == (late ? 3 : 1));
    }
    /* With the idle passes' half copies: all a frame wants, the loader's LD_SLOTS a frame, no
       decode; the held row drawn from them is drawn as from the copies spread two by two. */
    wheel_held = 0;
    for (int i = 0; i < 200 && th[T_ON]; ++i) th_pass(0, 17);
    close_page(), open_page(), s = slide(), index = (int *)(s->raw + SLIDE_INDEX), top_window = page;
    for (int i = 0; i < 20 * albums && halves(0) < albums; ++i) th_pass(0, 17);
    assert(halves(0) == albums);
    th_down();
    for (int t = 0; !(th[T_JUMP] && th[T_ENTER] <= 0) && t < 2000; t += 17) th_spin(150, 17);
    TO_LETTER('Q');
    for (int i = 0; i < 100; ++i) coverflow_paint(s, canvas);
    th_step(1);
    to = TARGET();
    decode_ms = 9;
    light = 1, coverflow_paint(s, canvas), light = 0; /* the pool emptied */
    l0 = loads;
    int q = (int)floorf(th[T_POS] + .5f), qf = (int)((th[T_POS] - (float)q) * ONE);
    c = (*index + q) % n;
    int w0 = c + cf_frame_window(qf), waited = 0, got = 0; /* the window's middle */
    while (waited < 10 && got < n) {
        coverflow_paint(s, canvas), ++waited, got = 0;
        for (int a = 0; a < n; ++a) got += !(WINDOW(a, w0) || WINDOW(a, to + sh)) || cf_fx_has(a) == 1;
    }
    assert(loads == l0 && got == n && waited >= (30 + LD_SLOTS - 1) / LD_SLOTS);
    const unsigned *ring[15];
    for (int k = 0; k < 15; ++k) {
        assert(read_half(((w0 + k - 7) % n + n) % n, half));
        for (int y = 0; y < 160; ++y)
            for (int x = 0; x < 160; ++x) wide[k][y * 160 + x] = half[y / 2 * 80 + x / 2];
        ring[k] = wide[k];
    }
    coverflow_render(f, PITCH, qf, ring + 4);
    for (int y = 0; y < CF_VIEW_H; ++y) assert(!memcmp(f + y * PITCH, shown + y * CF_VIEW_W, CF_VIEW_W * 4)); /* shown's rows */
    /* a copy cut short: that album's own art instead, decoded, costed as a decode (9 ms) so in a
       frame of its own or the first of one */
    cache_file(cf_album_key(c), ".half", path);
    truncate(path, 100);
    l0 = loads;
    light = 1, coverflow_paint(s, canvas), light = 0;
    decode_ms = 0;
    for (int i = 0; i < 4 && cf_fx_has(c) != 2; ++i) coverflow_paint(s, canvas);
    assert(cf_fx_has(c) == 2 && loads > l0);
    /* There, the screen's covers turn to full art, three a frame at 3 ms, before the fold shows them. */
    decode_ms = 3;
    th_arrive();
    for (int i = 0; i < 20; ++i) {
        l0 = loads;
        coverflow_paint(s, canvas);
        assert(loads - l0 <= 3);
    }
    for (int a = 0; a < n; ++a)
        if (WINDOW(a, *index + sh)) assert(cf_fx_has(a) == 2);
    decode_ms = 0;
    th_rest();
    /* The entry's budget is FX_ENTER_MS: with no copies, one decode a frame at 3 ms a decode, two at
       1 ms (0 and 1 ms in; a third would end at 3), the pool emptied each time, from early in Q
       (every album with art). */
    halves(1);
    close_page(), open_page(), s = slide(), index = (int *)(s->raw + SLIDE_INDEX), top_window = page;
    for (int a = 0; a < n; ++a) *index = strcmp(names[a], "Q04") ? *index : a;
    th_pass(0, 17);
    th_down();
    for (int t = 0; !(th[T_JUMP] && th[T_ENTER] > 0) && t < 2000; t += 17) th_spin(150, 17);
    assert(th[T_JUMP] && th[T_ENTER] > 0);
    for (int b = 0; b < 2; ++b) {
        decode_ms = b ? 1 : 3;
        light = 1, coverflow_paint(s, canvas), light = 0;
        l0 = loads;
        coverflow_paint(s, canvas);
        assert(loads - l0 == b + 1);
    }
    decode_ms = 0;
    wheel_held = 0;
    for (int i = 0; i < 200 && th[T_ON]; ++i) th_pass(0, 17);
    th_rest();
    #undef WINDOW
    #undef TO_LETTER
    #undef AHEAD
    #undef CAT
    close_page();
    top_window = 0;
    th_pass(0, 17);
    /* Refresh Coverflow empties the cache, the half copies with it. */
    coverflow_refresh_art();
    assert(!halves(0));
    shim_themes[1].cf = fixture;
    classic = 0, square_art = 0;
}

/* ---- The loader: going by letters the half copies are read on a thread of its own, the nearest
   first, at most LD_SLOTS in hand; the UI thread only spreads one that is there into its texture,
   within FX_LETTERS_MS, and never waits on the card. A copy read for a page since closed is
   dropped; closing the page stops the thread after the read in hand,
   and nothing runs while the page is closed. Runs on half_art's library. ---- */
static double wall_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1e3 + t.tv_nsec / 1e6;
}
#define FILL_FRAMES 2 /* the frames after a letter step with a cover drawn waiting, at most */
static void loader_test(void) {
    static const cf_style_t shipped = CF_SHIPPED;
    const cf_style_t fixture = shim_themes[1].cf;
    char path[600];
    int n = albums, canvas[16] = { 0 }, *index;
    widget *s;
    shim_themes[1].cf = shipped;
    int slots = LD_SLOTS, win = 15; /* all it holds; the window of the letter gone to, asked for first */
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0, light = 0, decode_ms = 0, half_ms = 0;
    for (int i = 0; i < n; ++i) { /* every album cached again (half_art ended on Refresh) */
        cache_file(album_key(records[i]), "", path);
        FILE *fp = fopen(path, "w");
        fputs("jpg", fp);
        fclose(fp);
    }
    #define OPEN() (open_page(), s = slide(), index = (int *)(s->raw + SLIDE_INDEX), top_window = page)
    #define SHUT() (wheel_held = 0, close_page(), top_window = 0, th_pass(0, 17))
    #define INTO_LETTERS() do { \
        for (int a = 0; a < n; ++a) *index = strcmp(names[a], "Q04") ? *index : a; \
        th_pass(0, 17), th_down(); \
        for (int t = 0; !(th[T_JUMP] && th[T_ENTER] <= 0) && t < 2000; t += 17) th_spin(150, 17); \
        assert(th[T_JUMP] && th[T_ENTER] <= 0); } while (0)
    #define MIDDLE() ((*index + (int)floorf(th[T_POS] + .5f) + \
                       cf_frame_window((int)((th[T_POS] - floorf(th[T_POS] + .5f)) * ONE))) % n)
    #define GOAL() ((TARGET() + cf_frame_window(0) + n) % n) /* the window of the letter gone to */
    OPEN();
    for (int i = 0; i < 20 * n && halves(0) < n; ++i) th_pass(0, 17);
    assert(halves(0) == n);
    /* At rest no loader: it starts at the first copy asked for. */
    int started = ld_started;
    for (int i = 0; i < 20; ++i) coverflow_paint(s, canvas);
    assert(ld_started == started && !cf_ld(-1));
    /* Its order is fx_window's, the window of the letter gone to first, nearest its middle first,
       and it holds LD_SLOTS at most, however many frames ask. */
    INTO_LETTERS();
    ld_hold = 1;
    light = 1, coverflow_paint(s, canvas), light = 0; /* the pool emptied */
    assert(ld_started == started + 1);
    for (int i = 0; i < 50; ++i) coverflow_paint(s, canvas), assert(cf_ld(-1) == slots);
    #define NEAR(m) (((c + ((m) & 1 ? ((m) + 1) / 2 : -(m) / 2)) % n + n) % n)
    int c = GOAL();
    for (int m = 0; m < win; ++m) assert(cf_fx_has(NEAR(m)) == 0 && cf_ld(NEAR(m)) == 1);
    ++*index; /* the row an album on: the same requests asked for in another order, one swapped */
    coverflow_paint(s, canvas);
    c = GOAL();
    assert(cf_ld(-1) == slots);
    for (int m = 0; m < win; ++m)
        assert(cf_fx_has(NEAR(m)) == 0 && cf_ld(NEAR(m)) == 1 && cf_ld_step() && cf_ld(NEAR(m)) == 3);
    for (int m = win; m < slots; ++m) assert(cf_ld_step());
    assert(!cf_ld_step());
    /* Copies read and not yet taken give up their place to a cover wanted first: a step on, the
       window of the letter gone to is all loaded or in hand in that frame, what was not read yet
       asked for in the places of copies dropped. */
    ld_hold = 1, light = 1, coverflow_paint(s, canvas), light = 0, coverflow_paint(s, canvas);
    while (cf_ld_step()) {
    }
    assert(cf_ld(-1) == slots && cf_ld(NEAR(0)) == 3);
    th_step(1);
    coverflow_paint(s, canvas);
    c = GOAL();
    int asked = 0;
    for (int m = 0; m < win; ++m) {
        assert(cf_fx_has(NEAR(m)) || cf_ld(NEAR(m)) == 1 || cf_ld(NEAR(m)) == 3);
        asked += cf_ld(NEAR(m)) == 1;
    }
    assert(asked); /* every slot held a copy: asking took one */
    ld_hold = 0;
    SHUT();
    for (int i = 0; i < n; ++i) {
        cache_file(album_key(records[i]), "", path);
        FILE *fp = fopen(path, "w");
        fputs("jpg", fp);
        fclose(fp);
    }
    OPEN();
    for (int i = 0; i < 20 * n && halves(0) < n; ++i) th_pass(0, 17);
    assert(halves(0) == n);
    /* The install budget: at 1 ms a copy spread, as many a frame as FX_LETTERS_MS has ms, or as
       FX_SLACK_MS has in a frame two refreshes (FX_LATE_MS) after the last; the frame's ms go on
       the spreads alone, the reads being the loader's. */
    INTO_LETTERS();
    th_arrive(), th_step(1);
    for (int late = 0; late < 2; ++late) {
        int most = late ? FX_SLACK_MS : FX_LETTERS_MS;
        ld_hold = 1, light = 1, coverflow_paint(s, canvas), light = 0, coverflow_paint(s, canvas);
        ld_hold = 0, half_ms = 0; /* the pool emptied, slots asked for and read, the clock still */
        while (cf_ld_step()) {
        }
        half_ms = 1;
        static int was[256];
        int got = 0, l0 = loads;
        for (int a = 0; a < n; ++a) was[a] = cf_fx_has(a);
        clock_ms += late ? FX_LATE_MS : 0;
        unsigned t0 = clock_ms;
        coverflow_paint(s, canvas);
        for (int a = 0; a < n; ++a) got += cf_fx_has(a) == 1 && was[a] != 1;
        assert(got == most && loads == l0 && clock_ms - t0 == (unsigned)most);
    }
    /* A copy asked for and read missing is a full decode (9 ms), timed as one, not as a copy: in
       late frames the copies after it still go at half_ms, one in its frame and FX_SLACK_MS in the
       next. */
    {
        ld_hold = 1, light = 1, coverflow_paint(s, canvas), light = 0, coverflow_paint(s, canvas);
        int miss = GOAL();
        cache_file(cf_album_key(miss), ".half", path);
        assert(!remove(path));
        ld_hold = 0, half_ms = 0;
        while (cf_ld_step()) {
        }
        half_ms = 1, decode_ms = 9;
        for (int f = 0; f < 2; ++f) {
            static int was[256];
            int got = 0;
            for (int a = 0; a < n; ++a) was[a] = cf_fx_has(a);
            clock_ms += FX_LATE_MS;
            coverflow_paint(s, canvas);
            for (int a = 0; a < n; ++a) got += cf_fx_has(a) == 1 && was[a] != 1;
            assert(f ? got == FX_SLACK_MS : cf_fx_has(miss) == 2 && got == 1);
        }
        decode_ms = 0;
    }
    half_ms = 0;
    SHUT();
    /* On a thread of its own, a card that takes 200 ms a copy, then 20: no frame waits for one, and
       the covers come in, the window on the screen first. */
    ld_threads = 1, card(200000), main_thread = pthread_self(), sys_n = 0;
    OPEN();
    INTO_LETTERS();
    double worst = 0;
    int shown = 0;
    c = MIDDLE();
    for (int f = 0; f < 150; ++f) {
        if (f == 30) card(20000);
        double t = wall_ms();
        coverflow_paint(s, canvas);
        t = wall_ms() - t, worst = t > worst ? t : worst;
        usleep(17000);
        shown = 0;
        for (int d = -7; d <= 7; ++d) shown += cf_fx_has(((c + d) % n + n) % n) == 1;
        if (shown == 15) break;
    }
    assert(shown == 15 && worst < 100 && ld_live == 1);
    /* The loader lowered itself first, from its own thread: nice 19 (setpriority, o32 4097, its
       tid from gettid, 4222) and the idle I/O class (ioprio_set, 4314). */
    assert(sys_n == 3 && !sys_main && sys_log[0][0] == 4222 && SYS_IS(1, 4097, 0, 4242, 19) &&
           SYS_IS(2, 4314, 1, 4242, 3 << 13));
    /* Landed, letters' extra slots go, the covers kept in the slots left; the loader, asked for
       nothing more, stays parked with its stage till the page closes, and the next spin by letters
       has it again, the same thread. */
    assert(cf_fx_letters() == 7);
    wheel_held = 0;
    for (int i = 0; i < 400 && th[T_ON]; ++i) th_pass(0, 17);
    for (int i = 0; i < 200 && ((cf_fx_letters() & 1) || cf_ld(-1)); ++i) coverflow_paint(s, canvas), usleep(1000);
    assert(cf_fx_letters() == 6 && ld_live == 1 && !cf_ld(-1)); /* every request dropped, none read */
    for (int d = -7; d <= 7; ++d) assert(cf_fx_has(((*index + d) % n + n) % n));
    started = ld_started;
    INTO_LETTERS();
    for (int f = 0; f < 120 && cf_fx_letters() != 7; ++f) coverflow_paint(s, canvas), usleep(17000);
    assert(cf_fx_letters() == 7 && ld_live == 1 && ld_started == started);
    /* An abrupt landing (Centre, a touch) mid-letters, no fold: the covers loaded round the album
       landed on are kept, wherever in the pool they were, so the shelf at rest decodes none. */
    for (int f = 0; f < 150; ++f) {
        coverflow_paint(s, canvas), usleep(17000);
        int land = (*index + (int)floorf(th[T_POS] + .5f)) % n, had = 0;
        for (int d = -3; d <= 3; ++d) had += cf_fx_has(((land + d) % n + n) % n) == 1;
        if (had == 7) break;
    }
    static int had[256];
    for (int a = 0; a < n; ++a) had[a] = cf_fx_has(a);
    coverflow_wheel_land(), cf_thumb(th);
    assert(!th[T_ON] && cf_fx_letters() == 6);
    for (int d = -7; d <= 7; ++d) {
        int a = ((*index + d) % n + n) % n;
        assert(cf_fx_has(a) == had[a]);
        assert(d < -3 || d > 3 || had[a] == 1);
    }
    INTO_LETTERS();
    for (int f = 0; f < 120 && cf_fx_letters() != 7; ++f) coverflow_paint(s, canvas), usleep(17000);
    assert(cf_fx_letters() == 7 && ld_live == 1 && ld_started == started);
    /* Closing stops it after the read in hand (a slow one here), joined: nothing runs while the
       page is closed, and the next open starts it again only going by letters. */
    #define READING(a) do { \
        for (int i_ = 0; i_ < 2000 && cf_ld(a) != 2; ++i_) usleep(1000); \
        assert(cf_ld(a) == 2); } while (0)
    card(200000);
    usleep(30000); /* the read in hand at 20 ms done: the thread's next are slow */
    light = 1, coverflow_paint(s, canvas), light = 0;
    int reading = 0;
    for (int i = 0; i < 2000 && !reading; ++i, usleep(1000))
        for (int a = 0; a < n; ++a) reading |= cf_ld(a) == 2;
    assert(reading);
    double t = wall_ms();
    SHUT();
    t = wall_ms() - t;
    assert(ld_live == 0 && t >= 100);
    card(20000);
    started = ld_started;
    OPEN();
    for (int i = 0; i < 20; ++i) coverflow_paint(s, canvas);
    assert(ld_live == 0 && ld_started == started);
    /* The idle I/O class refused this time: best effort's lowest level (2 << 13 | 7) instead. */
    idle_fails = 1, sys_n = 0;
    INTO_LETTERS();
    card(100000);
    light = 1, coverflow_paint(s, canvas), light = 0;
    c = GOAL();
    READING(c);
    assert(sys_n == 4 && SYS_IS(2, 4314, 1, 4242, 3 << 13) && SYS_IS(3, 4314, 1, 4242, 2 << 13 | 7));
    /* A window over the covers, that read still in hand: the landing frees the letters' slots, with
       no Cover Flow frame after it, and does not wait for the read: the loader parks after it. */
    top_window = 0;
    t = wall_ms();
    th_pass(0, 17);
    t = wall_ms() - t;
    assert(!th[T_ON] && cf_fx_letters() == 6 && ld_live == 1 && t < 50);
    top_window = page;
    SHUT();
    assert(ld_live == 0);
    ld_threads = 0, card(0), idle_fails = 0;
    /* A spin by letters on a card of 3 ms a read, the frames 33 ms apart (two refreshes, as on the
       device), a spread 1 ms: two letters a frame apart, then the thumb still. While a cover of
       the window drawn or of the window of the letter gone to lacks art, it is asked for, and the
       covers drawn all have art within FILL_FRAMES frames of the last step. */
    for (int i = 0; i < n; ++i) {
        cache_file(album_key(records[i]), "", path);
        FILE *fp = fopen(path, "w");
        fputs("jpg", fp);
        fclose(fp);
    }
    OPEN();
    for (int i = 0; i < 20 * n && halves(0) < n; ++i) th_pass(0, 17);
    assert(halves(0) == n);
    INTO_LETTERS();
    ld_hold = 1, light = 1, coverflow_paint(s, canvas), light = 0; /* the pool emptied */
    assert(cf_ld(-1) == LD_SLOTS); /* all it holds in hand */
    ld_hold = 0;
    th_arrive();
    for (int i = 0; i < 100; ++i) coverflow_paint(s, canvas);
    int last = -1, grey = 0;
    ld_hold = 1, half_ms = 1;
    for (int f = 0; f < 60; ++f) {
        for (int r = 0; r < 11; ++r) cf_ld_step(); /* the loader's 33 ms */
        if (f < 4 && !(f & 1))
            th_step(1), th_pass(0, 16);
        else
            th_pass(0, 33);
        coverflow_paint(s, canvas);
        int miss = 0;
        c = MIDDLE();
        for (int d = -7; d <= 7; ++d) {
            int a = ((c + d) % n + n) % n, b = ((GOAL() + d) % n + n) % n;
            miss += !cf_fx_has(a);
            assert((cf_fx_has(a) || cf_ld(a) > 0) && (cf_fx_has(b) || cf_ld(b) > 0));
        }
        if (miss) last = f, grey += miss;
        if (f > 2 && !miss && th[T_POS] == th[T_GOAL]) break;
    }
    last = last > 2 ? last - 2 : 0; /* the frames after the last step (frame 2) */
    fprintf(stderr, "loader: covers drawn all had art %d frames after the last letter step, %d covers grey in all\n",
            last, grey);
    assert(last <= FILL_FRAMES);
    ld_hold = 0, half_ms = 0;
    SHUT();
    /* No memory for fx.more's slots: going by letters loads the window on the screen alone, in
       the pool's own slots, and every frame draws, mid-slide between letters too, where the screen
       shows covers outside the window of the letter gone to; landed, the pool is as it was. */
    more_fail = 1;
    OPEN();
    INTO_LETTERS();
    more_asks = 0;
    for (int f = 0; f < 40; ++f) {
        int d0 = draws;
        if (f < 20 && !(f & 3)) th_step(1);
        th_pass(0, 17);
        light = 1, coverflow_paint(s, canvas), light = 0;
        assert(draws == d0 + 1 && !(cf_fx_letters() & 9)); /* no fx.more, no slot past the pool */
    }
    assert(more_asks <= 1); /* refused once, not asked again every frame */
    c = MIDDLE();
    for (int d = -7; d <= 7; ++d) assert(cf_fx_has(((c + d) % n + n) % n));
    wheel_held = 0;
    for (int i = 0; i < 400 && th[T_ON]; ++i) th_pass(0, 17);
    for (int i = 0; i < 200 && (cf_fx_letters() & 1); ++i) coverflow_paint(s, canvas), usleep(1000);
    assert(!(cf_fx_letters() & 9));
    more_fail = 0;
    SHUT();
    #undef READING
    #undef OPEN
    #undef SHUT
    #undef INTO_LETTERS
    #undef MIDDLE
    #undef GOAL
    #undef NEAR
    shim_themes[1].cf = fixture;
    classic = 0, square_art = 0;
}

/* fx.more's slots are not asked for with MemAvailable under their 4900 KB and MP4_SPARE_KB's
   16384 more, as the calloc, under overcommit, never fails; refused, fx.no_more lasts till letters
   end: they don't ask again (loader_test), the next letters ask once again, and with the memory
   back they get them. */
static void no_more_reset(void) {
    static const cf_style_t shipped = CF_SHIPPED;
    const cf_style_t fixture = shim_themes[1].cf;
    int n = albums, canvas[16] = { 0 }, *index;
    widget *s;
    shim_themes[1].cf = shipped;
    classic = 1, frame_fail = 0, square_art = 1, pressed = 0, light = 0, decode_ms = 0, half_ms = 0;
    open_page(), s = slide(), index = (int *)(s->raw + SLIDE_INDEX), top_window = page;
    static const unsigned avail[3] = { 4900 + 16384 - 1, 1000, 4900 + 16384 };
    for (int k = 0; k < 3; ++k) {
        mem_kb = avail[k], mem_asks = more_asks = 0;
        for (int a = 0; a < n; ++a) *index = strcmp(names[a], "Q04") ? *index : a;
        th_pass(0, 17), th_down();
        for (int t = 0; !(th[T_JUMP] && th[T_ENTER] <= 0) && t < 2000; t += 17) th_spin(150, 17);
        for (int f = 0; f < 4; ++f) th_pass(0, 17), coverflow_paint(s, canvas);
        assert(mem_asks == 1 && more_asks == (k == 2) && (cf_fx_letters() & 1) == (k == 2));
        wheel_held = 0;
        for (int i = 0; i < 400 && th[T_ON]; ++i) th_pass(0, 17);
        coverflow_paint(s, canvas); /* the frame landed: letters over, fx.no_more with them */
        assert(!(cf_fx_letters() & 9));
    }
    mem_kb = ~0u;
    close_page(), top_window = 0, th_pass(0, 17);
    shim_themes[1].cf = fixture;
    classic = 0, square_art = 0;
}

/* ---- Playlist art (playlist_art.c): the playlist table's rows, as its own SQLite query reads them. ---- */
#include "playlist_art.h"
unsigned tags_key(const char *artist, const char *album);
static struct { int cd, id; const char *url, *album, *artist, *aa; } pl_rows[160];
static char pl_urls[160][600];
static int pl_n, pl_opens, pl_busy, pl_scans, pl_ui_opens, pl_awake;
int reset_poweroptions_timer(int standby, int power_off, int screen) {
    assert(standby && power_off && !screen); /* the screen still times out */
    return ++pl_awake;
}
static pthread_t pl_ui; /* the UI thread, which never opens the database for playlist art */
static int ui_fopens; /* fopens on the UI thread (each file_size is one) */
FILE *__real_fopen(const char *, const char *);
FILE *__wrap_fopen(const char *path, const char *mode) {
    ui_fopens += pthread_equal(pthread_self(), pl_ui);
    return __real_fopen(path, mode);
}
int sqlite3_open(const char *path, void **db) {
    assert(!strcmp(path, "/mnt/data/database.db"));
    *db = (void *)1, ++pl_opens, pl_ui_opens += pthread_equal(pthread_self(), pl_ui);
    return 0;
}
int sqlite3_close(void *db) { return 0; }
/* The messy library, test/fw/messy_playlists.py's shape made here in a loop: cd 1 20,000 untagged
   singles, each in its own folder; cd 2 5,000 songs of 500 albums, 4 KB album tags and 800-byte
   fileurls with non-UTF-8 bytes; cd 3 300 songs, 280 gone from the card, 20 in folders with a
   cover.jpg; cd 0 (Q2 Pod's first playlist) and cd 4 to 100 300 songs each; 50 junk rows (cd_num NULL, -1, 999; fileurl NULL or empty).
   Their ids shuffled across the playlists: id 101 + k is row messy_at[k]. */
enum { MESSY_ROWS = 20000 + 5000 + 300 + 98 * 300 + 50, MESSY_LIVE = 20 };
static int messy, messy_at[MESSY_ROWS], messy_ui_execs, messy_reads; /* reads: a playlist's own */
static int messy_cd(int r, int *null) { /* row r's cd_num; *null: none */
    static const int junk[5] = { 0, -1, 999, 5, 5 };
    *null = r >= MESSY_ROWS - 50 && (r - (MESSY_ROWS - 50)) % 5 == 0;
    return r < 20000 ? 1 : r < 25000 ? 2 : r < 25300 ? 3 : r < MESSY_ROWS - 50 ? (4 + (r - 25300) / 300) % 101 : junk[(r - (MESSY_ROWS - 50)) % 5];
}
/* row r's fileurl, album, songer, albumsonger (in static buffers) */
static void messy_row(int r, char **v) {
    static char url[900], album[4200], tag[4097];
    if (!tag[0]) memset(tag, 'T', 4096);
    v[1] = url, v[2] = album, v[3] = "Artist", v[4] = "";
    if (r < 20000) {
        snprintf(url, sizeof url, PEQ_ROOT "/mnt/mmc/S/%d/t.flac", r);
        v[2] = v[3] = v[4] = 0;
    } else if (r < 25000) {
        int n = snprintf(url, sizeof url, PEQ_ROOT "/mnt/mmc/B/%d/", (r - 20000) % 500);
        while (n < 780) url[n++] = '\xff', url[n++] = '\xfe';
        snprintf(url + n, sizeof url - n, "%d.flac", r);
        snprintf(album, sizeof album, "%s%d", tag, (r - 20000) % 500);
    } else if (r < 25300) {
        snprintf(url, sizeof url, PEQ_ROOT "/mnt/mmc/%s/%d/t.flac", r - 25000 < MESSY_LIVE ? "L" : "Gone", r - 25000);
        snprintf(album, sizeof album, "Album %d", r - 25000);
    } else if (r < MESSY_ROWS - 50) {
        int cd = (4 + (r - 25300) / 300) % 101;
        snprintf(url, sizeof url, PEQ_ROOT "/mnt/mmc/P/%d/%d.flac", cd, (r - 25300) % 300);
        snprintf(album, sizeof album, "Album %d.%d", cd, (r - 25300) % 7);
    } else {
        int j = (r - (MESSY_ROWS - 50)) % 5;
        snprintf(url, sizeof url, j == 4 ? "" : PEQ_ROOT "/mnt/mmc/j.flac");
        v[1] = j == 3 ? 0 : url, v[2] = v[3] = v[4] = 0;
    }
}
static void messy_on(void) {
    unsigned seed = 2026;
    for (int k = 0; k < MESSY_ROWS; ++k) messy_at[k] = k;
    for (int k = MESSY_ROWS; k > 1; --k) {
        seed = seed * 1664525u + 1013904223u;
        int j = (int)((seed >> 8) % (unsigned)k), t = messy_at[k - 1];
        messy_at[k - 1] = messy_at[j], messy_at[j] = t;
    }
    messy = 1;
}
/* the messy table's first 1024 rows past id lo through row, every playlist's: (id, cd_num, fileurl)
   for the refresh, else (id, fileurl, album, songer, albumsonger, cd_num); the rows given */
static int messy_exec(int songs, unsigned lo, int (*row)(void *, int, char **, char **), void *ctx, int *rows) {
    char cds[16], id[16], *v[6];
    if (pthread_equal(pthread_self(), pl_ui)) ++messy_ui_execs;
    messy_reads += songs && lo == 100;
    for (int k = (int)lo - 100, null; k < MESSY_ROWS && *rows < 1024; ++k) {
        int c = messy_cd(messy_at[k], &null);
        snprintf(cds, sizeof cds, "%d", c), snprintf(id, sizeof id, "%d", 101 + k);
        messy_row(messy_at[k], v);
        v[0] = id, v[5] = null ? 0 : cds, ++*rows;
        char *three[3] = { id, v[5], v[1] };
        if (songs ? row(ctx, 6, v, 0) : row(ctx, 3, three, 0)) return 4; /* SQLITE_ABORT */
    }
    return 0;
}
/* The most rows a statement gave (the reads go in pages of rows, a statement each). */
static int pl_page_most;
static int pl_exec(void *db, const char *sql, int (*row)(void *, int, char **, char **), void *ctx, int *rows) {
    unsigned lo;
    char want[200], id[16];
    if (sscanf(sql, "select id,cd_num,fileurl from playlist where id>%u", &lo) == 1) {
        snprintf(want, sizeof want, "select id,cd_num,fileurl from playlist where id>%u order by id limit 1024", lo);
        assert(!strcmp(sql, want) && lo >= 100);
        if (pl_busy) return 5; /* SQLITE_BUSY: stock writes the database */
        pl_scans += lo == 100;
        if (messy) return messy_exec(0, lo, row, ctx, rows);
        for (int last = (int)lo, next; *rows < 1024; last = pl_rows[next].id) { /* every playlist's songs, in id order */
            next = -1;
            for (int i = 0; i < pl_n; ++i)
                if (pl_rows[i].id > last && (next < 0 || pl_rows[i].id < pl_rows[next].id)) next = i;
            if (next < 0) return 0;
            char cds[16];
            snprintf(cds, sizeof cds, "%d", pl_rows[next].cd), snprintf(id, sizeof id, "%d", pl_rows[next].id);
            char *v[3] = { id, cds, (char *)pl_rows[next].url };
            ++*rows;
            if (row(ctx, 3, v, 0)) return 4;
        }
        return 0;
    }
    /* a playlist's own read: every playlist's rows, a page at a time, song_row keeping its own */
    assert(sscanf(sql, "select id,fileurl,album,songer,albumsonger,cd_num from playlist where id>%u", &lo) == 1);
    snprintf(want, sizeof want, "select id,fileurl,album,songer,albumsonger,cd_num from playlist where id>%u order by id limit 1024", lo);
    assert(!strcmp(sql, want) && lo >= 100);
    if (messy) return messy_exec(1, lo, row, ctx, rows);
    for (int i = 0; i < pl_n && *rows < 1024; ++i) { /* kept in id order */
        if (pl_rows[i].id <= (int)lo) continue;
        char cds[16];
        snprintf(id, sizeof id, "%d", pl_rows[i].id), snprintf(cds, sizeof cds, "%d", pl_rows[i].cd);
        char *v[6] = { id, (char *)pl_rows[i].url, (char *)pl_rows[i].album, (char *)pl_rows[i].artist, (char *)pl_rows[i].aa, cds };
        ++*rows;
        if (row(ctx, 6, v, 0)) return 4; /* SQLITE_ABORT */
    }
    return 0;
}
int sqlite3_exec(void *db, const char *sql, int (*row)(void *, int, char **, char **), void *ctx, char **err) {
    int rows = 0, r;
    if (!strcmp(sql, "PRAGMA cache_size=64")) return 0;
    r = pl_exec(db, sql, row, ctx, &rows);
    pl_page_most = rows > pl_page_most ? rows : pl_page_most;
    return r;
}
int shim_xstat(int ver, const char *path, void *out) {
    struct stat s;
    unsigned *o = out;
    if (stat(path, &s)) return -1;
    memset(o, 0, 36 * 4);
    o[5] = s.st_mode, o[12] = (unsigned)s.st_size, o[16] = (unsigned)s.st_mtime;
    return 0;
}
/* A song of album in playlist cd, its file in music/<album>/. */
static void pl_song(int cd, int id, const char *album) {
    char dir[500];
    snprintf(dir, sizeof dir, PEQ_ROOT "/music/%s", album);
    mkdir(dir, 0755);
    snprintf(pl_urls[pl_n], 600, "%s/%d.flac", dir, id);
    fclose(fopen(pl_urls[pl_n], "w"));
    pl_rows[pl_n].cd = cd, pl_rows[pl_n].id = id, pl_rows[pl_n].url = pl_urls[pl_n];
    pl_rows[pl_n].album = album, pl_rows[pl_n].artist = "Artist", pl_rows[pl_n++].aa = "";
}
/* The art file a playlist's songs name now: FNV-1a over each song's fileurl, in id order. */
static char *pl_file(char *path, const char *kind, int cd, const char *suffix) {
    unsigned h = FNV_SEED;
    for (int i = 0; i < pl_n; ++i)
        if (pl_rows[i].cd == cd) h = fnv(h, (const unsigned char *)pl_rows[i].url);
    snprintf(path, 600, PEQ_ROOT "/mnt/mmc/.coverflow/%s_%d_%08x%s", kind, cd, h, suffix);
    return path;
}
static int pl_ready[16], pl_readies;
static void on_ready(int cd) { pl_ready[pl_readies++ % 16] = cd; }
static int pl_heard[100];
static void heard(int cd) { ++pl_heard[cd]; }
/* The last 2x2's ffmpeg: its four inputs and its filter graph. */
static char ff_in[4][600], ff_graph[600];
static int tile_args(void) {
    char line[600], prev[600] = "";
    int n = 0;
    FILE *f = fopen(ART_FFMPEG ".args", "r");
    while (f && fgets(line, sizeof line, f)) {
        line[strcspn(line, "\n")] = 0;
        if (!strcmp(prev, "-i") && n < 4) snprintf(ff_in[n++], 600, "%s", line);
        if (!strcmp(prev, "-filter_complex")) snprintf(ff_graph, sizeof ff_graph, "%s", line);
        snprintf(prev, sizeof prev, "%s", line);
    }
    if (f) fclose(f);
    unlink(ART_FFMPEG ".args");
    return n;
}
static int content(const char *path, const char *want) {
    char got[600] = "";
    FILE *f = fopen(path, "r");
    size_t n = f ? fread(got, 1, sizeof got - 1, f) : 0;
    if (f) fclose(f);
    got[n] = 0;
    return f && !strcmp(got, want);
}
static void wait_blocked(void) { while (!blocked) usleep(1000); }
/* A playlist's own picture at path, w x h by its JPEG frame header (SOF0), which the ffmpeg stub
   decodes when it holds a "picture" line. */
static void pl_picture(const char *path, int w, int h, int decodes) {
    const unsigned char b[24] = { 0xff, 0xd8, 0xff, 0xc0, 0, 17, 8, h >> 8, h & 255, w >> 8, w & 255, 3 };
    FILE *f = fopen(path, "wb");
    fwrite(b, 1, sizeof b, f), fputs(decodes ? "\npicture\n" : "\n", f), fclose(f);
}
/* playlists.c's refresh, as a Playlists rebuild asks for it, and the worker's read landed (its
   playlists not told: the tests count the builds' readies). */
static void pl_refresh(void) {
    void (*ready)(int) = playlist_art_ready;
    playlist_art_ready = 0;
    playlist_art_refresh();
    run();
    playlist_art_ready = ready;
}
static void playlist_art_files(void) {
    /* each cover filled to 80x80 and cut square: a non-square one is not squashed */
#define FILL(i, o) "[" #i "]scale=80:80:force_original_aspect_ratio=increase,crop=80:80[" #o "];"
    static const char graph[] = FILL(0, a) FILL(1, b) FILL(2, c) FILL(3, d)
        "[a][b]hstack[t];[c][d]hstack[u];[t][u]vstack";
#undef FILL
    char url[700], path[600], old[600], cached[3][600];
    const char *three[] = { "PL A", "PL B", "PL C" };
    playlist_art_ready = on_ready;
    int fails = embedded_fails; /* the albums' tests after these build from cover.jpg */
    embedded_fails = 0, block_at = -1, free_blocks = 1 << 20, calls = 0;
    mkdir(PEQ_ROOT "/mnt/mmc/.coverflow", 0755);
    mkdir(PEQ_ROOT "/mnt/mmc/_explaylist_data", 0755);

    /* Three albums whose Cover Flow art is cached: nothing built but the 2x2, a b / c a of those
       files by one ffmpeg (hstack and vstack: 4.2 has no xstack), named for the songs. */
    pl_song(7, 101, "PL A"), pl_song(7, 102, "PL B"), pl_song(7, 103, "PL B"), pl_song(7, 104, "PL C");
    for (int i = 0; i < 3; ++i) {
        snprintf(cached[i], 600, PEQ_ROOT "/mnt/mmc/.coverflow/%08x.jpg", tags_key("Artist", three[i]));
        FILE *f = fopen(cached[i], "w");
        fputs(three[i], f), fclose(f);
    }
    /* The UI thread never reads the table: the refresh asks the worker. Before its first read
       lands an ask answers none and queues nothing; a read the database refuses (busy) publishes
       nothing and is not tried again per ask; the next rebuild's read fills the table, and the
       playlists it brings are told, so their rows ask again. */
    pl_ui = pthread_self();
    pl_busy = 1;
    playlist_art_refresh();
    assert(timer_fn && !pl_ui_opens);
    run();
    pl_busy = 0;
    int opens = pl_opens;
    for (int i = 0; i < 20; ++i) assert(!playlist_art(7, "Seven", i & 1, url, sizeof url) && !timer_fn);
    assert(pl_opens == opens && !pl_ui_opens && !playlist_songs(7) && !pl_readies);
    playlist_art_refresh();
    run();
    assert(pl_opens == opens + 1 && !pl_ui_opens && playlist_songs(7) == 4 && pl_readies == 1 && pl_ready[0] == 7);
    pl_readies = 0;
    assert(!playlist_art(7, "Seven", 0, url, sizeof url) && timer_fn);
    run();
    assert(pl_readies == 1 && pl_ready[0] == 7);
    assert(tile_args() == 4 && !strcmp(ff_graph, graph) && !strcmp(ff_in[3], ff_in[0]));
    for (int i = 0; i < 3; ++i) {
        int hit = 0;
        for (int k = 0; k < 3; ++k) hit += !strcmp(ff_in[i], cached[k]);
        assert(hit == 1 && strcmp(ff_in[i], ff_in[(i + 1) % 3]));
    }
    assert(content(pl_file(path, "pl", 7, ".jpg"), "tile") && !tmp_files());
    assert(calls == 1 && strstr(made[0], ".tmp.jpg")); /* only the small one, from the 2x2 */
    assert(playlist_art(7, "Seven", 0, url, sizeof url) && !strncmp(url, "file://", 7) && !strcmp(url + 7, path));
    assert(playlist_art(7, "Seven", 1, url, sizeof url) && !strcmp(url + 7, pl_file(path, "pl", 7, "_s.jpg")) && !access(path, 0));
    assert(!timer_fn && pl_readies == 1); /* ready: nothing queued */
    /* Asks read the hashes refresh took: no SQLite on the UI thread, the same answers. */
    opens = pl_opens;
    for (int i = 0; i < 20; ++i)
        assert(playlist_art(7, "Seven", i & 1, url, sizeof url) && !strcmp(url + 7, pl_file(path, "pl", 7, i & 1 ? "_s.jpg" : ".jpg")));
    assert(pl_opens == opens && !timer_fn);
    /* A refresh the database refuses (busy) keeps the hashes it had. */
    pl_busy = 1, pl_refresh(), pl_busy = 0;
    assert(playlist_art(7, "Seven", 0, url, sizeof url) && !strcmp(url + 7, pl_file(path, "pl", 7, ".jpg")));

    /* Its own picture, _explaylist_data/<name>.jpg, wins over the 2x2. */
    FILE *f;
    pl_picture(PEQ_ROOT "/mnt/mmc/_explaylist_data/Seven.jpg", 640, 640, 1);
    assert(!playlist_art(7, "Seven", 0, url, sizeof url));
    run();
    assert(pl_readies == 2 && pl_ready[1] == 7);
    assert(playlist_art(7, "Seven", 0, url, sizeof url) && strstr(url, "/.coverflow/plc_7_") && content(url + 7, "Seven.jpg"));
    assert(playlist_art(7, "Seven", 1, url, sizeof url) && strstr(url, "_s.jpg") && !access(url + 7, 0));
    /* A 63-byte name with characters FAT refuses: its picture under the escaped name. */
    char name[64];
    snprintf(name, sizeof name, "AC/DC: Live?%051d", 0);
    assert(strlen(name) == 63);
    snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/_explaylist_data/AC_DC_ Live_%051d.png", 0);
    pl_picture(path, 640, 640, 1);
    assert(!playlist_art(7, name, 0, url, sizeof url));
    run();
    assert(playlist_art(7, name, 0, url, sizeof url) && content(url + 7, strrchr(path, '/') + 1));

    /* A picture whose size its header doesn't tell: marked, and the 2x2 instead, from then on. */
    pl_song(8, 105, "PL A"), pl_song(8, 106, "PL C");
    pl_refresh();
    fputs("junk", f = fopen(PEQ_ROOT "/mnt/mmc/_explaylist_data/Bad one.jpg", "w")), fclose(f);
    assert(!playlist_art(8, "Bad one", 0, url, sizeof url));
    run();
    assert(tile_args() == 4 && !strcmp(ff_in[0], ff_in[3]) && !strcmp(ff_in[1], ff_in[2]) && strcmp(ff_in[0], ff_in[1]));
    assert(playlist_art(8, "Bad one", 0, url, sizeof url) && !strcmp(url + 7, pl_file(path, "pl", 8, ".jpg")));
    assert(!timer_fn && !tmp_files());
    /* A read that lands tells only the playlists whose songs it changed: 8's, not 7's. */
    int told = pl_readies;
    pl_song(8, 150, "PL B");
    playlist_art_refresh();
    run();
    assert(pl_readies == told + 1 && pl_ready[told % 16] == 8 && playlist_songs(8) == 3 && playlist_songs(7) == 4);
    assert(!playlist_art(8, "Bad one", 0, url, sizeof url));
    run();
    assert(playlist_art(8, "Bad one", 0, url, sizeof url) && !strcmp(url + 7, pl_file(path, "pl", 8, ".jpg")));

    /* Songs changed mid-build: the finished art keeps the old songs' name, which is never shown for
       the new ones; the next ask builds theirs. The covers are built here (none cached). */
    pl_song(9, 107, "PL D"), pl_song(9, 108, "PL E");
    pl_refresh();
    pl_file(old, "pl", 9, ".jpg");
    calls = 0, block_at = 0;
    assert(!playlist_art(9, "Nine", 0, url, sizeof url));
    wait_blocked();
    pl_song(9, 109, "PL F");
    playlist_art_refresh(); /* the list rebuilt meanwhile: the old songs' build never names its art */
    blocked = 0, block_at = -1;
    run();
    assert(content(old, "tile") && access(pl_file(path, "pl", 9, ".jpg"), 0) && !tmp_files());
    assert(tile_args() == 4 && strstr(ff_in[0], ".tmp.jpg") && !strcmp(ff_in[0], ff_in[3]) && !strcmp(ff_in[1], ff_in[2]));
    assert(!playlist_art(9, "Nine", 0, url, sizeof url));
    run();
    assert(playlist_art(9, "Nine", 0, url, sizeof url) && !strcmp(url + 7, path) && content(path, "tile"));

    /* A finished build hands the UI the songs it read, their hash and count, with no read of the
       table on the UI thread: songs added after the last refresh show their art and count. */
    pl_song(11, 115, "PL A"), pl_song(11, 116, "PL C");
    pl_refresh();
    pl_song(11, 117, "PL B");
    int scans = pl_scans;
    assert(!playlist_art(11, "Eleven", 0, url, sizeof url));
    run();
    assert(pl_scans == scans && playlist_songs(11) == 3);
    assert(playlist_art(11, "Eleven", 0, url, sizeof url) && !strcmp(url + 7, pl_file(path, "pl", 11, ".jpg")));

    /* The card full, or gone mid-build: nothing named, the worker ends, the next ask builds. */
    pl_song(10, 110, "PL G"), pl_song(10, 111, "PL H");
    pl_refresh();
    int readies = pl_readies;
    free_blocks = 0;
    assert(!playlist_art(10, "Ten", 0, url, sizeof url));
    run();
    free_blocks = 1 << 20;
    assert(access(pl_file(path, "pl", 10, ".jpg"), 0) && pl_readies == readies && !tmp_files());
    calls = 0, block_at = 0;
    assert(!playlist_art(10, "Ten", 0, url, sizeof url));
    wait_blocked();
    assert(!rename(PEQ_ROOT "/mnt/mmc", PEQ_ROOT "/mnt/gone"));
    blocked = 0, block_at = -1;
    run();
    assert(calls == 1 && pl_readies == readies); /* stopped at the card: no second album, no small one */
    assert(!rename(PEQ_ROOT "/mnt/gone", PEQ_ROOT "/mnt/mmc"));
    /* the card pulled in album_art leaves its note, which would strike that album, as Cover Flow's */
    assert(!unlink(PEQ_ROOT "/mnt/mmc/.coverflow/pl-building"));
    assert(access(path, 0) && !tmp_files());
    assert(!playlist_art(10, "Ten", 0, url, sizeof url));
    run();
    assert(playlist_art(10, "Ten", 0, url, sizeof url) && content(path, "tile"));
    /* A refresh after a song change: the new songs' hash, its art still to build. */
    pl_song(10, 112, "PL I");
    assert(playlist_art(10, "Ten", 0, url, sizeof url) && !strcmp(url + 7, path)); /* until a refresh */
    pl_refresh();
    assert(!playlist_art(10, "Ten", 0, url, sizeof url));
    run();
    assert(playlist_art(10, "Ten", 0, url, sizeof url) && !strcmp(url + 7, pl_file(path, "pl", 10, ".jpg")));
    /* Stopped (the page closed) mid-build: back after the one cover being built, nothing named,
       no marker. While it builds, each tick holds off Standby and auto power-off, so the card is
       not unmounted under it. */
    pl_song(12, 113, "PL J"), pl_song(12, 114, "PL K");
    pl_refresh();
    calls = 0, block_at = 0;
    assert(!playlist_art(12, "Twelve", 0, url, sizeof url));
    wait_blocked();
    int awake = pl_awake, (*tick)(const void *) = timer_fn;
    timer_fn = 0, tick(0);
    assert(pl_awake == awake + 1 && timer_fn);
    pthread_t releaser;
    pthread_create(&releaser, 0, release, 0);
    readies = pl_readies;
    playlist_art_stop();
    pthread_join(releaser, 0);
    block_at = -1;
    assert(calls == 1 && pl_readies == readies && !timer_fn);
    assert(access(pl_file(path, "pl", 12, ".jpg"), 0) && !tmp_files());
    playlist_art_stop();

    /* Albums Cover Flow knows have no art (its empty marker) are not tried again, though their
       songs would give some now: twenty of them are no asks, and the playlist is marked. */
    static char albums[20][16];
    for (int i = 0; i < 20; ++i) {
        snprintf(albums[i], 16, "PL R%02d", i);
        pl_song(13, 120 + i, albums[i]);
        snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/.coverflow/%08x.jpg", tags_key("Artist", albums[i]));
        fclose(fopen(path, "w"));
    }
    pl_refresh();
    int asks = album_asks;
    assert(!playlist_art(13, "Thirteen", 1, url, sizeof url));
    run();
    assert(album_asks == asks && content(pl_file(path, "pl", 13, ".jpg"), "") && !tmp_files());
    assert(!playlist_art(13, "Thirteen", 1, url, sizeof url) && !timer_fn); /* marked: none, not queued */

    /* A 2x2 whose ffmpeg fails, run to its own exit: marked as none, not built again at every ask. */
    pl_song(66, 140, "PL A"), pl_song(66, 141, "PL C");
    pl_refresh();
    assert(!playlist_art(66, "Sixty-six", 1, url, sizeof url));
    run();
    assert(tile_args() == 4 && content(pl_file(path, "pl", 66, ".jpg"), "") && !tmp_files());
    assert(!playlist_art(66, "Sixty-six", 1, url, sizeof url) && !timer_fn);

    /* A hundred playlists listed at once: every ask answers at once, placeholders
       while one worker builds a job at a time (the first holds its first cover meanwhile, and no
       other cover starts); each playlist queued once however often it is asked (twice each here:
       128 jobs would hold only 64 playlists), and each fills in. */
    pl_n = 0;
    playlist_art_ready = heard;
    for (int cd = 0; cd < 100; ++cd) pl_song(cd, 1000 + cd, "PL Many");
    pl_refresh();
    calls = 0, block_at = 0;
    for (int cd = 0; cd < 100; ++cd)
        for (int twice = 0; twice < 2; ++twice) {
            snprintf(name, sizeof name, "Many %d", cd);
            assert(!playlist_art(cd, name, 1, url, sizeof url));
        }
    wait_blocked();
    usleep(20000);
    assert(calls == 1 && timer_fn);
    for (int cd = 0; cd < 100; ++cd) assert(!pl_heard[cd]);
    blocked = 0, block_at = -1;
    run();
    for (int cd = 0; cd < 100; ++cd) {
        snprintf(name, sizeof name, "Many %d", cd);
        assert(pl_heard[cd] == 1 && playlist_art(cd, name, 1, url, sizeof url) &&
               !strcmp(url + 7, pl_file(path, "pl", cd, "_s.jpg")));
    }
    assert(!timer_fn && !tmp_files());
    playlist_art_ready = 0;
    embedded_fails = fails;
}

/* The 2x2's album builds under a synced note of their own (pl-building), so an album whose cover
   kills or hangs demo is struck at the worker's next start, not tried again at every open. */
#define PL_NOTE PEQ_ROOT "/mnt/mmc/.coverflow/pl-building"
char *art_path(char *out, unsigned key, const char *suffix);
void coverflow_clean_end(void);
static void pl_write_note(unsigned key, const char *track) {
    FILE *f = fopen(PL_NOTE, "wb");
    fwrite(&key, 4, 1, f), fputs(track, f), fclose(f);
}
static void playlist_art_crash(void) {
    char url[700], path[600], note[700], log[4096];
    int fails = embedded_fails, n;
    embedded_fails = 0;
    pl_n = 0;
    pl_song(20, 2001, "PL Crash");
    unsigned key = tags_key("Artist", "PL Crash");
    pl_refresh();
    calls = 0, block_at = 0;
    assert(!playlist_art(20, "Twenty", 0, url, sizeof url));
    wait_blocked(); /* in album_art: the note holds the album's key and track */
    FILE *f = fopen(PL_NOTE, "rb");
    n = f ? (int)fread(note, 1, sizeof note, f) : 0;
    if (f) fclose(f);
    assert(n == 4 + (int)strlen(pl_urls[0]) && !memcmp(note, &key, 4) && !memcmp(note + 4, pl_urls[0], n - 4));
    blocked = 0, block_at = -1;
    run();
    assert(access(PL_NOTE, 0) && !access(pl_file(path, "pl", 20, ".jpg"), 0) && !tmp_files());

    /* A note a build left (demo died in it): the next start strikes its album, Cover Flow's empty
       marker and a crash-log line, and never asks for its art. */
    pl_song(21, 2002, "PL Struck");
    pl_refresh();
    pl_write_note(tags_key("Artist", "PL Struck"), pl_urls[1]);
    unlink(PEQ_ROOT "/mnt/mmc/q2-crash.log");
    int asks = album_asks;
    assert(!playlist_art(21, "Twenty-one", 0, url, sizeof url));
    run();
    snprintf(log, sizeof log, "== cover flow skipped\n%s\n", pl_urls[1]);
    assert(album_asks == asks && access(PL_NOTE, 0) && content(PEQ_ROOT "/mnt/mmc/q2-crash.log", log));
    assert(content(art_path(path, tags_key("Artist", "PL Struck"), ""), ""));
    assert(content(pl_file(path, "pl", 21, ".jpg"), "")); /* its one album known to have none */

    /* A note whose album's art is there leaves the art alone. */
    art_path(path, key, "");
    fputs("art", f = fopen(path, "w")), fclose(f);
    pl_write_note(key, pl_urls[0]);
    pl_song(22, 2003, "PL Crash");
    pl_refresh();
    assert(!playlist_art(22, "Twenty-two", 0, url, sizeof url));
    run();
    assert(content(path, "art") && access(PL_NOTE, 0));

    /* A clean end (power-off) mid-build stops the worker before its next cover: no note of the next
       album's outlives demo for the next start to strike, the one in hand's goes, nothing is named. */
    pl_song(23, 2004, "PL End A"), pl_song(23, 2005, "PL End B");
    pl_refresh();
    calls = 0, block_at = 0, asks = album_asks;
    assert(!playlist_art(23, "Twenty-three", 0, url, sizeof url));
    wait_blocked(); /* in the first album's album_art, under its note */
    coverflow_clean_end();
    assert(access(PL_NOTE, 0));
    blocked = 0, block_at = -1;
    run();
    assert(album_asks == asks + 1 && access(PL_NOTE, 0) && !tmp_files());
    assert(access(pl_file(path, "pl", 23, ".jpg"), 0));

    /* A clean end with no build running is no album's fault either: the note goes. */
    pl_write_note(key, pl_urls[0]);
    coverflow_clean_end();
    assert(access(PL_NOTE, 0) && !pl_ui_opens);
    playlist_art_stop(); /* its cancel dropped, for the tests after */
    embedded_fails = fails;
}

/* Each ffmpeg run so far (the stub counts them). */
static int ff_runs(void) {
    int n = 0;
    FILE *f = fopen(ART_FFMPEG ".runs", "r");
    for (int c; f && (c = fgetc(f)) != EOF;) n += c == '\n';
    if (f) fclose(f);
    return n;
}
/* playlist cd's files of kind, as a glob's count, and whether the last one is empty */
static int pl_files(const char *kind, int cd, int *empty) {
    char want[32], path[600];
    int n = 0;
    snprintf(want, sizeof want, "%s_%d_", kind, cd);
    DIR *d = opendir(PEQ_ROOT "/mnt/mmc/.coverflow");
    for (struct dirent *e; d && (e = readdir(d));) {
        if (strncmp(e->d_name, want, strlen(want)) || strstr(e->d_name, "_s.jpg")) continue;
        snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/.coverflow/%s", e->d_name);
        ++n, *empty = content(path, "");
    }
    if (d) closedir(d);
    return n;
}
/* The user's own picture is decoded up to 12 MP (PL_PICTURE_PX) by its header: 4080 x 3072 (a Pixel's
   12 MP photo) runs ffmpeg, 4081 x 3072 is marked at once with no run and its 2x2 shows. One ffmpeg won't decode is
   marked too; one whose ffmpeg is killed (Big.png: memory short just then) is tried again at the
   next ask, never marked. */
static void playlist_art_kills(void) {
    char url[700], path[600];
    int empty = 0, runs, fails = embedded_fails;
    embedded_fails = 0;
    pl_n = 0;
    pl_song(120, 2101, "PL A"); /* its Cover Flow art cached: a 2x2 costs no run */
    pl_song(121, 2102, "PL A"), pl_song(122, 2103, "PL A"), pl_song(123, 2104, "PL A");
    pl_refresh();
    static const struct { int cd, w, h, decodes, runs, shown; const char *name; } cap[] = {
        { 121, 4080, 3072, 1, 1, 1, "Edge" }, { 122, 4081, 3072, 1, 0, 0, "Over" }, { 123, 3000, 4000, 0, 1, 0, "Junk" } };
    for (int i = 0; i < 3; ++i) {
        snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/_explaylist_data/%s.jpg", cap[i].name);
        pl_picture(path, cap[i].w, cap[i].h, cap[i].decodes);
        runs = ff_runs();
        assert(!playlist_art(cap[i].cd, cap[i].name, 0, url, sizeof url));
        run();
        assert(ff_runs() == runs + cap[i].runs && pl_files("plc", cap[i].cd, &empty) == 1 && empty == !cap[i].shown);
        assert(playlist_art(cap[i].cd, cap[i].name, 0, url, sizeof url) && strstr(url, cap[i].shown ? "/plc_" : "/pl_"));
        unlink(path);
    }
    pl_picture(PEQ_ROOT "/mnt/mmc/_explaylist_data/Big.png", 4000, 3000, 1);
    for (int i = 0; i < 2; ++i) {
        runs = ff_runs();
        assert(!playlist_art(120, "Big", 0, url, sizeof url));
        run();
        assert(ff_runs() == runs + 1 && !pl_files("plc", 120, &empty) && !pl_files("pl", 120, &empty));
    }
    assert(!tmp_files());
    unlink(PEQ_ROOT "/mnt/mmc/_explaylist_data/Big.png");
    embedded_fails = fails;
}

/* The 2x2 is the first four albums with art in song id order, one Cover Flow found none for passed
   over; songs added after those four leave its covers as they were, in a file of the new songs'. */
static void playlist_art_order(void) {
    static const char *const names[] = { "PO A", "PO None", "PO B", "PO C", "PO D", "PO E", "PO F" };
    char url[700], path[600];
    int fails = embedded_fails;
    embedded_fails = 0;
    pl_n = 0;
    for (int i = 0; i < 7; ++i) {
        FILE *f = fopen(art_path(path, tags_key("Artist", names[i]), ""), "w");
        if (i != 1) fputs(names[i], f);
        fclose(f);
    }
    for (int i = 0; i < 6; ++i) pl_song(32, 3200 + i, names[i]);
    pl_refresh();
    for (int pass = 0; pass < 2; ++pass) {
        assert(!playlist_art(32, "Order", 0, url, sizeof url));
        run();
        assert(tile_args() == 4);
        for (int i = 0; i < 4; ++i) assert(!strcmp(ff_in[i], art_path(path, tags_key("Artist", names[i ? i + 1 : 0]), "")));
        assert(playlist_art(32, "Order", 0, url, sizeof url) && !strcmp(url + 7, pl_file(path, "pl", 32, ".jpg")));
        pl_song(32, 3210, names[6]), pl_song(32, 3211, names[3]); /* added after the four */
        pl_refresh();
    }
    embedded_fails = fails;
}

/* The messy library's (messy_on) playlist art: every bound holds, counted, never timed. */
static int runs_of(const char *what) { /* ffmpeg runs whose input names what */
    char line[1000];
    int n = 0;
    FILE *f = fopen(ART_FFMPEG ".runs", "r");
    while (f && fgets(line, sizeof line, f)) n += !!strstr(line, what);
    if (f) fclose(f);
    return n;
}
static const char *messy_name(int cd, const char *l255) { /* the containers' names */
    return cd == 1 ? "Mix" : cd == 2 ? "Road Trip" : cd == 3 ? "\xc3\x9c" "n\xc3\xaf" "c\xc3\xb8" "d\xc3\xa9/"
         : cd == 4 ? l255 : cd == 5 ? "Big" : "Playlist";
}
static void playlist_art_messy(void) {
    static const unsigned char png[24] = { 0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n', 0, 0, 0, 13,
                                           'I', 'H', 'D', 'R', 0, 0, 0x1f, 0x40, 0, 0, 0x1f, 0x40 };
    static const unsigned char sof2[32] = { 0xff, 0xd8, 0xff, 0xc2, 0, 17, 8, 0x0b, 0xb8, 0x0f, 0xa0, 3 };
    char url[700], path[1100], name[300];
    int fails = embedded_fails, empty = 0;
    embedded_fails = 1, free_blocks = 1 << 20; /* no embedded art: a build reaches its folder's cover, then ffmpeg */
    playlist_art_stop(); /* the covers' last worker: the shim's one timer slot lost its poll */
    messy_on();
    /* The card: cd 3's twenty live songs, each beside a cover.jpg; Big.png (8000 x 8000) for cd 5,
       Road Trip.jpg (progressive, 4000 x 3000) for cd 2; ten of cd 2's albums marked by Cover Flow as
       having none, and a hundred with art. No playlist's art yet. */
    for (int i = 0; i < MESSY_LIVE; ++i) {
        snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/L/%d", i), mkdir(PEQ_ROOT "/mnt/mmc/L", 0755), mkdir(path, 0755);
        snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/L/%d/t.flac", i), fclose(fopen(path, "w"));
        snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/L/%d/cover.jpg", i), fclose(fopen(path, "w"));
    }
    FILE *f = fopen(PEQ_ROOT "/mnt/mmc/_explaylist_data/Big.png", "wb");
    fwrite(png, 1, sizeof png, f), fclose(f);
    f = fopen(PEQ_ROOT "/mnt/mmc/_explaylist_data/Road Trip.jpg", "wb");
    fwrite(sof2, 1, sizeof sof2, f), fclose(f);
    static char tag[4200];
    memset(tag, 'T', 4096);
    for (int i = 0; i < 500; i += i == 9 ? 391 : 1) { /* albums 0-9 marked none, 400-499 with art */
        snprintf(tag + 4096, 16, "%d", i);
        art_path(path, tags_key("Artist", tag), "");
        f = fopen(path, "w");
        if (i >= 400) fputs("art", f);
        fclose(f);
    }
    DIR *d = opendir(PEQ_ROOT "/mnt/mmc/.coverflow");
    for (struct dirent *e; d && (e = readdir(d));)
        if (!strncmp(e->d_name, "pl", 2) && strchr(e->d_name, '_'))
            snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/.coverflow/%s", e->d_name), unlink(path);
    if (d) closedir(d);
    /* A Playlists rebuild of 101 rows: the refresh asks the worker, each row asks its art. No SQLite
       on the UI thread, at most two files opened per row (file_size), whatever the table holds. */
    int opens = pl_ui_opens, execs = messy_ui_execs, fopens = ui_fopens, runs = ff_runs();
    int gone0 = runs_of("/Gone/"), big0 = runs_of("Big.png"), road0 = runs_of("Road Trip.jpg");
    struct timespec t0, t1;
    playlist_art_ready = 0;
    playlist_art_refresh();
    clock_gettime(CLOCK_MONOTONIC, &t0);
    memset(name, 'L', 255), name[255] = 0; /* cd 4's: 255 characters */
    for (int cd = 0; cd <= 100; ++cd) playlist_art(cd, messy_name(cd, name), 1, url, sizeof url);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    assert(pl_ui_opens == opens && messy_ui_execs == execs && ui_fopens - fopens <= 2 * 101);
    printf("playlist art, messy library: a 101-row rebuild asks %.3f ms a row on the UI thread (%d files opened)\n",
           ((t1.tv_sec - t0.tv_sec) * 1e3 + (t1.tv_nsec - t0.tv_nsec) / 1e6) / 101, ui_fopens - fopens);
    fflush(stdout);
    run(); /* the worker's read, then the first build: 101 jobs */
    assert(pl_page_most == 1024); /* a page of rows a statement: a writer waits for one, never the table */
    assert(playlist_songs(1) == 20000 && playlist_songs(2) == 5000 && playlist_songs(3) == 300 && playlist_songs(5) == 320);
    assert(playlist_songs(0) == 300); /* Q2 Pod's first playlist is cd 0 */
    assert(!playlist_songs(-1) && !playlist_songs(999)); /* junk: no playlist's */
    int forks = ff_runs() - runs;
    printf("playlist art, messy library: %d rows; first build of 101 playlists forked %d ffmpeg runs, %d on files gone\n",
           MESSY_ROWS, forks, runs_of("/Gone/") - gone0);
    fflush(stdout);
    assert(runs_of("/Gone/") == gone0 && runs_of("/mnt/mmc/S/") + runs_of("/mnt/mmc/P/") + runs_of("/mnt/mmc/B/") == 0);
    /* Road Trip's picture (12 MP: Big.png's 64 MP is never run), cd 2's 2x2 (its albums with art)
       and cd 3's (its live songs' covers) */
    assert(runs_of("Big.png") == big0 && runs_of("Road Trip.jpg") == road0 + 1 && forks == 3);
    assert(pl_files("pl", 3, &empty) == 1 && !empty); /* cd 3's twenty live songs: their 2x2, not a marker */
    assert(pl_files("pl", 1, &empty) == 1 && empty);  /* 20,000 albums, every file gone: marked, no run */
    assert(pl_files("pl", 2, &empty) == 1 && !empty && pl_files("plc", 2, &empty) == 1 && empty); /* the picture won't decode */
    assert(pl_files("plc", 5, &empty) == 1 && empty && pl_files("pl", 5, &empty) == 1 && empty); /* over 12 MP: marked */
    assert(!pl_ui_opens && !messy_ui_execs && !tmp_files() && access(PL_NOTE, 0));
    /* The page shown again: every row asks, the art there now answers at once, nothing built again. */
    runs = ff_runs(), fopens = ui_fopens;
    int reads = messy_reads;
    playlist_art_refresh();
    for (int cd = 0; cd <= 100; ++cd) playlist_art(cd, messy_name(cd, name), 1, url, sizeof url);
    assert(ui_fopens - fopens <= 2 * 101);
    run();
    assert(messy_reads == reads && playlist_songs(0) == 300); /* cd 0 kept by the refresh: no build */
    assert(ff_runs() == runs && runs_of("Big.png") == big0);
    assert(!pl_ui_opens && !messy_ui_execs && !tmp_files());
    /* Return mid-build waits for the cover in hand at most: none after it, nothing named or marked. */
    d = opendir(PEQ_ROOT "/mnt/mmc/.coverflow");
    for (struct dirent *e; d && (e = readdir(d));)
        if (!strncmp(e->d_name, "pl_3_", 5)) snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/.coverflow/%s", e->d_name), unlink(path);
    if (d) closedir(d);
    calls = 0, block_at = 0, joining = 0;
    assert(!playlist_art(3, "Gone", 1, url, sizeof url));
    wait_blocked();
    pthread_t releaser;
    pthread_create(&releaser, 0, release, 0);
    playlist_art_stop();
    pthread_join(releaser, 0);
    block_at = -1;
    assert(calls == 1 && !pl_files("pl", 3, &empty) && !tmp_files() && access(PL_NOTE, 0));
    messy = 0;
    unlink(PEQ_ROOT "/mnt/mmc/_explaylist_data/Big.png"), unlink(PEQ_ROOT "/mnt/mmc/_explaylist_data/Road Trip.jpg");
    embedded_fails = fails;
}

/* Songs whose files are gone (deleted over USB, a bad m3u line: stock's rescan leaves playlist rows)
   cost no ffmpeg run and none of the 16 tries: forty of them ahead of the one album still there
   still give its art. */
static void playlist_art_gone(void) {
    char url[700], dir[600];
    int empty = 1, fails = embedded_fails, runs = ff_runs();
    embedded_fails = 1; /* no embedded art: each album's build reaches ffmpeg */
    pl_n = 0;
    for (int i = 0; i < 40; ++i) {
        snprintf(pl_urls[pl_n], 600, PEQ_ROOT "/music/Gone %d/1.flac", i);
        pl_rows[pl_n].cd = 121, pl_rows[pl_n].id = 2200 + i, pl_rows[pl_n].url = pl_urls[pl_n];
        pl_rows[pl_n].album = "Gone", pl_rows[pl_n].artist = pl_urls[pl_n], pl_rows[pl_n++].aa = "";
    }
    pl_song(121, 2300, "PL Live");
    snprintf(dir, sizeof dir, PEQ_ROOT "/music/PL Live/cover.jpg");
    fclose(fopen(dir, "w"));
    pl_refresh();
    assert(!playlist_art(121, "Gone", 0, url, sizeof url));
    run();
    assert(ff_runs() == runs && pl_files("pl", 121, &empty) == 1 && !empty && !tmp_files());
    embedded_fails = fails;
}

/* Refresh Cover Flow with Playlists open under Settings, mid-build: the build stops first (a join),
   so the 2x2 whose cached cover Refresh deletes is not tiled from a missing file and marked as
   none: no pl_ file, empty or not, and the next ask builds it. */
static void *release_soon(void *unused) { /* at the join, or after 200 ms when none comes */
    for (int i = 0; i < 200 && !joining; ++i) usleep(1000);
    blocked = 0;
    return 0;
}
static void playlist_art_refresh_cf(void) {
    extern void coverflow_refresh_art(void);
    char url[700], path[600];
    int empty = 0, fails = embedded_fails;
    embedded_fails = 0;
    pl_n = 0;
    pl_song(24, 2401, "PL Kept"), pl_song(24, 2402, "PL Built");
    FILE *f = fopen(art_path(path, tags_key("Artist", "PL Kept"), ""), "w");
    fputs("art", f), fclose(f);
    pl_refresh();
    calls = 0, block_at = 0, joining = 0;
    assert(!playlist_art(24, "Twenty-four", 0, url, sizeof url));
    wait_blocked(); /* "PL Built"'s album_art, "PL Kept"'s cached cover already picked */
    pthread_t releaser;
    pthread_create(&releaser, 0, release_soon, 0);
    coverflow_refresh_art();
    pthread_join(releaser, 0);
    block_at = -1;
    run();
    assert(!pl_files("pl", 24, &empty) && !tmp_files() && !timer_fn);
    assert(!playlist_art(24, "Twenty-four", 0, url, sizeof url));
    run();
    assert(pl_files("pl", 24, &empty) == 1 && !empty);
    embedded_fails = fails;
}

/* ---- Playlists as Cover Flow (coverflow_playlists, which playlists.c opens over the veiled
   Playlists page): the playlists as covers, no cards, their art playlist_art.c's. ---- */
int coverflow_playlists(void *lists), lettered(void *w), coverflow_bounded(void *w), coverflow_playlist_queue(void);
void *coverflow_album(void *page), *coverflow_tracks(void *page), *coverflow_playlist(void *page);
void *coverflow_playlists_close(void);
unsigned coverflow_scope(void *page);
static void *list_win;
static int list_cd = -1;
void playlist_open(void *win, int cd_num) { list_win = win, list_cd = cd_num; } /* playlists.c's */
static int pl_staged; /* each getMusicByPlayList */
int getMusicByPlayList(int cd) { /* a playlist's songs: 40's are records 0 and 1, 41's record 2, 42's 3 to 9 */
    deque *d = shim_dir;
    d->n = 0, ++pl_staged;
    if (messy) { /* the messy library's cd 3: 300 songs (cd 1's 20,000 would overflow the deque) */
        for (int i = 0; cd == 3 && i < 300; ++i) d->at[d->n++] = records[i % albums];
        return (int)d->n;
    }
    for (int i = cd == 41 ? 2 : cd == 42 ? 3 : 0; i < (cd == 40 ? 2 : cd == 41 ? 3 : cd == 42 ? 10 : 0); ++i) d->at[d->n++] = records[i];
    return (int)d->n;
}
static const char *second_caption(widget *covers_view) { /* the second label: the album's artist */
    for (int i = 0, k = 0; i < covers_view->nkids; ++i)
        if (!strcmp(w[covers_view->kids[i]].type, "hscroll_label") && k++) return w[covers_view->kids[i]].text;
    return "";
}
static unsigned saved_key(void) { /* the album key kept on the card to reopen on */
    unsigned key = 0;
    FILE *f = fopen(PEQ_ROOT "/mnt/mmc/.coverflow/album", "rb");
    if (f) assert(fread(&key, sizeof key, 1, f) == 1), fclose(f);
    return key;
}
/* Classic's Centre on a playlist turns it over: the header its name and its song count; each song
   two lines of a 45 px row, its title over its artist in grey; five rows
   show where an album's seven do. A pick plays the songs as a song list. */
static widget *kid(widget *x, const char *type) {
    for (int i = 0; i < x->nkids; ++i)
        if (!strcmp(w[x->kids[i]].type, type)) return &w[x->kids[i]];
    return 0;
}
static int *box(widget *x) { return (int *)x->raw; } /* W_X, W_Y, W_W, W_H */
static void flip_playlist(void) {
    static char list[0x60];
    const char *kept[7][2];
    int painter = shim_themes[1].painter[PAINT_PAGES];
    assert(albums >= 10);
    for (int i = 0; i < 7; ++i) {
        char **t = (char **)(records[3 + i] + REC_TITLE), **a = (char **)(records[3 + i] + REC_ARTIST);
        kept[i][0] = *t, kept[i][1] = *a;
        *t = names[3 + i], *a = i == 1 ? "Other" : "Artist";
    }
    memset(list, 0, sizeof list);
    *(int *)(list + REC_ID) = *(int *)(list + REC_DISC) = 42;
    *(const char **)(list + REC_TITLE) = "Mixed";
    deque *d = _create_deque(0);
    d->at[d->n++] = list;
    shim_themes[1].painter[PAINT_PAGES] = PAGES_CLASSIC_LIGHT;
    page = &w[nw + 1];
    assert(coverflow_playlists(d));
    assert(coverflow_bounded(slide())); /* one playlist: one cover, not seven of it */
    top_window = page;
    w[slide()->kids[0]].click(w[slide()->kids[0]].ctx, 0);
    run();
    widget *panel = 0;
    for (int i = 0; i < page->nkids; ++i)
        if (box(&w[page->kids[i]])[2] == 294) panel = &w[page->kids[i]];
    assert(panel && deque_size(coverflow_tracks(page)) == 7);
    const char *head[2] = { 0, 0 };
    for (int i = 0, k = 0; i < panel->nkids; ++i)
        if (!strcmp(w[panel->kids[i]].type, "hscroll_label") && k < 2) head[k++] = w[panel->kids[i]].text;
    assert(head[0] && !strcmp(head[0], "Mixed") && head[1] && !strcmp(head[1], "7 songs"));
    widget *lv = kid(panel, "list_view"), *view = kid(lv, "scroll_view");
    assert(box(lv)[1] == 57 && box(lv)[3] == 228 && box(lv)[3] / 45 == 5 && view->nkids == 7);
    for (int i = 0; i < 7; ++i) {
        widget *row = &w[view->kids[i]];
        assert(box(row)[1] == i * 45 && box(row)[3] == 45 && row->nkids == 2);
        widget *t = &w[row->kids[0]], *a = &w[row->kids[1]];
        assert(!strcmp(t->text, names[3 + i]) && !strcmp(a->text, i == 1 ? "Other" : "Artist"));
        assert(box(t)[1] + box(t)[3] <= box(a)[1] + 1 && box(a)[1] + box(a)[3] <= 45 && box(t)[0] == box(a)[0]);
        assert((unsigned)a->fg == 0xffaaaaaau && !t->fg && box(t)[0] + box(t)[2] == 282);
    }
    played_cls = -1;
    ++playback_loads; /* a queue loaded since the covers' plays above */
    assert(!coverflow_playlist_queue());
    w[view->kids[3]].click(w[view->kids[3]].ctx, 0);
    assert(played_cls == 0xf001 && played_idx == 3);
    /* its queue keeps Now Playing's More from offering Delete (the library's) until another loads */
    assert(coverflow_playlist_queue());
    ++playback_loads; /* any other queue started: stock's load (ringnav_load) counts it */
    assert(!coverflow_playlist_queue());
    close_page();
    /* a podcast in it is left out of the turned panel and its count, as its own list leaves it out */
    const char **path = (const char **)(records[4] + REC_PATH), *was_path = *path;
    *path = "/mnt/mmc/Podcasts/Show/ep.mp3";
    page = &w[nw + 1];
    assert(coverflow_playlists(d));
    top_window = page;
    w[slide()->kids[0]].click(w[slide()->kids[0]].ctx, 0);
    run();
    panel = 0;
    for (int i = 0; i < page->nkids; ++i)
        if (box(&w[page->kids[i]])[2] == 294) panel = &w[page->kids[i]];
    assert(panel && deque_size(coverflow_tracks(page)) == 6);
    for (unsigned i = 0; i < 6; ++i) assert(deque_at(coverflow_tracks(page), i) != records[4]);
    const char *six = 0;
    for (int i = 0, k = 0; i < panel->nkids; ++i)
        if (!strcmp(w[panel->kids[i]].type, "hscroll_label") && k++ == 1) six = w[panel->kids[i]].text;
    assert(six && !strcmp(six, "6 songs"));
    close_page();
    *path = was_path;
    /* an empty playlist turns over too: its name over "0 songs", no rows */
    *(int *)(list + REC_ID) = *(int *)(list + REC_DISC) = 43;
    *(const char **)(list + REC_TITLE) = "Empty";
    page = &w[nw + 1];
    assert(coverflow_playlists(d));
    top_window = page;
    w[slide()->kids[0]].click(w[slide()->kids[0]].ctx, 0);
    run();
    panel = 0;
    for (int i = 0; i < page->nkids; ++i)
        if (box(&w[page->kids[i]])[2] == 294) panel = &w[page->kids[i]];
    assert(panel && coverflow_tracks(page) && !deque_size(coverflow_tracks(page)));
    head[0] = head[1] = 0;
    for (int i = 0, k = 0; i < panel->nkids; ++i)
        if (!strcmp(w[panel->kids[i]].type, "hscroll_label") && k < 2) head[k++] = w[panel->kids[i]].text;
    assert(head[0] && !strcmp(head[0], "Empty") && head[1] && !strcmp(head[1], "0 songs"));
    assert(!kid(kid(panel, "list_view"), "scroll_view")->nkids);
    close_page();
    top_window = 0;
    free(d);
    shim_themes[1].painter[PAINT_PAGES] = painter;
    for (int i = 0; i < 7; ++i)
        *(const char **)(records[3 + i] + REC_TITLE) = kept[i][0], *(const char **)(records[3 + i] + REC_ARTIST) = kept[i][1];
}
/* Classic's turned panel builds every row at once (about 3 KB each): a playlist over PANEL_ROWS
   (200) opens in stock's list on the Playlists page under the covers instead, as Minimal's Centre
   does: by its count in the table, with no songs loaded, or, the table not read yet (count 0), by the
   songs it loaded. */
static void flip_big_playlist(void) {
    static char list[2][0x60];
    int painter = shim_themes[1].painter[PAINT_PAGES];
    deque *d = _create_deque(0);
    for (int i = 0; i < 2; ++i) {
        memset(list[i], 0, 0x60);
        *(int *)(list[i] + REC_ID) = *(int *)(list[i] + REC_DISC) = i ? 3 : 1;
        *(const char **)(list[i] + REC_TITLE) = i ? "Gone" : "Mix";
        d->at[d->n++] = list[i];
    }
    messy_on();
    assert(!playlist_songs(1) && !playlist_songs(3)); /* the table not read yet */
    shim_themes[1].painter[PAINT_PAGES] = PAGES_CLASSIC_LIGHT;
    page = &w[nw + 1];
    assert(coverflow_playlists(d));
    widget *under = make(0, "playlist_page");
    wm_stack[0] = under, wm_stack[1] = page, wm_n = 2, top_window = page;
    for (int pass = 0; pass < 2; ++pass) {
        int cd = pass ? 1 : 3, staged_before = pl_staged;
        list_win = 0, list_cd = -1;
        slide_menu_set_value(slide(), pass ? 0 : 1);
        changed_fn(0, 0);
        w[slide()->kids[pass ? 0 : 1]].click(w[slide()->kids[pass ? 0 : 1]].ctx, 0);
        run();
        int panels = 0;
        for (int i = 0; i < page->nkids; ++i) panels += box(&w[page->kids[i]])[2] == 294;
        assert(list_win == under && list_cd == cd && !panels && !coverflow_tracks(page) && slide()->visible);
        assert(pl_staged == staged_before + !pass); /* the backstop loaded them; the count needs none */
        if (pass) continue;
        playlist_art_stop(); /* the covers' builds: the shim's one timer slot lost their poll to the Centre's */
        pl_refresh();
        assert(playlist_songs(1) == 20000 && playlist_songs(3) == 300);
    }
    playlist_art_stop();
    close_page();
    wm_n = 0, top_window = 0, messy = 0;
    free(d);
    shim_themes[1].painter[PAINT_PAGES] = painter;
}
static int rows_heard; /* the rows' own playlist_art_ready, under the covers' */
static void rows_hear(int cd_num) { rows_heard = cd_num; }
static void playlist_covers(void) {
    static char lists[2][0x60];
    static const cf_style_t shipped = CF_SHIPPED;
    const cf_style_t fixture = shim_themes[1].cf;
    char url[600];
    unsigned kept = saved_key(), was[5] = { classic, frame_fail, square_art, pressed, embedded_fails };
    embedded_fails = 0; /* the playlists' songs' art is their embedded art */
    pl_n = 0;
    pl_song(40, 2001, "PL CF A"), pl_song(40, 2002, "PL CF A"), pl_song(41, 2003, "PL CF B");
    deque *d = _create_deque(0);
    for (int i = 0; i < 2; ++i) {
        memset(lists[i], 0, 0x60);
        *(int *)(lists[i] + REC_ID) = *(int *)(lists[i] + REC_DISC) = 40 + i;
        *(const char **)(lists[i] + REC_TITLE) = i ? "List 1" : "List 0";
        d->at[d->n++] = lists[i];
    }
    /* Flat: two covers, no card; the name over its count; placeholders while the art builds, then
       the art, the page still open. */
    classic = 0, frame_fail = 1, square_art = 1, pressed = 0;
    page = &w[nw + 1];
    playlist_art_refresh(); /* playlists.c's, as it opens them: the worker reads the songs */
    playlist_art_ready = rows_hear, rows_heard = 0; /* the rows', which the covers take over */
    assert(coverflow_playlists(d) && d->n == 2); /* the caller's list stays the caller's */
    widget *s = slide(), *view = &w[s->parent];
    assert(s && s->nkids == 2 && !lettered(s) && coverflow_bounded(s)); /* two covers, not A B A B A */
    assert(!strcmp(album_caption(view), "List 0") && !strcmp(second_caption(view), "0 songs")); /* not read yet */
    assert(!strcmp(w[s->kids[0]].image, PLACEHOLDER) && !strcmp(w[s->kids[1]].image, PLACEHOLDER) && timer_fn);
    run();
    assert(!strcmp(second_caption(view), "2 songs")); /* the read landed: the count named again */
    assert(!rows_heard); /* the hidden rows ask again as their page comes to the front */
    for (int i = 0; i < 2; ++i)
        assert(playlist_art(40 + i, "", 0, url, sizeof url) && !strcmp(w[s->kids[i]].image, url) && !strstr(url, "_s.jpg"));
    slide_menu_set_value(s, 1);
    changed_fn(0, 0);
    assert(!strcmp(album_caption(view), "List 1") && !strcmp(second_caption(view), "1 song"));
    /* a playlist with no name: an empty caption, not the one before */
    *(const char **)(lists[0] + REC_TITLE) = 0;
    slide_menu_set_value(s, 0);
    changed_fn(0, 0);
    assert(!strcmp(album_caption(view), "") && !strcmp(second_caption(view), "2 songs"));
    *(const char **)(lists[0] + REC_TITLE) = "List 0";
    slide_menu_set_value(s, 1);
    changed_fn(0, 0);
    /* No album hold or rating on a playlist; its hold is the playlist's, the record at the centre. */
    assert(!coverflow_album(page) && !coverflow_playlist(0));
    assert(coverflow_playlist(page) && *(int *)((char *)coverflow_playlist(page) + REC_DISC) == 41);
    /* Minimal's Centre: the playlist opens from its row on the page under the covers, by its
       cd_num, and the covers stay, for Return from its list to come back to. */
    widget *under = make(0, "playlist_page");
    wm_stack[0] = under, wm_stack[1] = page, wm_n = 2, force_closes = 0;
    w[s->kids[1]].click(w[s->kids[1]].ctx, 0);
    run();
    assert(!force_closes && list_win == under && list_cd == 41);
    assert(slide() == s && s->visible && !strcmp(album_caption(view), "List 1"));
    /* The hold's New Playlist and Select Playlists…: the covers close, unanimated, for the Playlists
       page under them; with no covers up, nothing. */
    force_closes = 0;
    assert(coverflow_playlists_close() == under && force_closes == 1 && force_closed == page);
    close_page();
    assert(!playlist_art_ready); /* the rows take it again as their page is rebuilt */
    assert(!coverflow_playlists_close() && force_closes == 1 && !coverflow_playlist(page));
    /* Classic's Centre, with no room to turn: the songs as a list, under the name; a pick plays
       them as a song list (0xf001), never an album's resume; no position memory. */
    shim_themes[0].painter[PAINT_PAGES] = PAGES_CLASSIC_LIGHT;
    page = &w[nw + 1];
    wm_stack[1] = page;
    assert(coverflow_playlists(d));
    w[slide()->kids[0]].click(w[slide()->kids[0]].ctx, 0);
    run();
    void *tracks = coverflow_tracks(page);
    assert(!strcmp(title(), "List 0") && deque_size(tracks) == 2 && deque_at(tracks, 1) == records[1]);
    assert(!coverflow_scope(page));
    memset(shim_memory, 0x5a, sizeof shim_memory);
    widget *row = 0;
    for (int i = (int)(page - w); i <= nw; ++i)
        if (!strcmp(w[i].type, "list_item") && w[i].click) row = row ? row : &w[i];
    row->click(row->ctx, 0);
    assert(played_cls == 0xf001 && shim_memory[0x611] == 0x5a);
    key(KEY_RETURN); /* the songs back to the covers */
    assert(slide() && slide()->visible);
    /* Return on the covers: the Playlists page under them closes first, then the covers. */
    force_closes = 0, backs = 0;
    key(KEY_RETURN);
    assert(force_closes == 1 && force_closed == under && backs == 1);
    close_page();
    shim_themes[0].painter[PAINT_PAGES] = PAGES_STOCK;
    wm_n = 0;
    /* Depth: a cover whose art is still building shows the placeholder, and its art once made,
       decoded then, the page still open; a fast spin goes by covers, never letters. */
    pl_song(41, 2004, "PL CF C"); /* 41's songs changed: its art is built again */
    pl_refresh();
    classic = 1, frame_fail = 0;
    shim_themes[1].cf = shipped;
    page = &w[nw + 1];
    assert(coverflow_playlists(d));
    s = slide();
    top_window = page;
    int canvas[16] = { 0 }, before = loads;
    coverflow_paint(s, canvas);
    assert(loads == before + 1 && timer_fn); /* 40's art; 41's the placeholder */
    /* the builds stopped under the covers (USB Storage, Refresh Cover Flow): 41 stays on the
       placeholder, then is asked again as the covers come back to the front */
    playlist_art_stop();
    run();
    coverflow_paint(s, canvas);
    assert(loads == before + 1 && !timer_fn);
    foreground_fn(page, 0);
    run();
    coverflow_paint(s, canvas);
    run();
    coverflow_paint(s, canvas);
    assert(loads == before + 2); /* 41's, as it was made */
    th_pass(0, 17);
    int letters0 = letters;
    th_down();
    for (int t = 0; t < 2500; t += 17) th_spin(150, 17);
    assert(letters == letters0);
    th_rest();
    close_page();
    top_window = 0;
    th_pass(0, 17);
    shim_themes[1].cf = fixture;
    flip_playlist();
    flip_big_playlist();
    assert(saved_key() == kept); /* a playlist is never the album Cover Flow reopens on */
    free(d);
    playlist_art_ready = 0;
    classic = was[0], frame_fail = was[1], square_art = was[2], pressed = was[3], embedded_fails = was[4];
}

/* More's Rename or Delete over the covers (stock's list, getAllPlayList, read again as they come
   back to the front): a deleted playlist's cover goes, a renamed one's caption follows, and the
   centre stays on its playlist; with none left the covers close, for the list under them. Nothing
   changed: the covers stay as they are. */
static char *pl_heads[4];
static int pl_nheads;
int getAllPlayList(void) {
    deque *d = shim_dir;
    d->n = 0;
    for (int i = 0; i < pl_nheads; ++i) d->at[d->n++] = pl_heads[i];
    return pl_nheads;
}
static void playlist_covers_back(void) {
    static char lists[3][0x60], renamed[0x60], made[0x60];
    unsigned was[2] = { classic, frame_fail };
    deque *d = _create_deque(0);
    for (int i = 0; i < 3; ++i) {
        memset(lists[i], 0, 0x60);
        *(int *)(lists[i] + REC_ID) = *(int *)(lists[i] + REC_DISC) = 50 + i;
        *(const char **)(lists[i] + REC_TITLE) = i == 0 ? "Back 0" : i == 1 ? "Back 1" : "Back 2";
        d->at[d->n++] = pl_heads[i] = lists[i];
    }
    pl_nheads = 3;
    classic = 0, frame_fail = 1;
    page = &w[nw + 1];
    foreground_fn = 0;
    assert(coverflow_playlists(d) && foreground_fn);
    widget *s = slide(), *view = &w[s->parent];
    slide_menu_set_value(s, 1);
    changed_fn(0, 0);
    foreground_fn(page, 0);
    run();
    assert(slide() == s && s->nkids == 3 && !strcmp(album_caption(view), "Back 1"));
    /* A song added to Back 1 from its list (Edit Playlist, Add to Playlist): its count follows as
       the covers come back, the art's worker reading the songs again. */
    assert(strcmp(second_caption(view), "1 song"));
    pl_song(51, 2101, "PL Back");
    playlist_art_stop(); /* the shim's one timer slot lost the running worker's poll */
    foreground_fn(page, 0);
    run();
    assert(slide() == s && playlist_songs(51) == 1 && !strcmp(second_caption(view), "1 song"));
    /* A failed read of the headers keeps the covers. */
    pl_nheads = -1, force_closes = 0;
    foreground_fn(page, 0);
    run();
    assert(!force_closes && slide() == s && s->nkids == 3 && !strcmp(album_caption(view), "Back 1"));
    pl_nheads = 3;
    /* A Centre queued as the covers come back after an edit (Back 2 deleted) wins, on its own
       playlist; the covers are read again on its way back. */
    widget *list_page = make(0, "playlist_page");
    wm_stack[0] = list_page, wm_stack[1] = page, wm_n = 2;
    list_win = 0, list_cd = -1, pl_nheads = 2;
    w[s->kids[1]].click(w[s->kids[1]].ctx, 0);
    int (*centre)(const void *) = timer_fn;
    foreground_fn(page, 0); /* the shim's one timer slot: the read's timer runs first */
    run();
    assert(slide() == s && s->nkids == 3);
    centre(0);
    assert(list_win == list_page && list_cd == 51);
    pl_nheads = 3, wm_n = 0;
    /* Classic's songs (a list here: no room to turn): the covers read again as they come back from
       them, not before; a playlist made from a song's hold there gets its cover. */
    int painter = shim_themes[0].painter[PAINT_PAGES];
    shim_themes[0].painter[PAINT_PAGES] = PAGES_CLASSIC_LIGHT;
    w[s->kids[1]].click(w[s->kids[1]].ctx, 0);
    run();
    assert(!strcmp(title(), "Back 1"));
    memset(made, 0, 0x60);
    *(int *)(made + REC_ID) = *(int *)(made + REC_DISC) = 53;
    *(const char **)(made + REC_TITLE) = "Made";
    pl_heads[3] = made, pl_nheads = 4;
    foreground_fn(page, 0); /* back from the picker, over the songs */
    run();
    assert(slide() == s && s->nkids == 3);
    key(KEY_RETURN);
    s = slide(), view = &w[s->parent];
    assert(s->nkids == 4 && !strcmp(album_caption(view), "Back 1"));
    shim_themes[0].painter[PAINT_PAGES] = painter;
    memcpy(renamed, lists[1], 0x60);
    *(const char **)(renamed + REC_TITLE) = "Renamed";
    pl_heads[0] = renamed, pl_heads[1] = lists[2], pl_nheads = 2; /* Back 0 deleted, Back 1 renamed */
    foreground_fn(page, 0);
    run();
    s = slide(), view = &w[s->parent];
    assert(s->nkids == 2 && *(int *)(s->raw + SLIDE_INDEX) == 0 && !strcmp(album_caption(view), "Renamed"));
    assert(*(int *)((char *)coverflow_playlist(page) + REC_DISC) == 51);
    pl_heads[0] = lists[2], pl_nheads = 1; /* the centre's own deleted: the cover at its place */
    foreground_fn(page, 0);
    run();
    s = slide(), view = &w[s->parent];
    assert(s->nkids == 1 && !strcmp(album_caption(view), "Back 2"));
    widget *under = make(0, "playlist_page");
    wm_stack[0] = under, wm_stack[1] = page, wm_n = 2, force_closes = 0;
    pl_nheads = 0;
    foreground_fn(page, 0);
    run();
    assert(force_closes == 1 && force_closed == page);
    close_page();
    wm_n = 0;
    free(d);
    playlist_art_ready = 0;
    classic = was[0], frame_fail = was[1];
}

/* ---- A track's play and its resume after a reboot. ---- */
extern int album_only(void *, void *, int);
static int open_card(const char *name) { /* the card whose tracks list is titled name, left open */
    widget *s = slide();
    for (int i = 0; s && i < s->nkids; ++i) {
        w[s->kids[i]].click(w[s->kids[i]].ctx, 0);
        run();
        if (!strcmp(title(), name)) return 1;
        key(KEY_RETURN);
    }
    return 0;
}
static widget *track_row(int k) { /* the newest row under the page that plays track k */
    for (int i = nw; i > page - w; --i)
        if (!strcmp(w[i].type, "list_item") && under(&w[i], page) && w[i].click && w[i].ctx == (void *)(long)k) return &w[i];
    return 0;
}
/* Stock's resume after a reboot (memeory_startplayer, then playback.c's stock_memory): getMusicByAlbum of the saved
   name, or of NULL with the Unknown flag, the saved track found by its path, then album_only to its album. */
static deque *resumed(const char *path, int *at) {
    getMusicByAlbum(shim_memory[0x60d] ? 0 : (const char *)shim_memory + 0x611);
    deque *d = calloc(1, sizeof *d);
    *d = *(deque *)shim_dir;
    int k = -1;
    for (unsigned i = 0; i < d->n; ++i)
        if (!strcmp(*(char **)((char *)d->at[i] + REC_PATH), path)) k = (int)i;
    assert(k >= 0);
    *at = album_only(d, d->at[k], k);
    return d;
}
static int has(const deque *d, const void *r) {
    for (unsigned i = 0; i < d->n; ++i)
        if (d->at[i] == r) return 1;
    return 0;
}
/* The Unknown Album card (x.flac and y.flac, untagged, in two folders) resumes whole, at the song
   that was playing. */
static void unknown_resume(void) {
    char *loose[2] = { untagged_song, untagged_more };
    static char where[2][600];
    for (int i = 0; i < 2; ++i) {
        char d[500];
        snprintf(d, sizeof d, PEQ_ROOT "/music/Loose %c", 'A' + i);
        mkdir(d, 0755);
        snprintf(where[i], 600, "%s/%c.flac", d, 'x' + i);
        fclose(fopen(where[i], "w"));
        memset(loose[i], 0, 0x60);
        *(int *)(loose[i] + REC_ID) = 60 + i;
        *(char **)(loose[i] + REC_PATH) = where[i];
    }
    unknown_row = unknown_songs = untagged_two = 1;
    rescan();
    open_page();
    assert(open_card("Unknown Album"));
    deque *t = coverflow_tracks(page);
    assert(t->n == 2 && t->at[0] == untagged_song && t->at[1] == untagged_more);
    memset(shim_memory, 0, sizeof shim_memory);
    widget *row = track_row(1);
    assert(row);
    row->click(row->ctx, 0);
    assert(shim_memory[0x60d] == 1 && nav_ctx.cls == 0xff10 && nav_ctx.idx == 1);
    key(KEY_RETURN);
    close_page();
    int at;
    deque *q = resumed(where[1], &at);
    assert(q->n == 2 && has(q, untagged_song) && has(q, untagged_more) && q->at[at] == untagged_more);
    free(q);
    unknown_row = unknown_songs = untagged_two = 0;
    memset(untagged_song, 0, 0x60), memset(untagged_more, 0, 0x60);
    rescan();
}

int main(void) {
    big_library = 1; /* every test of letters, years and half copies; off: unlettered_sorts, half_art */
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
    assert(!strcmp(title(), "Update Library first") && !slide() && queries == 1);
    /* Every list (message, progress, tracks) comes from list(): iPod rows are transparent, so its
       list_view paints black itself rather than the theme's light card. */
    int lists = 0;
    for (int i = nw; i > page - w; --i) if (!strcmp(w[i].type, "list_view")) { assert((unsigned)w[i].bg == 0xff000000u); ++lists; }
    assert(lists == 1);
    key(KEY_RETURN); close_page();
    album("Cover", "cover.jpg");
    *(volatile int *)SCAN_THREAD = 1;
    open_page();
    assert(!strcmp(title(), "Update Library first") && queries == 1 && !calls);
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
    joining = 0;
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
    rest_pinned();
    thumb_wheel();
    thumb_shipped();
    letters_176();
    arch();
    thumb_letters_held();
    letters_slide();
    lift_handoff();
    fold_flow();
    letters_entry();
    entry_handover();
    real_spins();
    letters_turn_back();
    short_letters();
    held_row();
    fold_clearance();
    fold_frames();
    enter_frames();
    edge_drawn();
    flip_frames();
    sorting();
    unlettered_sorts();
    by_years(0);
    by_years(1);
    open_page();
    while (saved_sort() != 0) press_sort();
    close_page();
    artist_only();
    unknown_card();
    same_name();
    disc_folders();
    audiobooks();
    ffmpeg_art();
    player_cover();
    flip_turn();
    scoped_pages();
    cancel_build();
    unknown_resume();
    playlist_art_files();
    playlist_art_crash();
    playlist_art_kills();
    playlist_art_order();
    playlist_art_gone();
    playlist_covers();
    playlist_covers_back();
    playlist_art_messy();
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
    half_art();
    loader_test();
    no_more_reset();
    playlist_art_refresh_cf(); /* last: Refresh empties the cache the tests above share */
    return 0;
}
"""


# Stands in for stock ffmpeg: "ffmpeg" in its output (the last argument) for a .webp source or a
# track under Ffok/, else a failure; a playlist's 2x2 (-filter_complex) writes "tile" and leaves its
# arguments, a line each, in ffmpeg.args, but playlist 66's fails, an exit of its own.
FFMPEG_STUB = r"""#!/bin/sh
for a; do [ "$prev" = -i ] && { src=$a; [ -e "$a" ] || gone=1; }; prev=$a; done
printf '%s\n' "$src" >> "$0.runs"
case " $* " in *" -filter_complex "*) printf '%s\n' "$@" > "$0.args"; case $a in */pl_66_*) exit 1;; esac; [ -z "$gone" ] || exit 1; printf tile > "$a"; exit 0;; esac
case $src in *.webp|*/Ffok/*) printf ffmpeg > "$a";; */Fkill/*|*/Big.png) kill -9 $$;;
  */_explaylist_data/*) grep -q '^picture$' "$src" && printf '%s' "${src##*/}" > "$a" || exit 1;; *) exit 1;; esac
"""


def main():
    import argparse, os
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--captures', type=pathlib.Path,
                    help='write the renderer\'s frames (rest, quarter, half, three-quarter turns, one album) as PNGs here')
    a = ap.parse_args()
    # shim_themes stands in for the generated themes[]: each theme's .home_* fields must match the
    # offsets.inc expressions tools/theme.py ROLE_MACROS gives it (0, unset in the shim, left out).
    from theme import ROLE_MACROS, builtins, stops_c, half, accent_default, validate, ThemeError
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
                for i, m in enumerate(({**ROLE_MACROS['minimal'], **SHELF_60, 'cf.art_persp': builtins()[0]['cf.art_persp']},))]
    cf_style += [f'-DCF_{f[3:].upper()}={v}' for f, v in SHELF_60.items() if f not in ('cf.tilt', 'cf.swing')]  # CF_X1 ...: the fixture's own
    # The wheel's numbers are retuned on the device: these cases pin the model, with its own fixed ones.
    # (letters off: thumb_shipped has them on the shipped numbers)
    fixture = {**classic_light, **SHELF_180, 'cf.ticks_album': 13, 'cf.fast_at': 25, 'cf.fast_full': 120, 'cf.jump_ticks': 0}
    cf_style.append('-DCF_STYLE1={ ' + ', '.join(f'.{f[3:]} = {v}' for f, v in fixture.items() if f.startswith('cf.')) + ' }')
    cf_style.append('-DCF_SHIPPED={ ' + ', '.join(f'.{f[3:]} = {v}' for f, v in classic_light.items() if f.startswith('cf.')) + ' }')
    cf_style.append('-DCF_SHIPPED0={ ' + ', '.join(f'.{f[3:]} = {v}' for f, v in builtins()[0].items() if f.startswith('cf.')) + ' }')
    cf_style.append('-DCF_SHIPPED2={ ' + ', '.join(f'.{f[3:]} = {v}' for f, v in builtins()[2].items() if f.startswith('cf.')) + ' }')
    cf_style.append('-DPINNED_FRAMES=0x055534d4u')
    # The built-in numbers the checks use, as coverflow.h and coverflow.c have them
    cf_style += [f'-D{k}={v}' for k, v in re.findall(r'^#define (CF_FOLD_MS|CF_ENTER_SPEED|FOLD_FROM|FOLD_TO|ENTER_RUN|LD_SLOTS|FX_ENTER_MS|FX_LETTERS_MS|FX_SLACK_MS|FX_LATE_MS|HALF_GAP_MS) (\d+)',
                                                 ''.join((ROOT/'patch'/f).read_text() for f in ('coverflow.h', 'coverflow.c', 'coverflow_letters.c', 'coverflow_loader.c')), re.M)]
    def accepts(t):
        try:
            validate(t, 't.xml')
        except ThemeError:
            return False
        return True
    # edge_drawn's held row at the smallest row_out tools/theme.py takes, each built-in theme's
    cf_style.append('-DCF_ROW_OUT_MIN={ ' + ', '.join(str(next(r for r in range(101) if accepts({**t, 'cf.row_out': r}))) for t in builtins()) + ' }')
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
        (tmp/'coverflow_all.c').write_text(CF_SOURCES)
        (tmp/'test.c').write_text(TEST)
        (tmp/'ffmpeg').write_text(FFMPEG_STUB)
        (tmp/'ffmpeg').chmod(0o755)
        builds = {'coverflow_test': ()}  # each build's own flags; its card is <name>-root
        def cc(name, src):  # each source on its own, so they compile at once
            return subprocess.Popen(['cc', '-m32', '-pthread', '-DPEQ_HOST', f'-DPEQ_ROOT="{tmp}/{name}-root"',
                        f'-DART_FFMPEG="{tmp}/ffmpeg"', '-D_GNU_SOURCE', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-result', '-Wno-unused-parameter', '-Wno-unused-function',
                        *builds[name], *cf_style, '-I', str(ROOT/'patch'), '-include', str(tmp/'shim.h'), '-c', str(tmp/src), '-o', str(tmp/f'{name}-{src}.o')])
        objects = [cc(name, src) for name in builds for src in ('coverflow_all.c', 'test.c')]
        assert all(o.wait() == 0 for o in objects), 'the host tests did not build'
        for name in builds:
            subprocess.run(['cc', '-m32', '-pthread', str(tmp/f'{name}-coverflow_all.c.o'), str(tmp/f'{name}-test.c.o'), '-lm', '-Wl,--wrap=fopen', '-o', str(tmp/name)], check=True)
        binary = tmp/'coverflow_test'
        env = dict(os.environ)
        spins = json.loads((ROOT/'test/fixtures/coverflow_spins.json').read_text())  # real_spins' input
        (tmp/'spins').write_text(spins['library'] + '\n' + ''.join(
            f"{k} {r['start']} {r['lands']} {len(r['passes'])}\n" + ''.join(f'{m} {t} {h}\n' for m, t, h in r['passes'])
            for k, r in spins['runs'].items()))
        env['CF_SPINS'] = str(tmp/'spins')
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
          ' Home art sources, fit, clip, cache and Split/Full layout in every theme, and Classic\'s pane, passed; Classic\'s slanted art and its one frame\'s key passed;'
          ' playlist art (own picture, 2x2, bad picture, songs changed mid-build, card full or gone) passed; playlists as covers (captions, art filling in, no letters, Centre, the Classic turn-over, Return) passed;'
          ' Classic\'s flip into an album\'s panel and back, the scoped pages\' cards, Go to Album, the Cancel row and the Unknown Album card\'s resume after a restart passed.')


if __name__ == '__main__':
    main()
