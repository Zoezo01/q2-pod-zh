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
enum { POD_CF_POSITION }; /* peq.h's pod_rows rows: here only Cover Flow's position is read */
static int pod_value(int i) { return i == POD_CF_POSITION && enabled; }
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
/* The scrub's boundary: the covers' view at the screen's origin, the slide's glides recorded. */
static int glides, glide_goal, slide_invalidations, is_lettered = 1, letters;
static unsigned glide_ms, letter_shown;
static int widget_to_local(void *w, void *xy) {
    assert(w == covers);
    return 0;
}
static int stride_of(void *s) { return 100; }
static int carousel_glide(void *w, int goal, unsigned ms, int easing) {
    assert(w == slide && (ms ? easing == SLIDE_EASING : !easing));
    ++glides, glide_goal = goal, glide_ms = ms;
    return ms != 0;
}
static int lettered(void *w) { return w == slide && is_lettered; }
static int coverflow_letter_first(int i, unsigned *letter) {
    *letter = 'A' + (unsigned)i / 100;
    return i - i % 100;
}
static void coverflow_letter(void *w, unsigned letter, unsigned now) {
    assert(w == slide);
    ++letters, letter_shown = letter;
}
static int widget_invalidate_force(void *w, void *r) {
    if (w == slide && !r) {
        ++slide_invalidations; /* a scrub's move at once */
        return 0;
    }
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
        /* It must become drawable DURING the movement, well before the 1000 ms idle hold. */
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
    tick(last + 1150);
    present_strip();
    assert(position.alpha == 127 && dot_alpha == 127 * 220 / 255);
    tick(last + 1300);
    present_strip();
    assert(!fills && !position.alpha && !timer_id);
    clock_ms = last + 1400;
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
/* The scrub: a press in the footer takes the bar, and the covers go to the album under the
 * finger, at a tap's glide a few albums on, farther at once with that album's letter; held, the
 * thumb fills the strip and the bar stays lit; the lift lets go and the hold starts there. */
static void scrub_checks(void) {
    const int left = 107, top = 274, foot = 290, mid = 282; /* text_y 220 + 26 + CF_ARTIST_H */
    /* Off the footer, past the track's reach, or nothing to scrub: the covers keep the press. */
    assert(!coverflow_position_press(left + 80, top - 1));
    assert(!coverflow_position_press(left + 80, foot));
    assert(!coverflow_position_press(left - 13, mid));
    assert(!coverflow_position_press(left + 160 + 12, mid));
    enabled = 0;
    assert(!coverflow_position_press(left + 80, mid));
    enabled = 1, albums = cards = 1;
    assert(!coverflow_position_press(left + 80, mid));
    albums = 400, cards = 399;
    assert(!coverflow_position_press(left + 80, mid));
    cards = 400, live = 0;
    assert(!coverflow_position_press(left + 80, mid));
    live = 1;
    assert(!glides && !timer_id && !position.held && !position.alpha);
    assert(!coverflow_position_touch(EVT_POINTER_MOVE_BEFORE, left + 80, mid)); /* not held */
    /* The press at the track's left reach: album 0, where the covers are, so nothing moves, but
     * the bar lights at once. */
    assert(coverflow_position_press(left - 12, top));
    assert(position.held && position.alpha == 255 && timer_id && !glides);
    /* The far end: the last album, at once, its letter over the covers. */
    assert(coverflow_position_touch(EVT_POINTER_MOVE_BEFORE, left + 159, mid));
    assert(glides == 1 && !glide_ms && glide_goal == -399 * 100 && slide_invalidations == 1);
    assert(letters == 1 && letter_shown == 'A' + 3);
    /* The same album again (past the end, and off the strip's rows) moves nothing. */
    assert(coverflow_position_touch(EVT_POINTER_MOVE_BEFORE, left + 175, foot + 20));
    assert(glides == 1);
    /* Three albums on from where the covers show: a tap's glide, no letter. */
    assert(coverflow_position_touch(EVT_POINTER_MOVE_BEFORE, left + 5, mid));
    assert(glides == 2 && glide_ms == CF_TAP_MS && glide_goal == -3 * 100);
    assert(letters == 1 && slide_invalidations == 1);
    /* Mid-glide, a stride and a half on (album 2 at the centre): back to 0 from there. */
    I(slide, SLIDE_OFFSET) = -150, centre = 2, fraction = -CF_ONE / 2;
    assert(coverflow_position_touch(EVT_POINTER_MOVE_BEFORE, left, mid));
    assert(glides == 3 && glide_ms == CF_TAP_MS && !glide_goal);
    /* Back past album 0 on a ring, album 399 at the centre: the offset's floor, not its
     * truncation, gives the whole album, so the glide finishes onto 399. */
    I(slide, SLIDE_OFFSET) = 130, centre = 399, fraction = -CF_ONE * 3 / 10;
    assert(coverflow_position_touch(EVT_POINTER_MOVE_BEFORE, left + 159, mid));
    assert(glides == 4 && glide_ms == CF_TAP_MS && glide_goal == 100);
    I(slide, SLIDE_OFFSET) = 0, centre = 0, fraction = 0;
    /* A sort without letters moves at once with none. 76 px of 151: album 201 of 400. */
    is_lettered = 0;
    assert(coverflow_position_touch(EVT_POINTER_MOVE_BEFORE, left + 80, mid));
    assert(glides == 5 && !glide_ms && glide_goal == -201 * 100 && letters == 1);
    is_lettered = 1;
    /* Held, the thumb stands the strip's whole height, centred under the finger (76 px of travel
     * at 80 px in), not where the covers show (album 0, the stub's: x 0). */
    coverflow_position_paint(slide, canvas);
    fills = 0;
    memset(pixels, 0, sizeof pixels);
    coverflow_position_paint(covers, canvas);
    unsigned grey = 220u << 24 | 0xaaaaaa;
    assert(fills == 9 && fill == 0x12345678 && position.x == 76);
    for (int j = -4; j <= 3; ++j) assert(pixels[(mid + j) * 375 + left + 80] == grey);
    assert(!pixels[(mid - 5) * 375 + left + 80] && !pixels[(mid - 3) * 375 + left + 80 - 5]);
    assert(pixels[(mid - 4) * 375 + left + 80 - 3] == grey &&
           pixels[mid * 375 + left + 80 + 4] == grey);
    /* Past an end, the thumb stops at it. */
    assert(coverflow_position_touch(EVT_POINTER_MOVE_BEFORE, left + 200, mid) &&
           position.finger == 151);
    assert(coverflow_position_touch(EVT_POINTER_MOVE_BEFORE, left - 30, mid) && !position.finger);
    assert(coverflow_position_touch(EVT_POINTER_MOVE_BEFORE, left + 80, mid) &&
           position.finger == 76);
    int moves = glides;
    /* Held, it stays lit however long the finger rests; the lift asks for the covers' still frame
     * (every cover a scrub's budget left loads) and starts the bar's hold. */
    tick(10000);
    assert(position.alpha == 255);
    int asked = slide_invalidations;
    assert(coverflow_position_touch(EVT_POINTER_UP_BEFORE, left, mid) && !position.held);
    assert(slide_invalidations == asked + 1 && glides == moves);
    tick(11000);
    assert(position.alpha == 255);
    tick(11150);
    assert(position.alpha == 127);
    tick(11300);
    assert(!position.alpha && !timer_id);
    assert(!coverflow_position_touch(EVT_POINTER_MOVE_BEFORE, left, mid) && glides == moves);
    /* An abort lets go too, and so does a key with no finger seen (coverflow_lost_touch). */
    assert(coverflow_position_press(left + 80, mid) && glides == moves + 1);
    assert(coverflow_position_touch(EVT_POINTER_ABORT, 0, 0) && !position.held);
    assert(coverflow_position_press(left + 81, mid));
    coverflow_position_lift();
    assert(!position.held);
    /* The covers gone mid-scrub (a load, the panel): the next move lets go and moves nothing. */
    assert(coverflow_position_press(left + 79, mid));
    int moved = glides;
    live = 0;
    assert(coverflow_position_touch(EVT_POINTER_MOVE_BEFORE, left + 150, mid));
    assert(!position.held && glides == moved);
    live = 1;
    /* The page's teardown lets go as well. */
    assert(coverflow_position_press(left + 82, mid));
    coverflow_position_reset();
    assert(!position.held && !timer_id);
    /* The album under the thumb, the offset's inverse: exact where albums are fewer than pixels,
     * in order and held to the ends however many. */
    for (int n = 2; n <= 151; ++n)
        for (int a = 0; a < n; ++a)
            assert(coverflow_position_album(coverflow_position_offset((float)a, n, n, 151), n,
                                            151) == a);
    for (int x = 1; x <= 151; ++x)
        assert(coverflow_position_album(x, 100000, 151) >=
               coverflow_position_album(x - 1, 100000, 151));
    assert(coverflow_position_album(-20, 400, 151) == 0);
    assert(coverflow_position_album(400, 400, 151) == 399);
    assert(!coverflow_position_album(5, 1, 151) && !coverflow_position_album(5, 400, 0));
    puts("Cover Flow scrub: footer reach, setting and album gates, press, far move at once with "
         "its letter, near glide, mid-glide and ring floors, letterless sorts, held thumb, held "
         "and lifted fade, abort, lost touch, covers gone, teardown and the offset's inverse "
         "passed");
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
    if (argc == 2 && !strcmp(argv[1], "--scrub")) {
        scrub_checks();
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
    assert(coverflow_position_offset(-.2f, 400, 401, 295) ==
           0); /* artist's albums: bounce at the first */
    assert(coverflow_position_offset(-.4f, 400, 400, 295) ==
           0); /* playlists: the rubber band's deepest */
    assert(coverflow_position_offset(400.2f, 400, 401, 295) ==
           295); /* bounce past the All albums card */
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
    tick(1150);
    assert(position.alpha == 127);
    coverflow_position_paint(covers, canvas);
    assert(dot_alpha == 127 * 220 / 255 && fill == 0x12345678);
    /* A tiny movement may leave the dot on the same pixel, but must reveal it again. */
    th.pos = .01f;
    coverflow_position_paint(slide, canvas);
    assert(position.alpha == 255 && timer_adds == started);
    tick(2150);
    assert(position.alpha == 255);
    tick(2300);
    assert(position.alpha == 127);
    tick(2450);
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
    tick(2500);
    assert(!timer_id && !position.alpha);
    fl.panel = 0;
    /* The unsigned clock subtraction also works across the device's millisecond rollover. */
    coverflow_position_reset();
    clock_ms = 0xfffffff0u;
    coverflow_position_paint(slide, canvas);
    th.pos = 21;
    coverflow_position_paint(slide, canvas);
    tick(0xfffffff0u + 1150u);
    assert(position.alpha == 127);
    tick(0xfffffff0u + 1300u);
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
    P(canvas, CANVAS_LCD) = lcd;
    albums = cards = 2, centre = 0,
    fraction = CF_ONE / 4; /* a touch drag a quarter past the first */
    coverflow_position_paint(slide, canvas);
    assert(position.x ==
           38); /* .25 of the 151 px travel: a painter without the touch term gives 0 */
    puts("Cover Flow position: 400 albums, direction, fractional motion, wrap, extra cards, 100000 "
         "albums, cover-aligned track, 9x4 soft-grey thumb, 2px track, coincident centres, footer "
         "midpoint, no contrasting outline, paint bounds, light/dark, fill "
         "restoration, strip invalidation, idle opening, "
         "1000ms hold, 300ms fade, resuming during fade, single transient timer, teardown, clock "
         "rollover, timer failure and hidden states passed");
}
