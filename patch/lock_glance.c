/* At a Glance (docs/internals.md#lock-screen): the Lock Screen's second layout (Display > Lock
 * Screen > Layout, POD_LOCKLAYOUT), taken as each lock window opens. Stock's lock window and its
 * widgets, laid out again in the theme's own ground, ink, "on" colour and faces, either half: the
 * time top left and the date top right; with a queue, stock's song card across the screen with its
 * cover square and the song's own lines, on Background's ground (the cover's colour, the theme's
 * plain ground, or the cover blurred); without one, the chosen picture, or the time large on the
 * plain ground; and slide to unlock, the one thing a finger moves. With a queue the clock held
 * turns the song's view to the picture's and back. lockscreen.c hands the window over (glance_init)
 * and calls the rest; lockscreen_input hands it the touches (lockscreen_touch). */
#include "navigation.h"
#include "theme.h"

enum { GL_SONG, GL_CLOCK, GL_PICTURE };           /* what the screen shows: gl.mode */
enum { GROUND_COVER, GROUND_PLAIN, GROUND_BLUR }; /* Background: POD_LOCKGROUND */
#define GL_W 375
#define GL_H 320

/* Where things go, every theme's alike: px on the screen, but cover_y and the song's lines' *_y,
 * in its card at card_y. If theme authors should ever move them, these become theme keys. */
static const struct {
    short x, right, time_y, time_px, ampm_y, ampm_px, date_y, date_gap, date_px;
    short big_y, big_px, big_ampm_y, big_ampm_px, big_day_y, big_day_px;
    short card_y, card_h, cover_y, cover_px, text_x;
    short title_y, title_px, artist_y, artist_px, album_y, album_px, place_y, place_px;
    short track_y, track_h, knob_w, knob_h, knob_in, words_px;
    short hold_w, hold_h, big_hold_y, big_hold_h;
} G = { 36,  339, 16, 44, 40, 17, 25, 19,  15, 50,  104, 122, 24, 168, 18, 76,  150, 8,  124,
        174, 12,  20, 38, 16, 60, 15, 104, 14, 234, 48,  66,  40, 4,   24, 220, 80,  44, 126 };
#define GL_TEXT_A 209   /* the album, place and count's ink, of 255 */
#define GL_DAY_A 220    /* ... the large clock's date's */
#define GL_BAND_H 88    /* over a picture, the flat band behind the clock */
#define GL_BAND_A 88    /* ... its alpha in the dark half */
#define GL_BAND_A_L 200 /* ... in the light half, so the dark ink reads */
#define GL_TRACK_A 0x30 /* the slider's track: the ink at this alpha */
#define GL_GRAB 26      /* px round the knob a finger may land */
#define GL_DONE 625     /* per mille of the knob's travel: let go past it, it unlocks */
#define GL_WORDS_A 140  /* slide to unlock's ink, fading over the knob's first GL_WORDS_FADE px */
#define GL_WORDS_FADE 90
#define GL_SHINE_FROM 60 /* the shine: from this far left of the words, GL_SHINE_STEP px each */
#define GL_SHINE_MS 50   /* GL_SHINE_MS, round a run of GL_SHINE_RUN */
#define GL_SHINE_STEP 7
#define GL_SHINE_RUN 420
#define GL_BLUR_PX 495 /* the blurred cover: a square this wide centred behind the screen */
#define GL_HOLD_MS 1000
#define GL_HOLD_SLOP 14
#define GL_FADE_MS 160     /* each half of the flip */
#define GL_OPEN_MS 16      /* the unlock, after the knob's release */
#define GL_WHEEL_TICKS 3   /* Wheel Unlocks: the ticks forward that take the knob past GL_DONE */
#define GL_WHEEL_REST 1000 /* ... and the knob goes back this long after a tick short of it */
/* the shine's bands, outermost first: px wide, and how far each lifts the words' ink to full, of
 * 112 */
static const unsigned char shine_w[] = { 96, 60, 28 }, shine_a[] = { 64, 80, 112 };

static struct {
    widget_t *win, *img; /* the lock window and its picture (img_screensaver), where it paints */
    /* the song's, in stock's card (view_playinfo), the clock's, the battery percent (stock's own
     * hides with view_timeinfo), and a label holding slide to unlock's words in their language */
    widget_t *card, *cover, *title, *artist, *album, *place, *count;
    widget_t *time, *ampm, *day, *date, *battery, *words;
    void *full; /* the built-in picture lockscreen.c rendered, 0 for none */
    int picture, mode, minute, queue, clock, background, shiny;
    unsigned timer;
    /* slide to unlock: its view, the knob's px along the track, the shine's px along its run and
     * the words' width; a finger on the knob (drag, drag_dx its x less the knob's), and unlocking
     * under way (step) */
    widget_t *slider;
    int knob, shine, words_w, drag, drag_dx, step;
    unsigned wheel_at; /* Wheel Unlocks: the last tick that moved the knob, 0 none since */
    unsigned shine_timer;
    /* the clock held with a queue turns the song's view to the picture's and back (flip): hold
     * while a finger rests there since (hold_x, hold_y); then the veil, a view over everything,
     * covers the old view in the plain ground's colour, the view changes under it (turned), and it
     * lifts off the new one, fade its alpha */
    widget_t *veil;
    int flip, hold, hold_x, hold_y, fade, turned;
    unsigned hold_timer, fade_timer, fade_at;
    /* the ground behind the song, 0xRRGGBB, from the cover of np_art_key() art_key (sampled 0:
     * none, the plain ground then); with Background: Blurred Cover, the cover blurred, GL_W x
     * GL_H, made only then and freed with the window */
    unsigned ground, art_key;
    int sampled, resample;
    void *blur;
} gl __attribute__((section(".scratch")));

