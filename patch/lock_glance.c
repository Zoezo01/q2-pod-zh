/* At a Glance (docs/internals.md#lock-screen): the Lock Screen's second layout (Display > Lock
 * Screen > Layout, POD_LOCKLAYOUT), taken as each lock window opens. Stock's lock window and its
 * widgets, laid out again through the theme engine (theme()->glance, lock.glance in Minimal's
 * theme.xml, every theme's alike): the time top left and the date top right; with a queue, stock's
 * song card across the screen with its cover square, the song's own lines under the title and
 * artist, the playing cover blurred behind it all; without one, the chosen picture, or on black the
 * time large in the middle; and slide to unlock, the one thing a finger moves. With a queue the
 * clock held turns the song's view to the picture's and back. lockscreen.c hands the window over
 * (glance_init) and calls the rest; ringnav_input hands it the touches (lockscreen_touch). */
#include "peq.h"
#include "offsets.inc"
#include "stock.h"
#include "theme.h"

extern const int *clock_now(int *buf);       /* theme_engine.c: the local time, a struct tm */
extern unsigned flip_if(int on, unsigned c); /* theme_engine.c: a colour as Light Mode flips it */
extern const char *now_tag(void *r, int field);
extern void *queue_now(unsigned *pos, unsigned *n); /* coverflow.c: the playing record, or 0 */

enum { GL_SONG, GL_CLOCK, GL_PICTURE }; /* what the screen shows: gl.mode */
#define GL_W 375
#define GL_H 320

static struct {
    widget_t *win, *img; /* the lock window and its picture (img_screensaver), where it paints */
    /* the song's, in stock's card (view_playinfo), and the clock's */
    widget_t *card, *cover, *title, *artist, *album, *place, *count;
    widget_t *time, *ampm, *day, *date;
    void *full; /* the built-in picture lockscreen.c rendered, 0 for none */
    int picture, mode, minute, queue;
    unsigned timer;
    /* slide to unlock: its view, the knob's px along the track, the shine's px along its run; a
     * finger on the knob (drag, drag_dx its x less the knob's), and unlocking under way (step) */
    widget_t *slider;
    int knob, shine, drag, drag_dx, step;
    unsigned shine_timer;
    /* the clock held with a queue turns the song's view to the picture's and back (flip): hold
     * while a finger rests there since (hold_x, hold_y); then the veil, a view over everything,
     * darkens the old view, the view changes under it (turned), and it lifts off the new one, fade
     * its black's alpha */
    widget_t *veil;
    int flip, hold, hold_x, hold_y, fade, turned;
    unsigned hold_timer, fade_timer, fade_at;
    /* the playing cover blurred, GL_W x GL_H, kept between windows: made for np_art_key() blur_key
     * from the image blur_name ("": none made yet) */
    void *blur;
    unsigned blur_key;
    char blur_name[256];
} gl __attribute__((section(".scratch")));

static const glance_t *style(void) { return &theme()->glance; }

/* The picture widget goes: nothing here may match a later widget at its address. */
static int forget(void *ctx, event_t *event) {
    (void)event;
    if (ctx != gl.img) return 0;
    gl.win = gl.img = gl.card = gl.cover = gl.title = gl.artist = gl.album = gl.place = 0;
    gl.count = gl.time = gl.ampm = gl.day = gl.date = gl.slider = gl.veil = 0;
    gl.full = 0;
    gl.knob = gl.drag = gl.step = gl.hold = gl.fade = 0;
    stop_timer(&gl.timer);
    stop_timer(&gl.shine_timer);
    stop_timer(&gl.hold_timer);
    stop_timer(&gl.fade_timer);
    return 0;
}

