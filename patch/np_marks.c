/* Now Playing's marks in the flat look (flat_pages): More, the play mode and the heart are stock's
 * images, which stay its state and take its touch but go unseen (opacity 0), and are drawn on
 * their parent, under its children (the heart's rating badge stays over it), in the flat look's
 * glyphs (tools/art.py np_*), plain in the half's ink. An image with no glyph of its own is drawn
 * as it is. np_theme hands a local page's over (np_marks_page) as the page opens and at a theme
 * change, radio.c its heart, and navigation.c's background paint draws them. */
#include "navigation.h"

/* two local pages' three (one opens over another that stays) and Internet Radio's heart; past it a
 * page's marks all stay stock's, so no page is half flat */
#define NP_MARKS 7
static widget_t *np_mark[NP_MARKS];
static unsigned np_marked; /* how many: with none a paint looks no further */
static int np_mark_gone(void *w, event_t *event) {
    (void)event;
    for (unsigned i = 0; i < NP_MARKS; ++i)
        if (np_mark[i] == w) np_mark[i] = (void *)0, --np_marked;
    return 0;
}
static int np_held(const widget_t *image) {
    for (unsigned i = 0; i < NP_MARKS; ++i)
        if (np_mark[i] == image) return 1;
    return 0;
}
/* A page's images, n of them, all or none: those not held yet, if they all fit */
void np_mark_add(widget_t *const *image, unsigned n) {
    unsigned fresh = 0;
    if (!flat_pages()) return;
    for (unsigned k = 0; k < n; ++k) fresh += image[k] && !np_held(image[k]);
    if (!fresh || np_marked + fresh > NP_MARKS) return; /* no room: they stay stock's, seen */
    for (unsigned k = 0, i = 0; k < n; ++k) {
        if (!image[k] || np_held(image[k])) continue;
        while (np_mark[i]) ++i;
        np_mark[i] = image[k], ++np_marked;
        widget_set_opacity(image[k], 0);
        widget_on(image[k], EVT_DESTROY, np_mark_gone, image[k]);
    }
}
/* Local Now Playing's More, play mode and heart */
void np_marks_page(widget_t *win) {
    static const char *const names[] = { "img_more", "img_playmode", "img_fav" };
    widget_t *image[3];
    if (!win) return;
    for (unsigned i = 0; i < 3; ++i) image[i] = widget_lookup(win, names[i], 1);
    np_mark_add(image, 3);
}
void np_marks(widget_t *w, canvas_t *canvas) {
    static const char *const glyphs[][2] = {
        { "play_more", "np_more" },    { "play_moredown", "np_more_q" },
        { "play_fav", "np_heart_on" }, { "play_unfav", "np_heart_off" },
        { "play_order", "np_order" },  { "play_random", "np_shuffle" },
        { "play_cycle", "np_repeat" }, { "play_cycle1", "np_repeat1" },
    };
    if (!np_marked || !w || !P(canvas, CANVAS_LCD)) return;
    if (!flat_pages()) { /* another theme picked with the page open: stock's images again */
        for (unsigned i = 0; i < NP_MARKS; ++i)
            if (np_mark[i]) widget_set_opacity(np_mark[i], 255), np_mark[i] = (void *)0;
        np_marked = 0;
        return;
    }
    for (unsigned i = 0; i < NP_MARKS; ++i) {
        widget_t *m = np_mark[i];
        if (!m || P(m, W_PARENT) != w || !widget_get_visible(m)) continue;
        const char *image = widget_get_prop_str(m, "image", "");
        char name[40];
        unsigned bitmap[64]; /* bitmap_t: width @0, height @4 */
        /* An image that names none answers null, not the default. More is one: stock draws it as
         * its style's background, play_more or, pressed, play_moredown. Another, the radio's heart
         * before its first state, has nothing to draw. */
        if (!image || !*image) {
            if (tk_strcmp(widget_get_prop_str(m, "name", ""), "img_more")) continue;
            image = tk_strcmp(widget_get_prop_str(m, "state_for_style", ""), "pressed")
                        ? "play_more"
                        : "play_moredown";
        }
        for (unsigned k = 0; k < sizeof glyphs / sizeof *glyphs; ++k)
            if (!tk_strcmp(image, glyphs[k][0])) {
                tk_snprintf(name, sizeof name, FLAT_ART "%s_%s", glyphs[k][1],
                            ipod_light() ? "l" : "d");
                image = name;
                break;
            }
        if (widget_load_image(m, image, bitmap)) continue;
        int bw = (int)bitmap[0], bh = (int)bitmap[1], src[4] = { 0, 0, bw, bh },
            dst[4] = { I(m, W_X) + (I(m, W_W) - bw) / 2, I(m, W_Y) + (I(m, W_H) - bh) / 2, bw, bh };
        canvas_draw_image(canvas, bitmap, src, dst);
    }
}
