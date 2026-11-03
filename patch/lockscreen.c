/* Lock screen (docs/internals.md#lock-screen): six built-in wallpapers, drawn here since the rootfs
 * has no room for image files, and the Large clock. wall_render is plain C, so test/patch.py also
 * builds it on the host (PEQ_HOST) to check it and write previews. */
#include "peq.h"

#define WALLS 6
#define WALL_GRID 5 /* the colour field is evaluated every 5 pixels and interpolated */

typedef struct { float u, v, r, g, b; } wall_point;

/* Inverse-distance-squared blend of the points: a mesh gradient with no edges. */
static void mesh(const wall_point *p, int n, float u, float v, float aspect, float *rgb) {
    float sum = 0, c[3] = { 0, 0, 0 };
    for (int i = 0; i < n; ++i) {
        float dx = (u - p[i].u) * aspect, dy = v - p[i].v, k = 1 / (0.06f + dx * dx + dy * dy);
        k *= k;
        sum += k, c[0] += k * p[i].r, c[1] += k * p[i].g, c[2] += k * p[i].b;
    }
    for (int i = 0; i < 3; ++i) rgb[i] = c[i] / sum;
}
static float clamp01(float x) { return x < 0 ? 0 : x > 1 ? 1 : x; }
/* A soft round falloff: 1 at the centre, 0 from radius r. */
static float glow(float dx, float dy, float r) {
    float t = clamp01(1 - (dx * dx + dy * dy) / (r * r));
    return t * t;
}
static void blend(float *rgb, float r, float g, float b, float t) {
    rgb[0] += (r - rgb[0]) * t, rgb[1] += (g - rgb[1]) * t, rgb[2] += (b - rgb[2]) * t;
}

/* The colour at (u, v), each 0..1 across the picture; aspect is width / height. */
static void wall_field(int id, float u, float v, float aspect, float *rgb) {
    static const wall_point aurora[] = {
        { 0.0f, 0.0f, 0.06f, 0.05f, 0.22f },  { 0.30f, 0.30f, 0.42f, 0.20f, 0.82f },
        { 0.80f, 0.20f, 0.08f, 0.62f, 0.70f }, { 0.62f, 0.78f, 0.80f, 0.22f, 0.58f },
        { 0.05f, 0.95f, 0.04f, 0.05f, 0.16f }, { 1.0f, 1.0f, 0.10f, 0.06f, 0.26f },
        { 1.0f, 0.0f, 0.05f, 0.18f, 0.35f },
    };
    static const wall_point sunrise[] = {
        { 0.82f, 0.10f, 1.00f, 0.74f, 0.38f }, { 0.08f, 0.05f, 0.96f, 0.56f, 0.46f },
        { 0.40f, 0.48f, 0.92f, 0.38f, 0.36f }, { 0.90f, 0.80f, 0.74f, 0.26f, 0.42f },
        { 0.12f, 0.98f, 0.36f, 0.12f, 0.32f }, { 0.55f, 1.05f, 0.50f, 0.16f, 0.36f },
    };
    float dx, dy;
    switch (id) {
    case 0: /* Aurora: indigo, violet, teal and magenta */
        mesh(aurora, sizeof aurora / sizeof *aurora, u, v, aspect, rgb);
        break;
    case 1: /* Sunrise: gold, coral and plum */
        mesh(sunrise, sizeof sunrise / sizeof *sunrise, u, v, aspect, rgb);
        break;
    case 2: { /* Graphite: dark machined aluminium with one soft diagonal sheen */
        float base = 0.19f - 0.10f * v, d = (u * aspect + 0.7f * v - 0.75f) / 0.42f;
        rgb[0] = rgb[1] = base, rgb[2] = base + 0.012f;
        float s = glow(d, 0, 1);
        rgb[0] += 0.11f * s, rgb[1] += 0.11f * s, rgb[2] += 0.12f * s;
        break;
    }
    case 3: { /* Tide: layered waves, deep navy into azure */
        static const float bands[][3] = { { 0.04f, 0.13f, 0.34f }, { 0.06f, 0.24f, 0.52f },
                                          { 0.10f, 0.40f, 0.70f }, { 0.30f, 0.62f, 0.86f } };
        rgb[0] = 0.02f, rgb[1] = 0.04f, rgb[2] = 0.12f + 0.06f * v;
        for (int k = 0; k < 4; ++k) {
            float crest = 0.40f + 0.15f * (float)k +
                          0.055f * sinf(6.2832f * (u * (0.9f + 0.15f * (float)k) + 0.27f * (float)k));
            float t = clamp01((v - crest + 0.02f) / 0.055f);
            t = t * t * (3 - 2 * t);
            blend(rgb, bands[k][0], bands[k][1], bands[k][2], t);
            float lip = clamp01(1 - (v - crest) * (v - crest) / 0.0009f); /* a faint bright crest */
            blend(rgb, 0.70f, 0.85f, 1.0f, 0.12f * lip * lip);
        }
        break;
    }
    case 4: /* Ember: Shanling red rising out of black */
        dx = (u - 0.5f) * aspect, dy = v - 1.15f;
        rgb[0] = 0.035f, rgb[1] = 0.02f, rgb[2] = 0.025f;
        blend(rgb, 0.62f, 0.04f, 0.08f, glow(dx, dy, 1.25f));
        blend(rgb, 0.98f, 0.30f, 0.12f, glow(dx, dy * 1.15f, 0.80f) * 0.85f);
        blend(rgb, 1.0f, 0.62f, 0.30f, glow(dx * 1.3f, dy * 1.5f, 0.55f) * 0.6f);
        break;
    default: /* Black */
        rgb[0] = rgb[1] = rgb[2] = 0;
        return;
    }
    /* A gentle vignette keeps the edges and the clock's top third calm. */
    dx = (u - 0.5f) * aspect, dy = v - 0.55f;
    float dark = 1 - 0.28f * clamp01((dx * dx + dy * dy) / 0.55f);
    for (int i = 0; i < 3; ++i) rgb[i] *= dark;
}