/* A label: its box, size and side, white at alpha a in either half (text_ink). */
static void put(widget_t *w, int x, int y, int ww, int h, int px, const char *face,
                const char *align, unsigned a) {
    if (!w) return;
    slider_label(w, x, y, ww, h, px, face);
    widget_set_prop_str(w, "style:normal:text_align_h", align);
    text_ink(w, a << 24 | 0xffffffu, 1);
}
static widget_t *new_label(widget_t *parent) {
    return label(parent, 0, 0, 10, 10, "s_label_white20c", 20, 0xffffffffu);
}
/* A label's face for its text, any script (theme_face), at px: set again only when it changes. */
static void face(widget_t *w, int px) {
    if (!w) return;
    const char *f = theme_face(widget_get_text(w), -1, &px);
    /* a face is one of theme_face's literals: the payload's .rodata fits the low 24 bits */
    int key = px << 24 | (int)(long)f;
    if (widget_get_prop_int(w, "_ringnav_box", 0) == key) return;
    widget_set_prop_int(w, "_ringnav_box", key);
    widget_set_prop_str(w, "style:normal:font_name", f ? f : "default");
    widget_set_prop_int(w, "style:normal:font_size", px);
}
/* A box filled in the widget's own pixels, 0xAARRGGBB as it is to look: Light Mode flips what is
 * painted, so there it is given flipped (as text_ink gives a label's colour). */
static void box(canvas_t *canvas, int x, int y, int w, int h, unsigned color, unsigned radius) {
    rect_t r = { x, y, w, h };
    unsigned ink = RGBA(flip_if(ipod_light(), color & 0xffffffu)) & 0xffffffu;
    fill_box(canvas, &r, ink | (color & 0xff000000u), radius);
}

/* The playing cover (the lock window's own img_playcover image, as stock loaded it) blurred into
 * gl.blur, once per cover: sampled onto a grid of 8 px cells from a square blur_px wide centred
 * behind the screen, the grid smoothed twice with its neighbours, then drawn back bilinearly and
 * dimmed to dim of 256. Shapes and colours stay; nothing in it is sharp. 0 when the cover can't be
 * read: the screen is black then.
 * ponytail: the bitmap, about 470 KB, is kept for the life of demo, as the built-in picture is. */
#define GL_GW 47 /* the grid: the screen at a cell each 8 px */
#define GL_GH 40
static int blur_cover(void) {
    static const unsigned char at[4][4] = BITMAP_RGBA_AT;
    const glance_t *g = style();
    const char *name = gl.cover ? widget_get_prop_str(gl.cover, "image", "") : "";
    if (!name || !*name || strlen(name) >= sizeof gl.blur_name) return gl.blur_name[0] = 0;
    if (gl.blur && !strcmp(name, gl.blur_name)) return 1;
    gl.blur_name[0] = 0;
    unsigned bitmap[64]; /* bitmap_t: width @0, height @4, format @14 */
    memset(bitmap, 0, sizeof bitmap);
    /* the player writes every song's cover to the same file: the cached bitmap is the last one's */
    image_manager_unload_bitmap_by_name(image_manager(), name);
    if (widget_load_image(gl.cover, name, bitmap)) return 0;
    unsigned w = bitmap[0], h = bitmap[1], format = ((unsigned short *)bitmap)[7] - 1u;
    if (format >= 4 || w < 8 || h < 8) return 0;
    unsigned short (*grid)[GL_GW][3] = calloc(2 * GL_GH, sizeof *grid);
    unsigned short (*t)[GL_GW][3] = grid ? grid + GL_GH : 0;
    if (!gl.blur) gl.blur = bitmap_create_ex(GL_W, GL_H, GL_W * 4, 1 /* RGBA8888 */);
    const unsigned char *data = grid && gl.blur ? bitmap_lock_buffer_for_read(bitmap) : 0;
    if (!data) {
        free(grid);
        return 0;
    }
    const unsigned char *o = at[format];
    unsigned stride = bitmap_get_line_length(bitmap);
    int px = g->blur_px < GL_W ? GL_W : g->blur_px; /* the square covers the screen */
    for (int j = 0; j < GL_GH; ++j)
        for (int i = 0; i < GL_GW; ++i) {
            unsigned sum[3] = { 0, 0, 0 };
            for (int k = 0; k < 4; ++k) { /* four points in the cell */
                int sx = i * 8 + 2 + (k & 1) * 4 + (px - GL_W) / 2,
                    sy = j * 8 + 2 + (k >> 1) * 4 + (px - GL_H) / 2;
                unsigned u = (unsigned)sx * w / (unsigned)px, v = (unsigned)sy * h / (unsigned)px;
                const unsigned char *p =
                    data + (v < h ? v : h - 1) * stride + (u < w ? u : w - 1) * 4;
                sum[0] += p[o[0]], sum[1] += p[o[1]], sum[2] += p[o[2]];
            }
            for (int c = 0; c < 3; ++c) grid[j][i][c] = (unsigned short)(sum[c] / 4);
        }
    bitmap_unlock_buffer(bitmap);
    for (int pass = 0; pass < 2; ++pass) {
        for (int j = 0; j < GL_GH; ++j)
            for (int i = 0; i < GL_GW; ++i)
                for (int c = 0; c < 3; ++c) {
                    unsigned sum = 0, n = 0;
                    for (int y = j - 1; y <= j + 1; ++y)
                        for (int x = i - 1; x <= i + 1; ++x)
                            if (y >= 0 && y < GL_GH && x >= 0 && x < GL_GW)
                                sum += grid[y][x][c], ++n;
                    t[j][i][c] = (unsigned short)(sum / n);
                }
        memcpy(grid, t, GL_GH * sizeof *grid);
    }
    unsigned *d = (unsigned *)bitmap_lock_buffer_for_write(gl.blur);
    unsigned line = d ? bitmap_get_line_length(gl.blur) / 4 : 0;
    for (int y = 0; d && y < GL_H; ++y) {
        int gy = y * 256 / 8 - 128, j = gy < 0 ? 0 : gy >> 8, fy = gy < 0 ? 0 : gy & 255;
        int j1 = j + 1 < GL_GH ? j + 1 : GL_GH - 1;
        if (j >= GL_GH) j = GL_GH - 1;
        for (int x = 0; x < GL_W; ++x) {
            int gx = x * 256 / 8 - 128, i = gx < 0 ? 0 : gx >> 8, fx = gx < 0 ? 0 : gx & 255;
            int i1 = i + 1 < GL_GW ? i + 1 : GL_GW - 1;
            if (i >= GL_GW) i = GL_GW - 1;
            unsigned pixel = 0xff000000u;
            for (int c = 0; c < 3; ++c) {
                int top = grid[j][i][c] * (256 - fx) + grid[j][i1][c] * fx,
                    bottom = grid[j1][i][c] * (256 - fx) + grid[j1][i1][c] * fx;
                unsigned v = (unsigned)(((top * (256 - fy) + bottom * fy) >> 16) * g->dim) >> 8;
                pixel |= (v > 255 ? 255 : v) << (8 * c);
            }
            d[(unsigned)y * line + (unsigned)x] = pixel;
        }
    }
    if (d) bitmap_unlock_buffer(gl.blur);
    free(grid);
    if (!d) return 0;
    *(unsigned short *)((char *)gl.blur + 0xc) |= 1; /* BITMAP_FLAG_OPAQUE */
    memcpy(gl.blur_name, name, strlen(name) + 1);
    return 1;
}

