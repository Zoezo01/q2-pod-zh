/* Up Next (docs/internals.md#queue-menu): View Queue's list while a shuffle is on, the tracks
 * after the playing one in play order. navigation.c's qm_run opens it (up_next), ringnav_keylong
 * hands a Play/Pause hold on a row to un_hold, and page_closed tells it the page went
 * (upnext_closed). */
#include "offsets.inc"
#include "peq.h"
#include "playback.h"
#include "stock.h"

#define UP_NEXT_ROWS 100                                   /* Up Next's snapshot cap */
#define SEL "_ringnav_index"                               /* navigation.c's */
unsigned queue_hash(void *q);                              /* navigation.c */
const char *track_name(char *buf, unsigned size, void *t); /* coverflow.c */
static struct {
    void *page;                 /* Up Next's page, one at a time */
    unsigned idx[UP_NEXT_ROWS]; /* its rows' queue indices */
    void *view;                 /* its list */
    unsigned n, hash, row;      /* its rows, the queue_hash they were taken from, a hold's */
} un __attribute__((section(".scratch")));

/* View Queue while a shuffle is on (Up Next): the tracks after the playing one in the order they
 * play, which a shuffle's order differs from the queue's that stock's playerqueue_page lists. A row
 * plays its track as stock's does (player_start_queue), and the page closes, as its snapshot is
 * then stale. ponytail: a snapshot taken on open (and again on a hold's move, un_move), capped at
 * UP_NEXT_ROWS; the page does not follow the playing track, and a queue replaced while it is open
 * (a folder play's next folder) leaves a row's tap naming another track; a hold checks. */
static int un_play(void *ctx, void *event) {
    void *q = P(mcl_pdeqplaylist, 0);
    unsigned at = un.idx[(long)ctx];
    if (q && at < deque_size(q)) player_start_queue((int)at);
    navigator_back();
    return 0;
}
/* The rows, taken again; sel, if not -1, the row selected. */
static void un_fill(int sel) {
    void *q = P(mcl_pdeqplaylist, 0);
    unsigned *idx = un.idx, n = un.n = playback_upcoming(idx, UP_NEXT_ROWS);
    un.hash = q ? queue_hash(q) : 0;
    void *view = un.view =
        page_list(un.page, un.page, 0, n ? "View Queue" : "Nothing to play next", n, 64);
    char name[512];
    for (unsigned i = 0; i < n; i++) { /* the title over the artist, if tagged */
        void *t = deque_at(q, idx[i]);
        const char *artist = P(t, REC_ARTIST);
        page_row_detail(view, i, track_name(name, sizeof name, t), artist ? artist : "", un_play);
    }
    if (sel >= 0 && n) ringnav_select(view, sel, (int)n);
}
void up_next(int (*closed)(void *, void *), int (*keyup)(void *, void *)) {
    if (un.page || !(un.page = page_open("upnext_page", closed, keyup))) return;
    un_fill(-1);
}
/* A Play/Pause hold on a row (un_hold, deferred out of its dispatch): the row's track plays next
 * (playback_move) and the rows are taken again with it at row 0, the selection there. A row already
 * next stays there. A queue changed under the page, or a row no longer up next (played since),
 * takes the rows again and says "Queue changed"; a page gone or a row out of range does nothing. */
static int un_move(const void *unused) {
    unsigned row = un.row, up[UP_NEXT_ROWS], n = playback_upcoming(up, UP_NEXT_ROWS), i = 0;
    void *q = P(mcl_pdeqplaylist, 0);
    if (!un.page || row >= un.n || !q) return 0;
    unsigned at = un.idx[row];
    while (i < n && up[i] != at) ++i;
    int stale = queue_hash(q) != un.hash || i == n || (i && playback_move(at) < 0);
    un_fill(0);
    if (stale) toast("Queue changed");
    return 0;
}
int un_hold(void *top) {
    int row = top && top == un.page ? widget_get_prop_int(un.view, SEL, -1) : -1;
    if (row < 0 || !timer_add(un_move, (void *)0, 0)) return 0;
    un.row = (unsigned)row;
    return 1;
}

/* navigation.c's page_closed: the page is gone, so the next View Queue opens a new one. */
void upnext_closed(void *page) {
    if (page == un.page) un.page = 0;
}
