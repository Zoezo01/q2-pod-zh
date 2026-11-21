/* Now Playing's marks in the flat look (flat_pages): More, the play mode and the heart are stock's
 * images, which stay its state and take its touch but go unseen (opacity 0), and are drawn on
 * their parent, under its children (the heart's rating badge stays over it), in the flat look's
 * glyphs (tools/art.py np_*), plain in the half's ink. An image with no glyph of its own is drawn
 * as it is. navigation.c's ringnav_playing hands them over and its background paint draws them. */
#include "navigation.h"

#define NP_MARKS 4 /* the page's three, and room for a page opening as the last one goes */
static void *np_mark[NP_MARKS];
static unsigned np_marked; /* how many: with none a paint looks no further */
static int np_mark_gone(void *w, void *event) {
    (void)event;
    for (unsigned i = 0; i < NP_MARKS; ++i)
        if (np_mark[i] == w) np_mark[i] = (void *)0, --np_marked;
    return 0;
}
void np_mark_add(void *image) {
    unsigned at = NP_MARKS;
    if (!image || !flat_pages()) return;
    for (unsigned i = NP_MARKS; i--;) {
        if (np_mark[i] == image) return;
        if (!np_mark[i]) at = i;
    }
    if (at == NP_MARKS) return; /* no room: it stays stock's, seen */
    np_mark[at] = image, ++np_marked;
    widget_set_opacity(image, 0);
    widget_on(image, EVT_DESTROY, np_mark_gone, image);
}
void np_marks(void *w, void *canvas) {
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
        void *m = np_mark[i];
        if (!m || P(m, W_PARENT) != w || !widget_get_visible(m)) continue;
        const char *image = widget_get_prop_str(m, "image", "");
        char name[40];
        unsigned bitmap[64]; /* bitmap_t: width @0, height @4 */
        for (unsigned k = 0; k < sizeof glyphs / sizeof *glyphs; ++k)
            if (!tk_strcmp(image, glyphs[k][0])) {
                tk_snprintf(name, sizeof name, FLAT_ART "%s_%s", glyphs[k][1],
                            ipod_light() ? "l" : "d");
                image = name;
                break;
            }
        if (!*image || widget_load_image(m, image, bitmap)) continue;
        int bw = (int)bitmap[0], bh = (int)bitmap[1], src[4] = { 0, 0, bw, bh },
            dst[4] = { I(m, W_X) + (I(m, W_W) - bw) / 2, I(m, W_Y) + (I(m, W_H) - bh) / 2, bw, bh };
        canvas_draw_image(canvas, bitmap, src, dst);
    }
}