/* The time and the date where the mode has them: top left and right, or, on black with no song,
 * large in the middle with the date under it. A digit is about 0.56 of the size wide in stock's
 * face, the colon half that; AM or PM follows the time. */
static void clock_place(void) {
    static const char *const days[] = { "Sunday",   "Monday", "Tuesday", "Wednesday",
                                        "Thursday", "Friday", "Saturday" };
    static const char *const months[] = { "January",   "February", "March",    "April",
                                          "May",       "June",     "July",     "August",
                                          "September", "October",  "November", "December" };
    const glance_t *g = style();
    int buf[11];
    const int *tm = clock_now(buf);
    int hour = tm ? tm[2] : 0, min = tm ? tm[1] : 0, h12 = !g_time24h_flag;
    char s[48];
    tk_snprintf(s, sizeof s, tm ? "%d:%02d" : "--:--", h12 ? (hour + 11) % 12 + 1 : hour, min);
    if (gl.time) widget_set_text_utf8(gl.time, s);
    if (gl.ampm) widget_set_text_utf8(gl.ampm, !tm || !h12 ? "" : hour < 12 ? "AM" : "PM");
    int big = gl.mode == GL_CLOCK, px = big ? g->big_px : g->time_px;
    int apx = big ? g->big_ampm_px : g->ampm_px, digits = (int)strlen(s) - 1;
    int tw = tm ? digits * px * 56 / 100 + px * 28 / 100 : px * 2;
    int pm = h12 ? (apx * 10 + 40) / 7 : 0; /* AM or PM's room */
    if (big) {
        int x = (GL_W - tw - (pm ? pm + 6 : 0)) / 2;
        put(gl.time, x - 4, g->big_y, tw + 8, px + 8, px, "default", "center", 0xff);
        put(gl.ampm, x + tw + 6, g->big_ampm_y, pm + 4, apx + 4, apx, "default", "left", 0xff);
        if (tm) tk_snprintf(s, sizeof s, "%s, %s %d", days[tm[6] % 7], months[tm[4] % 12], tm[3]);
        if (gl.day) widget_set_text_utf8(gl.day, tm ? s : "");
        if (gl.date) widget_set_text_utf8(gl.date, "");
        put(gl.day, 0, g->big_day_y, GL_W, g->big_day_px + 8, g->big_day_px, "default", "center",
            (unsigned)g->day_a);
        put(gl.date, 0, 0, 1, 1, g->date_px, "default", "right", 0xff);
    } else {
        put(gl.time, g->x, g->time_y, tw + 8, px + 8, px, "default", "left", 0xff);
        put(gl.ampm, g->x + tw + 6, g->ampm_y, pm + 4, apx + 5, apx, "default", "left", 0xff);
        if (gl.day) widget_set_text_utf8(gl.day, tm ? days[tm[6] % 7] : "");
        if (tm) tk_snprintf(s, sizeof s, "%s %d", months[tm[4] % 12], tm[3]);
        if (gl.date) widget_set_text_utf8(gl.date, tm ? s : "");
        put(gl.day, g->right - 170, g->date_y, 170, g->date_px + 5, g->date_px, "default", "right",
            0xff);
        put(gl.date, g->right - 170, g->date_y + g->date_gap, 170, g->date_px + 5, g->date_px,
            "default", "right", 0xff);
    }
    gl.minute = tm ? hour * 60 + min : -1;
}

