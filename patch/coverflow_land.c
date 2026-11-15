/* Classic's landing: a song picked on Cover Flow's flip panel turns the panel over and lands it as
 * Now Playing's art (docs/internals.md, the flip). The path and the frames, then the lifecycle from
 * the click (coverflow.c play) to the hand-over to Now Playing's own art. */
#include "coverflow.h"

/* LAND_LEAN is the turn at which the flip's own projection gives slant_render's far edge,
 * asin(FLIP_EYE (1 - f) / (1 + f) / (NP_IPOD_ART / 2)) degrees, f = SLANT_FAR / 256; it and its
 * sine and cosine are constants, as the payload has sinf and cosf but no asin. */
#define LAND_LEAN 31.624f
#define LAND_LEAN_SIN .52434457f
#define LAND_LEAN_COS .85150618f

/* Cubic ease-out, x clamped to 0 to 1. */
float land_ease(float x) {
    x = x < 0 ? 0 : x > 1 ? 1 : x;
    return 1 - (1 - x) * (1 - x) * (1 - x);
}

/* A face w x h about centre (cx, cy), turned deg (as sine sn, cosine cs) about its vertical axis,
 * seen from FLIP_EYE: its TL, TR, BR, BL into q, the left edge at -w/2 of the face's own. */
static void land_face(float cx, float cy, float w, float h, float sn, float cs, float q[8]) {
    float xs = cs * w / 2, zs = sn * w / 2, kl = FLIP_EYE / (FLIP_EYE + zs),
          kr = FLIP_EYE / (FLIP_EYE - zs);
    q[0] = cx - xs * kl, q[1] = cy - h / 2 * kl, q[2] = cx + xs * kr, q[3] = cy - h / 2 * kr;
    q[4] = q[2], q[5] = cy + h / 2 * kr, q[6] = q[0], q[7] = cy + h / 2 * kl;
}

/* The landing at ms of len: the front face's TL, TR, BR, BL into q, its turn into *deg. from is the
 * panel's box (x, y, w, h), to the art's (x, y, side). The centre travels by travel percent of the
 * time; size and turn ease over all of it, from 180 (the back, as the panel shows it) on the flip
 * open's way round to 360 - LAND_LEAN. The same turn at the art's place and size falls short of
 * slant_render's keystone by a little, so each corner gains that shortfall in step with the turn,
 * and lands on it. test/coverflow.py */
void land_quad(const int from[4], const int to[3], int ms, int len, int travel, float q[8],
               float *deg) {
    float x = (float)ms / len, m = land_ease(x * 100 / travel), u = land_ease(x), s = to[2],
          i = s * (256 - SLANT_FAR) / 512, end[8], d = 180 + (180 - LAND_LEAN) * u;
    float ax = to[0] + s / 2, ay = to[1] + s / 2, px = from[0] + from[2] / 2.f,
          py = from[1] + from[3] / 2.f, rad = d * 3.14159265f / 180;
    const float ks[8] = { to[0],     to[1],         to[0] + s, to[1] + i,
                          to[0] + s, to[1] + s - i, to[0],     to[1] + s };
    land_face(px + (ax - px) * m, py + (ay - py) * m, from[2] + (s - from[2]) * u,
              from[3] + (s - from[3]) * u, sinf(rad), cosf(rad), q);
    land_face(ax, ay, s, s, -LAND_LEAN_SIN, LAND_LEAN_COS, end);
    for (int j = 0; j < 8; ++j) q[j] += (ks[j] - end[j]) * u;
    *deg = d;
}

/* The landing's frame at ms into d (CF_VIEW_W x FLIP_H). Before np_at, 1, opaque, all of it: the
 * paper, the shelf parted as the flip leaves it and faded to the paper from out_at over out_ms, the
 * face and its reflection. Then 0: in box only the face and its reflection, over alpha 0 (straight
 * alpha); *veil the paper's alpha over Now Playing as it comes in over np_ms. From len, 2:
 * slant_render's art at to, box its rect, opaque. The face as coverflow_flip_render draws it, a
 * column at a time in 1/256 px: its left and right edges are upright, so each column's top and
 * bottom lie on the lines between them, u by one divide (u h is linear across, h being 1 / depth),
 * nearest texel, coverage at the ends and edges. The texture's first column is on the screen's
 * left for either face (the back's left is the front's right). The reflection, the front's only,
 * mirrors each column about its bottom, as slant_render's, its alpha growing with the turn to
 * slant_render's at the lean. test/coverflow.py */
