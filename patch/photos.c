/* Photos (docs/internals.md#photos), after the iPod's: the card's Photos folder, its subfolders as
 * albums under All Photos, a thumbnail grid, then one photo at a time, full screen, the wheel
 * moving between them. Stock's toolsThumbSpecCover makes a screen-size copy and a thumbnail of
 * each photo into PHOTO_DIR on one pthread, as Coverflow's art; the page draws them itself so the
 * EXIF orientation applies everywhere. Music plays on throughout. */
#include "offsets.inc"
#include "peq.h"
#include "stock.h"

#define PHOTO_DIR PEQ_ROOT "/mnt/mmc/.photos"
#define ALBUMS_MAX 200    /* ponytail: subfolders listed; deeper folders are not albums */
#define PH_HEAD 4096      /* bytes read for the EXIF orientation; IFD0 sits at the start */
#define PH_TILE_SLOTS 32  /* decoded thumbnails kept: three rows on screen and some either side */
#define PH_BAD 9          /* state: the file will not open */

enum { ALBUMS, GRID, VIEWER };
typedef struct {
    void *bm;  /* bitmap_t *, box x box RGBA8888 */
    int photo; /* its photo + 1; 0 empty */
    int w, h;  /* the shown size; 0 when the file did not decode */
} slot_t;
static struct {
    void *page, *albums, *grid, *view, *viewer, *slide, *info;
    char root[32];
    char **album; /* album folder names, sorted */
    int nalbums;
    char **path;                   /* the open grid's photos */
    unsigned *key;                 /* their cache keys */
    volatile unsigned char *state; /* 0 pending, 1-8 ready (the EXIF orientation), PH_BAD */
    int n, screen, target, current, info_on, space, cursor, seen;
    unsigned long thread;
    unsigned timer, poll;
    int running;
    volatile int done, cancel, want, finished;
    slot_t tiles[PH_TILE_SLOTS], shots[3];
} ph __attribute__((section(".scratch")));

/* A .jpg, .jpeg or .png that is not hidden (macOS leaves ._ copies beside each photo). */
int photo_file(const char *name) {
    const char *dot = strrchr(name, '.');
    return name[0] != '.' && dot &&
           (!strcasecmp(dot + 1, "jpg") || !strcasecmp(dot + 1, "jpeg") || !strcasecmp(dot + 1, "png"));
}

static unsigned u16(const unsigned char *p, int le) {
    return le ? p[0] | p[1] << 8 : p[0] << 8 | p[1];
}

/* The EXIF orientation (1-8) in a JPEG's first n bytes, 1 when it has none. */
int photo_orientation(const unsigned char *b, unsigned n) {
    for (unsigned i = 2; n >= 4 && b[0] == 0xff && b[1] == 0xd8 && i + 4 <= n && b[i] == 0xff;) {
        unsigned marker = b[i + 1], len = u16(b + i + 2, 0);
        if (marker == 0xda || len < 2) break; /* image data follows */
        if (marker == 0xe1 && len >= 16 && i + 18 <= n && !memcmp(b + i + 4, "Exif\0\0", 6)) {
            const unsigned char *t = b + i + 10; /* the TIFF header */
            unsigned size = (i + 2 + len < n ? i + 2 + len : n) - (i + 10);
            int le = t[0] == 'I';
            unsigned ifd =
                le ? u16(t + 4, 1) | u16(t + 6, 1) << 16 : u16(t + 4, 0) << 16 | u16(t + 6, 0);
            if (t[0] != t[1] || (t[0] != 'I' && t[0] != 'M') || ifd >= size || size - ifd < 2)
                return 1;
            for (unsigned k = 0, count = u16(t + ifd, le); k < count && ifd + 14 + 12 * k <= size;
                 ++k) {
                const unsigned char *e = t + ifd + 2 + 12 * k;
                if (u16(e, le) == 0x112) return u16(e + 8, le) - 1u < 8 ? u16(e + 8, le) : 1;
            }
            return 1;
        }
        i += 2 + len;
    }
    return 1;
}

/* src (w x h, 32-bit, stride bytes, channels in order at) as EXIF orientation o shows it, over
 * black, into dst (RGBA8888, box x box), cropped to the box; size gets the shown width and height.
 * Orientations 5-8 swap the sides; 2, 3, 7 and 8 mirror x, and 3, 4, 6 and 7 y. */