/* The song's own lines: the album, how far in, and its place in the queue. */
static void song_lines(void) {
    char s[64], had[300];
    unsigned pos = 0, n = 0;
    void *r = queue_now(&pos, &n);
    const char *album = r ? now_tag(r, REC_ALBUM) : 0;
    /* each second while a song plays: the album written again only when it changed, so its
     * marquee keeps its rests at either end */
    const char *want = album && strcmp(album, "(null)") ? album : "";
    if (gl.album && (widget_get_text_utf8(gl.album, had, sizeof had) || strcmp(had, want)))
        widget_set_text_utf8(gl.album, want);
    int sec = 0, total = 0;
    mclGetPlayTime(&sec, &total);
    if (sec < 0) sec = 0;
    if (total < 0) total = 0;
    tk_snprintf(s, sizeof s, "%d:%02d / %d:%02d", sec / 60, sec % 60, total / 60, total % 60);
    if (gl.place) widget_set_text_utf8(gl.place, s);
    tk_snprintf(s, sizeof s, n ? "%u of %u" : "", pos + 1, n);
    if (gl.count) widget_set_text_utf8(gl.count, s);
}

/* What the screen shows now: the song with a queue (stock shows its card then) unless flipped,
 * else the chosen picture, else the clock on black. The picture's view shows only the picture (or
 * the clock) and the slider. A change lays the clock out again and repaints. */
static void sync(int force) {
    if (!gl.img) return;
    unsigned pos = 0, n = 0;
    queue_now(&pos, &n);
    int queue = gl.card && n > 0, song = queue && !gl.flip;
    if (!queue) gl.flip = 0; /* nothing to flip from */
    if (gl.card && widget_get_visible(gl.card) != song) widget_set_visible(gl.card, song, 0);
    if (queue != gl.queue) gl.queue = queue, force = 1;
    int mode = song ? GL_SONG : gl.picture ? GL_PICTURE : GL_CLOCK;
    int buf[11];
    const int *tm = clock_now(buf);
    int minute = tm ? tm[2] * 60 + tm[1] : -1, changed = force || mode != gl.mode;
    gl.mode = mode;
    if (changed || minute != gl.minute) clock_place();
    if (song) {
        int had = gl.blur_name[0];
        const char *name = gl.cover ? widget_get_prop_str(gl.cover, "image", "") : "";
        /* another song, or its cover file written since (np_art_key holds the file's size and
         * time): the blur kept from before, whose file name every song's cover shares, is made
         * again, so it never shows an earlier song's */
        unsigned key = np_art_key();
        if (key && key != gl.blur_key) gl.blur_key = key, gl.blur_name[0] = 0, changed = 1;
        if (strcmp(name ? name : "", gl.blur_name)) changed |= blur_cover() || had;
        song_lines();
    }
    if (changed) widget_invalidate_force(gl.win, 0);
}