/* The picture widget goes: nothing here may match a later widget at its address. */
static int forget(void *ctx, event_t *event) {
    (void)event;
    if (ctx != gl.img) return 0;
    gl.win = gl.img = gl.card = gl.cover = gl.title = gl.artist = gl.album = gl.place = 0;
    gl.count = gl.time = gl.ampm = gl.day = gl.date = gl.battery = gl.words = 0;
    gl.slider = gl.veil = 0;
    gl.full = 0;
    gl.wheel_at = 0, gl.knob = gl.drag = gl.step = gl.hold = gl.fade = gl.sampled = 0;
    if (gl.blur) bitmap_destroy(gl.blur);
    gl.blur = 0;
    stop_timer(&gl.timer);
    stop_timer(&gl.shine_timer);
    stop_timer(&gl.hold_timer);
    stop_timer(&gl.fade_timer);
    return 0;
}

/* A face's name for script_face's FONT_*, 0 the system font */
static const char *face_name(int f) {
    return f == FONT_CLASSIC_LIGHT ? "classic-light" : f == FONT_BOLD ? "bold" : (const char *)0;
}
/* A label's face: the theme's for its role (ROLE_NP, ROLE_INFO) at px, else bold, else the system
 * font, for its text in any script (script_face); its "_glance" holds role << 8 | px. */
static void face(widget_t *w) {
    if (!w) return;
    int v = widget_get_prop_int(w, "_glance", 0), px = v & 255;
    lock_face(w, face_name(script_face(theme()->font[v >> 8], widget_get_text(w), -1, &px)), px);
}
/* A label: its box, role's face at px, side, and the dark half's white at alpha a, which Light
 * Mode turns to the theme's own ink as on its pages (text_ink). */
static void put(widget_t *w, int x, int y, int ww, int h, int px, int role, const char *align,
                unsigned a) {
    if (!w) return;
    slider_label(w, x, y, ww, h, px, "default");
    widget_set_prop_str(w, "style:normal:text_align_h", align);
    if (a != 0xff) text_ink(w, a << 24 | 0xffffffu, INK_LABEL);
    widget_set_prop_int(w, "_glance", role << 8 | px);
    widget_set_prop_int(w, "_ringnav_box", 0);
    face(w);
}
static widget_t *new_label(widget_t *parent) {
    return label(parent, 0, 0, 10, 10, "s_label_white20c", 20, 0xffffffffu);
}
/* A box filled in a final 0xAARRGGBB (the paint runs with ui_ink_raw on, glance_paint). */
static void box(canvas_t *canvas, int x, int y, int w, int h, unsigned color, unsigned radius) {
    rect_t r = { x, y, w, h };
    fill_box(canvas, &r, (RGBA(color & 0xffffffu) & 0xffffffu) | (color & 0xff000000u), radius);
}

/* A cover's colour as the ground behind white text, or a pale tint of it under Light Mode's ink.
 * ponytail: 3/8 and 1/4 of the cover are starting points; tune on the device. */
static unsigned tone(unsigned avg) {
    return ipod_paper() ? mix(avg, flat_page(), 3, 4) : mix(avg, 0, 5, 8);
}

/* The cover's pixels blurred into gl.blur: sampled onto a grid of 8 px cells from a square
 * GL_BLUR_PX wide centred behind the screen, the grid smoothed twice with its neighbours, then
 * drawn back bilinearly, each pixel tone()'s. Shapes and colours stay; nothing in it is sharp. */
