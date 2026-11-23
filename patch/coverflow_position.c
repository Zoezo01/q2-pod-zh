/* Cover Flow's browsing position, independent of playback and duration scanning. */
#include "coverflow.h"

#define POSITION_THUMB_W 9
#define POSITION_THUMB_H 4
#define POSITION_STRIP_H 8
#define POSITION_HOLD_MS 1000
#define POSITION_FADE_MS 300
#define POSITION_TICK_MS 33
#define POSITION_REACH 12 /* px past each end of the track that still take the bar */

static struct {
    widget_t *parent, *slide;
    float pos;
    int x, r[4], dirty;
    unsigned moved, timer, alpha;
    int held, album; /* a finger scrubbing the bar, and the album it last sent the covers to */
    int finger;      /* held: the thumb's x along the travel, under the finger, not the covers' */
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
           pod_value(POD_CF_POSITION);
}

/* A single temporary UI timer finishes the fade even when nothing else repaints the shelf.
 * It stops after the final clearing repaint, or as soon as its page is no longer live. */
static int position_tick(const void *unused) {
    if (position.held && !window_manager_get_pointer_pressed(window_manager()))
        coverflow_position_lift(); /* its release went elsewhere */
    if (!position_live() || position.parent != cf.covers || position.slide != cf.slide) {
        position.timer = position.alpha = 0;
        return 0;
    }
    if (position.held) position.moved = (unsigned)time_now_ms(); /* its hold starts at the lift */
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

/* The album whose thumb is x px along the track's travel, of albums: coverflow_position_offset's
 * inverse, held to the first and last album, the extra cards never. */
static int coverflow_position_album(int x, int albums, int travel) {
    if (albums <= 1 || travel <= 0 || x <= 0) return 0;
    if (x >= travel) return albums - 1;
    return (int)((float)x * (float)(albums - 1) / (float)travel + .5f);
}

/* Where the bar goes in the covers' view, at rest pose(0)'s central cover's ART_SIZE about CF_CX:
 * the track's left edge, the last caption's lower edge, and the strip's middle row, halfway from
 * there to the view's foot. 0 where the footer has no room for the strip. */
static int position_place(int *left, int *bottom, int *mid) {
    int height = I(cf.covers, W_H);
    *left = (I(cf.covers, W_W) - ART_SIZE) / 2;
    *bottom = cfs.text_y + cfs.artist_y + CF_ARTIST_H;
    *mid = (*bottom + height) / 2;
    int y = *mid - POSITION_STRIP_H / 2;
    return *left >= 0 && height - *bottom >= POSITION_STRIP_H && y >= *bottom &&
           y + POSITION_STRIP_H <= height;
}

/* The shelf invalidates only its own height. A changed position or opacity asks for the small
 * footer strip too. Queue damage on the UI timer, not from inside a paint: toolkit
 * paint cleanup can otherwise consume the new damage before it reaches the screen. */
void coverflow_position_paint(widget_t *w, canvas_t *canvas) {
    if (!w || (w != cf.slide && w != cf.covers) || !position_live()) return;
    int n = (int)deque_size(cf.albums), cards = (int)widget_count_children(cf.slide), c, frac;
    if (!visual(cf.slide, cards, &c, &frac)) return;
    int width = ART_SIZE, left, bottom, mid;
    if (!position_place(&left, &bottom, &mid)) {
        position_reset(0);
        return;
    }
    int y = mid - POSITION_STRIP_H / 2;
    float pos =
        th.on ? (float)I(cf.slide, SLIDE_INDEX) + th.pos : (float)c + (float)frac / (float)CF_ONE;
    int x = coverflow_position_offset(pos, n, cards, width - POSITION_THUMB_W);
    if (x < 0) {
        position_reset(0);
        return; /* no useful position for an empty list or a single album */
    }
    if (position.held)
        x = position.finger; /* under the finger, however far behind the covers are */
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
    /* A short flat grey pill, softened against the paper without a contrasting outline; held, it
     * stands the strip's whole height, so the finger's bar shows it has it. */
    int h = position.held ? POSITION_STRIP_H : POSITION_THUMB_H;
    int tx = left + x, ty = mid - h / 2;
    unsigned ink = ipod_light() ? 0x808080 : 0xaaaaaa, alpha = position.alpha * 220 / 255;
    stock_canvas_set_fill_color(canvas, (RGBA(ink) & 0xffffffu) | alpha << 24);
    for (int j = 0; j < h; ++j) {
        int inset = h > 2 && (j == 0 || j == h - 1);
        canvas_fill_rect(canvas, tx + inset, ty + j, POSITION_THUMB_W - 2 * inset, 1);
    }
    stock_canvas_set_fill_color(canvas, fill);
}

/* Touch (docs/internals.md#cover-flow-browsing-position). A press in the footer, between the last
 * caption and the view's foot and across the track's width, POSITION_REACH past each end, takes
 * the bar: the covers go to the album under the finger and follow it, a few albums at a tap's
 * glide and farther at once, as coverflow_tail moves them, the album's letter over them while the
 * sort has letters. The lift leaves them there. Only with Position Bar on and two albums or more;
 * coverflow.c's touch() asks here first, after its own checks (the covers live, no thumb). */

/* Lit from now, its hold starting again: the press, the lift. */
static void position_wake(void) {
    position.moved = (unsigned)time_now_ms(), position.dirty = 1, position.alpha = 255;
    if (!position.timer && !(position.timer = timer_add(position_tick, 0, POSITION_TICK_MS)))
        position.alpha = 0; /* a failed timer must not leave an indicator stuck onscreen */
}

/* The covers on album t, from wherever they show now: d albums on from the centre's, the goal
 * taken from the live offset's whole albums, so a glide in progress or a wrapped ring goes the
 * short way to it. */
static void position_seek(int t) {
    void *s = cf.slide;
    int stride = stride_of(s), cards = (int)widget_count_children(s), c, frac;
    if (t == position.album || stride <= 0 || !visual(s, cards, &c, &frac)) return;
    position.album = t;
    if (t == c && !frac && !P(s, SLIDE_ANIMATOR)) return; /* there already, and not gliding away */
    int d = t - c, off = -I(s, SLIDE_OFFSET), q = (2 * off + stride) / (2 * stride);
    if ((2 * off + stride) % (2 * stride) < 0) --q; /* visual()'s floor */
    if (d >= -TAIL_GLIDE && d <= TAIL_GLIDE) {
        carousel_glide(s, -(q + d) * stride, CF_TAP_MS, SLIDE_EASING);
        return;
    }
    carousel_glide(s, -(q + d) * stride, 0, 0); /* at once: the index moves, its offset 0 */
    widget_invalidate_force(s, 0);
    if (lettered(s)) coverflow_letter(s, coverflow_group(t), (unsigned)time_now_ms());
}

/* The finger x px from the track's left edge: the thumb centred there, held to the track, at once,
 * and the covers to its album. */
static void position_scrub(int x) {
    int n = (int)deque_size(cf.albums), travel = ART_SIZE - POSITION_THUMB_W;
    int f = x - POSITION_THUMB_W / 2;
    f = f < 0 ? 0 : f > travel ? travel : f;
    if (f != position.finger) position.finger = f, position.dirty = 1;
    position_seek(coverflow_position_album(f, n, travel));
}

/* A press at (x, y) on the screen: answers whether the bar took it. */
int coverflow_position_press(int x, int y) {
    int left, bottom, mid, xy[2] = { x, y };
    if (!position_live() || !cf.albums || !position_place(&left, &bottom, &mid)) return 0;
    int n = (int)deque_size(cf.albums), cards = (int)widget_count_children(cf.slide);
    if (n <= 1 || cards < n) return 0;
    widget_to_local(cf.covers, xy);
    if (xy[1] < bottom || xy[1] >= I(cf.covers, W_H) || xy[0] < left - POSITION_REACH ||
        xy[0] >= left + ART_SIZE + POSITION_REACH)
        return 0;
    if (position.parent != cf.covers || position.slide != cf.slide) {
        position_reset(0);
        position.parent = cf.covers, position.slide = cf.slide;
    }
    position.r[0] = left, position.r[1] = mid - POSITION_STRIP_H / 2;
    position.r[2] = ART_SIZE, position.r[3] = POSITION_STRIP_H;
    position.held = 1, position.album = -1;
    position_wake();
    position_scrub(xy[0] - left);
    return 1;
}

/* The finger off the bar, its release seen or not (coverflow_lost_touch): the covers stay where
 * the scrub sent them, their still frame asked for (it loads every cover the scrub's budget left
 * as the placeholder), and the bar holds and fades from here. */
void coverflow_position_lift(void) {
    if (!position.held) return;
    position.held = 0;
    if (!position_live() || position.parent != cf.covers || position.slide != cf.slide) return;
    widget_invalidate_force(cf.slide, 0);
    position_wake();
}

int coverflow_position_held(void) { return position.held; }

/* The rest of a press the bar took (the page's moves and release): a move scrubs, a release or an
 * abort lets go. Answers whether the event was the bar's. */
int coverflow_position_touch(int type, int x, int y) {
    if (!position.held) return 0;
    if (type == EVT_POINTER_UP_BEFORE || type == EVT_POINTER_ABORT) {
        coverflow_position_lift();
        return 1;
    }
    if (type != EVT_POINTER_MOVE_BEFORE) return 0;
    if (!position_live() || position.parent != cf.covers || position.slide != cf.slide) {
        coverflow_position_lift(); /* the covers went (a load, the panel): nothing to scrub */
        return 1;
    }
    int xy[2] = { x, y };
    widget_to_local(cf.covers, xy);
    position_scrub(xy[0] - position.r[0]);
    return 1;
}