/* Hashed noise in -k/2..k/2. */
static float noise(unsigned x, float k) {
    x ^= x >> 16, x *= 0x7feb352du, x ^= x >> 15, x *= 0x846ca68bu;
    return ((float)((x ^ x >> 16) & 255) - 127.5f) / 255 * k;
}

/* The wallpaper id (0..WALLS-1) into w x h RGBA8888 pixels, stride in pixels. */
int wall_render(unsigned *dst, int stride, int w, int h, int id) {
    static const unsigned char bayer[16] = { 0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5 };
    int gw = w / WALL_GRID + 2, gh = h / WALL_GRID + 2;
    float *grid = calloc((unsigned)(gw * gh * 3), sizeof(float)), aspect = (float)w / (float)h;
    if (!grid) return 0;
    for (int j = 0; j < gh; ++j)
        for (int i = 0; i < gw; ++i)
            wall_field(id, (float)(i * WALL_GRID) / (float)(w - 1), (float)(j * WALL_GRID) / (float)(h - 1),
                       aspect, grid + (j * gw + i) * 3);
    for (int y = 0; y < h; ++y) {
        int j = y / WALL_GRID;
        float fy = (float)(y % WALL_GRID) / WALL_GRID;
        /* Graphite's brushed grain: each row its own shade, drifting slowly along it. */
        float row = id == 2 ? noise((unsigned)y, 0.035f) : 0;
        for (int x = 0; x < w; ++x) {
            int i = x / WALL_GRID;
            float fx = (float)(x % WALL_GRID) / WALL_GRID;
            const float *a = grid + (j * gw + i) * 3, *b = a + 3, *c = a + gw * 3, *d = c + 3;
            float grain = id == 2 ? row + noise((unsigned)(y * 977 + x / 24), 0.012f) : 0;
            float dither = ((float)bayer[(y & 3) * 4 + (x & 3)] + 0.5f) / 16;
            unsigned px = 0xff000000u;
            for (int k = 0; k < 3; ++k) {
                float top = a[k] + (b[k] - a[k]) * fx, bottom = c[k] + (d[k] - c[k]) * fx;
                float value = (top + (bottom - top) * fy + grain) * 255 + dither;
                px |= (unsigned)(value < 0 ? 0 : value > 255 ? 255 : (int)value) << (8 * k);
            }
            dst[y * stride + x] = px;
        }
    }
    free(grid);
    return 1;
}

#ifndef PEQ_HOST
#include "offsets.inc"
#include "playback.h"
#include "stock.h"

extern int stock_saver_trampoline(void *, void *), stock_saverrows_trampoline(void *),
    stock_saverlist_trampoline(void), stock_savercover_trampoline(void *);
extern void pod_rows(void *win, const char *view_name, int first, int n, const char *const *icons);
extern int ringnav_lock_clock(void); /* navigation.c: Q2POD LOCKCLOCK, 1 Large, else Standard */
extern int ringnav_wm_paint(void *wm); /* navigation.c: window_manager_paint's leaf */