#define GL_GW 47 /* the grid: the screen at a cell each 8 px */
#define GL_GH 40
static int blur(const unsigned char *data, const unsigned char *o, unsigned w, unsigned h,
                unsigned stride) {
    unsigned short (*grid)[GL_GW][3] = calloc(2 * GL_GH, sizeof *grid);
    unsigned short (*t)[GL_GW][3] = grid ? grid + GL_GH : 0;
    if (grid && !gl.blur) gl.blur = bitmap_create_ex(GL_W, GL_H, GL_W * 4, 1 /* RGBA8888 */);
    unsigned *d = grid && gl.blur ? (unsigned *)bitmap_lock_buffer_for_write(gl.blur) : 0;
    if (!d) {
        free(grid);
        return 0;
    }
    int px = GL_BLUR_PX;
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
    unsigned line = bitmap_get_line_length(gl.blur) / 4;
    for (int y = 0; y < GL_H; ++y) {
        int gy = y * 256 / 8 - 128, j = gy < 0 ? 0 : gy >> 8, fy = gy < 0 ? 0 : gy & 255;
        int j1 = j + 1 < GL_GH ? j + 1 : GL_GH - 1;
        if (j >= GL_GH) j = GL_GH - 1;
        for (int x = 0; x < GL_W; ++x) {
            int gx = x * 256 / 8 - 128, i = gx < 0 ? 0 : gx >> 8, fx = gx < 0 ? 0 : gx & 255;
            int i1 = i + 1 < GL_GW ? i + 1 : GL_GW - 1;
            if (i >= GL_GW) i = GL_GW - 1;
            unsigned rgb = 0;
            for (int c = 0; c < 3; ++c) {
                int top = grid[j][i][c] * (256 - fx) + grid[j][i1][c] * fx,
                    bottom = grid[j1][i][c] * (256 - fx) + grid[j1][i1][c] * fx;
                unsigned v = (unsigned)(top * (256 - fy) + bottom * fy) >> 16;
                rgb |= (v > 255 ? 255 : v) << (16 - 8 * c);
            }
            d[(unsigned)y * line + (unsigned)x] = RGBA(tone(rgb));
        }
    }
    bitmap_unlock_buffer(gl.blur);
    free(grid);
    *(unsigned short *)((char *)gl.blur + 0xc) |= 1; /* BITMAP_FLAG_OPAQUE */
    return 1;
}

/* The ground behind the song from the playing cover (the lock window's own img_playcover image, as
 * stock loaded it): an 8 x 8 lattice of its pixels, each over the half's paper, averaged and
 * tone()'d; and with Blurred Cover the blur. Once per cover key: a cover that can't be read leaves
 * the plain ground until the key moves. The decoded cover is let go after. */
static void sample(void) {
    static const unsigned char at[4][4] = BITMAP_RGBA_AT;
    const char *name = gl.cover ? widget_get_prop_str(gl.cover, "image", "") : "";
    gl.sampled = 0;
    if (gl.background == GROUND_PLAIN || !name || !*name) return;
    unsigned bitmap[64]; /* bitmap_t: width @0, height @4, format @14 */
    memset(bitmap, 0, sizeof bitmap);
    /* the player writes every song's cover to the same file: the cached bitmap is the last one's */
    image_manager_unload_bitmap_by_name(image_manager(), name);
    if (widget_load_image(gl.cover, name, bitmap)) return;
    unsigned w = bitmap[0], h = bitmap[1], format = ((unsigned short *)bitmap)[7] - 1u;
    const unsigned char *data =
        format < 4 && w && h && w <= 4096 && h <= 4096 ? bitmap_lock_buffer_for_read(bitmap) : 0;
    if (data) {
        const unsigned char *o = at[format];
        unsigned stride = bitmap_get_line_length(bitmap), paper = flat_page(), sum[3] = { 0 };
        for (unsigned j = 0; j < 8; ++j)
            for (unsigned i = 0; i < 8; ++i) {
                const unsigned char *p =
                    data + h * (2 * j + 1) / 16 * stride + w * (2 * i + 1) / 16 * 4;
                unsigned a = p[o[3]];
                for (int c = 0; c < 3; ++c)
                    sum[c] += (p[o[c]] * a + (paper >> (16 - 8 * c) & 255) * (255 - a)) / 255;
            }
        gl.ground = tone(sum[0] / 64 << 16 | sum[1] / 64 << 8 | sum[2] / 64);
        gl.sampled = gl.background != GROUND_BLUR || blur(data, o, w, h, stride);
        bitmap_unlock_buffer(bitmap);
    }
    widget_unload_image(gl.cover, bitmap);
}

/* The time and the date where the mode has them: top left and right, or, with no song and no
 * picture, large in the middle with the date under it; none with Time show off. A digit is about
 * 0.56 of the size wide in stock's face, the colon half that; AM or PM follows the time. The
 * battery percent, when the Battery setting shows one, sits over the weekday, or under the large
 * clock's date. */