int land_frame(unsigned *d, int pitch, int ms, const land_t *L, int box[4], int *veil) {
    int np = ms - L->np_at, opaque = np < 0, side = L->to[2];
    *veil = opaque ? 255 : np >= L->np_ms ? 0 : 255 - 255 * np / L->np_ms;
    if (ms >= L->len) {
        box[0] = L->to[0], box[1] = L->to[1], box[2] = side;
        box[3] = side + (side * SLANT_REFLECT_PCT + 99) / 100;
        slant_render(d + box[1] * pitch + box[0], pitch, side, box[3], L->front, ART_SIZE,
                     L->paper & 255);
        return 2;
    }
    float q[8], deg;
    land_quad(L->from, L->to, ms, L->len, L->travel, q, &deg);
    float cs = cosf(deg * 3.14159265f / 180);
    int back = cs < 0, i = back ? 2 : 0, j = 2 - i, aw = back ? BACK_W : ART_SIZE;
    int ah = back ? BACK_H : ART_SIZE,
        ra = cs <= 0 ? 0 : (int)(SLANT_REFLECT_A * cs / LAND_LEAN_COS);
    ra = ra > SLANT_REFLECT_A ? SLANT_REFLECT_A : ra;
    /* the screen's left edge (x0, its top t0, bottom b0) and right (x1, t1, b1), 1/256 px */
    int x0 = (int)(q[i] * 256 + .5f), t0 = (int)(q[i + 1] * 256 + .5f),
        b0 = (int)(q[7 - i] * 256 + .5f), x1 = (int)(q[j] * 256 + .5f),
        t1 = (int)(q[j + 1] * 256 + .5f), b1 = (int)(q[7 - j] * 256 + .5f), w = x1 - x0;
    const unsigned *t = back ? L->back : L->front;
    int lo = t0 < t1 ? t0 : t1, hi = b0 + (ra ? (b0 - t0) * SLANT_REFLECT_PCT / 100 : 0),
        hr = b1 + (ra ? (b1 - t1) * SLANT_REFLECT_PCT / 100 : 0);
    hi = hi > hr ? hi : hr;
    box[0] = x0 >> 8, box[1] = lo >> 8, box[2] = (x1 + 255) >> 8, box[3] = (hi + 255) >> 8;
    box[0] = box[0] < 0 ? 0 : box[0] > CF_VIEW_W ? CF_VIEW_W : box[0];
    box[1] = box[1] < 0 ? 0 : box[1] > FLIP_H ? FLIP_H : box[1];
    box[2] = (box[2] > CF_VIEW_W ? CF_VIEW_W : box[2]) - box[0];
    box[3] = (box[3] > FLIP_H ? FLIP_H : box[3]) - box[1];
    box[2] = box[2] < 0 ? 0 : box[2], box[3] = box[3] < 0 ? 0 : box[3]; /* off the frame, edge on */
    if (opaque) {
        int f = ms <= L->out_at               ? 0
                : ms >= L->out_at + L->out_ms ? 256
                                              : 256 * (ms - L->out_at) / L->out_ms;
        for (int y = 0; y < FLIP_H; ++y) {
            unsigned *o = d + y * pitch;
            for (int x = 0; x < CF_VIEW_W; ++x) {
                int at = x < CF_CX ? x + FLIP_PART : x - FLIP_PART; /* parted */
                o[x] =
                    L->shelf && y < cfs.view_h && (x < CF_CX - FLIP_PART || x >= CF_CX + FLIP_PART)
                        ? tilt_mix(L->shelf[y * CF_VIEW_W + at], L->paper, (unsigned)f) |
                              0xff000000u
                        : L->paper;
            }
        }
    } else
        for (int y = box[1]; y < box[1] + box[3]; ++y)
            memset(d + y * pitch + box[0], 0, (unsigned)box[2] * 4);
    if (w < 16 || !t) return opaque; /* edge on */
    for (int x = box[0]; x < box[0] + box[2]; ++x) {
        int a = x << 8 > x0 ? x << 8 : x0, b = (x + 1) << 8 < x1 ? (x + 1) << 8 : x1, cx = b - a;
        int s = (((a + b) >> 1) - x0) * 4096 / w; /* across, 12 bits */
        int y0 = t0 + ((t1 - t0) * s >> 12), y1 = b0 + ((b1 - b0) * s >> 12), h = y1 - y0;
        if (cx <= 0 || h < 256) continue;
        int col = (s * ((b1 - t1) >> 4) / (h >> 4)) * aw >> 12, step = (ah << 20) / (h >> 4);
        int fade = h * SLANT_REFLECT_PCT / 100, end = ra ? y1 + fade : y1;
        col = col < aw ? col : aw - 1;
        int y = y0 >> 8 < 0 ? 0 : y0 >> 8, last = (end - 1) >> 8;
        for (; y <= last && y < FLIP_H; ++y) {
            int p = y << 8, f0 = p > y0 ? p : y0, f1 = p + 256 < y1 ? p + 256 : y1;
            int r0 = p > y1 ? p : y1, r1 = p + 256 < end ? p + 256 : end, wf = 0, wr = 0;
            unsigned fc = 0, rc = 0, *o = d + y * pitch + x;
            if (f1 > f0) { /* the face's, its rows a step apart */
                int v = ((p + 128 - y0) >> 4) * step >> 20;
                fc = t[(v < 0 ? 0 : v < ah ? v : ah - 1) * aw + col];
                wf = (f1 - f0) * cx >> 8;
            }
            if (r1 > r0) { /* the reflection's, as far under the bottom as the texel is above it */
                int v = ((p + 128 - y1) >> 4) * step >> 20;
                rc = t[(ah - 1 - (v < 0 ? 0 : v < ah ? v : ah - 1)) * aw + col];
                wr = (r1 - r0) * (ra * (fade - ((r0 + r1) >> 1) + y1) / fade) >> 8;
                wr = wr * cx >> 8;
            }
            if (opaque)
                *o = (shade(*o, (unsigned)(256 - wf - wr)) + shade(fc, (unsigned)wf) +
                      shade(rc, (unsigned)wr)) |
                     0xff000000u;
            else if (wf + wr) /* straight alpha: the colour, its share */
                *o = ((wr ? wf ? tilt_mix(fc, rc, (unsigned)(wr * 256 / (wf + wr))) : rc : fc) &
                      0xffffffu) |
                     (unsigned)(wf + wr > 255 ? 255 : wf + wr) << 24;
        }
    }
    return opaque;
}

