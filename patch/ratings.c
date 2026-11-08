/* Ratings: a song's or album's hearts, Now Playing's heart and its picker, the Library's Ratings
 * lists, and the card's copy of the ratings. */
#include "navigation.h"

/* Ratings (docs/internals.md#ratings): 0 to 5 hearts a song. 5 is stock's My Fav, so Now Playing's
 * heart, the queue menu's Favourites rows and Loved stay one; 1 to 4 are kept here by listen_key,
 * its low three bits holding the hearts, read once into a table calloc'd on first use. */
#define RATINGS_FILE "/mnt/data/ringnav-ratings"
/* Albums keep 1 to 5 hearts apart from their songs, by coverflow.c's album_id: 5 is the album's
 * favourite, there being no stock one. */
#define ALBUM_RATINGS_FILE "/mnt/data/ringnav-album-ratings"
/* Now Playing's heart click (playing_page_init binds it to img_fav with the window): stock's My
 * Fav toggled for the playing track (manage_myfav), and the heart's image with it. */
#define FAV_CLICK ((int (*)(void *, void *))0x52b154)

/* The songs' table, or with album the albums'. */
static int rate_io(int album, int write) {
    const char *f = album ? ALBUM_RATINGS_FILE : RATINGS_FILE;
    const char *tmp = album ? ALBUM_RATINGS_FILE ".tmp" : RATINGS_FILE ".tmp";
    return blob_io(f, tmp, album ? st.album_rates : st.rates, RATE_SLOTS * sizeof *st.rates, write);
}
static unsigned *rates(int album) {
    unsigned **t = album ? &st.album_rates : &st.rates;
    if (!*t && (*t = calloc(RATE_SLOTS, sizeof **t))) rate_io(album, 0);
    return *t;
}

/* key's slot; with free, the first free one when it has none; -1 when neither. */
static int rate_slot(int album, unsigned key, int free) {
    unsigned *t = rates(album);
    int empty = -1;
    for (int i = 0; t && i < RATE_SLOTS; i++) {
        if (t[i] && (t[i] ^ key) < 8) return i;
        if (!t[i] && empty < 0) empty = i;
    }
    return free ? empty : -1;
}

/* The hearts kept for key, 1 to 4 for a song or 5 for an album, else 0 (a malformed slot too). */
int rate_kept(int album, unsigned key) {
    int i = rate_slot(album, key, 0), n = i < 0 ? 0 : (int)(rates(album)[i] & 7);
    return n <= 4 + album ? n : 0;
}

/* Keeps n hearts for key: a song's 1 to 4, none for 0 and 5 (My Fav's); an album's 1 to 5. 1 when
 * saved, 0 when the table is full, -1 when the file was not written (the slot then as it was). */
int rate_keep(int album, unsigned key, int n) {
    int some = n > 0 && n < 5 + album, i = rate_slot(album, key, some);
    if (i < 0) return !some;
    unsigned *t = rates(album), old = t[i];
    t[i] = some ? (key & ~7u) | (unsigned)n : 0;
    if (rate_io(album, 1)) {
        ratings_copy_due();
        return 1;
    }
    t[i] = old;
    return -1;
}

/* Report only failures from rate_keep. */
int rate_saved(int k) {
    if (k <= 0) toast(k ? "Rating not saved" : "Ratings are full");
    return k > 0;
}

/* The playing local track (the queue menu's types), or 0. */
static void *rate_track(void) {
    unsigned at, n, type = (unsigned)MCL(MCL_TYPE);
    if (type != 1 && (type & 0xf000) != 0xf000) return (void *)0;
    return queue_now(&at, &n);
}

/* "♥♥♥♡♡" for n hearts, in s (16 bytes). */
const char *hearts_text(char *s, int n) {
    char *p = s;
    for (int i = 0; i < 5; i++) p += tk_snprintf(p, 4, i < n ? "\xe2\x99\xa5" : "\xe2\x99\xa1");
    return s;
}

/* A theme's glyph on w (tools/art.py): the half's image, set only on a change. */
static void np_image(void *w, const char *glyph) {
    char name[40];
    tk_snprintf(name, sizeof name, "classic_light_%s_%s", glyph, ipod_light() ? "l" : "d");
    if (w && tk_strcmp(widget_get_prop_str(w, "image", ""), name)) {
        image_base_set_image(w, name);
        widget_invalidate_force(w, (void *)0);
    }
}
/* The badge on an empty heart: the playing track's 1-4 hearts, hidden for none and on a full heart
 * (My Fav, whatever was kept before). */
