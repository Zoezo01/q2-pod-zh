/* A card app's icon in the status bar (addon.c addon_bar_icon: a VPN's while connected), in the
 * icons' row left of Wi-Fi, made the first time one is asked for. bar_sync (navigation.c) says
 * whether it has room (BATT_ROOM): on 0 it hides. */
#include "navigation.h"

static void *bar_addon __attribute__((section(".scratch")));
void bar_addon_sync(int room) {
    const char *icon = room ? addon_bar_icon() : (void *)0;
    if (!bar_addon) {
        if (!icon) return;
        bar_addon = image_create(P(st.bar_wifi, W_PARENT), 0, I(st.bar_wifi, W_Y),
                                 addon_bar_width(), I(st.bar_wifi, W_H));
        if (!bar_addon) return;
        widget_set_name(bar_addon, "img_addon");
        image_set_draw_type(bar_addon, IMAGE_DRAW_CENTER);
        widget_restack(bar_addon, widget_index_of(st.bar_wifi));
    }
    int on = icon != 0, changed = 0;
    if (on && tk_strcmp(widget_get_prop_str(bar_addon, "image", ""), icon)) {
        image_base_set_image(bar_addon, icon);
        changed = 1;
    }
    int w = on ? addon_bar_width() : I(bar_addon, W_W); /* its own width: Wi-Fi's is 14 px */
    if (I(bar_addon, W_Y) != I(st.bar_wifi, W_Y) ||
        I(bar_addon, W_W) != w) { /* as the look moves Wi-Fi's */
        widget_move_resize(bar_addon, I(bar_addon, W_X), I(st.bar_wifi, W_Y), w, I(bar_addon, W_H));
        widget_layout_children(P(bar_addon, W_PARENT));
    }
    if (on != !!widget_get_visible(bar_addon)) {
        widget_set_visible(bar_addon, on, 0);
        widget_layout_children(P(bar_addon, W_PARENT));
        changed = 1;
    }
    /* set mid-paint: this frame may not draw it (bar_state_sync's reason), so the bar again */
    if (changed && !st.bar_timer) st.bar_timer = timer_add(bar_repaint, (void *)0, 0);
}