void photo_orient(const unsigned char *src, int w, int h, int stride, const unsigned char *at,
                  int o, unsigned *dst, int box, int *size) {
    int turn = o >= 5, fx = 0x18c >> o & 1, fy = 0xd8 >> o & 1;
    int sw = turn ? h : w, sh = turn ? w : h;
    size[0] = sw = sw < box ? sw : box;
    size[1] = sh = sh < box ? sh : box;
    for (int y = 0; y < sh; ++y)
        for (int x = 0; x < sw; ++x) {
            int u = turn ? y : x, v = turn ? x : y;
            const unsigned char *px =
                src + (fy ? h - 1 - v : v) * stride + (fx ? w - 1 - u : u) * 4;
            unsigned a = px[at[3]], r = px[at[0]], g = px[at[1]], b = px[at[2]];
            if (a < 255) r = r * a / 255, g = g * a / 255, b = b * a / 255;
            dst[y * box + x] = 0xff000000u | r | g << 8 | b << 16;
        }
}

/* A w x h image fitted into the bw x bh box at x, y, centred and never enlarged. */
void photo_place(int w, int h, int x, int y, int bw, int bh, int *r) {
    if (w > bw) h = h * bw / w, w = bw;
    if (h > bh) w = w * bh / h, h = bh;
    r[0] = x + (bw - w) / 2, r[1] = y + (bh - h) / 2, r[2] = w > 0 ? w : 1, r[3] = h > 0 ? h : 1;
}

static void cache_path(char *out, unsigned key, const char *suffix) {
    tk_snprintf(out, 64, PHOTO_DIR "/%08x%s", key, suffix);
}

/* The worker: photo i's orientation, then its screen-size copy (written to .tmp, renamed) and a
 * thumbnail from that copy, unless cached. A photo that stock cannot read (corrupt, a PNG over
 * 1 MB, a JPEG over 6 MB) gets a .bad marker, so it is not tried again. */
static int build(int i) {
    char shot[64], tmp[64], small[64];
    unsigned char head[PH_HEAD];
    void *f = fopen(ph.path[i], "rb");
    if (!f) return PH_BAD; /* storage gone: no marker, the next open retries */
    unsigned n = fread(head, 1, sizeof head, f);
    fclose(f);
    int o = photo_orientation(head, n), turn = o >= 5;
    cache_path(shot, ph.key[i], ".bad");
    if (!access(shot, 0)) return PH_BAD;
    cache_path(tmp, ph.key[i], ".tmp");
    cache_path(shot, ph.key[i], ".jpg");
    if (access(shot, 0)) {
        if (!ph.space) return PH_BAD;
        if (!thumb(ph.path[i], tmp, turn ? PH_SHOT_H : PH_SHOT_W, turn ? PH_SHOT_W : PH_SHOT_H) ||
            rename(tmp, shot)) {
            unlink(tmp);
            if (access(ph.path[i], 0)) return PH_BAD;
            cache_path(shot, ph.key[i], ".bad");
            if ((f = fopen(shot, "w"))) fclose(f);
            return PH_BAD;
        }
    }
    cache_path(small, ph.key[i], "t.jpg");
    if (ph.space && access(small, 0) &&
        (!thumb(shot, tmp, turn ? PH_THUMB_H : PH_THUMB_W, turn ? PH_THUMB_W : PH_THUMB_H) ||
         rename(tmp, small)))
        unlink(tmp); /* no thumbnail: the tile stays grey, the photo still opens */
    return o;
}

/* The viewer's photo first, then the next and previous, then the rest in order. */
static int next_job(void) {
    static const int near[3] = { 0, 1, -1 };
    for (int k = 0; k < 3; ++k) {
        int i = ph.want + near[k];
        if (ph.want >= 0 && i >= 0 && i < ph.n && !ph.state[i]) return i;
    }
    while (ph.cursor < ph.n && ph.state[ph.cursor]) ++ph.cursor;
    return ph.cursor < ph.n ? ph.cursor : -1;
}

static void *worker(void *unused) {
    for (int i; !ph.cancel && (i = next_job()) >= 0; ++ph.done)
        ph.state[i] = (unsigned char)build(i);
    ph.finished = 1;
    return 0;
}

static void info(void);