static void clock_place(void) {
    static const char *const days[] = { "星期日",    "星期一",   "星期二",
                                        "星期三", "星期四", "星期五",
                                        "星期六" };
    int buf[11];
    const int *now = clock_now(buf), *tm = gl.clock ? now : 0;
    int hour = now ? now[2] : 0, min = now ? now[1] : 0, h12 = !g_time24h_flag;
    char s[64];
    tk_snprintf(s, sizeof s, now ? "%d:%02d" : "--:--", h12 ? (hour + 11) % 12 + 1 : hour, min);
    if (gl.time) widget_set_text_utf8(gl.time, gl.clock ? s : "");
    if (gl.ampm) widget_set_text_utf8(gl.ampm, !tm || !h12 ? "" : hour < 12 ? "上午" : "下午");
    int big = gl.mode == GL_CLOCK, px = big ? G.big_px : G.time_px;
    int apx = big ? G.big_ampm_px : G.ampm_px, digits = (int)strlen(s) - 1;
    int tw = now ? digits * px * 56 / 100 + px * 28 / 100 : px * 2;
    int pm = h12 ? (apx * 10 + 40) / 7 : 0; /* AM or PM's room */
    const char *month = tm ? tr(time_months[tm[4] % 12]) : "";
    /* the date's order: a language puts its day first by translating "MD" as "DM" */
    int dm = *tr("MD") == 'D';
    if (big) {
        int x = (GL_W - tw - (pm ? pm + 6 : 0)) / 2;
        put(gl.time, x - 4, G.big_y, tw + 8, px + 8, px, ROLE_NP, "center", 0xff);
        put(gl.ampm, x + tw + 6, G.big_ampm_y, pm + 4, apx + 4, apx, ROLE_INFO, "left", 0xff);
        if (tm && dm)
            tk_snprintf(s, sizeof s, "%s, %d %s", tr(days[tm[6] % 7]), tm[3], month);
        else if (tm)
            tk_snprintf(s, sizeof s, "%s, %s %d", tr(days[tm[6] % 7]), month, tm[3]);
        if (gl.day) widget_set_text_utf8(gl.day, tm ? s : "");
        if (gl.date) widget_set_text_utf8(gl.date, "");
        put(gl.day, 0, G.big_day_y, GL_W, G.big_day_px + 8, G.big_day_px, ROLE_INFO, "center",
            GL_DAY_A);
        put(gl.date, 0, 0, 1, 1, G.date_px, ROLE_INFO, "right", 0xff);
        put(gl.battery, 0, G.big_day_y + G.big_day_px + 10, GL_W, G.date_px + 5, G.date_px,
            ROLE_INFO, "center", GL_DAY_A);
    } else {
        put(gl.time, G.x, G.time_y, tw + 8, px + 8, px, ROLE_NP, "left", 0xff);
        put(gl.ampm, G.x + tw + 6, G.ampm_y, pm + 4, apx + 5, apx, ROLE_INFO, "left", 0xff);
        if (gl.day) widget_set_text_utf8(gl.day, tm ? tr(days[tm[6] % 7]) : "");
        if (tm && dm)
            tk_snprintf(s, sizeof s, "%d %s", tm[3], month);
        else if (tm)
            tk_snprintf(s, sizeof s, "%s %d", month, tm[3]);
        if (gl.date) widget_set_text_utf8(gl.date, tm ? s : "");
        put(gl.day, G.right - 170, G.date_y, 170, G.date_px + 5, G.date_px, ROLE_INFO, "right",
            0xff);
        put(gl.date, G.right - 170, G.date_y + G.date_gap, 170, G.date_px + 5, G.date_px, ROLE_INFO,
            "right", 0xff);
        put(gl.battery, G.right - 170, G.date_y - G.date_gap, 170, G.date_px + 5, G.date_px,
            ROLE_INFO, "right", GL_TEXT_A);
    }
    if (gl.battery) widget_set_visible(gl.battery, ipod_battery() != 0, 0);
    gl.minute = now ? hour * 60 + min : -1;
}

/* A label's text, written only when it changed (each second while a song plays), so a marquee
 * keeps its rests at either end. */
static void retext(widget_t *w, const char *want) {
    if (w && text_differs(w, want)) widget_set_text_utf8(w, want);
}
/* The song's own lines: the album, how far in, and its place in the queue. */
static void song_lines(void) {
    char s[64];
    unsigned pos = 0, n = 0;
    void *r = queue_now(&pos, &n);
    const char *album = r ? now_tag(r, REC_ALBUM) : 0;
    retext(gl.album, album && strcmp(album, "(null)") ? album : "");
    int sec = 0, total = 0;
    mclGetPlayTime(&sec, &total);
    if (sec < 0) sec = 0;
    if (total < 0) total = 0;
    tk_snprintf(s, sizeof s, "%d:%02d / %d:%02d", sec / 60, sec % 60, total / 60, total % 60);
    retext(gl.place, s);
    if (n)
        tk_snprintf(s, sizeof s, "%u / %u", pos + 1, n);
    else
        *s = 0;
    retext(gl.count, s);
}

/* What the screen shows now: the song with a queue (stock shows its card then) unless flipped,
 * else the chosen picture, else the clock large. The picture's view shows only the picture (or
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
    /* stock's battery percent, which it still writes in its hidden view_timeinfo */
    widget_t *stock_battery = gl.win ? widget_lookup(gl.win, "label_battery", 1) : 0;
    char pct[16];
    if (stock_battery && !widget_get_text_utf8(stock_battery, pct, sizeof pct))
        retext(gl.battery, pct);
    if (song) {
        /* another song, or its cover file written since (np_art_key holds the file's size and
         * time): every song's cover shares one file name, so the ground is sampled again */
        unsigned key = np_art_key();
        if (gl.resample || key != gl.art_key) {
            gl.art_key = key, gl.resample = 0, changed = 1;
            sample();
        }
        song_lines();
    }
    if (changed) widget_invalidate_force(gl.win, 0);
}

/* The shine that crosses slide to unlock (Classic Slider's): a step of GL_SHINE_STEP px each
 * GL_SHINE_MS while the screen is lit, the slider repainted only while a band is over the words
 * or has just left them. It stops with the screen, and glance_poll starts it again. */