/* The landing's state (land_start to land_end): the picked row, then Now Playing's window and
 * art; the frame, the panel's back and the art (the screen's BGRA); top the window under the press,
 * at the time Now Playing opened, t0 the first paint's (slid on by a late tick); ms the frame
 * rendered (-1 before the first paint), got and veil land_frame's, box its rect; path the song's
 * (song_key). */
static struct {
    land_t L;
    void *win, *art, *frame, *top;
    unsigned *back, *front;
    unsigned timer, at, t0, path;
    int row, ms, got, veil, box[4];
} land __attribute__((section(".scratch")));

int coverflow_landing(void) { return land.front != 0; }

/* A song's key: its path's FNV and its CUE start, as a CUE image's tracks share one path. */
static unsigned song_key(void *r) {
    return hash_bytes(fnv(FNV_SEED, P(r, REC_PATH)), (const unsigned char *)r + REC_CUE_START, 4);
}

/* From the click until the motion ends (coverflow_turning). */
int land_turning(void) {
    return land.front && (land.ms < 0 || (unsigned)time_now_ms() - land.t0 < (unsigned)land.L.len);
}

/* The landing's lifecycle (docs/internals.md, the flip). The click lights the row and keeps the
 * panel standing for land_press ms, its back captured and its art turned to the screen's BGRA; the
 * press opens Now Playing (coverflow_play), and over that window, from its first paint, the frames
 * run for land_ms; then the landed art stays over Now Playing's own until that is this song's
 * (np_art_ready), or for land_hold ms at most. Anything else ends it at once, Now Playing then
 * shown as it is: no first paint within LAND_FIRST_PAINT_MS of the open, another window on top,
 * another song, a touch on it, the wheel or Return (ringnav, land_key). */
extern int np_layout(void), np_art_ready(void);
extern void ringnav_light(void *list, int index), drop_held(void);
#define LAND_HOLD_TICK_MS 50
#define LAND_FIRST_PAINT_MS 2000 /* open to Now Playing's first paint; about 700 ms on the Q2 */