/* While the worker runs: repaint as photos become ready. */
static int poll(const void *unused) {
    ph.poll = 0;
    if (ph.done != ph.seen) {
        ph.seen = ph.done;
        widget_invalidate_force(ph.page, 0);
        info();
    }
    if (ph.finished) {
        pthread_join(ph.thread, 0);
        ph.running = 0;
    } else
        ph.poll = timer_add(poll, 0, 250);
    return 0;
}

static void drop_slots(slot_t *s, int count) {
    for (int k = 0; k < count; ++k)
        if (s[k].bm) bitmap_destroy(s[k].bm);
    memset(s, 0, count * sizeof *s);
}

/* Cancel waits for the photo being made; finished copies stay for the next open. */
static void drop_photos(void) {
    worker_stop(ph.thread, &ph.running, &ph.cancel, &ph.poll);
    for (int i = 0; i < ph.n; ++i) free(ph.path[i]);
    free(ph.path);
    free(ph.key);
    free((void *)ph.state);
    ph.path = 0, ph.key = 0, ph.state = 0, ph.n = 0;
    drop_slots(ph.tiles, PH_TILE_SLOTS);
    drop_slots(ph.shots, 3);
    if (ph.viewer) widget_destroy_children(ph.viewer);
    ph.slide = ph.info = 0;
}

/* qsort's order for a list of strings; shared with books.c. */
int by_string(const void *a, const void *b) {
    return strcmp(*(char *const *)a, *(char *const *)b);
}

/* dir's photos (full paths) or, with albums, its subfolders that hold any (names), added to list
 * from *n up to cap in name order; with no list, whether dir holds a photo. */
static int scan(const char *dir, char **list, int *n, int cap, int albums) {
    void *d = opendir(dir);
    int from = *n, found = 0, none = 0;
    char path[600];
    for (struct dirent *e; d && !found && *n < cap && (e = readdir(d));) {
        if (e->d_name[0] == '.') continue;
        tk_snprintf(path, sizeof path, "%s/%s", dir, e->d_name);
        int ok = albums ? (e->d_type == 4 || !e->d_type) && scan(path, 0, &none, 1, 0)
                        : (e->d_type == 8 || !e->d_type) && photo_file(e->d_name);
        if (ok && !list)
            found = 1;
        else if (ok && (list[*n] = strdup(albums ? e->d_name : path)))
            ++*n;
    }
    if (d) closedir(d);
    if (list) qsort(list + from, (unsigned)(*n - from), sizeof *list, by_string);
    return found;
}

static int open_tile(void *ctx, void *event);
static int toggle_info(void *ctx, void *event);

/* The grid of album target - 1, or with -1 All Photos: the folder's own photos, then every
 * album's. The worker then makes whatever is not cached yet. */
static int to_grid(const void *unused) {
    ph.timer = 0;
    drop_photos();
    int a = ph.target - 1, n = 0;
    char dir[600];
    ph.path = calloc(PHOTOS_MAX, sizeof *ph.path);
    if (ph.path) {
        if (a < 0) scan(ph.root, ph.path, &n, PHOTOS_MAX, 0);
        for (int k = a < 0 ? 0 : a; k < ph.nalbums && (a < 0 || k == a); ++k) {
            tk_snprintf(dir, sizeof dir, "%s/%s", ph.root, ph.album[k]);
            scan(dir, ph.path, &n, PHOTOS_MAX, 0);
        }
    }
    ph.n = n;
    ph.key = calloc(n + 1, sizeof *ph.key);
    ph.state = calloc(n + 1, 1);
    if (!ph.key || !ph.state) {
        for (int i = 0; i < n; ++i) free(ph.path[i]);
        n = ph.n = 0;
    }
    for (int i = 0; i < n; ++i) ph.key[i] = fnv(FNV_SEED, (const unsigned char *)ph.path[i]);
    ph.screen = GRID;
    widget_set_visible(ph.albums, 0, 0);
    widget_destroy_children(ph.grid);
    widget_set_visible(ph.grid, 1, 0);
    page_title(ph.grid, !n           ? "暂无照片"
                        : a >= 0     ? ph.album[a]
                        : ph.nalbums ? "所有照片"
                                     : "照片");
    /* A bare scroll view: a list_view would lay its children out as full-width rows. */
    int rows = (n + PH_COLS - 1) / PH_COLS;
    ph.view = scroll_view_create(ph.grid, 0, 48, 375, 240);
    widget_set_prop_int(ph.view, "yslidable", 1);
    widget_set_prop_int(ph.view, "xslidable", 0);
    widget_set_prop_int(ph.view, "virtual_h", rows * PH_TILE_H);
    for (int i = 0; i < n; ++i) {
        void *tile = list_item_create(ph.view, PH_GRID_X + i % PH_COLS * PH_TILE_W,
                                      i / PH_COLS * PH_TILE_H, PH_TILE_W, PH_TILE_H);
        widget_use_style(tile, "s_listitem_black");
        widget_on(tile, EVT_CLICK, open_tile, (void *)(long)i);
    }
    mkdir(PHOTO_DIR, 0755);
    ph.space = card_space(PHOTO_DIR);
    ph.done = ph.seen = ph.cancel = ph.finished = ph.cursor = 0;
    ph.want = -1;
    if (n && !pthread_create(&ph.thread, 0, worker, 0)) {
        ph.running = 1;
        poll(0);
    } else
        for (int i = 0; i < n; ++i) ph.state[i] = PH_BAD;
    widget_invalidate_force(ph.page, 0);
    return 0;
}

