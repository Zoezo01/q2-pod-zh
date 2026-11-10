/* The volume panel: stock's volume dialog, drawn over Now Playing's times or on its own. */
#include "navigation.h"

/* The dialog vol_paint last drew, its value and the redraw timer. */
static struct {
    void *vol_dialog;
    int vol_drawn;
    unsigned vol_timer;
} vd __attribute__((section(".scratch")));

/* A volume bar's end where the time it replaces sits: the speaker image name (tools/art.py) from x,
 * or ending at x when right, centred on bar's rows. */
static void vol_end(void *top, void *canvas, const char *name, int x, int right,
                    const rect_t *bar) {
    unsigned bitmap[64]; /* bitmap_t: width @0, height @4 */
    if (widget_load_image(top, name, bitmap)) return;
    int w = (int)bitmap[0], h = (int)bitmap[1];
    rect_t src = { 0, 0, w, h }, dst = { right ? x - w : x, bar->y + (bar->h - h) / 2, w, h };
    canvas_draw_image(canvas, bitmap, &src, &dst);
}

/* Every VOL_POLL_MS while the dialog vol_paint drew is still on top: a changed volume repaints it
 * whole. Stock's own invalidation of the hidden slider does not reliably reach the screen, so the
 * bar would stop following the wheel after a tick or two. */
static int vol_poll(const void *info) {
    void *top = window_manager_get_top_window(window_manager());
    void *vol = top && top == vd.vol_dialog ? widget_lookup(top, "slider_vol", 1) : (void *)0;
    if (!vol) {
        vd.vol_timer = 0;
        vd.vol_dialog = (void *)0;
        return 0; /* RET_OK: removed */
    }
    if (widget_get_prop_int(vol, "value", 0) != vd.vol_drawn)
        widget_invalidate_force(top, (void *)0);
    return 8; /* RET_REPEAT */
}

#define VOL_CL_X 28 /* Classic's panel: its ring and corners clear the glass */
/* The volume, drawn by stock's dialog/volume_dialog itself: transparent and full-screen (its
 * dimming highlight removed at build time, tools/ipod.py), it opens on the wheel's volume and
 * sets its slider_vol. Over Now Playing, as on an iPod classic, the band from the progress bar to
 * the times turns black with a white bar over the track and "Volume N"; with Classic's times beside
 * its bar, the bar shows the volume between the iPod's speakers in the times' place (vol_end), no
 * number. Over any other window (Quick Settings included) a VOL_PANEL_* rounded panel in the
 * fast-scroll letter's style holds a pill bar and the number alone; Classic's, VOL_CL_X in, the
 * rows painter's gloss between two speakers. slider_vol and label_vol are
 * hidden, and slider_vol is moved onto the area drawn, so stock's partial repaints land on it;
 * vol_poll catches the changes those miss. */