static void land_end(void) {
    stop_timer(&land.timer);
    if (land.frame) bitmap_destroy(land.frame);
    free(land.back);
    free(land.front);
    memset(&land, 0, sizeof land);
}

/* The frame at ms into land.frame, opaque as land_frame says. */
static void land_render(int ms) {
    unsigned *d = (unsigned *)bitmap_lock_buffer_for_write(land.frame);
    land.L.shelf = (const unsigned *)bitmap_lock_buffer_for_read(fx.frame);
    land.L.back = land.back, land.L.front = land.front;
    land.L.paper = bgra(ipod_light() ? scheme()->paper : 0);
    land.got = land_frame(d, (int)(bitmap_get_line_length(land.frame) / 4), ms, &land.L, land.box,
                          &land.veil);
    bitmap_unlock_buffer(fx.frame);
    bitmap_unlock_buffer(land.frame);
    unsigned short *flags = (unsigned short *)((char *)land.frame + 0xc); /* BITMAP_FLAG_OPAQUE */
    *flags = land.got ? *flags | 1 : *flags & ~1u;
    land.ms = ms;
}

/* Over Now Playing's window (ringnav_paint, after its children): the first paint starts the clock.
 * Opaque, the whole frame; else, in the motion, the paper's veil and the face's box. The art's box
 * is land_art's, under the page dots. */
void land_paint(void *w, void *canvas) {
    if (!w || w != land.win) return;
    if (land.ms < 0) {
        land.t0 = (unsigned)time_now_ms();
        land_render(0);
    }
    int all[4] = { 0, 0, CF_VIEW_W, FLIP_H };
    if (land.got == 1) {
        canvas_draw_image(canvas, land.frame, all, all);
        return;
    }
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    unsigned paper = ipod_light() ? scheme()->paper : 0;
    if (!land.got && land.veil) {
        stock_canvas_set_fill_color(canvas, paper | (unsigned)land.veil << 24);
        canvas_fill_rect(canvas, 0, 0, CF_VIEW_W, FLIP_H);
    }
    if (!land.got) canvas_draw_image(canvas, land.frame, land.box, land.box);
    stock_canvas_set_fill_color(canvas, fill);
}

/* Now Playing's art under the landing (coverflow_slant, its own paint, so what Now Playing paints
 * after it, the page dots, stays on top): the paper, so its own art does not show, and once landed
 * the landed frame, at the art's place. */
void land_art(void *w, void *canvas) {
    if (!w || w != land.art) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    stock_canvas_set_fill_color(canvas, (ipod_light() ? scheme()->paper : 0) | 0xff000000u);
    canvas_fill_rect(canvas, 0, 0, I(w, W_W), I(w, W_H));
    stock_canvas_set_fill_color(canvas, fill);
    int at[4] = { 0, 0, land.box[2], land.box[3] };
    if (land.got == 2) canvas_draw_image(canvas, land.frame, land.box, at);
}

/* Now Playing's window going: the landing with it, the covers drawn again under it. */
static int land_gone(void *ctx, void *event) {
    if (ctx != land.win) return 0;
    land_end();
    widget_invalidate_force(cf.covers, 0);
    return 0;
}

/* Each FLIP_TICK_MS of the motion: the frame at the clock's ms (a tick more than two late renders
 * one on from the last frame and slides t0 with it, so a slow frame slows the motion), the window
 * (while the frame is opaque or veiled, or was) or the face's old and new boxes invalidated; the
 * back freed once the front faces the eye. Then every LAND_HOLD_TICK_MS of the hold. */
