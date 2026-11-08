/* Home's Library and Streaming: their pages and the lists Home's Split shows. */
#include "navigation.h"

/* A Library row titled text; into: one that opens another list, with stock's own list_into
 * trailing image as the stock Local Music categories place it. Returns the row's list_item. */
static void *library_row(void *view, const char *icon, int (*click)(void *, void *), void *ctx,
                         const char *text, int into) {
    void *label = list_row_core(view, icon, click, ctx, into ? 210 : 260, into);
    widget_set_text_utf8(label, text);
    return P(P(label, W_PARENT), W_PARENT);
}

/* localmusic_page_init: stock's 11 category rows (0x5247ec), by index (get_localmusic_showinfo
 * 0x5012d0): Update Local Music, All Songs, Album, Artist, Genre, Hi-Res, My Fav, Frequent,
 * Recent, Recently Added, Playlist. */
enum {
    L_UPDATE,
    L_SONGS,
    L_ALBUMS,
    L_ARTISTS,
    L_GENRES,
    L_HIRES,
    L_FAV,
    L_FREQUENT,
    L_RECENT,
    L_ADDED,
    L_PLAYLISTS,
    L_STOCK
};

/* Every Library row, stock's and the added ones: its label takes the list titles' role. In a set
 * without Library icons (Classic) it also drops its icon and starts its label at the set's
 * text inset on screen, its right edge kept. */
static void library_look(void *view) {
    const theme_t *T = theme();
    int bare = !geom_val(V_LIB_ART);
    if (!bare && !T->font[ROLE_TITLE] && !T->font_px[ROLE_TITLE]) return;
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *row = widget_get_child(view, i);
        void *button = widget_count_children(row) ? widget_get_child(row, 0) : (void *)0;
        for (unsigned j = 0, k = button ? widget_count_children(button) : 0; j < k; ++j) {
            void *c = widget_get_child(button, j);
            if (tk_strcmp(widget_get_type(c), "hscroll_label")) {
                if (bare) widget_set_visible(c, 0, 0);
                continue;
            }
            theme_font(c, ROLE_TITLE, FONT_ROW);
            if (bare) {
                int x = geom_val(V_TEXT_X) - I(button, W_X);
                widget_move_resize(c, x, I(c, W_Y), I(c, W_W) + I(c, W_X) - x, I(c, W_H));
            }
            break;
        }
    }
}

/* 1 when the Library leads with Search, in a set without Library icons (Classic): the pull-down's
 * own stock search (0x5241c4, which ignores its arguments) shown, as Home's Split list hides the
 * pull. Home's pane counts Shuffle's row from it (coverflow.c coverflow_home_row). */
int library_search(void) { return !geom_val(V_LIB_ART); }

/* The Library: Search (library_search), one Shuffle row opening its menu, browsing, the user's own
 * lists, listening history, the card's other media, then upkeep. The added rows' buttons have no
 * name, so stock's row click (atoi of the name, 0x5241fc) never sees them. */
int ringnav_localmusic(void *win, void *ctx) {
    int result = stock_localmusic_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_localmusic", 1) : (void *)0;
    if (!view || widget_count_children(view) != L_STOCK) return result;
    void *stock[L_STOCK];
    for (int i = 0; i < L_STOCK; i++) stock[i] = widget_get_child(view, i);
    unsigned n = 0; /* each row moves to n as it comes, so Update Local Music, left, ends last */
    if (library_search())
        widget_restack(library_row(view, (void *)0, stock_search, 0, "Search", 1), n++);
    widget_restack(library_row(view, "local_shuffle", shuffle_menu, 0, "Shuffle", 1), n++);
    static const unsigned char middle[] = { L_ARTISTS,   L_ALBUMS, L_SONGS, L_GENRES,
                                            L_PLAYLISTS, L_FAV,    L_ADDED, L_RECENT };
    for (unsigned i = 0; i < sizeof middle; i++)
        if (middle[i] == L_GENRES) { /* browsing by a tag, as Genre */
            widget_restack(stock[L_GENRES], n++);
            widget_restack(library_row(view, "local_era", era_menu, 0, "Era", 1), n++);
        } else if (middle[i] == L_FAV) { /* My Fav is Ratings' Loved, its row kept hidden */
            widget_restack(library_row(view, "local_fav", ratings_menu, 0, "Ratings", 1), n++);
            widget_set_visible(stock[L_FAV], 0, 0);
            widget_restack(stock[L_FAV], n++);
        } else
            widget_restack(stock[middle[i]], n++);
    widget_restack(library_row(view, "local_frequentplay", most_played, 0, "Most Played", 1), n++);
    widget_restack(stock[L_FREQUENT], n++);
    widget_restack(stock[L_HIRES], n++);
    widget_restack(library_row(view, "local_longsongs", long_songs, 0, "Long Songs", 1), n++);
    /* Podcasts, Audiobooks, Photos, Books and Videos, each only with its folder; then iPod's Games.
     */
    static const char *const icons[] = { "local_podcasts", "local_audiobooks", "local_photos",
                                         "local_books", "local_videos" };
    char path[sizeof st.media_root];
    for (int k = 0; k < VIDEOS; k++)
        if (media_find(k + 1, path))
            widget_restack(
                library_row(view, icons[k], media_click, (void *)(long)(k + 1), MEDIA[k], 1), n++);
    widget_restack(library_row(view, "local_games", games_open, 0, "Games", 1), n++);
    if (scrobble_ready())
        widget_restack(
            library_row(view, "local_scrobble", upload_scrobbles, 0, "Upload Scrobbles", 0), n++);
    library_look(view);
    return result;
}