/* The shine that crosses "slide to unlock": a step of shine_step px each shine_ms while the screen
 * is lit, the slider alone repainted. It stops with the screen, and glance_poll starts it again. */
static unsigned shine_ms(void) { return style()->shine_ms > 0 ? (unsigned)style()->shine_ms : 1; }
static int shine(const void *info) {
    const glance_t *g = style();
    (void)info;
    if (!g_backlight_status || !gl.slider) {
        gl.shine_timer = 0;
        return 0; /* RET_OK: a timer's end */
    }
    gl.shine = (gl.shine + g->shine_step) % (g->shine_run > 0 ? g->shine_run : 1);
    widget_invalidate_force(gl.slider, 0);
    return 8; /* RET_REPEAT */
}

/* A look each second with the screen lit. With it dark the timer stops, for the battery, and
 * glance_poll starts it again at the first loop pass lit. */
static int tick(const void *info) {
    (void)info;
    if (!g_backlight_status || !gl.img) {
        gl.timer = 0;
        return 0; /* RET_OK: a timer's end */
    }
    sync(0);
    return 8; /* RET_REPEAT */
}
/* info_poll's, each loop pass: each timer looked at on its own, as a lock window opens with the
 * screen still dark, and the shine's first step can end it while the slower tick lives on. */
void glance_poll(void) {
    if (!gl.img || !g_backlight_status) return;
    if (!gl.shine_timer) rearm(&gl.shine_timer, shine, shine_ms());
    if (gl.timer) return;
    sync(0);
    rearm(&gl.timer, tick, 1000);
}

/* lockscreen_init under At a Glance, after stock built the window: img its picture, picture 1 for
 * the user's own or a built-in but Black, full the built-in's rendering. Stock's clocks, battery,
 * shade and slide hint hide; its song card (stock shows it only with a queue) takes the screen's
 * width under the time; the time, date, the song's other lines, the slider and the flip's veil are
 * added. */
void glance_init(widget_t *win, widget_t *img, int picture, void *full) {
    const glance_t *g = style();
    static const char *const hidden[] = { "view_timeinfo", "clock_time",  "clock_date",
                                          "label_battery", "img_battery", "pgbar_battery",
                                          "view_shade",    "img_slide" };
    for (unsigned i = 0; i < sizeof hidden / sizeof *hidden; ++i) {
        widget_t *w = widget_lookup(win, hidden[i], 1);
        if (w) widget_set_visible(w, 0, 0);
    }
    gl.win = win, gl.img = img, gl.picture = picture, gl.full = full;
    widget_on(img, EVT_DESTROY, forget, img);
    widget_t *top = P(img, W_PARENT), *card = widget_lookup(win, "view_playinfo", 1);
    gl.card = card;
    gl.cover = widget_lookup(win, "img_playcover", 1);
    gl.title = widget_lookup(win, "scrlabel_title", 1);
    gl.artist = widget_lookup(win, "scrlabel_artist", 1);
    gl.album = gl.place = gl.count = 0;
    if (card) {
        widget_move_resize(card, 0, g->card_y, GL_W, g->card_h);
        widget_set_prop_int(card, "style:normal:bg_color", 0);
        widget_set_prop_int(card, "style:normal:round_radius", 0);
        gl.album = new_label(card), gl.place = new_label(card), gl.count = new_label(card);
    }
    if (gl.cover) { /* square: stock rounds this one */
        widget_move_resize(gl.cover, g->x, g->cover_y, g->cover_px, g->cover_px);
        widget_set_prop_int(gl.cover, "style:normal:round_radius", 0);
        widget_set_prop_int(gl.cover, "style:pressed:round_radius", 0);
        widget_set_prop_int(gl.cover, "style:selected:round_radius", 0);
    }
    int tx = g->text_x, tw = g->right - tx;
    unsigned a = (unsigned)g->text_a;
    /* the title and artist 6 px over their fonts, the rest 22 high (a label draws nothing in a box
     * shorter than its font); the place and count 90 wide, at either end */
    put(gl.title, tx, g->title_y, tw, g->title_px + 6, g->title_px, "classic-light", "left", 0xff);
    put(gl.artist, tx, g->artist_y, tw, g->artist_px + 6, g->artist_px, "default", "left", 0xff);
    put(gl.album, tx, g->album_y, tw, 22, g->album_px, "default", "left", a);
    put(gl.place, tx, g->place_y, 90, 22, g->place_px, "default", "left", a);
    put(gl.count, g->right - 90, g->place_y, 90, 22, g->place_px, "default", "right", a);
    gl.time = new_label(top), gl.ampm = new_label(top), gl.day = new_label(top);
    gl.date = new_label(top);
    gl.slider = widget_factory_create_widget(widget_factory(), "view", top, g->x, g->track_y,
                                             g->right - g->x, g->track_h);
    /* the flip's veil, over everything, last: it takes no touch */
    gl.veil = widget_factory_create_widget(widget_factory(), "view", top, 0, 0, GL_W, GL_H);
    if (gl.veil) widget_set_sensitive(gl.veil, 0);
    gl.knob = gl.shine = gl.drag = gl.step = gl.hold = gl.fade = 0;
    gl.mode = -1, gl.queue = -1;
    rearm(&gl.shine_timer, shine, shine_ms());
    sync(1);
    rearm(&gl.timer, tick, 1000);
}