static void rate_badge(void) {
    if (!st.rate_badge) return;
    void *r = rate_track();
    int n = r ? rate_kept(0, listen_key(r)) : 0;
    if (n && !tk_strcmp(widget_get_prop_str(st.rate_fav, "image", ""), "play_fav")) n = 0;
    /* each set repaints the badge, and this runs every 500 ms: only a change (AWTK's "text" is
     * a wide string widget_get_prop_str doesn't read) */
    if (st.rate_shown == n + 1) return;
    st.rate_shown = n + 1;
    char s[4] = { (char)('0' + n) };
    widget_set_text_utf8(st.rate_badge, s);
    widget_set_visible(st.rate_badge, n != 0, 0);
}

static int rate_tick(const void *unused) {
    if (g_backlight_status) rate_badge();
    return 8; /* RET_REPEAT */
}

static int rate_tap(void *ctx, void *event);
static int rate_out(void *ctx, void *event);
/* The picker goes down with its page or panel (Coverflow's load destroys the page's children). */
static int rate_panel_gone(void *win, void *event) {
    if (win == st.rate_on) st.rate_on = (void *)0;
    st.rate_box = (void *)0; /* rate_panel names it again */
    return 0;
}
/* The picker on win, made on first use: a dark rounded panel, its caption, then five of the
 * heart's own images. */
static void *rate_panel(void *win) {
    void *panel = widget_lookup(win, "rate_panel", 0);
    /* Classic's panel paints itself (ringnav_paint_bg) */
    int gloss = classic_pages();
    if (panel) {
        if (gloss) st.rate_box = panel;
        return panel;
    }
    int h = 110, w = 5 * 56 + 24;
    /* Classic's widget also holds the panel's ring and soft shadow, so they repaint with it
     */
    int mx = gloss ? PANEL_PAD : 0, my = gloss ? PANEL_PAD - PANEL_DROP : 0;
    if (gloss) { /* under the panel, over the page: a tap outside leaves it as Return does */
        void *out = image_create(win, 0, 0, I(win, W_W), I(win, W_H));
        widget_set_name(out, "rate_out");
        widget_set_prop_str(out, "clickable", "true");
        widget_on(out, EVT_CLICK, rate_out, (void *)0);
    }
    panel =
        text(win, (I(win, W_W) - w) / 2 - mx, (I(win, W_H) - h) / 2 - my, w + 2 * mx, h + 2 * mx);
    widget_set_name(panel, "rate_panel");
    if (gloss) {
        st.rate_box = panel;
        widget_set_prop_int(panel, "style:normal:bg_color", 0);
    } else {
        widget_set_prop_int(panel, "style:normal:bg_color",
                            (int)(0xff000000u | FILL_RGB)); /* opaque */
        widget_set_prop_int(panel, "style:normal:round_radius", LETTER_RADIUS);
    }
    void *caption = text(panel, mx, my + 8, w, 28);
    if (gloss) { /* the ink, which Light Mode's style hook flips back */
        widget_set_prop_int(caption, "style:normal:text_color",
                            (int)RGBA(flip_if(ipod_light(), scheme()->ink)));
        widget_set_prop_str(caption, "style:normal:font_name", "classic-light");
        widget_set_prop_int(caption, "style:normal:font_size", 22);
    }
    for (int i = 0; i < 5; i++) {
        /* centred in it, Classic's 32 x 30 heart starts 58 down the panel */
        void *heart = image_create(panel, mx + 12 + i * 56, my + 44 + gloss, 56, 56);
        image_set_draw_type(heart, IMAGE_DRAW_CENTER);
        widget_set_prop_str(heart, "clickable", "true");
        widget_on(heart, EVT_CLICK, rate_tap, (void *)(long)(i + 1));
    }
    widget_on(panel, EVT_DESTROY, rate_panel_gone, win);
    widget_on(win, EVT_DESTROY, rate_panel_gone, win);
    return panel;
}