/* The built-ins' names in the picture list and SYSSET SCREENSAVERPATH, by id. None is a file:
 * stock's lock window falls back to the name as an asset, finds none and draws nothing, and these
 * paint over it. */
static const char *const walls[WALLS] = { "q2pod:aurora", "q2pod:sunrise", "q2pod:graphite",
                                          "q2pod:tide",   "q2pod:ember",   "q2pod:black" };
#define PICTURE ((char *)g_screensaver_picture) /* 256 bytes */
#define PICTURES P(p_deque_screensaverlist, 0)

static struct {
    void *img, *full;                      /* the lock window's picture and its wallpaper */
    unsigned full_key;                     /* full's id, width and height, kept between windows */
    void *thumb[WALLS], *small[WALLS];     /* the picker's built-in thumbnails and theirs */
} lk __attribute__((section(".scratch")));

static int wall_id(const char *name) {
    for (int i = 0; name && i < WALLS; ++i)
        if (!strcmp(name, walls[i])) return i;
    return -1;
}

/* Only stock's two wallpaper validation calls use this; ordinary missing files still fail. */
int lockscreen_access(const char *path, int mode) {
    return wall_id(path) >= 0 ? 0 : access(path, mode);
}

static void *wall_bitmap(int w, int h, int id) {
    void *bm = bitmap_create_ex((unsigned)w, (unsigned)h, (unsigned)w * 4, 1 /* RGBA8888 */);
    unsigned *d = bm ? (unsigned *)bitmap_lock_buffer_for_write(bm) : 0;
    int ok = d && wall_render(d, (int)(bitmap_get_line_length(bm) / 4), w, h, id);
    if (d) bitmap_unlock_buffer(bm);
    if (!ok) {
        if (bm) bitmap_destroy(bm);
        return 0;
    }
    *(unsigned short *)((char *)bm + 0xc) |= 1; /* BITMAP_FLAG_OPAQUE: every pixel is */
    return bm;
}

/* A picture widget (ctx) goes: its pointer must never match a later widget at the same address. A
 * widget already replaced by a newer one matches nothing here. */
static int forget(void *ctx, void *event) {
    if (ctx == lk.img) lk.img = 0; /* full stays for the next lock window */
    for (int i = 0; i < WALLS; ++i)
        if (ctx == lk.thumb[i]) {
            if (lk.small[i]) bitmap_destroy(lk.small[i]);
            lk.thumb[i] = lk.small[i] = 0;
        }
    return 0;
}

/* get_screensaver_image: stock lists screensaver_default and up to five card images, and resets a
 * picture that is neither the default nor a file. The built-ins go between them, and a built-in
 * that was picked is kept. */
int lockscreen_images(void) {
    char saved[256], cards[5][256];
    memcpy(saved, PICTURE, sizeof saved);
    saved[255] = 0;
    int result = stock_saverlist_trampoline();
    void *list = PICTURES;
    unsigned n = list ? deque_size(list) : 0, kept = 0;
    for (; n > 1; --n) { /* no pop_front in demo: take the card images off the back */
        const char *s = deque_at(list, n - 1);
        if (wall_id(s) < 0 && kept < 5) snprintf(cards[kept++], sizeof *cards, "%s", s);
        deque_pop_back(list);
    }
    if (n) {
        for (int i = 0; i < WALLS; ++i) _deque_push_back(list, walls[i]);
        while (kept) _deque_push_back(list, cards[--kept]);
    }
    if (wall_id(saved) >= 0 && strcmp(PICTURE, saved)) {
        memcpy(PICTURE, saved, sizeof saved);
    }
    return result;
}

/* The Large clock: the date (22 px) centred on a top line at y 14 with the battery percent at its
 * right end, wide enough for "100%", when the Battery setting shows a percentage (the icon hides;
 * stock never sets their visibility), the time (116 px)
 * across the screen under it, all above the now-playing card (y 190). */
static void big_clock(void *win) {
    void *info = widget_lookup(win, "view_timeinfo", 1), *time = widget_lookup(win, "clock_time", 1),
         *date = widget_lookup(win, "clock_date", 1), *pct = widget_lookup(win, "label_battery", 1),
         *batt = widget_lookup(win, "img_battery", 1);
    if (!info || !time || !date || !pct || !batt) return;
    widget_move_resize(info, 0, 14, 375, 150);
    widget_move_resize(date, 0, 0, 375, 30);
    widget_move_resize(time, 0, 26, 375, 124);
    widget_move_resize(pct, 296, 3, 64, 24);
    widget_set_visible(batt, 0, 0);
    extern int ipod_battery(void); /* navigation.c: IPOD BATTERY, 0 Icon, else a percentage */
    widget_set_visible(pct, ipod_battery() != 0, 0);
    widget_set_prop_int(date, "style:normal:font_size", 22);
    widget_set_prop_int(time, "style:normal:font_size", 116);
    widget_set_prop_str(date, "style:normal:text_align_h", "center");
    widget_set_prop_str(time, "style:normal:text_align_h", "center");
}