static int land_tick(const void *unused) {
    void *wm = window_manager(), *r, *top = window_manager_get_top_window(wm);
    unsigned now = (unsigned)time_now_ms(), pos, n;
    /* ponytail: the touch is polled per tick (16 ms, the hold's 50), so a tap shorter than a tick
     * goes to Now Playing; ending it on the pointer's own down event would catch every one */
    if (top != land.win || !(r = queue_now(&pos, &n)) || song_key(r) != land.path ||
        window_manager_get_pointer_pressed(wm)) {
        /* a dialog on top keeps the bar's title: Now Playing's; a page names its own */
        if (top && top != land.win && tk_strcmp(widget_get_type(top), "window"))
            bar_title_name(land.win);
        widget_invalidate_force(land.win, 0);
        land.timer = 0;
        land_end();
        return 0;
    }
    int len = land.L.len, hold = theme()->cf.land_hold, ms = (int)(now - land.t0);
    if (land.ms < 0) {
        /* ponytail: Centre is dropped for up to LAND_FIRST_PAINT_MS + ms if Now Playing never
         * paints; a Now Playing that shows dark sooner would need its own end */
        if (now - land.at < LAND_FIRST_PAINT_MS) return 8; /* RET_REPEAT: no first paint yet */
    } else if (land.ms < len) {
        /* late: one tick on from the last frame, never a skip */
        if (ms - land.ms > 2 * FLIP_TICK_MS)
            ms = land.ms + FLIP_TICK_MS, land.t0 = now - (unsigned)ms;
        int was = land.got == 1 || land.veil, b[4];
        memcpy(b, land.box, sizeof b);
        land_render(ms);
        float q[8], deg = 360;
        if (ms < len) land_quad(land.L.from, land.L.to, ms, len, land.L.travel, q, &deg);
        if (deg >= 270) free(land.back), land.back = 0; /* face up from here on */
        if (was || land.got == 1 || land.veil)
            widget_invalidate_force(land.win, 0);
        else {
            int *c = land.box, x = b[0] < c[0] ? b[0] : c[0], y = b[1] < c[1] ? b[1] : c[1];
            int x1 = b[0] + b[2] > c[0] + c[2] ? b[0] + b[2] : c[0] + c[2];
            int y1 = b[1] + b[3] > c[1] + c[3] ? b[1] + b[3] : c[1] + c[3];
            int u[4] = { x, y, x1 - x, y1 - y };
            widget_invalidate_force(land.win, u);
        }
        if (ms < len) return 8;
        widget_invalidate_force(*(void *const *)system_bar, 0); /* its title: Now Playing, now */
        if (!(land.timer = timer_add(land_tick, 0, LAND_HOLD_TICK_MS))) land_end();
        return 0;
    } else if (!np_art_ready() && ms - len < hold)
        return 8; /* ponytail: past hold, Now Playing's art not loaded yet shows as stock's init
                   * left it (the last song's, or the placeholder) until np_art_poll loads it; a
                   * longer hold, or holding on while it reads, would cover it */
    /* ponytail: handed over, the 160-texel cache's art turns to stock's 356-texel cover, sharper;
     * the landing rendering from stock's cover would need its decode before Now Playing's own */
    void *art = land.art; /* handed over: Now Playing's own art */
    land.timer = 0;
    land_end();
    widget_invalidate_force(art, 0);
    return 0;
}

/* The press over: the song plays. Its window must be a Now Playing other than the window on top at
 * the press (land.top), whose art leans (its box taller than wide, as paint_cover asks) at
 * NP_IPOD_ART, else Now Playing stays as it opened. The panel's box and the art's in that window's
 * canvas. */
static int land_open(const void *unused) {
    land.timer = 0;
    coverflow_play(land.row);
    if (!land.front) return 0; /* ended while Now Playing opened */
    void *win = window_manager_get_top_window(window_manager()), *art = 0;
    if (win != land.top && !tk_strcmp(widget_get_prop_str(win, "name", ""), "playing_page"))
        art = widget_lookup(win, "img_cover", 1);
    if (!art || I(art, W_W) != NP_IPOD_ART || I(art, W_H) <= NP_IPOD_ART ||
        !(land.timer = timer_add(land_tick, 0, FLIP_TICK_MS))) {
        land_end();
        return 0;
    }
    const cf_style_t *s = &theme()->cf;
    land_t L = { .from = { I(cf.page, W_X) + I(fl.panel, W_X) - I(win, W_X),
                           I(cf.page, W_Y) + I(fl.panel, W_Y) - I(win, W_Y), BACK_W, BACK_H },
                 .to = { 0, 0, NP_IPOD_ART },
                 .len = s->land_ms,
                 .travel = s->land_travel,
                 .out_at = s->land_out_at,
                 .out_ms = s->land_out_ms,
                 .np_at = s->land_np_at,
                 .np_ms = s->land_np_ms };
    for (void *a = art; a != win; a = P(a, W_PARENT)) L.to[0] += I(a, W_X), L.to[1] += I(a, W_Y);
    if (L.to[0] < 0 || L.to[1] < 0 || L.to[0] + NP_IPOD_ART > CF_VIEW_W ||
        L.to[1] + I(art, W_H) > FLIP_H) { /* outside the frame slant_render lands in */
        land_end();
        return 0;
    }
    land.L = L, land.win = win, land.art = art, land.at = (unsigned)time_now_ms();
    widget_on(win, EVT_DESTROY, land_gone, win);
    return 0;
}