/* The picker's hearts and caption for rate_pick. */
static void rate_paint(void) {
    void *panel = rate_panel(st.rate_on);
    char t[24] = "Unrated";
    int gloss = classic_pages();
    for (int i = 0; i < 5; i++) {
        void *heart = widget_get_child(panel, (unsigned)i + 1);
        if (gloss)
            np_image(heart, i < st.rate_pick ? "heart_on" : "heart_off");
        else
            image_base_set_image(heart, i < st.rate_pick ? "play_fav" : "play_unfav");
    }
    if (st.rate_pick)
        tk_snprintf(t, sizeof t,
                    !gloss              ? "%d of 5"
                    : st.rate_pick == 1 ? "1 Heart"
                                        : "%d Hearts",
                    st.rate_pick);
    widget_set_text_utf8(widget_get_child(panel, 0), t);
}

/* What's under the picker takes a touch again, or doesn't. */
static void rate_outside(void *win, int on) {
    void *out = widget_lookup(win, "rate_out", 0);
    if (!out) return;
    widget_set_visible(out, on, 0);
    widget_set_sensitive(out, on);
}

/* Closes the picker, and with save rates rate_pick: an album (rate_album) if still Coverflow's
 * centre card, else the playing track, My Fav in or out through the heart's own click and the
 * rest kept here. */
static void rate_close(int save) {
    void *win = st.rate_on;
    if (!win) return;
    st.rate_on = (void *)0;
    void *panel = rate_panel(win);
    widget_set_visible(panel, 0, 0);
    widget_set_sensitive(panel, 0); /* stock's hit test skips only the insensitive, hidden or not */
    rate_outside(win, 0);
    int n = st.rate_pick;
    if (save && st.rate_album) { /* the menu's album, still on its page */
        void *a = window_manager_get_top_window(window_manager()) == win ? qm_record() : (void *)0;
        if (!a || qm_album_id(a) != st.rate_album) {
            toast("Album changed");
            return;
        }
        rate_saved(rate_keep(1, st.rate_album, n));
        return;
    }
    void *r = save ? rate_track() : (void *)0;
    if (!r) return;
    int fav = checkFavExist(r) != 0;
    if (!rate_saved(rate_keep(0, listen_key(r), n))) return;
    if ((n == 5) != fav) FAV_CLICK(st.rate_ctx, (void *)0), ratings_copy_due();
    rate_badge();
}

static int rate_tap(void *ctx, void *event) {
    st.rate_pick = (int)(long)ctx;
    rate_close(1);
    return STOP;
}
static int rate_out(void *ctx, void *event) {
    rate_close(0);
    return STOP;
}

/* The picker over win on pick hearts, for the queue menu's album album or, with 0, the playing
 * track: the wheel moves the rating, Centre saves it and Return leaves it as it was; a tap on a
 * heart saves that many. */
int rate_show(void *win, int pick, unsigned album) {
    if (!win || st.rate_on) return 0;
    st.rate_on = win;
    st.rate_pick = pick;
    st.rate_album = album;
    rate_paint();
    void *panel = rate_panel(win);
    widget_set_visible(panel, 1, 0);
    widget_set_sensitive(panel, 1);
    rate_outside(win, 1);
    return 1;
}
/* Now Playing's, on the playing track's rating. */
int rate_open(void) {
    void *r = rate_track();
    return r && rate_show(st.rate_win, checkFavExist(r) ? 5 : rate_kept(0, listen_key(r)), 0);
}

/* The wheel, Centre and Return while the picker is up. */
int rate_key(void *top, unsigned key) {
    if (!st.rate_on || top != st.rate_on) return 0;
    if (key == KEY_NEXT || key == KEY_PREV) {
        st.rate_pick = clamp_step(st.rate_pick, 5, key == KEY_NEXT ? 1 : -1);
        rate_paint();
    } else if (key == KEY_CENTER || key == KEY_RETURN)
        rate_close(key == KEY_CENTER);
    else
        return 0;
    drop_input();
    return 1;
}

/* Holding the heart RATE_HOLD_MS opens the picker; a tap is stock's, a full heart then keeping
 * nothing here, so taking it away leaves the song unrated. The picker aborts the press, so the
 * slide_view under the heart lets go of its grab and the picker takes the next touch. */
