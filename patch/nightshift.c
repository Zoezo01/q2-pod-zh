/* Night Shift (docs/internals.md#night-shift): Display's row (crossfade.c's page, xf.ctx 3) warms
 * the screen as f.lux does, red kept and green and blue scaled to a colour temperature, Q2POD
 * NIGHTSHIFT (0 Off, 1 On, 2 Auto) and NIGHTK (NS_WARM..NS_COOL in NS_STEPs) in config.ini. Auto is
 * on from 19:00 to 7:00 by the clock, the hours Light Mode's Auto is dark (navigation.c
 * clock_night), and off while the clock cannot be read; clock_show applies it each minute
 * (night_clock). While it is on, the screen's lcd end_frame is night_end: before stock shows a
 * frame, the pixels drawn in it, those whose alpha is not NS_DONE, are warmed and given NS_DONE.
 * Stock's fills and blends write alpha 0xff over an opaque pixel (>= 0xf5; fill_bgra8888_rect
 * @0x6cbf24), so a pixel drawn again is warmed once more and one left alone is never warmed twice.
 * ponytail: a fill or image pixel whose own alpha is exactly NS_DONE (stock's clear path and
 * image_copy keep it) stays unwarmed; no payload colour has it. Off puts stock's end_frame back, so
 * nothing runs per frame; the lcd is found again on the next paint after On. The setting is read at
 * boot (playback.c ringnav_bootmode), so a paint reads none. */
#include "offsets.inc"
#include "peq.h"
#include "stock.h"

#define NS_DONE 0xfeu

/* Green and blue's scale, of 255, from NS_WARM to NS_COOL by NS_STEP: Tanner Helland's fit to the
 * black-body colours, where red stays 255 below 6600K. */
static const unsigned char ns_green[] = { 137, 142, 146, 151, 155, 159, 163, 167, 170, 174, 177,
                                          180, 184, 187, 190, 193, 195, 198, 201, 203, 206, 208,
                                          211, 213, 215, 218, 220, 222, 224, 226, 228 };
static const unsigned char ns_blue[] = { 14,  27,  39,  50,  61,  70,  79,  87,  95,  103, 110,
                                         117, 123, 129, 135, 141, 146, 151, 157, 161, 166, 171,
                                         175, 179, 183, 187, 191, 195, 199, 202, 206 };
_Static_assert(sizeof ns_green == (NS_COOL - NS_WARM) / NS_STEP + 1, "a scale per step");
_Static_assert(sizeof ns_blue == sizeof ns_green, "a blue per green");

static struct {
    int read, on, kelvin, built; /* on: 0 Off, 1 On, 2 Auto; built: the kelvin g and b are for */
    int dark;                    /* Auto's hours now (clock_night) */
    int saved_on, saved_kelvin;  /* as config.ini holds them */
    void *lcd;                   /* the screen's, its end_frame night_end; 0 while off */
    int (*end)(void *);          /* stock's end_frame */
    unsigned char g[256], b[256];
} ns __attribute__((section(".scratch")));

static void settings(void) {
    if (ns.read) return;
    char v[16] = "";
    ns.on = config_value("Q2POD", "NIGHTSHIFT", 3, 0);
    ns.dark = clock_night();
    toolsReadConfig("/mnt/data/config.ini", "Q2POD", "NIGHTK", v, "0");
    int k = atoi(v);
    ns.kelvin = k >= NS_WARM && k <= NS_COOL && !(k % NS_STEP) ? k : NS_DEFAULT;
    ns.saved_on = ns.on, ns.saved_kelvin = ns.kelvin;
    ns.read = 1;
}
/* Whether the screen is warmed now: On, or Auto in its hours. */
static int lit(void) { return ns.on == 1 || (ns.on == 2 && ns.dark); }
int night_on(void) {
    settings();
    return ns.on;
}
int night_kelvin(void) {
    settings();
    return ns.kelvin;
}

