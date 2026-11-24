/* System settings → Power → Sleep Timer: stock's row reads the time left while the timer runs, as
 * an iPod's Sleep Timer did, so it shows from the menu without opening the timer. */
#include "navigation.h"

/* Stock's own count: g_sleep_time, the seconds left, which the Sleep Timer's OK sets from its
 * fields and its switch (g_sleepflag) and a power-off zero; check_to_poweroption counts it down on
 * the first UI pass of each new second while the switch is on, and at 0 clears the switch too and
 * powers off. The row is the one systemset_powermanager_page_init builds first: its list_item and
 * button both named "0" (its index), the title the button's hscroll_label with tr_text
 * powerm_sleep. Running, the title reads "Sleep Timer: 45 min", from an hour "Sleep Timer: 1:30",
 * minutes rounded up, so it reads what was just set where the page's own fields round down (00:44).
 * The longest, "59 min" and "23:59", are 176 and 163 px at Classic's 27, 181 and 171 at the
 * others' 24. Otherwise stock's tr_text again, translated.
 * ponytail: read as Power opens and comes back to the front, it doesn't tick while Power stays
 * open; the page's timer would. */
static int sleep_text(void *ctx, event_t *event) {
    widget_t *view = widget_lookup(ctx, "scroll_view_powermanager", 1),
             *item = view ? widget_lookup(view, "0", 0) : (void *)0,
             *button = item ? widget_lookup(item, "0", 0) : (void *)0;
    widget_t *title = button ? widget_lookup_by_type(button, "hscroll_label", 0) : (void *)0;
    if (!title) return 0;
    int s = I(g_sleep_time, 0), left = (s + 59) / 60;
    char t[96];
    if (!g_sleepflag || s <= 0) {
        widget_set_tr_text(title, "powerm_sleep");
        return 0;
    }
    if (left < 60)
        tk_snprintf(t, sizeof t, tr("Sleep Timer: %d min"), left);
    else
        tk_snprintf(t, sizeof t, tr("Sleep Timer: %d:%02d"), left / 60, left % 60);
    widget_set_text_utf8(title, t);
    return 0;
}

void sleep_row(widget_t *win) {
    if (!win) return;
    sleep_text(win, (void *)0);
    widget_on(win, EVT_WINDOW_FOREGROUND, sleep_text, win);
}