static int shine_lit(int at) {
    int mid = at - GL_SHINE_FROM, half = shine_w[0] / 2;
    return mid + half > 0 && mid - half < gl.words_w;
}
static int shine(const void *info) {
    (void)info;
    if (!g_backlight_status || !gl.slider) {
        gl.shine_timer = 0;
        return 0; /* RET_OK: a timer's end */
    }
    int was = gl.shine;
    gl.shine = (gl.shine + GL_SHINE_STEP) % GL_SHINE_RUN;
    if (shine_lit(was) || shine_lit(gl.shine)) widget_invalidate_force(gl.slider, 0);
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
    if (gl.wheel_at && !gl.step && !gl.drag && time_now_ms() - gl.wheel_at >= GL_WHEEL_REST) {
        gl.wheel_at = 0, gl.knob = 0; /* the wheel stopped short: a pocket's nudges never add up */
        if (gl.slider) widget_invalidate_force(gl.slider, 0);
    }
    sync(0);
    return 8; /* RET_REPEAT */
}
/* info_poll's, each loop pass: each timer looked at on its own, as a lock window opens with the
 * screen still dark, and the shine's first step can end it while the slower tick lives on. */
void glance_poll(void) {
    if (!gl.img || !g_backlight_status) return;
    if (gl.shiny && !gl.shine_timer) rearm(&gl.shine_timer, shine, GL_SHINE_MS);
    if (gl.timer) return;
    sync(0);
    rearm(&gl.timer, tick, 1000);
}

/* lockscreen_init under At a Glance, after stock built the window: img its picture, picture 1 for
 * the user's own or a built-in but Black, full the built-in's rendering. Stock's clocks, battery,
 * shade and slide hint hide (a label of its own shows stock's percent); its song card (stock shows
 * it only with a queue) takes the screen's width under the time; the time, date, the song's other
 * lines, the slider and the flip's veil are added. Time show off (stock's init hid view_timeinfo)
 * leaves the clock out. The flip stays for the next lock. */
void glance_init(widget_t *win, widget_t *img, int picture, void *full) {
    static const char *const hidden[] = { "view_timeinfo", "clock_time",  "clock_date",
                                          "label_battery", "img_battery", "pgbar_battery",
                                          "view_shade",    "img_slide" };
    widget_t *info = widget_lookup(win, "view_timeinfo", 1);
    gl.clock = !info || widget_get_visible(info);
    for (unsigned i = 0; i < sizeof hidden / sizeof *hidden; ++i) {
        widget_t *w = widget_lookup(win, hidden[i], 1);
        if (w) widget_set_visible(w, 0, 0);
    }
    gl.win = win, gl.img = img, gl.picture = picture, gl.full = full;
    gl.background = pod_value(POD_LOCKGROUND), gl.shiny = pod_value(POD_LOCKSLIDER);
    widget_on(img, EVT_DESTROY, forget, img);
    widget_t *top = P(img, W_PARENT), *card = widget_lookup(win, "view_playinfo", 1);
    gl.card = card;
    gl.cover = widget_lookup(win, "img_playcover", 1);
    gl.title = widget_lookup(win, "scrlabel_title", 1);
    gl.artist = widget_lookup(win, "scrlabel_artist", 1);
    gl.album = gl.place = gl.count = 0;
    if (card) {
        widget_move_resize(card, 0, G.card_y, GL_W, G.card_h);
        widget_set_prop_int(card, "style:normal:bg_color", 0);
        widget_set_prop_int(card, "style:normal:round_radius", 0);
        gl.album = new_label(card), gl.place = new_label(card), gl.count = new_label(card);
    }
    if (gl.cover) { /* square: stock rounds this one */
        widget_move_resize(gl.cover, G.x, G.cover_y, G.cover_px, G.cover_px);
        widget_set_prop_int(gl.cover, "style:normal:round_radius", 0);
        widget_set_prop_int(gl.cover, "style:pressed:round_radius", 0);
        widget_set_prop_int(gl.cover, "style:selected:round_radius", 0);
    }
    int tx = G.text_x, tw = G.right - tx;
    /* the title and artist 6 px over their fonts, the rest 22 high (a label draws nothing in a box
     * shorter than its font); the place and count 90 wide, at either end */
    put(gl.title, tx, G.title_y, tw, G.title_px + 6, G.title_px, ROLE_NP, "left", 0xff);
    put(gl.artist, tx, G.artist_y, tw, G.artist_px + 6, G.artist_px, ROLE_NP, "left", 0xff);
    put(gl.album, tx, G.album_y, tw, 22, G.album_px, ROLE_INFO, "left", GL_TEXT_A);
    put(gl.place, tx, G.place_y, 90, 22, G.place_px, ROLE_INFO, "left", GL_TEXT_A);
    put(gl.count, G.right - 90, G.place_y, 90, 22, G.place_px, ROLE_INFO, "right", GL_TEXT_A);
    gl.time = new_label(top), gl.ampm = new_label(top), gl.day = new_label(top);
    gl.date = new_label(top), gl.battery = new_label(top);
    gl.slider = widget_factory_create_widget(widget_factory(), "view", top, G.x, G.track_y,
                                             G.right - G.x, G.track_h);
    gl.words = gl.slider ? new_label(gl.slider) : 0; /* the words' text alone: the slider draws */
    if (gl.words) {
        widget_set_text_utf8(gl.words, "滑动解锁");
        widget_set_visible(gl.words, 0, 0);
    }
    /* the flip's veil, over everything, last: it takes no touch */
    gl.veil = widget_factory_create_widget(widget_factory(), "view", top, 0, 0, GL_W, GL_H);
    if (gl.veil) widget_set_sensitive(gl.veil, 0);
    /* gl.flip stays: the view the clock's hold left is the next lock's, until demo restarts */
    gl.wheel_at = 0, gl.knob = gl.shine = gl.words_w = gl.drag = gl.step = gl.hold = gl.fade = 0;
    gl.mode = -1, gl.queue = -1, gl.resample = 1;
    if (gl.shiny) rearm(&gl.shine_timer, shine, GL_SHINE_MS);
    sync(1);
    rearm(&gl.timer, tick, 1000);
}

