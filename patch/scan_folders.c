/* Scan Folders (stock's specfolder_page; docs/internals.md, "Scan Folders' rows"). */
#include "navigation.h"

extern int stock_specfolder_click_trampoline(void *, void *);

#define TICK_COLUMN 81 /* the row click's slti: a tap left of it ticks, past it opens */

/* The list shows folders only, so specfolder_enter refuses a folder with none inside and stock
 * says "Folder not found". An album's folder is what's picked here: that tap goes to the column.
 * ponytail: a folder that does open is listed twice on the tap, here and by specfolder_enter. */
int ringnav_specfolder_click(void *ctx, void *event) {
    void *row = table_row_of(P(event, EVENT_TARGET));
    if (row && I(event, EVENT_X) >= TICK_COLUMN) {
        char url[1024] = "";
        get_specfolderurl(I(row, ROW_INDEX), url, sizeof url);
        if (toolsLoadDirectory(url) <= 0 || !toolsCheckTypeDeque(P(tools_pdeq_directory, 0), 4))
            I(event, EVENT_X) = 0;
    }
    return stock_specfolder_click_trampoline(ctx, event);
}
