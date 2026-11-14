/* Cover Flow's browsing position, independent of playback and duration scanning. */
#include "coverflow.h"

#define POSITION_THUMB_W 9
#define POSITION_THUMB_H 4
#define POSITION_STRIP_H 8
#define POSITION_HOLD_MS 800
#define POSITION_FADE_MS 300
#define POSITION_TICK_MS 33

static struct {
    void *parent, *slide;
    float pos;
    int x, r[4], dirty;
    unsigned moved, timer, alpha;
} position;

static void position_reset(int redraw) {
    stop_timer(&position.timer);
    if (redraw && position.parent == cf.covers && position.slide == cf.slide && position.r[2])
        widget_invalidate_force(position.parent, position.r);
    memset(&position, 0, sizeof position);
}
void coverflow_position_reset(void) { position_reset(1); }

static int position_live(void) {
    return cf.covers && cf.slide && covers_live() && !cf.text_albums && !fl.panel &&
           coverflow_position_enabled();
}

/* A single temporary UI timer finishes the fade even when nothing else repaints the shelf.
 * It stops after the final clearing repaint, or as soon as its page is no longer live. */
static int position_tick(const void *unused) {
    if (!position_live() || position.parent != cf.covers || position.slide != cf.slide) {
        position.timer = position.alpha = 0;
        return 0;
    }
    unsigned age = (unsigned)time_now_ms() - position.moved;
    unsigned alpha = age <= POSITION_HOLD_MS ? 255
                     : age < POSITION_HOLD_MS + POSITION_FADE_MS
                         ? 255 * (POSITION_HOLD_MS + POSITION_FADE_MS - age) / POSITION_FADE_MS
                         : 0;
    if (position.dirty || alpha != position.alpha) {
        position.alpha = alpha;
        position.dirty = 0;
        widget_invalidate_force(position.parent, position.r);
    }
    if (!alpha) {
        position.timer = 0;
        return 0; /* RET_OK: any return but RET_REPEAT removes the timer */
    }
    return 8; /* RET_REPEAT */
}

/* Pixel offset of the thumb's left edge. Wrap in the slide's order first, then hold the last
 * album's position on an extra Sort/All Albums card. Float arithmetic avoids a fixed-point
 * album count overflowing on a large library. */
static int coverflow_position_offset(float pos, int albums, int cards, int travel) {
    if (albums <= 1 || cards < albums || travel < 0) return -1;
    pos -= (float)cf_floor((pos + .5f) / (float)cards) * (float)cards; /* where visual() wraps */
    if (pos < 0) pos = 0; /* a bounded list's bounce or rubber band past its first album */
    if (pos > (float)(albums - 1)) pos = (float)(albums - 1);
    return (int)(pos * (float)travel / (float)(albums - 1) + .5f);
}

/* The shelf invalidates only its own height. A changed position or opacity asks for the small
 * footer strip too. Queue damage on the UI timer, not from inside a paint: toolkit
 * paint cleanup can otherwise consume the new damage before it reaches the screen. */
void coverflow_position_paint(void *w, void *canvas) {
    if (!w || (w != cf.slide && w != cf.covers) || !position_live()) return;
    int n = (int)deque_size(cf.albums), cards = (int)widget_count_children(cf.slide), c, frac;
    if (!visual(cf.slide, cards, &c, &frac)) return;
    /* At rest, pose(0) projects the central cover to ART_SIZE pixels about CF_CX. Match those
     * edges; place the indicator halfway from the last caption's lower edge to the screen. */
    int width = ART_SIZE, left = (I(cf.covers, W_W) - width) / 2;
    int bottom = cfs.text_y + cfs.artist_y + CF_ARTIST_H, height = I(cf.covers, W_H);
    int mid = (bottom + height) / 2, y = mid - POSITION_STRIP_H / 2;
    if (left < 0 || height - bottom < POSITION_STRIP_H || y < bottom ||
        y + POSITION_STRIP_H > height) {
        position_reset(0);
        return;
    }
    float pos =
        th.on ? (float)I(cf.slide, SLIDE_INDEX) + th.pos : (float)c + (float)frac / (float)CF_ONE;
    int x = coverflow_position_offset(pos, n, cards, width - POSITION_THUMB_W);
    if (x < 0) {
        position_reset(0);
        return; /* no useful position for an empty list or a single album */
    }
    if (w == cf.slide) {
        if (position.parent != cf.covers || position.slide != cf.slide) {
            position_reset(0);
            position.parent = cf.covers, position.slide = cf.slide, position.pos = pos;
        }
        position.r[0] = left, position.r[1] = y;
        position.r[2] = width, position.r[3] = POSITION_STRIP_H;
        if (pos != position.pos) {
            unsigned old_alpha = position.alpha;
            position.pos = pos, position.moved = (unsigned)time_now_ms(), position.alpha = 255;
            if (!position.timer &&
                !(position.timer = timer_add(position_tick, 0, POSITION_TICK_MS)))
                position.alpha = 0; /* a failed timer must not leave an indicator stuck onscreen */
            if (position.x != x || old_alpha != position.alpha) position.dirty = 1;
        }
        position.x = x;
        return;
    }
    if (!position.alpha || position.parent != cf.covers || position.slide != cf.slide) return;
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    stock_canvas_set_fill_color(canvas, (RGBA(ipod_light() ? 0xb4b9c0 : 0x444444) & 0xffffffu) |
                                            position.alpha << 24);
    canvas_fill_rect(canvas, left, mid - 1, width, 2);
    /* A short flat grey pill, softened against the paper without a contrasting outline. */
    int tx = left + x, ty = mid - POSITION_THUMB_H / 2;
    unsigned ink = ipod_light() ? 0x808080 : 0xaaaaaa, alpha = position.alpha * 220 / 255;
    stock_canvas_set_fill_color(canvas, (RGBA(ink) & 0xffffffu) | alpha << 24);
    for (int j = 0; j < POSITION_THUMB_H; ++j) {
        int inset = POSITION_THUMB_H > 2 && (j == 0 || j == POSITION_THUMB_H - 1);
        canvas_fill_rect(canvas, tx + inset, ty + j, POSITION_THUMB_W - 2 * inset, 1);
    }
    stock_canvas_set_fill_color(canvas, fill);
}
