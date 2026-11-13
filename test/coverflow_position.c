/* The actual position painter, with only its widget/canvas boundary replaced. */
#include "../patch/offsets.inc"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#define Q2_COVERFLOW_H
#define CF_ONE 65536
#define ART_SIZE 160
#define RGBA(c) (0xff000000u | (c))

static struct {
    void *slide, *covers, *albums;
    int text_albums;
} cf;
static struct {
    int text_y, artist_y, top;
} cfs = { 220, 26, 32 };
static struct {
    int on;
    float pos;
} th;
static struct {
    void *panel;
} fl;
static int live = 1, albums = 400, cards = 400, centre, fraction, light, invalidations, fills;
static unsigned fill;
static unsigned clock_ms, timer_id, next_timer, timer_adds, dot_alpha;
static int fail_timer;
static int (*due)(const void *);
static int strip[4];
static int painting, queued_strip, dropped_in_paint;
static unsigned char slide[512], covers[512], canvas[512], lcd[512];
static unsigned pixels[375 * 320];
static int cf_floor(float f) { return (int)floorf(f); }
static int covers_live(void) { return live; }
static int enabled = 1;
static int coverflow_position_enabled(void) { return enabled; }
static unsigned time_now_ms(void) { return clock_ms; }
static unsigned timer_add(int (*fn)(const void *), void *ctx, unsigned ms) {
    assert(ms == 33 && !timer_id);
    if (fail_timer) return 0;
    due = fn;
    ++timer_adds;
    return timer_id = ++next_timer;
}
static void stop_timer(unsigned *id) {
    if (*id) {
        assert(*id == timer_id);
        timer_id = 0;
        due = NULL;
        *id = 0;
    }
}
static unsigned deque_size(void *d) { return (unsigned)albums; }
static unsigned widget_count_children(void *w) { return (unsigned)cards; }
static int visual(void *w, int n, int *c, int *frac) {
    *c = centre;
    *frac = fraction;
    return n;
}
static int widget_invalidate_force(void *w, void *r) {
    assert(w == covers);
    memcpy(strip, r, sizeof strip);
    ++invalidations;
    if (painting)
        ++dropped_in_paint;
    else
        queued_strip = 1;
    return 0;
}
static int ipod_light(void) { return light; }
static int stock_canvas_set_fill_color(void *c, unsigned v) {
    fill = v;
    return 0;
}
static int canvas_fill_rect(void *c, int x, int y, int w, int h) {
    assert(x >= 0 && y >= 0 && x + w <= 375 && y + h <= I(covers, W_H));
    for (int j = y; j < y + h; ++j)
        for (int i = x; i < x + w; ++i) pixels[j * 375 + i] = fill;
    ++fills;
    dot_alpha = fill >> 24;
    return 0;
}
#include "../patch/coverflow_position.c"

static int footer_mid(void) {
    return (cfs.text_y + cfs.artist_y + CF_ARTIST_H + I(covers, W_H)) / 2;
}
static void paint(int at) {
    centre = at ? 0 : 1;
    coverflow_position_paint(slide, canvas);
    centre = at;
    coverflow_position_paint(slide, canvas);
    fills = 0;
    memset(pixels, 0, sizeof pixels);
    coverflow_position_paint(covers, canvas);
    assert(fills == 5 && fill == 0x12345678);
    int x = coverflow_position_offset((float)at, albums, cards, 151), y = footer_mid();
    unsigned grey = 220u << 24 | (light ? 0x808080 : 0xaaaaaa);
    int count = 0;
    for (int i = 0; i < 375; ++i) count += pixels[y * 375 + i] == grey;
    assert(count == 9 && pixels[y * 375 + 107 + x + 4] == grey);
    for (int j = -2; j <= 1; ++j) assert(pixels[(y + j) * 375 + 107 + x + 4] == grey);
    assert(!pixels[(y - 3) * 375 + 107 + x + 4] && !pixels[(y + 2) * 375 + 107 + x + 4]);
    /* Track spans rows y-1,y; thumb spans y-2..y+1: centres coincide. */
    unsigned track = 255u << 24 | (light ? 0xb4b9c0 : 0x444444);
    int away = at < albums / 2 ? 107 + 159 : 107;
    assert(pixels[(y - 1) * 375 + away] == track && pixels[y * 375 + away] == track);
    assert(!pixels[(y - 2) * 375 + away] && !pixels[(y + 1) * 375 + away]);
}
static void tick(unsigned now) {
    clock_ms = now;
    assert(timer_id && due);
    if (!due(NULL)) timer_id = 0, due = NULL;
}
/* Model the UI's actual damage boundary: repainting the shelf alone does not paint a footer,
 * and damage requested from inside a paint may be consumed by that paint's final cleanup. */