static int rate_long(const void *unused) {
    st.rate_hold = 0;
    if (!pointer_down() || !(st.rate_held = rate_open())) return 0;
    unsigned abort_event[12];
    pointer_event_init(abort_event, EVT_POINTER_ABORT, st.rate_win, 0, 0);
    widget_dispatch_event_to_target_recursive(st.rate_win, abort_event);
    return 0;
}
static int rate_down(void *ctx, void *event) {
    st.rate_held = 0;
    rearm(&st.rate_hold, rate_long, RATE_HOLD_MS);
    return 0;
}
/* Returns 0 as stock's does: a STOP skips the slide_view's own release, and it keeps its grab. */
static int rate_fav_click(void *ctx, void *event) {
    stop_timer(&st.rate_hold);
    if (st.rate_held || st.rate_on) {
        st.rate_held = 0;
        return 0;
    }
    FAV_CLICK(ctx, event);
    ratings_copy_due();
    void *r = rate_track();
    if (r) rate_keep(0, listen_key(r), 0);
    rate_badge();
    return 0;
}

static int rate_gone(void *win, void *event) {
    if (win != st.rate_win) return 0;
    stop_timer(&st.rate_hold);
    stop_timer(&st.rate_tick);
    st.rate_win = st.rate_fav = st.rate_ctx = st.rate_badge = (void *)0;
    st.rate_held = 0;
    return 0;
}

/* Now Playing's heart (playing_page_init): stock's click is swapped for one that
 * tells a hold from a tap, and the badge sits on the heart's top right. */
void rate_bind(void *win) {
    void *fav = widget_lookup(win, "img_fav", 1), *emitter = fav ? P(fav, W_EMITTER) : (void *)0;
    stop_timer(&st.rate_tick);
    stop_timer(&st.rate_hold);
    st.rate_ctx = (void *)0;
    for (void *item = emitter ? P(emitter, 0) : 0; item; item = P(item, EMIT_NEXT))
        if (I(item, EMIT_TYPE) == EVT_CLICK && P(item, 12) == (void *)FAV_CLICK &&
            !B(item, EMIT_PENDING_REMOVE)) {
            st.rate_ctx = P(item, 0);
            break;
        }
    if (!st.rate_ctx) return;
    st.rate_win = win;
    st.rate_fav = fav;
    widget_off_by_func(fav, EVT_CLICK, (void *)FAV_CLICK, st.rate_ctx);
    widget_on(fav, EVT_CLICK, rate_fav_click, st.rate_ctx);
    widget_on(fav, EVT_POINTER_DOWN, rate_down, (void *)0);
    st.rate_badge = label(P(fav, W_PARENT), I(fav, W_X) + I(fav, W_W) - 18, I(fav, W_Y) + 6, 16, 16,
                          "s_scrlabel_white20c", 12, 0xffffffffu);
    widget_set_sensitive(st.rate_badge, 0); /* the heart's corner stays the heart's */
    st.rate_shown = 0;
    widget_on(win, EVT_DESTROY, rate_gone, win);
    st.rate_tick = timer_add(rate_tick, (void *)0, 500);
    rate_badge();
}

/* The Library's Ratings row: its menu, then a page of one list's songs, as Most Played's. */
static int fav_songs(void *unused) {
    return getAllFav(); /* select * from myfav, stock's song rows */
}

int rt_play(void *ctx, void *event) {
    play_folder(st.rt_list, (int)(long)ctx, 0xf001);
    return 0;
}

/* Each of songs' hearts into h: 5 for My Fav's, else the kept 1-4.
 * ponytail: every song against every rated one, fine for thousands rated; a sorted table if not. */
static void rate_all(void *songs, unsigned char *h) {
    int nf, kept = 0;
    void *favs = staged(fav_songs, 0, &nf);
    unsigned nfav = deque_size(favs), n = deque_size(songs), *t = rates(0);
    unsigned *keys = calloc(nfav + RATE_SLOTS + 1, sizeof *keys);
    for (unsigned i = 0; keys && i < nfav; i++) keys[i] = listen_key(deque_at(favs, i)) | 7;
    for (int i = 0; keys && t && i < RATE_SLOTS; i++)
        if (t[i]) keys[nfav + kept++] = t[i];
    for (unsigned i = 0; keys && i < n; i++) {
        unsigned key = listen_key(deque_at(songs, i));
        h[i] = 0;
        for (unsigned j = 0; j < nfav + (unsigned)kept && h[i] < 5; j++)
            if ((keys[j] ^ key) < 8) h[i] = j < nfav ? 5 : (unsigned char)(keys[j] & 7);
    }
    free(keys);
    deque_destroy(favs);
}