/* The lock window's cover changed (lockscreen_cover, stock's same file name): the blur is the old
 * cover's, made again from this one. */
void glance_cover(widget_t *cover) {
    if (!gl.img || cover != gl.cover) return;
    gl.blur_name[0] = 0;
    sync(1);
}

/* The picture: the blurred cover with a song (black while it can't be made); without one the
 * built-in or the user's own picture (stock drew that) with a shade under the time and date fading
 * out, or black. */
static void paint_picture(canvas_t *canvas, const glance_t *g) {
    int r[4] = { 0, 0, GL_W, GL_H };
    face(gl.title, g->title_px); /* the artist and the album stay in stock's face, any script */
    if (gl.mode == GL_SONG && gl.blur && gl.blur_name[0])
        canvas_draw_image(canvas, gl.blur, r, r);
    else if (gl.mode == GL_PICTURE && gl.full)
        canvas_draw_image(canvas, gl.full, r, r);
    else if (gl.mode != GL_PICTURE)
        box(canvas, 0, 0, GL_W, GL_H, 0xff000000u, 0);
    if (gl.mode == GL_PICTURE && g->shade_h > 1) /* 2 px rows, each lighter */
        for (int i = 0; i < g->shade_h / 2; ++i)
            box(canvas, 0, i * 2, GL_W, 2,
                (unsigned)(g->shade_a - i * 2 * g->shade_a / g->shade_h) << 24, 0);
}

/* The slider: its see-through track, the knob where the finger has it, the words right of the
 * knob's rest, dim white fading as the knob leaves, and over them the shine: the same words
 * brighter inside its bands, the innermost brightest; then the knob's arrow, a shaft and a head. */