/* The bottom caption: "3 of 40" and the file name when Centre turned it on, or why there is no
 * photo yet. */
static void info(void) {
    if (!ph.info || ph.current >= ph.n) return;
    int s = ph.state[ph.current];
    const char *name = strrchr(ph.path[ph.current], '/') + 1; /* a full path */
    char caption[300];
    if (!s || s == PH_BAD)
        tk_snprintf(caption, sizeof caption, "%s",
                    s ? "无法打开此照片" : "加载中…");
    else
        tk_snprintf(caption, sizeof caption, "%d / %d  %s", ph.current + 1, ph.n, name);
    if (text_differs(ph.info, caption)) { /* a decode redraws the same */
        widget_set_text_utf8(ph.info, caption);
        widget_set_prop_int(ph.info, "xoffset", 0); /* a long caption's scroll would shift this one left */
    }
    widget_set_visible(ph.info, ph.info_on || !s || s == PH_BAD, 0);
}

static int moved(void *ctx, void *event) {
    ph.current = I(ph.slide, SLIDE_INDEX);
    info();
    return 0;
}

/* One photo full screen: a slide_menu of empty children, one per photo, whose items are the
 * page's height square with a spacer making the stride the screen's width. The wheel steps it as
 * Coverflow's covers (ringnav's carousel), and the paint hook draws the photos at its live
 * offset, so they slide. */
static int to_viewer(const void *unused) {
    ph.timer = 0;
    int h = widget_get_prop_int(ph.page, "h", PH_SHOT_H);
    if (!ph.slide) {
        void *f = widget_factory();
        ph.slide = widget_factory_create_widget(f, "slide_menu", ph.viewer, 0, 0, 375, h);
        slide_menu_set_spacer(ph.slide, 375 - h);
        for (int i = 0; i < ph.n; ++i) {
            void *img = image_create(ph.slide, 0, 0, 0, 0);
            widget_set_prop_int(img, "clickable", 1);
            widget_on(img, EVT_CLICK, toggle_info, 0);
        }
        ph.info = bottom_caption(ph.viewer, h);
        widget_on(ph.slide, EVT_VALUE_CHANGED, moved, 0);
    }
    ph.screen = VIEWER;
    ph.current = ph.want = ph.target;
    slide_menu_set_value(ph.slide, ph.target);
    widget_set_visible(ph.grid, 0, 0);
    widget_set_visible(ph.viewer, 1, 0);
    info();
    widget_invalidate_force(ph.page, 0);
    return 0;
}

/* Back from the viewer: the grid with the photo last shown selected and in view. */
static void to_tiles(void) {
    ph.screen = GRID;
    ph.want = -1;
    widget_set_visible(ph.viewer, 0, 0);
    widget_set_visible(ph.grid, 1, 0);
    ringnav_select(ph.view, ph.current, ph.n);
    int y = ph.current / PH_COLS * PH_TILE_H, top = I(ph.view, SCROLL_Y);
    if (y < top) top = y;
    if (y + PH_TILE_H > top + 240) top = y + PH_TILE_H - 240;
    scroll_view_set_offset(ph.view, 0, top);
    widget_invalidate_force(ph.page, 0);
}