/* The frame's new pixels warmed: its rect, the buffer's rows the canvas's width (stock's 375 x 320
 * lcd_mem, which the flush thread turns onto /dev/fb0); another layout is left as drawn. */
static void warm(void *lcd) {
    unsigned *fb = P(lcd, LCD_OFFLINE_FB), w = I(lcd, LCD_W), h = I(lcd, LCD_H);
    if (!fb || (unsigned)I(lcd, LCD_LINE_LENGTH) != 4 * w) return;
    if (ns.built != ns.kelvin) {
        unsigned i = (unsigned)(ns.kelvin - NS_WARM) / NS_STEP;
        for (unsigned v = 0; v < 256; ++v)
            ns.g[v] = (unsigned char)((v * ns_green[i] + 127) / 255),
            ns.b[v] = (unsigned char)((v * ns_blue[i] + 127) / 255);
        ns.built = ns.kelvin;
    }
    int x = I(lcd, LCD_DIRTY), y = I(lcd, LCD_DIRTY + 4);
    int x1 = x + I(lcd, LCD_DIRTY + 8), y1 = y + I(lcd, LCD_DIRTY + 12);
    x = x < 0 ? 0 : x, y = y < 0 ? 0 : y;
    x1 = x1 > (int)w ? (int)w : x1, y1 = y1 > (int)h ? (int)h : y1;
    if (x1 <= x || y1 <= y) return;
    for (unsigned *line = fb + y * w + x, *last = fb + y1 * w + x; line < last; line += w)
        for (unsigned *p = line, *end = line + (x1 - x); p < end; ++p) {
            unsigned c = *p;
            if (c >> 24 != NS_DONE)
                *p = NS_DONE << 24 | (c & 0xff0000) | ns.g[c >> 8 & 255] << 8 | ns.b[c & 255];
        }
}

/* The screen's end_frame while on. Window snapshots are drawn on canvas_offline's own lcd, so
 * they reach this screen as images, warmed when shown. */
static int night_end(void *lcd) {
    warm(lcd);
    return ns.end(lcd);
}

/* ringnav_paint, each widget: once on, the screen's lcd takes night_end; a normal frame's canvas
 * is the screen's, an offline one draws into a bitmap. Off (or not yet read), a flag test. */
void night_paint(void *canvas) {
    if (ns.lcd || !lit()) return;
    void *lcd = canvas ? P(canvas, CANVAS_LCD) : 0;
    if (!lcd || I(lcd, LCD_DRAW_MODE) != LCD_DRAW_NORMAL || I(lcd, LCD_FORMAT) != LCD_BGRA8888 ||
        !P(lcd, LCD_END_FRAME))
        return;
    ns.end = (int (*)(void *))P(lcd, LCD_END_FRAME);
    P(lcd, LCD_END_FRAME) = (void *)night_end;
    ns.lcd = lcd;
}

/* The page's: on and the temperature, shown at once (a drag's every step), saved by night_save;
 * off gives stock's end_frame back. The whole screen draws again, in the new colours. */
void night_set(int on, int kelvin) {
    settings();
    ns.on = on, ns.kelvin = kelvin;
    if (!lit() && ns.lcd) {
        P(ns.lcd, LCD_END_FRAME) = (void *)ns.end;
        ns.lcd = 0;
    }
    widget_invalidate_force(window_manager(), 0);
}

/* clock_show, each minute: Auto's hours, a crossing shown as the page's change is. */
void night_clock(int dark) {
    settings();
    if (dark == ns.dark) return;
    ns.dark = dark;
    if (ns.on == 2) night_set(2, ns.kelvin);
}

/* The page's save (crossfade.c refresh): what night_set showed, into config.ini. */
void night_save(void) {
    settings();
    if (ns.on != ns.saved_on) write_int_config(ns.on, "Q2POD", "NIGHTSHIFT");
    if (ns.kelvin != ns.saved_kelvin) write_int_config(ns.kelvin, "Q2POD", "NIGHTK");
    ns.saved_on = ns.on, ns.saved_kelvin = ns.kelvin;
}