static void moving_repaint(void) {
    painting = 1;
    coverflow_position_paint(slide, canvas);
    painting = 0;
}
static void present_strip(void) {
    assert(queued_strip);
    queued_strip = 0;
    memset(pixels, 0, sizeof pixels); /* the parent's background / cached cover frame */
    fills = 0;
    painting = 1;
    coverflow_position_paint(covers, canvas);
    painting = 0;
}
static void moving_pixel(void) {
    int x = coverflow_position_offset(th.on ? th.pos : (float)centre, albums, cards, 151);
    assert(pixels[footer_mid() * 375 + 107 + x + 4] == (220u << 24 | 0xaaaaaa));
    assert(position.r[1] >= cfs.text_y + cfs.artist_y + CF_ARTIST_H);
    assert(position.r[1] + position.r[3] <= I(covers, W_H));
    assert(position.r[1] + POSITION_STRIP_H / 2 == footer_mid());
}
static void repaint_regression(void) {
    moving_repaint();
    assert(!position.alpha && !timer_id);
    for (int i = 1; i <= 12; ++i) {
        clock_ms = (unsigned)i * 33;
        centre = i * 20;
        queued_strip = 0;
        moving_repaint();
        assert(position.alpha == 255 && timer_id);
        tick(clock_ms + 1);
        /* It must become drawable DURING the movement, well before the 800 ms idle hold. */
        assert(queued_strip);
        present_strip();
        assert(fills == 5 && dot_alpha == 220);
        moving_pixel();
    }
    /* The fractional wheel follower and screen-drag offset share the same painter. */
    th.on = 1;
    I(slide, SLIDE_INDEX) = 0;
    for (int i = 1; i <= 12; ++i) {
        clock_ms += 33;
        th.pos = (float)i * 12 + .25f;
        queued_strip = 0;
        moving_repaint();
        tick(clock_ms + 1);
        present_strip();
        moving_pixel();
    }
    th.on = 0;
    assert(!dropped_in_paint);
    unsigned last = position.moved;
    tick(last + 950);
    present_strip();
    assert(position.alpha == 127 && dot_alpha == 127 * 220 / 255);
    tick(last + 1100);
    present_strip();
    assert(!fills && !position.alpha && !timer_id);
    clock_ms = last + 1200;
    centre = 300;
    moving_repaint();
    tick(clock_ms + 1);
    present_strip();
    assert(fills == 5);
    enabled = 0;
    queued_strip = 0;
    coverflow_position_reset();
    present_strip();
    assert(!fills && !timer_id);
    puts("Repaint lifecycle: visible while moving, deferred damage survives paint, fade clears, "
         "switch-off clears immediately: passed");
}
int main(int argc, char **argv) {
    cf.slide = slide;
    cf.covers = covers;
    cf.albums = &albums;
    I(covers, W_W) = 375;
    I(covers, W_H) = 290;
    P(canvas, CANVAS_LCD) = lcd;
    I(lcd, LCD_FILL_COLOR) = 0x12345678;
    if (argc == 2 && !strcmp(argv[1], "--repaint")) {
        repaint_regression();
        return 0;
    }
    assert(coverflow_position_offset(0, 400, 400, 295) == 0);
    assert(coverflow_position_offset(199, 400, 400, 295) == 147);
    assert(coverflow_position_offset(399, 400, 400, 295) == 295);
    assert(coverflow_position_offset(400, 400, 400, 295) == 0);
    assert(coverflow_position_offset(-1, 400, 400, 295) == 295);
    assert(coverflow_position_offset(800, 400, 400, 295) == 0);
    assert(coverflow_position_offset(400, 400, 401, 295) == 295); /* Sort card */
    assert(coverflow_position_offset(401, 400, 401, 295) == 0);
    assert(coverflow_position_offset(-.2f, 400, 401, 295) == 0);     /* artist's albums: bounce at the first */
    assert(coverflow_position_offset(-.4f, 400, 400, 295) == 0);     /* playlists: the rubber band's deepest */
    assert(coverflow_position_offset(400.2f, 400, 401, 295) == 295); /* bounce past the All albums card */
    for (int i = 1; i < 100000; ++i)
        assert(coverflow_position_offset((float)i, 100000, 100000, 295) >=
               coverflow_position_offset((float)(i - 1), 100000, 100000, 295));
    assert(coverflow_position_offset(99999, 100000, 100000, 295) == 295);
    assert(coverflow_position_offset(0, 0, 0, 295) == -1);
    assert(coverflow_position_offset(0, 1, 1, 295) == -1);
    assert(coverflow_position_offset(0, 400, 399, 295) == -1);
    /* Opening the page without browsing leaves the indicator hidden and starts no timer. */
    coverflow_position_paint(slide, canvas);
    coverflow_position_paint(covers, canvas);
    assert(!fills && !position.alpha && !timer_id);
    enabled = 0;
    centre = 100;
    coverflow_position_paint(slide, canvas);
    coverflow_position_paint(covers, canvas);
    assert(!fills && !position.alpha && !timer_id);
    enabled = 1;
    for (int i = 0; i < 400; ++i) paint(i);
    light = 1;
    paint(199);
    light = 0;
    cfs.text_y = 212;
    cfs.top = 13;
    cfs.artist_y = 32;
    paint(199);
    assert(position.r[1] == 277);
    cfs.artist_y = 26;
    cfs.text_y = 220;
    cfs.top = 32;
    I(covers, W_H) = 294;
    paint(199);
    assert(position.r[1] == 280);
    I(covers, W_H) = 290;
    coverflow_position_reset();
    invalidations = 0;
    centre = 0;
    coverflow_position_paint(slide, canvas);
    assert(invalidations == 0 && !position.alpha && !timer_id);
    coverflow_position_paint(slide, canvas);
    assert(invalidations == 0);
    centre = 199;
    coverflow_position_paint(slide, canvas);
    assert(invalidations == 0 && position.r[0] == 107 && position.r[1] == 278 &&
           position.r[2] == 160 && position.r[3] == 8 && position.dirty && timer_id &&
           position.alpha == 255);
    centre = 0;
    th.on = 1;
    th.pos = 199.5f;
    coverflow_position_paint(slide, canvas);
    assert(invalidations == 0 && position.dirty); /* deferred, fractional follower */
    th.pos = 400;
    coverflow_position_paint(slide, canvas);
    assert(invalidations == 0 && position.dirty);
    unsigned started = timer_adds;
    tick(800);
    assert(position.alpha == 255 && timer_id && invalidations == 1 && strip[1] == 278);
    tick(950);
    assert(position.alpha == 127);
    coverflow_position_paint(covers, canvas);
    assert(dot_alpha == 127 * 220 / 255 && fill == 0x12345678);
    /* A tiny movement may leave the dot on the same pixel, but must reveal it again. */
    th.pos = .01f;
    coverflow_position_paint(slide, canvas);
    assert(position.alpha == 255 && timer_adds == started);
    tick(1750);
    assert(position.alpha == 255);
    tick(1900);
    assert(position.alpha == 127);
    tick(2050);
    assert(!position.alpha && !timer_id);
    fills = 0;
    coverflow_position_paint(covers, canvas);
    assert(!fills); /* the final strip invalidation clears it without another shelf repaint */
    th.pos = .02f;
    coverflow_position_paint(slide, canvas);
    assert(position.alpha == 255 && timer_adds == started + 1);
    /* Close/reopen, or go to the flipped panel: no stale timer or retained visibility. */
    coverflow_position_reset();
    assert(!timer_id && !position.alpha);
    coverflow_position_paint(slide, canvas);
    th.pos = 20;
    coverflow_position_paint(slide, canvas);
    fl.panel = covers;
    tick(2100);
    assert(!timer_id && !position.alpha);
    fl.panel = 0;
    /* The unsigned clock subtraction also works across the device's millisecond rollover. */
    coverflow_position_reset();
    clock_ms = 0xfffffff0u;
    coverflow_position_paint(slide, canvas);
    th.pos = 21;
    coverflow_position_paint(slide, canvas);
    tick(0xfffffff0u + 950u);
    assert(position.alpha == 127);
    tick(0xfffffff0u + 1100u);
    assert(!position.alpha && !timer_id);
    fail_timer = 1;
    th.pos = 22;
    coverflow_position_paint(slide, canvas);
    assert(!position.alpha && !timer_id);
    fail_timer = 0;
    th.on = 0;
    centre = 0;
    fraction = CF_ONE / 2;
    assert(coverflow_position_offset(.5f, 2, 2, 295) == 148);
    fills = 0;
    live = 0;
    coverflow_position_paint(covers, canvas);
    assert(!fills);
    live = 1;
    cf.text_albums = 1;
    coverflow_position_paint(covers, canvas);
    assert(!fills);
    cf.text_albums = 0;
    fl.panel = covers;
    coverflow_position_paint(covers, canvas);
    assert(!fills);
    fl.panel = 0;
    albums = 1;
    coverflow_position_paint(covers, canvas);
    assert(!fills);
    albums = 400;
    cfs.text_y = 245;
    coverflow_position_paint(covers, canvas);
    assert(!fills);
    cfs.text_y = 220;
    P(canvas, CANVAS_LCD) = NULL;
    coverflow_position_paint(covers, canvas);
    assert(!fills);
    puts("Cover Flow position: 400 albums, direction, fractional motion, wrap, extra cards, 100000 "
         "albums, cover-aligned track, 9x4 soft-grey thumb, 2px track, coincident centres, footer "
         "midpoint, no contrasting outline, paint bounds, light/dark, fill "
         "restoration, strip invalidation, idle opening, "
         "800ms hold, 300ms fade, resuming during fade, single transient timer, teardown, clock "
         "rollover, timer failure and hidden states passed");
}