/* Cover Flow closing or loading again under the landing, or its wheel or Return (land_key): a press
 * not over yet plays its song now, as with no landing; a frame drawn over Now Playing goes with it.
 */
void land_drop(void) {
    if (land.timer && !land.win) coverflow_play(land.row);
    if (land.win) widget_invalidate_force(land.win, 0);
    land_end();
}

/* ringnav's key until the motion's last frame, which names the bar, is drawn (land_turning's clock
 * runs out a tick, or a stall, sooner): the wheel or Return ends the landing, the bar then
 * naming Now Playing (a volume pop-up the tick opens would keep it on Cover Flow's). 1 for a
 * Return Now Playing doesn't take yet (its open event comes after the pass's input): dropped, not
 * replayed after the open event. A second one reaches Cover Flow's page, whose keyup drops it. */
int land_key(unsigned key) {
    if (!land.front || land.ms >= land.L.len ||
        (key != KEY_PREV && key != KEY_NEXT && key != KEY_RETURN))
        return 0;
    land_drop();
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    bar_title_sync(top);
    return key == KEY_RETURN && !*(volatile unsigned char *)RETURN_RELEASE_LATCH &&
           P(wm, W_KEY_TARGET) != top;
}

/* The click on row (t) at t0, or 0: Now Playing then opens at once, as under Minimal. Classic's
 * panel standing, not turning back (a row's click can come during it: the turn's grab), Now
 * Playing's leaning art, the memory for the frame, the back, the art and the capture's canvas, the
 * art and the capture. The art is the cache's of the album the panel shows, a playlist's song's
 * own album's, as Home falls back to (coverflow_home_art), over Now Playing's paper as its bars;
 * no cache file, or a "no art" one, is refused. */
int land_start(void *t, int row, unsigned t0) {
    const cf_style_t *s = &theme()->cf;
    unsigned kb = (CF_VIEW_W * (FLIP_H + 320) + BACK_W * BACK_H + CF_TEXELS) / 256 + MP4_SPARE_KB;
    if (!classic_pages() || !s->land_ms || !fl.panel || fl.dir || np_layout() == NP_CENTRED ||
        mem_available_kb() < kb || !(land.front = calloc(CF_TEXELS, 4)))
        return 0;
    /* ponytail: the album's cache, so a compilation track or a playlist song with its own embedded
     * art swaps pictures at the hand-over; the track's own art would need its decode on the click
     */
    void *r = cf.scoped == PLAYLISTS ? t : deque_at(cf.albums, (unsigned)cf.album);
    char url[600] = "file://";
    if (r) art_path(url + 7, album_key(r), "");
    if (!r || !cf_decode(url, land.front, 1, ipod_light() ? scheme()->paper & 255 : 0) ||
        !flip_alloc()) {
        land_end();
        return 0;
    }
    for (int i = 0; i < CF_TEXELS; ++i) land.front[i] = bgra(land.front[i]);
    ringnav_light(widget_get_child(widget_get_child(fl.panel, 1), 0), row); /* lit, tapped too */
    int painted = flip_capture();
    widget_set_visible(fl.panel, 1, 0);
    widget_invalidate_force(fl.panel, 0);
    land.frame = fl.frame, land.back = fl.back, fl.frame = 0, fl.back = 0;
    if (!painted) { /* the flip's paper would turn a blank sheet */
        land_end();
        return 0;
    }
    land.path = song_key(t), land.row = row, land.ms = -1;
    land.top = window_manager_get_top_window(window_manager());
    int wait = s->land_press - (int)((unsigned)time_now_ms() - t0);
    if (!(land.timer = timer_add(land_open, 0, wait < 1 ? 1 : (unsigned)wait))) {
        land_end();
        return 0;
    }
    drop_held(); /* wheel ticks held before a tap's pick */
    return 1;
}
