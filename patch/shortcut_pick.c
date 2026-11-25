/* Display → Appearance → Shortcut's picker (navigation.c ringnav_display, as Theme's): the fixed
 * targets in the order the row once cycled them, then the card's apps by name, then its games by
 * name (addon_sorted), the chosen one checked. A press saves it (shortcut_set), lays Home out
 * again, writes the Shortcut row's label and goes back. A row's ctx: a fixed value, or
 * PICK_SLOT + an app's slot, which stays its app's. */
#include "navigation.h"

#define PICK_SLOT 16
void coverflow_home_layout(void); /* coverflow.c */

static int shortcut_pick(void *ctx, event_t *event) {
    int c = (int)(long)ctx;
    const char *id = c >= PICK_SLOT ? addon_slot_id(c - PICK_SLOT) : (void *)0;
    if (c >= PICK_SLOT && !id) return 0; /* gone from the card since the page opened */
    shortcut_set(id ? SHORTCUT_APP : c, id);
    coverflow_home_layout();
    setting_text(SETTING_SHORTCUT);
    navigator_back();
    return 0;
}

static void pick_row(widget_t *view, int on, const char *name, int ctx) {
    widget_set_text_utf8(
        list_row(view, on ? "select" : "unselect", shortcut_pick, (void *)(long)ctx), name);
}

void shortcut_rows(widget_t *view) {
    /* Favourites, Shuffle, Rated Albums, Playlists, Artists, Albums, Games, Podcasts, Audiobooks,
     * None */
    static const unsigned char fixed[] = { 3, 5, 4, 7, 1, 2, 6, 8, 9, SHORTCUT_NONE };
    int s = shortcut(), slots[APPS_MAX];
    for (unsigned k = 0; k < sizeof fixed; ++k)
        pick_row(view, s == fixed[k], tr(shortcut_names[fixed[k]]), fixed[k]);
    for (int game = 0; game < 2; ++game)
        for (int k = 0, n = addon_sorted(game, slots); k < n; ++k) {
            const char *id = addon_slot_id(slots[k]);
            pick_row(view, s == SHORTCUT_APP && !strcmp(id, shortcut_app()), addon_name_of(id),
                     PICK_SLOT + slots[k]);
        }
}