/* The lock window's cover changed (lockscreen_cover, stock's same file name): the ground is the
 * old cover's, sampled again from this one. */
void glance_cover(widget_t *cover) {
    if (!gl.img || cover != gl.cover) return;
    gl.resample = 1;
    sync(1);
}

/* The picture: behind the song, Background's ground (the plain ground while the cover can't be
 * read); without one the built-in or the user's own picture (stock drew that) with a flat band
 * behind the clock in the plain ground's colour, or the plain ground. Each label's face first, for
 * the text it has now. */
static void paint_picture(canvas_t *canvas) {
    widget_t *const labels[] = { gl.title, gl.artist, gl.album, gl.place, gl.count,
                                 gl.time,  gl.ampm,   gl.day,   gl.date,  gl.battery };
    int r[4] = { 0, 0, GL_W, GL_H };
    unsigned plain = flat_page();
    for (unsigned i = 0; i < sizeof labels / sizeof *labels; ++i) face(labels[i]);
    if (gl.mode == GL_SONG && gl.sampled && gl.background == GROUND_BLUR && gl.blur)
        canvas_draw_image(canvas, gl.blur, r, r);
    else if (gl.mode == GL_PICTURE && gl.full)
        canvas_draw_image(canvas, gl.full, r, r);
    else if (gl.mode != GL_PICTURE)
        box(canvas, 0, 0, GL_W, GL_H,
            0xff000000u | (gl.mode == GL_SONG && gl.sampled ? gl.ground : plain), 0);
    if (gl.mode == GL_PICTURE)
        box(canvas, 0, 0, GL_W, GL_BAND_H,
            (unsigned)(ipod_paper() ? GL_BAND_A_L : GL_BAND_A) << 24 | plain, 0);
}

/* The slider. Flat (Classic Slider off): its track the labels' ink at GL_TRACK_A, the knob where
 * the finger has it in the theme's "on" colour with a plain arrow, the words right of the knob's
 * rest in the ink, fading as the knob leaves. Classic Slider on, alike in every theme and either
 * half (the user's pick, the one place the flat rule is lifted): a white rim round a see-through
 * black track, a grey two-tone knob with a grey arrow, the words white, and the shine: the same
 * words brighter inside its bands, the innermost brightest, on the words alone. */