/* Clicks only schedule: a screen change never destroys the widget whose click is running. */
static int open_album(void *ctx, void *event) {
    ph.target = (int)(long)ctx;
    rearm(&ph.timer, to_grid, 0);
    return 0;
}

static int open_tile(void *ctx, void *event) {
    ph.target = (int)(long)ctx;
    rearm(&ph.timer, to_viewer, 0);
    return 0;
}

static int toggle_info(void *ctx, void *event) {
    ph.info_on = !ph.info_on;
    info();
    return 0;
}

static int leave(const void *unused) {
    ph.timer = 0;
    if (ph.screen == VIEWER)
        to_tiles();
    else if (ph.screen == GRID && ph.nalbums) {
        drop_photos();
        widget_destroy_children(ph.grid);
        ph.view = 0;
        widget_set_visible(ph.grid, 0, 0);
        widget_set_visible(ph.albums, 1, 0);
        ph.screen = ALBUMS;
        widget_invalidate_force(ph.page, 0);
    } else
        navigator_back();
    return 0;
}

/* Return: viewer -> grid -> albums -> Local Music. */
static int keyup(void *ctx, void *event) {
    return return_key(event, &ph.timer, leave);
}

static int closed(void *ctx, void *event) {
    ph.viewer = 0; /* going with the page */
    drop_photos();
    stop_timer(&ph.timer);
    for (int k = 0; k < ph.nalbums; ++k) free(ph.album[k]);
    free(ph.album);
    memset(&ph, 0, sizeof ph);
    return 0;
}

/* root's photo i into a slot of s (count of them, box px square): the one already holding it, else
 * an empty one, else the one holding the photo farthest away. The cached file is decoded, turned
 * upright into the slot and dropped from the image manager at once, as Coverflow's covers. */
static slot_t *slot(slot_t *s, int count, int box, int i, const char *suffix) {
    static const unsigned char at[4][4] = BITMAP_RGBA_AT;
    slot_t *pick = 0;
    for (int k = 0; k < count; ++k) {
        int d = s[k].photo - 1 - i, far = pick ? pick->photo - 1 - i : 0;
        if (s[k].photo == i + 1) return &s[k];
        if (!pick || (pick->photo && (!s[k].photo || d * d > far * far))) pick = &s[k];
    }
    if (!pick->bm && !(pick->bm = bitmap_create_ex(box, box, box * 4, 1 /* RGBA8888 */))) return 0;
    pick->photo = i + 1, pick->w = pick->h = 0;
    char url[80] = "file://";
    unsigned bitmap[64]; /* bitmap_t */
    cache_path(url + 7, ph.key[i], suffix);
    if (widget_load_image(ph.page, url, bitmap)) return pick;
    unsigned w = bitmap[0], h = bitmap[1], format = ((unsigned short *)bitmap)[7] - 1u;
    const unsigned char *src =
        format < 4 && w && h && w <= 4096 && h <= 4096 ? bitmap_lock_buffer_for_read(bitmap) : 0;
    unsigned *dst = src ? (unsigned *)bitmap_lock_buffer_for_write(pick->bm) : 0;
    if (dst) {
        int size[2];
        photo_orient(src, (int)w, (int)h, (int)bitmap_get_line_length(bitmap), at[format],
                     ph.state[i], dst, box, size);
        pick->w = size[0], pick->h = size[1];
        bitmap_unlock_buffer(pick->bm);
        *(unsigned short *)((char *)pick->bm + 0xc) |= 1; /* BITMAP_FLAG_OPAQUE */
    }
    if (src) bitmap_unlock_buffer(bitmap);
    widget_unload_image(ph.page, bitmap);
    return pick;
}

/* w's text isn't s. AWTK keeps a widget's text as UTF-32, which widget_get_prop_str(w, "text")
 * never returns, so a compare through it always differs and every set repaints. */
int text_differs(void *w, const char *s) {
    char cur[512] = ""; /* stock writes nothing when it doesn't fit */
    return widget_get_text_utf8(w, cur, sizeof cur) || strcmp(cur, s);
}

/* The bottom caption of a page h high: above the rounded glass's bottom corners, dark under white
 * so it reads over any photo. Shared with books.c. */