/* saverscreen_page_init: stock builds the lock window; a built-in picture then paints in place of
 * stock's (lockscreen_paint), and Large lays the clock out again. */
int lockscreen_init(void *win, void *ctx) {
    int result = stock_saver_trampoline(win, ctx);
    void *img = widget_lookup(win, "img_screensaver", 1);
    int id = wall_id(PICTURE);
    if (img && id >= 0) {
        image_base_set_image(img, ""); /* nothing to look up on every paint */
        /* rendered once per picture and size, not per lock window: each screen-on shows one.
         * ponytail: about 470 KB kept for the life of demo; drop it on the next pick if RAM is short. */
        unsigned key = (unsigned)id << 24 | (unsigned)I(img, W_W) << 12 | (unsigned)I(img, W_H);
        lk.img = img;
        if (!lk.full || lk.full_key != key) {
            if (lk.full) bitmap_destroy(lk.full);
            lk.full = id == WALLS - 1 ? 0 : wall_bitmap(I(img, W_W), I(img, W_H), id); /* Black: the window's own */
            lk.full_key = key;
        }
        widget_on(img, EVT_DESTROY, forget, img);
    }
    if (ringnav_lock_clock()) big_clock(win);
    return result;
}

/* The lock window's cover (stock 0x52d844, from the window's timer once a new track's cover is
 * parsed, and from its init): stock sets img_playcover to the same file name and never invalidates
 * it, so the old art stayed until something else repainted over it. */
int lockscreen_cover(void *win) {
    int result = stock_savercover_trampoline(win);
    void *img = widget_lookup(win, "img_playcover", 1);
    if (img) widget_invalidate_force(img, 0);
    return result;
}

/* The Lock screen page's list (stock 0x4c9134), rebuilt whole when its switch turns: Time show and
 * About, then the picture grid with the switch on. Clock (navigation.c's POD_LOCKCLOCK) follows
 * Time show, 78 high as stock's rows, and the built-ins' thumbnails are found for lockscreen_paint. */
int lockscreen_rows(void *win) {
    static const char *const icons[] = { 0 };
    int result = stock_saverrows_trampoline(win);
    void *view = widget_lookup(win, "scroll_view", 1);
    if (!view || widget_count_children(view) < 2) return result;
    pod_rows(win, "scroll_view", 7 /* POD_LOCKCLOCK */, 1, icons);
    void *row = widget_get_child(view, widget_count_children(view) - 1);
    widget_resize(row, I(row, W_W), 78);
    widget_restack(row, 1);
    void *list = PICTURES;
    for (unsigned i = 0, n = list ? deque_size(list) : 0; i < n; ++i) {
        int id = wall_id(deque_at(list, i));
        char name[16];
        tk_snprintf(name, sizeof name, "image_%u", i);
        void *img = id >= 0 ? widget_lookup(view, name, 1) : (void *)0;
        if (!img) continue;
        image_base_set_image(img, "");
        lk.thumb[id] = img; /* a rebuilt grid's thumbnails are the same size: small[id] stays */
        widget_on(img, EVT_DESTROY, forget, img);
    }
    return result;
}

/* ringnav_paint (the border hook, after stock painted the widget) calls this for every widget:
 * over a built-in picture it draws the wallpaper; thumbnails render on first sight. Stock painted
 * the children (a thumbnail's img_choice, the check badge) first, so they are painted again on top. */
void lockscreen_paint(void *w, void *canvas) {
    void *bm = 0;
    if (!w) return;
    if (w == lk.img) bm = lk.full;
    for (int i = 0; !bm && i < WALLS; ++i)
        if (w == lk.thumb[i]) bm = lk.small[i] ? lk.small[i] : (lk.small[i] = wall_bitmap(I(w, W_W), I(w, W_H), i));
    if (!bm) return;
    int r[4] = { 0, 0, I(w, W_W), I(w, W_H) };
    canvas_draw_image(canvas, bm, r, r);
    widget_on_paint_children(w, canvas);
}
#endif