static void paint_slider(canvas_t *canvas) {
    const unsigned *words = gl.words ? widget_get_text(gl.words) : 0;
    unsigned n = 0, ink = flip_if(ipod_light(), 0xffffffu), on = flat_on();
    while (words && words[n]) ++n;
    int tw = G.right - G.x, th = G.track_h, kw = G.knob_w, kh = G.knob_h;
    int x = G.knob_in + gl.knob, y = (th - kh) / 2;
    if (gl.shiny) {
        box(canvas, 0, 0, tw, th, 51u << 24 | 0xffffffu, 11); /* its rim */
        box(canvas, 1, 1, tw - 2, th - 2, 48u << 24, 10);
        box(canvas, x, y, kw, kh, 0xff8a8a8au, 8);
        box(canvas, x + 1, y + 1, kw - 2, kh - 2, 0xfff4f4f4u, 7);
        box(canvas, x + 2, y + kh / 2, kw - 4, kh / 2 - 2, 0xffd6d6d6u, 6);
        box(canvas, x + 2, y + kh / 2, kw - 4, 6, 0xffd6d6d6u, 0);
        ink = 0xffffffu;
    } else {
        box(canvas, 0, 0, tw, th, GL_TRACK_A << 24 | ink, (unsigned)th / 2);
        box(canvas, x, y, kw, kh, 0xff000000u | on, (unsigned)kh / 2);
    }
    int fade = gl.knob >= GL_WORDS_FADE ? 0 : 255 - gl.knob * 255 / GL_WORDS_FADE;
    unsigned color = (unsigned)I(P(canvas, CANVAS_LCD), LCD_TEXT_COLOR),
             c = gl.shiny ? ink : RGBA(ink) & 0xffffffu; /* the classic white is as it looks */
    int px = G.words_px;
    canvas_set_font(canvas, face_name(script_face(theme()->font[ROLE_INFO], words, (int)n, &px)),
                    (unsigned)px);
    int text = n ? (int)(canvas_measure_text(canvas, words, n) + 0.5f) : 0;
    int room = G.knob_in + kw + 2;
    int tx = room + (tw - room - G.knob_in - text) / 2, ty = (th - px) / 2;
    gl.words_w = text;
    if (fade && n) {
        canvas_set_text_color(canvas, (unsigned)(GL_WORDS_A * fade / 255) << 24 | c);
        canvas_draw_text(canvas, words, n, tx, ty);
        int at = tx - GL_SHINE_FROM + gl.shine; /* the shine's middle; past the words it rests */
        for (unsigned b = 0; gl.shiny && b < sizeof shine_w; ++b) {
            int bw = shine_w[b], bx = at - bw / 2, old[4], clip[4];
            if (bx < tx) bw -= tx - bx, bx = tx;
            if (bx + bw > tx + text) bw = tx + text - bx;
            if (bw <= 0 || !clip_within(canvas, old, clip, I(canvas, CANVAS_X) + bx,
                                        I(canvas, CANVAS_Y), bw, th))
                continue;
            canvas_set_clip_rect(canvas, clip);
            canvas_set_text_color(canvas, (shine_a[b] * (unsigned)fade / 255) << 24 | c);
            canvas_draw_text(canvas, words, n, tx, ty);
            canvas_set_clip_rect(canvas, old);
        }
    }
    stock_canvas_set_text_color(canvas, color);
    int cx = x + kw / 2, cy = y + kh / 2;
    if (gl.shiny) { /* a shaft and a head */
        box(canvas, cx - 14, cy - 3, 16, 6, 0xff6b6b6bu, 0);
        for (int i = 0; i < 10; ++i)
            box(canvas, cx + 2 + i, cy - 9 + i, 1, 19 - 2 * i, 0xff6b6b6bu, 0);
        return;
    }
    unsigned arrow = 0xff000000u | (bright_tone(on) ? flat_ink[1] : 0xffffffu);
    box(canvas, cx - 12, cy - 2, 14, 4, arrow, 0);
    for (int i = 0; i < 8; ++i) box(canvas, cx + 2 + i, cy - 7 + i, 1, 15 - 2 * i, arrow, 0);
}

/* lockscreen_paint, for every widget: 1 when w is one of At a Glance's and painted here. The veil
 * is the flip's cover, the plain ground at its alpha, over the whole window. */
int glance_paint(widget_t *w, canvas_t *canvas) {
    if (!w || !gl.img || (w != gl.img && w != gl.slider && w != gl.veil)) return 0;
    void *lcd = canvas ? P(canvas, CANVAS_LCD) : 0;
    if (!lcd) return 1;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    unsigned raw = ui_ink_raw(1); /* every colour here is final */
    if (w == gl.img)
        paint_picture(canvas);
    else if (w == gl.slider)
        paint_slider(canvas);
    else if (gl.fade > 0) { /* stock's setter: Light Mode's ink held off */
        unsigned a = (unsigned)(gl.fade > 255 ? 255 : gl.fade) << 24;
        stock_canvas_set_fill_color(canvas, (RGBA(flat_page()) & 0xffffffu) | a);
        canvas_fill_rect(canvas, 0, 0, GL_W, GL_H);
    }
    ui_ink_raw(raw);
    stock_canvas_set_fill_color(canvas, fill);
    return 1;
}

/* The flip, a step each 16 ms: the veil covers the old view over GL_FADE_MS, the view turns once
 * when it is whole, and the veil lifts off the new one over GL_FADE_MS. Never the window's own
 * opacity, which let the page under the lock window show through. */
