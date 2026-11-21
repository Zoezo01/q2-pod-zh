/* Scan Folders (stock's specfolder_page; docs/internals.md, "Scan Folders' rows"). */
#include "navigation.h"

extern int stock_specfolder_click_trampoline(void *, event_t *);
extern int stock_specfolder_enter_trampoline(int, int);
extern int stock_specfolder_back_trampoline(int *);
extern int stock_specfolder_scan_trampoline(void *, event_t *);
extern int stock_cancelchoice_trampoline(void *, event_t *);

#define TICK_COLUMN 81 /* the row click's slti: a tap left of it ticks, past it opens */

/* The list shows folders only, so specfolder_enter refuses a folder with none inside and stock
 * says "Folder not found". An album's folder is what's picked here: that tap goes to the column.
 * ponytail: a folder that does open is listed twice on the tap, here and by specfolder_enter. */
int ringnav_specfolder_click(void *ctx, event_t *event) {
    widget_t *row = table_row_of(P(event, EVENT_TARGET));
    if (row && I(event, EVENT_X) >= TICK_COLUMN) {
        char url[1024] = "";
        get_specfolderurl(I(row, ROW_INDEX), url, sizeof url);
        if (toolsLoadDirectory(url) <= 0 || !toolsCheckTypeDeque(P(tools_pdeq_directory, 0), 4))
            I(event, EVENT_X) = 0;
    }
    return stock_specfolder_click_trampoline(ctx, event);
}

/* Ticks across folders. Stock's ticks are the listed folder's alone: specfolder_enter and
 * specfolder_back start the record again (batch_init_selectrecord), so albums ticked under two
 * artists never reached one scan, and Scan in a folder with none ticked said "Folder not
 * specified". picks holds every ticked folder's path while the page is open: the listed folder's
 * ticks are taken before stock leaves it and put back on the one it lists next, and Scan takes
 * them all. ponytail: each level change compares its rows with every pick; sort the picks if
 * thousands are ever ticked and browsed past. */
static struct {
    char **url;
    unsigned n;
} picks;

static void picks_clear(void) {
    while (picks.n) free(picks.url[--picks.n]);
}

static int picked(const char *url) {
    for (unsigned i = 0; i < picks.n; i++)
        if (!strcmp(picks.url[i], url)) return (int)i;
    return -1;
}

/* The listed folder's ticks into picks: a ticked row's path joins, an unticked one's leaves.
 * ponytail: a path that can't be kept (no memory) is a tick lost at the next level change. */
static void picks_keep(void) {
    for (int i = 0, n = get_specfoldersize(); i < n; i++) {
        char url[1024] = "";
        get_specfolderurl(i, url, sizeof url);
        int at = picked(url), on = batch_get_selectitem(i) == 1;
        if (on && at < 0) {
            char **more =
                picks.n & 15 ? picks.url : realloc(picks.url, (picks.n + 16) * sizeof *more);
            char *copy = more ? strdup(url) : 0;
            if (more) picks.url = more;
            if (copy) picks.url[picks.n++] = copy;
        } else if (!on && at >= 0) {
            free(picks.url[at]);
            picks.url[at] = picks.url[--picks.n];
        }
    }
}

/* ... and back onto the folder stock has just listed, whose record it cleared. The last tick's
 * answer is batch_set_selectitem's "every row is ticked", which stock's select-all picture
 * reads from g_allselect_flag once this returns. */
static void picks_show(void) {
    for (int i = 0, n = picks.n ? get_specfoldersize() : 0; i < n; i++) {
        char url[1024] = "";
        get_specfolderurl(i, url, sizeof url);
        if (picked(url) >= 0) g_allselect_flag = (unsigned char)batch_set_selectitem(i);
    }
}

/* The page's init lists the card with index -1: nothing is ticked yet. */
int ringnav_specfolder_enter(int index, int yoffset) {
    if (index < 0)
        picks_clear();
    else
        picks_keep();
    int entered = stock_specfolder_enter_trampoline(index, yoffset);
    if (entered == 1) picks_show();
    return entered;
}

/* 0 is Return at the card's own list: stock has left the page. */
int ringnav_specfolder_back(int *yoffset) {
    picks_keep();
    int listed = stock_specfolder_back_trampoline(yoffset);
    if (listed)
        picks_show();
    else
        picks_clear();
    return listed;
}

/* Cancel unticks every folder, the ones not listed too. */
int ringnav_cancelchoice(void *ctx, event_t *event) {
    picks_clear();
    return stock_cancelchoice_trampoline(ctx, event);
}

/* Scan (stock's unnamed 0x540658) scans the listed folder's ticks; this is its body over every
 * pick. With none it is stock's, for its "Folder not specified". A folder ticked along with one
 * inside it is scanned twice, which the scan's existing-song check (ringnav_scan_refresh) makes
 * a second reading, not a second copy. */
int ringnav_specfolder_scan(void *ctx, event_t *event) {
    picks_keep();
    void *list = picks.n ? _create_deque("char*") : 0;
    if (!list) return stock_specfolder_scan_trampoline(ctx, event);
    player_stop();
    deque_init(list);
    for (unsigned i = 0; i < picks.n; i++) _deque_push_back(list, picks.url[i]);
    navigator_back();
    int scan[4] = { (int)list, 0, 1, 0 }; /* the folders, and 1: a folder scan */
    navigator_to_with_context("dialog/updatemusic_dialog", scan);
    deque_destroy(list);
    clear_specfoldervariate();
    picks_clear();
    return 0;
}