/* Top Rated (4-5 hearts), Loved (5), All Rated (1-5) and Disliked (1): most hearts first, then
 * the library's title order, each row's hearts under its title. */
static const char *const rt_names[] = { "Top Rated", "Loved", "All Rated", "Disliked" };
#define RT_ALBUMS 2 /* the Ratings menu's Albums row, after Loved */
static int rated_open(void *ctx, void *event) {
    static const unsigned char lo[] = { 4, 5, 1, 1 }, hi[] = { 5, 5, 5, 1 };
    int k = (int)(long)ctx - ((int)(long)ctx > RT_ALBUMS), n;
    if (st.rt_page || !(st.rt_page = page_open("rated_page", page_closed, page_keyup))) return 0;
    void *songs = staged(all_songs, 0, &n), *list = _create_deque("stSongInfo");
    unsigned size = deque_size(songs);
    unsigned char *h = calloc(size + 1, 1), *of = calloc(size + 1, 1);
    deque_init(list);
    if (h && of) {
        rate_all(songs, h);
        for (int v = hi[k]; v >= lo[k]; v--)
            for (unsigned i = 0; i < size; i++)
                if (h[i] == v)
                    of[deque_size(list)] = (unsigned char)v,
                    _deque_push_back(list, deque_at(songs, i));
    }
    st.rt_list = list;
    deque_destroy(songs);
    int count = (int)deque_size(list);
    void *view = page_list(st.rt_page, st.rt_page, 0, rt_names[k], count ? count : 1, 64);
    char name[512], detail[300], hs[16];
    for (int i = 0; i < count; i++) {
        void *t = deque_at(list, (unsigned)i);
        const char *artist = P(t, REC_ARTIST);
        tk_snprintf(detail, sizeof detail, artist && *artist ? "%s · %s" : "%s%s",
                    hearts_text(hs, of[i]), artist && *artist ? artist : "");
        page_row_detail(view, i, track_name(name, sizeof name, t), detail, rt_play);
    }
    if (!count)
        page_row_detail(view, 0, "No songs yet", "Hold the heart on Now Playing to rate", 0);
    free(h);
    free(of);
    return 0;
}

/* Albums: the rated albums as Coverflow (coverflow_rated), by most hearts unless sorted. */
extern int coverflow_rated(int (*hearts)(void *));
static int album_hearts(void *r) { return rate_kept(1, album_id(r)); }
int rated_albums(void *ctx, void *event) {
    if (!coverflow_rated(album_hearts)) toast("No rated albums yet");
    return 0;
}

/* The songs' best first, then the albums, then the songs' lists to tidy. */
int ratings_menu(void *ctx, void *event) {
    if (st.rt_menu || !(st.rt_menu = page_open("ratings_page", page_closed, page_keyup))) return 0;
    void *view = page_list(st.rt_menu, st.rt_menu, 0, "Ratings", 5, 48);
    for (int i = 0; i < 5; i++)
        page_row_into(view, i, i == RT_ALBUMS ? "Albums" : rt_names[i - (i > RT_ALBUMS)],
                      i == RT_ALBUMS ? rated_albums : rated_open);
    return 0;
}

/* The card's copy of the ratings (docs/internals.md#ratings): the tables hold only hashes, so a
 * pass reads the paths and tags back from the library, a page of rows each quiet poll on this
 * thread (demo has no busy timeout, so a worker's read lock could make stock's own My Fav writes
 * fail), and writes the card once at the end, only if the file would change; the longest copy so
 * far is kept as .1, the one way back from a lost table. ponytail: every row against every rated
 * one, as the Ratings lists do; a rating whose song or album has left the library has no name to
 * write, and stays out until it is back. */
#define RC_FILE "/mnt/mmc/.ratings.tsv"
#define RC_HEADER "#song\thearts\tpath\tcue_start\n#album\thearts\tname\talbum_artist\tfolder\n"
#define RC_PAGE 64
#define RC_WAIT_MS 5000 /* after a change, so a run of them writes once; between checks */
#define RC_LINE 3200    /* the fields' limits below, and room */
#define RC_GROW 0x10000
#define RC_QUIET_MS 1000 /* no pages this soon after input: a long walk stays off the wheel */
static struct {
    void *db;
    unsigned *song, *album; /* the rated still to find, their hearts in the low bits */
    unsigned songs, albums, len, cap, at;
    char *out;
    int table, last, rows, oom;
    unsigned gen;
} rc __attribute__((section(".scratch")));
static int rc_due __attribute__((section(".scratch")));