static int fade_step(const void *info) {
    unsigned t = time_now_ms() - gl.fade_at;
    (void)info;
    if (!gl.img || !gl.veil) {
        gl.fade_timer = 0, gl.fade = 0;
        return 0; /* RET_OK: a timer's end */
    }
    if (t >= GL_FADE_MS && !gl.turned) { /* the turn, once, when whole, however late the step */
        gl.turned = 1;
        gl.flip = !gl.flip;
        sync(1);
    }
    if (t < GL_FADE_MS)
        gl.fade = (int)(t * 255 / GL_FADE_MS);
    else if (t < 2 * GL_FADE_MS)
        gl.fade = 255 - (int)((t - GL_FADE_MS) * 255 / GL_FADE_MS);
    else {
        gl.fade = 0;
        gl.fade_timer = 0;
        widget_invalidate_force(gl.win, 0);
        return 0;
    }
    widget_invalidate_force(gl.win, 0);
    return 8; /* RET_REPEAT */
}
/* The clock held GL_HOLD_MS with a queue: the other view, through the veil. */
static int turn(const void *info) {
    (void)info;
    gl.hold_timer = 0;
    if (!gl.hold || !gl.img || gl.queue != 1) return 0;
    gl.hold = 0;
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

/* lockscreen_input, for a wheel tick with Wheel Unlocks on: 1 when At a Glance's lock window is in
 * front with the screen lit, and stock never sees the tick, so it opens no volume panel; 0 for
 * Default's window, a prompt over it, or the screen dark (the wheel is the volume there, as ever).
 * Each tick's key-down moves the knob a step, forward on Next and back on Prev, sized so the
 * GL_WHEEL_TICKS-th forward tick passes GL_DONE and unlocks as a slide let go there does. Short of
 * it, the one-second tick puts the knob back GL_WHEEL_REST after the last tick (up to a second
 * later). Every tick taken holds the screen on, as stock's own keys do. */
int glance_wheel(widget_t *wm, unsigned type, unsigned key) {
    if (!gl.img || !gl.slider || !g_backlight_status || window_manager_get_top_window(wm) != gl.win)
        return 0;
    reset_poweroptions_timer(1, 1, 1);
    if (type != EVT_KEY_DOWN || gl.step || gl.drag) return 1;
    int travel = G.right - G.x - G.knob_w - 2 * G.knob_in, done = travel * GL_DONE / 1000;
    int at = gl.knob + (key == KEY_NEXT ? 1 : -1) * (done / GL_WHEEL_TICKS + 1);
    gl.knob = at < 0 ? 0 : at > travel ? travel : at;
    unsigned t = (unsigned)time_now_ms();
    gl.wheel_at = t ? t : 1; /* 0 is none */
    if (gl.knob >= done) gl.knob = travel, gl.step = 1, timer_add(unlock, (void *)0, GL_OPEN_MS);
    widget_invalidate_force(gl.slider, 0);
    return 1;
}

#define GL_POINTER_MOVE 0x101 /* the window manager's move; EVT_POINTER_FIRST is its down */
/* lockscreen_input, for a pointer event with the lock screen up: 1 when At a Glance's lock window,
 * or the volume panel over it (lockscreen_input closed it on the down), is in front, and stock
 * never sees it; 0 for anything else, a prompt over the lock screen (Shut Down?) or Default's
 * window. Every touch taken holds the screen on, as stock's own touches do. Only the knob takes a
 * finger, within GL_GRAB px of it: it follows the finger along the track, springs back when let go
 * short of GL_DONE per mille of its travel, and past it unlocks. The clock held GL_HOLD_MS with a
 * queue flips the view; a finger moving GL_HOLD_SLOP px lets it go. The slider repaints only when
 * the knob, the drag or the hold changed. */
int lockscreen_touch(widget_t *wm, event_t *e, unsigned type) {
    if (!gl.img || !gl.slider || !e) return 0;
    widget_t *front = window_manager_get_top_window(wm);
    if (front != gl.win &&
        (!front || tk_strcmp(widget_get_prop_str(front, "name", ""), "volume_dialog")))
        return 0;
    reset_poweroptions_timer(1, 1, 1);
    if (gl.step) return 1; /* unlocking */
    /* an input event's place is the panel's, a quarter turn from the screen's: x up the screen
     * from its bottom edge, y across it */
    int x = I(e, EVENT_Y), y = GL_H - 1 - I(e, EVENT_X);
    int travel = G.right - G.x - G.knob_w - 2 * G.knob_in;
    int was = gl.knob, drag = gl.drag, hold = gl.hold;
    if (type == EVT_POINTER_FIRST) {
        int kx = G.x + G.knob_in + gl.knob, ky = G.track_y + (G.track_h - G.knob_h) / 2;
        gl.drag = x >= kx - GL_GRAB && x < kx + G.knob_w + GL_GRAB && y >= ky - GL_GRAB &&
                  y < ky + G.knob_h + GL_GRAB;
        gl.drag_dx = x - kx;
        /* the clock, top left, or large in the middle with no picture */
        gl.hold = !gl.drag && gl.queue == 1 &&
                  (gl.mode == GL_CLOCK ? y >= G.big_hold_y && y < G.big_hold_y + G.big_hold_h
                                       : x < G.hold_w && y < G.hold_h);
        gl.hold_x = x, gl.hold_y = y;
        stop_timer(&gl.hold_timer);
        if (gl.hold) gl.hold_timer = timer_add(turn, (void *)0, GL_HOLD_MS);
    } else if (type == GL_POINTER_MOVE && gl.hold) {
        int dx = x - gl.hold_x, dy = y - gl.hold_y;
        if (dx > GL_HOLD_SLOP || -dx > GL_HOLD_SLOP || dy > GL_HOLD_SLOP || -dy > GL_HOLD_SLOP)
            gl.hold = 0;
    } else if (type == GL_POINTER_MOVE && gl.drag) {
        int at = x - gl.drag_dx - G.x - G.knob_in;
        gl.knob = at < 0 ? 0 : at > travel ? travel : at;
    } else if (type != GL_POINTER_MOVE) {
        /* up, or the touch was lost: either way a knob past done is a slide meant */
        int done = gl.drag && gl.knob >= travel * GL_DONE / 1000;
        gl.hold = 0;
        stop_timer(&gl.hold_timer);
        gl.drag = 0;
        gl.knob = done ? travel : 0;
        if (done) gl.step = 1, timer_add(unlock, (void *)0, GL_OPEN_MS);
    }
    if (gl.knob != was || gl.drag != drag || gl.hold != hold) widget_invalidate_force(gl.slider, 0);
    return 1;
}