void *bottom_caption(void *parent, int h) {
    void *label = text(parent, CF_EDGE, h - 56, 375 - 2 * CF_EDGE, 36);
    widget_set_prop_int(label, "style:normal:bg_color", (int)0xb3000000u);
    widget_set_prop_int(label, "style:normal:round_radius", 8);
    return label;
}

static int ready(int i) { return i >= 0 && i < ph.n && ph.state[i] - 1u < 8; }

/* The viewer's photo i, round the ends as the slide_menu wraps. */
static slot_t *shot(int i) {
    i = (i % ph.n + ph.n) % ph.n;
    return ready(i) ? slot(ph.shots, 3, PH_SHOT_W, i, ".jpg") : 0;
}

/* The slot's image fitted into the box at x, y; 0 when there is none to draw. */
static int draw(void *canvas, slot_t *s, int x, int y, int bw, int bh) {
    int src[4] = { 0, 0, s ? s->w : 0, s ? s->h : 0 }, r[4];
    if (!src[2]) return 0;
    photo_place(src[2], src[3], x, y, bw, bh, r);
    canvas_draw_image(canvas, s->bm, src, r);
    return 1;
}

/* ringnav_paint calls this for every widget after stock painted it: a grid tile gets its
 * thumbnail (grey until there is one), the viewer's slide_menu the photo at its live offset and,
 * mid-slide, the one coming in. */
void photos_paint(void *w, void *canvas) {
    if (!ph.page || !w) return;
    if (ph.screen == GRID && P(w, W_PARENT) == ph.view) {
        int i = I(w, W_Y) / PH_TILE_H * PH_COLS + (I(w, W_X) - PH_GRID_X) / PH_TILE_W;
        int x = (PH_TILE_W - PH_THUMB_W) / 2, y = (PH_TILE_H - PH_THUMB_H) / 2;
        if (ready(i) && draw(canvas, slot(ph.tiles, PH_TILE_SLOTS, PH_THUMB_W, i, "t.jpg"), x, y,
                             PH_THUMB_W, PH_THUMB_H))
            return;
        unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
        canvas_set_fill_color(canvas, PH_GREY);
        canvas_fill_rect(canvas, x, y, PH_THUMB_W, PH_THUMB_H);
        stock_canvas_set_fill_color(canvas, fill);
    } else if (ph.screen == VIEWER && w == ph.slide) {
        int c, frac, stride = visual(w, ph.n, &c, &frac), h = I(w, W_H);
        if (!stride) return;
        ph.want = c;
        int dx = -(frac * stride >> 16), next = frac >= 0 ? 1 : -1;
        draw(canvas, shot(c), dx, 0, PH_SHOT_W, h);
        if (frac)
            draw(canvas, shot(c + next), dx + next * stride, 0, PH_SHOT_W, h);
        else /* at rest: both neighbours decoded before the next slide */
            shot(c + 1), shot(c - 1);
    }
}

/* Local Music's Photos row (navigation.c media_click): the albums, or the grid when there are none. */
void photos_open(const char *root) {
    if (ph.page) return;
    void *page = ph.page = page_open("photos_page", closed, keyup);
    if (!page) return;
    tk_snprintf(ph.root, sizeof ph.root, "%s", root);
    void *f = widget_factory();
    int h = widget_get_prop_int(page, "h", PH_SHOT_H);
    ph.albums = widget_factory_create_widget(f, "view", page, 0, 0, 375, h);
    ph.grid = widget_factory_create_widget(f, "view", page, 0, 0, 375, h);
    ph.viewer = widget_factory_create_widget(f, "view", page, 0, 0, 375, h);
    widget_set_visible(ph.grid, 0, 0);
    widget_set_visible(ph.viewer, 0, 0);
    ph.album = calloc(ALBUMS_MAX, sizeof *ph.album);
    if (ph.album) scan(root, ph.album, &ph.nalbums, ALBUMS_MAX, 1);
    if (!ph.nalbums) {
        ph.target = 0; /* All Photos, which is just the folder's own */
        to_grid(0);
        return;
    }
    ph.screen = ALBUMS;
    void *view = page_list(page, ph.albums, 0, "照片", ph.nalbums + 1, 48);
    page_row_into(view, 0, "所有照片", open_album);
    for (int k = 0; k < ph.nalbums; ++k) page_row_into(view, k + 1, ph.album[k], open_album);
}