void vol_paint(void *top, void *canvas) {
    void *wm = window_manager(), *lcd = P(canvas, CANVAS_LCD);
    if (!lcd || tk_strcmp(widget_get_prop_str(top, "name", ""), "volume_dialog")) return;
    unsigned n = widget_count_children(wm);
    void *vol = widget_lookup(top, "slider_vol", 1), *label = widget_lookup(top, "label_vol", 1);
    if (n < 2 || !vol) return;
    int np = widget_get_child(wm, n - 2) == st.np_win && st.np_slider && st.np_elapsed;
    const theme_t *T = theme();
    int gloss = !np && T->painter[PAINT_VOLUME] == VOL_CLASSIC_LIGHT;
    rect_t area, bar, text, lo, hi;
    int beside = 0;
    if (np) {
        void *slider = st.np_slider, *times = st.np_elapsed;
        int dy = I(st.np_win, W_Y) - I(top, W_Y), y = dy + I(slider, W_Y),
            bh = widget_get_prop_int(slider, "bar_size", 8), ty = dy + I(times, W_Y);
        bar = (rect_t){ I(slider, W_X), y + (I(slider, W_H) - bh) / 2, I(slider, W_W), bh };
        text = (rect_t){ 0, ty, I(st.np_win, W_W), I(times, W_H) };
        area = (rect_t){ 0, y, text.w, ty + text.h - y };
        /* Classic's times share the bar's rows: their boxes, and the bar with its reflection */
        beside = np_beside();
        if (beside) {
            lo = (rect_t){ I(times, W_X), ty, I(times, W_W), text.h };
            hi = (rect_t){ I(st.np_remain, W_X), dy + I(st.np_remain, W_Y), I(st.np_remain, W_W),
                           I(st.np_remain, W_H) };
            int end = bar.y + bh + SCRUB_REFLECT_ROWS;
            area.y = ty < bar.y ? ty : bar.y;
            area.h = (ty + text.h > end ? ty + text.h : end) - area.y;
        }
    } else {
        int ax = gloss ? VOL_CL_X : VOL_PANEL_X;
        area = (rect_t){ ax, VOL_PANEL_Y, I(top, W_W) - 2 * ax, VOL_PANEL_H };
        int x = area.x + VOL_PANEL_PAD, bw = area.w - 2 * VOL_PANEL_PAD - VOL_PANEL_TEXT;
        bar = (rect_t){ x, area.y + (area.h - VOL_PANEL_BAR) / 2, bw, VOL_PANEL_BAR };
        text = (rect_t){ x + bw, area.y, VOL_PANEL_TEXT, area.h };
    }
    if (I(vol, W_Y) != area.y) widget_move_resize(vol, area.x, area.y, area.w, area.h);
    widget_set_visible(vol, 0, 0);
    if (label) widget_set_visible(label, 0, 0);
    int max = widget_get_prop_int(vol, "max", 100), level = widget_get_prop_int(vol, "value", 0);
    vd.vol_dialog = top;
    vd.vol_drawn = level;
    if (!vd.vol_timer) vd.vol_timer = timer_add(vol_poll, (void *)0, VOL_POLL_MS);
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), s[12] = { 'V', 'o', 'l', 'u', 'm', 'e', ' ' },
             k = np ? 7 : 0;
    if (gloss) { /* no number */
        int L = ipod_light(), bx = 68, bw = 243, y = 271;
        paint_panel(top, canvas, &area, "classic_light_panel_shadow", 0);
        paint_rows_bar(canvas, bx, y, bw, 17, max > 0 ? bw * clamp_step(0, max, level) / max : 0);
        paint_art(top, canvas, L ? "classic_light_spk_quiet_l" : "classic_light_spk_quiet_d", 52,
                  y + 2);
        paint_art(top, canvas, L ? "classic_light_spk_loud_l" : "classic_light_spk_loud_d", 317,
                  y + 2);
        stock_canvas_set_fill_color(canvas, fill);
        return;
    }
    int w = max > 0 ? bar.w * clamp_step(0, max, level) / max : 0;
    if (beside) { /* paper over the times alone; ponytail: the elapsed time's box also takes the
                   * reflection's faint last rows (internals.md) */
        canvas_set_fill_color(canvas, RGBA(0));
        canvas_fill_rect(canvas, lo.x, lo.y, lo.w, lo.h);
        canvas_fill_rect(canvas, hi.x, hi.y, hi.w, hi.h);
    } else if (np) {
        canvas_set_fill_color(canvas, RGBA(0));
        canvas_fill_rect(canvas, area.x, area.y, area.w, area.h);
    } else {
        /* unsigned, as draw_letter's: 0xd9 << 24 is past INT_MAX */
        fill_box(canvas, &area, ((unsigned)LETTER_ALPHA << 24) | FILL_RGB, LETTER_RADIUS);
    }
    /* Pills, like the progress capsule: a non-zero fill is at least round, never a sliver. Over
     * Classic's Now Playing, its own bar. */
    if (!np || !ipod_bar(canvas, bar.x, bar.y, bar.w, w, 0)) {
        fill_box(canvas, &bar, RGBA(np ? TRACK_COLOR : VOL_PANEL_TRACK), bar.h / 2);
        rect_t on = { bar.x, bar.y, w < bar.h ? bar.h : w, bar.h };
        /* Over Now Playing it is the progress bar's own: the accent's fill */
        if (w) fill_box(canvas, &on, RGBA(np ? accent_tone(TONE_LIGHT) : 0xffffff), bar.h / 2);
    }
    stock_canvas_set_fill_color(canvas, fill);
    if (beside) {
        vol_end(top, canvas, "classic_light_vol_lo", lo.x + lo.w, 1, &bar);
        vol_end(top, canvas, "classic_light_vol_hi", hi.x, 0, &bar);
        return;
    }
    k += put_num(s + k, (unsigned)clamp_step(0, 999, level));
    draw_centred(canvas, s, k, &text, np ? NP_TIMES_PX : VOL_PANEL_PX, 0xffffffff);
}