void ratings_copy_due(void) {
    rc_due = 1;
    rc.at = time_now_ms();
}

/* key's hearts in the first n of t, its entry gone, or 0. */
static int rc_take(unsigned *t, unsigned *n, unsigned key) {
    for (unsigned i = 0; i < *n; i++)
        if ((t[i] ^ key) < 8) {
            int hearts = (int)(t[i] & 7);
            t[i] = t[--*n];
            return hearts;
        }
    return 0;
}

/* A line's start, its kind and hearts written, with room for RC_LINE more; 0 when there's no
 * memory. */
static char *rc_line(const char *kind, int hearts) {
    if (rc.cap - rc.len < RC_LINE) {
        char *out = realloc(rc.out, rc.cap + RC_GROW);
        if (!out) {
            rc.oom = 1; /* ends the pass: a copy with rows missing must not replace the last */
            return (char *)0;
        }
        rc.out = out, rc.cap += RC_GROW;
    }
    char *o = rc.out + rc.len;
    return o + tk_snprintf(o, 16, "%s\t%d\t", kind, hearts);
}
static void rc_song(const char *path, int hearts, int cue) {
    char *o = rc_line("song", hearts);
    if (!o) return;
    o = scrobble_tag(o, o + 1100, path);
    if (cue) o += tk_snprintf(o, 16, "%d", cue);
    *o++ = '\n';
    rc.len = (unsigned)(o - rc.out);
}
static void rc_album(const char *name, const char *artist, const char *path, int hearts) {
    char *o = rc_line("album", hearts);
    if (!o) return;
    o = scrobble_tag(o, o + 600, name);
    o = scrobble_tag(o, o + 300, artist);
    unsigned dir = name && *name && artist && *artist ? 0 : path ? album_dir(path, 1) : 0;
    o = scrobble_tag(o, o + (dir < 1100 ? dir : 1100) + 1, path);
    o[-1] = '\n';
    rc.len = (unsigned)(o - rc.out);
}

/* A row as a record, for the keys its song and album are rated under. */
static int rc_row(void *ctx, int n, char **v, char **c) {
    int rec[REC_CUE_END / 4 + 1] = { 0 };
    rc.last = atoi(v[0]), rc.rows++;
    if (!v[1]) return 0;
    P(rec, REC_PATH) = v[1], P(rec, REC_ALBUM) = v[3], P(rec, REC_ALBUM_ARTIST) = v[4];
    int cue = I(rec, REC_CUE_START) = v[2] ? atoi(v[2]) : 0, hearts;
    if (!rc.table) { /* My Fav's: 5, whatever is kept for it */
        rc_take(rc.song, &rc.songs, listen_key(rec));
        rc_song(v[1], 5, cue);
        return rc.oom;
    }
    if (rc.songs && (hearts = rc_take(rc.song, &rc.songs, listen_key(rec))))
        rc_song(v[1], hearts, cue);
    if (rc.albums && (hearts = rc_take(rc.album, &rc.albums, album_id(rec))))
        rc_album(v[3], v[4], v[1], hearts);
    return rc.oom;
}

static void rc_end(void) {
    if (rc.db) sqlite3_close(rc.db);
    free(rc.song), free(rc.album), free(rc.out);
    rc.db = 0, rc.song = rc.album = 0, rc.out = 0;
    rc.len = rc.cap = rc.oom = 0;
}

/* path's lines, 0 with no file; same, if asked, whether it is this copy. */
static unsigned rc_read(const char *path, int *same) {
    void *f = fopen(path, "rb");
    char b[1024];
    unsigned n, at = 0, lines = 0;
    int eq = f != 0;
    while (f && (n = fread(b, 1, sizeof b, f)) > 0) {
        eq = eq && at + n <= rc.len && !memcmp(b, rc.out + at, n);
        at += n;
        for (unsigned i = 0; i < n; i++) lines += b[i] == '\n';
    }
    if (f) fclose(f);
    if (same) *same = eq && at == rc.len;
    return lines;
}

