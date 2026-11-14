/* Retain a theme's bold intent when its selected face falls back to stock's full Unicode font.
 * Shared by Cover Flow captions, its track panel, themed rows and Now Playing titles. */
#include "navigation.h"

#define WEIGHT_PROP "_q2_bold_fallback"

void font_weight_mark(void *w, int bold) {
    bold = bold != 0;
    if (w && widget_get_prop_int(w, WEIGHT_PROP, 0) != bold)
        widget_set_prop_int(w, WEIGHT_PROP, bold);
}

/* The existing border hook runs after native self painting. Repeat only marked labels' own
 * foreground one pixel right, keeping their native shaping, marquee, clip and style. Calling
 * the vtable self painter does not dispatch paint events or re-enter border painting. */
void font_weight_paint(void *w, void *canvas) {
    if (!w || !canvas || !widget_get_prop_int(w, WEIGHT_PROP, 0)) return;
    const unsigned *text = widget_get_text(w);
    if (!text || !*text) return;
    canvas_translate(canvas, 1, 0);
    widget_vtable_on_paint_self(w, canvas);
    canvas_translate(canvas, -1, 0); /* restore even when the native painter returns an error */
}