static void paint_slider(canvas_t *canvas, const glance_t *g) {
    static const unsigned words[] = { 's', 'l', 'i', 'd', 'e', ' ', 't', 'o',
                                      ' ', 'u', 'n', 'l', 'o', 'c', 'k' };
    unsigned n = sizeof words / sizeof *words;
    int tw = g->right - g->x, th = g->track_h, kw = g->knob_w, kh = g->knob_h;
    int x = g->knob_in + gl.knob, y = (th - kh) / 2;
    box(canvas, 0, 0, tw, th, (unsigned)g->rim_a << 24 | 0xffffffu, 11); /* its rim */
    box(canvas, 1, 1, tw - 2, th - 2, (unsigned)g->track_a << 24, 10);
    box(canvas, x, y, kw, kh, 0xff000000u | g->knob_edge, 8);
    box(canvas, x + 1, y + 1, kw - 2, kh - 2, 0xff000000u | g->knob, 7);
    box(canvas, x + 2, y + kh / 2, kw - 4, kh / 2 - 2, 0xff000000u | g->knob_low, 6);
    box(canvas, x + 2, y + kh / 2, kw - 4, 6, 0xff000000u | g->knob_low, 0);
    int fade =
        g->words_fade <= 0 || gl.knob >= g->words_fade ? 0 : 255 - gl.knob * 255 / g->words_fade;
    unsigned ink = (unsigned)I(P(canvas, CANVAS_LCD), LCD_TEXT_COLOR);
    canvas_set_font(canvas, (void *)0, (unsigned)g->words_px);
    int text = (int)(canvas_measure_text(canvas, words, n) + 0.5f);
    int room = g->knob_in + kw + 2;
    int tx = room + (tw - room - g->knob_in - text) / 2, ty = (th - g->words_px) / 2;
    if (fade) {
        /* white as it is to look: Light Mode flips painted text too (box) */
        unsigned white = RGBA(flip_if(ipod_light(), 0xffffffu)) & 0xffffffu;
        canvas_set_text_color(canvas, (unsigned)(g->words_a * fade / 255) << 24 | white);
        canvas_draw_text(canvas, words, n, tx, ty);
        int at = tx - g->shine_from + gl.shine; /* the shine's middle; past the words it rests */
        for (int b = 0; b < (int)sizeof g->shine_w && g->shine_w[b]; ++b) {
            int bw = g->shine_w[b], bx = at - bw / 2, old[4], clip[4];
            if (bx < tx) bw -= tx - bx, bx = tx;
            if (bx + bw > tx + text) bw = tx + text - bx;
            if (bw <= 0 || !clip_within(canvas, old, clip, I(canvas, CANVAS_X) + bx,
                                        I(canvas, CANVAS_Y), bw, th))
                continue;
            canvas_set_clip_rect(canvas, clip);
            canvas_set_text_color(canvas, (unsigned)(g->shine_a[b] * fade / 255) << 24 | white);
            canvas_draw_text(canvas, words, n, tx, ty);
            canvas_set_clip_rect(canvas, old);
        }
    }
    stock_canvas_set_text_color(canvas, ink);
    int cx = x + kw / 2, cy = y + kh / 2;
    box(canvas, cx - 14, cy - 3, 16, 6, 0xff000000u | g->arrow, 0);
    for (int i = 0; i < 10; ++i)
        box(canvas, cx + 2 + i, cy - 9 + i, 1, 19 - 2 * i, 0xff000000u | g->arrow, 0);
}

/* lockscreen_paint, for every widget: 1 when w is one of At a Glance's and painted here. The veil
 * is the flip's black, at its alpha, over the whole window. */
int glance_paint(widget_t *w, canvas_t *canvas) {
    if (!w || !gl.img || (w != gl.img && w != gl.slider && w != gl.veil)) return 0;
    void *lcd = canvas ? P(canvas, CANVAS_LCD) : 0;
    if (!lcd) return 1;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    if (w == gl.img)
        paint_picture(canvas, style());
    else if (w == gl.slider)
        paint_slider(canvas, style());
    else if (gl.fade > 0) { /* black as it is: stock's setter, Light Mode's ink held off */
        stock_canvas_set_fill_color(canvas, (unsigned)(gl.fade > 255 ? 255 : gl.fade) << 24);
        canvas_fill_rect(canvas, 0, 0, GL_W, GL_H);
    }
    stock_canvas_set_fill_color(canvas, fill);
    return 1;
}

/* The flip, a step each 16 ms: the veil darkens the old view over fade_ms, the view
 * turns once at the darkest, and the veil lifts off the new one over fade_ms. Never the window's
 * own opacity, which let the page under the lock window show through. */
static int fade_step(const void *info) {
    const glance_t *g = style();
    unsigned ms = g->fade_ms > 0 ? (unsigned)g->fade_ms : 1, t = time_now_ms() - gl.fade_at;
    (void)info;
    if (!gl.img || !gl.veil) {
        gl.fade_timer = 0, gl.fade = 0;
        return 0; /* RET_OK: a timer's end */
    }
    if (t >= ms && !gl.turned) { /* the turn, once, at the darkest, however late the step */
        gl.turned = 1;
        gl.flip = !gl.flip;
        sync(1);
    }
    if (t < ms)
        gl.fade = (int)(t * 255 / ms);
    else if (t < 2 * ms)
        gl.fade = 255 - (int)((t - ms) * 255 / ms);
    else {
        gl.fade = 0;
        gl.fade_timer = 0;
        widget_invalidate_force(gl.win, 0);
        return 0;
    }
    widget_invalidate_force(gl.win, 0);
    return 8; /* RET_REPEAT */
}
/* The clock held hold_ms with a queue: the other view, through black. */
static int turn(const void *info) {
    (void)info;
    gl.hold_timer = 0;
    if (!gl.hold || !gl.img || gl.queue != 1) return 0;
    gl.hold = 0;
    reset_poweroptions_timer(1, 1, 1);
    if (!gl.veil) { /* no veil: the turn alone */
        gl.flip = !gl.flip;
        sync(1);
        return 0;
    }
    gl.fade_at = time_now_ms();
    gl.turned = 0;
    gl.fade = 1;
    stop_timer(&gl.fade_timer);
    gl.fade_timer = timer_add(fade_step, (void *)0, 16);
    return 0;
}
/* Unlock: stock's own handler for its lock window's slide (saver_unlock: it ends the window's timer
 * and closes it, and the window's close clears g_lockscreen_pageflag), called a moment after the
 * touch, outside its dispatch. */