/* The card's copy replaced, unless it is this one already. Once the new one is safely written, a
 * copy with more lines goes to .1, unless .1 has more still: a rating given and cleared after a
 * lost table must not take the way back. */
static void rc_write(void) {
    int same;
    unsigned lines = rc_read(RC_FILE, &same), now = 0;
    if (same) return;
    if (!blob_io(RC_FILE ".tmp", RC_FILE ".tmp", rc.out, rc.len, 1)) {
        unlink(RC_FILE ".tmp");
        return;
    }
    for (unsigned i = 0; i < rc.len; i++) now += rc.out[i] == '\n';
    if (lines > now && lines >= rc_read(RC_FILE ".1", 0)) rename(RC_FILE, RC_FILE ".1");
    if (rename(RC_FILE ".tmp", RC_FILE)) unlink(RC_FILE ".tmp");
}

/* The rated of table album, valid hearts only, for rc_take. */
static unsigned *rc_rated(int album, unsigned *n) {
    unsigned *t = rates(album), *c = t ? calloc(RATE_SLOTS, sizeof *c) : (unsigned *)0;
    for (unsigned i = 0; c && i < RATE_SLOTS; i++)
        if ((t[i] & 7) && (t[i] & 7) <= 4u + (unsigned)album) c[(*n)++] = t[i];
    return c;
}

static void rc_start(void) {
    int unknown[REC_CUE_END / 4 + 1] = { 0 }, hearts;
    rc.songs = rc.albums = 0, rc.table = rc.last = 0;
    rc.song = rc_rated(0, &rc.songs), rc.album = rc_rated(1, &rc.albums);
    rc.out = calloc(rc.cap = RC_GROW, 1);
    if (!rc.song || !rc.album || !rc.out || sqlite3_open("/mnt/data/database.db", &rc.db) ||
        sqlite3_exec(rc.db, "PRAGMA cache_size=64", 0, 0, 0)) {
        rc_end();
        return;
    }
    rc.len = (unsigned)tk_snprintf(rc.out, RC_LINE, RC_HEADER);
    I(unknown, REC_ID) = -1; /* the Unknown Album card, which no row is */
    if ((hearts = rc_take(rc.album, &rc.albums, album_id(unknown))))
        rc_album("Unknown Album", "", (const char *)0, hearts);
}

static int rc_exists(void *ctx, int n, char **v, char **c) {
    rc.rows = 1;
    return 0;
}

/* A page of the pass, then its end. A failed read or write stays as it was, for the next change or
 * boot; a scan rewriting the library ends the pass, and library_gen starts another after it. */
static void rc_page(void) {
    char sql[128];
    if (library_scanning()) {
        rc_end();
        return;
    }
    if (time_now_ms() - st.last_input < RC_QUIET_MS) return;
    tk_snprintf(sql, sizeof sql,
                "select id,fileurl,time,album,albumsonger from %s where id>%d order by id limit %d",
                rc.table ? "songtable" : "myfav", rc.last, RC_PAGE);
    rc.rows = 0;
    if (sqlite3_exec(rc.db, sql, rc_row, 0, 0)) {
        rc_end();
        return;
    }
    if (rc.rows == RC_PAGE && (!rc.table || rc.songs || rc.albums)) return;
    if (!rc.table++) {
        rc.last = rc.rows = 0;
        /* no songtable before the first scan: My Fav's alone, still worth a copy */
        if (sqlite3_exec(rc.db, "select 1 from sqlite_master where name='songtable'", rc_exists, 0,
                         0)) {
            rc_end();
            return;
        }
        if (rc.rows) return;
    }
    if (!card_mounted())
        rc_due = 1; /* shared over USB meanwhile */
    else
        rc_write();
    rc_end();
}

/* Every RC_WAIT_MS: a change, the boot (stock's own My Fav rows change it unseen) or the library
 * rewritten, once the card is there and no scan is running. */
void ratings_copy_poll(void) {
    if (rc.db) {
        rc_page();
        return;
    }
    unsigned now = time_now_ms();
    if (!rc.at) {
        rc_due = 1, rc.at = now | !now;
        return;
    }
    if (now - rc.at < RC_WAIT_MS) return;
    rc.at = now;
    if (rc.gen != library_gen) rc.gen = library_gen, rc_due = 1;
    if (!rc_due || library_scanning() || !card_mounted()) return;
    rc_due = 0;
    rc_start();
}