/* Home's Library list (coverflow.c home_library), filled each time it opens, as the page is: the
 * same rows in the same order, whatever card, user or app adds. Stock's constructor takes a window
 * and finds the rows' view by name, so list, which holds one of that name, stands in. What else it
 * sets on the window goes again, list being none: the play gif's timer and its handlers. */
void library_rows(void *list) {
    ringnav_localmusic(list, (void *)0);
    ((int (*)(void *, void *))LOCALMUSIC_BG)(list, (void *)0);
    widget_off_by_ctx(list, list);
}

/* Streaming's AirPlay row: stock's own receiver (Shairport 1.1.1, AirPlay 1), whose switch is on
 * System settings' Network service page; that page, one press from Streaming. */
static int airplay_open(void *ctx, void *event) {
    navigator_to("systemset/netservice_page");
    return 0;
}

/* Whether w, or a widget in it, shows the image name. */
static int shows_image(void *w, const char *name) {
    if (!tk_strcmp(widget_get_prop_str(w, "image", ""), name)) return 1;
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i)
        if (shows_image(widget_get_child(w, i), name)) return 1;
    return 0;
}

/* Home pane's Streaming and Games captions, and the card's free space in tenths of a GB (0: no card
 * mounted, whose dir shares /mnt's device), read with home_counts, never on a wheel move: the
 * Streaming page's rows with the app scan it makes, less Configuration and those hidden; and the
 * Games page's built-ins and card games. */
void home_pane_read(int *services, int *games, int *free_tenths) {
    char hide[SVC_HIDE];
    unsigned card[36], mnt[36], fs[32];
    int n = 0, total = (st.svc_n = addon_scan()) + 5;
    svc_hidden(hide);
    for (int i = 0; i < total; ++i)
        if (!svc_find(hide, i)) ++n;
    *services = n;
    *games = GAMES_BUILTIN + addon_games_last();
    /* statfs on MIPS o32: f_bsize word 1, f_bavail word 7 (netupdate.c). In binary GB, as About's
     * Remaining: 209715 blocks of 512 B to a tenth, so 32 bits hold a 2 TB card's. */
    *free_tenths = !__xstat(3, "/mnt/mmc", card) && !__xstat(3, "/mnt", mnt) && card[0] != mnt[0] &&
                           !statfs("/mnt/mmc", fs)
                       ? (int)(fs[7] * (fs[1] >> 9) / 209715)
                       : 0;
}

/* A Streaming row tagged as service i (svc), hidden when STREAMHIDE holds it. */
static void svc_row(void *row, int i, char *hide) {
    prop(row, SVC_PROP, i + 1);
    if (svc_find(hide, i)) widget_set_visible(row, 0, 0);
}

/* stream_page_init: stock's rows but Baidu Netdisk (a Chinese cloud drive, signed in to from
 * Baidu's app: its row, the one with list_baidu's icon, removed), then Spotify (spotify.c), which
 * opens its browser, the card apps on the card (addon.c: Deezer, Soulseek, File Browser… as System
 * settings' Apps or the user put them there), Tidal Downloads, Internet Radio (radio.c), AirPlay
 * and Configuration. Their buttons have no name, so stock's row click never sees them. Smart
 * Wi-Fi's radio comes back first. */
int ringnav_stream(void *win, void *ctx) {
    smart_wake();
    int result = stock_stream_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_streamsset", 1) : (void *)0;
    for (int i = view ? (int)widget_count_children(view) : 0; i-- > 0;)
        if (shows_image(widget_get_child(view, (unsigned)i), "list_baidu"))
            widget_destroy(widget_get_child(view, (unsigned)i));
    if (view) {
        char hide[SVC_HIDE];
        int i = 1;
        st.svc_n = addon_scan(); /* at most APPS_MAX */
        svc_hidden(hide);
        for (unsigned c = 0, stock = widget_count_children(view); c < stock; ++c)
            if (shows_image(widget_get_child(view, c), "list_tidal"))
                svc_row(widget_get_child(view, c), 0, hide);
        svc_row(library_row(view, "stream_spotify", spot_open, 0, "Spotify", 1), i++, hide);
        for (int k = 0; k < st.svc_n; ++k)
            svc_row(library_row(view, addon_icon(k), addon_open, (void *)(long)k, addon_name(k), 1),
                    i++, hide);
        svc_row(library_row(view, "list_tidal", tidal_downloads, 0, "Tidal Downloads", 1), i++,
                hide);
        svc_row(library_row(view, "stream_radio", radio_open, 0, "Internet Radio", 1), i++, hide);
        svc_row(library_row(view, "stream_airplay", airplay_open, 0, "AirPlay", 1), i, hide);
        library_row(view, "stream_config", stream_config, 0, "Configuration", 1);
    }
    return result;
}

/* Home's Streaming list (coverflow.c home_streaming), filled each time it opens, as the page is. As
 * library_rows: stock's constructor takes a window and finds the rows' view by name, so list, which
 * holds one of that name, stands in; it finds no view_navbar there. What it sets on a window goes
 * again: its play gif's timer and its handlers. */
void stream_rows(void *list) {
    ringnav_stream(list, (void *)0);
    ((int (*)(void *, void *))STREAM_BG)(list, (void *)0);
    widget_off_by_ctx(list, list);
    library_look(widget_lookup(list, "scroll_view_streamsset", 1));
}