static int unlock(const void *info) {
    (void)info; /* a timer hands its own record, not the context */
    if (gl.win) saver_unlock(gl.win, (void *)0);
    return 0; /* RET_OK: this timer's end */
}

#define GL_POINTER_MOVE 0x101 /* the window manager's move; EVT_POINTER_FIRST is its down */
/* ringnav_input, for a pointer event with the lock screen up: 1 when At a Glance's lock window, or
 * the volume panel over it (ringnav_input closed it on the down), is in front, and stock never
 * sees it; 0 for anything else, a prompt over the lock screen (Shut Down?) or Default's window.
 * Only the knob takes a finger, within grab px of it: it follows the finger along the track,
 * springs back when let go short of done per mille of its travel, and past it unlocks. The clock
 * held hold_ms with a queue flips the view; a finger moving hold_slop px lets it go. */
int lockscreen_touch(widget_t *wm, event_t *e, unsigned type) {
    if (!gl.img || !gl.slider || !e) return 0;
    widget_t *front = window_manager_get_top_window(wm);
    if (front != gl.win &&
        (!front || tk_strcmp(widget_get_prop_str(front, "name", ""), "volume_dialog")))
        return 0;
    const glance_t *g = style();
    /* an input event's place is the panel's, a quarter turn from the screen's: x up the screen
     * from its bottom edge, y across it */
    int x = I(e, EVENT_Y), y = GL_H - 1 - I(e, EVENT_X);
    int travel = g->right - g->x - g->knob_w - 2 * g->knob_in;
    if (gl.step) return 1; /* unlocking */
    if (travel < 1) travel = 1;
    if (type == EVT_POINTER_FIRST) {
        int kx = g->x + g->knob_in + gl.knob, ky = g->track_y + (g->track_h - g->knob_h) / 2;
        gl.drag = x >= kx - g->grab && x < kx + g->knob_w + g->grab && y >= ky - g->grab &&
                  y < ky + g->knob_h + g->grab;
        gl.drag_dx = x - kx;
        if (gl.drag) reset_poweroptions_timer(1, 1, 1);
        /* the clock, top left, or large in the middle with no picture */
        gl.hold = !gl.drag && gl.queue == 1 &&
                  (gl.mode == GL_CLOCK ? y >= g->big_hold_y && y < g->big_hold_y + g->big_hold_h
                                       : x < g->hold_w && y < g->hold_h);
        gl.hold_x = x, gl.hold_y = y;
        stop_timer(&gl.hold_timer);
        if (gl.hold) gl.hold_timer = timer_add(turn, (void *)0, (unsigned)g->hold_ms);
    } else if (type == GL_POINTER_MOVE && gl.hold) {
        int dx = x - gl.hold_x, dy = y - gl.hold_y;
        if (dx > g->hold_slop || -dx > g->hold_slop || dy > g->hold_slop || -dy > g->hold_slop)
            gl.hold = 0;
    } else if (type == GL_POINTER_MOVE && gl.drag) {
        int at = x - gl.drag_dx - g->x - g->knob_in;
        gl.knob = at < 0 ? 0 : at > travel ? travel : at;
    } else if (type != GL_POINTER_MOVE) {
        /* up, or the touch was lost: either way a knob past done is a slide meant */
        int done = gl.drag && gl.knob >= travel * g->done / 1000;
        gl.hold = 0;
        stop_timer(&gl.hold_timer);
        gl.drag = 0;
        gl.knob = done ? travel : 0;
        if (done) gl.step = 1, timer_add(unlock, (void *)0, (unsigned)g->open_ms);
    }
    widget_invalidate_force(gl.slider, 0);
    return 1;
}
